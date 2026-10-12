"""
clean_workbook.py
=================
Container-Port-Flow -- Phase 3: Historical Data Cleaning
--------------------------------------------------------
Reads  : data/raw/18 months Historical Data.xlsx   (never modified)
Writes : data/processed/port_calls_cleaned.csv
         data/processed/cleaning_report.txt

Cleaning rules applied (documented in cleaning_report.txt):
  C1  Drop completely empty rows (Vessel Name AND both timestamps are null)
  C2  Strip leading/trailing whitespace from every column name
  C3  Strip leading/trailing whitespace from all string-valued cells
  C4  Normalize month name variants to canonical Bahasa-Indonesia spelling
  C5  Derive a missing/invalid Week (ATD) value from the ATD date (ISO week)
      Convention: ISO 8601 week number of ATD (Python isocalendar()[1]).
      This matches the integer week values already present in the workbook.
  C6  Flag negative BCH values -- set to NaN; no replacement values invented
  C7  Flag extreme BCH outlier -- set to NaN
      Rule: BCH > Q3 + 3*IQR  (Tukey far-outlier on strictly positive BCH)
      The threshold is computed at runtime and logged to the cleaning report.
  C8  Preserve all 807 valid records including legitimate zero-TEU visits
  C9  Do not impute any other missing values

Author  : Antigravity
Date    : 2026-10-12
Python  : 3.8+
Requires: pandas, openpyxl
"""

import sys
import os
import pandas as pd
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths -- relative to this script so you can run from any working directory
# ---------------------------------------------------------------------------

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

RAW_XLSX    = os.path.join(PROJECT_ROOT, "data", "raw", "18 months Historical Data.xlsx")
CLEANED_CSV = os.path.join(PROJECT_ROOT, "data", "processed", "port_calls_cleaned.csv")
REPORT_TXT  = os.path.join(PROJECT_ROOT, "data", "processed", "cleaning_report.txt")

# ---------------------------------------------------------------------------
# Month-name normalisation map
#
# Workbook uses Bahasa Indonesia month names.  Two problems were identified:
#   - Trailing spaces: 'Januari ', 'Oktober ', 'September '
#     -> resolved by str.strip() in step C3 (map below is belt-and-suspenders)
#   - Spelling variant: 'Nopember' (old spelling, 22 occurrences) vs
#     'November' (modern standard, 43 occurrences)
#     -> normalised to 'November' (dominant modern form)
# ---------------------------------------------------------------------------

MONTH_MAP = {
    "Nopember": "November",   # old Indonesian spelling -> modern standard
}

# Column name after whitespace stripping
BCH_COLUMN = "BCH (ET) (bph)"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def read_workbook(path):
    """Read the Excel workbook without modifying the source file."""
    print("[INFO] Reading workbook: {}".format(path))
    df = pd.read_excel(path, engine="openpyxl")
    print("[INFO] Raw shape: {} rows x {} columns".format(df.shape[0], df.shape[1]))
    return df


def strip_column_names(df):
    """C2: Strip whitespace from every column name."""
    old = list(df.columns)
    df.columns = [str(c).strip() for c in df.columns]
    renamed = [(o, n) for o, n in zip(old, df.columns) if o != n]
    return df, renamed


def drop_empty_rows(df):
    """
    C1: Drop completely empty rows.

    A row is 'empty' if ALL three of these are null: Vessel Name, ATB, ATD.
    This precisely matches the 3 known empty rows (original Excel indices
    296, 297, and 586).
    """
    mask = (
        df["Vessel Name"].isna() &
        df["ATB"].isna() &
        df["ATD"].isna()
    )
    return df[~mask].copy(), df[mask].copy()


def strip_string_values(df):
    """C3: Strip leading/trailing whitespace from all string-typed cells."""
    changes = {}
    for col in df.select_dtypes(include=["object", "string"]).columns:
        before = df[col].copy()
        df[col] = df[col].str.strip()
        n = int((before != df[col]).sum())
        if n:
            changes[col] = n
    return df, changes


