from pathlib import Path

import joblib
import pandas as pd
import pytest

from ml.dataset import TARGET, load_visits
from ml.experiment import chronological_splits, model
from ml.features import FEATURES, FEATURE_SETS, make_features
from ml.inference import predict_hours
from ml.disruption_audit import FIELDS, audit


def csv_file(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "visits.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_target_uses_elapsed_hours(tmp_path):
    visits = load_visits(csv_file(tmp_path, [
        {"Vessel Name": "A", "ATB": "2019-01-01 23:00:00", "ATD": "2019-01-02 01:30:00"},
    ]))
    assert visits[TARGET].tolist() == [2.5]


@pytest.mark.parametrize("atb,atd", [
    ("", "2019-01-02 01:00:00"),
    ("bad", "2019-01-02 01:00:00"),
    ("2019-01-02 01:00:00", ""),
    ("2019-01-02 01:00:00", "2019-01-01 23:00:00"),
])
def test_invalid_or_missing_target_fails(tmp_path, atb, atd):
    path = csv_file(tmp_path, [{"Vessel Name": "A", "ATB": atb, "ATD": atd}])
    with pytest.raises(ValueError):
        load_visits(path)


def test_missing_required_column_fails(tmp_path):
    path = csv_file(tmp_path, [{"Vessel Name": "A", "ATB": "2019-01-01 00:00:00"}])
    with pytest.raises(ValueError, match="Missing required columns"):
        load_visits(path)


def test_features_ignore_future_outcomes_and_hour():
    row = {"Vessel Name": "A", "ATB": "2019-01-01 02:00:00", "ATD": "2019-01-02 00:00:00"}
    a = make_features(pd.DataFrame([row]))
    b = make_features(pd.DataFrame([{**row, "ATB": "2019-01-01 23:00:00", "ATD": "2099-01-01 00:00:00",
                                     "Moves": 99999, "BAD WEATHER": 100}]))
    pd.testing.assert_frame_equal(a, b)
    assert tuple(a.columns) == FEATURES


def test_cutoff_excludes_labels_observed_after_training_period():
    visits = pd.DataFrame([
        {"Vessel Name": "A", "ATB": pd.Timestamp("2019-01-01"), "ATD": pd.Timestamp("2019-01-02"), TARGET: 24},
        {"Vessel Name": "A", "ATB": pd.Timestamp("2019-12-31"), "ATD": pd.Timestamp("2020-01-02"), TARGET: 48},
        {"Vessel Name": "B", "ATB": pd.Timestamp("2020-01-01"), "ATD": pd.Timestamp("2020-01-02"), TARGET: 24},
        {"Vessel Name": "B", "ATB": pd.Timestamp("2021-01-01"), "ATD": pd.Timestamp("2021-01-02"), TARGET: 24},
    ])
    train, validation, test, info = chronological_splits(visits)
    assert (len(train), len(validation), len(test)) == (1, 1, 1)
    assert info["excluded_cutoff_crossing_rows"] == 1


def test_unknown_category_and_persisted_inference_match(tmp_path):
    train = pd.DataFrame([
        {"Vessel Name": "A", "ATB": "2019-01-01 00:00:00"},
        {"Vessel Name": "B", "ATB": "2019-02-02 00:00:00"},
    ])
    fitted = model("ridge").fit(make_features(train), [10.0, 20.0])
    candidate = {"Vessel Name": "NEW", "ATB": "2021-03-03 00:00:00"}
    expected = float(fitted.predict(make_features(pd.DataFrame([candidate])))[0])
    path = tmp_path / "model.joblib"
    joblib.dump(fitted, path)
    assert predict_hours(path, candidate["Vessel Name"], candidate["ATB"]) == pytest.approx(expected)


def test_feature_set_pipelines_accept_unseen_vessel():
    train = pd.DataFrame([
        {"Vessel Name": "A", "ATB": "2019-01-01 00:00:00"},
        {"Vessel Name": "B", "ATB": "2019-02-02 00:00:00"},
    ])
    unseen = pd.DataFrame([{"Vessel Name": "NEW", "ATB": "2021-03-03 00:00:00"}])
    for features in FEATURE_SETS.values():
        fitted = model("forest", features).fit(make_features(train), [10.0, 20.0])
        assert len(fitted.predict(make_features(unseen))) == 1
        assert fitted.named_steps["preprocess"].transformers[0][2] == list(features)


def test_disruption_audit_is_descriptive_only(tmp_path):
    row = {"Vessel Name": "A", "ATB": "2019-01-01 00:00:00", "ATD": "2019-01-02 00:00:00"}
    row.update({field: 0 for field in FIELDS})
    row["BAD WEATHER"] = 2.5
    result = audit(csv_file(tmp_path, [row]))
    assert result["BAD WEATHER"]["nonzero"] == 1
    assert result["BAD WEATHER"]["max"] == 2.5
    assert result["BAD WEATHER"]["units_documented"] is False
