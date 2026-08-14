"""Tests for the context-driven metric re-tag pass.

Goes to tests/test_metric_tags.py in the ask-avia repo.

Every case below is a shape observed in the real store at Newcastle, not an
invented one. The fixture rows carry the verbatim context format:

    [scale cue] <row label> <sheet name> || <header stack> <section> <title>

Four of these cases are regressions against bugs the first dry run exposed:

  test_ebitda_is_not_promoted_to_aero_revenue
      an EBITDA row on an aero sheet became rev_aero, which is the original
      sheet-decides-the-measure defect in mirror image.
  test_per_pax_denominator_is_not_read_as_the_subject
      "Aero per Pax" read as a passenger count because "pax" was in the label.
  test_segment_on_an_aero_sheet_is_a_segment_not_a_total
      "Low Cost" summed with "Total" double counts.
  test_growth_and_index_carry_no_money_code
      a growth rate answering a revenue question is the worst outcome here.

Copyright Avia Solutions Limited. All rights reserved.
"""

from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")

import build_metric_tags as B  # noqa: E402

# point_id, value, metric_code, unit, sheet!cell, context, year
ROWS = [
    ("p_pax", 809.4, "rev_aero", "'000", "Aero Revenue!C4",
     "[000s] Passengers Aero Revenue || [000s] Newcastle Airport: Aeronautical Revenue Progression"),
    ("p_rev", 4622.3, "rev_aero", "GBP", "Aero Revenue!C5",
     "[£000s, Nominal] Revenue Aero Revenue || [£000s] Newcastle Airport"),
    ("p_yield", 5.17, "rev_aero", "", "Aero Revenue!C6",
     "[Nominal] Yield Aero Revenue || [%] Newcastle Airport"),
    ("p_cpi", 1.253, "rev_aero", "", "Aero Revenue!C7",
     "[Number] Consumer Price Index Aero Revenue || [%] Newcastle Airport"),
    ("p_ebitda", 28000.0, "ebitda", "GBP", "Aero Revenue!C8",
     "[£000s, Nominal] Reported EBITDA Aero Revenue || [£000s] Newcastle Airport"),
    ("p_gdp", 0.021, "rev_aero", "", "Aero Revenue!C9",
     "[Nominal] GDP Aero Revenue || [%] Newcastle Airport"),
    ("p_seg", 5518.2, "", "GBP", "Aero Calc!D4",
     "[£000s, Nominal] Low Cost Aero Calc || [£000s] Newcastle"),
    ("p_perpax", 4.68, "", "per pax", "EBITDA Comparisons!E4",
     "[£, 2008 Prices] Aero per Pax EBITDA Comparisons || [£] Newcastle"),
    ("p_nonaero", 6548.3, "", "GBP", "Non-Aero Calc!F4",
     "[£000s, Nominal] Total Non-Aero Calc || [£000s] Newcastle"),
    ("p_growth", -0.001, "rev_aero", "", "Aero Revenue!C10",
     "[Nominal] YoY Growth Aero Revenue || [%] Newcastle"),
    ("p_traffic", 1200.0, "", "", "Traffic!B9",
     "[000s] asia 1999-q2 Traffic || [000s] Passengers by region"),
    ("p_fare", 8176.0, "average_fare", "GBP", "Aero Revenues!G4",
     "[£000s, Nominal] Revenue Aero Revenues || [£000s] Newcastle"),
]


@pytest.fixture(scope="module")
def tags(tmp_path_factory):
    """Classify the fixture once, return {point_id: row}."""
    path = str(tmp_path_factory.mktemp("store") / "fix.duckdb")
    con = duckdb.connect(path)
    con.execute("""
        CREATE TABLE ask_points (
          point_id VARCHAR, value_num DOUBLE, metric_code VARCHAR, entity_id VARCHAR,
          unit VARCHAR, source_file VARCHAR, project_id VARCHAR, review_status VARCHAR,
          source_type_flag VARCHAR, temporality VARCHAR, currency VARCHAR,
          value_scale VARCHAR, location VARCHAR, context VARCHAR, year INTEGER)
    """)
    con.executemany(
        "INSERT INTO ask_points VALUES (?,?,?,'NCL',?,'/a.xls','pr','','','actual','','',?,?,2009)",
        [(p, v, m, u, f"sheet={loc}", ctx) for p, v, m, u, loc, ctx in ROWS],
    )
    con.execute("CREATE TABLE doc_canonical(source_file VARCHAR, is_canonical BOOLEAN)")
    con.execute("INSERT INTO doc_canonical VALUES ('/a.xls', true)")
    con.close()

    con = duckdb.connect(path, read_only=True)
    sql = B.classified_sql("entity_id = 'NCL'" + B.canonical_clause(con))
    con.execute("CREATE OR REPLACE TEMP VIEW ctag AS " + sql)
    out = {}
    for pid, kind, noun, code, src, ccy, scale, basis, base_yr in con.execute("""
        SELECT p.point_id, t.measure_kind, t.measure_noun, t.metric_code_v2,
               t.label_source, t.currency_v2, t.scale_mult, t.price_basis, t.base_year
        FROM ask_points p JOIN ctag t
          ON p.context = t.context
         AND regexp_extract(p.location, 'sheet=(.*)!', 1) = t.sheet
    """).fetchall():
        out[pid] = dict(kind=kind, noun=noun, code=code, source=src,
                        currency=ccy, scale=scale, basis=basis, base_year=base_yr)
    con.close()
    return out


