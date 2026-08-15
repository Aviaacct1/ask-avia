"""verify_tags.py - prove the built re-tag is correct and measure what it costs.

Read-only. Safe to run while the ask-avia service is running.

The store-wide build wrote context_tag (80.4m rows) and the ask_points_v2 view
but its report was lost to the reboot, so this re-establishes three things:

  1. CORRECTNESS. The Newcastle gold figure must still read circa GBP 5.17
     through the BUILT view, not just through a dry-run temp table.
  2. THE VETO. An out-of-band point must have lost its metric_code_v2 in the
     view as built. A filter that reports itself applied and does nothing is
     the worst failure this store can produce, and we have shipped one before.
  3. COST. context_tag is 11% of the point count, not the small map that was
     intended, so every query through ask_points_v2 joins 722m rows to 80m on
     a long string. Each check below is timed.

Run on the WORKSTATION (Donatello).

    .venv\\Scripts\\python.exe verify_tags.py
    .venv\\Scripts\\python.exe verify_tags.py --entity BRS

Copyright Avia Solutions Limited. All rights reserved.
"""

from __future__ import annotations

import argparse
import os
import time

import duckdb

DEFAULT_STORE = os.environ.get(
    "ASKAVIA_STORE_PATH", r"E:\Avia\Extract\full\out\full_v2.duckdb"
)


def timed(con, label, sql, params=None):
    t0 = time.time()
    rows = con.execute(sql, params or []).fetchall()
    print(f"  [{time.time() - t0:6.1f}s] {label}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--entity", default="NCL")
    args = ap.parse_args()

    if not os.path.exists(args.store):
        raise SystemExit(
            f"\nNo store at {args.store}\n"
            "This runs on the WORKSTATION (Donatello). Check the prompt reads\n"
            "[Donatello]: before you run it.\n"
        )
    con = duckdb.connect(args.store, read_only=True)
    held = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    for needed in ("ask_points", "context_tag", "ask_points_v2"):
        if needed not in held:
            raise SystemExit(f"\n{args.store} has no {needed}. Tables: {sorted(held)}\n")
    canon = (
        ' AND "source_file" IN '
        "(SELECT source_file FROM doc_canonical WHERE is_canonical)"
    )

    print(f"store  : {args.store}")
    print(f"entity : {args.entity}\n")

    print("shape of what was built")
    for label, sql in [
        ("context_tag rows", "SELECT count(*) FROM context_tag"),
        ("  carrying a code", "SELECT count(*) FROM context_tag WHERE metric_code_v2 IS NOT NULL"),
        ("  from row label", "SELECT count(*) FROM context_tag WHERE label_source = 'row_label'"),
        ("  from sheet", "SELECT count(*) FROM context_tag WHERE label_source = 'sheet_fallback'"),
        ("  no noun found", "SELECT count(*) FROM context_tag WHERE label_source = 'none'"),
    ]:
        print(f"{label:<20}: {timed(con, label.strip(), sql)[0][0]:,}")

    print("\ncodes the built map issues, by distinct context")
    for code, n in timed(
        con,
        "code distribution",
        "SELECT coalesce(metric_code_v2, '(none)'), count(*) n FROM context_tag "
        "GROUP BY 1 ORDER BY n DESC",
    ):
        print(f"  {code:<24} {n:>12,}")

    # ---------------------------------------------------------------- 1. gold
    print(f"\nGOLD CHECK through the BUILT view: aero revenue per pax, {args.entity}")
    rows = timed(
        con,
        "gold check",
        f"""SELECT year, count(*), round(min(value_num), 2),
                   round(median(value_num), 2), round(max(value_num), 2)
            FROM ask_points_v2
            WHERE entity_id = ? AND metric_code_v2 = 'rev_aero_per_pax'
              AND label_source = 'row_label' AND year BETWEEN 2005 AND 2015
              {canon}
            GROUP BY 1 ORDER BY 1""",
        [args.entity],
    )
    for yr, n, lo, p50, hi in rows:
        print(f"  {yr}  n={n:>7,}  min {lo:>8}  median {p50:>8}  max {hi:>8}")
    print("  (Newcastle 2009 should read circa GBP 5.17)")

    # ---------------------------------------------------------------- 2. veto
    print("\nTHE VETO: does an out-of-band point actually lose its code")
    got = timed(
        con,
        "veto check",
        f"""SELECT count(*) FILTER (WHERE metric_code_v2 IS NOT NULL),
                   count(*) FILTER (WHERE metric_code_v2_raw IS NOT NULL),
                   count(*)
            FROM ask_points_v2
            WHERE entity_id = ? AND magnitude_check <> 'ok' {canon}""",
        [args.entity],
    )[0]
    print(f"  out-of-band points at {args.entity} : {got[2]:,}")
    print(f"  still carrying a code          : {got[0]:,}   <- must be 0")
    print(f"  rule's original claim retained : {got[1]:,}   <- must be > 0")
    if got[0] != 0:
        print("  FAIL: the veto is not filtering. Do not use these figures.")
    elif got[1] == 0:
        print("  FAIL: the raw claim is missing, so the veto cannot be audited.")
    else:
        print("  PASS")

    # ------------------------------------------------- 3. before and after
    print(f"\nWHAT AN ANSWER LOOKS LIKE NOW, {args.entity} aero revenue per pax 2009")
    before = timed(
        con,
        "old code, one metric one year",
        f"""SELECT count(*), round(min(value_num), 2), round(median(value_num), 2),
                   round(max(value_num), 2)
            FROM ask_points
            WHERE entity_id = ? AND metric_code = 'rev_aero' AND year = 2009 {canon}""",
        [args.entity],
    )[0]
    after = timed(
        con,
        "new code, same question",
        f"""SELECT count(*), round(min(value_num), 2), round(median(value_num), 2),
                   round(max(value_num), 2)
            FROM ask_points_v2
            WHERE entity_id = ? AND metric_code_v2 = 'rev_aero_per_pax'
              AND label_source = 'row_label' AND year = 2009 {canon}""",
        [args.entity],
    )[0]
    print(f"  before  rev_aero        n={before[0]:>8,}  {before[1]} .. {before[2]} .. {before[3]}")
    print(f"  after   rev_aero_per_pax n={after[0]:>8,}  {after[1]} .. {after[2]} .. {after[3]}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
