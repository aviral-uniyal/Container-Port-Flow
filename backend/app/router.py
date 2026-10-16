"""
FastAPI router – Container-Port-Flow simulation endpoints.

Every handler is a thin translation layer:
  HTTP request  →  Pydantic validation  →  CppBridge.send()  →  HTTP response

C++ owns all scheduling/resource logic; Python only validates input shape
and maps responses / errors to appropriate HTTP status codes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .cpp_bridge import CppBridgeError
from .database import (
    batch_sync_crane_statuses,
    check_connection,
    get_connection,
    get_resource_inventory,
    get_simulation_job_id,
    insert_crane_assignment,
    insert_disruption_event,
    insert_simulation_job,
    insert_simulation_run,
    release_active_crane_assignment_by_crane,
    release_active_crane_assignments_by_job,
    update_crane_status,
    update_job_status,
    update_job_work_progress,
    upsert_crane,
)
from .models import (
    CraneRegisterRequest,
    InitRequest,
    JobSubmitRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _get_bridge(request: Request):
    """Retrieve the CppBridge instance stored in app state.

    Raises 503 if the bridge is missing or not alive.
    """
    bridge = getattr(request.app.state, "bridge", None)
    if bridge is None or not bridge.is_alive:
        raise HTTPException(
            status_code=503,
            detail="C++ simulation bridge is unavailable",
        )
    return bridge


def _send(bridge, command: dict[str, Any]) -> dict[str, Any]:
    """Send a command to the bridge; map transport errors to 503.

    If the C++ process returns ``{ok: false}``, raise 400.
    """
    try:
        resp = bridge.send(command)
    except CppBridgeError:
        raise HTTPException(
            status_code=503,
            detail="C++ simulation bridge is unavailable",
        )

    if not resp.get("ok", False):
        raise HTTPException(
            status_code=400,
            detail=resp.get("error", "C++ command rejected"),
        )
    return resp


def _get_db_pool(request: Request):
    """Return the database pool from app state, or *None* if unavailable."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is not None and getattr(pool, "is_open", False):
        return pool
    return None


def _sync_crane_statuses(bridge, request: Request) -> None:
    """Take a C++ snapshot and persist all crane statuses to PostgreSQL.

    This is the core of Bug 2's fix.  The C++ runtime is the authoritative
    source for per-crane status (AVAILABLE / ASSIGNED / FAILED).  After any
    operation that may change crane statuses (job submission that triggers
    dispatch, or job completion that releases cranes) we snapshot the C++
    registry and batch-update the ``cranes`` table so Database Inventory
    reflects the real runtime state.

    Design decisions:
    - Only updates cranes that already exist in the DB (batch_sync skips
      unknown IDs), so we never create phantom rows.
    - Errors are caught and logged; they must NOT propagate back to the
      caller since the primary C++ operation has already succeeded.
    - If the snapshot itself fails (bridge error) we log a warning and
      return without persisting — the next manual refresh will catch up.
    """
    pool = _get_db_pool(request)
    if pool is None:
        return  # No DB available — nothing to sync.

    # Only sync runtime crane status if a simulation run is active
    if getattr(request.app.state, "current_run_id", None) is None:
        return

    try:
        snap = bridge.send({"cmd": "snapshot"})
    except Exception as exc:
        logger.warning("Could not take snapshot for crane status sync: %s", exc)
        return

    if not snap.get("ok", False):
        logger.warning(
            "Snapshot returned ok=false during crane status sync: %s",
            snap.get("error", ""),
        )
        return

    cranes_list = snap.get("cranes", [])
    if not cranes_list:
        return

    # Build a list of {crane_id, status} dicts from the snapshot.
    crane_statuses = []
    for c in cranes_list:
        cid = c.get("crane_id", "")
        status = c.get("status", "")
        if cid and status in ("AVAILABLE", "ASSIGNED", "FAILED"):
            crane_statuses.append({"crane_id": cid, "status": status})

    if not crane_statuses:
        return

    try:
        with get_connection(pool) as conn:
            updated = batch_sync_crane_statuses(conn, crane_statuses)
        logger.info(
            "Synced crane statuses from C++ snapshot: %d/%d rows updated",
            updated,
            len(crane_statuses),
        )
    except Exception as exc:
        logger.warning("DB write failed during crane status sync: %s", exc)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/health")
