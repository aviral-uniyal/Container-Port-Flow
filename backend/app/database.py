"""
PostgreSQL persistence layer for Container-Port-Flow.

Manages a connection pool via ``psycopg2`` and provides helper functions for
common database operations against the five simulation schema tables:
  1. cranes
  2. simulation_runs
  3. simulation_jobs
  4. crane_assignments
  5. disruption_events

The pool is created/closed during the FastAPI lifespan and stored on
``app.state.db_pool``. Tests can replace it with a mock or dependency injection
to avoid needing a live PostgreSQL server.

**Connection settings** are read from environment variables by
:func:`~backend.app.config.get_db_config`. Never hardcodes credentials.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
import psycopg2.extras
import psycopg2.pool

from .config import get_db_config

logger = logging.getLogger(__name__)


# ─── Pool lifecycle ───────────────────────────────────────────────────────────


class DatabasePool:
    """Thin wrapper around ``psycopg2.pool.ThreadedConnectionPool``.

    Attributes
    ----------
    _pool : psycopg2.pool.ThreadedConnectionPool | None
        The underlying connection pool, or *None* before ``open()`` or
        after ``close()``.
    """

    def __init__(self, minconn: int = 1, maxconn: int = 5) -> None:
        self._minconn = minconn
        self._maxconn = maxconn
        self._pool: psycopg2.pool.ThreadedConnectionPool | None = None

    @property
    def is_open(self) -> bool:
        """True when the pool has been opened and not yet closed."""
        return self._pool is not None

    def open(self) -> None:
        """Create the connection pool using env-var configuration.

        Raises
        ------
        psycopg2.OperationalError
            If the database is unreachable.
        """
        if self._pool is not None:
            return  # already open

        config = get_db_config()
        if "dsn" in config:
            logger.info("Opening database pool using DATABASE_URL DSN")
        else:
            logger.info(
                "Opening database pool  host=%s  port=%s  dbname=%s  user=%s",
                config.get("host"),
                config.get("port"),
                config.get("dbname"),
                config.get("user"),
            )
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            self._minconn,
            self._maxconn,
            **config,
        )

    def close(self) -> None:
        """Close all pooled connections. Safe to call more than once."""
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
            logger.info("Database pool closed")

    def getconn(self):
        """Borrow a connection from the pool.

        Raises
        ------
        RuntimeError
            If the pool is not open.
        """
        if self._pool is None:
            raise RuntimeError("Database pool is not open")
        return self._pool.getconn()

    def putconn(self, conn, close: bool = False) -> None:
        """Return a connection to the pool."""
        if self._pool is not None:
            self._pool.putconn(conn, close=close)


@contextmanager
def get_connection(pool: DatabasePool) -> Generator[Any, None, None]:
    """Context-manager helper: borrow -> yield -> return (with rollback on error).

    Usage::

        with get_connection(pool) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

    If the block or commit raises an exception, the transaction is rolled back
    and the connection is returned to the pool. If rollback also fails, the
    original exception is preserved without being masked.
    """
    conn = pool.getconn()
    try:
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception as rollback_exc:
                logger.warning("Failed to roll back transaction: %s", rollback_exc)
            raise
    finally:
        try:
            pool.putconn(conn)
        except Exception as putconn_exc:
            logger.warning("Failed to return connection to pool: %s", putconn_exc)


# ─── Query helpers (simulation schema) ────────────────────────────────────────


def check_connection(conn) -> bool:
    """Run ``SELECT 1`` to verify the connection is alive.

    Returns True on success, False on any database error.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
            return row is not None and row[0] == 1
    except Exception:
        return False


# ── 1. Cranes ─────────────────────────────────────────────────────────────────


def upsert_crane(
    conn,
    *,
    crane_id: str,
    terminal_id: int,
    status: str = "AVAILABLE",
) -> str:
    """Insert or update a crane record.

    Matches C++ CraneRegistry identifiers. Status must be one of:
    'AVAILABLE', 'ASSIGNED', 'FAILED'.
    """
    sql = """
        INSERT INTO cranes (crane_id, terminal_id, status, updated_at)
        VALUES (%(crane_id)s, %(terminal_id)s, %(status)s, now())
        ON CONFLICT (crane_id) DO UPDATE
            SET terminal_id = EXCLUDED.terminal_id,
                status      = EXCLUDED.status,
                updated_at  = now()
        RETURNING crane_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "crane_id": crane_id,
            "terminal_id": terminal_id,
            "status": status,
        })
        row = cur.fetchone()
        return row[0]


def get_crane(conn, crane_id: str) -> dict[str, Any] | None:
    """Fetch a single crane record by ``crane_id``."""
    sql = """
        SELECT crane_id, terminal_id, status, created_at, updated_at
          FROM cranes
         WHERE crane_id = %(crane_id)s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, {"crane_id": crane_id})
        row = cur.fetchone()
        return dict(row) if row else None


