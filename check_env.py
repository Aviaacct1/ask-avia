#!/usr/bin/env python3
"""ask-avia environment check and store probe.

Why this file exists, in the words of the lesson that produced it: pip reported a
broken install as a warning and exited zero. A clone plus a data root is not a runnable
host. This script exits NON-ZERO when anything required is missing or broken, so a
provisioning step cannot pass by looking like it passed.

Run it as step 5 of provisioning, after: clone, copy the data root, set
AVIA_LOCAL_CACHE, pip install -r requirements.txt.

    py -3.12 check_env.py              full check, exits non-zero on any FAIL
    py -3.12 check_env.py --probe-only store probe only, no secrets required

--probe-only reports the ACTUAL state of the extraction store: which stores exist,
their tables, columns, row counts, and whether resolve has run over them. It asserts
nothing about what the store should contain. It opens every store READ-ONLY and takes
no lock, so it is safe to run while a harvest or a rebuild is writing elsewhere under
the data root.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from askavia import config as cfg  # noqa: E402

MIN_PYTHON = (3, 12)

REQUIRED_MODULES = (
    ("duckdb", "reads the extraction store"),
    ("openpyxl", "builds the precedent comparison workbook"),
    ("mcp", "serves the tools over the Model Context Protocol"),
    ("httpx", "talks to Egnyte for filing and source locators"),
)

# Columns the seven tools need in order to answer without inventing anything.
# Taxonomy v1.0 (Knowledge Programme note 03) and ingestion schema v1.1 (note 11).
# This is a CONTRACT, not an assumption: the probe reports what is actually present and
# the check fails loudly on a gap rather than substituting a neutral default. A missing
# table substituting a silent default is the recurring bug shape in this estate.
CONTRACT_COLUMNS = {
    "identity": ("entity", "entity_id", "entity_type"),
    "metric": ("metric_code",),
    "measure": ("value", "unit", "basis", "currency"),
    "time": ("year", "temporality"),
    "provenance": ("source_file", "source", "project_id"),
    "governance": ("source_type_flag", "inferred_fields"),
}

RESOLVE_MARKERS = ("metric_code", "temporality", "project_id", "entity")

_results: list[tuple[str, str, str]] = []


def record(status: str, check: str, detail: str = "") -> None:
    _results.append((status, check, detail))
    print(f"  [{status:4}] {check}" + (f"  -  {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------------
# Interpreter and dependencies
# ---------------------------------------------------------------------------------


def check_interpreter() -> None:
    section("Interpreter")
    v = sys.version_info
    if (v.major, v.minor) >= MIN_PYTHON:
        record("PASS", f"python {v.major}.{v.minor}.{v.micro}")
    else:
        record(
            "FAIL",
            f"python {v.major}.{v.minor}.{v.micro}",
            f"ask-avia requires {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or later",
        )

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        record("PASS", "virtualenv YES", sys.prefix)
    else:
        record(
            "WARN",
            "virtualenv NO, this is a shared Python",
            "Installing one tool's dependencies here changes every other tool that uses "
            "this interpreter. On 8 August a legacy set resolved starlette DOWN from "
            "1.6.0 to 1.3.1, and starlette is what the live portal server runs on. "
            "One virtual environment per tool on a shared host.",
        )


def check_modules() -> None:
    section("Dependencies")
    for name, why in REQUIRED_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - the reason must reach the operator
            record("FAIL", f"import {name}", f"{why}. {type(exc).__name__}: {exc}")
            continue
        # Some packages expose no __version__ (mcp is one), and "version unknown" for
        # the protocol library is exactly the thing a provisioning record must not say.
        version = getattr(mod, "__version__", None)
        if not version:
            try:
                from importlib.metadata import version as dist_version

                version = dist_version(name)
            except Exception:  # noqa: BLE001 - reported below, never hidden
                version = "version not reported by package or distribution metadata"
        record("PASS", f"import {name}", str(version))


# ---------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------


def check_config(require_secrets: bool) -> cfg.Config | None:
    section("Configuration")
    try:
        conf = cfg.load(require_secrets=require_secrets)
    except cfg.ConfigError as exc:
        record("FAIL", "configuration", str(exc))
        return None
    secret_keys = {"auth_token", "egnyte_token"}
    for key, value in conf.redacted().items():
        if value != "MISSING":
            status = "PASS"
        elif key in secret_keys and not require_secrets:
            # --probe-only exists to be run before secrets are provisioned. A missing
            # secret is reported, never hidden, but it does not fail the probe.
            status = "WARN"
        else:
            status = "FAIL"
        record(status, key, value)
    record("PASS", "store binding", conf.store.describe())
    return conf


def check_quarantine() -> None:
    section("Benchmark quarantine (AIP Note 2, O9)")
    if not cfg.EXCLUDED_CORPUS_PATHS:
        record("FAIL", "exclusion configured", "EXCLUDED_CORPUS_PATHS is empty")
        return
    for path in cfg.EXCLUDED_CORPUS_PATHS:
        record("PASS", "excluded from corpus", path)
    probes = {
        cfg.EXCLUDED_CORPUS_PATHS[0]: True,
        cfg.EXCLUDED_CORPUS_PATHS[0] + "/Note 5 v1.1.md": True,
        cfg.EXCLUDED_CORPUS_PATHS[0].replace("/", "\\"): True,
        "/Shared/Company Data/14 Avia/AI_System/AIP/AIP Note 3.md": False,
    }
    for probe, expected in probes.items():
        got = cfg.is_excluded(probe)
        record(
            "PASS" if got == expected else "FAIL",
            f"is_excluded({'inside' if expected else 'outside'})",
            probe,
        )


# ---------------------------------------------------------------------------------
# Store probe. Reports actual state. Asserts nothing it has not read.
# ---------------------------------------------------------------------------------


def _probe_duckdb(path: Path) -> None:
    import duckdb

    try:
        con = duckdb.connect(str(path), read_only=True)
    except Exception as exc:  # noqa: BLE001
        record("FAIL", f"open {path.name}", f"{type(exc).__name__}: {exc}")
        return

    try:
        tables = [r[0] for r in con.execute(
            "select table_name from information_schema.tables order by 1"
        ).fetchall()]
        record("PASS", f"open {path.name}", f"{len(tables)} table(s): {', '.join(tables) or 'none'}")

        for table in tables:
            try:
                cols = con.execute(
                    "select column_name, data_type from information_schema.columns "
                    "where table_name = ? order by ordinal_position",
                    [table],
                ).fetchall()
                rows = con.execute(f'select count(*) from "{table}"').fetchone()[0]
            except Exception as exc:  # noqa: BLE001
                record("WARN", f"{path.name}:{table}", f"unreadable: {exc}")
                continue

            names = [c for c, _ in cols]
            print(f"        {table}  rows={rows:,}")
            print(f"          columns: {', '.join(f'{c}:{d}' for c, d in cols)}")

            present = {
                group: [c for c in wanted if c in names]
                for group, wanted in CONTRACT_COLUMNS.items()
            }
            if not present["metric"] and not present["measure"]:
                continue  # not a points table; nothing to say about it

            for group, wanted in CONTRACT_COLUMNS.items():
                have = present[group]
                missing = [c for c in wanted if c not in names]
                record(
                    "PASS" if have else "WARN",
                    f"{table}: contract {group}",
                    f"present {have or 'none'}; absent {missing or 'none'}",
                )

            # Resolve state, measured rather than assumed. taxonomy note 03 section 6.6:
            # the pilot store is pre-resolve with these fields blank; the proof stores
            # are post-resolve. Do not take either on trust.
            for marker in RESOLVE_MARKERS:
                if marker not in names or rows == 0:
                    continue
                try:
                    filled = con.execute(
                        f'select count(*) from "{table}" '
                        f"where \"{marker}\" is not null and cast(\"{marker}\" as varchar) <> ''"
                    ).fetchone()[0]
                except Exception as exc:  # noqa: BLE001
                    record("WARN", f"{table}: resolve marker {marker}", str(exc))
                    continue
                pct = 100.0 * filled / rows
                record(
                    "PASS" if pct > 0 else "WARN",
                    f"{table}: {marker} populated",
                    f"{filled:,} of {rows:,} ({pct:.1f}%)"
                    + ("  <- looks PRE-resolve" if pct == 0 else ""),
                )

            if "source_type_flag" in names:
                try:
                    flags = con.execute(
                        f'select "source_type_flag", count(*) from "{table}" '
                        f'group by 1 order by 2 desc limit 10'
                    ).fetchall()
                    record(
                        "PASS",
                        f"{table}: source_type_flag",
                        "; ".join(f"{f or 'blank'}={n:,}" for f, n in flags),
                    )
                except Exception as exc:  # noqa: BLE001
                    record("WARN", f"{table}: source_type_flag", str(exc))

        skip_tables = [t for t in tables if "skip" in t.lower()]
        record(
            "PASS" if skip_tables else "WARN",
            f"{path.name}: skip manifest queryable (Note 2, O2)",
            ", ".join(skip_tables) if skip_tables else "no table with 'skip' in its name",
        )
    finally:
        con.close()


def _probe_parquet(paths: tuple[Path, ...]) -> None:
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        sample = str(paths[0]).replace("'", "''")
        cols = con.execute(f"describe select * from read_parquet('{sample}')").fetchall()
        record(
            "PASS",
            f"parquet store: {len(paths)} part(s)",
            ", ".join(f"{c[0]}:{c[1]}" for c in cols),
        )
    except Exception as exc:  # noqa: BLE001
        record("FAIL", "parquet store", f"{type(exc).__name__}: {exc}")
    finally:
        con.close()


def probe_store(conf: cfg.Config) -> None:
    section(f"Store probe - {conf.store.describe()}")
    duck = [p for p in conf.store.members if p.suffix == ".duckdb"]
    parquet = tuple(p for p in conf.store.members if p.suffix == ".parquet")
    if not duck and not parquet:
        record("FAIL", "store members", "no readable member files")
        return
    for path in duck:
        _probe_duckdb(path)
    if parquet:
        _probe_parquet(parquet)


def probe_pipeline(conf: cfg.Config) -> None:
    section("Pipeline reference files")
    for landmark in cfg.REQUIRED_PIPELINE_FILES:
        path = conf.data_root / landmark
        if path.is_file():
            lines = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
            record("PASS", landmark.name, f"{path} ({lines} lines)")
        else:
            record("WARN", landmark.name, f"not found at {path}")


# ---------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="ask-avia environment check and store probe")
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="report the store's actual state without requiring service secrets",
    )
    args = parser.parse_args()

    print("ask-avia check_env")
    print(f"data root variable: {cfg.ENV_DATA_ROOT}={os.environ.get(cfg.ENV_DATA_ROOT, '(unset)')}")

    check_interpreter()
    check_modules()
    check_quarantine()
    conf = check_config(require_secrets=not args.probe_only)
    if conf is not None:
        probe_store(conf)
        probe_pipeline(conf)

    section("Summary")
    fails = [r for r in _results if r[0] == "FAIL"]
    warns = [r for r in _results if r[0] == "WARN"]
    print(f"  {len(_results) - len(fails) - len(warns)} pass, {len(warns)} warn, {len(fails)} fail")
    for _, check, detail in fails:
        print(f"  FAIL: {check}  -  {detail}")
    if fails:
        print("\ncheck_env FAILED. This host is not provisioned.")
        return 1
    print("\ncheck_env passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
