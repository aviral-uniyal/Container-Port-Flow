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
    check_connection,
    get_connection,
    get_resource_inventory,
    insert_simulation_job,
    insert_simulation_run,
    update_job_status,
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
    """Register a crane with the C++ dispatcher."""
    bridge = _get_bridge(request)
    cmd: dict[str, Any] = {
        "cmd": "register_crane",
        "crane_id": body.crane_id,
    }
    resp = _send(bridge, cmd)

    # ── Persist crane registration ────────────────────────────────────────
    pool = _get_db_pool(request)
    if pool is not None:
        try:
            with get_connection(pool) as conn:
                upsert_crane(
                    conn,
                    crane_id=body.crane_id,
                    terminal_id=1,
                    status="AVAILABLE",
                )
            logger.info("Persisted crane %s", body.crane_id)
        except Exception as exc:
            logger.warning("DB write failed for crane %s: %s", body.crane_id, exc)
    else:
        logger.warning(
            "No database pool available; skipping crane persistence"
        )

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
                logger.info("Persisted completion for job %s run %d", job_id, run_id)
            except Exception as exc:
                logger.warning(
                    "DB update failed for job %s completion: %s", job_id, exc
                )
        else:
            logger.warning(
                "No database pool available; skipping job completion persistence"
            )

    return resp


@router.get("/simulation/snapshot")
def simulation_snapshot(request: Request):
    """Return the full C++ simulation state snapshot."""
    bridge = _get_bridge(request)
    return _send(bridge, {"cmd": "snapshot"})


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
