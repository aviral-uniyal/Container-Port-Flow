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
ACTIVE_CRANE_IDS = tuple(f"C{i}" for i in range(1, 13))


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
        self._owner_conn = None

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
            if self._owner_conn is not None:
                try:
                    with self._owner_conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_unlock(4407366, 1)")
                    self._owner_conn.commit()
                except Exception as exc:
                    logger.warning("Could not explicitly release backend ownership lock: %s",
                                   type(exc).__name__)
                finally:
                    self._pool.putconn(self._owner_conn, close=True)
                    self._owner_conn = None
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

    def acquire_session_ownership(self) -> bool:
        """Hold one PostgreSQL advisory lock for the whole backend session.

        A second cooperative worker must fail startup instead of resetting the
        first worker's live inventory. The reserved connection is never lent
        to a request and is released when the pool closes.
        """
        if self._owner_conn is not None:
            return True
        conn = self.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(4407366, 1)")
                acquired = bool(cur.fetchone()[0])
            conn.commit()
            if acquired:
                self._owner_conn = conn
                return True
            self.putconn(conn)
            return False
        except Exception:
            conn.rollback()
            self.putconn(conn, close=True)
            raise


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


def initialize_backend_inventory(conn) -> None:
    """Create the twelve demo cranes and retire state from prior sessions.

    Called once per FastAPI lifespan, under session ownership and one database
    transaction. Existing crane rows and all historical records are retained.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO terminals (terminal_name, description)
            VALUES ('Demo Terminal', 'Primary demonstration terminal used for simulation scenarios.')
            ON CONFLICT (terminal_name) DO NOTHING
        """)
        cur.execute("""
            INSERT INTO cranes (crane_id, terminal_id, status)
            SELECT ids.crane_id, t.terminal_id, 'AVAILABLE'
              FROM terminals t
              CROSS JOIN unnest(%(crane_ids)s::varchar[]) AS ids(crane_id)
             WHERE t.terminal_name = 'Demo Terminal'
            ON CONFLICT (crane_id) DO NOTHING
        """, {"crane_ids": list(ACTIVE_CRANE_IDS)})
        # No prior C++ dispatcher survives a backend process restart. Preserve
        # completed jobs and all rows; only unfinished runs/jobs are closed.
        cur.execute("""
            UPDATE simulation_runs
               SET status = 'FAILED', ended_at = now()
             WHERE status = 'RUNNING'
            RETURNING run_id
        """)
        abandoned_run_ids = [row[0] for row in cur.fetchall()]
        if abandoned_run_ids:
            cur.execute("""
                UPDATE simulation_jobs
                   SET status = 'CANCELLED', completed_at = now(),
                       allocated_berths = 0, allocated_cranes = 0,
                       allocated_trucks = 0
                 WHERE run_id = ANY(%(run_ids)s)
                   AND status IN ('PENDING', 'ACTIVE', 'INTERRUPTED')
            """, {"run_ids": abandoned_run_ids})
        # Even a terminal run may contain an old unreleased assignment after
        # interrupted persistence. At startup every such row is stale.
        cur.execute("""
            UPDATE crane_assignments
               SET released_at = now(), release_reason = 'BACKEND_RESTART'
             WHERE released_at IS NULL
        """)
        cur.execute("""
            UPDATE cranes
               SET status = 'AVAILABLE', updated_at = now()
             WHERE crane_id = ANY(%(crane_ids)s)
               AND status <> 'AVAILABLE'
        """, {"crane_ids": list(ACTIVE_CRANE_IDS)})


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


def update_crane_status(conn, *, crane_id: str, status: str) -> bool:
    """Update the status of an existing crane record.

    Only touches the ``status`` and ``updated_at`` columns — it does NOT
    alter ``terminal_id`` or any other field.  This is the correct helper
    for reflecting runtime state transitions (AVAILABLE → ASSIGNED → AVAILABLE)
    without overwriting registration metadata.

    Status must be one of: 'AVAILABLE', 'ASSIGNED', 'FAILED'.

    Returns True if a row was updated, False if the crane_id was not found.
    """
    sql = """
        UPDATE cranes
           SET status     = %(status)s,
               updated_at = now()
         WHERE crane_id = %(crane_id)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"crane_id": crane_id, "status": status})
        return cur.rowcount > 0


def batch_sync_crane_statuses(
    conn,
    crane_statuses: list[dict[str, str]],
) -> int:
    """Sync crane status for each entry in *crane_statuses*.

    Each dict must have ``crane_id`` and ``status`` keys.  Only cranes that
    already exist in the database are updated — unknown IDs are silently
    skipped (they may not have been registered in the DB yet).

    Returns the number of existing crane rows matched. Unchanged statuses
    retain their original updated_at timestamp.

    This is used to persist runtime state (ASSIGNED / AVAILABLE transitions)
    from the C++ snapshot into PostgreSQL so that Database Inventory reflects
    the real simulation state.
    """
    if not crane_statuses:
        return 0
    matched = 0
    sql = """
        UPDATE cranes
           SET status     = %(status)s,
               updated_at = now()
         WHERE crane_id = %(crane_id)s
    """
    with conn.cursor() as cur:
        for entry in crane_statuses:
            cur.execute("SELECT status FROM cranes WHERE crane_id = %(crane_id)s FOR UPDATE",
                        {"crane_id": entry["crane_id"]})
            row = cur.fetchone()
            if row is None:
                continue
            matched += 1
            if row[0] != entry["status"]:
                cur.execute(sql, {"crane_id": entry["crane_id"], "status": entry["status"]})
    return matched


def make_assigned_cranes_available(conn, crane_ids: list[str]) -> int:
    """Clear only selected ASSIGNED inventory rows; never recover FAILED cranes."""
    if not crane_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE cranes SET status = 'AVAILABLE', updated_at = now()
             WHERE crane_id = ANY(%(crane_ids)s) AND status = 'ASSIGNED'
        """, {"crane_ids": crane_ids})
        return cur.rowcount


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


