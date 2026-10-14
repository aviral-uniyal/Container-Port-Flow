-- ============================================================================
-- Container-Port-Flow: Demo Resource Seed
-- File: database/seeds/001_demo_resources.sql
--
-- Inserts one terminal, six berths, and twelve cranes for demonstration
-- and simulation use.
--
-- Design notes
-- ----------------------------------------------------------------------------
-- * All numeric IDs are resolved at runtime via sub-selects on their UNIQUE
--   name columns (terminal_name, berth_name).  No hardcoded integer IDs.
-- * ON CONFLICT ... DO NOTHING makes every statement idempotent; re-running
--   the script never overwrites existing rows or changes crane statuses.
-- * Trucks are aggregate-only: simulation_runs.total_trucks records the
--   capacity (30) per run.  No individual truck records or trucks table exist.
-- * Berths: UNIQUE (terminal_id, berth_name) is the natural conflict target.
-- * Cranes: crane_id VARCHAR(20) is the PK; conflict on crane_id is used so
--   that existing statuses (ASSIGNED, FAILED) are never reset to AVAILABLE.
-- ============================================================================

BEGIN;

-- ============================================================================
-- 1. TERMINAL
-- ============================================================================
-- terminals.terminal_name has a UNIQUE constraint, so ON CONFLICT (terminal_name)
-- is the idempotency key.

INSERT INTO terminals (terminal_name, description)
VALUES (
    'Demo Terminal',
    'Primary demonstration terminal used for simulation scenarios.'
)
ON CONFLICT (terminal_name) DO NOTHING;

-- ============================================================================
-- 2. BERTHS
-- ============================================================================
-- berths has a UNIQUE (terminal_id, berth_name) constraint.
-- terminal_id is resolved by sub-select; no hardcoded integer ID.

INSERT INTO berths (terminal_id, berth_name)
SELECT t.terminal_id, b.berth_name
FROM   (SELECT terminal_id FROM terminals WHERE terminal_name = 'Demo Terminal') AS t
CROSS JOIN (
    VALUES
        ('Berth-1'),
        ('Berth-2'),
        ('Berth-3'),
        ('Berth-4'),
        ('Berth-5'),
        ('Berth-6')
) AS b (berth_name)
ON CONFLICT (terminal_id, berth_name) DO NOTHING;

-- ============================================================================
-- 3. CRANES
-- ============================================================================
-- cranes.crane_id VARCHAR(20) is the PRIMARY KEY.
-- ON CONFLICT (crane_id) DO NOTHING preserves any existing status
-- (AVAILABLE / ASSIGNED / FAILED) and does not reassign the crane terminal.
--
-- Truck capacity note: the simulation uses an aggregate of 30 trucks per run,
-- recorded in simulation_runs.total_trucks.  No individual truck rows are
-- inserted here.

INSERT INTO cranes (crane_id, terminal_id, status)
SELECT c.crane_id, t.terminal_id, 'AVAILABLE'
FROM   (SELECT terminal_id FROM terminals WHERE terminal_name = 'Demo Terminal') AS t
CROSS JOIN (
    VALUES
        ('C1'),  ('C2'),  ('C3'),  ('C4'),
        ('C5'),  ('C6'),  ('C7'),  ('C8'),
        ('C9'),  ('C10'), ('C11'), ('C12')
) AS c (crane_id)
ON CONFLICT (crane_id) DO NOTHING;

-- ============================================================================
-- END OF DEMO RESOURCE SEED
-- ============================================================================

COMMIT;
