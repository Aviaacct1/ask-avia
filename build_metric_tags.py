"""build_metric_tags.py - re-tag metrics from the stored `context` column.

Step 1 of making the Library trustworthy. Additive: writes one table and one
view, changes nothing that exists. No re-harvest, no re-resolve. Same pattern as
harvest_manifest and doc_canonical.

WHY. metric_code was assigned from the sheet or section header, not the row
label. On a sheet called "Aero Revenue", every row on it took metric_code
rev_aero: passenger counts, CPI, GDP, yields, segment splits and the total
alike. At Newcastle the largest single group inside rev_aero (9,764 points,
unit '000, values 272-4,906) is the passenger count.

THE PARSE IS EXACT, NOT A GUESS. context is structured:

    [scale cue] <row label> <sheet name> || <header stack> <section> <title>

and the sheet name is held separately in `location` (sheet=NAME!CELL), so
stripping the known sheet name off the tail of the label recovers the true row
label. "Passengers Aero Revenue" on sheet "Aero Revenue" -> "Passengers".

TWO INDEPENDENT FIELDS come out of it, and both are needed:
  measure_kind   what type of number it is (level, rate, growth, index, ...)
  metric_code_v2 which measure it is, NULL wherever the point must never
                 answer a money question, so filtering on it is safe by
                 construction rather than by the caller remembering to.

The rule table below is the single definition. It generates the SQL for the
dry run and for the build, so what is inspected and what is written cannot
diverge.

Run on the WORKSTATION (Donatello).

    dry run, safe while the service runs:
        .venv\\Scripts\\python.exe build_metric_tags.py --entity NCL
    store-wide dry run:
        .venv\\Scripts\\python.exe build_metric_tags.py --all
    build (STOP the ask-avia service first: DuckDB is single-writer):
        .venv\\Scripts\\python.exe build_metric_tags.py --all --build

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

# --------------------------------------------------------------------------
# The rule table. Ordered, first match wins. Patterns are RE2 (DuckDB), so no
# lookarounds: negation is done by ordering, not by lookbehind. Matched against
# the lower-cased row label unless the rule names another target.
#
#   rule_id, measure_kind, target, pattern, expected value band
#
# The band is carried into the table so the view can flag a point whose
# magnitude contradicts its class, rather than silently trusting the text.
# --------------------------------------------------------------------------
RULES = [
    # Shares and growth first: "YoY Growth Aero Revenue" must never read as revenue.
    ("R01_share", "share", "label", r"(% share|share of|% of total|mix %)", 0.0, 1.5),
    (
        "R02_growth",
        "growth",
        "label",
        r"(yoy|y/y|year on year|year-on-year|cagr|% change|% chg|change in|growth|variance|delta)",
        -5.0,
        5.0,
    ),
    # Macro drivers and deflators. Guarded by band: an index is a small number.
    (
        "R03_index",
        "index",
        "label",
        r"(consumer price index|inflation index|^cpi|^rpi|^gdp|\(cpi|deflat|price index|^index)",
        -1.0,
        20.0,
    ),
    # Per-unit rates. The unit 'per pax' is decisive where it is present.
    ("R04_rate_unit", "rate", "unit", r"^(per pax|per passenger|/pax)$", -200.0, 200.0),
    (
        "R05_rate_label",
        "rate",
        "label",
        r"(per pax|per pass|per passenger|yield|/pax|per wlu|per atm|per movement|per sqm|rev/pax)",
        -200.0,
        200.0,
    ),
    # Volumes. Freight and mail are excluded here on purpose: at Newcastle
    # "Freight / Mail / General Aviation" is a revenue line, not a tonnage.
    (
        "R06_volume",
        "volume",
        "label",
        r"^(pax|passengers?|total pax|atms?|air transport movements|movements|wlu|seats)\b",
        0.0,
        1e9,
    ),
    # Segment components of a total. Summing these with the total double counts.
    (
        "R07_component",
        "component",
        "label",
        r"(low cost|lcc|charter|scheduled|full service|domestic|international|^eu\b|non-eu|non eu|british airways|other airlines|transfer|freight|mail|general aviation|car park|parking|duty free|retail|advertising|concession|fuel|handling|hbs|cute|check-in|transactions|rental)",
        None,
        None,
    ),
    (
        "R08_total",
        "level",
        "label",
        r"(^total|^sub ?total|^revenues?$|^aero(nautical)? revenues?$|^net revenue|^turnover)",
        None,
        None,
    ),
    # A bare "Revenue" style label on a revenue sheet, caught after the above.
    ("R09_level", "level", "label", r"(revenue|income|ebitda|opex|cost)", None, None),
]

# Metric family. Non-aero is tested first, because every non-aero label
# contains the string "aero".
FAMILY_SQL = """
CASE
  WHEN regexp_matches(fam_text, '(non-aero|non aero|nonaero|retail|car park|duty free|concession)') THEN 'nonaero'
  WHEN regexp_matches(fam_text, 'aero')                                                             THEN 'aero'
  WHEN regexp_matches(fam_text, '(passenger|pax|traffic)')                                          THEN 'traffic'
  ELSE 'other'