def close_simulation_run(conn, run_id: int, status: str = "FAILED") -> bool:
    """Close a run without changing an already closed run."""
    if status not in ("FAILED", "COMPLETED"):
        raise ValueError("Invalid terminal simulation run status")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE simulation_runs SET status = %(status)s, ended_at = now()
             WHERE run_id = %(run_id)s AND status = 'RUNNING'
        """, {"run_id": run_id, "status": status})
        return cur.rowcount > 0


def cancel_unfinished_jobs_for_run(conn, run_id: int) -> int:
    """Retain completed history and cancel only unfinished jobs in one run."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE simulation_jobs SET status = 'CANCELLED', completed_at = now()
             WHERE run_id = %(run_id)s
               AND status IN ('PENDING', 'ACTIVE', 'INTERRUPTED')
        """, {"run_id": run_id})
        return cur.rowcount


# ── 3. Simulation Jobs ────────────────────────────────────────────────────────


def ship_exists(conn, ship_id: int) -> bool:
    """Check the vessel registry before touching the C++ dispatcher."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM ships WHERE ship_id = %(ship_id)s", {"ship_id": ship_id})
        return cur.fetchone() is not None


def ship_has_job_in_run(conn, *, run_id: int, ship_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM simulation_jobs
             WHERE run_id = %(run_id)s AND ship_id = %(ship_id)s
        """, {"run_id": run_id, "ship_id": ship_id})
        return cur.fetchone() is not None


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
    ship_id: int | None = None,
) -> int:
    """Insert a simulation job and return its ``simulation_job_id``.

    ``work_remaining`` is initialized equal to ``work_total`` per schema.
    """
    sql = """
        INSERT INTO simulation_jobs
               (run_id, job_id, port_call_id, ship_id, arrival_time, priority,
                work_total, work_remaining,
                requested_berths, requested_cranes, requested_trucks)
        VALUES (%(run_id)s, %(job_id)s, %(port_call_id)s, %(ship_id)s, %(arrival_time)s, %(priority)s,
                %(work_total)s, %(work_total)s,
                %(requested_berths)s, %(requested_cranes)s, %(requested_trucks)s)
        RETURNING simulation_job_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "run_id": run_id,
            "job_id": job_id,
            "port_call_id": port_call_id,
            "ship_id": ship_id,
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


def update_job_work_progress(
    conn,
    *,
    run_id: int,
    job_id: str,
    work_completed: int,
    work_remaining: int,
) -> bool:
    """Update the work_completed and work_remaining columns for a simulation job.

    Used when a crane fails mid-job to persist the progress snapshot from the
    C++ runtime.  Does NOT change the job status — that is handled separately
    by update_job_status().

    Returns True if a row was updated, False if the (run_id, job_id) was
    not found.
    """
    sql = """
        UPDATE simulation_jobs
           SET work_completed = %(work_completed)s,
               work_remaining = %(work_remaining)s
         WHERE run_id = %(run_id)s AND job_id = %(job_id)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "run_id": run_id,
            "job_id": job_id,
            "work_completed": work_completed,
            "work_remaining": work_remaining,
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


def get_simulation_job_id(
    conn,
    *,
    run_id: int,
    job_id: str,
) -> int | None:
    """Look up the integer primary key simulation_job_id for a job in a run."""
    sql = """
        SELECT simulation_job_id
          FROM simulation_jobs
         WHERE run_id = %(run_id)s AND job_id = %(job_id)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"run_id": run_id, "job_id": job_id})
        row = cur.fetchone()
        return row[0] if row else None


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


