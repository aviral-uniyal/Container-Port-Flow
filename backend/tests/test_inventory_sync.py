"""Focused, database-free tests for run-scoped inventory synchronization."""

from contextlib import contextmanager
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.execution import SimulationExecution
from backend.app.router import AssignmentConflictError, _persist_snapshot, router


def snapshot(state, crane_status, job_id="J1", assigned_job=""):
    return {
        "ok": True,
        "jobs": [{"job_id": job_id, "state": state,
                  "work_completed": 0 if state != "COMPLETED" else 2,
                  "work_remaining": 2 if state != "COMPLETED" else 0}],
        "cranes": [{"crane_id": "C1", "status": crane_status,
                    "assigned_job": assigned_job}],
    }


class SnapshotStore:
    def __init__(self):
        self.jobs = {
            "J1": {"simulation_job_id": 11, "job_id": "J1", "status": "PENDING",
                   "work_completed": 0, "work_remaining": 2},
            "J2": {"simulation_job_id": 12, "job_id": "J2", "status": "PENDING",
                   "work_completed": 0, "work_remaining": 2},
        }
        self.active = []
        self.inserted = []
        self.released = []
        self.statuses = []
        self.dispatch_updates = []
        self.crane_status = {"C1": "AVAILABLE", "C2": "AVAILABLE"}

    @contextmanager
    def connection(self, pool):
        yield object()

    def assignments(self, conn, run_id):
        assert run_id == 7
        return [a for a in self.active if a["run_id"] == run_id]

    def assignments_for_cranes(self, conn, crane_ids):
        return [a for a in self.active if a["crane_id"] in crane_ids]

    def insert(self, conn, crane_id, simulation_job_id):
        assert crane_id in self.crane_status
        job_id = next(jid for jid, row in self.jobs.items()
                      if row["simulation_job_id"] == simulation_job_id)
        assignment_id = len(self.inserted) + 1
        row = {"assignment_id": assignment_id, "crane_id": crane_id,
               "simulation_job_id": simulation_job_id, "job_id": job_id,
               "run_id": 7, "run_status": "RUNNING"}
        self.active.append(row)
        self.inserted.append(row)
        return assignment_id

    def release(self, conn, assignment_id, release_reason):
        self.active = [a for a in self.active if a["assignment_id"] != assignment_id]
        self.released.append((assignment_id, release_reason))
        return True

    def update_status(self, conn, run_id, job_id, status):
        assert run_id == 7
        self.jobs[job_id]["status"] = status
        if status == "COMPLETED":
            self.jobs[job_id].update(work_completed=2, work_remaining=0)
        return True

    def update_progress(self, conn, run_id, job_id, work_completed, work_remaining):
        assert run_id == 7
        self.jobs[job_id].update(work_completed=work_completed,
                                 work_remaining=work_remaining)
        return True

    def update_dispatch_sequence(self, conn, run_id, job_id, dispatch_sequence):
        assert run_id == 7
        self.jobs[job_id]["dispatch_sequence"] = dispatch_sequence
        self.dispatch_updates.append((job_id, dispatch_sequence))
        return True

    def sync_cranes(self, conn, statuses):
        self.statuses.append(statuses)
        self.crane_status.update({entry["crane_id"]: entry["status"] for entry in statuses})
        return len(statuses)


@pytest.fixture
def store():
    state = SnapshotStore()
    patches = [
        patch("backend.app.router.get_connection", side_effect=state.connection),
        patch("backend.app.router.get_jobs_for_run", side_effect=lambda conn, run_id: list(state.jobs.values())),
        patch("backend.app.router.get_active_assignments_for_run", side_effect=state.assignments),
        patch("backend.app.router.get_active_assignments_for_cranes", side_effect=state.assignments_for_cranes),
        patch("backend.app.router.insert_crane_assignment", side_effect=state.insert),
        patch("backend.app.router.release_crane_assignment", side_effect=state.release),
        patch("backend.app.router.update_job_status", side_effect=state.update_status),
        patch("backend.app.router.update_job_dispatch_sequence",
              side_effect=state.update_dispatch_sequence),
        patch("backend.app.router.update_job_work_progress", side_effect=state.update_progress),
        patch("backend.app.router.batch_sync_crane_statuses", side_effect=state.sync_cranes),
    ]
    for p in patches:
        p.start()
    yield state
    for p in reversed(patches):
        p.stop()