def health(request: Request):
    """Report API, bridge, and database liveness."""
    bridge = getattr(request.app.state, "bridge", None)
    bridge_alive = bridge is not None and bridge.is_alive

    db_pool = getattr(request.app.state, "db_pool", None)
    db_connected = False
    if db_pool is not None and getattr(db_pool, "is_open", False):
        conn = None
        try:
            conn = db_pool.getconn()
            db_connected = check_connection(conn)
        except Exception:
            db_connected = False
        finally:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    db_pool.putconn(conn)
                except Exception:
                    pass

    all_ok = bridge_alive and db_connected
    status = "ready" if all_ok else "degraded"
    return {
        "status": status,
        "bridge_alive": bridge_alive,
        "db_connected": db_connected,
    }


@router.post("/simulation/init")
def simulation_init(body: InitRequest, request: Request):
    """Initialise the PortDispatcher with capacities and policy."""
    bridge = _get_bridge(request)
    cmd: dict[str, Any] = {
        "cmd": "init",
        "berths": body.berths,
        "cranes": body.cranes,
        "trucks": body.trucks,
    }
    if body.policy is not None:
        cmd["policy"] = body.policy.value
    if body.batch_mode is not None:
        cmd["batch_mode"] = body.batch_mode
    resp = _send(bridge, cmd)

    # ── Persist simulation run ────────────────────────────────────────────
    # Always reset current_run_id before attempting persistence so a failed
    # run never inherits a stale ID from a previous initialization.
    request.app.state.current_run_id = None
    pool = _get_db_pool(request)
    if pool is not None:
        confirmed_policy = resp.get("policy", "FCFS")
        now = datetime.now(timezone.utc)
        run_name = f"Run {now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"
        try:
            with get_connection(pool) as conn:
                run_id = insert_simulation_run(
                    conn,
                    run_name=run_name,
                    scheduling_policy=confirmed_policy,
                    total_berths=body.berths,
                    total_cranes=body.cranes,
                    total_trucks=body.trucks,
                )
            request.app.state.current_run_id = run_id
            logger.info("Created simulation run %d (%s)", run_id, run_name)
        except Exception as exc:
            request.app.state.current_run_id = None
            logger.warning("DB write failed for simulation init: %s", exc)
    else:
        logger.warning(
            "No database pool available; skipping simulation run persistence"
        )

    return resp


@router.post("/simulation/cranes")
def simulation_register_crane(body: CraneRegisterRequest, request: Request):
    """Register a crane with the C++ dispatcher.

    Idempotent: if the crane is already registered in the current C++ session
    (i.e. C++ returns a duplicate-crane error), the handler still upserts the
    crane record in PostgreSQL and returns HTTP 200.  This prevents stale DB
    entries when a crane was registered in a previous session but the C++
    process was restarted without clearing the database.
    """
    bridge = _get_bridge(request)
    crane_id = body.crane_id.strip()
    cmd: dict[str, Any] = {
        "cmd": "register_crane",
        "crane_id": crane_id,
    }

    try:
        resp = bridge.send(cmd)
    except CppBridgeError:
        raise HTTPException(
            status_code=503,
            detail="C++ simulation bridge is unavailable",
        )

    # Bug 1 fix: C++ CraneRegistry rejects re-registration of an already-known
    # crane with "Duplicate crane ID: ...".  From the API perspective this is
    # idempotent — the crane is already AVAILABLE in the runtime and the DB
    # ON CONFLICT clause will simply update updated_at.  We persist and return
    # 200 so callers do not have to distinguish "registered now" vs "already
    # registered" and the DB row is always kept in sync.
    is_duplicate = (
        not resp.get("ok", False)
        and "duplicate crane" in resp.get("error", "").lower()
    )

    if not resp.get("ok", False) and not is_duplicate:
        # Genuine C++ rejection (not a duplicate) — propagate as 400.
        raise HTTPException(
            status_code=400,
            detail=resp.get("error", "C++ command rejected"),
        )

    # ── Persist crane registration (new or duplicate) ──────────────────────
    pool = _get_db_pool(request)
    if pool is not None:
        try:
            with get_connection(pool) as conn:
                upsert_crane(
                    conn,
                    crane_id=crane_id,
                    terminal_id=1,
                    status="AVAILABLE",
                )
            logger.info("Persisted crane %s (duplicate=%s)", crane_id, is_duplicate)
        except Exception as exc:
            logger.warning("DB write failed for crane %s: %s", crane_id, exc)
    else:
        logger.warning(
            "No database pool available; skipping crane persistence"
        )

    # Return a normalised ok response for duplicates so the frontend treats
    # re-registration as a success.
    if is_duplicate:
        return {"ok": True, "crane_id": crane_id}
    return resp