END"""

# metric_code_v2 is deliberately NULL for growth, share, index and unclassified.
# A point that cannot answer a money question should not carry a money code.
CODE_SQL = """
CASE
  WHEN measure_kind = 'volume'                          THEN 'pax_total'
  WHEN measure_kind = 'rate'      AND family = 'aero'    THEN 'rev_aero_per_pax'
  WHEN measure_kind = 'rate'      AND family = 'nonaero' THEN 'rev_nonaero_per_pax'
  WHEN measure_kind = 'level'     AND family = 'aero'    THEN 'rev_aero'
  WHEN measure_kind = 'level'     AND family = 'nonaero' THEN 'rev_nonaero'
  WHEN measure_kind = 'component' AND family = 'aero'    THEN 'rev_aero_segment'
  WHEN measure_kind = 'component' AND family = 'nonaero' THEN 'rev_nonaero_segment'
  ELSE NULL
END"""

# The scale cue carries three facts, not one: currency, scale and price basis.
# Mixing 2008 real with nominal is a wrong answer that looks right.
CURRENCY_SQL = """
CASE
  WHEN regexp_matches(cue, '(£|gbp)') THEN 'GBP'
  WHEN regexp_matches(cue, '(€|eur)') THEN 'EUR'
  WHEN regexp_matches(cue, '([$]|usd)') THEN 'USD'
  ELSE ''
END"""

SCALE_SQL = """
CASE
  WHEN regexp_matches(cue, '(000s|000''s|thousand)') THEN 1000
  WHEN regexp_matches(cue, '(^| )(m|mn|million)s?( |,|$)') THEN 1000000
  WHEN regexp_matches(cue, '(bn|billion)') THEN 1000000000
  ELSE 1
END"""

BASIS_SQL = """
CASE
  WHEN regexp_matches(cue, 'nominal') THEN 'nominal'
  WHEN regexp_matches(cue, '(real|[0-9]{4} price)') THEN 'real'
  WHEN regexp_matches(cue, '[0-9]{4}') THEN 'real'
  ELSE ''
END"""

BASE_YEAR_SQL = "try_cast(nullif(regexp_extract(cue, '(19[0-9]{2}|20[0-9]{2})', 1), '') AS INTEGER)"


def kind_sql() -> str:
    """First-match-wins CASE over the rule table, for measure_kind."""
    parts = []
    for rule_id, kind, target, pattern, _lo, _hi in RULES:
        col = "row_label" if target == "label" else "unit_l"
        parts.append(f"    WHEN regexp_matches({col}, '{pattern}') THEN '{kind}'")
    return "CASE\n" + "\n".join(parts) + "\n    ELSE 'unclassified'\n  END"


def rule_sql() -> str:
    """The same chain, returning the rule id that fired, so every tag is traceable."""
    parts = []
    for rule_id, _kind, target, pattern, _lo, _hi in RULES:
        col = "row_label" if target == "label" else "unit_l"
        parts.append(f"    WHEN regexp_matches({col}, '{pattern}') THEN '{rule_id}'")
    return "CASE\n" + "\n".join(parts) + "\n    ELSE 'R99_none'\n  END"


def band_sql(idx: int) -> str:
    """Expected value band, so the view can flag magnitude that contradicts class."""
    parts = []
    for rule_id, _kind, target, pattern, lo, hi in RULES:
        bound = (lo, hi)[idx]
        if bound is None:
            continue
        col = "row_label" if target == "label" else "unit_l"
        parts.append(f"    WHEN regexp_matches({col}, '{pattern}') THEN {bound}")
    if not parts:
        return "NULL"
    return "CASE\n" + "\n".join(parts) + "\n    ELSE NULL\n  END"


def classified_sql(where: str) -> str:
    """One distinct row per (context, sheet): the classification depends only on
    those, so the map is small even though the store is 722m points."""
    return f"""