# ── 2. Simulation Runs ────────────────────────────────────────────────────────


def insert_simulation_run(
    conn,
    *,
    run_name: str,
    scheduling_policy: str,
    total_berths: int,
    total_cranes: int,
    total_trucks: int,
) -> int:
    """Insert a new simulation run and return its ``run_id``.

    Uses a parameterized INSERT with RETURNING to avoid an extra query.
    """
    sql = """
        INSERT INTO simulation_runs
               (run_name, scheduling_policy, total_berths, total_cranes, total_trucks)
        VALUES (%(run_name)s, %(scheduling_policy)s,
                %(total_berths)s, %(total_cranes)s, %(total_trucks)s)
        RETURNING run_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "run_name": run_name,
            "scheduling_policy": scheduling_policy,
            "total_berths": total_berths,
            "total_cranes": total_cranes,
            "total_trucks": total_trucks,
        })
        row = cur.fetchone()
        return row[0]


def get_simulation_run(conn, run_id: int) -> dict[str, Any] | None:
    """Fetch a single simulation run by ``run_id``.

    Returns a dict with column names as keys, or None if not found.
    """
    sql = """
        SELECT run_id, run_name, scheduling_policy, status,
               total_berths, total_cranes, total_trucks,
               started_at, ended_at, created_at
          FROM simulation_runs
         WHERE run_id = %(run_id)s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, {"run_id": run_id})
        row = cur.fetchone()
        return dict(row) if row else None


# ── 3. Simulation Jobs ────────────────────────────────────────────────────────


def insert_simulation_job(
    conn,
    *,
    run_id: int,
    job_id: str,
    arrival_time: int,
    priority: int,
    work_total: int,
    requested_berths: int = 0,
    requested_cranes: int = 1,
    requested_trucks: int = 0,
    port_call_id: int | None = None,
) -> int:
    """Insert a simulation job and return its ``simulation_job_id``.

    ``work_remaining`` is initialized equal to ``work_total`` per schema.
    """
    sql = """
        INSERT INTO simulation_jobs
               (run_id, job_id, port_call_id, arrival_time, priority,
                work_total, work_remaining,
                requested_berths, requested_cranes, requested_trucks)
        VALUES (%(run_id)s, %(job_id)s, %(port_call_id)s, %(arrival_time)s, %(priority)s,
                %(work_total)s, %(work_total)s,
                %(requested_berths)s, %(requested_cranes)s, %(requested_trucks)s)
        RETURNING simulation_job_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "run_id": run_id,
            "job_id": job_id,
            "port_call_id": port_call_id,
            "arrival_time": arrival_time,
            "priority": priority,
            "work_total": work_total,
            "requested_berths": requested_berths,
            "requested_cranes": requested_cranes,
            "requested_trucks": requested_trucks,
        })
        row = cur.fetchone()
        return row[0]


def update_job_status(
    conn,
    *,
    run_id: int,
    job_id: str,
    status: str,
) -> bool:
    """Update the status of a simulation job.

    When *status* is ``'COMPLETED'``, also sets ``work_completed = work_total``
    and ``work_remaining = 0`` so the row accurately reflects full completion.
    All other status transitions leave the work counters unchanged.

    Returns True if a row was updated, False if the (run_id, job_id) was
    not found.
    """
    sql = """
        UPDATE simulation_jobs
           SET status = %(status)s,
               completed_at = CASE
                   WHEN %(status)s IN ('COMPLETED', 'CANCELLED') THEN now()
                   ELSE completed_at
               END,
               started_at = CASE
                   WHEN %(status)s = 'ACTIVE' AND started_at IS NULL THEN now()
                   ELSE started_at
               END,
               work_completed = CASE
                   WHEN %(status)s = 'COMPLETED' THEN work_total
                   ELSE work_completed
               END,
               work_remaining = CASE
                   WHEN %(status)s = 'COMPLETED' THEN 0
                   ELSE work_remaining
               END
         WHERE run_id = %(run_id)s AND job_id = %(job_id)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "run_id": run_id,
            "job_id": job_id,
            "status": status,
        })
        return cur.rowcount > 0


