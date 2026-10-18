-- Preserve the immutable order in which jobs were first selected to execute.
BEGIN;

ALTER TABLE simulation_jobs
    ADD COLUMN IF NOT EXISTS dispatch_sequence INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'chk_job_dispatch_sequence_non_neg'
           AND conrelid = 'simulation_jobs'::regclass
    ) THEN
        ALTER TABLE simulation_jobs
            ADD CONSTRAINT chk_job_dispatch_sequence_non_neg
            CHECK (dispatch_sequence >= 0);
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_simulation_jobs_run_dispatch_sequence
    ON simulation_jobs (run_id, dispatch_sequence)
    WHERE dispatch_sequence IS NOT NULL;

COMMENT ON COLUMN simulation_jobs.dispatch_sequence IS
    'Zero-based first-dispatch order within a simulation run; NULL until first dispatch.';

COMMIT;
