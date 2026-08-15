"""build_metric_tags.py - re-tag metrics from the stored `context` column.

Step 1 of making the Library trustworthy. Additive: writes one table and one
view, changes nothing that exists. No re-harvest, no re-resolve. Same pattern as
harvest_manifest and doc_canonical.

WHY. metric_code was assigned from the sheet or section header, not the row
label. On a sheet called "Aero Revenue", every row on it took metric_code
rev_aero: passenger counts, CPI, GDP, yields, segment splits and the total
alike. Measured at Newcastle: under half the points labelled rev_aero are a
level of aero revenue.

THE PARSE IS EXACT, NOT A GUESS. context is structured:

    [scale cue] <row label> <sheet name> || <header stack> <section> <title>

and the sheet name is held separately in `location` (sheet=NAME!CELL), so
stripping the known sheet name off the tail of the label recovers the true row
label. "Passengers Aero Revenue" on sheet "Aero Revenue" -> "Passengers".

THREE FIELDS come out of it, and the separation between them is the point:

  measure_noun   WHAT is being measured (revenue, ebitda, opex, pax, macro).
                 Taken from the ROW LABEL first. This is what stops the mirror
                 image of the original defect: an EBITDA row on an aero sheet
                 must stay EBITDA, not become aero revenue.
  measure_kind   WHAT TYPE of number it is (level, component, rate, growth,
                 index, share, volume).
  metric_code_v2 the code that may answer a question, NULL wherever the point
                 must not, so filtering on it is safe by construction rather
                 than by the caller remembering to.

SHEET FALLBACK, deliberately narrow. Wide traffic tables label rows with a
dimension ("asia 1999-q2") and carry the measure in the header. The sheet may
supply the noun ONLY where the row label names none and the sheet names exactly
one, and label_source records that it did, so a tag that leaned on the sheet can
always be excluded or scored separately.

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
# WHAT is being measured. Ordered, first match wins, applied to the row label.
# Patterns are RE2 (DuckDB): no lookarounds, so negation is done by ordering.
#
# Order matters twice over:
#   - ebitda / opex / capex before revenue, so "EBITDA" on a revenue sheet
#     stays EBITDA;
#   - revenue before pax, so "aero rev per pax" is revenue-at-a-rate, not a
#     passenger count.
# "cost" is never matched bare: "Low Cost" is a market segment, not opex.
# --------------------------------------------------------------------------
NOUNS = [
    ("macro", r"(consumer price index|inflation|deflator|price index|elasticit|^cpi|^rpi|^gdp|\(cpi|\(rpi)"),
    ("ebitda", r"(ebitda|ebit\b|operating profit|net income|profit before)"),
    ("opex", r"(opex|operating cost|operating expen|staff cost|payroll|total cost|unit cost|cost base|overhead)"),
    ("capex", r"(capex|capital expen|capital cost|investment programme)"),
    ("charge", r"(charge|tariff|landing fee|passenger fee)"),
    ("revenue", r"(revenue|rev\b|revs\b|income|turnover|yield|fare|receipts|takings)"),
    ("pax", r"(passengers?|^pax|pax\b|atms?\b|air transport movement|movements?\b|wlu|seats\b|load factor)"),
    # Last, and only after pax has had its chance: in this corpus a bare "Aero"
    # or "Non-Aero" naming a line IS a revenue category ("Low Cost" on sheet
    # "Aero Calc", "Aero per Pax"). Placed here so "Aero Passengers" stays pax.
    ("revenue", r"(^|\s)(non-?)?aero(\s|$)"),
]

# The per-unit denominator is stripped before the noun is read. Otherwise
# "Aero per Pax" reads as a passenger count, when the pax is the denominator
# and the subject is revenue.
DENOM_STRIP = r"(per pax|per passenger|per pass[a-z]*|per wlu|per atm|per fte|per employee|per movement|per sqm|/pax)"

# WHAT TYPE of number. Ordered, first match wins.
#
#   rule_id, measure_kind, target, pattern, band_lo, band_hi
#
# Bands are corrected from the first dry run: indices are commonly rebased to
# 100, and growth and share are stated as percentage points as often as as
# fractions. A band that is too tight manufactures conflicts and hides the real
# ones, so these are set to catch nonsense only.
KINDS = [
    ("R01_share", "share", "label", r"(% share|share of|% of total|mix %|penetration)", -200.0, 200.0),
    ("R02_growth", "growth", "label", r"(yoy|y/y|year on year|year-on-year|cagr|% change|% chg|change in|growth|variance|delta)", -500.0, 500.0),
    ("R03_index", "index", "label", r"(consumer price index|inflation index|price index|deflator|^index|index$|elasticit|^cpi|^rpi|^gdp|\(cpi|\(rpi)", -100.0, 100000.0),
    # A per-unit denominator anywhere makes it a rate, whatever the noun.
    # Band tightened after the Newcastle dry run: at +/-100,000 it admitted a
    # per-pax figure of 71,412. A revenue per passenger above four digits is a
    # level or a variance that has leaked into the rate class, in any currency
    # this firm works in.
    ("R04_rate_unit", "rate", "unit", r"^(per pax|per passenger|/pax|per wlu|per atm)$", -1000.0, 1000.0),
    ("R05_rate_label", "rate", "label", r"(per pax|per pass|per passenger|per wlu|per atm|per movement|per fte|per sqm|per employee|/pax|rev/pax|yield|average fare|unit rate)", -1000.0, 1000.0),
    ("R06_volume", "volume", "label", r"^(pax|passengers?|total pax|atms?|air transport movements|movements|wlu|seats)\b", None, None),
    ("R07_component", "component", "label", r"(low cost|lcc|charter|scheduled|full service|domestic|international|^eu\b|non-eu|non eu|british airways|other airlines|transfer|freight|mail|general aviation|car park|parking|duty free|retail|advertising|concession|fuel|handling|hbs|cute|check-in|transactions|rental|catering)", None, None),
    ("R08_total", "level", "label", r"(^total|^sub ?total|^net\b|^gross\b|^reported|^normalised)", None, None),
    ("R09_level", "level", "noun", r"(revenue|charge|ebitda|opex|capex)", None, None),
]

# Family. Non-aero is tested first, because every non-aero label contains the
# string "aero". Only consulted for the revenue and charge nouns.
FAMILY_SQL = """
CASE
  WHEN regexp_matches(fam_text, '(non-aero|non aero|nonaero|retail|car park|duty free|concession|commercial revenue)') THEN 'nonaero'
  WHEN regexp_matches(fam_text, 'aero')                                                                                THEN 'aero'
  WHEN regexp_matches(fam_text, '(passenger|pax|traffic)')                                                             THEN 'traffic'
  ELSE 'other'