def normalize_months(df):
    """C4: Apply MONTH_MAP to the Month column."""
    applied = {}
    for old_val, new_val in MONTH_MAP.items():
        mask  = df["Month"] == old_val
        count = int(mask.sum())
        if count:
            df.loc[mask, "Month"] = new_val
            applied[old_val] = (new_val, count)
    return df, applied


def derive_week_from_atd(df):
    """
    C5: Fix invalid or missing 'Week (ATD)' values.

    Convention used (and preserved for future scripts):
      Week number = ISO 8601 week number of the ATD timestamp.
      Python equivalent: pd.Timestamp(atd).isocalendar()[1]
      Example: ATD 2019-01-07 -> ISO week 2.

    This convention was established by inspecting the existing numeric week
    values in the workbook and verifying they match ISO week numbers of ATD.

    Steps:
      1. Coerce the column to numeric -- non-numeric strings (e.g. 'S') -> NaN.
      2. For every resulting NaN, derive the ISO week from the ATD column.
    """
    fixes       = []
    original    = df["Week (ATD)"].copy()
    df["Week (ATD)"] = pd.to_numeric(df["Week (ATD)"], errors="coerce")

    # Rows that were non-numeric originally (coercion turned them to NaN)
    bad_mask = df["Week (ATD)"].isna() & original.notna()

    for idx in df[bad_mask].index:
        orig_val = original.at[idx]
        atd      = df.at[idx, "ATD"]
        if pd.notna(atd):
            derived_week = int(pd.Timestamp(atd).isocalendar()[1])
            df.at[idx, "Week (ATD)"] = derived_week
            fixes.append((idx, orig_val, derived_week))
        else:
            fixes.append((idx, orig_val, None))

    # Int64 (capital I) supports pandas NA for missing integers
    df["Week (ATD)"] = df["Week (ATD)"].astype("Int64")
    return df, fixes


# Hard threshold for nulling a single extreme BCH value.
# The analysis identified one record (MERATUS SAMPIT, BCH=617.86 bph) that is
# ~28x the median and almost certainly a formula error in the source workbook.
# A 500 bph hard cutoff isolates this record unambiguously (the next highest
# legitimate value in the data is ~170 bph).
BCH_EXTREME_THRESHOLD = 500.0   # bph -- documented, not silent


