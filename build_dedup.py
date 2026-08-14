"""Deduplicate the Library by DOCUMENT, not by point. Additive; nothing is deleted.

The problem, measured on 14 August 2026. Aeronautical revenue at Newcastle returns 521,875
points against 14,113 distinct (year, value, unit) triples: 37x repetition. Within any one
document the rows are all distinct, so the repetition is entirely ACROSS files. The cause
is visible in the paths: every new project copied the previous project's folder in
wholesale, so the same workbook is harvested three or four times over.

    Non Aero Modelling and Analysis (AR) - 31Aug2012.xlsx
      /Shared/Archive/2012/AMP Capital - Newcastle/...
      /Shared/Archive/2022/IMCO - AMP Airports/OLD PROJECT FILES/AMP Capital - Newcastle/...
      /Shared/Archive/2024/IGNEO - Newcastle/Data and Analysis/Prior Projects/Old AMP Project 2012/...

Counted as three documents, one workbook becomes three times the evidence, and a figure
that was stated once looks corroborated. That is worse than merely wasteful.

WHAT THIS DOES. It fingerprints every document from the points ALREADY extracted from it,
groups documents by fingerprint, and nominates one canonical path per group. It writes two
new tables and touches nothing that exists. No re-harvest, no re-resolve.

    doc_fingerprint   one row per source_file: point count and content hashes
    doc_canonical     one row per source_file: its fingerprint group, whether it is the
                      canonical copy, which path is canonical, and how many copies exist

THE FINGERPRINT. An order-independent composite over each document's (cell, value) pairs:
COUNT, SUM(hash) and BIT_XOR(hash). Deliberately not string_agg, which would materialise
every document's full cell list and fall over on a table this size. Two documents match
only if they hold the same number of points and both hash aggregates agree, which for
different content is vanishingly unlikely.

Note that the fingerprint ignores the PATH. That is the point: copies differ only by where
they sit.

CHOOSING THE CANONICAL COPY. Lowest penalty, then shallowest path, then shortest, then
alphabetical. The penalty marks the tells of a copy rather than an original: a path
containing "old project files", "prior projects", "/old/", "copy of" or "backup". Copies
get buried deeper inside later projects, so depth is a good second signal.

Nothing is discarded. Non-canonical paths stay in the store and stay queryable; they are
simply no longer counted as separate evidence.

READ THIS BEFORE RUNNING. DuckDB allows either one read-write connection or several
read-only ones, and the ask-avia service holds the store read-only. The service must be
stopped for this to run, and started again afterwards:

    WORKSTATION (Donatello), elevated PowerShell:
      Stop-Service ask-avia
      cd C:\\src\\ask-avia
      .venv\\Scripts\\python.exe build_dedup.py
      Start-Service ask-avia
"""

from __future__ import annotations

import os
import sys
import time

STORE = os.environ.get("ASKAVIA_STORE_PATH", r"E:\Avia\Extract\full\out\full_v2.duckdb")

# The base points table. core_points is the physical table; ask_points is the view over it
# that adds data_class, and a view cannot be grouped any more cheaply than its base.
POINTS_TABLE = "core_points"

# The columns the fingerprint is built from. `location` is the in-file cell locator, NOT a
# folder path; `value_num` is the numeric value. Together they describe what a document
# actually says, independently of where the file happens to sit.
CELL_COLUMN = "location"
VALUE_COLUMN = "value_num"
PATH_COLUMN = "source_file"