def request_for_run():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db_pool=SimpleNamespace(is_open=True), current_run_id=7)))


def test_new_dispatch_and_repeated_snapshot_are_idempotent(store):
    request = request_for_run()
    active = snapshot("ACTIVE", "ASSIGNED", assigned_job="J1")
    active["jobs"][0]["dispatch_sequence"] = 0
    _persist_snapshot(request, active)
    _persist_snapshot(request, active)
    assert len(store.inserted) == 1
    assert store.active[0]["simulation_job_id"] == 11
    assert store.released == []
    assert store.jobs["J1"]["status"] == "ACTIVE"
    assert store.dispatch_updates == [("J1", 0)]


def test_terminal_previous_run_assignment_is_closed_before_insert(store):
    store.active.append({"assignment_id": 40, "crane_id": "C1",
                         "simulation_job_id": 4, "job_id": "OLD",
                         "run_id": 3, "run_status": "FAILED"})
    active = snapshot("ACTIVE", "ASSIGNED", assigned_job="J1")
    _persist_snapshot(request_for_run(), active)
    _persist_snapshot(request_for_run(), active)
    assert store.released == [(40, "STALE_ASSIGNMENT_RECONCILED")]
    assert len(store.active) == 1
    assert store.active[0]["simulation_job_id"] == 11


def test_other_running_run_is_not_released(store):
    store.active.append({"assignment_id": 40, "crane_id": "C1",
                         "simulation_job_id": 4, "job_id": "OLD",
                         "run_id": 3, "run_status": "RUNNING"})
    with pytest.raises(AssignmentConflictError, match="active assignment in run 3"):
        _persist_snapshot(request_for_run(), snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))
    assert store.released == []
    assert store.inserted == []


def test_same_job_label_in_previous_run_is_not_mistaken_for_current(store):
    store.active.append({"assignment_id": 40, "crane_id": "C1",
                         "simulation_job_id": 4, "job_id": "J1",
                         "run_id": 3, "run_status": "COMPLETED"})
    _persist_snapshot(request_for_run(), snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))
    assert store.released == [(40, "STALE_ASSIGNMENT_RECONCILED")]
    assert store.active[0]["simulation_job_id"] == 11


def test_completion_releases_and_continuation_assigns_next_job(store):
    request = request_for_run()
    _persist_snapshot(request, snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))
    completed = snapshot("COMPLETED", "ASSIGNED", assigned_job="J2")
    completed["jobs"].append({"job_id": "J2", "state": "ACTIVE",
                              "work_completed": 0, "work_remaining": 2})
    _persist_snapshot(request, completed)
    _persist_snapshot(request, completed)
    assert store.released == [(1, "JOB_COMPLETED")]
    assert len(store.inserted) == 2
    assert store.active[0]["simulation_job_id"] == 12
    assert store.jobs["J1"]["status"] == "COMPLETED"
    assert store.jobs["J1"]["work_remaining"] == 0
    assert store.crane_status["C1"] == "ASSIGNED"


def test_final_completion_releases_assignment_and_inventory_crane(store):
    request = request_for_run()
    _persist_snapshot(request, snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))
    completed = snapshot("COMPLETED", "AVAILABLE")
    _persist_snapshot(request, completed)
    assert store.active == []
    assert store.released == [(1, "JOB_COMPLETED")]
    assert store.statuses[-1] == [{"crane_id": "C1", "status": "AVAILABLE"}]
    assert store.crane_status["C1"] == "AVAILABLE"
    _persist_snapshot(request, completed)
    assert store.released == [(1, "JOB_COMPLETED")]


