# Offline berth-duration experiment

This module estimates the hours from actual time of berthing (`ATB`) to actual departure (`ATD`) for a historical port visit. The prediction moment is **when actual berthing begins**. It is an offline experiment, not an approved operational model or a simulation input.

## Source and inputs

The read-only source is `data/processed/port_calls_cleaned.csv`, generated from the historical workbook. It contains 807 visits from 2019–2021. `ml.dataset` validates vessel name, both timestamps, and a strictly positive `ATD - ATB` duration. Invalid targets stop training with an error. No duration outlier is silently removed; the report flags unusually large durations using a descriptive IQR threshold.

Milestone 2B compares three feature sets: calendar date (ATB month and weekday), vessel name alone, and vessel plus calendar date. All are observable at actual berthing. The source hour is excluded because the workbook's timezone is unconfirmed. Operator and shipping-line codes are present in the CSV, but their availability at this prediction moment is not established, so they are not evaluated. No other CSV field has confirmed pre-operation availability. Departure, final moves, productivity, measured working/crane durations, and disruption fields are excluded to prevent outcome leakage. Unknown vessels and calendar categories are accepted by the encoder, but an accepted input is not necessarily a reliable prediction.

## Reproduce

From the repository root:

```powershell
python -m pip install -r ml/requirements.txt
python -m pytest ml/tests -q
python -m ml.experiment
python -m ml.disruption_audit
```

The experiment compares a historical median with Ridge regression and a small random forest for each feature set. It trains on 2019, selects by 2020 validation MAE, refits on eligible 2019–2020 visits, then tests the selected candidate and baseline once on 2021. Visits crossing a training cutoff are excluded from that period and counted, because their outcomes were not yet observed. Preprocessing is fitted inside each training pipeline. The report includes MAE, median and 90th-percentile absolute error, RMSE, signed-error quantiles, repeated/unseen vessel groups, and descriptive errors by month and duration outlier. The fixed random seed is 42.

`ml/artifacts/metadata.json` records results, feature and target definitions, training period, and dependency versions. `ml/artifacts/berth_duration_pipeline.joblib` contains fitted preprocessing and estimator together. Both are ignored by Git. Only load locally created, trusted joblib files. A saved model is **not** approved for deployment merely because training succeeded.

If installing into the repository's ignored `ml/.deps/` directory instead of a virtual environment, use `python -m pip install --target ml/.deps -r ml/requirements.txt` and set `PYTHONPATH=ml/.deps` for the test and training commands.

## Limits

The source timezone and external provenance remain unconfirmed. Many vessels have only one historical visit. The data ends in 2021, so present-day prediction reliability is unknown. The dataset has no validated delay or future-crane-failure labels. A historical duration is in real-world hours; C++ simulation `work_total` is in uncalibrated work units. Never substitute one for the other or use this prediction to change FCFS, SJF, Priority, resource allocation, or execution order.

## Milestone 2B findings

The 2020 validation comparison below was fixed before examining the 2021 test results. MAE values are hours. The unseen group means the vessel was absent from the 2019 training set.

| Feature set | Estimator | All visits MAE | Unseen-vessel MAE |
| --- | --- | ---: | ---: |
| None | Historical median | 9.20 | 7.83 |
| Calendar | Ridge | 9.22 | 6.93 |
| Calendar | Forest | 8.99 | 6.90 |
| Vessel | Ridge | 8.80 | 7.21 |
| Vessel | Forest | **6.80** | 7.38 |
| Vessel + calendar | Ridge | 8.58 | 6.94 |
| Vessel + calendar | Forest | 6.97 | 6.94 |

The vessel-only forest won by validation MAE. On the 2021 test, its overall MAE was **8.49 hours** versus **10.60** for the median baseline. For 139 repeat-vessel visits, its MAE was **5.95** versus **9.28** for baseline. For 84 unseen-vessel visits, its MAE was **12.70** versus **12.78** for baseline; that tiny difference does not support useful unseen-vessel personalization. Its test median absolute error was **6.02 hours**, 90th-percentile absolute error **15.79 hours**, and RMSE **16.43 hours**. The outlier threshold derived from 2019 training data is 86.09 hours. One test visit above it had absolute error **187.89 hours**; the other 222 visits had MAE **7.68 hours**. No 2020 validation visits exceeded that threshold. Per-month test MAE ranges from 4.18 to 15.99 hours, but monthly samples are small, so this does not establish a stable seasonal pattern. Vessel identity supplies most of the measured gain for repeat visits; adding calendar fields did not improve validation MAE. A previously unseen vessel has no learned vessel category, so the model falls back toward a generic prediction.

**Readiness:** Berth-to-departure duration remains the first defensible experimental target. This model is not approved for a Predictions tab yet. Show “prediction unavailable” when actual ATB is missing or invalid, the vessel is unseen, the saved model is unavailable or incompatible, or a present-day reliability claim would be required. The 2019–2021 source does not establish reliability for current operations. A future advisory could be considered only after a reviewed fallback, uncertainty display, and fresh validation.

## Disruption feasibility audit

`python -m ml.disruption_audit` computes these descriptive values from all 807 rows. Every field has zero missing and no negative values. Values are continuous, often nonzero and greater than one; interpreting nonzero as an event label would invent a definition. The names suggest possible operational causes or outcomes, but the source and schema do not define whether the numbers are durations, fractions, counts, or another measure.

| Exact CSV field | Zero / nonzero | Median | P90 | Maximum | Interpretation supported by source | Defensible target now? |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `QUAY CRANE BREAKDOWN` | 382 / 425 | 0.017 | 0.848 | 7.881 | Named crane-related disruption measure; no validated failure event or unit | No |
| `WAITING CONTAINER` | 267 / 540 | 0.188 | 1.668 | 9.780 | Named waiting measure; cause versus outcome and unit unknown | No |
| `WAITING INTERNAL TRUCK` | 484 / 323 | 0 | 0.361 | 4.964 | Named waiting measure; cause versus outcome and unit unknown | No |
| `GEAR BOX HANDLING` | 506 / 301 | 0 | 0.117 | 0.671 | Named handling measure; operational meaning and unit unknown | No |
| `BAD WEATHER` | 716 / 91 | 0 | 0.067 | 5.371 | Named weather-related measure; event timing and unit unknown | No |
| `SYSTEM FAILURE` | 746 / 61 | 0 | 0 | 2.059 | Named system-related measure; no validated failure event or unit | No |

The only currently defensible early features would be vessel identity and ATB calendar date, subject to the same limitations as duration prediction. To define an upcoming-disruption target, obtain the source data dictionary and units, whether zero means absence, event start times and observation windows, when each value was recorded, and which predictors were available *before* the event. External data such as weather forecasts or equipment health would require its own timestamped provenance. No disruption model or artificial labels are created here.