END"""

# The code that may answer a question. NULL for growth, share, index, for any
# unplaced point, and for any noun the sheet had to supply ambiguously.
CODE_SQL = """
CASE
  WHEN measure_kind IN ('growth', 'share', 'index')      THEN NULL
  WHEN measure_noun = 'macro'                            THEN NULL
  WHEN label_source = 'none'                             THEN NULL
  WHEN measure_noun = 'pax' AND measure_kind = 'component'      THEN 'pax_segment'
  WHEN measure_noun = 'pax'                                     THEN 'pax_total'
  WHEN measure_noun = 'ebitda' AND measure_kind = 'rate'        THEN 'ebitda_per_pax'
  WHEN measure_noun = 'ebitda'                                  THEN 'ebitda'
  WHEN measure_noun = 'opex'   AND measure_kind = 'rate'        THEN 'opex_per_pax'
  WHEN measure_noun = 'opex'                                    THEN 'opex_total'
  WHEN measure_noun = 'capex'                                   THEN 'capex'
  WHEN measure_noun = 'charge' AND family = 'aero'              THEN 'aero_charge'
  WHEN measure_noun = 'revenue' AND measure_kind = 'rate'    AND family = 'aero'    THEN 'rev_aero_per_pax'
  WHEN measure_noun = 'revenue' AND measure_kind = 'rate'    AND family = 'nonaero' THEN 'rev_nonaero_per_pax'
  WHEN measure_noun = 'revenue' AND measure_kind = 'rate'                           THEN 'rev_total_per_pax'
  WHEN measure_noun = 'revenue' AND measure_kind = 'component' AND family = 'aero'    THEN 'rev_aero_segment'
  WHEN measure_noun = 'revenue' AND measure_kind = 'component' AND family = 'nonaero' THEN 'rev_nonaero_segment'
  WHEN measure_noun = 'revenue' AND family = 'aero'                                 THEN 'rev_aero'
  WHEN measure_noun = 'revenue' AND family = 'nonaero'                              THEN 'rev_nonaero'
  WHEN measure_noun = 'revenue'                                                     THEN 'rev_total'
  ELSE NULL