def test_two_completed_jobs_release_both_cranes(store):
    request = request_for_run()
    active = snapshot("ACTIVE", "ASSIGNED", assigned_job="J1")
    active["jobs"].append({"job_id": "J2", "state": "ACTIVE",
                           "work_completed": 0, "work_remaining": 2})
    active["cranes"].append({"crane_id": "C2", "status": "ASSIGNED",
                             "assigned_job": "J2"})
    _persist_snapshot(request, active)
    completed = snapshot("COMPLETED", "AVAILABLE")
    completed["jobs"].append({"job_id": "J2", "state": "COMPLETED",
                              "work_completed": 2, "work_remaining": 0})
    completed["cranes"].append({"crane_id": "C2", "status": "AVAILABLE",
                                "assigned_job": ""})
    _persist_snapshot(request, completed)
    assert store.active == []
    assert len(store.released) == 2
    assert store.crane_status == {"C1": "AVAILABLE", "C2": "AVAILABLE"}


def test_manual_complete_commits_job_release_and_crane_status_together(store):
    _persist_snapshot(request_for_run(), snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))

    class CompletionBridge:
        is_alive = True

        def send(self, command):
            if command["cmd"] == "complete_job":
                return {"ok": True, "job_id": "J1", "state": "COMPLETED"}
            assert command["cmd"] == "snapshot"
            return snapshot("COMPLETED", "AVAILABLE")

    app = FastAPI()
    app.include_router(router)
    app.state.bridge = CompletionBridge()
    app.state.db_pool = SimpleNamespace(is_open=True)
    app.state.current_run_id = 7
    app.state.execution = SimulationExecution(app.state.bridge)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/simulation/jobs/J1/complete")
    assert response.status_code == 200
    assert store.jobs["J1"]["status"] == "COMPLETED"
    assert store.active == []
    assert store.released == [(1, "JOB_COMPLETED")]
    assert store.crane_status["C1"] == "AVAILABLE"


def test_run_completion_releases_assignment_and_repeated_step_is_idempotent(store):
    _persist_snapshot(request_for_run(), snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))

    class RunBridge:
        is_alive = True

        def __init__(self):
            self.progress = 0

        def send(self, command):
            if command["cmd"] == "step":
                self.progress = min(self.progress + 1, 2)
                return {"ok": True, "advanced_jobs": ["J1"], "dispatched_jobs": []}
            assert command["cmd"] == "snapshot"
            if self.progress == 2:
                return snapshot("COMPLETED", "AVAILABLE")
            result = snapshot("ACTIVE", "ASSIGNED", assigned_job="J1")
            result["jobs"][0].update(work_completed=self.progress,
                                      work_remaining=2 - self.progress)
            return result

    app = FastAPI()
    app.include_router(router)
    app.state.bridge = RunBridge()
    app.state.db_pool = SimpleNamespace(is_open=True)
    app.state.current_run_id = 7
    app.state.execution = SimulationExecution(app.state.bridge)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if client.get("/simulation/execution").json()["state"] == "completed":
                break
            time.sleep(0.01)
        else:
            pytest.fail("runner did not complete")
        assert client.post("/simulation/step").status_code == 200
    assert store.jobs["J1"]["status"] == "COMPLETED"
    assert store.active == []
    assert store.released == [(1, "JOB_COMPLETED")]
    assert store.crane_status["C1"] == "AVAILABLE"


def test_failure_and_recovery_create_no_duplicate_active_assignment(store):
    request = request_for_run()
    _persist_snapshot(request, snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))
    failed = snapshot("INTERRUPTED", "FAILED")
    _persist_snapshot(request, failed)
    resumed = snapshot("ACTIVE", "ASSIGNED", assigned_job="J1")
    _persist_snapshot(request, resumed)
    _persist_snapshot(request, resumed)
    assert store.released == [(1, "CRANE_FAILED")]
    assert len(store.inserted) == 2
    assert len(store.active) == 1


