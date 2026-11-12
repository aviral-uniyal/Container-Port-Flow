# Offline berth-duration experiment

This module estimates the hours from actual time of berthing (`ATB`) to actual departure (`ATD`) for a historical port visit. The prediction moment is **when actual berthing begins**. It is an offline experiment, not an approved operational model or a simulation input.

## Source and inputs

The read-only source is `data/processed/port_calls_cleaned.csv`, generated from the historical workbook. It contains 807 visits from 2019–2021. `ml.dataset` validates vessel name, both timestamps, and a strictly positive `ATD - ATB` duration. Invalid targets stop training with an error. No duration outlier is silently removed; the report flags unusually large durations using a descriptive IQR threshold.

The feature vector contains vessel name, month of ATB, and weekday of ATB. These are known at actual berthing. Month and weekday use the date, not the source hour, because the workbook's timezone is unconfirmed. Operator and shipping line are omitted until their availability at the prediction moment is confirmed. Departure, final moves, productivity, measured working/crane durations, and disruption fields are excluded to prevent outcome leakage. Unknown vessels and calendar categories are accepted by the encoder.

## Reproduce

From the repository root:

```powershell
python -m pip install -r ml/requirements.txt
python -m pytest ml/tests -q
python -m ml.experiment
```

The experiment compares a historical median, Ridge regression, and a small random forest. It trains on 2019, selects by 2020 validation MAE, refits on eligible 2019–2020 visits, then tests once on 2021. Visits crossing a training cutoff are excluded from that period and counted, because their outcomes were not yet observed. Preprocessing is fitted inside each training pipeline. The report includes MAE, median and 90th-percentile absolute error, RMSE, signed-error quantiles, and repeated/unseen vessel groups. The fixed random seed is 42.

`ml/artifacts/metadata.json` records results, feature and target definitions, training period, and dependency versions. `ml/artifacts/berth_duration_pipeline.joblib` contains fitted preprocessing and estimator together. Both are ignored by Git. Only load locally created, trusted joblib files. A saved model is **not** approved for deployment merely because training succeeded.

If installing into the repository's ignored `ml/.deps/` directory instead of a virtual environment, use `python -m pip install --target ml/.deps -r ml/requirements.txt` and set `PYTHONPATH=ml/.deps` for the test and training commands.

## Limits

The source timezone and external provenance remain unconfirmed. Many vessels have only one historical visit. The data ends in 2021, so present-day prediction reliability is unknown. The dataset has no validated delay or future-crane-failure labels. A historical duration is in real-world hours; C++ simulation `work_total` is in uncalibrated work units. Never substitute one for the other or use this prediction to change FCFS, SJF, Priority, resource allocation, or execution order.

The first 2021 test showed the selected forest was worse than the median baseline for vessels absent from training. Do not serve its estimate for unseen vessels without a separately evaluated fallback or further model work.
