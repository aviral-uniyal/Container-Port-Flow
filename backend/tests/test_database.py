"""
Unit tests for the PostgreSQL persistence foundation (backend/app/database.py).

These tests run entirely in-memory using mocks and monkeypatching.
No live PostgreSQL server is required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.app.config import get_db_config
from backend.app.database import (
    ACTIVE_CRANE_IDS,
    DatabasePool,
    cancel_unfinished_jobs_for_run,
    check_connection,
    close_simulation_run,
    get_active_assignments_for_run,
    get_active_assignments_for_cranes,
    get_connection,
    get_crane,
    get_disruption_events_for_run,
    get_jobs_for_run,
    get_resource_inventory,
    initialize_backend_inventory,
    get_simulation_job_id,
    get_simulation_run,
    insert_crane_assignment,
    insert_disruption_event,
    insert_simulation_job,
    insert_simulation_run,
    make_assigned_cranes_available,
    release_active_assignments_for_run,
    release_active_crane_assignment_by_crane,
    release_active_crane_assignments_by_job,
    release_crane_assignment,
    update_crane_status,
    batch_sync_crane_statuses,
    update_job_status,
    update_job_work_progress,
    upsert_crane,
)


def test_startup_inventory_is_idempotent_and_preserves_history() -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [[(7,), (8,)], []]

    initialize_backend_inventory(conn)
    initialize_backend_inventory(conn)

    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert len(ACTIVE_CRANE_IDS) == 12
    assert ACTIVE_CRANE_IDS == tuple(f"C{i}" for i in range(1, 13))
    assert sum("INSERT INTO cranes" in sql for sql in statements) == 2
    assert all("ON CONFLICT (crane_id) DO NOTHING" in sql for sql in statements
               if "INSERT INTO cranes" in sql)
    assert all("DELETE " not in sql and "TRUNCATE " not in sql for sql in statements)
    assert sum("UPDATE simulation_jobs" in sql for sql in statements) == 1
    cancel_call = next(call for call in cursor.execute.call_args_list
                       if "UPDATE simulation_jobs" in call.args[0])
    assert cancel_call.args[1] == {"run_ids": [7, 8]}
    assert "status IN ('PENDING', 'ACTIVE', 'INTERRUPTED')" in cancel_call.args[0]
    assert sum("UPDATE crane_assignments" in sql for sql in statements) == 2
    assert all("released_at IS NULL" in sql for sql in statements
               if "UPDATE crane_assignments" in sql)
    assert all("crane_id = ANY(%(crane_ids)s)" in sql for sql in statements
               if "UPDATE cranes" in sql)


def test_startup_inventory_transaction_rolls_back_on_error() -> None:
    pool = MagicMock()
    conn = pool.getconn.return_value
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = RuntimeError("isolated database failure")
    with pytest.raises(RuntimeError, match="isolated database failure"):
        with get_connection(pool) as connection:
            initialize_backend_inventory(connection)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_second_backend_cannot_acquire_session_ownership() -> None:
    pool = DatabasePool()
    pool._pool = MagicMock()
    conn = pool._pool.getconn.return_value
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
    assert pool.acquire_session_ownership() is False
    assert pool._owner_conn is None
    pool._pool.putconn.assert_called_once_with(conn, close=False)


def test_reset_queries_are_scoped_and_preserve_failed_cranes() -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.rowcount = 2
    cancel_unfinished_jobs_for_run(conn, 7)
    sql, params = cursor.execute.call_args.args
    assert "run_id = %(run_id)s" in sql
    assert "status IN ('PENDING', 'ACTIVE', 'INTERRUPTED')" in sql
    assert params == {"run_id": 7}

    release_active_assignments_for_run(conn, 7, "SIMULATION_RESET")
    sql, params = cursor.execute.call_args.args
    assert "j.run_id = %(run_id)s" in sql
    assert "a.released_at IS NULL" in sql
    assert params == {"run_id": 7, "reason": "SIMULATION_RESET"}

    make_assigned_cranes_available(conn, ["C1"])
    sql, params = cursor.execute.call_args.args
    assert "status = 'ASSIGNED'" in sql
    assert params == {"crane_ids": ["C1"]}

    close_simulation_run(conn, 7, "COMPLETED")
    sql, params = cursor.execute.call_args.args
    assert "status = 'RUNNING'" in sql
    assert "ended_at = now()" in sql
    assert params == {"run_id": 7, "status": "COMPLETED"}


def test_active_assignment_query_joins_jobs_by_run() -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{"assignment_id": 1, "crane_id": "C1",
                                      "simulation_job_id": 11, "job_id": "J1"}]
    rows = get_active_assignments_for_run(conn, 7)
    assert len(rows) == 1
    sql, params = cursor.execute.call_args.args
    assert "JOIN simulation_jobs" in sql
    assert "j.run_id = %(run_id)s" in sql
    assert params == {"run_id": 7}


def test_active_assignment_query_locks_cranes_and_checks_all_runs() -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{"assignment_id": 1, "crane_id": "C1",
                                      "simulation_job_id": 11, "job_id": "J1",
                                      "run_id": 3, "run_status": "FAILED"}]
    rows = get_active_assignments_for_cranes(conn, ["C2", "C1", "C1"])
    assert rows[0]["run_id"] == 3
    lock_sql, lock_params = cursor.execute.call_args_list[-2].args
    query_sql, query_params = cursor.execute.call_args_list[-1].args
    assert "ORDER BY crane_id FOR UPDATE" in lock_sql
    assert lock_params == {"crane_ids": ["C1", "C2"]}
    assert "JOIN simulation_runs" in query_sql
    assert "j.run_id =" not in query_sql
    assert query_params == lock_params


# ─── 1. Configuration tests ───────────────────────────────────────────────────


class TestDbConfig:
    def test_default_db_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify standard fallback defaults when no PG* env vars are set."""
        for var in ("DATABASE_URL", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"):
            monkeypatch.delenv(var, raising=False)

        cfg = get_db_config()
        assert cfg == {
            "host": "localhost",
            "port": 5432,
            "dbname": "container_port_flow",
            "user": "postgres",
            "password": None,
        }

    def test_custom_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify environment variables override defaults."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("PGHOST", "db.example.internal")
        monkeypatch.setenv("PGPORT", "5433")
        monkeypatch.setenv("PGDATABASE", "test_port")
        monkeypatch.setenv("PGUSER", "port_user")
        monkeypatch.setenv("PGPASSWORD", "secret123")

        cfg = get_db_config()
        assert cfg == {
            "host": "db.example.internal",
            "port": 5433,
            "dbname": "test_port",
            "user": "port_user",
            "password": "secret123",
        }

    def test_database_url_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify DATABASE_URL takes precedence when set."""
        url = "postgresql://custom_user:custom_pass@pgserver:5432/custom_db"
        monkeypatch.setenv("DATABASE_URL", url)

        cfg = get_db_config()
        assert cfg == {"dsn": url}


# ─── 2. DatabasePool Lifecycle tests ──────────────────────────────────────────


class TestDatabasePool:
    def test_initial_state(self) -> None:
        pool = DatabasePool(minconn=2, maxconn=10)
        assert pool.is_open is False
        assert pool._minconn == 2
        assert pool._maxconn == 10

    @patch("backend.app.database.psycopg2.pool.ThreadedConnectionPool")
    def test_open_success(self, mock_pool_cls: MagicMock) -> None:
        pool = DatabasePool(minconn=1, maxconn=5)
        mock_instance = MagicMock()
        mock_pool_cls.return_value = mock_instance

        pool.open()

        assert pool.is_open is True
        mock_pool_cls.assert_called_once()
        _, kwargs = mock_pool_cls.call_args
        assert "host" in kwargs or "dsn" in kwargs

    @patch("backend.app.database.psycopg2.pool.ThreadedConnectionPool")
    def test_open_idempotent(self, mock_pool_cls: MagicMock) -> None:
        pool = DatabasePool()
        pool.open()
        pool.open()
        assert mock_pool_cls.call_count == 1

    @patch("backend.app.database.psycopg2.pool.ThreadedConnectionPool")
    def test_close(self, mock_pool_cls: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_pool_cls.return_value = mock_instance

        pool = DatabasePool()
        pool.open()
        assert pool.is_open is True

        pool.close()
        assert pool.is_open is False
        mock_instance.closeall.assert_called_once()

        # Idempotent close
        pool.close()
        assert mock_instance.closeall.call_count == 1

    def test_getconn_unopened_raises_runtime_error(self) -> None:
        pool = DatabasePool()
        with pytest.raises(RuntimeError, match="Database pool is not open"):
            pool.getconn()

    @patch("backend.app.database.psycopg2.pool.ThreadedConnectionPool")
    def test_getconn_and_putconn_delegation(self, mock_pool_cls: MagicMock) -> None:
        mock_pool_instance = MagicMock()
        mock_conn = MagicMock()
        mock_pool_instance.getconn.return_value = mock_conn
        mock_pool_cls.return_value = mock_pool_instance

        pool = DatabasePool()
        pool.open()

        conn = pool.getconn()
        assert conn is mock_conn
        mock_pool_instance.getconn.assert_called_once()

        pool.putconn(conn, close=True)
        mock_pool_instance.putconn.assert_called_once_with(mock_conn, close=True)


# ─── 3. Context manager tests ─────────────────────────────────────────────────


class TestGetConnectionContextManager:
    def test_get_connection_commits_on_success(self) -> None:
        mock_pool = MagicMock(spec=DatabasePool)
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn

        with get_connection(mock_pool) as conn:
            assert conn is mock_conn

        mock_conn.commit.assert_called_once()
        mock_conn.rollback.assert_not_called()
        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_get_connection_rollbacks_on_error(self) -> None:
        mock_pool = MagicMock(spec=DatabasePool)
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn

        with pytest.raises(ValueError, match="Simulated query error"):
            with get_connection(mock_pool) as conn:
                raise ValueError("Simulated query error")

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()
        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_commit_failure_triggers_rollback_and_returns_connection(self) -> None:
        mock_pool = MagicMock(spec=DatabasePool)
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_conn.commit.side_effect = RuntimeError("Commit failed: serialization conflict")

        with pytest.raises(RuntimeError, match="Commit failed: serialization conflict"):
            with get_connection(mock_pool) as conn:
                pass

        mock_conn.commit.assert_called_once()
        mock_conn.rollback.assert_called_once()
        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_rollback_failure_does_not_mask_original_exception_and_returns_connection(self) -> None:
        mock_pool = MagicMock(spec=DatabasePool)
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_conn.rollback.side_effect = RuntimeError("Rollback failed: connection closed")

        with pytest.raises(ValueError, match="Original query error") as exc_info:
            with get_connection(mock_pool) as conn:
                raise ValueError("Original query error")

        assert str(exc_info.value) == "Original query error"
        mock_conn.rollback.assert_called_once()
        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_original_exception_from_block_remains_visible_if_rollback_fails(self) -> None:
        mock_pool = MagicMock(spec=DatabasePool)
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_conn.rollback.side_effect = Exception("Rollback connection lost")

        class CustomBusinessError(Exception):
            pass

        with pytest.raises(CustomBusinessError, match="Business logic violation") as exc_info:
            with get_connection(mock_pool) as conn:
                raise CustomBusinessError("Business logic violation")

        assert isinstance(exc_info.value, CustomBusinessError)
        assert str(exc_info.value) == "Business logic violation"
        mock_conn.rollback.assert_called_once()
        mock_pool.putconn.assert_called_once_with(mock_conn)


# ─── 4. Health / Connection verification tests ────────────────────────────────


class TestCheckConnection:
    def test_check_connection_true_when_one_returned(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        assert check_connection(mock_conn) is True
        mock_cur.execute.assert_called_once_with("SELECT 1")

    def test_check_connection_false_when_query_fails(self) -> None:
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("DB network drop")

        assert check_connection(mock_conn) is False

    def test_check_connection_false_when_result_not_one(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = None

        assert check_connection(mock_conn) is False


# ─── 5. Simulation Schema Query Helpers tests ─────────────────────────────────


class TestSimulationSchemaQueries:
    # ── Cranes ──
    def test_upsert_crane(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = ("C1",)

        res = upsert_crane(mock_conn, crane_id="C1", terminal_id=1, status="AVAILABLE")
        assert res == "C1"
        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        assert "INSERT INTO cranes" in sql
        assert params["crane_id"] == "C1"
        assert params["terminal_id"] == 1
        assert params["status"] == "AVAILABLE"

    def test_get_crane_found_and_not_found(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = {"crane_id": "C1", "status": "AVAILABLE"}
        crane = get_crane(mock_conn, "C1")
        assert crane == {"crane_id": "C1", "status": "AVAILABLE"}

        mock_cur.fetchone.return_value = None
        crane_missing = get_crane(mock_conn, "C999")
        assert crane_missing is None

    def test_update_crane_status_success(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 1

        updated = update_crane_status(mock_conn, crane_id="C1", status="ASSIGNED")
        assert updated is True
        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        assert "UPDATE cranes" in sql
        assert params["crane_id"] == "C1"
        assert params["status"] == "ASSIGNED"

    def test_update_crane_status_not_found(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 0

        updated = update_crane_status(mock_conn, crane_id="NON_EXISTENT", status="AVAILABLE")
        assert updated is False

    def test_batch_sync_crane_statuses_success(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 1
        mock_cur.fetchone.return_value = ("AVAILABLE",)

        statuses = [
            {"crane_id": "C1", "status": "ASSIGNED"},
            {"crane_id": "CRANE_DEMO_01", "status": "ASSIGNED"},
        ]
        count = batch_sync_crane_statuses(mock_conn, statuses)
        assert count == 2
        assert mock_cur.execute.call_count == 4

    def test_batch_sync_does_not_touch_unchanged_status(self) -> None:
        mock_conn = MagicMock()
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        mock_cur.fetchone.return_value = ("FAILED",)
        assert batch_sync_crane_statuses(mock_conn, [{"crane_id": "C1", "status": "FAILED"}]) == 1
        assert mock_cur.execute.call_count == 1

    def test_batch_sync_crane_statuses_empty_list(self) -> None:
        mock_conn = MagicMock()
        count = batch_sync_crane_statuses(mock_conn, [])
        assert count == 0
        mock_conn.cursor.assert_not_called()

    # ── Simulation Runs ──
    def test_insert_simulation_run(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (42,)

        run_id = insert_simulation_run(
            mock_conn,
            run_name="Run #42",
            scheduling_policy="FCFS",
            total_berths=3,
            total_cranes=5,
            total_trucks=10,
        )
        assert run_id == 42
        sql, params = mock_cur.execute.call_args[0]
        assert "INSERT INTO simulation_runs" in sql
        assert params["run_name"] == "Run #42"
        assert params["scheduling_policy"] == "FCFS"
        assert params["total_berths"] == 3

    def test_get_simulation_run(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = {"run_id": 42, "run_name": "Run #42"}
        run = get_simulation_run(mock_conn, 42)
        assert run == {"run_id": 42, "run_name": "Run #42"}

    # ── Simulation Jobs ──
    def test_insert_simulation_job(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (101,)

        job_pk = insert_simulation_job(
            mock_conn,
            run_id=42,
            job_id="JOB_A",
            arrival_time=0,
            priority=1,
            work_total=10,
            requested_berths=1,
            requested_cranes=2,
            requested_trucks=4,
        )
        assert job_pk == 101
        sql, params = mock_cur.execute.call_args[0]
        assert "INSERT INTO simulation_jobs" in sql
        assert params["run_id"] == 42
        assert params["job_id"] == "JOB_A"
        assert params["work_total"] == 10

    def test_update_job_status_rowcount(self) -> None:
        """update_job_status returns True when a row is updated, False otherwise."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.rowcount = 1
        updated = update_job_status(mock_conn, run_id=42, job_id="JOB_A", status="COMPLETED")
        assert updated is True

        mock_cur.rowcount = 0
        not_found = update_job_status(mock_conn, run_id=42, job_id="JOB_MISSING", status="COMPLETED")
        assert not_found is False

    def test_update_job_status_completed_sets_work_counters(self) -> None:
        """COMPLETED status → SQL includes work_completed = work_total and work_remaining = 0."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 1

        update_job_status(mock_conn, run_id=1, job_id="JOB_W", status="COMPLETED")

        sql, params = mock_cur.execute.call_args[0]
        # Work-counter CASE expressions must be present
        assert "work_completed" in sql
        assert "work_remaining" in sql
        assert "work_total" in sql
        # The status parameter is passed correctly
        assert params["status"] == "COMPLETED"
        assert params["run_id"] == 1
        assert params["job_id"] == "JOB_W"

    def test_update_job_status_non_completed_sql_has_work_counters_unchanged(self) -> None:
        """Non-COMPLETED transitions still include the CASE clauses but leave counters unchanged."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 1

        for status in ("ACTIVE", "CANCELLED", "PENDING"):
            mock_cur.reset_mock()
            update_job_status(mock_conn, run_id=1, job_id="JOB_X", status=status)
            sql, params = mock_cur.execute.call_args[0]
            # CASE clauses exist in SQL but the COMPLETED branch does not apply
            assert "work_completed" in sql
            assert "work_remaining" in sql
            # The ELSE branches preserve existing values, not setting to 0 unconditionally
            assert "ELSE work_completed" in sql
            assert "ELSE work_remaining" in sql
            assert params["status"] == status

    def test_get_jobs_for_run(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            {"job_id": "J1", "status": "COMPLETED"},
            {"job_id": "J2", "status": "ACTIVE"},
        ]

        jobs = get_jobs_for_run(mock_conn, 42)
        assert len(jobs) == 2
        assert jobs[0]["job_id"] == "J1"

    def test_update_job_work_progress(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 1

        updated = update_job_work_progress(
            mock_conn,
            run_id=42,
            job_id="J1",
            work_completed=5,
            work_remaining=5,
        )
        assert updated is True
        sql, params = mock_cur.execute.call_args[0]
        assert "UPDATE simulation_jobs" in sql
        assert params["work_completed"] == 5
        assert params["work_remaining"] == 5

    def test_get_simulation_job_id(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (105,)

        sim_job_id = get_simulation_job_id(mock_conn, run_id=42, job_id="J1")
        assert sim_job_id == 105
        sql, params = mock_cur.execute.call_args[0]
        assert "SELECT simulation_job_id" in sql
        assert params["run_id"] == 42
        assert params["job_id"] == "J1"

    # ── Crane Assignments ──
    def test_insert_and_release_crane_assignment(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (7,)

        asgn_id = insert_crane_assignment(mock_conn, crane_id="C1", simulation_job_id=101)
        assert asgn_id == 7
        sql, params = mock_cur.execute.call_args[0]
        assert "INSERT INTO crane_assignments" in sql
        assert params["crane_id"] == "C1"
        assert params["simulation_job_id"] == 101

        mock_cur.rowcount = 1
        released = release_crane_assignment(mock_conn, assignment_id=7, release_reason="JOB_COMPLETED")
        assert released is True

    def test_release_active_crane_assignment_by_crane(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 1

        released = release_active_crane_assignment_by_crane(
            mock_conn, crane_id="C1", release_reason="CRANE_FAILED"
        )
        assert released is True
        sql, params = mock_cur.execute.call_args[0]
        assert "UPDATE crane_assignments" in sql
        assert params["crane_id"] == "C1"
        assert params["release_reason"] == "CRANE_FAILED"

    def test_release_active_crane_assignments_by_job(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 2

        count = release_active_crane_assignments_by_job(
            mock_conn, simulation_job_id=101, release_reason="JOB_COMPLETED"
        )
        assert count == 2
        sql, params = mock_cur.execute.call_args[0]
        assert "UPDATE crane_assignments" in sql
        assert params["simulation_job_id"] == 101
        assert params["release_reason"] == "JOB_COMPLETED"

    # ── Disruption Events ──
    def test_insert_and_get_disruption_event(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (19,)

        event_id = insert_disruption_event(
            mock_conn,
            run_id=42,
            crane_id="C1",
            event_type="CRANE_FAILED",
            simulation_job_id=101,
            work_completed=4,
            work_remaining=6,
        )
        assert event_id == 19
        sql, params = mock_cur.execute.call_args[0]
        assert "INSERT INTO disruption_events" in sql
        assert params["event_type"] == "CRANE_FAILED"

        mock_cur.fetchall.return_value = [{"event_id": 19, "event_type": "CRANE_FAILED"}]
        events = get_disruption_events_for_run(mock_conn, 42)
        assert len(events) == 1
        assert events[0]["event_id"] == 19


# ─── 6. Resource Inventory Query tests ─────────────────────────────────────────


class TestResourceInventoryQueries:
    def test_get_resource_inventory_empty(self) -> None:
        """Returns empty lists when database tables are empty."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchall.side_effect = [[], [], []]

        inv = get_resource_inventory(mock_conn)
        assert inv == {
            "terminals": [],
            "berths": [],
            "cranes": [],
        }
        assert mock_cur.execute.call_count == 3

    def test_get_resource_inventory_populated(self) -> None:
        """Returns terminals, berths, and cranes with ISO formatted updated_at."""
        import datetime

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        ts = datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        terminals = [
            {"terminal_id": 1, "terminal_name": "Main Terminal", "description": "Primary container terminal"}
        ]
        berths = [
            {"berth_id": 10, "terminal_id": 1, "berth_name": "Berth 1"}
        ]
        cranes = [
            {"crane_id": "C1", "terminal_id": 1, "status": "AVAILABLE", "updated_at": ts},
            {"crane_id": "C2", "terminal_id": 1, "status": "ASSIGNED", "updated_at": None},
        ]
        mock_cur.fetchall.side_effect = [terminals, berths, cranes]

        inv = get_resource_inventory(mock_conn)
        assert len(inv["terminals"]) == 1
        assert inv["terminals"][0]["terminal_id"] == 1
        assert inv["terminals"][0]["terminal_name"] == "Main Terminal"

        assert len(inv["berths"]) == 1
        assert inv["berths"][0]["berth_id"] == 10
        assert inv["berths"][0]["berth_name"] == "Berth 1"

        assert len(inv["cranes"]) == 2
        assert inv["cranes"][0]["crane_id"] == "C1"
        assert inv["cranes"][0]["updated_at"] == ts.isoformat()
        assert inv["cranes"][1]["crane_id"] == "C2"
        assert inv["cranes"][1]["updated_at"] is None

        # Verify SQL queries targeted the expected tables
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        assert any("FROM terminals" in sql for sql in executed_sqls)
        assert any("FROM berths" in sql for sql in executed_sqls)
        assert any("FROM cranes" in sql for sql in executed_sqls)
        crane_query = mock_cur.execute.call_args_list[-1]
        assert "WHERE crane_id = ANY(%(crane_ids)s)" in crane_query.args[0]
        assert crane_query.args[1] == {"crane_ids": list(ACTIVE_CRANE_IDS)}

    def test_get_resource_inventory_db_error(self) -> None:
        """Database errors during execution are propagated."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.execute.side_effect = RuntimeError("DB connection dropped")

        with pytest.raises(RuntimeError, match="DB connection dropped"):
            get_resource_inventory(mock_conn)
