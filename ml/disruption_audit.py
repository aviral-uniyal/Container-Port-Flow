"""Read-only descriptive audit; does not construct labels or train a model.

Run: python -m ml.disruption_audit
"""

import json

import pandas as pd

from .dataset import CSV_PATH, load_visits

FIELDS = (
    "QUAY CRANE BREAKDOWN",
    "WAITING CONTAINER",
    "WAITING INTERNAL TRUCK",
    "GEAR BOX HANDLING",
    "BAD WEATHER",
    "SYSTEM FAILURE",
)


def audit(path=CSV_PATH) -> dict:
    visits = load_visits(path)
    report = {}
    for field in FIELDS:
        if field not in visits.columns:
            raise ValueError(f"Missing disruption field: {field}")
        values = pd.to_numeric(visits[field], errors="coerce")
        nonzero = values[values.ne(0) & values.notna()]
        report[field] = {
            "rows": int(len(values)),
            "missing": int(values.isna().sum()),
            "zero": int(values.eq(0).sum()),
            "nonzero": int(len(nonzero)),
            "negative": int(values.lt(0).sum()),
            "distinct_values": int(values.nunique()),
            "min": float(values.min()) if values.notna().any() else None,
            "median": float(values.median()) if values.notna().any() else None,
            "p90": float(values.quantile(0.9)) if values.notna().any() else None,
            "max": float(values.max()) if values.notna().any() else None,
            "units_documented": False,
            "event_timing_documented": False,
        }
    return report


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