WITH base AS (
  SELECT DISTINCT
         context,
         regexp_extract(location, 'sheet=(.*)!', 1) AS sheet,
         trim(regexp_replace(split_part(context, '||', 1),
                             '^\\s*\\[[^\\]]*\\]\\s*', ''))       AS label_full,
         lower(regexp_extract(context, '^\\s*\\[([^\\]]*)\\]', 1)) AS cue,
         lower(split_part(context, '||', 2))                     AS tail,
         lower(coalesce(unit, ''))                               AS unit_l
  FROM ask_points
  WHERE {where}
),
lab AS (
  SELECT *,
         CASE WHEN sheet <> ''
                   AND lower(right(label_full, length(sheet))) = lower(sheet)
              THEN lower(trim(left(label_full, length(label_full) - length(sheet))))
              ELSE lower(label_full)
         END AS row_label
  FROM base
),
tagged AS (
  SELECT context, sheet, row_label, cue, unit_l,
         lower(row_label || ' ' || tail) AS fam_text,
         {kind_sql()} AS measure_kind,
         {rule_sql()} AS rule_id,
         {band_sql(0)} AS band_lo,
         {band_sql(1)} AS band_hi
  FROM lab
),
famd AS (
  SELECT *, {FAMILY_SQL} AS family FROM tagged
)
SELECT context, sheet, row_label, measure_kind, family,
       {CODE_SQL}       AS metric_code_v2,
       {CURRENCY_SQL}   AS currency_v2,
       {SCALE_SQL}      AS scale_mult,
       {BASIS_SQL}      AS price_basis,
       {BASE_YEAR_SQL}  AS base_year,
       rule_id, band_lo, band_hi
FROM famd"""


VIEW_SQL = """
CREATE OR REPLACE VIEW ask_points_v2 AS
SELECT p.*,
       t.row_label,
       t.measure_kind,
       t.metric_family,
       t.metric_code_v2,
       nullif(t.currency_v2, '')  AS currency_v2,
       t.scale_mult,
       nullif(t.price_basis, '')  AS price_basis,
       t.base_year,
       t.rule_id,
       CASE WHEN t.context IS NULL THEN 'untagged'
            WHEN t.band_lo IS NOT NULL AND p.value_num < t.band_lo THEN 'below_band'
            WHEN t.band_hi IS NOT NULL AND p.value_num > t.band_hi THEN 'above_band'
            ELSE 'ok'
       END AS magnitude_check
