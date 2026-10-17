"""
ml/models/turnaround_predictor.py
=================================
Turnaround Time Predictor for container vessels berthing at port terminals.
"""

from pathlib import Path
from typing import Dict, List, Optional, Union
import json
import numpy as np
import pandas as pd

from .base import BaseModel


class TurnaroundPredictor(BaseModel):
    """
    Predicts vessel berth turnaround duration (in hours) based on container workload,
    crane availability, and operational indicators using regularized regression.
    """

    def __init__(self, l2_penalty: float = 1.0):
        super().__init__(model_name="TurnaroundPredictor")
        self.l2_penalty = l2_penalty
        self.weights: Optional[np.ndarray] = None
        self.feature_names: List[str] = []
        self.mean: Optional[np.ndarray] = None
        self.std: Optional[np.ndarray] = None

    def fit(self, X: Union[pd.DataFrame, np.ndarray], y: np.ndarray) -> "TurnaroundPredictor":
        """
        Trains model weights using closed-form Ridge Regression with standardized features.
        """
        if isinstance(X, pd.DataFrame):
            self.feature_names = list(X.columns)
            X_mat = X.values.astype(np.float64)
        else:
            X_mat = np.asarray(X, dtype=np.float64)
            self.feature_names = [f"f_{i}" for i in range(X_mat.shape[1])]

        y_vec = np.asarray(y, dtype=np.float64)

        # Standardize features for regularized conditioning
        self.mean = np.mean(X_mat, axis=0)
        self.std = np.std(X_mat, axis=0)
        self.std[self.std == 0.0] = 1.0

        X_norm = (X_mat - self.mean) / self.std

        # Add bias / intercept column
        n_samples = X_norm.shape[0]
        X_design = np.hstack([np.ones((n_samples, 1)), X_norm])

        # Closed-form Ridge solution: (X^T X + lambda * I)^(-1) X^T y
        n_features = X_design.shape[1]
        reg_matrix = self.l2_penalty * np.eye(n_features)
        reg_matrix[0, 0] = 0.0  # Do not regularize intercept

        XtX = X_design.T @ X_design + reg_matrix
        Xty = X_design.T @ y_vec
        self.weights = np.linalg.solve(XtX, Xty)

        self.is_trained = True
        return self

    def predict(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        """
        Predicts turnaround duration in hours.
        """
        if not self.is_trained or self.weights is None:
            raise RuntimeError("Model has not been trained yet.")

        if isinstance(X, pd.DataFrame):
            X_mat = X[self.feature_names].values.astype(np.float64)
        else:
            X_mat = np.asarray(X, dtype=np.float64)

        X_norm = (X_mat - self.mean) / self.std
        n_samples = X_norm.shape[0]
        X_design = np.hstack([np.ones((n_samples, 1)), X_norm])

        raw_preds = X_design @ self.weights
        # Enforce realistic port operational bounds (minimum 1.0 hour)
        return np.maximum(raw_preds, 1.0)

    def save(self, filepath: Union[str, Path]) -> None:
        """Serializes model parameters to JSON."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "model_name": self.model_name,
            "l2_penalty": self.l2_penalty,
            "feature_names": self.feature_names,
            "weights": self.weights.tolist() if self.weights is not None else [],
            "mean": self.mean.tolist() if self.mean is not None else [],
            "std": self.std.tolist() if self.std is not None else [],
            "is_trained": self.is_trained,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, filepath: Union[str, Path]) -> "TurnaroundPredictor":
        """Restores model parameters from JSON."""
        path = Path(filepath)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.l2_penalty = data["l2_penalty"]
        self.feature_names = data["feature_names"]
        self.weights = np.array(data["weights"], dtype=np.float64)
        self.mean = np.array(data["mean"], dtype=np.float64)
        self.std = np.array(data["std"], dtype=np.float64)
        self.is_trained = data["is_trained"]
        return self
