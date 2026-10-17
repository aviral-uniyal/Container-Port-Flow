"""
ml/models/base.py
=================
Abstract base class and core evaluation metrics for Container-Port-Flow models.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Union, Any
import json
import numpy as np
import pandas as pd


class BaseModel(ABC):
    """
    Abstract Base Class for predictive models.
    """

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.is_trained: bool = False

    @abstractmethod
    def fit(self, X: Union[pd.DataFrame, np.ndarray], y: np.ndarray) -> "BaseModel":
        """Fits model parameters from training matrices."""
        pass

    @abstractmethod
    def predict(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        """Generates continuous predictions for input features."""
        pass

    @abstractmethod
    def save(self, filepath: Union[str, Path]) -> None:
        """Serializes model weights and configuration."""
        pass

    @abstractmethod
    def load(self, filepath: Union[str, Path]) -> "BaseModel":
        """Restores model weights and configuration."""
        pass


def calculate_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    """
    Calculates regression evaluation metrics (MAE, RMSE, R2) natively in numpy.
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)

    # MAE
    mae = float(np.mean(np.abs(y_true - y_pred)))

    # RMSE
    mse = float(np.mean((y_true - y_pred) ** 2))
    rmse = float(np.sqrt(mse))

    # R2 Score
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    r2 = float(1.0 - (ss_res / (ss_tot + 1e-9)))

    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "r2": round(r2, 4),
    }