FROM ask_points p
LEFT JOIN context_tag t ON p.context = t.context
                       AND regexp_extract(p.location, 'sheet=(.*)!', 1) = t.sheet
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--entity", default="NCL")
    ap.add_argument("--all", action="store_true", help="whole store, not one entity")
    ap.add_argument("--build", action="store_true", help="write the table and view")
    ap.add_argument("--out", default=r"E:\Avia\Extract\diag")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.store, read_only=not args.build)

    where = "TRUE" if args.all else f"entity_id = '{args.entity}'"
    scope = "whole store" if args.all else f"entity_id={args.entity}"
    log: list[str] = []

    def say(s: str = "") -> None:
        print(s)
        log.append(s)

    say(f"store : {args.store}")
    say(f"scope : {scope}")
    say(f"mode  : {'BUILD (writing)' if args.build else 'dry run (read only)'}")
    say()

    if args.build:
        con.execute("DROP TABLE IF EXISTS context_tag")
        con.execute(
            "CREATE TABLE context_tag AS "
            + classified_sql(where).replace("family,", "family AS metric_family,")
        )
        con.execute("CREATE INDEX IF NOT EXISTS ix_ctx_tag ON context_tag(context)")
        n = con.execute("SELECT count(*) FROM context_tag").fetchone()[0]
        say(f"context_tag written: {n:,} distinct (context, sheet) rows")
        con.execute(VIEW_SQL)
        say("view ask_points_v2 created")
        src = "context_tag"
    else:
        con.execute("CREATE OR REPLACE TEMP VIEW ctag AS " + classified_sql(where))
        src = "ctag"

    # ---------------------------------------------------------------- report
    say()
    say("what the rules claim, by point count")
    rows = con.execute(
        f"""
        SELECT t.measure_kind,
               coalesce(t.metric_code_v2, '(no money code)') AS code_v2,
               count(*)                    AS n_points,
               count(DISTINCT p.source_file) AS n_docs,
               round(median(p.value_num), 3) AS p50
        FROM ask_points p
        JOIN {src} t
          ON p.context = t.context
         AND regexp_extract(p.location, 'sheet=(.*)!', 1) = t.sheet
        WHERE {where.replace('entity_id', 'p.entity_id')}
        GROUP BY 1, 2 ORDER BY n_points DESC
        """
    ).fetchall()
    say(f"{'n_points':>12} {'docs':>7} {'median':>12}  measure_kind / metric_code_v2")
    for kind, code, n, nd, p50 in rows:
        say(f"{n:>12,} {nd:>7,} {str(p50):>12}  {kind} / {code}")

    say()
    say("what leaves rev_aero, and what joins it")
    moved = con.execute(
        f"""
        SELECT coalesce(nullif(p.metric_code, ''), '(blank)') AS was,
               coalesce(t.metric_code_v2, '(none)')           AS now,
               t.measure_kind, count(*) AS n
        FROM ask_points p
        JOIN {src} t
          ON p.context = t.context
         AND regexp_extract(p.location, 'sheet=(.*)!', 1) = t.sheet
        WHERE {where.replace('entity_id', 'p.entity_id')}
          AND (p.metric_code = 'rev_aero' OR t.metric_code_v2 LIKE 'rev_aero%')
        GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 30
        """
    ).fetchall()
    say(f"{'n':>12}  was -> now (measure_kind)")
    for was, now, kind, n in moved:
        say(f"{n:>12,}  {was} -> {now} ({kind})")

    say()
    say("sample row labels per class, to eyeball the rules")
    for (kind,) in con.execute(
        f"SELECT DISTINCT measure_kind FROM {src} ORDER BY 1"
    ).fetchall():
        labs = con.execute(
            f"SELECT row_label, rule_id FROM {src} WHERE measure_kind = ? "
            "AND row_label <> '' LIMIT 6",
            [kind],
        ).fetchall()
        say(f"  {kind:<14}: " + "; ".join(f"{l[:40]} [{r}]" for l, r in labs))

    say()
    say("magnitude conflicts (value contradicts the class it was given)")
    conf = con.execute(
        f"""
        SELECT t.measure_kind, count(*) AS n
        FROM ask_points p
        JOIN {src} t
          ON p.context = t.context
         AND regexp_extract(p.location, 'sheet=(.*)!', 1) = t.sheet
        WHERE {where.replace('entity_id', 'p.entity_id')}
          AND ((t.band_lo IS NOT NULL AND p.value_num < t.band_lo)
            OR (t.band_hi IS NOT NULL AND p.value_num > t.band_hi))
        GROUP BY 1 ORDER BY n DESC
        """
    ).fetchall()
    for kind, n in conf:
        say(f"  {kind:<14}: {n:,}")
    if not conf:
        say("  none")

    say()
    say("coverage: points the rules could not place")
    cov = con.execute(
        f"""
        SELECT count(*) FILTER (WHERE t.context IS NULL)                       AS no_tag,
               count(*) FILTER (WHERE t.measure_kind = 'unclassified')         AS unclassified,
               count(*)                                                        AS total
        FROM ask_points p
        LEFT JOIN {src} t
          ON p.context = t.context
         AND regexp_extract(p.location, 'sheet=(.*)!', 1) = t.sheet
        WHERE {where.replace('entity_id', 'p.entity_id')}
        """
    ).fetchone()
    say(f"  no context match : {cov[0]:,}")
    say(f"  unclassified     : {cov[1]:,}")
    say(f"  total in scope   : {cov[2]:,}")

    (out / "metric_tags_report.txt").write_text("\n".join(log), encoding="utf-8")
    con.close()
    print(f"\nreport written to {out / 'metric_tags_report.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