@router.post("/simulation/jobs")
def simulation_submit_job(body: JobSubmitRequest, request: Request):
    """Submit a vessel job to the C++ dispatcher."""
    bridge = _get_bridge(request)
    cmd: dict[str, Any] = {
        "cmd": "submit_job",
        "job_id": body.job_id,
        "arrival_time": body.arrival_time,
        "work_total": body.work_total,
        "priority": body.priority,
        "berths": body.berths,
        "cranes": body.cranes,
        "trucks": body.trucks,
    }
    resp = _send(bridge, cmd)

    # ── Persist submitted job ─────────────────────────────────────────────
    run_id = getattr(request.app.state, "current_run_id", None)
    if run_id is None:
        logger.warning(
            "No current_run_id; skipping job submission persistence for %s",
            body.job_id,
        )
    else:
        pool = _get_db_pool(request)
        if pool is not None:
            try:
                with get_connection(pool) as conn:
                    insert_simulation_job(
                        conn,
                        run_id=run_id,
                        job_id=body.job_id,
                        arrival_time=body.arrival_time,
                        priority=body.priority,
                        work_total=body.work_total,
                        requested_berths=body.berths,
                        requested_cranes=body.cranes,
                        requested_trucks=body.trucks,
                    )
                logger.info("Persisted job %s for run %d", body.job_id, run_id)
            except Exception as exc:
                logger.warning(
                    "DB write failed for job %s: %s", body.job_id, exc
                )
        else:
            logger.warning(
                "No database pool available; skipping job submission persistence"
            )

    # ── Bug 2 fix: sync crane statuses ───────────────────────────────────
    # A job submission may immediately dispatch the job (PENDING → ACTIVE),
    # causing cranes to transition AVAILABLE → ASSIGNED in the C++ runtime.
    # We take a snapshot right after and persist the current crane statuses
    # to PostgreSQL so Database Inventory reflects the real assignment state.
    _sync_crane_statuses(bridge, request)

    return resp


@router.post("/simulation/jobs/{job_id}/complete")
def simulation_complete_job(job_id: str, request: Request):
    """Mark an active job as completed."""
    bridge = _get_bridge(request)
    cmd: dict[str, Any] = {
        "cmd": "complete_job",
        "job_id": job_id,
    }
    resp = _send(bridge, cmd)

    # ── Persist completion status ─────────────────────────────────────────
    run_id = getattr(request.app.state, "current_run_id", None)
    if run_id is None:
        logger.warning(
            "No current_run_id; skipping job completion persistence for %s",
            job_id,
        )
    else:
        pool = _get_db_pool(request)
        if pool is not None:
            try:
                with get_connection(pool) as conn:
                    update_job_status(
                        conn,
                        run_id=run_id,
                        job_id=job_id,
                        status="COMPLETED",
                    )
                    sim_job_id = get_simulation_job_id(
                        conn, run_id=run_id, job_id=job_id
                    )
                    if sim_job_id is not None:
                        release_active_crane_assignments_by_job(
                            conn,
                            simulation_job_id=sim_job_id,
                            release_reason="JOB_COMPLETED",
                        )
                logger.info("Persisted completion for job %s run %d", job_id, run_id)
            except Exception as exc:
                logger.warning(
                    "DB update failed for job %s completion: %s", job_id, exc
                )
        else:
            logger.warning(
                "No database pool available; skipping job completion persistence"
            )

    # ── Bug 2 fix: sync crane statuses ───────────────────────────────────
    # Job completion releases all assigned cranes back to AVAILABLE in C++.
    # Sync the snapshot so Database Inventory shows them as AVAILABLE again.
    _sync_crane_statuses(bridge, request)

    return resp


@router.get("/simulation/snapshot")
def simulation_snapshot(request: Request):
    """Return the full C++ simulation state snapshot."""
    bridge = _get_bridge(request)
    return _send(bridge, {"cmd": "snapshot"})


