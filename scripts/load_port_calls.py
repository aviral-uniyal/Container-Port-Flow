"""
load_port_calls.py
==================
Container-Port-Flow -- Historical CSV Import
---------------------------------------------
Reads  : data/processed/port_calls_cleaned.csv   (807 rows, 23 columns)
Writes : PostgreSQL database  `container_port_flow`

Dependencies:
    psycopg2  (or psycopg2-binary) -- PostgreSQL adapter for Python
    csv, os, sys, datetime         -- standard library

Connection settings are read from environment variables:
    PGHOST              (default: localhost)
    PGPORT              (default: 5432)
    PGDATABASE          (default: container_port_flow)
    PGUSER              (default: postgres)
    PGPASSWORD          (no default -- must be set)
    PORT_CALLS_TIMEZONE (no default -- must be explicitly set)

Usage:
    export PGPASSWORD='your_password'                 # Unix/macOS
    export PORT_CALLS_TIMEZONE='Asia/Jakarta'         # once source timezone is confirmed
    set    PGPASSWORD=your_password                   # Windows CMD
    set    PORT_CALLS_TIMEZONE=Asia/Jakarta           # Windows CMD
    $env:PGPASSWORD = 'your_password'                 # PowerShell
    $env:PORT_CALLS_TIMEZONE = 'Asia/Jakarta'         # PowerShell

    python scripts/load_port_calls.py

Initial-import guard:
    This script is designed for a one-time initial import of the cleaned
    historical dataset into empty tables.  Before inserting, it checks
    whether either port_calls or ships already contains rows.  If either
    table is non-empty, the script aborts with a clear message and rolls
    back — it will NOT delete or overwrite existing data.  To re-import,
    you must manually inspect the database and decide how to proceed
    (e.g. TRUNCATE port_calls, ships RESTART IDENTITY CASCADE, then re-run).

Timezone requirement:
    The CSV timestamps (ATB, ATD) are in the format 'YYYY-MM-DD HH:MM:SS'
    with NO timezone indicator.  The source data originates from a single
    Indonesian port (likely WIB / UTC+7), but the source timezone is
    currently UNCONFIRMED.

    To prevent silent assumptions (such as defaulting to UTC, Asia/Jakarta,
    the machine timezone, or PostgreSQL's existing session timezone), this
    script requires the PORT_CALLS_TIMEZONE environment variable to be
    explicitly set before running.  If it is missing or empty, the script
    aborts before connecting to PostgreSQL or modifying any tables.  When
    configured, the timezone is safely applied to the session using
    SELECT set_config('timezone', %s, false) and verified before any
    inserts take place.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ---------------------------------------------------------------------------
# 0. Dependency check -- fail fast with a helpful message
# ---------------------------------------------------------------------------
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print(
        "ERROR: psycopg2 is not installed.\n"
        "Install it with:\n"
        "    pip install psycopg2-binary\n"
        "or (for production builds):\n"
        "    pip install psycopg2\n"
        "\n"
        "This script requires a PostgreSQL driver and cannot proceed without one.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------

# Resolve path relative to this script's location so the script works
# regardless of the working directory.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CSV_PATH = PROJECT_ROOT / "data" / "processed" / "port_calls_cleaned.csv"

EXPECTED_ROW_COUNT = 807

# CSV header → schema column mapping.
# Keys are the exact CSV headers; values are the port_calls column names.
CSV_TO_SCHEMA = {
    "No":                       "source_no",
    "Vessel Name":              None,           # resolved via ship_id lookup
    "OPR":                      "opr",
    "Month":                    "month",
    "Week (ATD)":               "week_atd",
    "ATB":                      "atb",
    "ATD":                      "atd",
    "Shipping Line":            "shipping_line",
    "Total Teus":               "total_teus",
    "Moves":                    "moves",
    "Berth Time":               "berth_time",
    "Berth Working Time":       "berth_working_time",
    "Gross Crane Time":         "gross_crane_time",
    "Crane Ratio":              "crane_ratio",
    "Crane Idle":               "crane_idle",
    "BPR (mph)":                "bpr_mph",
    "BCH (ET) (bph)":           "bch_bph",
    "QUAY CRANE BREAKDOWN":     "quay_crane_breakdown",
    "WAITING CONTAINER":        "waiting_container",
    "WAITING INTERNAL TRUCK":   "waiting_internal_truck",
    "GEAR BOX HANDLING":        "gear_box_handling",
    "BAD WEATHER":              "bad_weather",
    "SYSTEM FAILURE":           "system_failure",
}

# Columns that hold integer values in the schema.
INTEGER_COLUMNS = {"source_no", "moves", "week_atd"}

# Columns that hold NUMERIC (decimal) values in the schema.
NUMERIC_COLUMNS = {
    "total_teus", "berth_time", "berth_working_time", "gross_crane_time",
    "crane_ratio", "crane_idle", "bpr_mph", "bch_bph",
    "quay_crane_breakdown", "waiting_container", "waiting_internal_truck",
    "gear_box_handling", "bad_weather", "system_failure",
}

# Columns that hold TIMESTAMPTZ values in the schema.
TIMESTAMP_COLUMNS = {"atb", "atd"}

# Expected CSV timestamp format (no timezone indicator).
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# 2. Helpers
# ---------------------------------------------------------------------------

def parse_integer(raw: str, schema_col: str) -> int:
    """Parse a string as an exact integer without floating-point conversion.

    Accepts both plain integers ('806') and decimal representations that
    are mathematically integral ('806.0', '806.00').  Uses Decimal for
    exact arithmetic — no float rounding can silently alter the value.

    Returns int.
    Raises ValueError if the value is not mathematically integral.
    """
    try:
        d = Decimal(raw)
    except InvalidOperation:
        raise ValueError(
            f"Column {schema_col!r}: cannot parse {raw!r} as a number"
        )
    # Decimal.to_integral_value() rounds; we must reject non-integral values.
    if d != d.to_integral_value():
        raise ValueError(
            f"Column {schema_col!r}: expected integer but got {raw!r}"
        )
    return int(d)


def parse_value(value: str, schema_col: str):
    """Convert a raw CSV string to the appropriate Python type for the schema.

    Returns None for empty strings (→ SQL NULL).
    Raises ValueError for genuinely unparseable data.
    """
    stripped = value.strip()

    if stripped == "":
        return None

    if schema_col in INTEGER_COLUMNS:
        return parse_integer(stripped, schema_col)

    if schema_col in NUMERIC_COLUMNS:
        # Use Decimal for exact representation matching NUMERIC in PostgreSQL.
        try:
            return Decimal(stripped)
        except InvalidOperation:
            raise ValueError(
                f"Column {schema_col!r}: cannot parse {stripped!r} as Decimal"
            )

    if schema_col in TIMESTAMP_COLUMNS:
        try:
            return datetime.strptime(stripped, TIMESTAMP_FORMAT)
        except ValueError:
            raise ValueError(
                f"Column {schema_col!r}: cannot parse {stripped!r} with "
                f"format {TIMESTAMP_FORMAT!r}"
            )

    # Default: return as string (for opr, shipping_line, month, etc.)
    return stripped


def get_connection():
    """Create a psycopg2 connection using environment variables."""
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=os.environ.get("PGDATABASE", "container_port_flow"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
    )


# ---------------------------------------------------------------------------
# 3. Main import logic
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the historical CSV import.  Returns 0 on success, 1 on failure."""

    # ---- 3a. Read and validate the CSV ---------------------------------
    if not CSV_PATH.is_file():
        print(f"ERROR: CSV file not found: {CSV_PATH}", file=sys.stderr)
        return 1

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)

        # Validate headers against the expected mapping.
        actual_headers = set(reader.fieldnames or [])
        expected_headers = set(CSV_TO_SCHEMA.keys())
        missing = expected_headers - actual_headers
        extra = actual_headers - expected_headers
        if missing:
            print(
                f"ERROR: Missing CSV columns (expected by schema mapping): "
                f"{sorted(missing)}",
                file=sys.stderr,
            )
            return 1
        if extra:
            print(
                f"WARNING: Extra CSV columns not mapped to schema (ignored): "
                f"{sorted(extra)}",
                file=sys.stderr,
            )

        rows = list(reader)

    total_source_rows = len(rows)
    print(f"Source CSV: {CSV_PATH.name}")
    print(f"  Rows read: {total_source_rows}")

    # Hard-fail if the row count does not match the expected value.
    # This catches silent data corruption before any database writes.
    if total_source_rows != EXPECTED_ROW_COUNT:
        print(
            f"ERROR: Expected {EXPECTED_ROW_COUNT} rows but read "
            f"{total_source_rows}. Aborting before database writes.\n"
            f"Investigate the CSV file before retrying.",
            file=sys.stderr,
        )
        return 1

    # ---- 3b. Extract distinct vessel names -----------------------------
    vessel_names = sorted({row["Vessel Name"] for row in rows})
    print(f"  Distinct vessels: {len(vessel_names)}")

    # ---- 3c. Parse all rows before touching the database ---------------
    parsed_rows: list[dict] = []
    parse_errors: list[str] = []

    for row_idx, raw_row in enumerate(rows, start=1):
        parsed: dict = {"_vessel_name": raw_row["Vessel Name"]}
        try:
            for csv_col, schema_col in CSV_TO_SCHEMA.items():
                if schema_col is None:
                    continue  # handled via ship_id lookup
                parsed[schema_col] = parse_value(raw_row[csv_col], schema_col)
            parsed_rows.append(parsed)
        except (ValueError, KeyError) as exc:
            parse_errors.append(f"  Row {row_idx}: {exc}")

    if parse_errors:
        print(
            f"\nERROR: {len(parse_errors)} row(s) failed to parse. "
            f"Aborting before database writes.",
            file=sys.stderr,
        )
        for err in parse_errors[:20]:
            print(err, file=sys.stderr)
        if len(parse_errors) > 20:
            print(
                f"  ... and {len(parse_errors) - 20} more.",
                file=sys.stderr,
            )
        return 1

    print(f"  Rows parsed successfully: {len(parsed_rows)}")

    # ---- 3d. Timezone configuration check ------------------------------
    target_timezone = os.environ.get("PORT_CALLS_TIMEZONE", "").strip()
    if not target_timezone:
        print(
            "\nERROR: Missing required environment variable PORT_CALLS_TIMEZONE.\n"
            "The historical port call timestamps (ATB, ATD) are timezone-naive in the\n"
            "source CSV ('YYYY-MM-DD HH:MM:SS') with no UTC offset or zone identifier.\n"
            "The source timezone is currently UNCONFIRMED.\n"
            "\n"
            "To prevent silently defaulting to UTC, Asia/Jakarta, the local machine\n"
            "timezone, or PostgreSQL's existing session timezone, you must explicitly\n"
            "configure PORT_CALLS_TIMEZONE once the source timezone has been verified.\n"
            "\n"
            "Example configuration (once confirmed):\n"
            "  PowerShell: $env:PORT_CALLS_TIMEZONE = 'Asia/Jakarta'\n"
            "  CMD:        set PORT_CALLS_TIMEZONE=Asia/Jakarta\n"
            "  Bash:       export PORT_CALLS_TIMEZONE='Asia/Jakarta'\n"
            "\n"
            "Aborting before connecting to PostgreSQL or performing any database writes.",
            file=sys.stderr,
        )
        return 1

    # Validate timezone identifier using Python standard library zoneinfo
    # before connecting to PostgreSQL.
    try:
        ZoneInfo(target_timezone)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        print(
            f"\nERROR: Invalid timezone identifier in PORT_CALLS_TIMEZONE: {target_timezone!r}.\n"
            f"Details: {exc}\n"
            f"Please specify a valid IANA timezone identifier (e.g., 'Asia/Jakarta' or 'UTC').\n"
            f"Aborting before connecting to PostgreSQL.",
            file=sys.stderr,
        )
        return 1

    print(f"  Validated timezone identifier: {target_timezone}")

    # ---- 3e. Connect to the database ----------------------------------
    try:
        conn = get_connection()
    except psycopg2.OperationalError as exc:
        print(f"ERROR: Cannot connect to PostgreSQL: {exc}", file=sys.stderr)
        return 1

    print(f"  Connected to: {conn.dsn}")

    inserted_ships = 0
    inserted_calls = 0
    effective_tz: str | None = None

    try:
        # Use manual transaction control so that ALL inserts (ships and
        # port_calls) are committed or rolled back atomically.
        conn.autocommit = False

        with conn.cursor() as cur:

            # ---- 3f. Set and verify session timezone -------------------
            # Validate and apply the timezone using a parameterized, safe
            # PostgreSQL function call (set_config) before any inserts.
            try:
                cur.execute(
                    "SELECT set_config('timezone', %s, false)",
                    (target_timezone,)
                )
                cur.execute("SELECT current_setting('timezone')")
                effective_tz = cur.fetchone()[0]
                if not effective_tz:
                    raise psycopg2.OperationalError(
                        "PostgreSQL returned an empty session timezone."
                    )
                print(f"  Verified effective session timezone: {effective_tz}")
            except psycopg2.Error as exc:
                print(
                    f"\nERROR: Failed to configure PostgreSQL session timezone "
                    f"to {target_timezone!r}: {exc}\n"
                    f"Aborting before any database writes.",
                    file=sys.stderr,
                )
                conn.rollback()
                return 1

            # ---- 3g. Initial-import guard ------------------------------
            # Abort if either port_calls or ships already contains rows.
            # This prevents accidental partial or duplicate imports.
            cur.execute("SELECT COUNT(*) FROM port_calls")
            existing_port_calls = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM ships")
            existing_ships = cur.fetchone()[0]

            if existing_port_calls > 0 or existing_ships > 0:
                print(
                    f"\nABORT: Target tables already contain existing records:\n"
                    f"  - port_calls: {existing_port_calls} row(s)\n"
                    f"  - ships:      {existing_ships} row(s)\n"
                    f"This script is designed for initial import into empty tables.\n"
                    f"To re-import, inspect the existing data and decide "
                    f"how to proceed.\n"
                    f"For example:\n"
                    f"  -- Review existing data:\n"
                    f"  SELECT COUNT(*) FROM ships;\n"
                    f"  SELECT COUNT(*), MIN(atb), MAX(atd) FROM port_calls;\n"
                    f"  -- If you want a clean re-import:\n"
                    f"  TRUNCATE port_calls, ships RESTART IDENTITY CASCADE;\n"
                    f"  -- Then re-run this script.\n"
                    f"\n"
                    f"No changes were made.",
                    file=sys.stderr,
                )
                conn.rollback()
                return 1

            # ---- 3h. Insert distinct vessels into ships -----------------
            ship_insert_sql = """
                INSERT INTO ships (vessel_name)
                VALUES (%s)
            """
            for name in vessel_names:
                cur.execute(ship_insert_sql, (name,))
                inserted_ships += 1

            # ---- 3i. Build vessel_name → ship_id lookup ----------------
            cur.execute(
                "SELECT ship_id, vessel_name FROM ships "
                "WHERE vessel_name = ANY(%s)",
                (vessel_names,)
            )
            ship_lookup: dict[str, int] = {
                row[1]: row[0] for row in cur.fetchall()
            }
            missing_vessels = set(vessel_names) - set(ship_lookup.keys())
            if missing_vessels:
                print(
                    f"ERROR: Could not find ship_id for: "
                    f"{sorted(missing_vessels)}",
                    file=sys.stderr,
                )
                conn.rollback()
                return 1

            # ---- 3j. Insert port calls ---------------------------------
            port_call_insert_sql = """
                INSERT INTO port_calls (
                    source_no,
                    ship_id,
                    opr,
                    shipping_line,
                    month,
                    week_atd,
                    atb,
                    atd,
                    berth_id,
                    total_teus,
                    moves,
                    berth_time,
                    berth_working_time,
                    gross_crane_time,
                    crane_ratio,
                    crane_idle,
                    bpr_mph,
                    bch_bph,
                    quay_crane_breakdown,
                    waiting_container,
                    waiting_internal_truck,
                    gear_box_handling,
                    bad_weather,
                    system_failure
                ) VALUES (
                    %(source_no)s,
                    %(ship_id)s,
                    %(opr)s,
                    %(shipping_line)s,
                    %(month)s,
                    %(week_atd)s,
                    %(atb)s,
                    %(atd)s,
                    NULL,
                    %(total_teus)s,
                    %(moves)s,
                    %(berth_time)s,
                    %(berth_working_time)s,
                    %(gross_crane_time)s,
                    %(crane_ratio)s,
                    %(crane_idle)s,
                    %(bpr_mph)s,
                    %(bch_bph)s,
                    %(quay_crane_breakdown)s,
                    %(waiting_container)s,
                    %(waiting_internal_truck)s,
                    %(gear_box_handling)s,
                    %(bad_weather)s,
                    %(system_failure)s
                )
            """

            for parsed in parsed_rows:
                params = dict(parsed)
                # Replace _vessel_name with the resolved ship_id.
                params["ship_id"] = ship_lookup[params.pop("_vessel_name")]
                cur.execute(port_call_insert_sql, params)
                inserted_calls += 1

        # All inserts succeeded — commit the transaction.
        conn.commit()

    except psycopg2.Error as exc:
        conn.rollback()
        print(f"\nERROR: Database error (transaction rolled back): {exc}",
              file=sys.stderr)
        return 1
    finally:
        conn.close()

    # ---- 3k. Summary ---------------------------------------------------
    print("\n" + "=" * 60)
    print("IMPORT SUMMARY")
    print("=" * 60)
    print(f"  Source rows read:         {total_source_rows}")
    print(f"  Expected rows:            {EXPECTED_ROW_COUNT}")
    print(f"  Row count check:          PASS")
    print()
    print(f"  Distinct vessels:         {len(vessel_names)}")
    print(f"    Inserted into ships:    {inserted_ships}")
    print()
    print(f"  Port calls:               {len(parsed_rows)}")
    print(f"    Inserted:               {inserted_calls}")
    print(f"    Parse errors:           0")
    if effective_tz:
        print(f"    Session timezone:       {effective_tz}")

    if inserted_calls != total_source_rows:
        print(
            f"\n  *** DISCREPANCY: inserted ({inserted_calls}) "
            f"!= source rows ({total_source_rows})",
            file=sys.stderr,
        )

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
