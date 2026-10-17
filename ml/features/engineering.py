"""
ml/features/engineering.py
==========================
Transforms raw port calls operational attributes into model-ready numerical feature matrices.
"""

from typing import Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd

MONTH_MAP: Dict[str, int] = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
    "january": 1, "february": 2, "march": 3, "may": 5,
    "june": 6, "july": 7, "august": 8, "october": 10, "december": 12,
}

DELAY_COLUMNS: List[str] = [
    "QUAY CRANE BREAKDOWN",
    "WAITING CONTAINER",
    "WAITING INTERNAL TRUCK",
    "GEAR BOX HANDLING",
    "BAD WEATHER",
    "SYSTEM FAILURE"
]


class FeatureEngineer:
    """
    Stateful feature engineering pipeline that transforms historical and real-time
    vessel call inputs into normalized numeric vectors.
    """

    def __init__(self):
        self.opr_to_idx: Dict[str, int] = {}
        self.default_opr_idx: int = 0
        self.feature_means: Dict[str, float] = {}
        self.feature_stds: Dict[str, float] = {}
        self.is_fitted: bool = False

    def _parse_month(self, month_val: Union[str, int, float]) -> int:
        """Converts textual or numerical month representation to integer (1-12)."""
        if pd.isna(month_val):
            return 1
        if isinstance(month_val, (int, float)):
            val = int(month_val)
            return val if 1 <= val <= 12 else 1
        cleaned = str(month_val).strip().lower()
        return MONTH_MAP.get(cleaned, 1)

    def fit(self, df: pd.DataFrame) -> "FeatureEngineer":
        """
        Learns categorical encodings and normalization statistics from training data.
        """
        # Learn top operators
        unique_oprs = df["OPR"].dropna().str.strip().unique().tolist()
        self.opr_to_idx = {opr: idx + 1 for idx, opr in enumerate(sorted(unique_oprs))}
        self.default_opr_idx = 0

        # Precompute means for imputation
        self.feature_means["total_teus"] = float(df["Total Teus"].fillna(0).mean())
        self.feature_means["moves"] = float(df["Moves"].fillna(0).mean())
        self.feature_means["crane_ratio"] = float(df["Crane Ratio"].fillna(2.0).mean())

        self.is_fitted = True
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Transforms input DataFrame into engineered feature representation.
        """
        if not self.is_fitted:
            raise RuntimeError("FeatureEngineer must be fitted before calling transform().")

        out = pd.DataFrame(index=df.index)

        # 1. Workload attributes
        total_teus = pd.to_numeric(df.get("Total Teus", self.feature_means["total_teus"]), errors="coerce").fillna(self.feature_means["total_teus"])
        moves = pd.to_numeric(df.get("Moves", self.feature_means["moves"]), errors="coerce").fillna(self.feature_means["moves"])
        crane_ratio = pd.to_numeric(df.get("Crane Ratio", self.feature_means["crane_ratio"]), errors="coerce").fillna(self.feature_means["crane_ratio"])

        out["total_teus"] = total_teus
        out["moves"] = moves
        out["crane_ratio"] = crane_ratio

        # 2. Derived interaction metrics
        out["teus_per_move"] = total_teus / (moves + 1.0)
        out["moves_per_crane"] = moves / (crane_ratio + 0.1)

        # 3. Temporal attributes
        if "Month" in df.columns:
            out["month_num"] = df["Month"].apply(self._parse_month)
        else:
            out["month_num"] = 1

        # 4. Operator encoding
        if "OPR" in df.columns:
            out["opr_encoded"] = df["OPR"].apply(
                lambda x: self.opr_to_idx.get(str(x).strip(), self.default_opr_idx) if pd.notna(x) else self.default_opr_idx
            )
        else:
            out["opr_encoded"] = self.default_opr_idx

        # 5. Total delay impact (if delay columns available)
        existing_delay_cols = [c for c in DELAY_COLUMNS if c in df.columns]
        if existing_delay_cols:
            out["total_delay_hours"] = df[existing_delay_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).sum(axis=1)
        else:
            out["total_delay_hours"] = 0.0

        return out

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fits and transforms in a single pass."""
        return self.fit(df).transform(df)

    def extract_targets(self, df: pd.DataFrame) -> Dict[str, np.ndarray]:
        """
        Extracts target ground truth columns as numpy arrays.
        """
        targets = {}
        if "Berth Time" in df.columns:
            targets["turnaround_time"] = df["Berth Time"].astype(float).values
        if "Berth Working Time" in df.columns:
            targets["working_time"] = df["Berth Working Time"].astype(float).values
        if "Crane Ratio" in df.columns:
            targets["crane_ratio"] = df["Crane Ratio"].astype(float).values
        if "BPR (mph)" in df.columns:
            targets["bpr"] = pd.to_numeric(df["BPR (mph)"], errors="coerce").fillna(0.0).values
        return targets