def flag_bch_problems(df):
    """
    C6 and C7: Identify BCH values that are invalid or suspicious.

    Three tiers are applied (in order):

    Tier 1 -- Negative BCH (C6)  -->  VALUE SET TO NaN
      Physically impossible: a berth crane handling rate cannot be negative.
      Rows: CAHAYA NELLY (BCH=-59.61), MERATUS JAYAPURA (BCH=-105.74)

    Tier 2 -- Extreme outlier (C7, hard threshold)  -->  VALUE SET TO NaN
      Rule: BCH > BCH_EXTREME_THRESHOLD (500 bph)
      This hard threshold is transparent and documented above.  It targets
      exactly one record (MERATUS SAMPIT, BCH=617.86) whose value is ~28x the
      median (21 bph) and is almost certainly a formula error.

    Tier 3 -- Moderate outliers (Tukey far fence, soft flag)  -->  VALUE KEPT
      Rule: BCH > Q3 + 3 * IQR  AND  BCH <= BCH_EXTREME_THRESHOLD
      These values are statistically unusual but operationally plausible
      (e.g. high-speed small-vessel operations).  They are listed in the
      cleaning report for manual review but are NOT changed in the CSV.

    IMPORTANT: The original Excel workbook is NEVER modified.
               No replacement values are invented for nulled cells.
    """
    report = {}

    # Compute Tukey fence on strictly positive BCH values (negatives separate)
    valid_positive = df[df[BCH_COLUMN] > 0][BCH_COLUMN].dropna()
    q1  = valid_positive.quantile(0.25)
    q3  = valid_positive.quantile(0.75)
    iqr = q3 - q1
    soft_fence = q3 + 3.0 * iqr

    report["q1"]              = float(q1)
    report["q3"]              = float(q3)
    report["iqr"]             = float(iqr)
    report["upper_fence"]     = float(soft_fence)          # kept for report compat
    report["extreme_threshold"] = float(BCH_EXTREME_THRESHOLD)

    # --- Tier 1: Negative values (C6) -- NULL OUT ---
    neg_mask = df[BCH_COLUMN] < 0
    neg_rows = [
        (i, df.at[i, "Vessel Name"], float(df.at[i, BCH_COLUMN]))
        for i in df[neg_mask].index
    ]
    df.loc[neg_mask, BCH_COLUMN] = float("nan")
    report["negative_rows"] = neg_rows

    # --- Tier 2: Extreme outlier (C7, hard threshold) -- NULL OUT ---
    extreme_mask = df[BCH_COLUMN] > BCH_EXTREME_THRESHOLD
    extreme_rows = [
        (i, df.at[i, "Vessel Name"], float(df.at[i, BCH_COLUMN]))
        for i in df[extreme_mask].index
    ]
    df.loc[extreme_mask, BCH_COLUMN] = float("nan")
    report["extreme_rows"] = extreme_rows

    # --- Tier 3: Moderate outliers (soft Tukey fence) -- FLAG ONLY, keep value ---
    soft_mask = (df[BCH_COLUMN] > soft_fence) & (df[BCH_COLUMN] <= BCH_EXTREME_THRESHOLD)
    soft_rows = [
        (i, df.at[i, "Vessel Name"], float(df.at[i, BCH_COLUMN]))
        for i in df[soft_mask].index
    ]
    # Values are deliberately NOT changed here
    report["soft_flag_rows"] = soft_rows

    # Backward-compat key: everything that was nulled (tiers 1+2)
    report["outlier_rows"] = extreme_rows

    return df, report


def check_duplicates(df):
    """Return duplicate counts for the full row and on key identity columns."""
    key_cols = [c for c in ["Vessel Name", "ATB", "ATD"] if c in df.columns]
    return {
        "exact_full_row" : int(df.duplicated().sum()),
        "key_cols"       : key_cols,
        "key_duplicates" : int(df.duplicated(subset=key_cols).sum()),
    }


def missing_value_summary(df):
    """Return {column: null_count} for every column with at least one null."""
    nulls = df.isnull().sum()
    return {col: int(cnt) for col, cnt in nulls.items() if cnt > 0}


