"""Chronological, reproducible offline berth-duration experiment.

Run from the repository root: python -m ml.experiment
"""

import argparse
import hashlib
import json
import platform
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
import scipy
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from .dataset import CSV_PATH, TARGET, describe_target, load_visits
from .features import FEATURES, FEATURE_SETS, make_features

DEFAULT_OUTPUT = Path(__file__).parent / "artifacts"
SEED = 42


def chronological_splits(visits: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Hold out 2021; remove boundary crossings whose labels arrived too late."""
    train = visits[(visits.ATB.dt.year == 2019) & (visits.ATD < pd.Timestamp("2020-01-01"))]
    validation = visits[(visits.ATB.dt.year == 2020) & (visits.ATD < pd.Timestamp("2021-01-01"))]
    test = visits[visits.ATB.dt.year == 2021]
    excluded = visits[~visits.index.isin(train.index.union(validation.index).union(test.index))]
    if train.empty or validation.empty or test.empty:
        raise ValueError("Each chronological period needs at least one valid visit")
    return train, validation, test, {
        "training_rows": int(len(train)),
        "validation_rows": int(len(validation)),
        "test_rows": int(len(test)),
        "excluded_cutoff_crossing_rows": int(len(excluded)),
        "cutoff_exclusion_reason": "ATD was not observed before the next period began",
    }


def model(name: str, features: tuple[str, ...] = FEATURES) -> Pipeline:
    estimators = {
        "median": DummyRegressor(strategy="median"),
        "ridge": Ridge(alpha=100.0),
        "forest": RandomForestRegressor(
            n_estimators=150, min_samples_leaf=5, max_features=0.8,
            random_state=SEED, n_jobs=1,
        ),
    }
    if name not in estimators:
        raise ValueError(f"Unknown model: {name}")
    preprocessing = ColumnTransformer([
        ("categories", OneHotEncoder(handle_unknown="ignore"), list(features)),
    ])
    return Pipeline([("preprocess", preprocessing), ("regressor", estimators[name])])


def metrics(y: pd.Series, predicted: np.ndarray) -> dict:
    error = np.asarray(predicted) - np.asarray(y)
    absolute = np.abs(error)
    return {
        "rows": int(len(y)),
        "mae_hours": float(mean_absolute_error(y, predicted)),
        "median_absolute_error_hours": float(np.median(absolute)),
        "p90_absolute_error_hours": float(np.quantile(absolute, 0.9)),
        "rmse_hours": float(np.sqrt(mean_squared_error(y, predicted))),
        "signed_error_p10_p50_p90_hours": [float(v) for v in np.quantile(error, [0.1, 0.5, 0.9])],
        "underestimates": int((error < 0).sum()),
        "overestimates": int((error > 0).sum()),
    }


def evaluate(fitted: Pipeline, reference: pd.DataFrame, holdout: pd.DataFrame) -> dict:
    predictions = fitted.predict(make_features(holdout))
    known = holdout["Vessel Name"].isin(set(reference["Vessel Name"]))
    result = {"all": metrics(holdout[TARGET], predictions)}
    for label, mask in (("repeated_vessel", known), ("unseen_vessel", ~known)):
        if mask.any():
            result[label] = metrics(holdout.loc[mask, TARGET], predictions[np.asarray(mask)])
        else:
            result[label] = {"rows": 0, "note": "No visits in this group"}
    return result


def error_diagnostics(fitted: Pipeline, holdout: pd.DataFrame, outlier_threshold: float) -> dict:
    """Describe errors by month and observed-duration outlier, without refitting."""
    predicted = fitted.predict(make_features(holdout))
    absolute = np.abs(predicted - holdout[TARGET].to_numpy())
    outlier = holdout[TARGET].to_numpy() > outlier_threshold
    by_month = {}
    for month in sorted(holdout.ATB.dt.month.unique()):
        mask = holdout.ATB.dt.month.to_numpy() == month
        by_month[str(month)] = {"rows": int(mask.sum()), "mae_hours": float(absolute[mask].mean())}
    return {
        "duration_outliers": {"rows": int(outlier.sum()),
                              "mae_hours": float(absolute[outlier].mean()) if outlier.any() else None},
        "other_visits": {"rows": int((~outlier).sum()),
                         "mae_hours": float(absolute[~outlier].mean()) if (~outlier).any() else None},
        "by_atb_month": by_month,
    }


def run(path: Path = CSV_PATH, output: Path = DEFAULT_OUTPUT) -> dict:
    visits = load_visits(path)
    train, validation, test, split_report = chronological_splits(visits)
    validation_results = {}
    baseline_validation = model("median").fit(make_features(train), train[TARGET])
    validation_results["median"] = evaluate(baseline_validation, train, validation)
    # Candidate order is fixed before looking at the 2021 holdout. Keep the
    # original feature set as a direct point of comparison.
    for feature_set, features in FEATURE_SETS.items():
        for name in ("ridge", "forest"):
            key = f"{feature_set}_{name}"
            fitted = model(name, features).fit(make_features(train), train[TARGET])
            validation_results[key] = evaluate(fitted, train, validation)

    winner = min(validation_results, key=lambda key: validation_results[key]["all"]["mae_hours"])
    if winner == "median":
        winner_features, winner_estimator = FEATURES, "median"
    else:
        feature_set, winner_estimator = winner.rsplit("_", 1)
        winner_features = FEATURE_SETS[feature_set]
    pretest = pd.concat([train, validation]).sort_values("ATB")
    baseline = model("median").fit(make_features(pretest), pretest[TARGET])
    selected = model(winner_estimator, winner_features).fit(make_features(pretest), pretest[TARGET])
    target_report = describe_target(visits)
    diagnostic_outlier_threshold = describe_target(train)["far_outlier_threshold_hours"]
    report = {
        "status": "offline_experiment_only_not_approved_for_deployment",
        "target": "ATD minus ATB in hours; predicted at actual berthing",
        "features": list(winner_features),
        "feature_sets_compared": {key: list(value) for key, value in FEATURE_SETS.items()},
        "not_evaluated": {
            "operator_shipping_line": "Availability of per-visit codes at actual berthing is unconfirmed",
            "other_pre_operation_inputs": "No further source field has confirmed pre-operation availability",
        },
        "source_csv": str(path),
        "source_csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "dataset": target_report,
        "split": split_report,
        "selection_rule": "lowest 2020 validation MAE in fixed candidate order; no 2021 selection",
        "validation": validation_results,
        "selected_model": winner,
        "test": {
            "median": evaluate(baseline, pretest, test),
            "selected": evaluate(selected, pretest, test),
        },
        "diagnostics": {
            "outlier_threshold_from_2019_training_hours": diagnostic_outlier_threshold,
            "validation": error_diagnostics(baseline_validation if winner == "median" else
                                            model(winner_estimator, winner_features).fit(make_features(train), train[TARGET]),
                                            validation, diagnostic_outlier_threshold),
            "test": error_diagnostics(selected, test, diagnostic_outlier_threshold),
        },
        "training_period": "2019-01-01 through 2020-12-31, outcomes observed before 2021-01-01",
        "versions": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "scipy": scipy.__version__,
            "joblib": joblib.__version__,
        },
        "random_seed": SEED,
    }
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(selected, output / "berth_duration_pipeline.joblib")
    (output / "metadata.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.csv, args.output), indent=2))


if __name__ == "__main__":
    main()
