# Dataset and ML handoff reference

Verified on 25 October 2026 against the files and a read-only query of the configured PostgreSQL database. This describes available data; it does not define an ML task or training set.

## Historical dataset

- Source: [`data/raw/18 months Historical Data.xlsx`](../data/raw/18%20months%20Historical%20Data.xlsx), one worksheet (`Sheet2`). The filename says “18 months,” but the visit timestamps cover **all 36 calendar months from January 2019 through December 2021**. Earliest ATB: `2019-01-01 02:00:00`; latest ATD: `2021-12-30 06:00:00` in the workbook's timezone-free notation. The workbook's external provenance is not documented here.
- Processed data: [`data/processed/port_calls_cleaned.csv`](../data/processed/port_calls_cleaned.csv), **807 rows × 23 columns**, representing **179 distinct vessel names**. The workbook has 810 populated rows; three rows with no vessel name, ATB, or ATD were removed. The CSV has the same timestamp span.
- Reproduction inputs: [`scripts/clean_workbook.py`](../scripts/clean_workbook.py) and its [`cleaning_report.txt`](../data/processed/cleaning_report.txt) (generated 12 October 2026). The script uses pandas and openpyxl; dependency versions are not pinned for this cleaning step. Do not treat the filename as a date-range definition.
- SHA-256 of the source workbook: `84EC096EF8E3010BD0DBC7FA21D28CD06E195B60061EC0B422CAA88246B41CFB`.
- SHA-256 of the cleaned CSV: `60FE343827C6F20F9B6E0B98364FE570E37AA993D843DC65BF234CADDFE2493B`.

Cleaning strips column-name and string whitespace, changes `Nopember` to `November` (22 rows), and derives one invalid `Week (ATD)` value from the ATD ISO week. It leaves the other missing weeks missing. Two negative BCH values and one value above the project-specific 500 bph threshold were set to missing; eight moderate positive BCH outliers were retained and flagged. No other missing values were imputed, and three zero-TEU visits were retained. The report records zero duplicate full rows and zero duplicates on `(Vessel Name, ATB, ATD)`.

### Data dictionary and limitations

| CSV field(s) | Meaning and type | Unit or caution |
| --- | --- | --- |
| `No`, `Vessel Name` | Source number and vessel name | `No` repeats; neither is a unique visit key alone. Names were trimmed and mapped to a vessel registry. |
| `OPR`, `Shipping Line` | Per-visit operator and shipping-line codes | The same vessel can have different values on different visits. Code definitions are not supplied. |
| `ATB`, `ATD` | Actual time of berthing and departure, respectively | `YYYY-MM-DD HH:MM:SS` with **no timezone in the source**. Source timezone is unconfirmed. `ATD - ATB` is a possible observed duration. |
| `Month`, `Week (ATD)` | Indonesian month name; ISO 8601 week number where present | **806 of 807** cleaned rows lack `Week (ATD)`; one invalid source value was derived from ATD. |
| `Total Teus`, `Moves` | Container volume and move count | TEU quantity can be fractional; three visits have zero TEU. Whether either value was known before berthing is unverified. |
| `Berth Time`, `Berth Working Time`, `Gross Crane Time` | Recorded operational durations | Database schema describes these as hours; they describe work after arrival. |
| `Crane Ratio`, `Crane Idle` | Recorded crane measures | Numeric values; precise definitions and units are not documented. |
| `BPR (mph)`, `BCH (ET) (bph)` | Berth productivity (moves/hour) and crane handling rate (boxes/hour), per schema | BCH is missing in **71 rows**: 68 source blanks and three cleaning flags. Eight retained high values need source review. `ET` is not expanded in the project documentation. |
| `QUAY CRANE BREAKDOWN`, `WAITING CONTAINER`, `WAITING INTERNAL TRUCK`, `GEAR BOX HANDLING`, `BAD WEATHER`, `SYSTEM FAILURE` | Six preserved disruption-related numeric source fields | Original units and whether values were available before an operation are **unverified**. Do not interpret them as event counts or durations without source confirmation. |

## PostgreSQL mapping

[`scripts/load_port_calls.py`](../scripts/load_port_calls.py) maps the CSV into the [`ships` and `port_calls` schema](../database/schema/001_core_schema.sql). Distinct trimmed vessel names become `ships.vessel_name`; each CSV visit becomes one `port_calls` row with a `ship_id` foreign key. The per-visit operator and line remain on `port_calls`. `No` becomes `source_no`; `port_call_id` is a database-generated primary key because source numbers repeat. The loader requires an explicit `PORT_CALLS_TIMEZONE`, parses timestamps without an offset, and inserts them into PostgreSQL `TIMESTAMPTZ` columns. It refuses to import into nonempty `ships` or `port_calls` tables.

