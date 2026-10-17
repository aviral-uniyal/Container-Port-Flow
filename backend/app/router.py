"""
FastAPI router – Container-Port-Flow simulation endpoints.

Every handler is a thin translation layer:
  HTTP request  →  Pydantic validation  →  CppBridge.send()  →  HTTP response

C++ owns all scheduling/resource logic; Python only validates input shape
and maps responses / errors to appropriate HTTP status codes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .cpp_bridge import CppBridgeError
from .database import (
    batch_sync_crane_statuses,
    cancel_unfinished_jobs_for_run,
    check_connection,
    close_simulation_run,
    get_active_assignments_for_run,
    get_connection,
    get_crane,
    get_jobs_for_run,
    get_resource_inventory,
    get_simulation_job_id,
    insert_crane_assignment,
    insert_disruption_event,
    insert_simulation_job,
    insert_simulation_run,
    make_assigned_cranes_available,
    release_active_assignments_for_run,
    release_active_crane_assignments_by_job,
    release_crane_assignment,
    update_crane_status,
    update_job_status,
    update_job_work_progress,
)
from .execution import SimulationExecution, SimulationPersistenceError
from .models import (
    CraneRegisterRequest,
    InitRequest,
    JobSubmitRequest,
    RunRequest,
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


def _get_execution(request: Request) -> SimulationExecution:
    bridge = _get_bridge(request)
    execution = getattr(request.app.state, "execution", None)
    if execution is None or execution.bridge is not bridge:
        execution = SimulationExecution(bridge)
        request.app.state.execution = execution
    execution.on_snapshot = (
        (lambda snap: _persist_snapshot(request, snap))
        if getattr(request.app.state, "current_run_id", None) is not None else None
    )
    return execution


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


def _persist_snapshot(request: Request, snap: dict[str, Any]) -> dict[str, int]:
    """Reconcile current-run jobs, cranes and assignments in one transaction."""
    pool = _get_db_pool(request)
    run_id = getattr(request.app.state, "current_run_id", None)
    if run_id is None:
        return {"reconciled_cranes": 0, "reconciled_jobs": 0}
    if pool is None:
        raise RuntimeError("Database unavailable; runtime snapshot was not persisted")
    if not snap.get("ok") or not isinstance(snap.get("jobs"), list) or not isinstance(snap.get("cranes"), list):
        raise RuntimeError("Cannot persist an invalid C++ snapshot")

    jobs = {j["job_id"]: j for j in snap["jobs"]}
    desired = {}
    statuses = []
    crane_states = {}
    for crane in snap["cranes"]:
        cid, status = crane["crane_id"], crane["status"]
        if status not in ("AVAILABLE", "ASSIGNED", "FAILED"):
            raise RuntimeError(f"Invalid crane status for {cid}")
        statuses.append({"crane_id": cid, "status": status})
        crane_states[cid] = status
        if status == "ASSIGNED":
            jid = crane.get("assigned_job")
            if not jid or jid not in jobs or jobs[jid]["state"] != "ACTIVE":
                raise RuntimeError(f"Invalid assignment in C++ snapshot for {cid}")
            desired[cid] = jid

    with get_connection(pool) as conn:
        persisted_jobs = {j["job_id"]: j for j in get_jobs_for_run(conn, run_id)}
        old = {a["crane_id"]: a for a in get_active_assignments_for_run(conn, run_id)}
        # Release first to satisfy the partial unique index before replacements.
        for cid, assignment in old.items():
            if desired.get(cid) != assignment["job_id"]:
                state = jobs.get(assignment["job_id"], {}).get("state")
                reason = ("JOB_COMPLETED" if state == "COMPLETED" else
                          "JOB_CANCELLED" if state == "CANCELLED" else
                          "CRANE_FAILED" if crane_states.get(cid) == "FAILED" else
                          "REASSIGNED")
                release_crane_assignment(conn, assignment_id=assignment["assignment_id"],
                                         release_reason=reason)
        for cid, jid in desired.items():
            if cid in old and old[cid]["job_id"] == jid:
                continue
            job = persisted_jobs.get(jid)
            if job is None:
                raise RuntimeError(f"Job {jid} is missing from run {run_id} in PostgreSQL")
            insert_crane_assignment(conn, crane_id=cid,
                                    simulation_job_id=job["simulation_job_id"])
        for jid, job in jobs.items():
            previous = persisted_jobs.get(jid)
            if previous is None:
                raise RuntimeError(f"Job {jid} is missing from run {run_id} in PostgreSQL")
            state = job["state"]
            if state != previous["status"]:
                update_job_status(conn, run_id=run_id, job_id=jid, status=state)
            completed, remaining = int(job["work_completed"]), int(job["work_remaining"])
            completion_wrote_progress = state == "COMPLETED" and previous["status"] != "COMPLETED"
            if not completion_wrote_progress and (
                completed != previous["work_completed"] or remaining != previous["work_remaining"]
            ):
                update_job_work_progress(conn, run_id=run_id, job_id=jid,
                                         work_completed=completed, work_remaining=remaining)
        updated = 0
        if statuses:
            updated = batch_sync_crane_statuses(conn, statuses)
            if updated != len(statuses):
                raise RuntimeError("A runtime crane is missing from PostgreSQL inventory")
    return {"reconciled_cranes": updated, "reconciled_jobs": len(jobs)}


def _sync_crane_statuses(bridge, request: Request) -> None:
    """Snapshot and persist jobs, crane statuses, and assignments."""
    if getattr(request.app.state, "current_run_id", None) is None:
        return
    if _get_db_pool(request) is None:
        raise HTTPException(status_code=503, detail="Database unavailable; runtime changed and reconciliation is required")
    snap = _send(bridge, {"cmd": "snapshot"})
    try:
        _persist_snapshot(request, snap)
    except Exception:
        logger.exception("Could not synchronize C++ snapshot to PostgreSQL")
        raise HTTPException(status_code=503,
                            detail="Database synchronization failed; runtime changed and reconciliation is required")

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
async def simulation_init(body: InitRequest, request: Request):
    """Close the prior run before replacing its in-memory dispatcher."""
    bridge = _get_bridge(request)
    execution = _get_execution(request)
    cmd: dict[str, Any] = {
        "cmd": "init", "berths": body.berths, "cranes": body.cranes,
        "trucks": body.trucks,
    }
    if body.policy is not None:
        cmd["policy"] = body.policy.value
    if body.batch_mode is not None:
        cmd["batch_mode"] = body.batch_mode

    previous_run_id = getattr(request.app.state, "current_run_id", None)
    pool = _get_db_pool(request)
    if previous_run_id is not None and pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable; active simulation was not reset")
    new_run_id = None

    def prepare_reset(snapshot):
        nonlocal new_run_id, previous_run_id
        previous_run_id = getattr(request.app.state, "current_run_id", None)
        if pool is None:
            if previous_run_id is not None:
                raise RuntimeError("Database unavailable; active simulation was not reset")
            return
        if previous_run_id is not None and snapshot is None:
            raise RuntimeError("Previous simulation snapshot is unavailable")
        now = datetime.now(timezone.utc)
        run_name = f"Run {now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"
        # Commit reconciliation before the destructive C++ init. A DB failure
        # therefore leaves the previous runtime intact.
        with get_connection(pool) as conn:
            if previous_run_id is not None:
                assignments = get_active_assignments_for_run(conn, previous_run_id)
                assigned_ids = {c["crane_id"] for c in snapshot["cranes"]
                                if c["status"] == "ASSIGNED"}
                assigned_ids.update(a["crane_id"] for a in assignments)
                failed_ids = {c["crane_id"] for c in snapshot["cranes"]
                              if c["status"] == "FAILED"}
                assigned_ids.difference_update(failed_ids)
                cancel_unfinished_jobs_for_run(conn, previous_run_id)
                release_active_assignments_for_run(conn, previous_run_id, "SIMULATION_RESET")
                make_assigned_cranes_available(conn, sorted(assigned_ids))
                if failed_ids:
                    failed_statuses = [{"crane_id": cid, "status": "FAILED"}
                                       for cid in sorted(failed_ids)]
                    if batch_sync_crane_statuses(conn, failed_statuses) != len(failed_statuses):
                        raise RuntimeError("A failed runtime crane is missing from inventory")
                terminal = ("COMPLETED" if snapshot["jobs"] and all(
                    job["state"] in ("COMPLETED", "CANCELLED")
                    for job in snapshot["jobs"]) else "FAILED")
                close_simulation_run(conn, previous_run_id, terminal)
            candidate_run_id = insert_simulation_run(
                conn, run_name=run_name,
                scheduling_policy=body.policy.value if body.policy else "FCFS",
                total_berths=body.berths, total_cranes=body.cranes,
                total_trucks=body.trucks,
            )
        new_run_id = candidate_run_id

    try:
        resp = await execution.reset(cmd, before_reset=prepare_reset,
                                     capture_snapshot=lambda: getattr(
                                         request.app.state, "current_run_id", None) is not None)
    except Exception as exc:
        # If DB reconciliation failed, C++ init was never sent. If C++ init
        # failed after commit, the new DB run is closed as a failed attempt.
        if new_run_id is not None:
            try:
                with get_connection(pool) as conn:
                    close_simulation_run(conn, new_run_id)
            except Exception:
                logger.exception("Could not close failed initialization run %s", new_run_id)
            request.app.state.current_run_id = None
            # The old run is already closed in PostgreSQL. Prevent further
            # commands against a possibly still-live old dispatcher.
            request.app.state.bridge = None
        cpp_rejected = isinstance(exc, RuntimeError) and (new_run_id is not None or pool is None)
        detail = (f"Simulation initialization failed: {exc}" if cpp_rejected else
                  "C++ initialization failed after database prepare; restart the backend"
                  if new_run_id is not None else
                  "Simulation initialization failed before C++ reset; previous runtime is intact")
        raise HTTPException(status_code=400 if cpp_rejected else 503, detail=detail)
    request.app.state.current_run_id = new_run_id
    return resp

@router.post("/simulation/cranes")
def simulation_register_crane(body: CraneRegisterRequest, request: Request):
    """Register only an existing AVAILABLE physical crane."""
    bridge = _get_bridge(request)
    crane_id = body.crane_id.strip()
    if not crane_id:
        raise HTTPException(status_code=400, detail="crane_id must not be empty")
    pool = _get_db_pool(request)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database inventory unavailable")
    try:
        with get_connection(pool) as conn:
            crane = get_crane(conn, crane_id)
    except Exception:
        logger.exception("Could not validate crane inventory")
        raise HTTPException(status_code=503, detail="Database inventory unavailable")
    if crane is None:
        raise HTTPException(status_code=404, detail=f"Crane '{crane_id}' is not in PostgreSQL inventory")
    if crane["status"] != "AVAILABLE":
        raise HTTPException(status_code=409, detail=f"Crane '{crane_id}' is {crane['status']}")

    try:
        resp = bridge.send({"cmd": "register_crane", "crane_id": crane_id})
    except CppBridgeError:
        raise HTTPException(status_code=503, detail="C++ simulation bridge is unavailable")
    if not resp.get("ok", False):
        error = resp.get("error", "C++ command rejected")
        code = 409 if "duplicate crane" in error.lower() else 400
        raise HTTPException(status_code=code, detail=error)
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


@router.get("/simulation/execution")
def simulation_execution_status(request: Request):
    """Return the backend runner state."""
    return {"ok": True, **_get_execution(request).status()}


@router.post("/simulation/step")
async def simulation_step(request: Request):
    """Advance one simulated work unit without authorizing batch dispatch."""
    execution = _get_execution(request)
    try:
        response = await execution.step(continue_batch=False)
    except SimulationPersistenceError as exc:
        raise HTTPException(status_code=503, detail=f"Step completed in C++; database reconciliation required: {exc}")
    except CppBridgeError:
        raise HTTPException(status_code=503, detail="C++ simulation bridge is unavailable")
    except RuntimeError as exc:
        code = 409 if execution.state == "running" else 400
        raise HTTPException(status_code=code, detail=str(exc))
    return response


@router.post("/simulation/run")
async def simulation_run(request: Request, body: RunRequest | None = None):
    """Start or resume the single automatic runner."""
    execution = _get_execution(request)
    interval = body.interval_seconds if body is not None else 1.0
    return {"ok": True, **await execution.run(interval)}


@router.post("/simulation/pause")
async def simulation_pause(request: Request):
    """Stop the runner after any in-flight C++ command completes."""
    return {"ok": True, **await _get_execution(request).pause()}


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
async def simulation_crane_fail(crane_id: str, request: Request):
    execution = _get_execution(request)
    async with execution.lock:
        return await asyncio.to_thread(_simulation_crane_fail_sync, crane_id, request)


def _simulation_crane_fail_sync(crane_id: str, request: Request):
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
async def simulation_crane_recover(crane_id: str, request: Request):
    execution = _get_execution(request)
    async with execution.lock:
        return await asyncio.to_thread(_simulation_crane_recover_sync, crane_id, request)


def _simulation_crane_recover_sync(crane_id: str, request: Request):
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
                    # Snapshot reconciliation records any resumption assignment.

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
    """Repair job, crane and assignment rows from the current C++ snapshot."""
    bridge = _get_bridge(request)
    if _get_db_pool(request) is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    snap = _send(bridge, {"cmd": "snapshot"})
    try:
        counts = _persist_snapshot(request, snap)
    except Exception:
        logger.exception("Database reconciliation failed")
        raise HTTPException(status_code=503, detail="Database reconciliation failed")
    return {"ok": True, **counts}

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
