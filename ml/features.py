"""Features observable when actual berthing begins."""

import pandas as pd

from .dataset import TIMESTAMP_FORMAT

FEATURES = ("vessel", "month", "weekday")


def make_features(visits: pd.DataFrame) -> pd.DataFrame:
    """Use vessel identity and ATB calendar date, without ATD or outcome fields."""
    required = {"Vessel Name", "ATB"}
    if not required.issubset(visits.columns):
        raise ValueError(f"Feature inputs missing: {sorted(required - set(visits.columns))}")
    vessel = visits["Vessel Name"].astype("string")
    if vessel.isna().any() or vessel.str.strip().eq("").any():
        raise ValueError("Vessel Name cannot be missing")
    atb = pd.to_datetime(visits["ATB"], format=TIMESTAMP_FORMAT, errors="coerce")
    if atb.isna().any():
        raise ValueError("ATB contains missing or invalid timestamps")
    return pd.DataFrame({
        "vessel": vessel.str.strip().astype(str),
        "month": atb.dt.month.astype(str),
        "weekday": atb.dt.weekday.astype(str),
    }, index=visits.index)
