"""
ml/models/crane_allocator.py
============================
Crane Allocation Estimator and Productivity Optimization Model.
"""

from pathlib import Path
from typing import Dict, Optional, Union
import json
import numpy as np
import pandas as pd

from .base import BaseModel


class CraneAllocator(BaseModel):
    """
    Predicts optimal crane allocation and productivity (moves/hour) for container port calls.
    """

    def __init__(self, min_cranes: int = 1, max_cranes: int = 6):
        super().__init__(model_name="CraneAllocator")
        self.min_cranes = min_cranes
        self.max_cranes = max_cranes
        self.avg_productivity_bpr: float = 35.0  # Default moves/hour across cranes
        self.moves_thresholds: Dict[str, float] = {}

    def fit(self, X: Union[pd.DataFrame, np.ndarray], y: np.ndarray) -> "CraneAllocator":
        """
        Learns workload thresholds and empirical crane ratios from historical port data.
        """
        if isinstance(X, pd.DataFrame):
            moves = X["moves"].values if "moves" in X.columns else X.iloc[:, 0].values
        else:
            moves = X[:, 0]

        ratios = np.asarray(y, dtype=np.float64)

        # Segment moves into quantiles
        q25, q50, q75 = np.percentile(moves, [25, 50, 75])
        self.moves_thresholds = {
            "q25": float(q25),
            "q50": float(q50),
            "q75": float(q75),
        }

        # Average historical ratio
        self.avg_crane_ratio = float(np.mean(ratios))
        self.is_trained = True
        return self

    def predict(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        """
        Predicts expected crane ratio for given workload.
        """
        if not self.is_trained:
            raise RuntimeError("CraneAllocator model is not trained.")

        if isinstance(X, pd.DataFrame):
            moves = X["moves"].values if "moves" in X.columns else X.iloc[:, 0].values
        else:
            moves = X[:, 0]

        # Dynamic allocation heuristic based on workload
        preds = []
        for m in moves:
            if m <= self.moves_thresholds["q25"]:
                val = 1.5
            elif m <= self.moves_thresholds["q50"]:
                val = 2.0
            elif m <= self.moves_thresholds["q75"]:
                val = 2.8
            else:
                val = 3.8
            preds.append(np.clip(val, self.min_cranes, self.max_cranes))

        return np.array(preds, dtype=np.float64)

    def recommend_cranes_for_target(self, moves: int, target_hours: float) -> int:
        """
        Determines the integer number of cranes required to complete operations within target_hours.
        Formula: Needed Cranes = ceil(moves / (target_hours * productivity_per_crane))
        """
        if target_hours <= 0:
            return self.max_cranes
        productivity_per_crane = self.avg_productivity_bpr / 2.0  # Approx per-crane bph
        needed = int(np.ceil(moves / (target_hours * productivity_per_crane)))
        return max(self.min_cranes, min(self.max_cranes, needed))

    def save(self, filepath: Union[str, Path]) -> None:
        """Serializes allocator thresholds to JSON."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "model_name": self.model_name,
            "min_cranes": self.min_cranes,
            "max_cranes": self.max_cranes,
            "avg_productivity_bpr": self.avg_productivity_bpr,
            "moves_thresholds": self.moves_thresholds,
            "is_trained": self.is_trained,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, filepath: Union[str, Path]) -> "CraneAllocator":
        """Restores allocator parameters from JSON."""
        path = Path(filepath)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.min_cranes = data["min_cranes"]
        self.max_cranes = data["max_cranes"]
        self.avg_productivity_bpr = data["avg_productivity_bpr"]
        self.moves_thresholds = data["moves_thresholds"]
        self.is_trained = data["is_trained"]
        return self
