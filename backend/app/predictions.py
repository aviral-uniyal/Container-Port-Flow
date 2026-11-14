"""Experimental, read-only duration predictions, isolated from simulation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(prefix="/predictions", tags=["predictions"])


class BerthDurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    vessel_name: str = Field(min_length=1, max_length=200)

    @field_validator("vessel_name")
    @classmethod
    def require_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("vessel_name must not be blank")
        return value


class BerthDurationResponse(BaseModel):
    designation: Literal["experimental_advisory"]
    status: Literal["available", "unavailable"]
    vessel_name: str | None
    estimated_duration_hours: float | None
    units: Literal["hours"]
    model_version: str | None
    training_period: str | None
    predicted_at: str | None
    prediction_moment: Literal["actual berthing"]
    explanation: str
    reason: str | None


def _unavailable(vessel_name: str, reason: str) -> dict:
    """Preserve the offline response shape when inference cannot be imported."""
    return {
        "status": "unavailable",
        "vessel_name": vessel_name,
        "estimated_duration_hours": None,
        "units": "hours",
        "model_version": None,
        "training_period": None,
        "predicted_at": None,
        "prediction_moment": "actual berthing",
        "explanation": "No duration estimate is available for this request.",
        "reason": reason,
    }


def _predict_duration(vessel_name: str) -> dict:
    """Import ML only on request; startup and simulation need no ML artifact."""
    from ml.inference import predict_duration

    artifact_dir = os.environ.get("ML_ARTIFACT_DIR")
    if artifact_dir:
        return predict_duration(vessel_name, Path(artifact_dir))
    return predict_duration(vessel_name)


@router.post("/berth-duration", response_model=BerthDurationResponse)
def berth_duration(body: BerthDurationRequest) -> BerthDurationResponse:
    try:
        prediction = _predict_duration(body.vessel_name)
        return BerthDurationResponse(designation="experimental_advisory", **prediction)
    except ImportError:
        prediction = _unavailable(body.vessel_name, "ml_dependencies_unavailable")
    except Exception:
        prediction = _unavailable(body.vessel_name, "prediction_failed")
    return BerthDurationResponse(designation="experimental_advisory", **prediction)
