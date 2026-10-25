# ML development handoff

This is a planning guide for the teammate taking over the ML phase of Container-Port-Flow. The current application is an OS and DBMS demonstration; it has **no trained model, prediction API, ML-driven scheduling, or approved ML target**. Read [the data reference](ml_data_handoff.md) for the field dictionary, cleaning details, database mapping, and known data limitations, and [the OS demonstration guide](os_demonstration.md) for a short walkthrough of current behavior.

## Project and implemented architecture

Container-Port-Flow demonstrates scheduling vessel jobs against limited terminal resources, crane disruptions, and persistence of simulation activity. The React/Vite dashboard provides simulation controls and a separate Inventory view. FastAPI validates requests, owns the C++ process and automatic runner, and writes simulation state to PostgreSQL. The C++14 `PortDispatcher` makes the live scheduling and resource decisions. PostgreSQL also holds the cleaned historical ship and port-call records. ML is a future, separately designed layer that may supply predictions or decision inputs; the non-ML path already works without it.

| Layer | Current implementation and entry points |
| --- | --- |
| React | [`frontend/src/App.jsx`](../frontend/src/App.jsx) holds simulation, execution, inventory, and tab state; [`frontend/src/api.js`](../frontend/src/api.js) calls the HTTP endpoints. `JobsTable`, `CraneRegistry`, and `DatabaseInventory` render the existing results. The Dashboard and Inventory tabs keep their components mounted; polling continues while Inventory is visible. |
| FastAPI | [`backend/app/router.py`](../backend/app/router.py) implements `/health`, `/inventory`, and `/simulation/*`; [`models.py`](../backend/app/models.py) validates request bodies. [`main.py`](../backend/app/main.py) starts the bridge, execution controller, and database pool. |
| C++ bridge and engine | [`cpp_bridge.py`](../backend/app/cpp_bridge.py) exchanges one JSON Line per command/response with persistent [`port_json_iface.cpp`](../os-simulation/src/port_json_iface.cpp). [`port_dispatcher.cpp`](../os-simulation/src/port_dispatcher.cpp) owns the live queue, clock, jobs, `ResourceManager`, `CraneRegistry`, and `DisruptionManager`. |
| PostgreSQL | [`database.py`](../backend/app/database.py) reads inventory and persists runs, jobs, assignments, and disruptions using the [core schema](../database/schema/001_core_schema.sql) and [simulation migrations](../database/migrations/002_simulation_schema.sql). The [ship link](../database/migrations/003_simulation_job_ship.sql) and [dispatch sequence](../database/migrations/004_simulation_job_dispatch_sequence.sql) are later migrations. |

The separate [`scheduler.cpp`](../os-simulation/src/scheduler.cpp) and [`main.cpp`](../os-simulation/src/main.cpp) demonstrate textbook FCFS, non-preemptive SJF, and Priority results. Their results are **not** the live dispatcher implementation. [`deadlock_manager.cpp`](../os-simulation/src/deadlock_manager.cpp) and its tests demonstrate deadlock detection and Banker's Algorithm; neither is linked into live `PortDispatcher` allocation.

## Live simulation and persistence

1. `POST /simulation/init` creates a database run and initializes the C++ dispatcher with capacities, FCFS/SJF/Priority, and optional Batch Mode. `POST /simulation/cranes` registers an existing database crane up to the chosen capacity. `POST /simulation/jobs` submits a job associated with a selected `ship_id` and records its simulated arrival, work, priority, and resource request.
2. The live queue uses arrival eligibility (`arrival_time <= simulation_time`). FCFS favors earlier arrival then submission order; SJF favors shorter remaining work among eligible jobs; Priority favors the lowest priority number. Interrupted work gets precedence. An already active job is not preempted. The dispatcher checks aggregate berth/crane/truck availability and available registered cranes before allocating. Several active jobs can advance one work unit in the same step if resources permit.
3. `POST /simulation/dispatch` drains jobs that fit in Batch Mode. `POST /simulation/step` advances one simulated tick; `POST /simulation/run` starts or resumes the backend's automatic step loop, and `/simulation/pause` waits for an in-flight step to finish. Crane failure may interrupt work; recovery can resume assignment. Completion releases aggregate resources and active crane assignments. The execution controller in [`execution.py`](../backend/app/execution.py) serializes C++ command, snapshot, and PostgreSQL reconciliation using its execution lock.
4. `GET /simulation/snapshot` returns the C++ state, including current simulated time, jobs, cranes, and available resources. `GET /simulation/execution` returns runner status. The dashboard's Refresh fetches a snapshot and PostgreSQL inventory; automatic Run polling continues on either tab. `POST /simulation/reset` stops execution, closes the current run, clears the active C++ simulation, and releases active allocations while preserving historical rows and physical inventory. Finishing all currently submitted jobs leaves the run open for another submission until Reset or reinitialization.

