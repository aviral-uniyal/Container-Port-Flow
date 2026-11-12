"""Read and validate the immutable cleaned historical CSV."""

from pathlib import Path

import pandas as pd

CSV_PATH = Path(__file__).resolve().parents[1] / "data/processed/port_calls_cleaned.csv"
REQUIRED = ("Vessel Name", "ATB", "ATD")
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
TARGET = "duration_hours"


def load_visits(path: Path = CSV_PATH) -> pd.DataFrame:
    """Return every visit with parsed timestamps and a positive duration target.

    Invalid rows fail loudly. No unusual duration is silently excluded.
    """
    visits = pd.read_csv(path, dtype={"Vessel Name": "string"})
    missing_columns = set(REQUIRED) - set(visits.columns)
    if missing_columns:
        raise ValueError(f"Missing required columns: {sorted(missing_columns)}")
    if visits.empty:
        raise ValueError("The historical dataset is empty")
    if visits["Vessel Name"].isna().any() or visits["Vessel Name"].str.strip().eq("").any():
        raise ValueError("Vessel Name is missing in at least one visit")
    visits = visits.copy()
    for column in ("ATB", "ATD"):
        parsed = pd.to_datetime(visits[column], format=TIMESTAMP_FORMAT, errors="coerce")
        if parsed.isna().any():
            raise ValueError(f"{column} has {int(parsed.isna().sum())} missing or invalid timestamps")
        visits[column] = parsed
    visits[TARGET] = (visits["ATD"] - visits["ATB"]).dt.total_seconds() / 3600
    if (~(visits[TARGET] > 0)).any():
        raise ValueError("ATD must be later than ATB for every visit")
    return visits


def describe_target(visits: pd.DataFrame) -> dict:
    durations = visits[TARGET]
    q1, q3 = durations.quantile([0.25, 0.75])
    upper = q3 + 3 * (q3 - q1)  # descriptive flag, not an exclusion rule
    return {
        "rows": int(len(visits)),
        "vessels": int(visits["Vessel Name"].nunique()),
        "min_hours": float(durations.min()),
        "median_hours": float(durations.median()),
        "p90_hours": float(durations.quantile(0.9)),
        "max_hours": float(durations.max()),
        "far_outlier_threshold_hours": float(upper),
        "far_outlier_count": int((durations > upper).sum()),
        "excluded_invalid_target_rows": 0,
        "excluded_outlier_rows": 0,
    }