def release_active_crane_assignment_by_crane(
    conn,
    *,
    crane_id: str,
    release_reason: str,
) -> bool:
    """Release the currently active assignment for a crane, if one exists."""
    sql = """
        UPDATE crane_assignments
           SET released_at    = now(),
               release_reason = %(release_reason)s
         WHERE crane_id = %(crane_id)s
           AND released_at IS NULL
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "crane_id": crane_id,
            "release_reason": release_reason,
        })
        return getattr(cur, "rowcount", 0) > 0


def release_active_crane_assignments_by_job(
    conn,
    *,
    simulation_job_id: int,
    release_reason: str = "JOB_COMPLETED",
) -> int:
    """Release all currently active crane assignments for a given simulation_job_id."""
    sql = """
        UPDATE crane_assignments
           SET released_at    = now(),
               release_reason = %(release_reason)s
         WHERE simulation_job_id = %(simulation_job_id)s
           AND released_at IS NULL
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "simulation_job_id": simulation_job_id,
            "release_reason": release_reason,
        })
        return getattr(cur, "rowcount", 0)


def get_active_assignments_for_run(conn, run_id: int) -> list[dict[str, Any]]:
    """Return active assignments belonging to exactly one simulation run."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT a.assignment_id, a.crane_id, a.simulation_job_id, j.job_id
              FROM crane_assignments a
              JOIN simulation_jobs j ON j.simulation_job_id = a.simulation_job_id
             WHERE j.run_id = %(run_id)s AND a.released_at IS NULL
        """, {"run_id": run_id})
        return [dict(row) for row in cur.fetchall()]


def get_active_assignments_for_cranes(conn, crane_ids: list[str]) -> list[dict[str, Any]]:
    """Lock physical cranes, then inspect their active assignments across all runs.

    Snapshot reconciliation and registration lock crane rows before inspecting
    assignments. Sorting the IDs prevents those transactions from taking locks
    in opposite orders. The partial unique index remains the final safeguard.
    """
    if not crane_ids:
        return []
    ids = sorted(set(crane_ids))
    with conn.cursor() as cur:
        cur.execute("""
            SELECT crane_id FROM cranes
             WHERE crane_id = ANY(%(crane_ids)s)
             ORDER BY crane_id FOR UPDATE
        """, {"crane_ids": ids})
        cur.fetchall()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT a.assignment_id, a.crane_id, a.simulation_job_id,
                   j.job_id, j.run_id, r.status AS run_status
              FROM crane_assignments a
              JOIN simulation_jobs j ON j.simulation_job_id = a.simulation_job_id
              JOIN simulation_runs r ON r.run_id = j.run_id
             WHERE a.crane_id = ANY(%(crane_ids)s)
               AND a.released_at IS NULL
        """, {"crane_ids": ids})
        return [dict(row) for row in cur.fetchall()]


def release_active_assignments_for_run(conn, run_id: int, reason: str) -> int:
    """Close assignment history for one run; repeated calls are harmless."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE crane_assignments a
               SET released_at = now(), release_reason = %(reason)s
              FROM simulation_jobs j
             WHERE a.simulation_job_id = j.simulation_job_id
               AND j.run_id = %(run_id)s AND a.released_at IS NULL
        """, {"run_id": run_id, "reason": reason})
        return cur.rowcount


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


def get_resource_inventory(conn, run_id: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Return a snapshot of physical infrastructure from PostgreSQL.

    Queries inventory and current-run ship status in a single connection:
      - terminals  : all terminal records
      - berths     : all berth records (ordered by terminal then berth)
      - cranes     : the twelve active demo cranes only
      - ships      : registry names and status derived from this run's jobs

    ``updated_at`` on cranes is serialised to an ISO-8601 string so the
    result is directly JSON-serialisable.

    Returns a dict with keys ``"terminals"``, ``"berths"``, ``"cranes"``, and ``"ships"``,
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
         WHERE crane_id = ANY(%(crane_ids)s)
         ORDER BY length(crane_id), crane_id
    """
    ships_sql = """
        SELECT s.ship_id, s.vessel_name,
               CASE
                   WHEN j.status = 'COMPLETED' THEN 'COMPLETED & LEFT'
                   WHEN j.status IN ('PENDING', 'ACTIVE', 'INTERRUPTED') THEN 'IN OPERATION'
                   ELSE 'NOT ARRIVED'
               END AS status
          FROM ships s
          LEFT JOIN simulation_jobs j
            ON j.ship_id = s.ship_id AND j.run_id = %(run_id)s
         ORDER BY s.vessel_name, s.ship_id
    """
    result: dict[str, list[dict[str, Any]]] = {
        "terminals": [],
        "berths": [],
        "cranes": [],
        "ships": [],
    }

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(terminals_sql)
        result["terminals"] = [dict(row) for row in cur.fetchall()]

        cur.execute(berths_sql)
        result["berths"] = [dict(row) for row in cur.fetchall()]

        cur.execute(cranes_sql, {"crane_ids": list(ACTIVE_CRANE_IDS)})
        rows = cur.fetchall()

        cur.execute(ships_sql, {"run_id": run_id})
        result["ships"] = [dict(row) for row in cur.fetchall()]

    # Convert datetime objects to ISO strings so FastAPI can serialise them.
    crane_list = []
    for row in rows:
        r = dict(row)
        if r.get("updated_at") is not None:
            r["updated_at"] = r["updated_at"].isoformat()
        crane_list.append(r)
    result["cranes"] = crane_list

    return result