PostgreSQL stores `simulation_runs`, `simulation_jobs`, `crane_assignments`, and `disruption_events`. `simulation_jobs.dispatch_sequence` is the immutable **first dispatch order**, not a timestamp. Database submission/start/completion and assignment/event times are wall-clock times. The C++ `simulation_time` and per-job simulated start/finish ticks are not persisted. The Jobs Table's stable display order is a frontend preview established on submission; it is not the authoritative dispatcher queue.

## Data available for ML planning

- **Historical source:** [`data/raw/18 months Historical Data.xlsx`](../data/raw/18%20months%20Historical%20Data.xlsx), cleaned by [`scripts/clean_workbook.py`](../scripts/clean_workbook.py) into [`data/processed/port_calls_cleaned.csv`](../data/processed/port_calls_cleaned.csv). The [cleaning report](../data/processed/cleaning_report.txt) records 810 populated source rows, three dropped rows, and **807 cleaned rows × 23 columns** covering **179 vessel names**. Despite the filename, visit timestamps span January 2019 through December 2021. The existing data reference records source and cleaned-file hashes for reproducibility.
- **Historical fields:** vessel name, operator/line, ATB/ATD, month/week, TEUs, moves, operating durations, productivity, crane measures, and six disruption-related fields. `ships.ship_id` identifies a vessel; `port_calls.port_call_id` identifies one visit and links to its ship. `source_no` repeats and is not a visit key. The source provides no berth assignment, so historical `port_calls.berth_id` is null.
- **Simulation data:** run policy/capacities; job simulated arrival/work/priority/requested and allocated resources, status, optional ship and port-call links, and first dispatch order; crane assignment/release history and failure/recovery events. These are application scenarios, not observed port calls. The database inventory contains one terminal, six berths, and C1–C12 cranes in the verified demonstration dataset; check current inventory before using counts in analysis.
- **Join limit:** submitted jobs select a `ship_id`, but the documented database inspection found **no populated `simulation_jobs.port_call_id` values** and many legacy jobs without `ship_id`. A ship may have several visits, so joining a simulation job to a historical port call by vessel alone is ambiguous. Do not treat such a join as ground truth.

The workbook timestamps have no timezone; the loader requires an explicit `PORT_CALLS_TIMEZONE` to store `TIMESTAMPTZ`. The prior inspection found that rendering stored times in `Asia/Jakarta` matches the CSV, but that does **not** prove the source timezone. `Week (ATD)` is missing in 806/807 cleaned rows and BCH in 71; several outliers and three zero-TEU visits remain. The units/timing of disruption fields are unverified. See the [data reference](ml_data_handoff.md) before selecting any feature. In particular, measured durations, productivity, final moves, departure time, and disruption outcomes can leak a target if predicting earlier in a visit.

There is no project synthetic-data generator or synthetic training label in the inspected scripts. Existing manually submitted simulations and test fixtures do not constitute validated training examples. One legacy row, `simulation_job_id=1` in run 2, is marked `COMPLETED` with `work_total=10`, `work_completed=0`, and `work_remaining=10`; exclude it from simulation-derived analysis pending review. Do not infer simulated turnaround or waiting time by subtracting backend wall-clock timestamps.

## Possible integration points, not requirements

| Possible point | Current behavior | Decision needed before changing it |
| --- | --- | --- |
| Work or delay estimate | `JobSubmitRequest.work_total` and `PortJob.work_total` are supplied as simulation work units; the live SJF queue uses remaining work. | Choose an observable prediction target and calibrated mapping to simulation units, if any. Keep the current supplied-work path available. |
| Priority or queue order | The user supplies `priority`; `PortDispatcher::queue_precedes` applies FCFS/SJF/Priority rules. | Decide whether a prediction should inform priority or merely be displayed. Any queue change needs explicit policy semantics, timing, and deterministic fallback; do not silently replace existing algorithms. |
| Resource allocation | `ResourceManager::can_allocate` and `CraneRegistry` gate assignments against capacities and crane availability. | Decide whether predictions are advisory or affect requests. Preserve capacity, failure, and release invariants; a model cannot create resources. |
| Operator information | FastAPI returns snapshots/execution status; React shows jobs, resources, feedback, and inventory. | If a prediction is useful to users, define its unit, provenance, confidence/uncertainty, update timing, and a small display/API contract before editing shared files. |

