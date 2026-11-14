"""Features observable when actual berthing begins."""

import pandas as pd

from .dataset import TIMESTAMP_FORMAT

FEATURES = ("vessel", "month", "weekday")
FEATURE_SETS = {
    "calendar": ("month", "weekday"),
    "vessel": ("vessel",),
    "vessel_calendar": FEATURES,
}


def make_features(visits: pd.DataFrame, features: tuple[str, ...] = FEATURES) -> pd.DataFrame:
    """Build only requested pre-outcome features; vessel-only needs no ATB input."""
    if not features or len(set(features)) != len(features) or not set(features).issubset(FEATURES):
        raise ValueError("Unsupported feature schema")
    values = {}
    if "vessel" in features:
        if "Vessel Name" not in visits.columns:
            raise ValueError("Feature inputs missing: Vessel Name")
        vessel = visits["Vessel Name"].astype("string")
        if vessel.isna().any() or vessel.str.strip().eq("").any():
            raise ValueError("Vessel Name cannot be missing")
        values["vessel"] = vessel.str.strip().astype(str)
    if set(features) & {"month", "weekday"}:
        if "ATB" not in visits.columns:
            raise ValueError("Feature inputs missing: ATB")
        atb = pd.to_datetime(visits["ATB"], format=TIMESTAMP_FORMAT, errors="coerce")
        if atb.isna().any():
            raise ValueError("ATB contains missing or invalid timestamps")
        if "month" in features:
            values["month"] = atb.dt.month.astype(str)
        if "weekday" in features:
            values["weekday"] = atb.dt.weekday.astype(str)
    return pd.DataFrame(values, index=visits.index)[list(features)]
