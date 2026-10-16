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
from unittest.mock import MagicMock, patch

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
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def execute(self, query: str, *args, **kwargs) -> None:
        if not self.healthy:
            raise RuntimeError("Database query failed: connection reset")

    def fetchone(self):
        if self.healthy:
            return (1,)
        return None

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, healthy: bool = True, fail_rollback: bool = False) -> None:
        self.healthy = healthy
        self.fail_rollback = fail_rollback
        self.rollbacks = 0
        self.commits = 0

    def cursor(self, *args, **kwargs):
        return FakeCursor(healthy=self.healthy)

    def commit(self) -> None:
        self.commits += 1

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
    app.state.current_run_id = None
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

    def test_complete_job_undersupplied_rejected_returns_400(self):
        """Premature completion rejected by C++ dispatcher when job is missing required cranes."""
        err_msg = "complete_job: job JOB_NEW requires 2 crane(s) but only 1 are currently ASSIGNED. Recover the missing crane(s) before completing."
        fake = FakeBridge(response={"ok": False, "error": err_msg})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 10

        with patch("backend.app.router.update_job_status") as mock_status:
            resp = client.post("/simulation/jobs/JOB_NEW/complete")
            assert resp.status_code == 400
            assert "Recover the missing crane(s)" in resp.json()["detail"]
            mock_status.assert_not_called()


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
        """Genuine C++ registration failure (non-duplicate) → upsert_crane never called."""
        fake = FakeBridge(response={"ok": False, "error": "dispatcher not initialised; send init first"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.post("/simulation/cranes", json={"crane_id": "C1"})

        assert resp.status_code == 400
        mock_upsert.assert_not_called()

    @patch("backend.app.router.upsert_crane")
    @patch("backend.app.router.get_connection")
    def test_crane_duplicate_registration_is_idempotent(
        self, mock_get_conn: MagicMock, mock_upsert: MagicMock
    ):
        """Duplicate C++ registration is idempotent: returns 200 OK and upserts DB record."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_upsert.return_value = "C1"

        fake = FakeBridge(response={"ok": False, "error": "Duplicate crane ID: C1"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.post("/simulation/cranes", json={"crane_id": "C1"})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["crane_id"] == "C1"
        mock_upsert.assert_called_once()
        call_kwargs = mock_upsert.call_args[1]
        assert call_kwargs["crane_id"] == "C1"
        assert call_kwargs["status"] == "AVAILABLE"


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

    @patch("backend.app.router.batch_sync_crane_statuses")
    @patch("backend.app.router.insert_simulation_job")
    @patch("backend.app.router.get_connection")
    def test_job_submission_syncs_assigned_crane_statuses(
        self, mock_get_conn: MagicMock, mock_insert: MagicMock, mock_batch_sync: MagicMock
    ):
        """Job submission dispatches cranes → crane statuses synced to PostgreSQL."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_insert.return_value = 101

        snapshot_data = {
            "ok": True,
            "cranes": [
                {"crane_id": "C1", "status": "ASSIGNED", "assigned_job": "J1"},
                {"crane_id": "CRANE_DEMO_01", "status": "ASSIGNED", "assigned_job": "J1"},
            ],
        }
        submit_data = {"ok": True, "job_id": "J1", "state": "ACTIVE"}

        class MultiResponseBridge(FakeBridge):
            def send(self, command: dict[str, Any]) -> dict[str, Any]:
                self.last_command = command
                if command.get("cmd") == "snapshot":
                    return snapshot_data
                return submit_data

        fake = MultiResponseBridge()
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs", json=self._VALID_JOB)

        assert resp.status_code == 200
        mock_batch_sync.assert_called_once()
        synced = mock_batch_sync.call_args[0][1]
        assert {"crane_id": "C1", "status": "ASSIGNED"} in synced
        assert {"crane_id": "CRANE_DEMO_01", "status": "ASSIGNED"} in synced


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

    @patch("backend.app.router.release_active_crane_assignments_by_job")
    @patch("backend.app.router.get_simulation_job_id", return_value=101)
    @patch("backend.app.router.update_job_status")
    @patch("backend.app.router.get_connection")
    def test_job_completion_releases_crane_assignments(
        self,
        mock_get_conn: MagicMock,
        mock_update: MagicMock,
        mock_get_sim_id: MagicMock,
        mock_release_asgn: MagicMock,
    ):
        """Successful C++ completion releases all active crane assignments for the job."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_update.return_value = True

        fake = FakeBridge(response={"ok": True, "job_id": "JOB_NEW", "state": "COMPLETED"})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs/JOB_NEW/complete")
        assert resp.status_code == 200
        mock_get_sim_id.assert_called_once_with(mock_conn, run_id=42, job_id="JOB_NEW")
        mock_release_asgn.assert_called_once_with(
            mock_conn, simulation_job_id=101, release_reason="JOB_COMPLETED"
        )

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

    @patch("backend.app.router.update_job_status")
    @patch("backend.app.router.get_connection")
    def test_job_completion_persists_work_counters(
        self, mock_get_conn: MagicMock, mock_update: MagicMock
    ):
        """Successful completion → update_job_status called; real SQL sets work counters.

        Verifies the SQL generated by the real update_job_status helper contains
        the CASE expressions for work_completed and work_remaining so that a live
        PostgreSQL database would record work_completed = work_total and
        work_remaining = 0 upon job completion.
        """
        from unittest.mock import MagicMock as _MagicMock
        from backend.app.database import update_job_status as real_update_job_status

        # Capture what SQL the real helper would execute by spying on cursor.execute
        captured_sql: list[str] = []

        mock_conn_inner = _MagicMock()
        mock_cur = _MagicMock()
        mock_cur.rowcount = 1

        def capture_execute(sql, params):
            captured_sql.append(sql)

        mock_cur.execute.side_effect = capture_execute
        mock_conn_inner.cursor.return_value.__enter__.return_value = mock_cur

        real_update_job_status(mock_conn_inner, run_id=42, job_id="J1", status="COMPLETED")

        assert len(captured_sql) == 1, "expected exactly one SQL statement"
        sql = captured_sql[0]
        assert "work_completed" in sql, "SQL must update work_completed"
        assert "work_remaining" in sql, "SQL must update work_remaining"
        assert "work_total" in sql, "SQL must reference work_total for COMPLETED branch"
        assert "ELSE work_completed" in sql, "non-COMPLETED branch must preserve work_completed"
        assert "ELSE work_remaining" in sql, "non-COMPLETED branch must preserve work_remaining"

    @patch("backend.app.router.batch_sync_crane_statuses")
    @patch("backend.app.router.update_job_status")
    @patch("backend.app.router.get_connection")
    def test_job_completion_syncs_released_crane_statuses(
        self, mock_get_conn: MagicMock, mock_update: MagicMock, mock_batch_sync: MagicMock
    ):
        """Job completion releases cranes → crane statuses synced back to AVAILABLE."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_update.return_value = True

        snapshot_data = {
            "ok": True,
            "cranes": [
                {"crane_id": "C1", "status": "AVAILABLE", "assigned_job": ""},
                {"crane_id": "CRANE_DEMO_01", "status": "AVAILABLE", "assigned_job": ""},
            ],
        }
        complete_data = {"ok": True, "job_id": "J1", "state": "COMPLETED"}

        class MultiResponseBridge(FakeBridge):
            def send(self, command: dict[str, Any]) -> dict[str, Any]:
                self.last_command = command
                if command.get("cmd") == "snapshot":
                    return snapshot_data
                return complete_data

        fake = MultiResponseBridge()
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 42

        resp = client.post("/simulation/jobs/J1/complete")

        assert resp.status_code == 200
        mock_batch_sync.assert_called_once()
        synced = mock_batch_sync.call_args[0][1]
        assert {"crane_id": "C1", "status": "AVAILABLE"} in synced
        assert {"crane_id": "CRANE_DEMO_01", "status": "AVAILABLE"} in synced


# ─── Endpoint: GET /inventory ─────────────────────────────────────────────────


class TestInventoryEndpoint:
    """Read-only physical infrastructure endpoint backed by PostgreSQL."""

    def test_inventory_no_db_pool_returns_503(self):
        """When db_pool is None, returns 503 Database unavailable."""
        fake = FakeBridge()
        app.state.bridge = fake
        app.state.db_pool = None
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/inventory")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Database unavailable"

    def test_inventory_db_pool_closed_returns_503(self):
        """When db_pool is closed, returns 503 error."""
        fake = FakeBridge()
        pool = FakeDBPool(is_open=False)
        client = _client(fake, pool)

        resp = client.get("/inventory")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Database unavailable"

    def test_inventory_db_exception_returns_503(self):
        """When DB execution raises an error, returns 503."""
        fake = FakeBridge()
        pool = FakeDBPool(is_open=True, healthy=False)
        client = _client(fake, pool)

        resp = client.get("/inventory")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Database error while fetching inventory"

    @patch("backend.app.router.get_resource_inventory")
    @patch("backend.app.router.get_connection")
    def test_inventory_success_returns_data(
        self, mock_get_conn: MagicMock, mock_get_inv: MagicMock
    ):
        """Successful fetch returns 200 with terminals, berths, and cranes."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        canned_inventory = {
            "terminals": [
                {"terminal_id": 1, "terminal_name": "Terminal Alpha", "description": "North Gate"}
            ],
            "berths": [
                {"berth_id": 101, "terminal_id": 1, "berth_name": "B-01"}
            ],
            "cranes": [
                {
                    "crane_id": "CR-1",
                    "terminal_id": 1,
                    "status": "AVAILABLE",
                    "updated_at": "2026-10-15T12:00:00+00:00",
                }
            ],
        }
        mock_get_inv.return_value = canned_inventory

        fake = FakeBridge()
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.get("/inventory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["terminals"][0]["terminal_name"] == "Terminal Alpha"
        assert data["berths"][0]["berth_name"] == "B-01"
        assert data["cranes"][0]["crane_id"] == "CR-1"
        mock_get_inv.assert_called_once_with(mock_conn)

    @patch("backend.app.router.get_resource_inventory")
    @patch("backend.app.router.get_connection")
    def test_inventory_works_when_bridge_is_down(
        self, mock_get_conn: MagicMock, mock_get_inv: MagicMock
    ):
        """Inventory is independent of C++ simulation runtime state."""
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_inv.return_value = {"terminals": [], "berths": [], "cranes": []}

        fake = FakeBridge(alive=False)
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        resp = client.get("/inventory")
        assert resp.status_code == 200
        assert resp.json() == {"terminals": [], "berths": [], "cranes": []}


# ─── Crane Failure & Recovery ─────────────────────────────────────────────────


class TestSimulationCraneFail:
    """POST /simulation/cranes/{crane_id}/fail"""

    def test_fail_idle_crane_success(self):
        """Failing an AVAILABLE crane sends idle_crane_failed, updates DB and records disruption."""
        commands = []
        snap_response = {
            "ok": True,
            "cranes": [{"crane_id": "CR-1", "status": "AVAILABLE", "assigned_job": ""}],
            "jobs": [],
        }

        def bridge_handler(cmd):
            commands.append(cmd)
            if cmd.get("cmd") == "snapshot":
                return snap_response
            if cmd.get("cmd") == "idle_crane_failed":
                return {
                    "ok": True,
                    "crane_id": cmd["crane_id"],
                    "affected_job_id": "",
                    "job_interrupted": False,
                    "dispatched_next": False,
                }
            return {"ok": True}

        fake = FakeBridge()
        fake.send = bridge_handler
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 10

        with patch("backend.app.router.update_crane_status", return_value=True) as mock_update, \
             patch("backend.app.router.insert_disruption_event", return_value=1) as mock_event:
            resp = client.post("/simulation/cranes/CR-1/fail")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["crane_id"] == "CR-1"
            assert data["status"] == "FAILED"
            assert data["affected_job_id"] == ""
            assert data["job_interrupted"] is False

            mock_update.assert_called_once()
            assert mock_update.call_args.kwargs["crane_id"] == "CR-1"
            assert mock_update.call_args.kwargs["status"] == "FAILED"

            mock_event.assert_called_once()
            assert mock_event.call_args.kwargs["crane_id"] == "CR-1"
            assert mock_event.call_args.kwargs["event_type"] == "CRANE_FAILED"

    def test_fail_assigned_crane_success(self):
        """Failing an ASSIGNED crane sends crane_failed, interrupts job, and updates work progress."""
        commands = []
        snap_response = {
            "ok": True,
            "cranes": [{"crane_id": "CR-2", "status": "ASSIGNED", "assigned_job": "JOB-99"}],
            "jobs": [{"job_id": "JOB-99", "work_completed": 4, "work_remaining": 6, "state": "ACTIVE"}],
        }

        def bridge_handler(cmd):
            commands.append(cmd)
            if cmd.get("cmd") == "snapshot":
                return snap_response
            if cmd.get("cmd") == "crane_failed":
                return {
                    "ok": True,
                    "crane_id": cmd["crane_id"],
                    "affected_job_id": "JOB-99",
                    "job_interrupted": True,
                    "dispatched_next": False,
                }
            return {"ok": True}

        fake = FakeBridge()
        fake.send = bridge_handler
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 10

        with patch("backend.app.router.update_crane_status", return_value=True) as mock_update, \
             patch("backend.app.router.get_simulation_job_id", return_value=404) as mock_sim_id, \
             patch("backend.app.router.insert_disruption_event", return_value=2) as mock_event, \
             patch("backend.app.router.release_active_crane_assignment_by_crane", return_value=True) as mock_rel, \
             patch("backend.app.router.update_job_status", return_value=True) as mock_job_status, \
             patch("backend.app.router.update_job_work_progress", return_value=True) as mock_job_prog:
            resp = client.post("/simulation/cranes/CR-2/fail")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["crane_id"] == "CR-2"
            assert data["status"] == "FAILED"
            assert data["affected_job_id"] == "JOB-99"
            assert data["job_interrupted"] is True
            assert data["work_completed"] == 4
            assert data["work_remaining"] == 6

            mock_sim_id.assert_called_once()
            mock_rel.assert_called_once()
            mock_job_status.assert_called_once()
            mock_job_prog.assert_called_once()

    def test_fail_crane_unknown_crane_returns_404(self):
        """Failing a crane not present in C++ runtime returns 404."""
        snap_response = {
            "ok": True,
            "cranes": [{"crane_id": "CR-1", "status": "AVAILABLE"}],
            "jobs": [],
        }
        fake = FakeBridge()
        fake.send = lambda cmd: snap_response
        client = _client(fake)

        resp = client.post("/simulation/cranes/NONEXISTENT/fail")
        assert resp.status_code == 404
        assert "not found in C++ runtime" in resp.json()["detail"]

    def test_fail_crane_already_failed_returns_409(self):
        """Failing an already FAILED crane returns 409 conflict and prevents duplicate disruption."""
        snap_response = {
            "ok": True,
            "cranes": [{"crane_id": "CR-1", "status": "FAILED"}],
            "jobs": [],
        }
        fake = FakeBridge()
        fake.send = lambda cmd: snap_response
        client = _client(fake)

        resp = client.post("/simulation/cranes/CR-1/fail")
        assert resp.status_code == 409
        assert "already FAILED" in resp.json()["detail"]

    def test_fail_crane_bridge_down_returns_503(self):
        """When bridge is down, returns 503."""
        fake = FakeBridge(alive=False)
        client = _client(fake)

        resp = client.post("/simulation/cranes/CR-1/fail")
        assert resp.status_code == 503

    def test_fail_crane_dispatcher_not_init_returns_400(self):
        """When dispatcher is not initialized, returns 400."""
        fake = FakeBridge()
        fake.send = lambda cmd: {"ok": False, "error": "dispatcher not initialised"}
        client = _client(fake)

        resp = client.post("/simulation/cranes/CR-1/fail")
        assert resp.status_code == 400
        assert "dispatcher not initialised" in resp.json()["detail"]

    def test_fail_crane_empty_id_returns_400(self):
        """Whitespace or empty crane_id returns 400."""
        client = _client()
        resp = client.post("/simulation/cranes/%20%20/fail")
        assert resp.status_code == 400

    def test_fail_crane_db_failure_compensates_for_idle_crane(self):
        """When DB persistence fails for an idle crane, attempts safe rollback and returns 500."""
        snap_calls = [0]

        def bridge_handler(cmd):
            if cmd.get("cmd") == "snapshot":
                snap_calls[0] += 1
                if snap_calls[0] == 1:
                    return {"ok": True, "cranes": [{"crane_id": "CR-1", "status": "AVAILABLE"}]}
                return {"ok": True, "cranes": [{"crane_id": "CR-1", "status": "AVAILABLE"}]}
            if cmd.get("cmd") == "idle_crane_failed":
                return {"ok": True, "crane_id": "CR-1", "affected_job_id": "", "job_interrupted": False}
            if cmd.get("cmd") == "crane_recovered":
                return {"ok": True, "crane_id": "CR-1", "job_dispatched": False}
            return {"ok": True}

        fake = FakeBridge()
        fake.send = bridge_handler
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        with patch("backend.app.router.update_crane_status", side_effect=RuntimeError("disk write error")):
            resp = client.post("/simulation/cranes/CR-1/fail")
            assert resp.status_code == 500
            assert "rolled back to AVAILABLE" in resp.json()["detail"]

    def test_fail_crane_db_failure_assigned_crane_reconciliation_required(self):
        """When DB persistence fails for an assigned crane, compensation is unsafe so 500 reports reconciliation required."""
        snap_response = {
            "ok": True,
            "cranes": [{"crane_id": "CR-2", "status": "ASSIGNED", "assigned_job": "JOB-1"}],
            "jobs": [{"job_id": "JOB-1", "work_completed": 1, "work_remaining": 5}],
        }

        def bridge_handler(cmd):
            if cmd.get("cmd") == "snapshot":
                return snap_response
            if cmd.get("cmd") == "crane_failed":
                return {"ok": True, "crane_id": "CR-2", "affected_job_id": "JOB-1", "job_interrupted": True}
            return {"ok": True}

        fake = FakeBridge()
        fake.send = bridge_handler
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 10

        with patch("backend.app.router.update_crane_status", side_effect=RuntimeError("connection drop")):
            resp = client.post("/simulation/cranes/CR-2/fail")
            assert resp.status_code == 500
            assert "Reconciliation required" in resp.json()["detail"]


class TestSimulationCraneRecover:
    """POST /simulation/cranes/{crane_id}/recover"""

    def test_recover_crane_success(self):
        """Recovering a FAILED crane sends crane_recovered, updates DB and disruption events."""
        fake = FakeBridge(response={
            "ok": True,
            "crane_id": "CR-1",
            "job_dispatched": False,
            "dispatched_job_id": "",
        })
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 12

        with patch("backend.app.router.update_crane_status", return_value=True) as mock_update, \
             patch("backend.app.router.insert_disruption_event", return_value=3) as mock_event:
            resp = client.post("/simulation/cranes/CR-1/recover")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["crane_id"] == "CR-1"
            assert data["status"] == "AVAILABLE"
            assert data["job_dispatched"] is False

            mock_update.assert_called_once()
            assert mock_update.call_args.kwargs["status"] == "AVAILABLE"
            mock_event.assert_called_once()
            assert mock_event.call_args.kwargs["event_type"] == "CRANE_RECOVERED"

    def test_recover_crane_dispatches_waiting_job(self):
        """When recovered crane enables dispatch of an interrupted job, dispatched_job_id is returned."""
        fake = FakeBridge(response={
            "ok": True,
            "crane_id": "CR-1",
            "job_dispatched": True,
            "dispatched_job_id": "JOB-88",
        })
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        with patch("backend.app.router.update_crane_status", return_value=True):
            resp = client.post("/simulation/cranes/CR-1/recover")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_dispatched"] is True
            assert data["dispatched_job_id"] == "JOB-88"

    def test_recover_crane_dispatches_waiting_job_with_db_persistence(self):
        """When recovered crane dispatches/resumes a job, PostgreSQL records crane assignment and marks job ACTIVE."""
        fake = FakeBridge(response={
            "ok": True,
            "crane_id": "CR-1",
            "job_dispatched": True,
            "dispatched_job_id": "JOB_NEW",
        })
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 15

        with patch("backend.app.router.update_crane_status", return_value=True), \
             patch("backend.app.router.insert_disruption_event", return_value=1) as mock_event, \
             patch("backend.app.router.get_simulation_job_id", return_value=99) as mock_sim_id, \
             patch("backend.app.router.insert_crane_assignment", return_value=12) as mock_asgn, \
             patch("backend.app.router.update_job_status", return_value=True) as mock_job_status, \
             patch("backend.app.router._sync_crane_statuses"):
            resp = client.post("/simulation/cranes/CR-1/recover")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_dispatched"] is True
            assert data["dispatched_job_id"] == "JOB_NEW"
            assert mock_event.called
            assert mock_sim_id.called
            assert mock_asgn.called
            assert mock_job_status.called

    def test_recover_crane_bridge_down_returns_503(self):
        """When bridge is down during recover, returns 503."""
        fake = FakeBridge(alive=False)
        client = _client(fake)
        resp = client.post("/simulation/cranes/CR-1/recover")
        assert resp.status_code == 503

    def test_recover_crane_rejection_returns_400(self):
        """When C++ rejects recovery (e.g. crane is not FAILED), returns 400."""
        fake = FakeBridge()
        fake.send = lambda cmd: {"ok": False, "error": "Crane CR-1 is not in FAILED state"}
        client = _client(fake)
        resp = client.post("/simulation/cranes/CR-1/recover")
        assert resp.status_code == 400
        assert "not in FAILED state" in resp.json()["detail"]

    def test_recover_crane_db_failure_requires_reconciliation(self):
        """When DB persistence fails after crane recovery, reports 500 with reconciliation required."""
        fake = FakeBridge(response={"ok": True, "crane_id": "CR-1", "job_dispatched": False})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)

        with patch("backend.app.router.update_crane_status", side_effect=RuntimeError("DB dead")):
            resp = client.post("/simulation/cranes/CR-1/recover")
            assert resp.status_code == 500
            assert "Reconciliation required" in resp.json()["detail"]


class TestSimulationReconcile:
    """POST /simulation/reconcile"""

    def test_reconcile_success(self):
        """Reconciliation syncs crane statuses and job states from runtime snapshot to PostgreSQL."""
        snap_response = {
            "ok": True,
            "cranes": [
                {"crane_id": "CR-1", "status": "FAILED"},
                {"crane_id": "CR-2", "status": "AVAILABLE"},
            ],
            "jobs": [
                {"job_id": "JOB-1", "state": "INTERRUPTED", "work_completed": 3, "work_remaining": 7},
            ],
        }
        fake = FakeBridge(response=snap_response)
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 5

        with patch("backend.app.router.batch_sync_crane_statuses", return_value=2) as mock_batch, \
             patch("backend.app.router.update_job_status", return_value=True) as mock_jstatus, \
             patch("backend.app.router.update_job_work_progress", return_value=True) as mock_jprog:
            resp = client.post("/simulation/reconcile")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["reconciled_cranes"] == 2
            assert data["reconciled_jobs"] == 1
            mock_batch.assert_called_once()
            mock_jstatus.assert_called_once()
            mock_jprog.assert_called_once()

    def test_reconcile_no_db_returns_503(self):
        """Reconciliation without DB pool returns 503."""
        fake = FakeBridge()
        app.state.bridge = fake
        app.state.db_pool = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/simulation/reconcile")
        assert resp.status_code == 503


class TestBatchDispatch:
    """POST /simulation/dispatch and batch_mode in init."""

    def test_init_with_batch_mode(self):
        """Init sends batch_mode to C++ bridge."""
        fake = FakeBridge(response={"ok": True, "policy": "FCFS", "batch_mode": True})
        client = _client(fake)
        resp = client.post("/simulation/init", json={
            "berths": 2, "cranes": 2, "trucks": 2, "policy": "FCFS", "batch_mode": True
        })
        assert resp.status_code == 200
        assert fake.last_command.get("batch_mode") is True
        assert resp.json().get("batch_mode") is True

    def test_simulation_dispatch_success(self):
        """Dispatch triggers batch flush in bridge and updates database."""
        fake = FakeBridge(response={"ok": True, "dispatched_jobs": ["J1", "J2"], "count": 2})
        pool = FakeDBPool(is_open=True, healthy=True)
        client = _client(fake, pool)
        app.state.current_run_id = 10

        with patch("backend.app.router.update_job_status") as mock_update, \
             patch("backend.app.router._sync_crane_statuses") as mock_sync:
            resp = client.post("/simulation/dispatch")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["dispatched_jobs"] == ["J1", "J2"]
            assert data["count"] == 2
            assert fake.last_command == {"cmd": "dispatch_batch"}
            assert mock_update.call_count == 2
            mock_sync.assert_called_once()

    def test_simulation_dispatch_bridge_down(self):
        """Dispatch returns 503 when bridge is None (unavailable)."""
        pool = FakeDBPool(is_open=True, healthy=True)
        app.state.bridge = None
        app.state.db_pool = pool
        app.state.current_run_id = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/simulation/dispatch")
        assert resp.status_code == 503
