"""Read-only prediction API tests; no backend lifespan or database writes."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app, raise_server_exceptions=False)
URL = "/predictions/berth-duration"


def prediction(status="available", reason=None):
    return {
        "status": status,
        "vessel_name": "ALS APOLLO",
        "estimated_duration_hours": 20.59 if status == "available" else None,
        "units": "hours",
        "model_version": "vessel_forest:example123456",
        "training_period": "2019-01-01 through 2020-12-31",
        "predicted_at": "2026-11-14T00:00:00+00:00" if status == "available" else None,
        "prediction_moment": "actual berthing",
        "explanation": "Historical vessel-based estimate; actual duration may differ.",
        "reason": reason,
    }


def test_known_vessel_response_and_isolation():
    run_id_before = getattr(app.state, "current_run_id", None)
    with patch("backend.app.predictions._predict_duration", return_value=prediction()) as infer:
        response = client.post(URL, json={"vessel_name": " ALS APOLLO "})
    assert response.status_code == 200
    infer.assert_called_once_with("ALS APOLLO")
    body = response.json()
    assert body == {"designation": "experimental_advisory", **prediction()}
    assert getattr(app.state, "current_run_id", None) == run_id_before


def test_unseen_vessel_has_no_estimate():
    unavailable = prediction(status="unavailable", reason="unseen_vessel")
    with patch("backend.app.predictions._predict_duration", return_value=unavailable):
        response = client.post(URL, json={"vessel_name": "ALS APOLLO"})
    assert response.status_code == 200
    assert response.json()["reason"] == "unseen_vessel"
    assert response.json()["estimated_duration_hours"] is None


@pytest.mark.parametrize("payload", [
    {}, {"vessel_name": None}, {"vessel_name": 123},
    {"vessel_name": "  "}, {"vessel_name": "A", "work_total": 1}, [],
])
def test_invalid_request_is_422(payload):
    with patch("backend.app.predictions._predict_duration") as infer:
        response = client.post(URL, json=payload)
    assert response.status_code == 422
    infer.assert_not_called()


def test_missing_artifact_is_structured_unavailable():
    unavailable = prediction(status="unavailable", reason="missing_model_artifact")
    with patch("backend.app.predictions._predict_duration", return_value=unavailable):
        response = client.post(URL, json={"vessel_name": "ALS APOLLO"})
    assert response.status_code == 200
    assert response.json()["reason"] == "missing_model_artifact"
    assert response.json()["estimated_duration_hours"] is None


def test_missing_artifact_with_real_inference(tmp_path, monkeypatch):
    pytest.importorskip("ml.inference")
    monkeypatch.setenv("ML_ARTIFACT_DIR", str(tmp_path))
    response = client.post(URL, json={"vessel_name": "ALS APOLLO"})
    assert response.status_code == 200
    assert response.json()["reason"] == "missing_model_artifact"


def test_inference_failure_is_isolated():
    with patch("backend.app.predictions._predict_duration", side_effect=RuntimeError("broken")):
        response = client.post(URL, json={"vessel_name": "ALS APOLLO"})
    assert response.status_code == 200
    assert response.json()["reason"] == "prediction_failed"
    assert response.json()["estimated_duration_hours"] is None


def test_missing_ml_dependencies_is_isolated():
    with patch("backend.app.predictions._predict_duration", side_effect=ImportError("sklearn")):
        response = client.post(URL, json={"vessel_name": "ALS APOLLO"})
    assert response.status_code == 200
    assert response.json()["reason"] == "ml_dependencies_unavailable"


def test_existing_routes_still_registered():
    paths = app.openapi()["paths"]
    assert URL in paths
    for path in ("/health", "/inventory", "/simulation/init", "/simulation/step"):
        assert path in paths
