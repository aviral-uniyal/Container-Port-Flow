"""Focused HTTP tests for deterministic simulation execution controls."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
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
        self.commands: list[dict] = []
        self.fail_step = False

    def send(self, command: dict) -> dict:
        with self.lock:
            self.commands.append(command.copy())
            cmd = command["cmd"]
            if cmd == "init":
                self.jobs.clear()
                self.batch = command.get("batch_mode", False)
                return {"ok": True, "policy": command.get("policy", "FCFS"),
                        "batch_mode": self.batch}
            if cmd == "snapshot":
                assigned = next((j["job_id"] for j in self.jobs if j["state"] == "ACTIVE"), "")
                return {"ok": True, "batch_mode": self.batch,
                        "jobs": [j.copy() for j in self.jobs],
                        "cranes": [{"crane_id": "C1",
                                    "status": "FAILED" if self.crane_failed else
                                    "ASSIGNED" if assigned else "AVAILABLE",
                                    "assigned_job": "" if self.crane_failed else assigned}]}
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
        assert client.post("/simulation/cranes/C1/recover").status_code == 200
        client.post("/simulation/run", json={"interval_seconds": 0.05})
        wait_for(client, "completed")
        assert all(j["state"] == "COMPLETED" for j in bridge.jobs)


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
        assert client.get("/simulation/execution").json()["state"] == "paused"
        count = len([c for c in bridge.commands if c["cmd"] == "step"])
        time.sleep(0.15)
        assert len([c for c in bridge.commands if c["cmd"] == "step"]) == count
        assert bridge.jobs == []


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
            assert client.post("/simulation/cranes", json={"crane_id": "C1"}).status_code == 200
            assert client.post("/simulation/jobs", json={
                "job_id": "J1", "arrival_time": 0, "work_total": 2,
                "priority": 1, "berths": 1, "cranes": 1, "trucks": 1,
            }).status_code == 200
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

        def close(self):
            pass

    with patch("backend.app.main.CppBridge", return_value=bridge), \
         patch("backend.app.main.DatabasePool", return_value=Pool()):
        with TestClient(main_app, raise_server_exceptions=False) as client:
            client.post("/simulation/run", json={"interval_seconds": 0.1})
            wait_for_work(bridge)
        count = bridge.jobs[0]["work_completed"]
        time.sleep(0.15)
        assert bridge.jobs[0]["work_completed"] == count
        assert stopped == [True]
