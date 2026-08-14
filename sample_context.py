"""sample_context.py - read-only sampler of the `context` column.

Step 1 of making the Library trustworthy: before designing any re-tagging rule,
look at what the verbatim cell and header text actually says.

Reads only. Opens full_v2.duckdb read_only, so it is safe to run while the
ask-avia service is running. Writes nothing to the store.

Run on the WORKSTATION (Donatello).

    .venv\\Scripts\\python.exe sample_context.py --out E:\\Avia\\Extract\\diag

Scope defaults to entity NCL, metric rev_aero, which is the scope the earlier
diagnosis measured (521,875 raw points, 68,870 canonical, values -0.03 to
42,660 under one code).

Copyright Avia Solutions Limited. All rights reserved.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import duckdb

DEFAULT_STORE = os.environ.get(
    "ASKAVIA_STORE_PATH", r"E:\Avia\Extract\full\out\full_v2.duckdb"
)

# Leading "[000s] " style scale cue, then the nearest label, then "||", then the
# header stack and title block. Derived from observed records, e.g.
#   "[000s] Total Aero Rev incl Aug update || [000s] [000s] Updated for Aug 11
#    MAp comments Newcastle Airport: Aeronautical Revenue Progression"
LABEL_SQL = (
    "trim(regexp_replace(split_part(context, '||', 1), '^\\s*\\[[^\\]]*\\]\\s*', ''))"
)
SCALE_SQL = "regexp_extract(context, '^\\s*\\[([^\\]]*)\\]', 1)"


def canonical_clause(con: duckdb.DuckDBPyConnection) -> str:
    """Uncorrelated semi-join. NOT an EXISTS: a correlated outer reference binds
    to the inner table and the filter silently does nothing (the bug of 14 Aug)."""
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "doc_canonical" not in tables:
        return ""
    return (
        ' AND "source_file" IN '
        "(SELECT source_file FROM doc_canonical WHERE is_canonical)"
    )


def write_tsv(path: Path, cols, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in rows:
            fh.write(
                "\t".join("" if v is None else str(v).replace("\t", " ") for v in r)
                + "\n"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--out", default=r"E:\Avia\Extract\diag")
    ap.add_argument("--entity", default="NCL")
    ap.add_argument("--metric", default="rev_aero")
    ap.add_argument("--top", type=int, default=400)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.store, read_only=True)

    canon = canonical_clause(con)
    scope = f"entity_id = ? {canon}"
    log = []

    def say(s: str = "") -> None:
        print(s)
        log.append(s)

    say(f"store       : {args.store}")
    say(f"scope       : entity_id={args.entity} metric_code={args.metric}")
    say(f"canonical   : {'applied' if canon else 'doc_canonical ABSENT - not applied'}")
    say()

    # ---------------------------------------------------------------- 1. shape
    n_all, n_metric, n_blank = con.execute(
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE metric_code = ?),
               count(*) FILTER (WHERE metric_code IS NULL OR trim(metric_code) = '')
        FROM ask_points WHERE {scope}
        """,
        [args.metric, args.entity],
    ).fetchone()
    say(f"points at entity      : {n_all:,}")
    say(f"  tagged {args.metric:<14}: {n_metric:,}")
    say(f"  no metric code      : {n_blank:,} ({100.0 * n_blank / max(n_all, 1):.1f}%)")
    say()

    # -------------------------------------- 2. context fill and parse coverage
    row = con.execute(
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE context IS NULL OR trim(context) = ''),
               count(*) FILTER (WHERE context LIKE '%||%'),
               count(*) FILTER (WHERE {SCALE_SQL} <> '')
        FROM ask_points WHERE {scope} AND metric_code = ?
        """,
        [args.entity, args.metric],
    ).fetchone()
    say("context column, within the tagged scope")
    say(f"  rows                : {row[0]:,}")
    say(f"  context empty       : {row[1]:,}")
    say(f"  has '||' separator  : {row[2]:,}")
    say(f"  has leading [scale] : {row[3]:,}")
    say()

    # ------------------------------------------------- 3. by label (the payload)
    cols = [
        "label",
        "scale_cue",
        "unit",
        "n",
        "n_distinct_values",
        "n_negative",
        "vmin",
        "p50",
        "vmax",
    ]
    rows = con.execute(
        f"""
        SELECT {LABEL_SQL} AS label,
               {SCALE_SQL}  AS scale_cue,
               coalesce(nullif(trim(unit), ''), '(blank)') AS unit,
               count(*)                    AS n,
               count(DISTINCT value_num)   AS n_distinct_values,
               count(*) FILTER (WHERE value_num < 0) AS n_negative,
               round(min(value_num), 3)    AS vmin,
               round(median(value_num), 3) AS p50,
               round(max(value_num), 3)    AS vmax
        FROM ask_points
        WHERE {scope} AND metric_code = ?
        GROUP BY 1, 2, 3
        ORDER BY n DESC
        LIMIT {args.top}
        """,
        [args.entity, args.metric],
    ).fetchall()
    write_tsv(out / "context_by_label.tsv", cols, rows)
    say(f"wrote context_by_label.tsv  ({len(rows)} groups)")
    say()
    say("top 40 label / scale / unit groups")
    say(
        f"{'n':>9}  {'unit':<8} {'scale':<8} {'min':>12} {'median':>12} {'max':>12}  label"
    )
    for lab, sc, un, n, _nd, _neg, vmin, p50, vmax in rows[:40]:
        say(
            f"{n:>9,}  {un:<8.8} {(sc or ''):<8.8} {vmin:>12} {p50:>12} {vmax:>12}  {(lab or '')[:90]}"
        )
    say()

    # ------------------------------------------- 4. verbatim contexts, unparsed
    cols2 = ["n", "unit", "vmin", "p50", "vmax", "context"]
    rows2 = con.execute(
        f"""
        SELECT count(*) AS n,
               coalesce(nullif(trim(unit), ''), '(blank)') AS unit,
               round(min(value_num), 3), round(median(value_num), 3),
               round(max(value_num), 3),
               context
        FROM ask_points
        WHERE {scope} AND metric_code = ?
        GROUP BY unit, context
        ORDER BY n DESC
        LIMIT {args.top}
        """,
        [args.entity, args.metric],
    ).fetchall()
    write_tsv(
        out / "context_verbatim.tsv",
        cols2,
        [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows2],
    )
    say(f"wrote context_verbatim.tsv  ({len(rows2)} groups)")

    # ------------------ 5. the per-pax band: where the right answer is hiding
    say()
    say("values in the per-pax band (0 < v < 50), by label")
    band = con.execute(
        f"""
        SELECT {LABEL_SQL} AS label,
               coalesce(nullif(trim(unit), ''), '(blank)') AS unit,
               count(*) AS n, round(median(value_num), 3) AS p50
        FROM ask_points
        WHERE {scope} AND metric_code = ? AND value_num > 0 AND value_num < 50
        GROUP BY 1, 2 ORDER BY n DESC LIMIT 40
        """,
        [args.entity, args.metric],
    ).fetchall()
    for lab, un, n, p50 in band:
        say(f"{n:>9,}  {un:<8.8} {p50:>10}  {(lab or '')[:90]}")
    write_tsv(out / "context_perpax_band.tsv", ["label", "unit", "n", "p50"], band)

    # ------------------ 6. untagged points: what the re-tag could reclaim
    say()
    say("untagged points whose context mentions aero, top 40 labels")
    untag = con.execute(
        f"""
        SELECT {LABEL_SQL} AS label,
               coalesce(nullif(trim(unit), ''), '(blank)') AS unit,
               count(*) AS n,
               round(min(value_num), 3), round(median(value_num), 3),
               round(max(value_num), 3)
        FROM ask_points
        WHERE {scope}
          AND (metric_code IS NULL OR trim(metric_code) = '')
          AND lower(context) LIKE '%aero%'
        GROUP BY 1, 2 ORDER BY n DESC LIMIT 40
        """,
        [args.entity],
    ).fetchall()
    for lab, un, n, vmin, p50, vmax in untag:
        say(f"{n:>9,}  {un:<8.8} {vmin:>12} {p50:>12} {vmax:>12}  {(lab or '')[:80]}")
    write_tsv(
        out / "context_untagged_aero.tsv",
        ["label", "unit", "n", "vmin", "p50", "vmax"],
        untag,
    )

    (out / "sample_context_report.txt").write_text("\n".join(log), encoding="utf-8")
    con.close()
    print(f"\nreport written to {out / 'sample_context_report.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
