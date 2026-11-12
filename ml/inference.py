"""Offline inference helper; never sends commands to the C++ dispatcher."""

from pathlib import Path

import joblib
import pandas as pd

from .features import make_features


def predict_hours(model_path: Path, vessel_name: str, actual_atb: str) -> float:
    """Predict for a vessel at its actual berthing timestamp.

    Only load locally trained, trusted joblib files. This is not an API.
    """
    pipeline = joblib.load(model_path)
    inputs = pd.DataFrame([{"Vessel Name": vessel_name, "ATB": actual_atb}])
    return float(pipeline.predict(make_features(inputs))[0])
