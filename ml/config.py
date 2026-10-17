"""
ml/config.py
============
Configuration settings and paths for the Container-Port-Flow Machine Learning Subsystem.
"""

from pathlib import Path
from typing import List

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ML_ROOT = PROJECT_ROOT / "ml"
DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DATA_PATH = DATA_DIR / "processed" / "port_calls_cleaned.csv"
ARTIFACTS_DIR = DATA_DIR / "ml" / "artifacts"

# Ensure artifacts directory exists
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# Training Hyperparameters & Defaults
RANDOM_SEED: int = 42
TEST_SPLIT_RATIO: float = 0.2

# Dataset Columns Specification
REQUIRED_RAW_COLUMNS: List[str] = [
    "Vessel Name",
    "OPR",
    "Month",
    "ATB",
    "ATD",
    "Shipping Line",
    "Total Teus",
    "Moves",
    "Berth Time",
    "Berth Working Time",
    "Gross Crane Time",
    "Crane Ratio",
    "BPR (mph)",
]

TARGET_COLUMNS = {
    "turnaround_time": "Berth Time",
    "working_time": "Berth Working Time",
    "crane_ratio": "Crane Ratio",
    "bpr": "BPR (mph)",
}

FEATURE_COLUMNS: List[str] = [
    "total_teus",
    "moves",
    "crane_ratio",
    "month_num",
    "teus_per_move",
    "opr_encoded",
]
