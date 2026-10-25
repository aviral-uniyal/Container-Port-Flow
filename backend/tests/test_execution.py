"""Focused HTTP tests for deterministic simulation execution controls."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.app.cpp_bridge import CppBridge
from backend.app.execution import SimulationExecution
from backend.app.main import app as main_app
from backend.app.router import router


class WorkBridge:
    """Small stateful stand-in for the C++ JSON-lines process."""

    is_alive = True

    def __init__(self, *, batch: bool = False, work: tuple[int, ...] = (2,)):
        self.lock = threading.Lock()
        self.batch = batch
        self.jobs = [
            {"job_id": f"J{i}", "state": "PENDING" if batch or i else "ACTIVE",
             "work_total": total, "work_completed": 0, "work_remaining": total}
            for i, total in enumerate(work)
        ]
        self.crane_failed = False
        self.registered = True
        self.commands: list[dict] = []
        self.fail_step = False

    def send(self, command: dict) -> dict:
        with self.lock:
            self.commands.append(command.copy())
            cmd = command["cmd"]
            if cmd == "init":
                self.jobs.clear()
                self.registered = True
                self.batch = command.get("batch_mode", False)
                return {"ok": True, "policy": command.get("policy", "FCFS"),
                        "batch_mode": self.batch}
            if cmd == "reset":
                self.jobs.clear()
                self.registered = False
                return {"ok": True}
            if cmd == "submit_job":
                total = command["work_total"]
                self.jobs.append({
                    "job_id": command["job_id"], "state": "ACTIVE",
                    "work_total": total, "work_completed": 0, "work_remaining": total,
                })
                return {"ok": True, "job_id": command["job_id"]}
            if cmd == "snapshot":
                assigned = next((j["job_id"] for j in self.jobs if j["state"] == "ACTIVE"), "")
                return {"ok": True, "batch_mode": self.batch,
                        "jobs": [j.copy() for j in self.jobs],
                        "cranes": ([{"crane_id": "C1",
                                    "status": "FAILED" if self.crane_failed else
                                    "ASSIGNED" if assigned else "AVAILABLE",
                                    "assigned_job": "" if self.crane_failed else assigned}]
                                   if self.registered else [])}
            if cmd == "step":
                if self.fail_step:
                    return {"ok": False, "error": "step rejected"}
                advanced, completed, dispatched = [], [], []
                active = [j for j in self.jobs if j["state"] == "ACTIVE"]
                for job in active:
                    if self.crane_failed:
                        continue
                    job["work_remaining"] -= 1
                    job["work_completed"] += 1
                    advanced.append(job["job_id"])
                    if job["work_remaining"] == 0:
                        job["state"] = "COMPLETED"
                        completed.append(job["job_id"])
                if not any(j["state"] == "ACTIVE" for j in self.jobs):
                    pending = next((j for j in self.jobs if j["state"] == "PENDING"), None)
                    if pending and (not self.batch or command["continue_batch"]):
                        pending["state"] = "ACTIVE"
                        dispatched.append(pending["job_id"])
                return {"ok": True, "advanced_jobs": advanced,
                        "completed_jobs": completed, "dispatched_jobs": dispatched}
            if cmd == "crane_failed":
                self.crane_failed = True
                job = next(j for j in self.jobs if j["state"] == "ACTIVE")
                job["state"] = "INTERRUPTED"
                return {"ok": True, "crane_id": "C1",
                        "affected_job_id": job["job_id"], "job_interrupted": True}
            if cmd == "crane_recovered":
                self.crane_failed = False
                job = next((j for j in self.jobs if j["state"] == "INTERRUPTED"), None)
                if job:
                    job["state"] = "ACTIVE"
                return {"ok": True, "crane_id": "C1", "job_dispatched": bool(job),
                        "dispatched_job_id": job["job_id"] if job else ""}
            return {"ok": True}


def client_for(bridge: WorkBridge) -> TestClient:
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.state.bridge = bridge
    test_app.state.db_pool = None
    test_app.state.current_run_id = None
    test_app.state.execution = SimulationExecution(bridge)
    return TestClient(test_app, raise_server_exceptions=False)


def wait_for(client: TestClient, state: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.get("/simulation/execution").json()
        if result["state"] == state:
            return result
        time.sleep(0.01)
    raise AssertionError(f"execution did not reach {state}")


def wait_for_work(bridge: WorkBridge, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bridge.jobs[0]["work_completed"] > 0:
            return
        time.sleep(0.01)
    raise AssertionError("job did not advance")


def test_step_forwarding_and_batch_authorization():
    bridge = WorkBridge(batch=True, work=(1, 1))
    with client_for(bridge) as client:
        step = client.post("/simulation/step")
        assert step.status_code == 200
        assert step.json()["advanced_jobs"] == []
        assert bridge.commands[-1] == {"cmd": "step", "continue_batch": False}
        assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
        wait_for(client, "completed")
        assert all(j["state"] == "COMPLETED" for j in bridge.jobs)
        assert any(c == {"cmd": "step", "continue_batch": True} for c in bridge.commands)


def test_runner_advances_through_future_arrival_without_blocking():
    class FutureArrivalBridge:
        def __init__(self):
            self.time = 0
            self.job = {"job_id": "FUTURE", "arrival_time": 3, "state": "PENDING"}

        def send(self, command):
            if command["cmd"] == "snapshot":
                return {"ok": True, "simulation_time": self.time,
                        "jobs": [self.job.copy()]}
            assert command == {"cmd": "step", "continue_batch": True}
            advanced, dispatched = [], []
            if self.job["state"] == "ACTIVE":
                advanced.append("FUTURE")
                self.job["state"] = "COMPLETED"
            self.time += 1
            if self.time == 3:
                self.job["state"] = "ACTIVE"
                dispatched.append("FUTURE")
            return {"ok": True, "advanced_jobs": advanced,
                    "dispatched_jobs": dispatched}

    async def scenario():
        bridge = FutureArrivalBridge()
        execution = SimulationExecution(bridge)
        await execution.run(0.05)
        for _ in range(100):
            if execution.state != "running":
                break
            await asyncio.sleep(0.01)
        assert execution.state == "completed"
        assert bridge.time == 4
        assert bridge.job["state"] == "COMPLETED"

    asyncio.run(scenario())


def test_initial_and_manual_step_status_are_not_running():
    bridge = WorkBridge(work=(2,))
    with client_for(bridge) as client:
        assert client.get("/simulation/execution").json()["state"] == "not_running"
        assert client.post("/simulation/step").status_code == 200
        assert client.get("/simulation/execution").json()["state"] == "not_running"


def test_pause_resume_and_duplicate_controls():
    bridge = WorkBridge(work=(8,))
    with client_for(bridge) as client:
        assert client.post("/simulation/run", json={"interval_seconds": 0.1}).json()["state"] == "running"
        assert client.post("/simulation/run").json()["state"] == "running"
        assert client.post("/simulation/step").status_code == 409
        wait_for_work(bridge)
        assert client.post("/simulation/pause").json()["state"] == "paused"
        count = bridge.jobs[0]["work_completed"]
        time.sleep(0.15)
        assert bridge.jobs[0]["work_completed"] == count
        assert client.post("/simulation/pause").json()["state"] == "paused"
        assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
        wait_for(client, "completed")
        assert bridge.jobs[0]["work_remaining"] == 0


def test_failure_recovery_and_immediate_continuation():
    bridge = WorkBridge(work=(3, 1))
    with client_for(bridge) as client:
        client.post("/simulation/run", json={"interval_seconds": 0.1})
        wait_for_work(bridge)
        failure = client.post("/simulation/cranes/C1/fail")
        assert failure.status_code == 200
        preserved = bridge.jobs[0]["work_remaining"]
        failure_command = next(c for c in reversed(bridge.commands) if c["cmd"] == "crane_failed")
        assert failure_command["work_completed"] + failure_command["work_remaining"] == 3
        assert failure_command["work_remaining"] == preserved
        wait_for(client, "blocked")
        assert bridge.jobs[0]["work_remaining"] == preserved
        recovery = client.post("/simulation/cranes/C1/recover")
        assert recovery.status_code == 200
        assert recovery.json()["execution_state"] == "running"
        wait_for(client, "completed")
        assert all(j["state"] == "COMPLETED" for j in bridge.jobs)


def test_manual_pause_prevents_recovery_from_restarting_runner():
    bridge = WorkBridge(work=(5,))
    with client_for(bridge) as client:
        client.post("/simulation/run", json={"interval_seconds": 0.1})
        wait_for_work(bridge)
        assert client.post("/simulation/pause").json()["state"] == "paused"
        assert client.post("/simulation/cranes/C1/fail").status_code == 200
        recovery = client.post("/simulation/cranes/C1/recover")
        assert recovery.status_code == 200
        assert recovery.json()["execution_state"] == "paused"
        preserved = bridge.jobs[0]["work_remaining"]
        time.sleep(0.15)
        assert bridge.jobs[0]["work_remaining"] == preserved


def test_blocked_runner_waits_until_recovery_can_make_progress():
    bridge = WorkBridge(work=(3,))

    async def scenario():
        execution = SimulationExecution(bridge)
        await execution.run(0.01)
        while bridge.jobs[0]["work_completed"] == 0:
            await asyncio.sleep(0.005)
        bridge.send({"cmd": "crane_failed"})
        while execution.state != "blocked":
            await asyncio.sleep(0.005)
        preserved = bridge.jobs[0]["work_remaining"]
        assert (await execution.resume_after_recovery(False))["state"] == "blocked"
        await asyncio.sleep(0.03)
        assert bridge.jobs[0]["work_remaining"] == preserved
        bridge.send({"cmd": "crane_recovered"})
        assert (await execution.resume_after_recovery(True))["state"] == "running"
        while execution.state != "completed":
            await asyncio.sleep(0.005)

    asyncio.run(scenario())
    assert bridge.jobs[0]["state"] == "COMPLETED"


def test_reset_clears_blocked_resume_intent():
    bridge = WorkBridge(work=(5,))
    with client_for(bridge) as client:
        client.post("/simulation/run", json={"interval_seconds": 0.05})
        wait_for_work(bridge)
        assert client.post("/simulation/cranes/C1/fail").status_code == 200
        wait_for(client, "blocked")
        assert client.post("/simulation/init", json={
            "berths": 1, "cranes": 1, "trucks": 0,
        }).status_code == 200
        assert client.get("/simulation/execution").json()["state"] == "not_running"


def test_runner_error_and_reinitialization_stops_execution():
    bridge = WorkBridge(work=(10,))
    with client_for(bridge) as client:
        bridge.fail_step = True
        client.post("/simulation/run", json={"interval_seconds": 0.05})
        assert "step rejected" in wait_for(client, "blocked")["error"]
        bridge.fail_step = False
        client.post("/simulation/run", json={"interval_seconds": 0.1})
        assert client.post("/simulation/init", json={
            "berths": 1, "cranes": 1, "trucks": 1, "policy": "FCFS"
        }).status_code == 200
        assert client.get("/simulation/execution").json()["state"] == "not_running"
        count = len([c for c in bridge.commands if c["cmd"] == "step"])
        time.sleep(0.15)
        assert len([c for c in bridge.commands if c["cmd"] == "step"]) == count
        assert bridge.jobs == []


def test_runner_snapshots_include_automatic_dispatch_and_completion():
    bridge = WorkBridge(work=(1, 1))
    with client_for(bridge) as client:
        client.app.state.db_pool = SimpleNamespace(is_open=True)
        client.app.state.current_run_id = 7
        captured = []
        with patch("backend.app.router._persist_snapshot", side_effect=lambda request, snap: captured.append(snap)):
            assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
            wait_for(client, "completed")
        assert any(any(j["job_id"] == "J1" and j["state"] == "ACTIVE"
                       for j in snap["jobs"]) for snap in captured)
        assert any(all(j["state"] == "COMPLETED" for j in snap["jobs"])
                   for snap in captured)


def test_manual_step_persists_its_snapshot():
    bridge = WorkBridge(work=(2,))
    with client_for(bridge) as client:
        client.app.state.db_pool = SimpleNamespace(is_open=True)
        client.app.state.current_run_id = 7
        with patch("backend.app.router._persist_snapshot") as persist:
            assert client.post("/simulation/step").status_code == 200
        persist.assert_called_once()
        assert persist.call_args.args[1]["jobs"][0]["work_completed"] == 1


def test_manual_step_reports_database_persistence_failure():
    bridge = WorkBridge(work=(2,))
    with client_for(bridge) as client:
        client.app.state.current_run_id = 7
        response = client.post("/simulation/step")
        assert response.status_code == 503
        assert "reconciliation required" in response.json()["detail"]
        assert bridge.jobs[0]["work_completed"] == 1


def test_runner_blocks_when_database_persistence_fails():
    bridge = WorkBridge(work=(4,))
    with client_for(bridge) as client:
        client.app.state.current_run_id = 7
        assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
        status = wait_for(client, "blocked")
        assert "Database persistence failed; reconciliation required" in status["error"]
        assert "Database unavailable" in status["error"]
        assert bridge.jobs[0]["work_completed"] == 1
        assert client.app.state.execution._recoverable_block is False
        assert client.app.state.execution._run_requested is False


def test_reset_waits_for_active_runner_before_database_reconciliation():
    bridge = WorkBridge(work=(20,))
    with client_for(bridge) as client:
        client.app.state.db_pool = SimpleNamespace(is_open=True)
        client.app.state.current_run_id = 7
        with patch("backend.app.router._persist_snapshot"), \
             patch("backend.app.router.get_connection") as connection, \
             patch("backend.app.router.get_active_assignments_for_run", return_value=[]), \
             patch("backend.app.router.cancel_unfinished_jobs_for_run") as cancel, \
             patch("backend.app.router.release_active_assignments_for_run"), \
             patch("backend.app.router.batch_sync_crane_statuses",
                   side_effect=lambda conn, statuses: len(statuses)), \
             patch("backend.app.router.close_simulation_run"), \
             patch("backend.app.router.insert_simulation_run", return_value=8):
            assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
            wait_for_work(bridge)
            assert client.post("/simulation/init", json={
                "berths": 1, "cranes": 1, "trucks": 0,
            }).status_code == 200
            cancel.assert_called_once_with(connection.return_value.__enter__.return_value, 7)
            assert bridge.jobs == []
            assert client.app.state.current_run_id == 8


def test_completed_workload_keeps_run_open_for_another_submission():
    bridge = WorkBridge(work=(1, 1))
    with client_for(bridge) as client:
        client.app.state.db_pool = SimpleNamespace(is_open=True)
        client.app.state.current_run_id = 7
        with patch("backend.app.router._persist_snapshot"), \
             patch("backend.app.router.close_simulation_run") as close_run, \
             patch("backend.app.router.get_connection"), \
             patch("backend.app.router.ship_exists", return_value=True), \
             patch("backend.app.router.ship_has_job_in_run", return_value=False), \
             patch("backend.app.router.insert_simulation_job"), \
             patch("backend.app.router._sync_crane_statuses"):
            assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
            wait_for(client, "completed")
            assert client.app.state.current_run_id == 7
            close_run.assert_not_called()
            response = client.post("/simulation/jobs", json={
                "ship_id": 1, "job_id": "LATER", "arrival_time": 0,
                "work_total": 1, "priority": 1, "berths": 1,
                "cranes": 1, "trucks": 0,
            })
            assert response.status_code == 200
            assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
            wait_for(client, "completed")
            assert bridge.jobs[-1]["state"] == "COMPLETED"
            assert client.app.state.current_run_id == 7
            close_run.assert_not_called()


def test_dashboard_reset_stops_runner_and_closes_only_current_run():
    bridge = WorkBridge(work=(20,))
    with client_for(bridge) as client:
        client.app.state.db_pool = SimpleNamespace(is_open=True)
        client.app.state.current_run_id = 7
        with patch("backend.app.router._persist_snapshot"), \
             patch("backend.app.router.get_connection") as connection, \
             patch("backend.app.router.get_active_assignments_for_run", return_value=[]), \
             patch("backend.app.router.cancel_unfinished_jobs_for_run") as cancel, \
             patch("backend.app.router.release_active_assignments_for_run") as release, \
             patch("backend.app.router.batch_sync_crane_statuses",
                   side_effect=lambda conn, statuses: len(statuses)), \
             patch("backend.app.router.close_simulation_run") as close_run, \
             patch("backend.app.router.insert_simulation_run", return_value=8) as insert_run:
            assert client.post("/simulation/run", json={"interval_seconds": 0.05}).status_code == 200
            wait_for_work(bridge)
            assert client.post("/simulation/reset").status_code == 200
            conn = connection.return_value.__enter__.return_value
            cancel.assert_called_once_with(conn, 7)
            release.assert_called_once_with(conn, 7, "SIMULATION_RESET")
            close_run.assert_called_once_with(conn, 7, "FAILED")
            insert_run.assert_not_called()
            assert bridge.jobs == []
            assert client.get("/simulation/snapshot").json()["cranes"] == []
            assert client.app.state.current_run_id is None
            assert client.get("/simulation/execution").json()["state"] == "not_running"
            assert client.post("/simulation/init", json={
                "berths": 1, "cranes": 1, "trucks": 0,
            }).status_code == 200


def test_reset_preserves_completed_history_and_refuses_unavailable_database():
    bridge = WorkBridge(work=(1,))
    with client_for(bridge) as client:
        client.app.state.current_run_id = 9
        assert client.post("/simulation/reset").status_code == 503
        assert bridge.jobs[0]["state"] == "ACTIVE"
        assert not any(command["cmd"] == "reset" for command in bridge.commands)

        bridge.jobs[0]["state"] = "COMPLETED"
        client.app.state.db_pool = SimpleNamespace(is_open=True)
        with patch("backend.app.router.get_connection") as connection, \
             patch("backend.app.router.get_active_assignments_for_run", return_value=[]), \
             patch("backend.app.router.cancel_unfinished_jobs_for_run") as cancel, \
             patch("backend.app.router.release_active_assignments_for_run") as release, \
             patch("backend.app.router.batch_sync_crane_statuses",
                   side_effect=lambda conn, statuses: len(statuses)), \
             patch("backend.app.router.close_simulation_run") as close_run:
            assert client.post("/simulation/reset").status_code == 200
            conn = connection.return_value.__enter__.return_value
            cancel.assert_called_once_with(conn, 9)
            release.assert_called_once_with(conn, 9, "SIMULATION_RESET")
            close_run.assert_called_once_with(conn, 9, "COMPLETED")
            assert client.app.state.current_run_id is None


def test_actual_cpp_bridge_step_and_run():
    if os.name != "nt":
        pytest.skip("Windows C++ executable integration test")
    executable = Path(__file__).resolve().parents[2] / "os-simulation" / "port_json_iface.exe"
    if not executable.exists():
        pytest.skip("C++ JSON-lines executable has not been built")
    bridge = CppBridge(exe_path=executable)
    bridge.start()
    try:
        with client_for(bridge) as client:
            assert client.post("/simulation/init", json={
                "berths": 1, "cranes": 1, "trucks": 1, "batch_mode": True
            }).status_code == 200
            client.app.state.db_pool = SimpleNamespace(is_open=True)
            with patch("backend.app.router.get_connection") as connection, \
                 patch("backend.app.router.get_crane", return_value={"crane_id": "C1", "status": "AVAILABLE"}), \
                 patch("backend.app.router.get_active_assignments_for_cranes", return_value=[]):
                connection.return_value.__enter__.return_value = object()
                assert client.post("/simulation/cranes", json={"crane_id": "C1"}).status_code == 200
            client.app.state.current_run_id = 7
            with patch("backend.app.router.get_connection") as connection, \
                 patch("backend.app.router.ship_exists", return_value=True), \
                 patch("backend.app.router.ship_has_job_in_run", return_value=False), \
                 patch("backend.app.router.insert_simulation_job"), \
                 patch("backend.app.router._persist_snapshot"):
                client.app.state.db_pool = SimpleNamespace(is_open=True)
                connection.return_value.__enter__.return_value = object()
                assert client.post("/simulation/jobs", json={
                    "ship_id": 1, "job_id": "J1", "arrival_time": 0, "work_total": 2,
                    "priority": 1, "berths": 1, "cranes": 1, "trucks": 1,
                }).status_code == 200
            client.app.state.current_run_id = None
            client.app.state.db_pool = None
            assert client.post("/simulation/step").json()["advanced_jobs"] == []
            client.post("/simulation/run", json={"interval_seconds": 0.05})
            wait_for(client, "completed")
            job = client.get("/simulation/snapshot").json()["jobs"][0]
            assert (job["state"], job["work_completed"], job["work_remaining"]) == (
                "COMPLETED", 2, 0,
            )
    finally:
        bridge.shutdown()


def test_application_shutdown_waits_for_runner():
    bridge = WorkBridge(work=(10,))
    bridge.start = lambda: None
    stopped = []
    bridge.shutdown = lambda: stopped.append(True)

    class Pool:
        is_open = False

        def open(self):
            pass

        def acquire_session_ownership(self):
            return True

        def getconn(self):
            return SimpleNamespace(commit=lambda: None, rollback=lambda: None)

        def putconn(self, conn, close=False):
            pass

        def close(self):
            pass

    with patch("backend.app.main.CppBridge", return_value=bridge), \
         patch("backend.app.main.DatabasePool", return_value=Pool()), \
         patch("backend.app.main.initialize_backend_inventory"):
        with TestClient(main_app, raise_server_exceptions=False) as client:
            client.post("/simulation/run", json={"interval_seconds": 0.1})
            wait_for_work(bridge)
        count = bridge.jobs[0]["work_completed"]
        time.sleep(0.15)
        assert bridge.jobs[0]["work_completed"] == count
        assert stopped == [True]


def test_startup_cleanup_runs_once_and_api_refresh_does_not_repeat_it():
    bridge = WorkBridge(work=(1,))
    bridge.start = lambda: None
    bridge.shutdown = lambda: None

    class Pool:
        is_open = True

        def open(self):
            pass

        def acquire_session_ownership(self):
            return True

        def getconn(self):
            return SimpleNamespace(commit=lambda: None, rollback=lambda: None)

        def putconn(self, conn, close=False):
            pass

        def close(self):
            pass

    with patch("backend.app.main.CppBridge", return_value=bridge), \
         patch("backend.app.main.DatabasePool", return_value=Pool()), \
         patch("backend.app.main.initialize_backend_inventory") as initialize, \
         patch("backend.app.router.get_resource_inventory", return_value={
             "terminals": [], "berths": [], "cranes": [],
             "ships": [{"ship_id": 1, "vessel_name": "A", "status": "NOT ARRIVED"}],
         }) as inventory:
        with TestClient(main_app, raise_server_exceptions=False) as client:
            assert client.get("/simulation/execution").status_code == 200
            assert client.get("/simulation/execution").status_code == 200
            assert client.get("/inventory").json()["ships"][0]["status"] == "NOT ARRIVED"
            inventory.assert_called_once()
            assert inventory.call_args.args[1] is None
            initialize.assert_called_once()


def test_competing_backend_startup_does_not_reset_inventory():
    bridge = WorkBridge(work=(1,))
    bridge.start = lambda: None
    bridge.shutdown = lambda: None

    class ContendedPool:
        def open(self):
            pass

        def acquire_session_ownership(self):
            return False

        def close(self):
            pass

    with patch("backend.app.main.CppBridge", return_value=bridge), \
         patch("backend.app.main.DatabasePool", return_value=ContendedPool()), \
         patch("backend.app.main.initialize_backend_inventory") as initialize:
        with pytest.raises(RuntimeError, match="Another backend owns"):
            with TestClient(main_app):
                pass
    initialize.assert_not_called()