@router.post("/simulation/dispatch")
def simulation_dispatch(request: Request):
    """Trigger batch dispatch of queued jobs according to the active policy."""
    bridge = _get_bridge(request)
    resp = _send(bridge, {"cmd": "dispatch_batch"})

    dispatched_jobs = resp.get("dispatched_jobs", [])
    run_id = getattr(request.app.state, "current_run_id", None)
    pool = _get_db_pool(request)

    if pool is not None and run_id is not None and dispatched_jobs:
        try:
            with get_connection(pool) as conn:
                for jid in dispatched_jobs:
                    update_job_status(conn, run_id=run_id, job_id=jid, status="ACTIVE")
            logger.info(
                "Persisted batch dispatch of %d jobs for run %d",
                len(dispatched_jobs),
                run_id,
            )
        except Exception as exc:
            logger.warning("DB write failed for batch dispatch: %s", exc)

    # Sync runtime crane assignments and statuses
    _sync_crane_statuses(bridge, request)

    return resp


@router.post("/simulation/cranes/{crane_id}/fail")
def simulation_crane_fail(crane_id: str, request: Request):
    """Mark a crane as FAILED in the C++ runtime and persist to PostgreSQL.

    Handles both idle (AVAILABLE) and assigned (ASSIGNED) cranes:
      - For idle cranes, sends ``idle_crane_failed`` to C++.
      - For assigned cranes, sends ``crane_failed`` with work progress.

    The endpoint inspects the current crane status via a snapshot before
    deciding which C++ command to use.  This avoids requiring the frontend
    to know implementation details about idle vs. assigned failures.
    """
    bridge = _get_bridge(request)
    crane_id = crane_id.strip()
    if not crane_id:
        raise HTTPException(status_code=400, detail="crane_id must not be empty")

    # 1. Take a snapshot to determine current crane state.
    try:
        snap = bridge.send({"cmd": "snapshot"})
    except CppBridgeError:
        raise HTTPException(
            status_code=503,
            detail="C++ simulation bridge is unavailable",
        )
    if not snap.get("ok", False):
        raise HTTPException(
            status_code=400,
            detail=snap.get("error", "snapshot failed"),
        )

    # Find the crane in the snapshot.
    crane_info = None
    for c in snap.get("cranes", []):
        if c.get("crane_id") == crane_id:
            crane_info = c
            break

    if crane_info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Crane '{crane_id}' not found in C++ runtime",
        )

    current_status = crane_info.get("status", "")
    if current_status == "FAILED":
        raise HTTPException(
            status_code=409,
            detail=f"Crane '{crane_id}' is already FAILED",
        )

    # 2. Determine the correct C++ command based on current status.
    affected_job_id = ""
    work_completed = 0
    work_remaining = 0
    job_interrupted = False

    if current_status == "ASSIGNED":
        # Find the job assigned to this crane from the snapshot.
        assigned_job = crane_info.get("assigned_job", "")
        # Find job record in snapshot to get work progress.
        job_info = None
        for j in snap.get("jobs", []):
            if j.get("job_id") == assigned_job:
                job_info = j
                break

        wc = job_info.get("work_completed", 0) if job_info else 0
        wr = job_info.get("work_remaining", 1) if job_info else 1
        # work_remaining must be > 0 for C++
        if wr <= 0:
            wr = 1

        cmd: dict[str, Any] = {
            "cmd": "crane_failed",
            "crane_id": crane_id,
            "work_completed": wc,
            "work_remaining": wr,
        }
        resp = _send(bridge, cmd)
        affected_job_id = resp.get("affected_job_id", assigned_job)
        job_interrupted = resp.get("job_interrupted", False)
        work_completed = wc
        work_remaining = wr
    else:
        # AVAILABLE crane
        cmd = {
            "cmd": "idle_crane_failed",
            "crane_id": crane_id,
        }
        resp = _send(bridge, cmd)

    # 3. Persist to PostgreSQL.
    pool = _get_db_pool(request)
    run_id = getattr(request.app.state, "current_run_id", None)

    if pool is not None:
        try:
            with get_connection(pool) as conn:
                # Update crane status.
                updated = update_crane_status(
                    conn, crane_id=crane_id, status="FAILED"
                )
                if not updated:
                    logger.warning(
                        "Crane %s not found in DB during fail persist", crane_id
                    )

                # Look up simulation_job_id if an active job was affected.
                sim_job_id = None
                if affected_job_id and run_id is not None:
                    sim_job_id = get_simulation_job_id(
                        conn, run_id=run_id, job_id=affected_job_id
                    )

                # Record disruption event.
                if run_id is not None:
                    insert_disruption_event(
                        conn,
                        run_id=run_id,
                        crane_id=crane_id,
                        event_type="CRANE_FAILED",
                        simulation_job_id=sim_job_id,
                        work_completed=work_completed if affected_job_id else None,
                        work_remaining=work_remaining if affected_job_id else None,
                    )

                # Release any active assignment record for this crane.
                release_active_crane_assignment_by_crane(
                    conn, crane_id=crane_id, release_reason="CRANE_FAILED"
                )

                # Update job status and work progress if interrupted.
                if affected_job_id and job_interrupted and run_id is not None:
                    update_job_status(
                        conn,
                        run_id=run_id,
                        job_id=affected_job_id,
                        status="INTERRUPTED",
                    )
                    update_job_work_progress(
                        conn,
                        run_id=run_id,
                        job_id=affected_job_id,
                        work_completed=work_completed,
                        work_remaining=work_remaining,
                    )

            logger.info(
                "Persisted crane %s failure (affected_job=%s, interrupted=%s)",
                crane_id, affected_job_id, job_interrupted,
            )
        except Exception as exc:
            logger.error("DB write failed for crane %s failure: %s", crane_id, exc)
            # Attempt safe compensating rollback for idle crane failure if no job was affected.
            if not affected_job_id:
                try:
                    comp_resp = bridge.send({"cmd": "crane_recovered", "crane_id": crane_id})
                    if comp_resp.get("ok", False) and not comp_resp.get("job_dispatched", False):
                        # Verify rollback in runtime snapshot.
                        v_snap = bridge.send({"cmd": "snapshot"})
                        for c in v_snap.get("cranes", []):
                            if c.get("crane_id") == crane_id and c.get("status") == "AVAILABLE":
                                raise HTTPException(
                                    status_code=500,
                                    detail=f"Database persistence failed ({exc}). Runtime mutation was rolled back to AVAILABLE.",
                                )
                except HTTPException:
                    raise
                except Exception as comp_exc:
                    logger.warning("Compensating rollback failed: %s", comp_exc)

            raise HTTPException(
                status_code=500,
                detail=f"Database persistence failed ({exc}). Runtime crane '{crane_id}' is FAILED. Reconciliation required.",
            )
    else:
        logger.warning(
            "No database pool; skipping crane failure persistence for %s",
            crane_id,
        )

    # 4. Sync all crane statuses from C++ snapshot.
    _sync_crane_statuses(bridge, request)

    result: dict[str, Any] = {
        "ok": True,
        "crane_id": crane_id,
        "status": "FAILED",
        "affected_job_id": affected_job_id,
        "job_interrupted": job_interrupted,
    }
    if affected_job_id:
        result["work_completed"] = work_completed
        result["work_remaining"] = work_remaining
    return result


