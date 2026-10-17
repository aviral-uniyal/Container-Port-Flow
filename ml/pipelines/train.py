"""
ml/pipelines/train.py
=====================
End-to-end training and evaluation pipeline for Container-Port-Flow ML models.
"""

from pathlib import Path
from typing import Dict, Any
import json
import numpy as np

from ml.config import ARTIFACTS_DIR
from ml.datasets.loader import PortCallsDatasetLoader
from ml.features.engineering import FeatureEngineer
from ml.models.base import calculate_metrics
from ml.models.turnaround_predictor import TurnaroundPredictor
from ml.models.crane_allocator import CraneAllocator


def run_training_pipeline(artifacts_dir: Path = ARTIFACTS_DIR) -> Dict[str, Any]:
    """
    Executes data loading, feature transformation, model training, evaluation, and serialization.
    """
    print("=" * 60)
    print("CONTAINER-PORT-FLOW: ML TRAINING PIPELINE")
    print("=" * 60)

    # 1. Ingestion
    loader = PortCallsDatasetLoader()
    print("Loading historical port calls dataset...")
    train_df, test_df = loader.train_test_split(test_size=0.2)
    print(f"Dataset partitioned: {len(train_df)} train calls, {len(test_df)} test calls.")

    # 2. Feature Engineering
    engineer = FeatureEngineer()
    X_train = engineer.fit_transform(train_df)
    X_test = engineer.transform(test_df)

    y_train = engineer.extract_targets(train_df)
    y_test = engineer.extract_targets(test_df)

    # 3. Train Turnaround Predictor
    print("\nTraining TurnaroundPredictor...")
    turnaround_model = TurnaroundPredictor(l2_penalty=1.0)
    turnaround_model.fit(X_train, y_train["turnaround_time"])

    test_preds_turnaround = turnaround_model.predict(X_test)
    turnaround_metrics = calculate_metrics(y_test["turnaround_time"], test_preds_turnaround)
    print(f"Turnaround Predictor Test Metrics: {turnaround_metrics}")

    # 4. Train Crane Allocator
    print("\nTraining CraneAllocator...")
    crane_model = CraneAllocator()
    crane_model.fit(X_train, y_train["crane_ratio"])

    test_preds_crane = crane_model.predict(X_test)
    crane_metrics = calculate_metrics(y_test["crane_ratio"], test_preds_crane)
    print(f"Crane Allocator Test Metrics: {crane_metrics}")

    # 5. Persist artifacts
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    turnaround_path = artifacts_dir / "turnaround_predictor.json"
    crane_path = artifacts_dir / "crane_allocator.json"
    engineer_meta_path = artifacts_dir / "feature_metadata.json"

    turnaround_model.save(turnaround_path)
    crane_model.save(crane_path)

    feature_metadata = {
        "opr_to_idx": engineer.opr_to_idx,
        "default_opr_idx": engineer.default_opr_idx,
        "feature_means": engineer.feature_means,
    }
    with open(engineer_meta_path, "w", encoding="utf-8") as f:
        json.dump(feature_metadata, f, indent=2)

    print(f"\nArtifacts successfully persisted to: {artifacts_dir}")
    print("=" * 60)

    return {
        "turnaround_metrics": turnaround_metrics,
        "crane_metrics": crane_metrics,
        "artifacts_dir": str(artifacts_dir),
    }


if __name__ == "__main__":
    run_training_pipeline()
