-- Link new simulation jobs to the existing vessel registry.
-- Nullable for jobs saved before ship selection was introduced.
BEGIN;

ALTER TABLE simulation_jobs
    ADD COLUMN ship_id INTEGER
    REFERENCES ships (ship_id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX uq_simulation_jobs_run_ship
    ON simulation_jobs (run_id, ship_id)
    WHERE ship_id IS NOT NULL;

COMMENT ON COLUMN simulation_jobs.ship_id IS
    'Selected vessel for this simulation job; NULL only for legacy jobs.';

COMMIT;
