import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
import pytest

from ml.dataset import TARGET, load_visits
from ml.experiment import chronological_splits, model, run
from ml.features import FEATURES, FEATURE_SETS, make_features
from ml.inference import predict_duration
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


def test_vessel_only_features_do_not_need_atb():
    features = make_features(pd.DataFrame([{"Vessel Name": " A "}]), FEATURE_SETS["vessel"])
    assert features.to_dict("records") == [{"vessel": "A"}]


def test_feature_set_pipelines_accept_unseen_vessel():
    train = pd.DataFrame([
        {"Vessel Name": "A", "ATB": "2019-01-01 00:00:00"},
        {"Vessel Name": "B", "ATB": "2019-02-02 00:00:00"},
    ])
    unseen = pd.DataFrame([{"Vessel Name": "NEW", "ATB": "2021-03-03 00:00:00"}])
    for features in FEATURE_SETS.values():
        fitted = model("forest", features).fit(make_features(train, features), [10.0, 20.0])
        assert len(fitted.predict(make_features(unseen, features))) == 1
        assert fitted.named_steps["preprocess"].transformers[0][2] == list(features)


def test_disruption_audit_is_descriptive_only(tmp_path):
    row = {"Vessel Name": "A", "ATB": "2019-01-01 00:00:00", "ATD": "2019-01-02 00:00:00"}
    row.update({field: 0 for field in FIELDS})
    row["BAD WEATHER"] = 2.5
    result = audit(csv_file(tmp_path, [row]))
    assert result["BAD WEATHER"]["nonzero"] == 1
    assert result["BAD WEATHER"]["max"] == 2.5
    assert result["BAD WEATHER"]["units_documented"] is False


@pytest.fixture(scope="module")
def trained_artifacts(tmp_path_factory):
    output = tmp_path_factory.mktemp("offline_model")
    report = run(output=output)
    assert report["selected_model"] == "vessel_forest"
    return output


@pytest.fixture
def artifact_copy(trained_artifacts, tmp_path):
    for filename in ("metadata.json", "berth_duration_pipeline.joblib"):
        shutil.copy2(trained_artifacts / filename, tmp_path / filename)
    return tmp_path


def test_known_vessel_result_matches_saved_pipeline(trained_artifacts):
    pipeline = joblib.load(trained_artifacts / "berth_duration_pipeline.joblib")
    vessel = str(pipeline.named_steps["preprocess"].transformers_[0][1].categories_[0][0])
    expected = float(pipeline.predict(make_features(
        pd.DataFrame([{"Vessel Name": vessel}]), FEATURE_SETS["vessel"]
    ))[0])
    result = predict_duration(vessel, trained_artifacts)
    assert result["status"] == "available"
    assert result["vessel_name"] == vessel
    assert result["estimated_duration_hours"] == pytest.approx(expected)
    assert result["units"] == "hours"
    assert result["prediction_moment"] == "actual berthing"
    assert result["model_version"].startswith("vessel_forest:")
    assert "2020" in result["training_period"]
    assert datetime.fromisoformat(result["predicted_at"]).tzinfo is not None
    assert result["reason"] is None
    assert "Historical" in result["explanation"]


def test_unseen_vessel_is_unavailable(trained_artifacts):
    result = predict_duration("A VESSEL THAT DOES NOT EXIST", trained_artifacts)
    assert result["status"] == "unavailable"
    assert result["reason"] == "unseen_vessel"
    assert result["estimated_duration_hours"] is None
    assert result["predicted_at"] is None


@pytest.mark.parametrize("vessel,reason", [
    (None, "missing_vessel"), ("", "missing_vessel"), ("  ", "missing_vessel"),
    (42, "invalid_vessel"),
])
def test_missing_or_invalid_identity(vessel, reason, trained_artifacts):
    result = predict_duration(vessel, trained_artifacts)
    assert result["status"] == "unavailable"
    assert result["reason"] == reason


def test_missing_artifact(tmp_path):
    assert predict_duration("A", tmp_path)["reason"] == "missing_model_artifact"


def test_invalid_artifact_location():
    assert predict_duration("A", None)["reason"] == "invalid_artifact_location"


def test_invalid_metadata(artifact_copy):
    (artifact_copy / "metadata.json").write_text("not json", encoding="utf-8")
    assert predict_duration("A", artifact_copy)["reason"] == "invalid_model_metadata"


def test_incompatible_feature_schema(artifact_copy):
    path = artifact_copy / "metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["features"] = ["vessel", "month"]
    path.write_text(json.dumps(metadata), encoding="utf-8")
    assert predict_duration("A", artifact_copy)["reason"] == "incompatible_feature_schema"


def test_artifact_checksum_is_verified(artifact_copy):
    path = artifact_copy / "metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["model_sha256"] = "0" * 64
    metadata["model_version"] = "vessel_forest:" + "0" * 12
    path.write_text(json.dumps(metadata), encoding="utf-8")
    assert predict_duration("A", artifact_copy)["reason"] == "model_artifact_mismatch"


def test_model_loading_failure(artifact_copy, monkeypatch):
    def broken_load(_path):
        raise OSError("load failed")
    monkeypatch.setattr("ml.inference.joblib.load", broken_load)
    assert predict_duration("A", artifact_copy)["reason"] == "model_load_failed"


def test_prediction_failure(artifact_copy, monkeypatch):
    def broken_features(_inputs, _schema):
        raise ValueError("prediction failed")
    monkeypatch.setattr("ml.inference.make_features", broken_features)
    pipeline = joblib.load(artifact_copy / "berth_duration_pipeline.joblib")
    vessel = str(pipeline.named_steps["preprocess"].transformers_[0][1].categories_[0][0])
    assert predict_duration(vessel, artifact_copy)["reason"] == "prediction_failed"


def test_reproduced_artifact_metadata_matches_file(trained_artifacts):
    metadata = json.loads((trained_artifacts / "metadata.json").read_text(encoding="utf-8"))
    artifact = trained_artifacts / "berth_duration_pipeline.joblib"
    assert metadata["features"] == ["vessel"]
    assert metadata["model_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
