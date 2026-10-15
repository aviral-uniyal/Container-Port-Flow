"""
FastAPI router – Container-Port-Flow simulation endpoints.

Every handler is a thin translation layer:
  HTTP request  →  Pydantic validation  →  CppBridge.send()  →  HTTP response

C++ owns all scheduling/resource logic; Python only validates input shape
and maps responses / errors to appropriate HTTP status codes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .cpp_bridge import CppBridgeError
from .models import (
    CraneRegisterRequest,
    InitRequest,
    JobSubmitRequest,
)

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


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/health")
def health(request: Request):
    """Report API and bridge liveness."""
    bridge = getattr(request.app.state, "bridge", None)
    bridge_alive = bridge is not None and bridge.is_alive
    status = "ready" if bridge_alive else "degraded"
    return {"status": status, "bridge_alive": bridge_alive}


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
    return _send(bridge, cmd)


@router.post("/simulation/cranes")
def simulation_register_crane(body: CraneRegisterRequest, request: Request):
    """Register a crane with the C++ dispatcher."""
    bridge = _get_bridge(request)
    cmd: dict[str, Any] = {
        "cmd": "register_crane",
        "crane_id": body.crane_id,
    }
    return _send(bridge, cmd)


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
    return _send(bridge, cmd)


@router.post("/simulation/jobs/{job_id}/complete")
def simulation_complete_job(job_id: str, request: Request):
    """Mark an active job as completed."""
    bridge = _get_bridge(request)
    cmd: dict[str, Any] = {
        "cmd": "complete_job",
        "job_id": job_id,
    }
    return _send(bridge, cmd)


@router.get("/simulation/snapshot")
def simulation_snapshot(request: Request):
    """Return the full C++ simulation state snapshot."""
    bridge = _get_bridge(request)
    return _send(bridge, {"cmd": "snapshot"})
