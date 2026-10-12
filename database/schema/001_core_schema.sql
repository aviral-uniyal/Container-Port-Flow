-- ============================================================================
-- Container-Port-Flow: Core Schema
-- File: database/schema/001_core_schema.sql
--
-- Creates the four core tables in dependency order:
--   1. terminals  (independent)
--   2. berths     (depends on terminals)
--   3. ships      (independent)
--   4. port_calls (depends on ships, berths)
--
-- Designed against: data/processed/port_calls_cleaned.csv  (807 rows, 23 columns)
-- ============================================================================

-- ============================================================================
-- 1. TERMINALS
-- ============================================================================
-- Represents a container terminal within a port.
-- The historical dataset comes from a single terminal, but the schema supports
-- multiple terminals for future expansion and simulation scenarios.

CREATE TABLE terminals (
    terminal_id   SERIAL        PRIMARY KEY,
    terminal_name VARCHAR(100)  NOT NULL UNIQUE,
    description   TEXT,

    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);

COMMENT ON TABLE  terminals IS
    'Container terminals within a port. Supports multi-terminal simulation.';


-- ============================================================================
-- 2. BERTHS
-- ============================================================================
-- A berth belongs to a terminal. The historical CSV does not include a berth
-- assignment column, so port_calls.berth_id is nullable. Four berths per
-- terminal is a proposed illustrative simulation configuration — seed data
-- is NOT inserted here; use a separate seed script.

CREATE TABLE berths (
    berth_id      SERIAL        PRIMARY KEY,
    terminal_id   INTEGER       NOT NULL
                                REFERENCES terminals (terminal_id)
                                ON DELETE CASCADE,
    berth_name    VARCHAR(50)   NOT NULL,
    -- (length_m, max_draft_m, crane_count omitted: not present in the dataset)

    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),

    UNIQUE (terminal_id, berth_name)
);

COMMENT ON TABLE  berths IS
    'Berths within a terminal. Historical data has no berth assignment, '
    'so port_calls.berth_id is nullable for imported records.';
COMMENT ON COLUMN berths.terminal_id IS
    'Parent terminal. CASCADE delete removes orphaned berths.';


-- ============================================================================
-- 3. SHIPS
-- ============================================================================
-- Master record for a vessel. Stores default operator and shipping line, but
-- these are NOT authoritative for every historical port call — the CSV records
-- different OPR / Shipping Line combinations per call, so those values are
-- preserved directly on port_calls as well.

CREATE TABLE ships (
    ship_id        SERIAL        PRIMARY KEY,
    vessel_name    VARCHAR(120)  NOT NULL UNIQUE,

    -- Default operator and shipping line (from most-recent or most-frequent
    -- usage). These are convenience defaults; per-call values live on
    -- port_calls and may differ.
    default_opr           VARCHAR(30),
    default_shipping_line VARCHAR(30),

    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);

COMMENT ON TABLE  ships IS
    'Master vessel registry. OPR and Shipping Line here are convenience '
    'defaults; the authoritative per-call values are on port_calls.';
COMMENT ON COLUMN ships.vessel_name IS
    'Vessel name as it appears in the cleaned dataset (uppercased, trimmed).';
COMMENT ON COLUMN ships.default_opr IS
    'Default operator code. May differ from historical port_calls.opr.';
COMMENT ON COLUMN ships.default_shipping_line IS
    'Default shipping line. May differ from historical port_calls.shipping_line.';


-- ============================================================================
-- 4. PORT_CALLS
-- ============================================================================
-- One row per vessel visit (port call). This is the main fact table.
--
-- Design decisions:
--   • Surrogate PK (port_call_id) because the Excel "No" column resets across
--     sheets and is NOT globally unique (values 1-223 repeat across months).
--   • source_no preserves the original Excel "No" for traceability.
--   • opr and shipping_line are stored per call — they vary across calls for
--     the same vessel in the historical data.
--   • berth_id is nullable: the CSV has no berth assignment column.
--   • month is stored as the original Indonesian month name (e.g. 'Januari',
--     'Februari') to match the source data without lossy transformation.
--   • week_atd is almost entirely NULL in the cleaned data (806 of 807 rows).
--     We preserve it but do NOT derive or generate values in the schema.
--   • total_teus uses NUMERIC because the CSV contains non-integer values
--     (e.g. 509.25 TEU — possibly half-TEU accounting).
--   • bch_bph (BCH crane handling rate) allows NULL; the cleaning script set
--     negative and extreme-outlier values to missing (3 rows), plus 68 rows
--     were already NULL in the source — 71 NULLs total.
--   • Six disruption source columns are preserved exactly as named in the CSV.
--     Their original units are unknown (could be hours, percentages, or
--     another measure) so the column names do NOT assert units.

