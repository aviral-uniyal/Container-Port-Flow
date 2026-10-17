"""
ml/tests/test_ml_pipeline.py
============================
Unit test suite verifying dataset loading, feature engineering, models, and inference.
"""

import sys
import unittest
import numpy as np
import pandas as pd

from ml.datasets.loader import PortCallsDatasetLoader
from ml.features.engineering import FeatureEngineer
from ml.models.base import calculate_metrics
from ml.models.turnaround_predictor import TurnaroundPredictor
from ml.models.crane_allocator import CraneAllocator
from ml.pipelines.inference import MLInferenceService
from ml.pipelines.train import run_training_pipeline


class TestMLPipeline(unittest.TestCase):
    """Integration and unit tests for Container-Port-Flow ML components."""

    @classmethod
    def setUpClass(cls):
        cls.loader = PortCallsDatasetLoader()
        cls.raw_df = cls.loader.load_data()

    def test_01_dataset_loader_integrity(self):
        """Verifies dataset rows, columns, and reproducible train-test split."""
        self.assertGreater(len(self.raw_df), 100)
        self.assertIn("Moves", self.raw_df.columns)
        self.assertIn("Berth Time", self.raw_df.columns)

        train_df, test_df = self.loader.train_test_split(test_size=0.25)
        self.assertEqual(len(train_df) + len(test_df), len(self.raw_df))
        self.assertAlmostEqual(len(test_df) / len(self.raw_df), 0.25, delta=0.02)

    def test_02_feature_engineering_dimensions(self):
        """Verifies transformation pipeline output shapes and column consistency."""
        engineer = FeatureEngineer()
        X = engineer.fit_transform(self.raw_df)
        self.assertEqual(len(X), len(self.raw_df))
        self.assertIn("total_teus", X.columns)
        self.assertIn("moves", X.columns)
        self.assertIn("teus_per_move", X.columns)

        # Ensure no NaN values in engineered features
        self.assertEqual(X.isna().sum().sum(), 0)

    def test_03_turnaround_predictor_training(self):
        """Verifies turnaround predictor training and metric calculations."""
        engineer = FeatureEngineer()
        X = engineer.fit_transform(self.raw_df)
        y = engineer.extract_targets(self.raw_df)["turnaround_time"]

        model = TurnaroundPredictor(l2_penalty=0.5)
        model.fit(X, y)
        preds = model.predict(X)

        self.assertEqual(len(preds), len(y))
        # Turnaround time must be non-negative
        self.assertTrue(np.all(preds >= 1.0))

        metrics = calculate_metrics(y, preds)
        self.assertIn("mae", metrics)
        self.assertIn("r2", metrics)
        self.assertGreater(metrics["r2"], 0.4)

    def test_04_crane_allocator_heuristics(self):
        """Verifies crane recommendation logic."""
        allocator = CraneAllocator(min_cranes=1, max_cranes=5)
        allocator.is_trained = True
        allocator.moves_thresholds = {"q25": 200, "q50": 600, "q75": 1200}

        rec = allocator.recommend_cranes_for_target(moves=800, target_hours=10)
        self.assertGreaterEqual(rec, 1)
        self.assertLessEqual(rec, 5)

    def test_05_train_and_inference_pipeline(self):
        """Runs the complete training pipeline and checks inference service response."""
        res = run_training_pipeline()
        self.assertIn("turnaround_metrics", res)

        service = MLInferenceService()
        output = service.predict_vessel_call(
            vessel_name="TEST_VESSEL_EXPRESS",
            total_teus=1200.0,
            moves=750,
            operator="CMA",
            month="Januari",
            target_hours=14.0
        )
        self.assertEqual(output["vessel_name"], "TEST_VESSEL_EXPRESS")
        self.assertGreater(output["estimated_berth_hours"], 0.0)
        self.assertTrue(output["is_ml_backed"])


if __name__ == "__main__":
    unittest.main()