def test_recovery_endpoint_commits_final_reassignment_with_event(store):
    request = request_for_run()
    _persist_snapshot(request, snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))
    _persist_snapshot(request, snapshot("INTERRUPTED", "FAILED"))

    class RecoveryBridge:
        is_alive = True

        def send(self, command):
            if command["cmd"] == "snapshot":
                return snapshot("ACTIVE", "ASSIGNED", assigned_job="J1")
            assert command == {"cmd": "crane_recovered", "crane_id": "C1"}
            return {"ok": True, "crane_id": "C1", "job_dispatched": True,
                    "dispatched_job_id": "J1"}

    app = FastAPI()
    app.include_router(router)
    app.state.bridge = RecoveryBridge()
    app.state.db_pool = SimpleNamespace(is_open=True)
    app.state.current_run_id = 7
    app.state.execution = SimulationExecution(app.state.bridge)
    with patch("backend.app.router.insert_disruption_event", return_value=1) as event:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/simulation/cranes/C1/recover")
    assert response.status_code == 200
    assert response.json()["status"] == "ASSIGNED"
    assert store.released == [(1, "CRANE_FAILED")]
    assert len(store.active) == 1
    assert store.active[0]["simulation_job_id"] == 11
    assert store.statuses[-1] == [{"crane_id": "C1", "status": "ASSIGNED"}]
    event.assert_called_once()


def test_snapshot_never_links_crane_to_missing_or_other_run_job(store):
    with pytest.raises(RuntimeError, match="missing from run"):
        _persist_snapshot(request_for_run(), snapshot("ACTIVE", "ASSIGNED", "OTHER", "OTHER"))
    assert store.inserted == []


@pytest.mark.parametrize("command, path", [
    ("submit_job", "/simulation/jobs"),
    ("dispatch_batch", "/simulation/dispatch"),
])
def test_dispatch_routes_reconcile_the_resulting_snapshot(command, path):
    active = snapshot("ACTIVE", "ASSIGNED", assigned_job="J1")

    class DispatchBridge:
        is_alive = True

        def send(self, request):
            if request["cmd"] == "snapshot":
                return active
            if request["cmd"] == "dispatch_batch":
                return {"ok": True, "dispatched_jobs": ["J1"]}
            return {"ok": True, "job_id": "J1", "state": "ACTIVE"}

    app = FastAPI()
    app.include_router(router)
    app.state.bridge = DispatchBridge()
    app.state.db_pool = SimpleNamespace(is_open=True)
    app.state.current_run_id = 7
    app.state.execution = SimulationExecution(app.state.bridge)
    body = {"ship_id": 1, "job_id": "J1", "arrival_time": 0, "work_total": 2,
            "priority": 0, "berths": 1, "cranes": 1, "trucks": 0}
    with patch("backend.app.router.get_connection"), \
         patch("backend.app.router.ship_exists", return_value=True), \
         patch("backend.app.router.ship_has_job_in_run", return_value=False), \
         patch("backend.app.router.insert_simulation_job"), \
         patch("backend.app.router.update_job_status"), \
         patch("backend.app.router._persist_snapshot") as persist:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(path, json=body if command == "submit_job" else None)
    assert response.status_code == 200
    persist.assert_called_once()
    assert persist.call_args.args[1] == active


class ResetBridge:
    is_alive = True

    def __init__(self, old_snapshot):
        self.old_snapshot = old_snapshot
        self.commands = []

    def send(self, command):
        self.commands.append(command["cmd"])
        if command["cmd"] == "snapshot":
            return self.old_snapshot
        return {"ok": True, "policy": "FCFS"}


