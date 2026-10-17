# Container-Port-Flow: Machine Learning Subsystem
Author - Nischay Singh
The **ML Subsystem** delivers predictive intelligence and decision support for the Container-Port-Flow Autonomous Port & Terminal Resource Management System.

---

## 🎯 Objectives

1. **Vessel Turnaround Time Prediction (`turnaround_predictor`)**:
   - Forecasts total `Berth Time` and `Berth Working Time` required for an incoming vessel call.
   - Enables optimal berth scheduling and minimizes demurrage costs.

2. **Crane Allocation & Demand Estimator (`crane_allocator`)**:
   - Predicts optimal `Crane Ratio` (number of quay cranes assigned per vessel) based on container workload (`Total Teus`, `Moves`) and target productivity rates.

3. **Disruption Risk Scoring**:
   - Estimates probabilities of quay crane breakdown and truck waiting bottlenecks.

---

## 🏗️ Architecture

```
ml/
├── config.py                 # Central ML configuration, hyperparams, and file paths
├── requirements.txt          # Python dependencies
├── datasets/
│   ├── __init__.py
│   └── loader.py             # Historical port calls data ingestion & cleaning
├── features/
│   ├── __init__.py
│   └── engineering.py        # Feature transformation, temporal extraction, encoding
├── models/
│   ├── __init__.py
│   ├── base.py               # Abstract base model contract
│   ├── turnaround_predictor.py# Turnaround time regression model
│   └── crane_allocator.py    # Crane demand and allocation estimator
├── pipelines/
│   ├── __init__.py
│   ├── train.py              # Model training, cross-validation & evaluation
│   └── inference.py          # Real-time inference interface for simulation & API
└── tests/
    ├── __init__.py
    └── test_ml_pipeline.py   # Unit and integration tests
```

---

## 📊 Dataset Reference

The models train on historical vessel calls located at:
`data/processed/port_calls_cleaned.csv`

Key attributes used:
- **Workload**: `Total Teus`, `Moves`
- **Carrier / Line**: `OPR`, `Shipping Line`
- **Productivity**: `Crane Ratio`, `Gross Crane Time`, `BPR (mph)`
- **Targets**: `Berth Time` (hours), `Berth Working Time` (hours)