The database currently contains **179 ships and 807 port calls**. A multiset comparison of all CSV `(No, Vessel Name, ATB, ATD)` values against joined database rows found **zero missing or extra visits** when stored timestamps were rendered in `Asia/Jakarta`. This establishes how the current database values are encoded; it does **not** independently establish the workbook's true source timezone. Use `port_call_id` for database joins. All historical `port_calls.berth_id` values are null because the source has no berth assignment. The demonstration inventory contains one terminal, six berths, and cranes `C1`–`C12`; it is not an additional historical visit dataset.

No project synthetic-data generator or synthetic labels were found in the data scripts. Manually submitted simulation jobs and test fixtures are separate from the historical workbook; the provenance of any individual simulation run must be checked before analysis.

## Simulation-generated data

The [simulation schema](../database/migrations/002_simulation_schema.sql), [ship link migration](../database/migrations/003_simulation_job_ship.sql), and [dispatch sequence migration](../database/migrations/004_simulation_job_dispatch_sequence.sql) define:

- `simulation_runs`: policy (`FCFS`, `SJF`, `PRIORITY`), resource capacities, status, and database wall-clock start/end timestamps. By design, a run can remain `RUNNING` with `ended_at` null after its current jobs finish, so additional jobs can be submitted before Reset.
- `simulation_jobs`: per-run job ID, simulated `arrival_time`, `priority`, `work_total/completed/remaining`, requested and current aggregate berth/crane/truck quantities, lifecycle status, immutable first `dispatch_sequence`, optional `ship_id` and `port_call_id`, and wall-clock submission/start/completion timestamps. `arrival_time` and work are simulation units, not historical dates or calibrated physical hours.
- `crane_assignments`: crane-to-job assignment history with wall-clock assignment/release timestamps and release reason. `disruption_events`: crane failure/recovery events, optional affected job and work snapshot, and wall-clock event time.

The C++ snapshot exposes the current `simulation_time`, which advances on Step/Run, but **that clock and per-job simulated start/finish ticks are not persisted** in PostgreSQL. Database `started_at`, `completed_at`, assignment times, and event times measure backend wall time; do not subtract them to label simulated waiting or turnaround time. `dispatch_sequence` records first dispatch order, not a simulated timestamp. Historical `port_calls` are source visits; simulation rows are application-generated scenarios, not observed port calls.

At inspection, PostgreSQL held 156 runs, 211 jobs, 141 crane assignments, and 51 disruption events. Only 102 jobs had `ship_id` (covering three distinct ships), and **no jobs had `port_call_id`**. The application joins a submitted job to a selected `ship_id`, not to a specific historical visit. Joining `simulation_jobs` to `port_calls` by ship alone would pair a job with multiple unrelated visits. Legacy jobs with null `ship_id` cannot be joined to ships. The optional `port_call_id` relationship is currently unused.

**Known legacy inconsistency:** `simulation_job_id=1` in run 2 (`JOB_001`) is still `COMPLETED` with `work_total=10`, `work_completed=0`, and `work_remaining=10`; it also has no ship or port-call link. Exclude it from simulation-derived analysis until its provenance and counters are reviewed. Other historical simulation rows have not been validated as training examples merely because they satisfy database constraints.

## ML readiness and decisions for the next developer

Candidate historical inputs include vessel/operator/line, calendar features derived from ATB, and TEUs or moves **only if their availability before the intended prediction time is established**. Candidate simulation inputs include scheduling policy, simulated arrival and work, priority, requested resources, and run capacities. Possible targets to evaluate include observed berth-to-departure duration (`ATD - ATB`) or an explicitly defined simulation outcome. These are suggestions, not chosen labels or a model specification.

Define the prediction moment before selecting features. Berth/working/crane times, productivity rates, final moves, departure time, and disruption measurements may reveal the outcome if predicting at or before arrival. Confirm when each field became known; fit preprocessing on training data only; evaluate with a time-aware split and consider repeat vessels. Do not mix historical durations with simulation work units or backend wall-clock durations as if they shared a unit.

Before implementation, the ML developer should confirm source provenance, timezone and disruption units; choose a target and observation point; decide how to handle missing BCH/week and flagged outliers; select and document a train/test split; and version the exact source, cleaned CSV, preprocessing code, and assumptions. Simulation-derived training would additionally require a reviewed run subset and a defined simulated-time outcome. No such labels, trained models, or prediction service are part of this handoff.