END"""

# The scale cue carries three facts, not one: currency, scale and price basis.
# Mixing 2008 real with nominal is a wrong answer that looks right.
CURRENCY_SQL = """
CASE
  WHEN regexp_matches(cue, '(£|gbp)')   THEN 'GBP'
  WHEN regexp_matches(cue, '(€|eur)')   THEN 'EUR'
  WHEN regexp_matches(cue, '([$]|usd)') THEN 'USD'
  ELSE ''
END"""

SCALE_SQL = """
CASE
  WHEN regexp_matches(cue, '(000s|000''s|thousand)')       THEN 1000
  WHEN regexp_matches(cue, '(^| )(m|mn|million)s?( |,|$)') THEN 1000000
  WHEN regexp_matches(cue, '(bn|billion)')                 THEN 1000000000
  ELSE 1
END"""

BASIS_SQL = """
CASE
  WHEN regexp_matches(cue, 'nominal')             THEN 'nominal'
  WHEN regexp_matches(cue, '(real|[0-9]{4} price)') THEN 'real'
  WHEN regexp_matches(cue, '[0-9]{4}')            THEN 'real'
  ELSE ''
END"""

BASE_YEAR_SQL = (
    "try_cast(nullif(regexp_extract(cue, '(19[0-9]{2}|20[0-9]{2})', 1), '') AS INTEGER)"
)

# Percent against fraction is a real convention split in the store: the same
# growth rate appears as 0.028 and as 2.8. Recorded, not silently normalised.
PCT_SQL = "(unit_l = '%' OR regexp_matches(cue, '%'))"


def _chain(rows, value_idx: int, default: str) -> str:
    """First-match-wins CASE from a rule table."""
    parts = []
    for row in rows:
        target = row[2]
        col = {"label": "row_label", "unit": "unit_l", "noun": "measure_noun"}[target]
        val = row[value_idx]
        if val is None:
            continue
        parts.append(f"    WHEN regexp_matches({col}, '{row[3]}') THEN {val}")
    if not parts:
        return default
    return "CASE\n" + "\n".join(parts) + f"\n    ELSE {default}\n  END"


def kind_sql() -> str:
    rows = [(r[0], r[1], r[2], r[3], f"'{r[1]}'", None) for r in KINDS]
    return _chain(rows, 4, "'unclassified'")


def rule_sql() -> str:
    rows = [(r[0], r[1], r[2], r[3], f"'{r[0]}'", None) for r in KINDS]
    return _chain(rows, 4, "'R99_none'")


def band_sql(which: int) -> str:
    rows = [(r[0], r[1], r[2], r[3], r[4] if which == 0 else r[5], None) for r in KINDS]
    return _chain(rows, 4, "NULL")


def noun_sql(col: str) -> str:
    parts = [f"    WHEN regexp_matches({col}, '{pat}') THEN '{name}'" for name, pat in NOUNS]
    return "CASE\n" + "\n".join(parts) + "\n    ELSE ''\n  END"


def sheet_noun_unambiguous_sql() -> str:
    """The sheet may supply the noun only if it names exactly ONE measure."""
    counts = " + ".join(
        f"CASE WHEN regexp_matches(fam_text, '{pat}') THEN 1 ELSE 0 END"
        for _name, pat in NOUNS
    )
    return f"({counts}) = 1"


def connect_store(path: str, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Open the store, or refuse.

    duckdb.connect() in write mode CREATES an empty database when the file is
    absent. Run on the wrong machine, this script therefore manufactured an
    empty full_v2.duckdb on the Dev PC on 15 Aug and then reported that
    ask_points did not exist, which reads exactly like a store that has been
    lost. Fail loudly instead."""
    if not os.path.exists(path):
        raise SystemExit(
            f"\nNo store at {path}\n"
            "Refusing to run: DuckDB would create an empty database here.\n"
            "This script runs on the WORKSTATION (Donatello). Check the prompt\n"
            "reads [Donatello]: before you run it.\n"
        )
    con = duckdb.connect(path, read_only=read_only)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "ask_points" not in tables:
        con.close()
        raise SystemExit(
            f"\n{path} holds no ask_points table, so it is not the Library.\n"
            f"Tables found: {sorted(tables) or 'none, the file is empty'}\n"
        )
    return con


