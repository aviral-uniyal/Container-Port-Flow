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
from .features import FEATURES, make_features

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


def model(name: str) -> Pipeline:
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
        ("categories", OneHotEncoder(handle_unknown="ignore"), list(FEATURES)),
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


def run(path: Path = CSV_PATH, output: Path = DEFAULT_OUTPUT) -> dict:
    visits = load_visits(path)
    train, validation, test, split_report = chronological_splits(visits)
    validation_results = {}
    for name in ("median", "ridge", "forest"):
        fitted = model(name).fit(make_features(train), train[TARGET])
        validation_results[name] = evaluate(fitted, train, validation)

    winner = min(validation_results, key=lambda name: validation_results[name]["all"]["mae_hours"])
    pretest = pd.concat([train, validation]).sort_values("ATB")
    baseline = model("median").fit(make_features(pretest), pretest[TARGET])
    selected = model(winner).fit(make_features(pretest), pretest[TARGET])
    report = {
        "status": "offline_experiment_only_not_approved_for_deployment",
        "target": "ATD minus ATB in hours; predicted at actual berthing",
        "features": list(FEATURES),
        "source_csv": str(path),
        "source_csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "dataset": describe_target(visits),
        "split": split_report,
        "selection_rule": "lowest validation MAE; ties keep median, then ridge, then forest",
        "validation": validation_results,
        "selected_model": winner,
        "test": {
            "median": evaluate(baseline, pretest, test),
            "selected": evaluate(selected, pretest, test),
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
