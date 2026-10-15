"""
API tests for the Container-Port-Flow FastAPI layer.

All tests use a **FakeBridge** that simulates CppBridge responses without
spawning the real C++ process.  This keeps tests fast, deterministic, and
independent of the C++ executable.

Run:
    python -m pytest backend/tests/test_api.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure the backend package is importable regardless of cwd.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR.parent))

from fastapi.testclient import TestClient

from backend.app.cpp_bridge import (
    CppBridgeDeadError,
    CppBridgeError,
    CppBridgeTimeoutError,
)
from backend.app.main import app


# ─── Fake bridge ──────────────────────────────────────────────────────────────


class FakeBridge:
    """Drop-in replacement for CppBridge used in tests.

    Attributes
    ----------
    alive : bool
        Controls what ``is_alive`` returns.
    response : dict | None
        The canned response returned by ``send()``.
    error : Exception | None
        If set, ``send()`` raises this instead of returning a response.
    last_command : dict | None
        The last command dict passed to ``send()``, for assertions.
    """

    def __init__(
        self,
        alive: bool = True,
        response: dict[str, Any] | None = None,
    ) -> None:
        self.alive = alive
        self.response: dict[str, Any] = response or {"ok": True}
        self.error: Exception | None = None
        self.last_command: dict[str, Any] | None = None

    @property
    def is_alive(self) -> bool:
        return self.alive

    def send(self, command: dict[str, Any]) -> dict[str, Any]:
        self.last_command = command
        if self.error is not None:
            raise self.error
        return self.response

    def start(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


def _client(bridge: FakeBridge | None = None) -> TestClient:
    """Return a TestClient whose bridge is replaced with a FakeBridge."""
    if bridge is None:
        bridge = FakeBridge()
    app.state.bridge = bridge
    return TestClient(app, raise_server_exceptions=False)


# ─── /health ──────────────────────────────────────────────────────────────────


class TestHealth:
    """GET /health when the bridge is alive and when it is unavailable."""

    def test_health_bridge_alive(self):
        client = _client(FakeBridge(alive=True))
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["bridge_alive"] is True

    def test_health_bridge_unavailable(self):
        client = _client(FakeBridge(alive=False))
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["bridge_alive"] is False

    def test_health_no_bridge(self):
        app.state.bridge = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["bridge_alive"] is False


# ─── POST /simulation/init ────────────────────────────────────────────────────


class TestSimulationInit:
    """Initialisation success and C++ business error."""

    def test_init_success(self):
        fake = FakeBridge(response={"ok": True, "policy": "FCFS"})
        client = _client(fake)
        resp = client.post(
            "/simulation/init",
            json={"berths": 4, "cranes": 2, "trucks": 6},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Verify the command forwarded to C++
        assert fake.last_command["cmd"] == "init"
        assert fake.last_command["berths"] == 4
        assert fake.last_command["cranes"] == 2
        assert fake.last_command["trucks"] == 6
        assert "policy" not in fake.last_command  # not sent when omitted

    def test_init_with_policy(self):
        fake = FakeBridge(response={"ok": True, "policy": "SJF"})
        client = _client(fake)
        resp = client.post(
            "/simulation/init",
            json={"berths": 2, "cranes": 1, "trucks": 3, "policy": "SJF"},
        )
        assert resp.status_code == 200
        assert fake.last_command["policy"] == "SJF"

    def test_init_cpp_business_error(self):
        fake = FakeBridge(
            response={"ok": False, "error": "berths must be > 0"}
        )
        client = _client(fake)
        resp = client.post(
            "/simulation/init",
            json={"berths": 1, "cranes": 1, "trucks": 0},
        )
        assert resp.status_code == 400
        assert "berths must be > 0" in resp.json()["detail"]


# ─── POST /simulation/cranes ─────────────────────────────────────────────────


class TestCraneRegistration:
    """Crane registration success."""

    def test_register_crane_success(self):
        fake = FakeBridge(response={"ok": True, "crane_id": "C1"})
        client = _client(fake)
        resp = client.post(
            "/simulation/cranes",
            json={"crane_id": "C1"},
        )
        assert resp.status_code == 200
        assert resp.json()["crane_id"] == "C1"
        assert fake.last_command["cmd"] == "register_crane"
        assert fake.last_command["crane_id"] == "C1"

    def test_register_crane_empty_id_422(self):
        client = _client()
        resp = client.post(
            "/simulation/cranes",
            json={"crane_id": ""},
        )
        assert resp.status_code == 422


# ─── POST /simulation/jobs ───────────────────────────────────────────────────


class TestJobSubmission:
    """Job submission success and validation failures."""

    _VALID_JOB = {
        "job_id": "J1",
        "arrival_time": 0,
        "work_total": 10,
        "priority": 1,
        "berths": 1,
        "cranes": 2,
        "trucks": 0,
    }

    def test_submit_job_success(self):
        fake = FakeBridge(
            response={"ok": True, "job_id": "J1", "state": "PENDING"}
        )
        client = _client(fake)
        resp = client.post("/simulation/jobs", json=self._VALID_JOB)
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "J1"
        # Verify all fields forwarded
        assert fake.last_command["cmd"] == "submit_job"
        for key in ("job_id", "arrival_time", "work_total", "priority",
                     "berths", "cranes", "trucks"):
            assert fake.last_command[key] == self._VALID_JOB[key]

    def test_submit_job_missing_field_422(self):
        client = _client()
        incomplete = {
            "job_id": "J1",
            "arrival_time": 0,
            # missing work_total, priority, berths, cranes, trucks
        }
        resp = client.post("/simulation/jobs", json=incomplete)
        assert resp.status_code == 422

    def test_submit_job_negative_berths_422(self):
        client = _client()
        bad = dict(self._VALID_JOB, berths=-1)
        resp = client.post("/simulation/jobs", json=bad)
        assert resp.status_code == 422

    def test_submit_job_empty_id_422(self):
        client = _client()
        bad = dict(self._VALID_JOB, job_id="")
        resp = client.post("/simulation/jobs", json=bad)
        assert resp.status_code == 422

    def test_submit_job_zero_work_422(self):
        client = _client()
        bad = dict(self._VALID_JOB, work_total=0)
        resp = client.post("/simulation/jobs", json=bad)
        assert resp.status_code == 422


# ─── POST /simulation/jobs/{job_id}/complete ──────────────────────────────────


class TestJobCompletion:
    """Job completion success and C++ business error."""

    def test_complete_job_success(self):
        fake = FakeBridge(
            response={"ok": True, "job_id": "J1", "state": "COMPLETED"}
        )
        client = _client(fake)
        resp = client.post("/simulation/jobs/J1/complete")
        assert resp.status_code == 200
        assert resp.json()["state"] == "COMPLETED"
        assert fake.last_command["cmd"] == "complete_job"
        assert fake.last_command["job_id"] == "J1"

    def test_complete_job_cpp_error(self):
        fake = FakeBridge(
            response={"ok": False, "error": "job J99 not found"}
        )
        client = _client(fake)
        resp = client.post("/simulation/jobs/J99/complete")
        assert resp.status_code == 400
        assert "J99" in resp.json()["detail"]


# ─── GET /simulation/snapshot ─────────────────────────────────────────────────


class TestSnapshot:
    """Snapshot success."""

    def test_snapshot_success(self):
        snapshot_data = {
            "ok": True,
            "pending_count": 2,
            "active_count": 1,
            "resources": {
                "total_berths": 4,
                "avail_berths": 2,
            },
            "jobs": [],
            "cranes": [],
        }
        fake = FakeBridge(response=snapshot_data)
        client = _client(fake)
        resp = client.get("/simulation/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending_count"] == 2
        assert data["resources"]["total_berths"] == 4
        assert fake.last_command["cmd"] == "snapshot"


# ─── Bridge failure → 503 ────────────────────────────────────────────────────


class TestBridgeFailure:
    """Bridge transport failures are mapped to 503 Service Unavailable."""

    def test_bridge_dead_503(self):
        fake = FakeBridge()
        fake.error = CppBridgeDeadError("process exited")
        client = _client(fake)
        resp = client.post(
            "/simulation/init",
            json={"berths": 1, "cranes": 1, "trucks": 0},
        )
        assert resp.status_code == 503

    def test_bridge_timeout_503(self):
        fake = FakeBridge()
        fake.error = CppBridgeTimeoutError("timed out")
        client = _client(fake)
        resp = client.get("/simulation/snapshot")
        assert resp.status_code == 503

    def test_bridge_generic_error_503(self):
        fake = FakeBridge()
        fake.error = CppBridgeError("unknown transport failure")
        client = _client(fake)
        resp = client.post(
            "/simulation/cranes", json={"crane_id": "C1"}
        )
        assert resp.status_code == 503

    def test_bridge_unavailable_503_on_send_endpoint(self):
        """Bridge not alive → 503 before send() is even called."""
        fake = FakeBridge(alive=False)
        client = _client(fake)
        resp = client.post(
            "/simulation/init",
            json={"berths": 1, "cranes": 1, "trucks": 0},
        )
        assert resp.status_code == 503