def canonical_clause(con: duckdb.DuckDBPyConnection, alias: str = "") -> str:
    """Uncorrelated semi-join. NOT an EXISTS: a correlated outer reference binds
    to the inner table, so the filter reports itself applied and does nothing.
    That bug cost us a day on 14 Aug; it is not repeated here."""
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "doc_canonical" not in tables:
        return ""
    col = f'{alias}"source_file"' if alias else '"source_file"'
    return f" AND {col} IN (SELECT source_file FROM doc_canonical WHERE is_canonical)"


def classified_sql(where: str, source: str = "ask_points") -> str:
    """One distinct row per (context, sheet): classification depends only on
    those, so the map stays small against 722m points."""
    return f"""
WITH base AS (
  SELECT DISTINCT
         context,
         regexp_extract(location, 'sheet=(.*)!', 1) AS sheet,
         trim(regexp_replace(split_part(context, '||', 1),
                             '^\\s*\\[[^\\]]*\\]\\s*', ''))        AS label_full,
         lower(regexp_extract(context, '^\\s*\\[([^\\]]*)\\]', 1)) AS cue,
         lower(split_part(context, '||', 2))                      AS tail,
         lower(coalesce(unit, ''))                                AS unit_l
  FROM {source}
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
nouned AS (
  SELECT *,
         lower(row_label || ' ' || sheet || ' ' || tail) AS fam_text,
         trim(regexp_replace(row_label, '{DENOM_STRIP}', ' ', 'g')) AS noun_label
  FROM lab
),
labelled AS (
  SELECT *, {noun_sql('noun_label')} AS label_noun FROM nouned
),
resolved AS (
  SELECT *,
         CASE WHEN label_noun <> '' THEN label_noun
              WHEN {sheet_noun_unambiguous_sql()} THEN {noun_sql('fam_text')}
              ELSE ''
         END AS measure_noun,
         CASE WHEN label_noun <> '' THEN 'row_label'
              WHEN {sheet_noun_unambiguous_sql()} THEN 'sheet_fallback'
              ELSE 'none'
         END AS label_source
  FROM labelled
),
kinded AS (
  SELECT *,
         {kind_sql()} AS measure_kind,
         {rule_sql()} AS rule_id,
         {band_sql(0)} AS band_lo,
         {band_sql(1)} AS band_hi,
         {FAMILY_SQL} AS family
  FROM resolved
),
coded AS (
  SELECT *, {CODE_SQL} AS metric_code_v2 FROM kinded
)
SELECT context, sheet, row_label, measure_noun, label_source, measure_kind,
       family AS metric_family,
       metric_code_v2,
       {CURRENCY_SQL}  AS currency_v2,
       {SCALE_SQL}     AS scale_mult,
       {BASIS_SQL}     AS price_basis,
       {BASE_YEAR_SQL} AS base_year,
       {PCT_SQL}       AS unit_is_pct,
       rule_id,
       -- The floor depends on the CODE, not the kind. Aeronautical revenue per
       -- passenger and a passenger count are never negative, so a negative one
       -- is a variance or growth row still wearing a rate code. EBITDA per pax
       -- legitimately goes negative and keeps its original floor.
       CASE WHEN metric_code_v2 IN ('rev_aero_per_pax', 'rev_nonaero_per_pax',
                                    'rev_total_per_pax', 'pax_total', 'pax_segment')
            THEN greatest(coalesce(band_lo, 0.0), 0.0)
            ELSE band_lo
       END AS band_lo,
       band_hi
FROM coded"""


