"""How much repetition survives document deduplication?

build_dedup.py removed exact copies: 80,704 duplicate documents, 29.5% of all points. But
the copies it caught are mostly tiny files, so the question is what that does to the case
that prompted the work. Aeronautical revenue at Newcastle returned 521,875 points against
14,113 distinct values, 37x. If most of that 37x survives, the remaining repetition is
RESTATEMENT across genuinely different documents, and no fingerprint will ever merge it:
it needs a precedence rule instead.

This measures the same five scopes before and after, so the answer is a number rather than
an argument.

Read-only. Run on the WORKSTATION; safe while the service is running.

    cd C:\\src\\ask-avia
    .venv\\Scripts\\python.exe diag_after_dedup.py
"""

from __future__ import annotations

import os
import sys

STORE = os.environ.get("ASKAVIA_STORE_PATH", r"E:\Avia\Extract\full\out\full_v2.duckdb")

SCOPES = [
    ("rev_aero", "Newcastle"),
    ("pax_total", "Newcastle"),
    ("rev_total", "Newcastle"),
    ("opex_total", "Newcastle"),
    ("rev_aero", "Bristol"),
]

DISTINCT_KEY = """
    COALESCE(CAST(year AS VARCHAR), '') || '|' ||
    COALESCE(CAST(value_num AS VARCHAR), '') || '|' ||
    COALESCE(CAST(unit AS VARCHAR), '')
"""


def main() -> None:
    try:
        import duckdb
    except ImportError:
        sys.exit("duckdb is not installed in this interpreter. Use the ask-avia venv.")

    con = duckdb.connect(STORE, read_only=True)

    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}
    if "doc_canonical" not in tables:
        sys.exit("doc_canonical not found. Run build_dedup.py first.")

    header = (
        f"{'metric':<12} {'entity':<11} {'rows':>10} {'canon rows':>11} "
        f"{'distinct':>9} {'canon dist':>11} {'repeat':>7} {'canon rpt':>10}"
    )
    print(header)
    print("-" * len(header))

    for metric, entity in SCOPES:
        sql = f"""
            WITH scope AS (
              SELECT p.year, p.value_num, p.unit, p.source_file,
                     COALESCE(d.is_canonical, TRUE) AS is_canonical
              FROM ask_points p
              LEFT JOIN doc_canonical d ON d.source_file = p.source_file
              WHERE p.metric_code = ?
                AND (p.entity ILIKE ? OR p.entity_id ILIKE ?)
            )
            SELECT
              COUNT(*),
              COUNT(*) FILTER (WHERE is_canonical),
              COUNT(DISTINCT {DISTINCT_KEY}),
              COUNT(DISTINCT CASE WHEN is_canonical THEN {DISTINCT_KEY} END)
            FROM scope
        """
        rows, canon_rows, distinct_v, canon_distinct = con.execute(
            sql, [metric, f"%{entity}%", f"%{entity}%"]
        ).fetchone()
        rpt = rows / distinct_v if distinct_v else 0
        crpt = canon_rows / canon_distinct if canon_distinct else 0
        print(
            f"{metric:<12} {entity:<11} {rows:>10,} {canon_rows:>11,} "
            f"{distinct_v:>9,} {canon_distinct:>11,} {rpt:>6.0f}x {crpt:>9.0f}x"
        )

    # If restatement is the residual cause, then within the canonical set a single
    # (metric, entity, year, value, unit) should still appear across many DIFFERENT
    # documents. Count how many documents state the most-restated figures.
    print("\nrev_aero / Newcastle, canonical documents only: most-restated figures")
    sql_restated = f"""
        SELECT p.year, p.value_num, COALESCE(NULLIF(CAST(p.unit AS VARCHAR), ''), '(blank)') AS unit,
               COUNT(*) AS points, COUNT(DISTINCT p.source_file) AS documents
        FROM ask_points p
        JOIN doc_canonical d ON d.source_file = p.source_file AND d.is_canonical
        WHERE p.metric_code = 'rev_aero'
          AND (p.entity ILIKE '%Newcastle%' OR p.entity_id ILIKE '%Newcastle%')
        GROUP BY 1, 2, 3
        ORDER BY documents DESC, points DESC
        LIMIT 10
    """
    for year, value, unit, points, documents in con.execute(sql_restated).fetchall():
        print(f"  year={str(year):<8} value={value:>16,.2f} {unit:<9} "
              f"stated in {documents:>5,} documents ({points:,} points)")

    con.close()


if __name__ == "__main__":
    main()