# Path fragments that mark a copy rather than an original. Matched case-insensitively.
COPY_MARKERS = (
    "%old project files%",
    "%prior projects%",
    "%/old/%",
    "%copy of%",
    "%backup%",
    "%- copy%",
)


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def main() -> None:
    try:
        import duckdb
    except ImportError:
        sys.exit("duckdb is not installed in this interpreter. Use the ask-avia venv.")

    if not os.path.exists(STORE):
        sys.exit(f"store not found: {STORE}")

    _log(f"opening {STORE} read-write")
    try:
        con = duckdb.connect(STORE, read_only=False)
    except Exception as exc:  # noqa: BLE001 - the reason must reach the operator
        sys.exit(
            f"could not open the store read-write: {type(exc).__name__}: {exc}\n"
            f"The ask-avia service holds it read-only. Stop-Service ask-avia first, "
            f"then run this again, then Start-Service ask-avia."
        )

    cell = f'COALESCE(CAST("{CELL_COLUMN}" AS VARCHAR), \'\')'
    value = f'COALESCE(CAST("{VALUE_COLUMN}" AS VARCHAR), \'\')'
    point_hash = f"hash({cell} || '|' || {value})"

    # ---------------------------------------------------------------- fingerprints ----
    _log("pass 1 of 2: fingerprinting every document (one scan of the points table)")
    started = time.time()
    con.execute("DROP TABLE IF EXISTS doc_fingerprint")
    con.execute(
        f"""
        CREATE TABLE doc_fingerprint AS
        SELECT
          "{PATH_COLUMN}"                    AS source_file,
          COUNT(*)                           AS n_points,
          CAST(SUM({point_hash}) AS HUGEINT) AS h_sum,
          BIT_XOR({point_hash})              AS h_xor
        FROM "{POINTS_TABLE}"
        WHERE "{PATH_COLUMN}" IS NOT NULL AND CAST("{PATH_COLUMN}" AS VARCHAR) <> ''
        GROUP BY 1
        """
    )
    docs = con.execute("SELECT COUNT(*) FROM doc_fingerprint").fetchone()[0]
    _log(f"pass 1 done in {time.time() - started:,.0f}s: {docs:,} documents fingerprinted")

    # ------------------------------------------------------------------- canonical ----
    _log("pass 2 of 2: grouping identical documents and choosing a canonical path")
    started = time.time()
    penalty = " + ".join(
        f"CASE WHEN lower(source_file) LIKE '{m}' THEN 1 ELSE 0 END" for m in COPY_MARKERS
    )
    con.execute("DROP TABLE IF EXISTS doc_canonical")
    con.execute(
        f"""
        CREATE TABLE doc_canonical AS
        WITH scored AS (
          SELECT
            source_file, n_points, h_sum, h_xor,
            length(source_file) - length(replace(source_file, '/', '')) AS depth,
            {penalty} AS copy_penalty
          FROM doc_fingerprint
        ),
        ranked AS (
          SELECT
            *,
            COUNT(*) OVER w                                     AS copies,
            row_number() OVER (PARTITION BY n_points, h_sum, h_xor
                               ORDER BY copy_penalty, depth, length(source_file),
                                        source_file)            AS rn,
            first_value(source_file) OVER (PARTITION BY n_points, h_sum, h_xor
                                           ORDER BY copy_penalty, depth,
                                                    length(source_file), source_file)
                                                                AS canonical_source_file
          FROM scored
          WINDOW w AS (PARTITION BY n_points, h_sum, h_xor)
        )
        SELECT
          source_file,
          canonical_source_file,
          (rn = 1)   AS is_canonical,
          copies,
          n_points,
          copy_penalty,
          depth
        FROM ranked
        """
    )
    _log(f"pass 2 done in {time.time() - started:,.0f}s")

    # ----------------------------------------------------------------------- report ----
    total_docs, canonical_docs, duplicate_docs = con.execute(
        """
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE is_canonical),
               COUNT(*) FILTER (WHERE NOT is_canonical)
        FROM doc_canonical
        """
    ).fetchone()
    total_points, canonical_points = con.execute(
        """
        SELECT SUM(n_points),
               SUM(n_points) FILTER (WHERE is_canonical)
        FROM doc_canonical
        """
    ).fetchone()

    print()
    print("=" * 72)
    print("DOCUMENT DEDUPLICATION")
    print("=" * 72)
    print(f"  documents fingerprinted   {total_docs:>14,}")
    print(f"  canonical documents       {canonical_docs:>14,}")
    print(f"  duplicate copies          {duplicate_docs:>14,}")
    if total_points:
        share = 100.0 * (total_points - canonical_points) / total_points
        print(f"  points held               {total_points:>14,}")
        print(f"  points on canonical docs  {canonical_points:>14,}")
        print(f"  points that were copies   {total_points - canonical_points:>14,}  ({share:.1f}%)")
        if canonical_points:
            print(f"  reduction factor          {total_points / canonical_points:>13,.1f}x")

    print("\n  most-copied documents:")
    rows = con.execute(
        """
        SELECT canonical_source_file, copies, n_points
        FROM doc_canonical WHERE is_canonical AND copies > 1
        ORDER BY copies DESC, n_points DESC LIMIT 10
        """
    ).fetchall()
    for path, copies, n_points in rows:
        print(f"    {copies:>3} copies, {n_points:>8,} points  {path}")
    if not rows:
        print("    none: every document is unique, which would contradict the diagnosis")

    con.close()
    print("\nWritten: doc_fingerprint, doc_canonical. Nothing existing was modified.")
    print("Start the service again: Start-Service ask-avia")


if __name__ == "__main__":
    main()