@router.post("/simulation/cranes/{crane_id}/recover")
def simulation_crane_recover(crane_id: str, request: Request):
    """Recover a FAILED crane back to AVAILABLE.

    Sends ``crane_recovered`` to the C++ runtime, persists the status change
    to PostgreSQL, and records a CRANE_RECOVERED disruption event.
    """
    bridge = _get_bridge(request)
    crane_id = crane_id.strip()
    if not crane_id:
        raise HTTPException(status_code=400, detail="crane_id must not be empty")

    # Send recover command to C++.
    cmd: dict[str, Any] = {
        "cmd": "crane_recovered",
        "crane_id": crane_id,
    }
    resp = _send(bridge, cmd)

    dispatched_job_id = resp.get("dispatched_job_id", "")
    job_dispatched = resp.get("job_dispatched", False)

    # Persist to PostgreSQL.
    pool = _get_db_pool(request)
    run_id = getattr(request.app.state, "current_run_id", None)

    if pool is not None:
        try:
            with get_connection(pool) as conn:
                updated = update_crane_status(
                    conn, crane_id=crane_id, status="AVAILABLE"
                )
                if not updated:
                    logger.warning(
                        "Crane %s not found in DB during recover persist",
                        crane_id,
                    )

                if run_id is not None:
                    insert_disruption_event(
                        conn,
                        run_id=run_id,
                        crane_id=crane_id,
                        event_type="CRANE_RECOVERED",
                    )
                    if dispatched_job_id:
                        sim_job_id = get_simulation_job_id(
                            conn, run_id=run_id, job_id=dispatched_job_id
                        )
                        if sim_job_id is not None:
                            insert_crane_assignment(
                                conn,
                                crane_id=crane_id,
                                simulation_job_id=sim_job_id,
                            )
                        update_job_status(
                            conn,
                            run_id=run_id,
                            job_id=dispatched_job_id,
                            status="ACTIVE",
                        )

            logger.info("Persisted crane %s recovery", crane_id)
        except Exception as exc:
            logger.error("DB write failed for crane %s recovery: %s", crane_id, exc)
            raise HTTPException(
                status_code=500,
                detail=f"Database persistence failed ({exc}). Runtime crane '{crane_id}' is AVAILABLE. Reconciliation required.",
            )
    else:
        logger.warning(
            "No database pool; skipping crane recovery persistence for %s",
            crane_id,
        )

    # Sync all crane statuses.
    _sync_crane_statuses(bridge, request)

    result: dict[str, Any] = {
        "ok": True,
        "crane_id": crane_id,
        "status": "AVAILABLE",
        "dispatched_job_id": dispatched_job_id,
        "job_dispatched": job_dispatched,
    }
    return result


