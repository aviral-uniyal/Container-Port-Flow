-- ============================================================================
-- Container-Port-Flow: Simulation Schema Migration
-- File: database/migrations/002_simulation_schema.sql
--
-- Additive migration — creates five simulation tables in dependency order:
--   1. cranes              (depends on terminals)
--   2. simulation_runs     (independent)
--   3. simulation_jobs     (depends on simulation_runs, port_calls)
--   4. crane_assignments   (depends on cranes, simulation_jobs)
--   5. disruption_events   (depends on simulation_runs, cranes, simulation_jobs)
--
-- Does NOT modify existing core schema tables (terminals, berths, ships,
-- port_calls) or any historical data.
--
-- Compatible with the C++ PortDispatcher / CraneRegistry / DisruptionManager
-- domain model defined in os-simulation/.
-- ============================================================================

BEGIN;


-- ============================================================================
-- 1. CRANES
-- ============================================================================
-- Physical crane inventory. Crane IDs are short strings (e.g. "C1", "C2")
-- matching the C++ CraneRegistry convention.

CREATE TABLE cranes (
    crane_id      VARCHAR(20)   NOT NULL PRIMARY KEY,
        -- String PK, compatible with the C++ CraneRegistry IDs.
    terminal_id   INTEGER       NOT NULL
                                REFERENCES terminals (terminal_id)
                                ON DELETE RESTRICT,
        -- Do not delete a terminal that still has registered cranes.
    status        VARCHAR(10)   NOT NULL DEFAULT 'AVAILABLE'
                                CONSTRAINT chk_crane_status
                                    CHECK (status IN ('AVAILABLE', 'ASSIGNED', 'FAILED')),

    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX idx_cranes_terminal_id ON cranes (terminal_id);
CREATE INDEX idx_cranes_status      ON cranes (status);

COMMENT ON TABLE  cranes IS
    'Physical crane inventory. IDs match C++ CraneRegistry string identifiers.';
COMMENT ON COLUMN cranes.status IS
    'Current operational state: AVAILABLE, ASSIGNED (to a job), or FAILED.';
COMMENT ON COLUMN cranes.terminal_id IS
    'Parent terminal. RESTRICT prevents deleting a terminal with registered cranes.';


-- ============================================================================
-- 2. SIMULATION_RUNS
-- ============================================================================
-- Each row represents one end-to-end simulation execution.
-- A run may be marked FAILED (e.g. on backend restart) without deleting any
-- child records (jobs, assignments, disruption events).

CREATE TABLE simulation_runs (
    run_id             SERIAL        PRIMARY KEY,
    run_name           VARCHAR(200)  NOT NULL,
    scheduling_policy  VARCHAR(10)   NOT NULL
                                     CONSTRAINT chk_run_policy
                                         CHECK (scheduling_policy IN ('FCFS', 'SJF', 'PRIORITY')),
    status             VARCHAR(10)   NOT NULL DEFAULT 'RUNNING'
                                     CONSTRAINT chk_run_status
                                         CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),

    -- Resource capacity snapshot for this run.
    total_berths       INTEGER       NOT NULL
                                     CONSTRAINT chk_run_berths_non_neg CHECK (total_berths >= 0),
    total_cranes       INTEGER       NOT NULL
                                     CONSTRAINT chk_run_cranes_non_neg CHECK (total_cranes >= 0),
    total_trucks       INTEGER       NOT NULL
                                     CONSTRAINT chk_run_trucks_non_neg CHECK (total_trucks >= 0),

    started_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
    ended_at           TIMESTAMPTZ,
        -- NULL while the run is still RUNNING.
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX idx_simulation_runs_status ON simulation_runs (status);

COMMENT ON TABLE  simulation_runs IS
    'Top-level simulation execution record. Preserves history even when marked FAILED.';
COMMENT ON COLUMN simulation_runs.scheduling_policy IS
    'Dispatch queue ordering: FCFS, SJF (shortest job first), or PRIORITY.';
COMMENT ON COLUMN simulation_runs.ended_at IS
    'NULL while the run is RUNNING. Set on COMPLETED or FAILED.';


-- ============================================================================
-- 3. SIMULATION_JOBS
-- ============================================================================
-- One row per job submitted to a simulation run.  Maps to the C++ PortJob /
-- JobRecord structures.  Optionally linked to a historical port_call.
--
-- Work constraints enforced by this schema:
--   • work_total >= 1       (C++ PortJob enforces work_total > 0)
--   • work_completed >= 0
--   • work_remaining >= 0
--   • work_completed <= work_total  (cannot complete more than total)
--   • work_remaining <= work_total  (cannot exceed total)
-- NOTE: the equality work_completed + work_remaining = work_total is NOT
-- enforced by a CHECK constraint — intermediate snapshots (e.g. during a
-- multi-crane disruption) may temporarily break strict equality. The
-- application layer is responsible for maintaining this invariant.

CREATE TABLE simulation_jobs (
    simulation_job_id  SERIAL        PRIMARY KEY,
    run_id             INTEGER       NOT NULL
                                     REFERENCES simulation_runs (run_id)
                                     ON DELETE RESTRICT,
        -- RESTRICT: do not delete a run that has jobs.

    job_id             VARCHAR(100)  NOT NULL,
        -- Application-level job identifier from the C++ dispatcher.

    port_call_id       INTEGER
                       REFERENCES port_calls (port_call_id)
                       ON DELETE SET NULL,
        -- Optional link to a historical port call.
        -- SET NULL: if a source port_call is ever removed, keep the simulation
        -- job record but clear the reference.

    -- ── Scheduling parameters ───────────────────────────────────────────────
    arrival_time       INTEGER       NOT NULL
                                     CONSTRAINT chk_job_arrival_non_neg CHECK (arrival_time >= 0),
    priority           INTEGER       NOT NULL DEFAULT 0,
        -- Lower value = higher priority (matches C++ convention).

    -- ── Work tracking ───────────────────────────────────────────────────────
    work_total         INTEGER       NOT NULL
                                     CONSTRAINT chk_job_work_total_pos CHECK (work_total >= 1),
        -- C++ PortJob enforces work_total > 0.
    work_completed     INTEGER       NOT NULL DEFAULT 0
                                     CONSTRAINT chk_job_work_completed_non_neg CHECK (work_completed >= 0),
    work_remaining     INTEGER       NOT NULL
                                     CONSTRAINT chk_job_work_remaining_non_neg CHECK (work_remaining >= 0),

    -- ── Resource requests (what the job asked for) ──────────────────────────
    requested_berths   INTEGER       NOT NULL DEFAULT 0
                                     CONSTRAINT chk_job_req_berths_non_neg CHECK (requested_berths >= 0),
    requested_cranes   INTEGER       NOT NULL
                                     CONSTRAINT chk_job_req_cranes_pos CHECK (requested_cranes >= 1),
        -- C++ PortJob enforces cranes > 0.
    requested_trucks   INTEGER       NOT NULL DEFAULT 0
                                     CONSTRAINT chk_job_req_trucks_non_neg CHECK (requested_trucks >= 0),

    -- ── Current aggregate allocation snapshot ───────────────────────────────
    -- These reflect the ResourceManager's current allocation for this job.
    -- Updated as resources are allocated/released.  Trucks are aggregate-based
    -- (no individual truck records).
    allocated_berths   INTEGER       NOT NULL DEFAULT 0
                                     CONSTRAINT chk_job_alloc_berths_non_neg CHECK (allocated_berths >= 0),
    allocated_cranes   INTEGER       NOT NULL DEFAULT 0
                                     CONSTRAINT chk_job_alloc_cranes_non_neg CHECK (allocated_cranes >= 0),
    allocated_trucks   INTEGER       NOT NULL DEFAULT 0
                                     CONSTRAINT chk_job_alloc_trucks_non_neg CHECK (allocated_trucks >= 0),

    -- ── Job lifecycle state ─────────────────────────────────────────────────
    status             VARCHAR(12)   NOT NULL DEFAULT 'PENDING'
                                     CONSTRAINT chk_job_status
                                         CHECK (status IN ('PENDING', 'ACTIVE', 'INTERRUPTED',
                                                           'COMPLETED', 'CANCELLED')),

    -- ── Timestamps ──────────────────────────────────────────────────────────
    submitted_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,
        -- Set when the job first transitions to ACTIVE.
    completed_at       TIMESTAMPTZ,
        -- Set when the job reaches COMPLETED or CANCELLED.

    -- ── Cross-field consistency ─────────────────────────────────────────────
    CONSTRAINT chk_job_work_completed_le_total
        CHECK (work_completed <= work_total),
    CONSTRAINT chk_job_work_remaining_le_total
        CHECK (work_remaining <= work_total),

    -- ── Uniqueness: (run_id, job_id) ────────────────────────────────────────
    CONSTRAINT uq_simulation_jobs_run_job
        UNIQUE (run_id, job_id)
);

-- Query indexes.
-- idx_simulation_jobs_run_id is intentionally omitted: the unique constraint
-- uq_simulation_jobs_run_job creates a (run_id, job_id) B-tree index whose
-- run_id prefix already satisfies FK lookups and run_id-only queries.
CREATE INDEX idx_simulation_jobs_port_call_id ON simulation_jobs (port_call_id);
CREATE INDEX idx_simulation_jobs_status       ON simulation_jobs (status);
CREATE INDEX idx_simulation_jobs_run_status   ON simulation_jobs (run_id, status);

COMMENT ON TABLE  simulation_jobs IS
    'Jobs submitted to a simulation run. Maps to the C++ PortJob/JobRecord model.';
COMMENT ON COLUMN simulation_jobs.job_id IS
    'Application-level job identifier. Unique within a run (see uq_simulation_jobs_run_job).';
COMMENT ON COLUMN simulation_jobs.port_call_id IS
    'Optional FK to a historical port call. SET NULL on source deletion to preserve '
    'simulation history.';
COMMENT ON COLUMN simulation_jobs.work_total IS
    'Total work units for the job. Must be >= 1 per C++ PortJob validation.';
COMMENT ON COLUMN simulation_jobs.work_remaining IS
    'Remaining work units. The application must explicitly set this to work_total '
    'at INSERT time; there is no DEFAULT that can reference another column. '
    'Decremented as work completes; may reach 0 when the job is COMPLETED.';
COMMENT ON COLUMN simulation_jobs.allocated_berths IS
    'Current aggregate berth allocation from the ResourceManager.';
COMMENT ON COLUMN simulation_jobs.allocated_trucks IS
    'Current aggregate truck allocation. No individual truck records exist.';


-- ============================================================================
-- 4. CRANE_ASSIGNMENTS
-- ============================================================================
-- Tracks the full history of crane-to-job assignments.
-- Released assignments are NOT deleted — they are preserved with released_at
-- and release_reason populated.
--
-- A partial unique index on (crane_id) WHERE released_at IS NULL prevents a
-- crane from having multiple simultaneous active assignments.

CREATE TABLE crane_assignments (
    assignment_id       SERIAL        PRIMARY KEY,
    crane_id            VARCHAR(20)   NOT NULL
                                      REFERENCES cranes (crane_id)
                                      ON DELETE RESTRICT,
        -- RESTRICT: do not delete a crane that has assignment history.
    simulation_job_id   INTEGER       NOT NULL
                                      REFERENCES simulation_jobs (simulation_job_id)
                                      ON DELETE RESTRICT,
        -- RESTRICT: do not delete a job that has assignment history.

    assigned_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
    released_at         TIMESTAMPTZ,
        -- NULL while the assignment is active.
    release_reason      VARCHAR(50),
        -- Free-form reason: e.g. "JOB_COMPLETED", "CRANE_FAILED", "JOB_CANCELLED".

    CONSTRAINT chk_released_at_after_assigned_at
        CHECK (released_at IS NULL OR released_at >= assigned_at)
);

-- Partial unique index: at most one active (unreleased) assignment per crane.
CREATE UNIQUE INDEX uq_crane_active_assignment
    ON crane_assignments (crane_id)
    WHERE released_at IS NULL;

-- Query indexes.
CREATE INDEX idx_crane_assignments_crane_id   ON crane_assignments (crane_id);
CREATE INDEX idx_crane_assignments_job_id     ON crane_assignments (simulation_job_id);
CREATE INDEX idx_crane_assignments_released   ON crane_assignments (released_at)
    WHERE released_at IS NOT NULL;

COMMENT ON TABLE  crane_assignments IS
    'Full history of crane-to-job assignments. Released rows are preserved, not deleted.';
COMMENT ON COLUMN crane_assignments.released_at IS
    'NULL while the assignment is active. Set when the crane is released.';
COMMENT ON COLUMN crane_assignments.release_reason IS
    'Free-form reason for release: JOB_COMPLETED, CRANE_FAILED, JOB_CANCELLED, etc.';


-- ============================================================================
-- 5. DISRUPTION_EVENTS
-- ============================================================================
-- Records crane failure and recovery events during simulation.
-- simulation_job_id is nullable because an idle crane may fail without an
-- associated job.

CREATE TABLE disruption_events (
    event_id             SERIAL        PRIMARY KEY,
    run_id               INTEGER       NOT NULL
                                       REFERENCES simulation_runs (run_id)
                                       ON DELETE RESTRICT,
    crane_id             VARCHAR(20)   NOT NULL
                                       REFERENCES cranes (crane_id)
                                       ON DELETE RESTRICT,
    simulation_job_id    INTEGER
                                       REFERENCES simulation_jobs (simulation_job_id)
                                       ON DELETE RESTRICT,
        -- NULL when an idle crane fails (no associated job).

    event_type           VARCHAR(20)   NOT NULL
                                       CONSTRAINT chk_disruption_event_type
                                           CHECK (event_type IN ('CRANE_FAILED', 'CRANE_RECOVERED')),

    -- Progress snapshot at the moment of disruption.
    -- NULL for idle-crane failures and recovery events.
    work_completed       INTEGER
                                       CONSTRAINT chk_disruption_work_completed_non_neg
                                           CHECK (work_completed IS NULL OR work_completed >= 0),
    work_remaining       INTEGER
                                       CONSTRAINT chk_disruption_work_remaining_non_neg
                                           CHECK (work_remaining IS NULL OR work_remaining >= 0),

    occurred_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Query indexes for common access patterns.
CREATE INDEX idx_disruption_events_run_id     ON disruption_events (run_id);
CREATE INDEX idx_disruption_events_crane_id   ON disruption_events (crane_id);
CREATE INDEX idx_disruption_events_job_id     ON disruption_events (simulation_job_id);
CREATE INDEX idx_disruption_events_run_type   ON disruption_events (run_id, event_type);
CREATE INDEX idx_disruption_events_occurred   ON disruption_events (occurred_at);

COMMENT ON TABLE  disruption_events IS
    'Crane failure/recovery events during simulation. Preserves full disruption timeline.';
COMMENT ON COLUMN disruption_events.simulation_job_id IS
    'NULL when the disruption affected an idle crane with no active job.';
COMMENT ON COLUMN disruption_events.work_completed IS
    'Progress snapshot. NULL for idle-crane failures and recovery events.';
COMMENT ON COLUMN disruption_events.work_remaining IS
    'Remaining work snapshot. NULL for idle-crane failures and recovery events.';
COMMENT ON COLUMN disruption_events.event_type IS
    'CRANE_FAILED or CRANE_RECOVERED, matching the C++ DisruptionEvent enum.';


-- ============================================================================
-- END OF SIMULATION SCHEMA MIGRATION
-- ============================================================================

COMMIT;