def get_jobs_for_run(conn, run_id: int) -> list[dict[str, Any]]:
    """Fetch all simulation jobs belonging to a run."""
    sql = """
        SELECT simulation_job_id, run_id, job_id, port_call_id, status,
               arrival_time, priority,
               work_total, work_completed, work_remaining,
               requested_berths, requested_cranes, requested_trucks,
               allocated_berths, allocated_cranes, allocated_trucks,
               submitted_at, started_at, completed_at
          FROM simulation_jobs
         WHERE run_id = %(run_id)s
         ORDER BY simulation_job_id
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, {"run_id": run_id})
        return [dict(row) for row in cur.fetchall()]


# ── 4. Crane Assignments ──────────────────────────────────────────────────────


def insert_crane_assignment(
    conn,
    *,
    crane_id: str,
    simulation_job_id: int,
) -> int:
    """Record a crane assignment to an active simulation job."""
    sql = """
        INSERT INTO crane_assignments (crane_id, simulation_job_id, assigned_at)
        VALUES (%(crane_id)s, %(simulation_job_id)s, now())
        RETURNING assignment_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "crane_id": crane_id,
            "simulation_job_id": simulation_job_id,
        })
        row = cur.fetchone()
        return row[0]


def release_crane_assignment(
    conn,
    *,
    assignment_id: int,
    release_reason: str,
) -> bool:
    """Mark a crane assignment as released."""
    sql = """
        UPDATE crane_assignments
           SET released_at    = now(),
               release_reason = %(release_reason)s
         WHERE assignment_id  = %(assignment_id)s
           AND released_at IS NULL
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "assignment_id": assignment_id,
            "release_reason": release_reason,
        })
        return cur.rowcount > 0


# ── 5. Disruption Events ──────────────────────────────────────────────────────


def insert_disruption_event(
    conn,
    *,
    run_id: int,
    crane_id: str,
    event_type: str,
    simulation_job_id: int | None = None,
    work_completed: int | None = None,
    work_remaining: int | None = None,
) -> int:
    """Record a disruption event (crane failure or recovery)."""
    sql = """
        INSERT INTO disruption_events
               (run_id, crane_id, simulation_job_id, event_type,
                work_completed, work_remaining, occurred_at)
        VALUES (%(run_id)s, %(crane_id)s, %(simulation_job_id)s, %(event_type)s,
                %(work_completed)s, %(work_remaining)s, now())
        RETURNING event_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "run_id": run_id,
            "crane_id": crane_id,
            "simulation_job_id": simulation_job_id,
            "event_type": event_type,
            "work_completed": work_completed,
            "work_remaining": work_remaining,
        })
        row = cur.fetchone()
        return row[0]


def get_disruption_events_for_run(conn, run_id: int) -> list[dict[str, Any]]:
    """Fetch all disruption events belonging to a simulation run."""
    sql = """
        SELECT event_id, run_id, crane_id, simulation_job_id,
               event_type, work_completed, work_remaining, occurred_at
          FROM disruption_events
         WHERE run_id = %(run_id)s
         ORDER BY occurred_at ASC, event_id ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, {"run_id": run_id})
        return [dict(row) for row in cur.fetchall()]


# ── 6. Resource Inventory ──────────────────────────────────────────────────────


def get_resource_inventory(conn) -> dict[str, list[dict[str, Any]]]:
    """Return a snapshot of physical infrastructure from PostgreSQL.

    Queries three tables in a single connection:
      - terminals  : all terminal records
      - berths     : all berth records (ordered by terminal then berth)
      - cranes     : all crane records (ordered by terminal then crane_id)

    ``updated_at`` on cranes is serialised to an ISO-8601 string so the
    result is directly JSON-serialisable.

    Returns a dict with keys ``"terminals"``, ``"berths"``, and ``"cranes"``,
    each containing a list of row dicts.
    """
    terminals_sql = """
        SELECT terminal_id, terminal_name, description
          FROM terminals
         ORDER BY terminal_id
    """
    berths_sql = """
        SELECT berth_id, terminal_id, berth_name
          FROM berths
         ORDER BY terminal_id, berth_id
    """
    cranes_sql = """
        SELECT crane_id, terminal_id, status, updated_at
          FROM cranes
         ORDER BY terminal_id, crane_id
    """
    result: dict[str, list[dict[str, Any]]] = {
        "terminals": [],
        "berths": [],
        "cranes": [],
    }

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(terminals_sql)
        result["terminals"] = [dict(row) for row in cur.fetchall()]

        cur.execute(berths_sql)
        result["berths"] = [dict(row) for row in cur.fetchall()]

        cur.execute(cranes_sql)
        rows = cur.fetchall()

    # Convert datetime objects to ISO strings so FastAPI can serialise them.
    crane_list = []
    for row in rows:
        r = dict(row)
        if r.get("updated_at") is not None:
            r["updated_at"] = r["updated_at"].isoformat()
        crane_list.append(r)
    result["cranes"] = crane_list

    return result
