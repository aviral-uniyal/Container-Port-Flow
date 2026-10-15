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


class FakeCursor:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def execute(self, query: str) -> None:
        if not self.healthy:
            raise RuntimeError("Database query failed: connection reset")

    def fetchone(self):
        if self.healthy:
            return (1,)
        return None


class FakeConn:
    def __init__(self, healthy: bool = True, fail_rollback: bool = False) -> None:
        self.healthy = healthy
        self.fail_rollback = fail_rollback
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(healthy=self.healthy)

    def rollback(self) -> None:
        if self.fail_rollback:
            raise RuntimeError("Database rollback failed")
        self.rollbacks += 1


class FakeDBPool:
    """Drop-in replacement for DatabasePool used in tests."""

    def __init__(
        self,
        is_open: bool = True,
        healthy: bool = True,
        fail_getconn: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self._is_open = is_open
        self._healthy = healthy
        self._fail_getconn = fail_getconn
        self._fail_rollback = fail_rollback
        self.borrowed = 0
        self.returned = 0
        self.last_conn: FakeConn | None = None

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def getconn(self):
        if not self._is_open:
            raise RuntimeError("Database pool is not open")
        if self._fail_getconn:
            raise RuntimeError("Database pool exhausted")
        self.borrowed += 1
        conn = FakeConn(healthy=self._healthy, fail_rollback=self._fail_rollback)
        self.last_conn = conn
        return conn

    def putconn(self, conn, close: bool = False) -> None:
        self.returned += 1


def _client(
    bridge: FakeBridge | None = None,
    db_pool: FakeDBPool | None = None,
) -> TestClient:
    """Return a TestClient whose bridge and db_pool are replaced with fakes."""
    if bridge is None:
        bridge = FakeBridge()
    if db_pool is None:
        db_pool = FakeDBPool()
    app.state.bridge = bridge
    app.state.db_pool = db_pool
    return TestClient(app, raise_server_exceptions=False)


# ─── /health ──────────────────────────────────────────────────────────────────


class TestHealth:
    """GET /health reporting bridge and database reachability."""

    def test_health_all_ready(self):
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(FakeBridge(alive=True), pool)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["bridge_alive"] is True
        assert data["db_connected"] is True
        assert pool.borrowed == 1
        assert pool.returned == 1
        assert pool.last_conn is not None
        assert pool.last_conn.rollbacks == 1

    def test_health_db_query_failed(self):
        pool = FakeDBPool(is_open=True, healthy=False)
        client = _client(FakeBridge(alive=True), pool)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["bridge_alive"] is True
        assert data["db_connected"] is False
        assert pool.borrowed == 1
        assert pool.returned == 1
        assert pool.last_conn is not None
        assert pool.last_conn.rollbacks == 1

    def test_health_db_rollback_failed_still_returns_conn(self):
        pool = FakeDBPool(is_open=True, healthy=True, fail_rollback=True)
        client = _client(FakeBridge(alive=True), pool)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["bridge_alive"] is True
        assert data["db_connected"] is True
        assert pool.borrowed == 1
        assert pool.returned == 1

    def test_health_db_getconn_failed(self):
        pool = FakeDBPool(is_open=True, fail_getconn=True)
        client = _client(FakeBridge(alive=True), pool)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["bridge_alive"] is True
        assert data["db_connected"] is False

    def test_health_db_pool_closed(self):
        pool = FakeDBPool(is_open=False)
        client = _client(FakeBridge(alive=True), pool)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["db_connected"] is False
        assert pool.borrowed == 0

    def test_health_no_db_pool(self):
        app.state.bridge = FakeBridge(alive=True)
        app.state.db_pool = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["db_connected"] is False

    def test_health_bridge_unavailable(self):
        client = _client(FakeBridge(alive=False), FakeDBPool(is_open=True, healthy=True))
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["bridge_alive"] is False
        assert data["db_connected"] is True

    def test_health_no_bridge(self):
        app.state.bridge = None
        app.state.db_pool = FakeDBPool(is_open=True, healthy=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["bridge_alive"] is False
        assert data["db_connected"] is True


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


# ─── Persistence: POST /simulation/init ───────────────────────────────────────


from unittest.mock import MagicMock, patch


class TestInitPersistence:
    """Database persistence after successful C++ init."""

    _INIT_BODY = {"berths": 4, "cranes": 2, "trucks": 6}

    @patch("backend.app.router.insert_simulation_run")
    @patch("backend.app.router.get_connection")
    def test_init_creates_simulation_run(
        self, mock_get_conn: MagicMock, mock_insert: MagicMock
    ):
        """Successful C++ init → simulation_runs row inserted, run_id stored."""
        # Setup: mock the DB context manager and insert helper.
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_insert.return_value = 42  # returned run_id

        fake = FakeBridge(response={"ok": True, "policy": "FCFS"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.post("/simulation/init", json=self._INIT_BODY)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify insert_simulation_run was called with correct parameters.
        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args[1]
        assert call_kwargs["scheduling_policy"] == "FCFS"
        assert call_kwargs["total_berths"] == 4
        assert call_kwargs["total_cranes"] == 2
        assert call_kwargs["total_trucks"] == 6
        assert call_kwargs["run_name"].startswith("Run ")

        # Verify run_id stored on app state.
        assert getattr(app.state, "current_run_id", None) == 42

    @patch("backend.app.router.insert_simulation_run")
    @patch("backend.app.router.get_connection")
    def test_init_with_policy_persists_confirmed_policy(
        self, mock_get_conn: MagicMock, mock_insert: MagicMock
    ):
        """The persisted scheduling_policy comes from the C++ response."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_insert.return_value = 7

        fake = FakeBridge(response={"ok": True, "policy": "PRIORITY"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.post(
            "/simulation/init",
            json=dict(self._INIT_BODY, policy="PRIORITY"),
        )

        assert resp.status_code == 200
        call_kwargs = mock_insert.call_args[1]
        assert call_kwargs["scheduling_policy"] == "PRIORITY"
        assert getattr(app.state, "current_run_id", None) == 7

    @patch("backend.app.router.insert_simulation_run")
    @patch("backend.app.router.get_connection")
    def test_init_db_failure_still_returns_200(
        self, mock_get_conn: MagicMock, mock_insert: MagicMock
    ):
        """If the DB write raises, C++ returns 200 and current_run_id is None."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_insert.side_effect = RuntimeError("connection refused")

        fake = FakeBridge(response={"ok": True, "policy": "FCFS"})
        pool = FakeDBPool(is_open=True, healthy=True)
        app.state.current_run_id = 999  # stale run_id from prior run
        client = _client(fake, pool)

        resp = client.post("/simulation/init", json=self._INIT_BODY)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert getattr(app.state, "current_run_id", None) is None

    def test_init_no_db_pool_still_returns_200(self):
        """No database pool → skip persistence, return 200, current_run_id is None."""
        fake = FakeBridge(response={"ok": True, "policy": "SJF"})
        app.state.bridge = fake
        app.state.db_pool = None
        app.state.current_run_id = 999  # stale run_id from prior run
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/simulation/init", json=self._INIT_BODY)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["policy"] == "SJF"
        assert getattr(app.state, "current_run_id", None) is None

    def test_init_db_pool_closed_still_returns_200(self):
        """DB pool exists but is_open=False → skip persistence, current_run_id is None."""
        fake = FakeBridge(response={"ok": True, "policy": "FCFS"})
        pool = FakeDBPool(is_open=False)
        app.state.current_run_id = 999  # stale run_id from prior run
        client = _client(fake, pool)

        resp = client.post("/simulation/init", json=self._INIT_BODY)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert getattr(app.state, "current_run_id", None) is None


# ─── Persistence: POST /simulation/cranes ─────────────────────────────────────


class TestCranePersistence:
    """Database persistence after successful C++ crane registration."""

    @patch("backend.app.router.upsert_crane")
    @patch("backend.app.router.get_connection")
    def test_crane_registration_persists_crane(
        self, mock_get_conn: MagicMock, mock_upsert: MagicMock
    ):
        """Successful C++ registration → upsert_crane called with correct values."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_upsert.return_value = "CRANE_01"

        fake = FakeBridge(response={"ok": True, "crane_id": "CRANE_01"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.post("/simulation/cranes", json={"crane_id": "CRANE_01"})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["crane_id"] == "CRANE_01"

        # Verify upsert_crane was called with the correct arguments.
        mock_upsert.assert_called_once()
        call_kwargs = mock_upsert.call_args[1]
        assert call_kwargs["crane_id"] == "CRANE_01"
        assert call_kwargs["terminal_id"] == 1
        assert call_kwargs["status"] == "AVAILABLE"

    @patch("backend.app.router.upsert_crane")
    @patch("backend.app.router.get_connection")
    def test_crane_db_failure_still_returns_200(
        self, mock_get_conn: MagicMock, mock_upsert: MagicMock
    ):
        """If the DB write raises, C++ success still returns 200."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_upsert.side_effect = RuntimeError("connection refused")

        fake = FakeBridge(response={"ok": True, "crane_id": "C1"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.post("/simulation/cranes", json={"crane_id": "C1"})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["crane_id"] == "C1"

    def test_crane_no_db_pool_still_returns_200(self):
        """No database pool → skip persistence, return 200."""
        fake = FakeBridge(response={"ok": True, "crane_id": "C1"})
        app.state.bridge = fake
        app.state.db_pool = None
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/simulation/cranes", json={"crane_id": "C1"})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["crane_id"] == "C1"

    def test_crane_db_pool_closed_still_returns_200(self):
        """DB pool exists but is_open=False → skip persistence, return 200."""
        fake = FakeBridge(response={"ok": True, "crane_id": "C1"})
        pool = FakeDBPool(is_open=False)
        client = _client(fake, pool)

        resp = client.post("/simulation/cranes", json={"crane_id": "C1"})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["crane_id"] == "C1"

    @patch("backend.app.router.upsert_crane")
    def test_crane_cpp_failure_does_not_persist(self, mock_upsert: MagicMock):
        """Failed C++ registration → upsert_crane never called."""
        fake = FakeBridge(response={"ok": False, "error": "duplicate crane"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.post("/simulation/cranes", json={"crane_id": "C1"})

        assert resp.status_code == 400
        mock_upsert.assert_not_called()


# ─── Persistence: POST /simulation/jobs ───────────────────────────────────────


class TestJobSubmissionPersistence:
    """Database persistence after successful C++ job submission."""

    _VALID_JOB = {
        "job_id": "J1",
        "arrival_time": 0,
        "work_total": 10,
        "priority": 1,
        "berths": 1,
        "cranes": 2,
        "trucks": 0,
    }

    @patch("backend.app.router.insert_simulation_job")
    @patch("backend.app.router.get_connection")
    def test_job_submission_persists_job(
        self, mock_get_conn: MagicMock, mock_insert: MagicMock
    ):
        """Successful C++ submit → insert_simulation_job called with correct values."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_insert.return_value = 101

        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "PENDING"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs", json=self._VALID_JOB)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["job_id"] == "J1"

        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args[1]
        assert call_kwargs["run_id"] == 42
        assert call_kwargs["job_id"] == "J1"
        assert call_kwargs["arrival_time"] == 0
        assert call_kwargs["priority"] == 1
        assert call_kwargs["work_total"] == 10
        assert call_kwargs["requested_berths"] == 1
        assert call_kwargs["requested_cranes"] == 2
        assert call_kwargs["requested_trucks"] == 0

    @patch("backend.app.router.insert_simulation_job")
    @patch("backend.app.router.get_connection")
    def test_job_submission_db_failure_still_returns_200(
        self, mock_get_conn: MagicMock, mock_insert: MagicMock
    ):
        """If the DB write raises, C++ success still returns 200."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_insert.side_effect = RuntimeError("connection refused")

        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "PENDING"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs", json=self._VALID_JOB)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["job_id"] == "J1"

    def test_job_submission_no_run_id_still_returns_200(self):
        """No current_run_id → skip persistence, return 200."""
        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "PENDING"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = None

        resp = client.post("/simulation/jobs", json=self._VALID_JOB)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_job_submission_no_db_pool_still_returns_200(self):
        """No database pool → skip persistence, return 200."""
        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "PENDING"})
        app.state.bridge = fake
        app.state.db_pool = None
        app.state.current_run_id = 42
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/simulation/jobs", json=self._VALID_JOB)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_job_submission_db_pool_closed_still_returns_200(self):
        """DB pool exists but is_open=False → skip persistence, return 200."""
        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "PENDING"})
        pool = FakeDBPool(is_open=False)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs", json=self._VALID_JOB)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("backend.app.router.insert_simulation_job")
    def test_job_submission_cpp_failure_does_not_persist(
        self, mock_insert: MagicMock
    ):
        """Failed C++ submission → insert_simulation_job never called."""
        fake = FakeBridge(response={"ok": False, "error": "duplicate job"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs", json=self._VALID_JOB)

        assert resp.status_code == 400
        mock_insert.assert_not_called()


# ─── Persistence: POST /simulation/jobs/{job_id}/complete ─────────────────────


class TestJobCompletionPersistence:
    """Database persistence after successful C++ job completion."""

    @patch("backend.app.router.update_job_status")
    @patch("backend.app.router.get_connection")
    def test_job_completion_updates_status(
        self, mock_get_conn: MagicMock, mock_update: MagicMock
    ):
        """Successful C++ complete → update_job_status called with COMPLETED."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_update.return_value = True

        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "COMPLETED"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs/J1/complete")

        assert resp.status_code == 200
        assert resp.json()["state"] == "COMPLETED"

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs["run_id"] == 42
        assert call_kwargs["job_id"] == "J1"
        assert call_kwargs["status"] == "COMPLETED"

    @patch("backend.app.router.update_job_status")
    @patch("backend.app.router.get_connection")
    def test_job_completion_db_failure_still_returns_200(
        self, mock_get_conn: MagicMock, mock_update: MagicMock
    ):
        """If the DB update raises, C++ success still returns 200."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_update.side_effect = RuntimeError("connection refused")

        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "COMPLETED"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs/J1/complete")

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["state"] == "COMPLETED"

    def test_job_completion_no_run_id_still_returns_200(self):
        """No current_run_id → skip persistence, return 200."""
        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "COMPLETED"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = None

        resp = client.post("/simulation/jobs/J1/complete")

        assert resp.status_code == 200
        assert resp.json()["state"] == "COMPLETED"

    def test_job_completion_no_db_pool_still_returns_200(self):
        """No database pool → skip persistence, return 200."""
        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "COMPLETED"})
        app.state.bridge = fake
        app.state.db_pool = None
        app.state.current_run_id = 42
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/simulation/jobs/J1/complete")

        assert resp.status_code == 200
        assert resp.json()["state"] == "COMPLETED"

    def test_job_completion_db_pool_closed_still_returns_200(self):
        """DB pool exists but is_open=False → skip persistence, return 200."""
        fake = FakeBridge(response={"ok": True, "job_id": "J1", "state": "COMPLETED"})
        pool = FakeDBPool(is_open=False)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs/J1/complete")

        assert resp.status_code == 200
        assert resp.json()["state"] == "COMPLETED"

    @patch("backend.app.router.update_job_status")
    def test_job_completion_cpp_failure_does_not_persist(
        self, mock_update: MagicMock
    ):
        """Failed C++ completion → update_job_status never called."""
        fake = FakeBridge(response={"ok": False, "error": "job J99 not found"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs/J99/complete")

        assert resp.status_code == 400
        mock_update.assert_not_called()
