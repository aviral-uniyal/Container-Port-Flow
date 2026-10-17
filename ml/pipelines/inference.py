"""
ml/pipelines/inference.py
=========================
Real-time inference service for port dispatching and berth scheduling.
"""

from pathlib import Path
from typing import Dict, Any, Optional
import json
import numpy as np
import pandas as pd

from ml.config import ARTIFACTS_DIR
from ml.features.engineering import FeatureEngineer
from ml.models.turnaround_predictor import TurnaroundPredictor
from ml.models.crane_allocator import CraneAllocator


class MLInferenceService:
    """
    Serves predictions to backend API and OS dispatch simulation.
    """

    def __init__(self, artifacts_dir: Path = ARTIFACTS_DIR):
        self.artifacts_dir = Path(artifacts_dir)
        self.turnaround_model = TurnaroundPredictor()
        self.crane_allocator = CraneAllocator()
        self.engineer = FeatureEngineer()
        self.is_ready: bool = False
        self._load_service()

    def _load_service(self) -> None:
        """Loads trained weights and feature statistics from disk."""
        turnaround_file = self.artifacts_dir / "turnaround_predictor.json"
        crane_file = self.artifacts_dir / "crane_allocator.json"
        feature_meta_file = self.artifacts_dir / "feature_metadata.json"

        if turnaround_file.exists() and crane_file.exists() and feature_meta_file.exists():
            self.turnaround_model.load(turnaround_file)
            self.crane_allocator.load(crane_file)

            with open(feature_meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            self.engineer.opr_to_idx = meta.get("opr_to_idx", {})
            self.engineer.default_opr_idx = meta.get("default_opr_idx", 0)
            self.engineer.feature_means = meta.get("feature_means", {})
            self.engineer.is_fitted = True

            self.is_ready = True
        else:
            self.is_ready = False

    def predict_vessel_call(
        self,
        vessel_name: str,
        total_teus: float,
        moves: int,
        operator: str = "GENERIC",
        month: str = "Januari",
        target_hours: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Generates operational turnaround and resource recommendations.
        """
        input_df = pd.DataFrame([{
            "Vessel Name": vessel_name,
            "Total Teus": float(total_teus),
            "Moves": int(moves),
            "OPR": operator,
            "Month": month,
            "Crane Ratio": 2.0,
        }])

        if not self.is_ready:
            # Fallback estimation if model artifacts not yet trained
            approx_hours = max(2.0, moves / 35.0)
            recommended_cranes = 2
            return {
                "vessel_name": vessel_name,
                "estimated_berth_hours": round(approx_hours, 2),
                "recommended_crane_ratio": 2.0,
                "recommended_cranes": recommended_cranes,
                "is_ml_backed": False,
            }

        X = self.engineer.transform(input_df)
        predicted_turnaround = float(self.turnaround_model.predict(X)[0])
        predicted_crane_ratio = float(self.crane_allocator.predict(X)[0])

        if target_hours and target_hours > 0:
            recommended_cranes = self.crane_allocator.recommend_cranes_for_target(moves, target_hours)
        else:
            recommended_cranes = int(np.clip(np.round(predicted_crane_ratio), 1, 6))

        return {
            "vessel_name": vessel_name,
            "estimated_berth_hours": round(predicted_turnaround, 2),
            "recommended_crane_ratio": round(predicted_crane_ratio, 2),
            "recommended_cranes": recommended_cranes,
            "is_ml_backed": True,
        }
