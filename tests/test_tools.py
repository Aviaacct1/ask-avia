"""Selftests for the tools: search_datapoints and get_source."""

from __future__ import annotations

import json

import pytest

from askavia.tools import search_datapoints, get_source
from askavia.errors import QuarantineError


def test_search_datapoints_result_and_audit(store, audit, config):
    res = search_datapoints.run(
        store, audit, user="jc", metric="aero", entity="NCL",
        year_from=2009, year_to=2015,
    )
    assert res["count"] == 2
    assert res["understood_as"]["entity"] == "NCL"
    assert res["not_applicable"] == []
    assert all(r.get("source") for r in res["records"])  # nothing returned unsourced
    assert all("data_class" in r for r in res["records"])

    files = list(config.audit_dir.glob("*.jsonl"))
    assert files, "audit log was not written"
    entry = json.loads(files[0].read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["tool"] == "search_datapoints"
    assert set(entry["record_ids"]) == {"p1", "p2"}


def test_search_datapoints_no_evidence(store, audit):
    res = search_datapoints.run(store, audit, user="jc", metric="does-not-exist")
    assert res["count"] == 0
    assert "no evidence held" in res["note"]


def test_get_source_verbatim_locator_and_skip_disclosure(store, audit):
    res = get_source.run(store, audit, user="jc", record_id="p1")
    assert res["outcome"] == "ok"
    assert res["locator"]["egnyte_path"].endswith("AeroRev.xlsx")
    assert res["locator"]["cell"] == "Model!B12"
    assert res["verbatim"].startswith("Aeronautical revenue")
    assert res["skip_disclosure"]["not_done"] == 3
    assert res["skip_disclosure"]["in_scope"] == 5
    assert "source:" in res["citation"]


def test_get_source_no_evidence(store, audit):
    res = get_source.run(store, audit, user="jc", record_id="nope")
    assert res["outcome"] == "no_evidence"


def test_get_source_refuses_quarantined_record(store, audit):
    # QP1's source sits in the excluded Benchmark folder: refuse, never return content.
    with pytest.raises(QuarantineError):
        get_source.run(store, audit, user="jc", record_id="QP1")