def test_every_fixture_row_is_classified(tags):
    """A row that falls out of the join is invisible, not merely untagged."""
    assert set(tags) == {r[0] for r in ROWS}


def test_passenger_counts_leave_aero_revenue(tags):
    # The single largest group inside rev_aero at Newcastle: 9,764 points,
    # unit '000, values 272-4,906. Passenger counts, not revenue.
    assert tags["p_pax"]["code"] == "pax_total"
    assert tags["p_pax"]["kind"] == "volume"


def test_a_revenue_row_on_an_aero_sheet_stays_aero_revenue(tags):
    assert tags["p_rev"]["code"] == "rev_aero"
    assert tags["p_rev"]["kind"] == "level"


def test_yield_is_a_rate_not_a_level(tags):
    # The Newcastle gold figure, GBP 5.17, lives on a "Yield" row.
    assert tags["p_yield"]["code"] == "rev_aero_per_pax"
    assert tags["p_yield"]["kind"] == "rate"


def test_ebitda_is_not_promoted_to_aero_revenue(tags):
    # REGRESSION: the sheet said aero, so an EBITDA row became rev_aero. The
    # row label names the measure; the sheet does not get to overrule it.
    assert tags["p_ebitda"]["code"] == "ebitda"
    assert tags["p_ebitda"]["noun"] == "ebitda"


def test_per_pax_denominator_is_not_read_as_the_subject(tags):
    # REGRESSION: "Aero per Pax" classified as a passenger count because the
    # label contains "pax". The pax is the denominator.
    assert tags["p_perpax"]["code"] == "rev_aero_per_pax"
    assert tags["p_perpax"]["kind"] == "rate"


def test_segment_on_an_aero_sheet_is_a_segment_not_a_total(tags):
    # REGRESSION: summing segment components with the total double counts.
    assert tags["p_seg"]["code"] == "rev_aero_segment"
    assert tags["p_seg"]["kind"] == "component"


def test_non_aero_is_tested_before_aero(tags):
    # Every non-aero label contains the string "aero".
    assert tags["p_nonaero"]["code"] == "rev_nonaero"


def test_growth_and_index_carry_no_money_code(tags):
    # A growth rate or a CPI answering a revenue question is the worst outcome
    # this pass can produce, so these must be NULL by construction.
    for pid in ("p_growth", "p_cpi", "p_gdp"):
        assert tags[pid]["code"] is None, pid


def test_the_sheet_may_supply_a_noun_only_when_the_label_has_none(tags):
    assert tags["p_yield"]["source"] == "row_label"
    assert tags["p_seg"]["source"] == "sheet_fallback"
    assert tags["p_traffic"]["source"] == "sheet_fallback"


def test_scale_cue_yields_currency_scale_and_price_basis(tags):
    # Mixing 2008 real with nominal is a wrong answer that looks right, so the
    # basis has to survive the pass as its own field.
    assert tags["p_rev"]["currency"] == "GBP"
    assert tags["p_rev"]["scale"] == 1000
    assert tags["p_rev"]["basis"] == "nominal"
    assert tags["p_perpax"]["basis"] == "real"
    assert tags["p_perpax"]["base_year"] == 2008
    assert tags["p_perpax"]["scale"] == 1


def test_a_miscoded_point_is_corrected_not_merely_relabelled(tags):
    # Held as average_fare in the store; it is a revenue level.
    assert tags["p_fare"]["code"] == "rev_aero"
