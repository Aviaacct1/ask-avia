"""Pinned fixtures for the ask-avia selftests.

Builds a small DuckDB store shaped like the real full_v2: a core_points table, the
ask_points view that adds data_class, and a harvest_manifest table, so the tools run
against the same surfaces they meet in production. No network, no real store, no secrets.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from askavia import config as cfg
from askavia import store as st
from askavia.audit import AuditContext, AuditLog

# One quarantined path, matching config.EXCLUDED_CORPUS_PATHS, to prove get_source refuses.
QUARANTINED = cfg.EXCLUDED_CORPUS_PATHS[0] + "/answers.xlsx"

_CORE_ROWS = [
    # point_id, project_id, value_num, value_text, unit, currency, value_scale, metric,
    # metric_code, scenario, entity, entity_id, entity_type, year, vantage_year,
    # temporality, source_file, location, context, source_type_flag, review_status
    ("p1", "PJ_NCL", 5.17, "5.17", "GBP", "GBP", "unit", "aero rev per pax", "rev_aero",
     "base", "Newcastle", "NCL", "airport", 2009, 2010, "actual",
     "/Shared/Archive/2010/Newcastle/AeroRev.xlsx", "Model!B12",
     "Aeronautical revenue per passenger 5.17", "avia_generated", "accepted"),
    ("p2", "PJ_NCL", 4.65, "4.65", "GBP", "GBP", "unit", "aero rev per pax", "rev_aero",
     "base", "Newcastle", "NCL", "airport", 2015, 2013, "forecast",
     "/Shared/Archive/2010/Newcastle/AeroRev.xlsx", "Model!B18",
     "Aeronautical revenue per passenger forecast 4.65", "avia_generated", "auto"),
    ("p3", "PJ_ROM", 7.60, "7.60", "EUR", "EUR", "unit", "aero rev per pax", "rev_aero",
     "base", "Rome", "FCO", "airport", 2007, 2012, "actual",
     "/Shared/Archive/2012/AdR Rome/History.xlsx", "Yield!C4",
     "Aeronautical yield 7.6", "avia_generated", "accepted"),
    ("p4", "PJ_NCL", 4.9, "4.9", "m", "", "unit", "passengers", "pax",
     "base", "Newcastle", "NCL", "airport", 2009, 2010, "actual",
     "/Shared/Archive/2010/Newcastle/AeroRev.xlsx", "Model!B4",
     "Passengers 4.9m", "avia_generated", "accepted"),
    ("p5", "PJ_NCL", 3.3, "3.3", "GBP", "GBP", "unit", "misc", "rev_aero",
     "base", "Newcastle", "NCL", "airport", None, None, "",
     "/Shared/Archive/2010/Newcastle/AeroRev.xlsx", "Model!Z9",
     "unlabelled 3.3", "avia_generated", "auto"),
    ("QP1", "PJ_BEN", 99.0, "99.0", "GBP", "GBP", "unit", "answer", "rev_aero",
     "base", "Newcastle", "NCL", "airport", 2009, 2010, "actual",
     QUARANTINED, "A!1", "exam answer", "avia_generated", "accepted"),
]

_CORE_COLS = [
    "point_id", "project_id", "value_num", "value_text", "unit", "currency",
    "value_scale", "metric", "metric_code", "scenario", "entity", "entity_id",
    "entity_type", "year", "vantage_year", "temporality", "source_file", "location",
    "context", "source_type_flag", "review_status",
]

_MANIFEST_ROWS = [
    ("/Shared/Archive/2010/Newcastle/AeroRev.xlsx", "done"),
    ("/Shared/Archive/2010/Newcastle/Model.xlsx", "done"),
    ("/Shared/Archive/2010/Newcastle/Scan.pdf", "skipped"),
    ("/Shared/Archive/2010/Newcastle/Old.doc", "skipped"),
    ("/Shared/Archive/2010/Newcastle/Broken.xls", "failed"),
    ("/Shared/Archive/2012/AdR Rome/History.xlsx", "done"),
]


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    db = tmp_path / "fixture.duckdb"
    con = duckdb.connect(str(db))
    coldefs = ", ".join(
        f'"{c}" {"DOUBLE" if c == "value_num" else "INTEGER" if c in ("year", "vantage_year") else "VARCHAR"}'
        for c in _CORE_COLS
    )
    con.execute(f"CREATE TABLE core_points ({coldefs})")
    con.executemany(
        f'INSERT INTO core_points VALUES ({", ".join("?" for _ in _CORE_COLS)})', _CORE_ROWS
    )
    con.execute("""CREATE VIEW ask_points AS
        SELECT *, CASE WHEN lower(temporality)='forecast' THEN 'forecast'
                       WHEN lower(temporality)='actual'   THEN 'fact'
                       WHEN lower(scenario) IN ('high','low') THEN 'assumption'
                       ELSE 'unclassified' END AS data_class
        FROM core_points""")
    con.execute("CREATE TABLE harvest_manifest (path VARCHAR, status VARCHAR)")
    con.executemany("INSERT INTO harvest_manifest VALUES (?, ?)", _MANIFEST_ROWS)
    con.close()
    return db


@pytest.fixture
def config(store_path: Path, tmp_path: Path) -> cfg.Config:
    binding = cfg.StoreBinding(kind="explicit", path=store_path, members=(store_path,))
    return cfg.Config(
        data_root=tmp_path, store=binding, auth_token="test-token", port=8040,
        hostname="test", egnyte_domain="", egnyte_token="",
        audit_dir=tmp_path / "audit", staging_dir=tmp_path / "staging",
    )


@pytest.fixture
def store(config: cfg.Config):
    s = st.Store(config)
    s.bind()
    yield s
    s.close()


@pytest.fixture
def audit(config: cfg.Config) -> AuditLog:
    ctx = AuditContext(store_kind=config.store.kind, store_path=str(config.store.path),
                       service_version="test", hostname="test")
    return AuditLog(config.audit_dir, ctx)