VIEW_SQL = """
CREATE OR REPLACE VIEW ask_points_v2 AS
SELECT p.*,
       t.row_label,
       t.measure_noun,
       t.label_source,
       t.measure_kind,
       t.metric_family,
       -- A point whose magnitude contradicts its class LOSES its code, it does
       -- not merely carry a warning. Flagging relies on the caller remembering
       -- to filter; this does not. metric_code_v2_raw is kept so the rules can
       -- still be audited against what they originally claimed.
       CASE WHEN (t.band_lo IS NOT NULL AND p.value_num < t.band_lo)
              OR (t.band_hi IS NOT NULL AND p.value_num > t.band_hi)
            THEN NULL ELSE t.metric_code_v2
       END AS metric_code_v2,
       t.metric_code_v2 AS metric_code_v2_raw,
       nullif(t.currency_v2, '') AS currency_v2,
       t.scale_mult,
       nullif(t.price_basis, '') AS price_basis,
       t.base_year,
       t.unit_is_pct,
       t.rule_id,
       CASE WHEN t.context IS NULL THEN 'untagged'
            WHEN t.band_lo IS NOT NULL AND p.value_num < t.band_lo THEN 'below_band'
            WHEN t.band_hi IS NOT NULL AND p.value_num > t.band_hi THEN 'above_band'
            ELSE 'ok'
       END AS magnitude_check
FROM ask_points p
LEFT JOIN context_tag t
       ON p.context = t.context
      AND regexp_extract(p.location, 'sheet=(.*)!', 1) = t.sheet
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--entity", default="NCL")
    ap.add_argument("--all", action="store_true", help="whole store, not one entity")
    ap.add_argument("--build", action="store_true", help="write the table and view")
    ap.add_argument("--no-canonical", action="store_true", help="include duplicate copies")
    ap.add_argument("--report", action="store_true", help="full breakdown after a store-wide build")
    ap.add_argument("--out", default=r"E:\Avia\Extract\diag")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    con = connect_store(args.store, read_only=not args.build)

    canon = "" if args.no_canonical else canonical_clause(con)
    canon_p = "" if args.no_canonical else canonical_clause(con, "p.")
    ent = "TRUE" if args.all else f"entity_id = '{args.entity}'"
    ent_p = "TRUE" if args.all else f"p.entity_id = '{args.entity}'"
    where = ent + canon
    where_p = ent_p + canon_p

    log: list[str] = []

    def say(s: str = "") -> None:
        print(s, flush=True)
        log.append(s)

    def flush() -> None:
        """Write the report as we go. A long run that dies at the end should
        still leave a record of what it did."""
        (out / "metric_tags_report.txt").write_text("\n".join(log), encoding="utf-8")

    say(f"store     : {args.store}")
    say(f"scope     : {'whole store' if args.all else 'entity_id=' + args.entity}")
    say(f"canonical : {'NOT applied' if not canon else 'applied'}")
    say(f"mode      : {'BUILD (writing)' if args.build else 'dry run (read only)'}")
    say()

    if args.build:
        con.execute("DROP TABLE IF EXISTS context_tag")
        con.execute("CREATE TABLE context_tag AS " + classified_sql(where))
        n = con.execute("SELECT count(*) FROM context_tag").fetchone()[0]
        say(f"context_tag written: {n:,} distinct (context, sheet) rows")
        con.execute(VIEW_SQL)
        say("view ask_points_v2 created")
        # CHECKPOINT, or the write lives only in the write-ahead log. An
        # uncommitted table is still visible to a read-only connection, which
        # replays the log, so it looks built; the next read-write open discards
        # the incomplete transaction and it is gone. That happened on 15 Aug.
        con.execute("CHECKPOINT")
        n2 = con.execute("SELECT count(*) FROM context_tag").fetchone()[0]
        say(f"checkpointed: {n2:,} rows durable on disk")
        flush()
        points, tagsrc, pwhere = "ask_points", "context_tag", where_p
        if args.all and not args.report:
            # The report queries carry no entity filter, so store-wide they
            # join 722m points to the tag map and group six ways. That is what
            # ran all night while the build itself had long since finished.
            say()
            say("build complete. Report skipped store-wide: run verify_tags.py,")
            say("or re-run with --report if the full breakdown is wanted.")
            (out / "metric_tags_report.txt").write_text("\n".join(log), encoding="utf-8")
            con.close()
            return 0
    elif args.all:
        # Store-wide: materialise only the tag map, which is small. The scope
        # itself is 722m points and must not be copied into a temp table.
        say("classifying every distinct context, one pass over the store ...")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE ctag AS " + classified_sql(where)
        )
        n_ctx = con.execute("SELECT count(*) FROM ctag").fetchone()[0]
        say(f"  distinct (context, sheet) : {n_ctx:,}")
        say()
        points, tagsrc, pwhere = "ask_points", "ctag", where_p
    else:
        # MATERIALISE, do not view. As a temp view the classification is
        # recomputed and the store re-scanned on every report query below: six
        # passes over 84GB to answer six questions. One pass each instead.
        say("materialising the scope, one pass over the store ...")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE pts AS SELECT point_id, value_num, "
            "metric_code, entity_id, unit, source_file, location, context, year "
            f"FROM ask_points WHERE {where}"
        )
        n_pts = con.execute("SELECT count(*) FROM pts").fetchone()[0]
        say(f"  scope materialised : {n_pts:,} points")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE ctag AS " + classified_sql("TRUE", "pts")
        )
        n_ctx = con.execute("SELECT count(*) FROM ctag").fetchone()[0]
        say(f"  distinct (context, sheet) : {n_ctx:,}")
        say()
        points, tagsrc, pwhere = "pts", "ctag", "TRUE"

    join = (
        f"FROM {points} p JOIN {tagsrc} t ON p.context = t.context "
        "AND regexp_extract(p.location, 'sheet=(.*)!', 1) = t.sheet "
        f"WHERE {pwhere}"
    )

    say("what the rules claim, by point count")
    rows = con.execute(
        f"""SELECT t.measure_noun, t.measure_kind, t.label_source,
                   coalesce(t.metric_code_v2, '(no money code)') AS code_v2,
                   count(*) AS n, round(median(p.value_num), 3) AS p50
            {join} GROUP BY 1,2,3,4 ORDER BY n DESC LIMIT 30"""
    ).fetchall()
    say(f"{'n_points':>12} {'median':>12}  noun / kind / source -> code_v2")
    for noun, kind, srcl, code, n, p50 in rows:
        say(f"{n:>12,} {str(p50):>12}  {noun or '(none)'} / {kind} / {srcl} -> {code}")

    say()
    say("what leaves rev_aero, and what joins it")
    for was, now, kind, n in con.execute(
        f"""SELECT coalesce(nullif(p.metric_code, ''), '(blank)'),
                   coalesce(t.metric_code_v2, '(none)'), t.measure_kind, count(*) AS n
            {join} AND (p.metric_code = 'rev_aero' OR t.metric_code_v2 LIKE 'rev_aero%')
            GROUP BY 1,2,3 ORDER BY n DESC LIMIT 25"""
    ).fetchall():
        say(f"{n:>12,}  {was} -> {now} ({kind})")

    say()
    say("did the noun rule stop the promotions it was written to stop")
    for was, now, n in con.execute(
        f"""SELECT p.metric_code, coalesce(t.metric_code_v2, '(none)'), count(*) AS n
            {join} AND p.metric_code IN ('ebitda','gdp_elasticity','opex_total','capex','pax_total')
            GROUP BY 1,2 ORDER BY n DESC LIMIT 15"""
    ).fetchall():
        say(f"{n:>12,}  {was} -> {now}")

    say()
    say("GOLD CHECK: aero revenue per pax at this entity, by year")
    for yr, n, lo, p50, hi in con.execute(
        f"""SELECT p.year, count(*) AS n, round(min(p.value_num),2),
                   round(median(p.value_num),2), round(max(p.value_num),2)
            {join} AND t.metric_code_v2 = 'rev_aero_per_pax'
              AND t.label_source = 'row_label' AND p.year BETWEEN 2005 AND 2015
              AND (t.band_lo IS NULL OR p.value_num >= t.band_lo)
              AND (t.band_hi IS NULL OR p.value_num <= t.band_hi)
            GROUP BY 1 ORDER BY 1"""
    ).fetchall():
        say(f"  {yr}  n={n:>7,}  min {lo:>8}  median {p50:>8}  max {hi:>8}")
    say("  (Newcastle 2009 should read circa GBP 5.17)")

    say()
    say("magnitude conflicts, and unplaced points")
    for kind, n in con.execute(
        f"""SELECT t.measure_kind, count(*) AS n {join}
            AND ((t.band_lo IS NOT NULL AND p.value_num < t.band_lo)
              OR (t.band_hi IS NOT NULL AND p.value_num > t.band_hi))
            GROUP BY 1 ORDER BY n DESC"""
    ).fetchall():
        say(f"  conflict {kind:<14}: {n:,}")
    cov = con.execute(
        f"""SELECT count(*) FILTER (WHERE t.label_source = 'row_label'),
                   count(*) FILTER (WHERE t.label_source = 'sheet_fallback'),
                   count(*) FILTER (WHERE t.label_source = 'none'),
                   count(*) FILTER (WHERE t.metric_code_v2 IS NOT NULL),
                   count(*) {join}"""
    ).fetchone()
    say(f"  noun from row label : {cov[0]:,}")
    say(f"  noun from sheet     : {cov[1]:,}")
    say(f"  no noun found       : {cov[2]:,}")
    say(f"  carries a money code: {cov[3]:,}")
    say(f"  total in scope      : {cov[4]:,}")

    (out / "metric_tags_report.txt").write_text("\n".join(log), encoding="utf-8")
    con.close()
    print(f"\nreport written to {out / 'metric_tags_report.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