@router.post("/simulation/reconcile")
def simulation_reconcile(request: Request):
    """Reconcile PostgreSQL state with the authoritative C++ runtime snapshot.

    Compares runtime crane statuses and active/interrupted jobs with PostgreSQL,
    repairing any discrepancies caused by transient database write failures
    without duplicating events or resource releases.
    """
    bridge = _get_bridge(request)
    pool = _get_db_pool(request)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        snap = bridge.send({"cmd": "snapshot"})
    except CppBridgeError:
        raise HTTPException(
            status_code=503,
            detail="C++ simulation bridge is unavailable",
        )
    if not snap.get("ok", False):
        raise HTTPException(
            status_code=400,
            detail=snap.get("error", "snapshot failed"),
        )

    run_id = getattr(request.app.state, "current_run_id", None)
    cranes_list = snap.get("cranes", [])
    jobs_list = snap.get("jobs", [])

    crane_statuses = []
    for c in cranes_list:
        cid = c.get("crane_id", "")
        status = c.get("status", "")
        if cid and status in ("AVAILABLE", "ASSIGNED", "FAILED"):
            crane_statuses.append({"crane_id": cid, "status": status})

    reconciled_cranes = 0
    reconciled_jobs = 0
    with get_connection(pool) as conn:
        if crane_statuses:
            reconciled_cranes = batch_sync_crane_statuses(conn, crane_statuses)

        if run_id is not None:
            for j in jobs_list:
                jid = j.get("job_id", "")
                jstate = j.get("state", "")
                wc = int(j.get("work_completed", 0))
                wr = int(j.get("work_remaining", 0))
                if jstate in ("INTERRUPTED", "ACTIVE", "COMPLETED", "CANCELLED"):
                    update_job_status(conn, run_id=run_id, job_id=jid, status=jstate)
                    update_job_work_progress(
                        conn,
                        run_id=run_id,
                        job_id=jid,
                        work_completed=wc,
                        work_remaining=wr,
                    )
                    reconciled_jobs += 1

    return {
        "ok": True,
        "reconciled_cranes": reconciled_cranes,
        "reconciled_jobs": reconciled_jobs,
    }


@router.get("/inventory")
def get_inventory(request: Request):
    """Return physical infrastructure from PostgreSQL: terminals, berths, cranes.

    This is a read-only endpoint that queries the database directly — it is
    independent of the C++ simulation state.  Returns 503 when the database
    pool is unavailable so callers can degrade gracefully.
    """
    pool = _get_db_pool(request)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )
    try:
        with get_connection(pool) as conn:
            data = get_resource_inventory(conn)
    except Exception as exc:
        logger.warning("GET /inventory DB error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Database error while fetching inventory",
        )
    return data
