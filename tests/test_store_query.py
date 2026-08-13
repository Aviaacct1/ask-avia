"""Selftests for the store query layer, against the pinned fixture store."""

from __future__ import annotations

import duckdb

from askavia import config as cfg
from askavia import store as st


def test_binds_ask_points_and_manifest(store):
    # ask_points is preferred over core_points so records can carry data_class, and the
    # imported manifest is found for skip disclosure.
    assert store.bound.points_table == "ask_points"
    assert store.bound.columns.has("data_class")
    assert store.bound.skip_table == "harvest_manifest"


def test_search_applies_filters_and_echoes(store):
    recs, echoed = store.search(metric="aero", entity="NCL", year_from=2009, year_to=2015)
    assert sorted(r.get("record_id") for r in recs) == ["p1", "p2"]
    assert echoed["understood_as"]["metric_code"] == "aero"
    assert echoed["understood_as"]["entity"] == "NCL"
    assert echoed["understood_as"]["year_from"] == 2009
    assert echoed["not_applicable"] == []
    assert {r.get("record_id"): r.get("data_class") for r in recs} == {
        "p1": "fact", "p2": "forecast"
    }


def test_search_matches_name_or_iata(store):
    by_iata = {r.get("record_id") for r in store.search(entity="NCL")[0]}
    by_name = {r.get("record_id") for r in store.search(entity="Newcastle")[0]}
    assert by_iata == by_name and "p1" in by_iata


def test_search_excludes_quarantined_source(store):
    recs, echoed = store.search(metric="aero", entity="NCL")
    assert "QP1" not in {r.get("record_id") for r in recs}
    assert echoed.get("quarantined_excluded") == 1


def test_get_point_present_and_missing(store):
    r = store.get_point("p1")
    assert r is not None and r.get("value") == 5.17 and r.get("unit") == "GBP"
    assert store.get_point("does-not-exist") is None


def test_skip_disclosure_counts(store):
    d = store.skip_disclosure("/Shared/Archive/2010/Newcastle")
    assert d["in_scope"] == 5 and d["done"] == 2 and d["not_done"] == 3
    assert d["by_status"]["skipped"] == 2 and d["by_status"]["failed"] == 1


def test_absent_filters_are_reported_not_ignored(tmp_path):
    # A store with neither data_class nor verification_status. Filtering on them must be
    # reported in not_applicable, never silently dropped.
    db = tmp_path / "thin.duckdb"
    con = duckdb.connect(str(db))
    con.execute('CREATE TABLE core_points ("point_id" VARCHAR, "value_num" DOUBLE, '
                '"year" INTEGER, "metric_code" VARCHAR, "entity_id" VARCHAR, '
                '"unit" VARCHAR, "source_file" VARCHAR)')
    con.execute("INSERT INTO core_points VALUES "
                "('x1', 1.0, 2009, 'rev_aero', 'NCL', 'GBP', '/Shared/a.xlsx')")
    con.close()
    conf = cfg.Config(
        data_root=tmp_path,
        store=cfg.StoreBinding(kind="explicit", path=db, members=(db,)),
        auth_token="t", port=8040, hostname="t", egnyte_domain="", egnyte_token="",
        audit_dir=tmp_path / "a", staging_dir=tmp_path / "s",
    )
    s = st.Store(conf)
    s.bind()
    try:
        recs, echoed = s.search(metric="aero", data_class="fact", status="accepted")
        assert {"data_class", "verification_status"}.issubset(set(echoed["not_applicable"]))
        assert len(recs) == 1  # the metric filter still applied
    finally:
        s.close()
