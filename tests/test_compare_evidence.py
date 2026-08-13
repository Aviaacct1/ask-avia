"""Selftests for compare_evidence: it must align on code, and refuse to combine records
that differ in currency, unit, scale, basis or metric."""

from __future__ import annotations

from askavia.tools import compare_evidence


def test_comparable_same_basis_reports_spread_not_mean(store, audit):
    # p1 and p2: both NCL aeronautical revenue per pax in GBP; same metric/unit/currency.
    res = compare_evidence.run(store, audit, user="jc", record_ids=["p1", "p2"])
    assert res["verdict"] == "comparable"
    assert res["differs_on"] == {}
    assert res["spread"]["n"] == 2
    assert res["spread"]["min"] == 4.65 and res["spread"]["max"] == 5.17
    assert "mean" not in res["spread"]  # never a blended average
    # actual vs forecast is disclosed as a soft flag
    assert any("temporality" in f for f in res["flags"])


def test_different_currency_is_not_comparable(store, audit):
    # p1 (GBP) vs p3 (EUR): different currency and unit, must refuse to combine.
    res = compare_evidence.run(store, audit, user="jc", record_ids=["p1", "p3"])
    assert res["verdict"] == "not_comparable"
    assert "currency" in res["differs_on"]
    assert "spread" not in res
    assert any("currenc" in f.lower() for f in res["flags"])
    assert len(res["components"]) == 2  # components always shown


def test_different_metric_is_not_comparable(store, audit):
    # p1 (rev_aero) vs p4 (pax): different metrics entirely.
    res = compare_evidence.run(store, audit, user="jc", record_ids=["p1", "p4"])
    assert res["verdict"] == "not_comparable"
    assert "metric_code" in res["differs_on"]


def test_insufficient_when_fewer_than_two_held(store, audit):
    res = compare_evidence.run(store, audit, user="jc", record_ids=["p1", "missing-id"])
    assert res["outcome"] == "insufficient"
    assert res["missing"] == ["missing-id"]