A small, explainable first experiment is preferable to changing multiple scheduling and allocation paths at once. None of these integrations is approved or implemented by this document.

## Decisions to agree on before implementation

1. **Target and observation time:** What outcome is useful, and at what exact point must a prediction be available? Which features genuinely exist then? Historical `ATD - ATB` is only a *candidate* observed duration, not a chosen label.
2. **Training population and labels:** Use historical visits, reviewed simulation runs, or a clearly separated comparison? Verify provenance and outcome quality; do not mix real durations, simulated work units, and backend wall time. Define whether synthetic data is needed before generating any.
3. **Features and missing data:** Confirm source timezone, field meanings/units, TEU/move availability, treatment of missing week/BCH and outliers, and how vessel/operator identifiers are encoded. Avoid using final outcome fields as early predictors.
4. **Evaluation and reproducibility:** Select a time-aware train/validation/test split; account for repeated vessels; fit preprocessing only on training folds. Record source/CSV hashes, cleaning code, chosen assumptions, dependencies, random seeds, metrics, and baseline comparison.
5. **Application effect and fallback:** Is ML advisory, a scheduling input, or a resource input? Specify API ownership, failure behavior, and how users can continue the existing FCFS/SJF/Priority simulation when predictions are unavailable. Agree on any backend/frontend contract with the owners of those shared files.

## Working in this repository

| Area | Where to start |
| --- | --- |
| Data and database | `docs/ml_data_handoff.md`, `data/raw/`, `data/processed/`, `scripts/`, `database/schema/`, `database/migrations/` |
| Live OS behavior | `os-simulation/src/port_dispatcher.cpp`, `port_json_iface.cpp`, `resource_manager.cpp`, `crane_registry.cpp`, `disruption_manager.cpp`, and `os-simulation/tests/` |
| HTTP and UI | `backend/app/router.py`, `execution.py`, `database.py`, `backend/tests/`, `frontend/src/App.jsx`, `api.js`, `components/`, and `frontend/tests/` |

The Windows launcher is [`start_all.bat`](../start_all.bat); [`check_database.bat`](../check_database.bat) checks the configured database. Copy [`.env.local.example`](../.env.local.example) to the ignored `.env.local` and supply the required `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD` locally. Never commit credentials. The launcher expects PostgreSQL, Python packages from [`backend/requirements.txt`](../backend/requirements.txt), Node/npm with frontend dependencies from [`frontend/package.json`](../frontend/package.json), and the C++ JSON executable. [`backend/app/config.py`](../backend/app/config.py) defaults to `os-simulation/port_json_iface.exe` or accepts `CPP_EXE_PATH`; set that variable to the compiled executable if it is elsewhere. Building C++ requires a C++14 compiler; [`os-simulation/CMakeLists.txt`](../os-simulation/CMakeLists.txt) defines its targets. Data cleaning additionally uses pandas/openpyxl, which are not pinned in backend requirements; the import script needs an explicitly chosen `PORT_CALLS_TIMEZONE` and should not be rerun against populated tables casually.

Existing verification commands (from the repository root unless noted):

```text
python -m pip install -r backend/requirements.txt
python -m pytest backend/tests -q
cmake -S os-simulation -B os-simulation/build
cmake --build os-simulation/build
cd os-simulation/build
ctest --output-on-failure
cd ../..
cd frontend
npm install
node --test
npm run build
```

Use mocks or an isolated database for new ML integration tests; the configured PostgreSQL database holds demonstration history. Verify the non-ML FCFS/SJF/Priority path, submission, Step/Run/Pause, Dispatch All, crane failure/recovery, Reset, persistence, and Dashboard/Inventory views after any integration. The existing [OS demonstration](os_demonstration.md) describes scenarios. Frontend test/build and backend/C++ tests should pass before merging ML work.

## Git collaboration

`main` is the stable branch; `dev` is the active development branch. Pull the latest `dev` and inspect current code before starting. Make incremental, reviewed commits with meaningful messages and run the affected tests. Do not push ML work to `main`. Coordinate concurrent edits to shared backend or frontend files with the other teammate before making them.