def reset_client(bridge):
    app = FastAPI()
    app.include_router(router)
    app.state.bridge = bridge
    app.state.db_pool = SimpleNamespace(is_open=True)
    app.state.current_run_id = 7
    app.state.execution = SimulationExecution(bridge)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("old_state, crane_status, expected_run_status", [
    ("ACTIVE", "ASSIGNED", "FAILED"),
    ("ACTIVE", "FAILED", "FAILED"),
    ("COMPLETED", "AVAILABLE", "COMPLETED"),
])
def test_reset_reconciles_only_previous_run(old_state, crane_status,
                                            expected_run_status):
    bridge = ResetBridge(snapshot(old_state, crane_status,
                                  assigned_job="J1" if crane_status == "ASSIGNED" else ""))
    client = reset_client(bridge)
    with patch("backend.app.router.get_connection") as connection, \
         patch("backend.app.router.get_active_assignments_for_run",
               return_value=[{"crane_id": "C2"}]) as active, \
         patch("backend.app.router.cancel_unfinished_jobs_for_run") as cancel, \
         patch("backend.app.router.release_active_assignments_for_run") as release, \
         patch("backend.app.router.batch_sync_crane_statuses",
               side_effect=lambda conn, statuses: len(statuses)) as sync, \
         patch("backend.app.router.close_simulation_run") as close, \
         patch("backend.app.router.insert_simulation_run", return_value=8):
        conn = connection.return_value.__enter__.return_value
        response = client.post("/simulation/init", json={"berths": 1, "cranes": 1, "trucks": 0})
        assert response.status_code == 200
        assert bridge.commands == ["snapshot", "init"]
        active.assert_called_once_with(conn, 7)
        cancel.assert_called_once_with(conn, 7)
        release.assert_called_once_with(conn, 7, "SIMULATION_RESET")
        sync.assert_called_once_with(conn, [
            {"crane_id": "C1", "status": "AVAILABLE"},
            {"crane_id": "C2", "status": "AVAILABLE"},
        ])
        close.assert_called_once_with(conn, 7, expected_run_status)
        assert client.app.state.current_run_id == 8


def test_reset_db_failure_does_not_discard_runtime():
    bridge = ResetBridge(snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))
    client = reset_client(bridge)
    with patch("backend.app.router.get_connection") as connection, \
         patch("backend.app.router.get_active_assignments_for_run", side_effect=RuntimeError("DB failed")):
        response = client.post("/simulation/init", json={"berths": 1, "cranes": 1, "trucks": 0})
    assert response.status_code == 503
    assert bridge.commands == ["snapshot"]
    assert client.app.state.current_run_id == 7
    connection.return_value.__exit__.assert_called_once()


def test_cpp_init_failure_after_db_prepare_closes_new_run_and_disables_bridge():
    class RejectingBridge(ResetBridge):
        def send(self, command):
            if command["cmd"] == "init":
                self.commands.append("init")
                return {"ok": False, "error": "init rejected"}
            return super().send(command)

    bridge = RejectingBridge(snapshot("ACTIVE", "ASSIGNED", assigned_job="J1"))
    client = reset_client(bridge)
    with patch("backend.app.router.get_connection") as connection, \
         patch("backend.app.router.get_active_assignments_for_run", return_value=[]), \
         patch("backend.app.router.cancel_unfinished_jobs_for_run"), \
         patch("backend.app.router.release_active_assignments_for_run"), \
         patch("backend.app.router.batch_sync_crane_statuses",
               side_effect=lambda conn, statuses: len(statuses)), \
         patch("backend.app.router.close_simulation_run") as close, \
         patch("backend.app.router.insert_simulation_run", return_value=8):
        response = client.post("/simulation/init", json={"berths": 1, "cranes": 1, "trucks": 0})
    assert response.status_code == 400
    assert bridge.commands == ["snapshot", "init"]
    assert close.call_count == 2
    assert close.call_args.args[1] == 8
    assert client.app.state.current_run_id is None
    assert client.app.state.bridge is None
