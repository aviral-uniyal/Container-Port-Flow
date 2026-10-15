"""
Pydantic request models for the Container-Port-Flow API.

Each model corresponds to a JSON body accepted by a POST endpoint.
Validation rules mirror what the C++ protocol expects so that obviously
invalid requests are rejected at the HTTP layer with a 422 response
before reaching the bridge.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────


class DispatchPolicy(str, Enum):
    """Scheduling policies supported by the C++ PortDispatcher."""
    FCFS = "FCFS"
    SJF = "SJF"
    PRIORITY = "PRIORITY"


# ─── Request bodies ───────────────────────────────────────────────────────────


class InitRequest(BaseModel):
    """POST /simulation/init – initialise the PortDispatcher."""
    berths: int = Field(..., ge=1, description="Number of berths (≥ 1)")
    cranes: int = Field(..., ge=1, description="Number of cranes (≥ 1)")
    trucks: int = Field(..., ge=0, description="Number of trucks (≥ 0)")
    policy: Optional[DispatchPolicy] = Field(
        default=None,
        description="Scheduling policy; defaults to FCFS in C++",
    )


class CraneRegisterRequest(BaseModel):
    """POST /simulation/cranes – register a crane."""
    crane_id: str = Field(
        ..., min_length=1, description="Unique crane identifier"
    )


class JobSubmitRequest(BaseModel):
    """POST /simulation/jobs – submit a vessel job."""
    job_id: str = Field(
        ..., min_length=1, description="Unique job identifier"
    )
    arrival_time: int = Field(..., ge=0, description="Arrival time (≥ 0)")
    work_total: int = Field(..., ge=1, description="Total work units (≥ 1)")
    priority: int = Field(..., ge=0, description="Priority level (≥ 0)")
    berths: int = Field(..., ge=1, description="Berths required (≥ 1)")
    cranes: int = Field(..., ge=0, description="Cranes required (≥ 0)")
    trucks: int = Field(..., ge=0, description="Trucks required (≥ 0)")
