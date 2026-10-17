"""
ml/datasets/loader.py
=====================
Loads, validates, and partitions historical port calls data for ML training and evaluation.
"""

from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import numpy as np
import pandas as pd

from ml.config import PROCESSED_DATA_PATH, REQUIRED_RAW_COLUMNS, RANDOM_SEED, TEST_SPLIT_RATIO


class PortCallsDatasetLoader:
    """
    Ingests and validates historical port calls dataset.
    """

    def __init__(self, data_path: Optional[Path] = None):
        self.data_path = Path(data_path) if data_path else PROCESSED_DATA_PATH
        self._raw_df: Optional[pd.DataFrame] = None

    def load_data(self) -> pd.DataFrame:
        """
        Loads the raw CSV dataset, performs basic validation and returns a DataFrame.
        """
        if not self.data_path.exists():
            raise FileNotFoundError(f"Port calls dataset not found at: {self.data_path}")

        df = pd.read_csv(self.data_path)

        # Validate required columns existence
        missing_cols = [col for col in REQUIRED_RAW_COLUMNS if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Dataset missing required columns: {missing_cols}")

        self._raw_df = df.copy()
        return df

    def get_summary_stats(self) -> Dict[str, Any]:
        """
        Computes summary statistics on core operational metrics.
        """
        if self._raw_df is None:
            self.load_data()

        df = self._raw_df
        return {
            "total_records": len(df),
            "unique_vessels": df["Vessel Name"].nunique(),
            "unique_operators": df["OPR"].nunique(),
            "mean_berth_time": float(df["Berth Time"].mean()),
            "mean_moves": float(df["Moves"].mean()),
            "mean_crane_ratio": float(df["Crane Ratio"].mean()),
        }

    def train_test_split(
        self,
        test_size: float = TEST_SPLIT_RATIO,
        random_seed: int = RANDOM_SEED
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Performs reproducible train-test split without external dependencies.
        """
        if self._raw_df is None:
            self.load_data()

        df = self._raw_df.copy()
        rng = np.random.RandomState(random_seed)
        shuffled_indices = rng.permutation(len(df))

        split_idx = int(len(df) * (1.0 - test_size))
        train_indices = shuffled_indices[:split_idx]
        test_indices = shuffled_indices[split_idx:]

        train_df = df.iloc[train_indices].reset_index(drop=True)
        test_df = df.iloc[test_indices].reset_index(drop=True)

        return train_df, test_df