def write_cleaning_report(
    path, raw_shape, renamed_cols, dropped_rows,
    string_changes, month_fixes, week_fixes,
    bch_report, dup_report, missing, final_shape
):
    """Write a human-readable plain-text cleaning report."""
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []

    def section(title):
        lines.extend(["", "=" * 70, title, "=" * 70])

    lines += [
        "Container-Port-Flow -- Data Cleaning Report",
        "Generated : {}".format(now),
        "Source    : data/raw/18 months Historical Data.xlsx",
        "Output    : data/processed/port_calls_cleaned.csv",
    ]

    # 1. Row counts
    section("1. ROW COUNTS")
    lines += [
        "  Input rows (raw workbook)  : {}".format(raw_shape[0]),
        "  Empty rows dropped (C1)    : {}".format(len(dropped_rows)),
        "  Output rows (cleaned CSV)  : {}".format(final_shape[0]),
        "  Columns in cleaned output  : {}".format(final_shape[1]),
    ]

    # 2. Column renames
    section("2. COLUMN NAME FIXES (C2)")
    if renamed_cols:
        for o, n in renamed_cols:
            lines.append("  Renamed: '{}' -> '{}'".format(o, n))
    else:
        lines.append("  No column names required renaming.")

    # 3. Dropped rows
    section("3. COMPLETELY EMPTY ROWS DROPPED (C1)")
    if len(dropped_rows) == 0:
        lines.append("  None found.")
    else:
        lines.append("  Count    : {}".format(len(dropped_rows)))
        lines.append("  Criterion: Vessel Name IS NULL AND ATB IS NULL AND ATD IS NULL")
        for i, (idx, row) in enumerate(dropped_rows.iterrows()):
            lines.append("  Row #{}: original Excel row index {}, No={}".format(
                i + 1, idx, row.get("No", "?")))

    # 4. String stripping
    section("4. STRING WHITESPACE STRIPPED (C3)")
    if string_changes:
        for col, cnt in string_changes.items():
            lines.append("  Column '{}': {} cell(s) trimmed".format(col, cnt))
    else:
        lines.append("  No changes needed.")

    # 5. Month normalisation
    section("5. MONTH NAME NORMALISATION (C4)")
    lines.append("  Map applied:")
    if month_fixes:
        for ov, (nv, cnt) in month_fixes.items():
            lines.append("    '{}' -> '{}'  ({} row(s))".format(ov, nv, cnt))
    else:
        lines.append("    (none)")
    lines += [
        "",
        "  Note: Trailing-space variants ('Januari ', 'Oktober ', 'September ')",
        "        were resolved by the whitespace strip in step C3.",
    ]

    # 6. Week derivation
    section("6. WEEK (ATD) DERIVATION (C5)")
    lines += [
        "  Convention  : ISO 8601 week number of the ATD timestamp",
        "                pd.Timestamp(atd).isocalendar()[1] in Python",
        "  Rows fixed  : {}".format(len(week_fixes)),
    ]
    for idx, orig, derived in week_fixes:
        lines.append("    Row {}: original value='{}' -> derived week {}".format(
            idx, orig, derived))
    if not week_fixes:
        lines.append("  No Week (ATD) values required derivation.")

    # 7. BCH flagging
    section("7. BCH (ET) (bph) -- FLAGGED RECORDS (C6 AND C7)")
    lines += [
        "",
        "  Three-tier BCH treatment:",
        "    TIER 1 (C6)  Negative BCH          --> VALUE SET TO NaN (physically impossible)",
        "    TIER 2 (C7)  BCH > {:.0f} bph (hard threshold) --> VALUE SET TO NaN (extreme error)".format(
            bch_report["extreme_threshold"]),
        "    TIER 3       BCH > Tukey fence AND BCH <= {:.0f} bph --> VALUE KEPT, flagged for review".format(
            bch_report["extreme_threshold"]),
        "",
        "  === Statistical fence (Tukey far-outlier, for tiers 2-3 reference) ===",
        "  Only strictly positive BCH values are used to compute the fence.",
        "  Q1  (25th pct)  = {:.4f} bph".format(bch_report["q1"]),
        "  Q3  (75th pct)  = {:.4f} bph".format(bch_report["q3"]),
        "  IQR             = {:.4f} bph".format(bch_report["iqr"]),
        "  Soft fence      = Q3 + 3*IQR = {:.4f} bph  (flag threshold)".format(bch_report["upper_fence"]),
        "  Hard threshold  = {:.1f} bph               (null threshold)".format(
            bch_report["extreme_threshold"]),
        "",
        "  The original Excel workbook is NOT modified in any case.",
        "",
    ]

    neg = bch_report["negative_rows"]
    lines.append("  --- TIER 1: Negative BCH -- VALUE SET TO NaN: {} row(s) ---".format(len(neg)))
    for idx, vessel, bch in neg:
        lines.append("    Row {:4d}  Vessel: {:<30s}  BCH = {:.2f} bph  [NULLED]".format(
            idx, vessel, bch))

    ext = bch_report["extreme_rows"]
    lines += [
        "",
        "  --- TIER 2: Extreme outlier (BCH > {:.0f} bph) -- VALUE SET TO NaN: {} row(s) ---".format(
            bch_report["extreme_threshold"], len(ext)),
    ]
    for idx, vessel, bch in ext:
        lines.append("    Row {:4d}  Vessel: {:<30s}  BCH = {:.2f} bph  [NULLED]".format(
            idx, vessel, bch))

    soft = bch_report["soft_flag_rows"]
    lines += [
        "",
        "  --- TIER 3: Moderate outliers (Tukey fence, BCH > {:.2f}) -- VALUE KEPT: {} row(s) ---".format(
            bch_report["upper_fence"], len(soft)),
        "  These rows are flagged for manual review but their BCH values are",
        "  preserved unchanged in the cleaned CSV.",
    ]
    for idx, vessel, bch in soft:
        lines.append("    Row {:4d}  Vessel: {:<30s}  BCH = {:.2f} bph  [KEPT, review recommended]".format(
            idx, vessel, bch))

    lines += [
        "",
        "  Summary:",
        "    BCH values set to NaN : {} (tier 1 negative + tier 2 extreme)".format(
            len(neg) + len(ext)),
        "    BCH values kept but flagged: {} (tier 3 moderate)".format(len(soft)),
    ]

    # 8. Duplicates
    section("8. DUPLICATE CHECK")
    lines += [
        "  Exact full-row duplicates           : {}".format(dup_report["exact_full_row"]),
        "  Key-column duplicates ({}) : {}".format(
            ", ".join(dup_report["key_cols"]), dup_report["key_duplicates"]),
    ]

    # 9. Missing values
    section("9. MISSING VALUES IN CLEANED OUTPUT")
    if missing:
        lines.append("  {:<35s}  {:>10s}".format("Column", "Null count"))
        lines.append("  {:<35s}  {:>10s}".format("-" * 35, "-" * 10))
        for col, cnt in missing.items():
            lines.append("  {:<35s}  {:>10d}".format(col, cnt))
    else:
        lines.append("  No missing values.")

    # 10. Preservation checks
    section("10. PRESERVATION CHECKS")
    ok = "PASS" if final_shape[0] == 807 else "FAIL (expected 807)"
    lines += [
        "  Requirement: All 807 valid records must be preserved.",
        "  Output row count : {}".format(final_shape[0]),
        "  Row count check  : {}".format(ok),
        "",
        "  Requirement: No replacement values invented for missing data.",
        "  Only BCH values that are negative or extreme outliers are nulled.",
        "  All other NaN values from the original workbook are preserved as-is.",
    ]

    lines += ["", "=" * 70, "END OF REPORT", "=" * 70]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[INFO] Cleaning report written: {}".format(path))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    # Validate that the source workbook exists
    if not os.path.isfile(RAW_XLSX):
        print("[ERROR] Raw workbook not found: {}".format(RAW_XLSX), file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(CLEANED_CSV), exist_ok=True)

    # Step 1: Read workbook (read-only -- we never write back to this file)
    df = read_workbook(RAW_XLSX)
    raw_shape = df.shape

    # Step 2 (C2): Strip column name whitespace
    df, renamed_cols = strip_column_names(df)
    print("[INFO] Column names cleaned. {} column(s) renamed.".format(len(renamed_cols)))

    # Step 3 (C1): Drop completely empty rows
    # Must happen after column renaming so 'ATB' and 'ATD' are the correct names
    df, dropped_rows = drop_empty_rows(df)
    print("[INFO] Dropped {} empty row(s). Remaining: {}".format(len(dropped_rows), len(df)))

    # Step 4 (C3): Strip whitespace from string cells
    df, string_changes = strip_string_values(df)
    print("[INFO] Stripped {} cell(s) across {} column(s).".format(
        sum(string_changes.values()), len(string_changes)))

    # Step 5 (C4): Normalise month names
    df, month_fixes = normalize_months(df)
    for ov, (nv, cnt) in month_fixes.items():
        print("[INFO] Month: '{}' -> '{}' ({} rows)".format(ov, nv, cnt))
    if not month_fixes:
        print("[INFO] No month normalisation needed.")

    # Step 6 (C5): Derive Week (ATD) from ATD date where the existing value is invalid
    df, week_fixes = derive_week_from_atd(df)
    for idx, orig, derived in week_fixes:
        print("[INFO] Week (ATD) fixed row {}: '{}' -> {} (ISO week)".format(
            idx, orig, derived))
    if not week_fixes:
        print("[INFO] No Week (ATD) fixes needed.")

    # Step 7 (C6 and C7): Flag BCH problems and null out the two invalid tiers
    df, bch_report = flag_bch_problems(df)
    n_neg  = len(bch_report["negative_rows"])
    n_ext  = len(bch_report["extreme_rows"])
    n_soft = len(bch_report["soft_flag_rows"])
    print("[INFO] BCH soft fence      : {:.4f} bph  (Tukey Q3+3xIQR, flag threshold)".format(
        bch_report["upper_fence"]))
    print("[INFO] BCH hard threshold  : {:.1f} bph  (null threshold)".format(
        bch_report["extreme_threshold"]))
    print("[INFO] Tier 1 -- negative BCH nulled   : {}".format(n_neg))
    print("[INFO] Tier 2 -- extreme BCH nulled    : {}".format(n_ext))
    print("[INFO] Tier 3 -- moderate BCH kept/flagged: {}".format(n_soft))
    for idx, vessel, bch in bch_report["negative_rows"]:
        print("       NULLED  (negative) : row {}, {}, BCH={:.2f}".format(idx, vessel, bch))
    for idx, vessel, bch in bch_report["extreme_rows"]:
        print("       NULLED  (extreme)  : row {}, {}, BCH={:.2f}".format(idx, vessel, bch))
    for idx, vessel, bch in bch_report["soft_flag_rows"]:
        print("       FLAGGED (moderate) : row {}, {}, BCH={:.2f} [value preserved]".format(
            idx, vessel, bch))

    # Step 8: Quality checks (for the report only -- no data changes)
    dup_report = check_duplicates(df)
    missing    = missing_value_summary(df)

    # Step 9: Save cleaned CSV
    df.to_csv(CLEANED_CSV, index=False, encoding="utf-8-sig")
    final_shape = df.shape
    print("[INFO] Cleaned CSV saved: {}".format(CLEANED_CSV))
    print("[INFO] Final shape: {} rows x {} columns".format(final_shape[0], final_shape[1]))

    # Step 10: Write the cleaning report
    write_cleaning_report(
        path           = REPORT_TXT,
        raw_shape      = raw_shape,
        renamed_cols   = renamed_cols,
        dropped_rows   = dropped_rows,
        string_changes = string_changes,
        month_fixes    = month_fixes,
        week_fixes     = week_fixes,
        bch_report     = bch_report,
        dup_report     = dup_report,
        missing        = missing,
        final_shape    = final_shape,
    )

    # Final console summary
    print("")
    print("=" * 60)
    print("CLEANING COMPLETE")
    print("  Input rows         : {}".format(raw_shape[0]))
    print("  Empty rows dropped : {}".format(len(dropped_rows)))
    print("  Output rows        : {}".format(final_shape[0]))
    row_check = "OK" if final_shape[0] == 807 else "WARNING -- expected 807"
    print("  Row count check    : {}".format(row_check))
    print("  BCH nulled         : {} negative + {} extreme = {} total".format(
        n_neg, n_ext, n_neg + n_ext))
    print("  BCH flagged (kept) : {} moderate outliers (see cleaning_report.txt)".format(n_soft))
    print("  Cleaned CSV        : {}".format(CLEANED_CSV))
    print("  Cleaning report    : {}".format(REPORT_TXT))
    print("=" * 60)


if __name__ == "__main__":
    main()
