"""How much of the Library is repetition?

The question this answers. A summary of aeronautical revenue at Newcastle returned
521,875 points. Avia does not hold half a million facts about that. The store holds every
cell of every iteration of every model, so the same figure appears many times over. Before
designing any rule for choosing an authoritative point, measure how much of the volume is
genuine variety and how much is the same number repeated.

If 521,875 rows collapse to a few hundred distinct (year, value, unit) triples, the fix is
deduplication and the problem is largely presentational. If they collapse to tens of
thousands, the figures genuinely disagree across documents and the fix is a precedence
rule: which document, which vintage, which status wins.

Read-only. Touches nothing. Run on the WORKSTATION.

    cd C:\\src\\ask-avia
    .venv\\Scripts\\python.exe diag_distinct.py
"""

from __future__ import annotations

import os
import sys

STORE = os.environ.get(
    "ASKAVIA_STORE_PATH", r"E:\Avia\Extract\full\out\full_v2.duckdb"
)

# The scopes to measure. Newcastle aeronautical revenue is the case that prompted this;
# the others are there so we do not generalise from one metric.
SCOPES = [
    ("rev_aero", "Newcastle"),
    ("pax_total", "Newcastle"),
    ("rev_total", "Newcastle"),
    ("opex_total", "Newcastle"),
    ("rev_aero", "Bristol"),
]


def main() -> None:
    try:
        import duckdb
    except ImportError:
        sys.exit("duckdb is not installed in this interpreter. Use the ask-avia venv.")

    con = duckdb.connect(STORE, read_only=True)
    print(f"store: {STORE}\n")

    header = (
        f"{'metric':<14} {'entity':<12} {'rows':>10} {'distinct':>9} {'files':>7} "
        f"{'docs':>6} {'repeat':>7}"
    )
    print(header)
    print("-" * len(header))

    for metric, entity in SCOPES:
        sql = """
            SELECT
              COUNT(*) AS rows,
              COUNT(DISTINCT
                COALESCE(CAST(year AS VARCHAR), '') || '|' ||
                COALESCE(CAST(value_num AS VARCHAR), '') || '|' ||
                COALESCE(CAST(unit AS VARCHAR), '')
              ) AS distinct_values,
              COUNT(DISTINCT source_file) AS files,
              COUNT(DISTINCT
                array_to_string(list_slice(string_split(
                  COALESCE(CAST(source_file AS VARCHAR), ''), '/'), 1, 5), '/')
              ) AS folders
            FROM ask_points
            WHERE metric_code = ?
              AND (entity ILIKE ? OR entity_id ILIKE ?)
        """
        rows, distinct_values, files, folders = con.execute(
            sql, [metric, f"%{entity}%", f"%{entity}%"]
        ).fetchone()
        ratio = (rows / distinct_values) if distinct_values else 0
        print(
            f"{metric:<14} {entity:<12} {rows:>10,} {distinct_values:>9,} "
            f"{files:>7,} {folders:>6,} {ratio:>6.0f}x"
        )

    # The second question: is the repetition WITHIN documents (the same number written
    # into many cells of one model) or ACROSS documents (many models each restating it)?
    # The answer changes the fix. Within-document repetition is deduplication; across
    # document repetition needs a precedence rule.
    print("\nrev_aero / Newcastle, by source document (top 10 by row count):")
    sql_docs = """
        SELECT source_file,
               COUNT(*) AS rows,
               COUNT(DISTINCT
                 COALESCE(CAST(year AS VARCHAR), '') || '|' ||
                 COALESCE(CAST(value_num AS VARCHAR), '') || '|' ||
                 COALESCE(CAST(unit AS VARCHAR), '')
               ) AS distinct_values
        FROM ask_points
        WHERE metric_code = 'rev_aero'
          AND (entity ILIKE '%Newcastle%' OR entity_id ILIKE '%Newcastle%')
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """
    for source_file, rows, distinct_values in con.execute(sql_docs).fetchall():
        ratio = (rows / distinct_values) if distinct_values else 0
        print(f"  {rows:>8,} rows / {distinct_values:>6,} distinct ({ratio:>4.0f}x)  {source_file}")

    # The third question: for ONE year, how far apart are the values? If a single year in
    # a single scope spans orders of magnitude, the metric tag is conflating different
    # measures (a total, a rate and a per-pax factor all tagged rev_aero), which is the
    # defect we already suspect and this would size it.
    print("\nrev_aero / Newcastle / 2019, value spread by unit:")
    sql_spread = """
        SELECT COALESCE(NULLIF(CAST(unit AS VARCHAR), ''), '(blank)') AS unit,
               COUNT(*) AS n,
               MIN(value_num) AS min_v,
               MEDIAN(value_num) AS median_v,
               MAX(value_num) AS max_v
        FROM ask_points
        WHERE metric_code = 'rev_aero'
          AND (entity ILIKE '%Newcastle%' OR entity_id ILIKE '%Newcastle%')
          AND TRY_CAST(year AS INTEGER) = 2019
        GROUP BY 1 ORDER BY 2 DESC
    """
    for unit, n, min_v, median_v, max_v in con.execute(sql_spread).fetchall():
        print(f"  {unit:<10} n={n:>8,}  min={min_v:>18,.2f}  median={median_v:>18,.2f}  max={max_v:>18,.2f}")

    con.close()


if __name__ == "__main__":
    main()