CREATE TABLE port_calls (
    port_call_id  SERIAL        PRIMARY KEY,

    -- --------------- Source traceability ---------------
    source_no     INTEGER       NOT NULL,
        -- Original Excel "No" column. NOT unique: resets per sheet/month.

    -- --------------- Vessel reference -----------------
    ship_id       INTEGER       NOT NULL
                                REFERENCES ships (ship_id)
                                ON DELETE RESTRICT,

    -- --------------- Per-call operator & line ----------
    -- Preserved separately from ship defaults because the historical data
    -- shows different OPR / Shipping Line values for the same vessel.
    opr            VARCHAR(30)  NOT NULL,
    shipping_line  VARCHAR(30)  NOT NULL,

    -- --------------- Time period ----------------------
    month          VARCHAR(20)  NOT NULL,
        -- Indonesian month name from source (Januari … Desember).
    week_atd       INTEGER,
        -- ISO 8601 week number derived from ATD. Mostly NULL in cleaned data.

    -- --------------- Timestamps -----------------------
    atb            TIMESTAMPTZ  NOT NULL,
        -- Actual Time of Berthing.
    atd            TIMESTAMPTZ  NOT NULL,
        -- Actual Time of Departure.

    -- --------------- Berth assignment -----------------
    berth_id       INTEGER
                   REFERENCES berths (berth_id)
                   ON DELETE SET NULL,
        -- NULL for all historical rows: the CSV has no berth column.

    -- --------------- Cargo & operations ----------------
    total_teus          NUMERIC(10,2),
        -- NUMERIC because the source contains non-integer values (e.g. 509.25).
    moves               INTEGER,

    -- --------------- Time metrics (hours) ---------------
    berth_time           NUMERIC(12,6),
    berth_working_time   NUMERIC(12,6),
    gross_crane_time     NUMERIC(12,6),

    -- --------------- Crane performance -----------------
    crane_ratio          NUMERIC(8,4),
    crane_idle           NUMERIC(8,4),
    bpr_mph              NUMERIC(10,4),
        -- Berth Productivity Rate (moves per hour).
    bch_bph              NUMERIC(10,4),
        -- Box Crane Handling rate (boxes per hour).
        -- NULL for 71 rows: 68 originally missing + 3 cleaned (negative/outlier).

    -- --------------- Disruption sources ----------------
    -- Six separate disruption fields preserved exactly as in the CSV.
    -- Original units are unverified; column names do not assert units.
    quay_crane_breakdown    NUMERIC(8,4),
    waiting_container       NUMERIC(8,4),
    waiting_internal_truck  NUMERIC(8,4),
    gear_box_handling       NUMERIC(8,4),
    bad_weather             NUMERIC(8,4),
    system_failure          NUMERIC(8,4),

    -- --------------- Sanity constraints ----------------
    CONSTRAINT chk_atd_after_atb CHECK (atd >= atb),
    CONSTRAINT chk_source_no_positive CHECK (source_no > 0),
    CONSTRAINT chk_total_teus_non_negative CHECK (total_teus >= 0),
    CONSTRAINT chk_moves_non_negative CHECK (moves >= 0)
);

-- Indexes for common query patterns
CREATE INDEX idx_port_calls_ship_id   ON port_calls (ship_id);
CREATE INDEX idx_port_calls_berth_id  ON port_calls (berth_id);
CREATE INDEX idx_port_calls_atb       ON port_calls (atb);
CREATE INDEX idx_port_calls_atd       ON port_calls (atd);
CREATE INDEX idx_port_calls_month     ON port_calls (month);

COMMENT ON TABLE  port_calls IS
    'Historical and simulated vessel port calls. One row per visit. '
    'Surrogate PK; original Excel row number in source_no.';
COMMENT ON COLUMN port_calls.source_no IS
    'Original "No" from the Excel workbook. Not globally unique — resets per month/sheet.';
COMMENT ON COLUMN port_calls.berth_id IS
    'NULL for all historical imports (CSV has no berth column). '
    'Populated by simulation or manual assignment.';
COMMENT ON COLUMN port_calls.bch_bph IS
    'Box Crane Handling rate. NULL for 71 rows: 68 originally missing, '
    '3 set to missing during cleaning (negative or extreme outlier values).';
COMMENT ON COLUMN port_calls.month IS
    'Indonesian month name from source data (Januari, Februari, … Desember).';


-- ============================================================================
-- END OF CORE SCHEMA
-- ============================================================================
