"""One read-only offline interface for the selected berth-duration experiment.

Call only at actual berthing. Load only trusted, locally trained joblib files;
joblib is not a safe format for untrusted input. This module does not touch the
simulator, database, backend, or frontend.
"""

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from .features import FEATURE_SETS, make_features

DEFAULT_ARTIFACT_DIR = Path(__file__).parent / "artifacts"
MODEL_FILE = "berth_duration_pipeline.joblib"
METADATA_FILE = "metadata.json"
SELECTED_MODEL = "vessel_forest"
SELECTED_FEATURES = FEATURE_SETS["vessel"]


def _result(vessel_name: str | None, *, reason: str | None = None,
            hours: float | None = None, metadata: dict | None = None) -> dict:
    available = reason is None
    return {
        "status": "available" if available else "unavailable",
        "vessel_name": vessel_name,
        "estimated_duration_hours": hours if available else None,
        "units": "hours",
        "model_version": metadata["model_version"] if metadata else None,
        "training_period": metadata["training_period"] if metadata else None,
        "predicted_at": datetime.now(timezone.utc).isoformat() if available else None,
        "prediction_moment": "actual berthing",
        "explanation": (
            "Historical vessel-based estimate of berth-to-departure duration; actual duration may differ."
            if available else "No duration estimate is available for this request."
        ),
        "reason": reason,
    }


def _load_metadata(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Metadata must be an object")
    required = ("selected_model", "features", "model_sha256", "model_version",
                "training_period", "versions", "target")
    if any(key not in data for key in required):
        raise ValueError("Metadata is incomplete")
    if data["selected_model"] != SELECTED_MODEL:
        raise ValueError("Metadata describes a different model")
    if data["features"] != list(SELECTED_FEATURES):
        raise RuntimeError("Metadata feature schema is incompatible")
    digest = data["model_sha256"]
    if (not isinstance(digest, str) or len(digest) != 64 or
            any(character not in "0123456789abcdef" for character in digest)):
        raise ValueError("Invalid model digest")
    if data["model_version"] != f"{SELECTED_MODEL}:{digest[:12]}":
        raise ValueError("Model version does not match digest")
    if not isinstance(data["training_period"], str) or not data["training_period"].strip():
        raise ValueError("Training period is missing")
    if (not isinstance(data["versions"], dict) or
            not isinstance(data["versions"].get("scikit_learn"), str)):
        raise ValueError("Dependency versions are missing")
    if data["target"] != "ATD minus ATB in hours; predicted at actual berthing":
        raise ValueError("Metadata target is incompatible")
    return data


def _vocabulary(pipeline: Pipeline) -> set[str]:
    if (not isinstance(pipeline, Pipeline) or
            not isinstance(pipeline.named_steps.get("preprocess"), ColumnTransformer) or
            not isinstance(pipeline.named_steps.get("regressor"), RandomForestRegressor)):
        raise ValueError("The saved object is not the selected pipeline")
    preprocessor = pipeline.named_steps["preprocess"]
    if tuple(preprocessor.feature_names_in_) != SELECTED_FEATURES:
        raise ValueError("The saved pipeline feature schema is incompatible")
    if len(preprocessor.transformers_) != 1:
        raise ValueError("Unexpected preprocessing steps")
    _, encoder, columns = preprocessor.transformers_[0]
    if (not isinstance(encoder, OneHotEncoder) or list(columns) != list(SELECTED_FEATURES)
            or len(encoder.categories_) != 1):
        raise ValueError("The vessel encoder is incompatible")
    return set(encoder.categories_[0])


def predict_duration(vessel_name: str | None, artifact_dir: Path = DEFAULT_ARTIFACT_DIR) -> dict:
    """Return a structured estimate or an explicit unavailable reason.

    Input is the exact historical vessel name, supplied at actual berthing.
    ATB is intentionally absent: the selected model uses vessel identity only.
    """
    if vessel_name is None or (isinstance(vessel_name, str) and not vessel_name.strip()):
        return _result(None, reason="missing_vessel")
    if not isinstance(vessel_name, str):
        return _result(None, reason="invalid_vessel")
    vessel = vessel_name.strip()
    try:
        directory = Path(artifact_dir)
    except (TypeError, ValueError):
        return _result(vessel, reason="invalid_artifact_location")
    model_path = directory / MODEL_FILE
    if not model_path.is_file():
        return _result(vessel, reason="missing_model_artifact")
    try:
        metadata = _load_metadata(directory / METADATA_FILE)
    except RuntimeError:
        return _result(vessel, reason="incompatible_feature_schema")
    except (OSError, ValueError, TypeError, KeyError):
        return _result(vessel, reason="invalid_model_metadata")
    if metadata["versions"]["scikit_learn"] != sklearn.__version__:
        return _result(vessel, reason="incompatible_runtime", metadata=metadata)
    try:
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != metadata["model_sha256"]:
            return _result(vessel, reason="model_artifact_mismatch", metadata=metadata)
        pipeline = joblib.load(model_path)
    except Exception:
        return _result(vessel, reason="model_load_failed", metadata=metadata)
    try:
        known_vessels = _vocabulary(pipeline)
    except Exception:
        return _result(vessel, reason="incompatible_feature_schema", metadata=metadata)
    if vessel not in known_vessels:
        return _result(vessel, reason="unseen_vessel", metadata=metadata)
    try:
        features = make_features(pd.DataFrame([{"Vessel Name": vessel}]), SELECTED_FEATURES)
        hours = float(pipeline.predict(features)[0])
        if not math.isfinite(hours) or hours <= 0:
            raise ValueError("Invalid prediction")
    except Exception:
        return _result(vessel, reason="prediction_failed", metadata=metadata)
    return _result(vessel, hours=hours, metadata=metadata)
