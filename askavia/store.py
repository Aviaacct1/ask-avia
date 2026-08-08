"""Read-only adapter over the extraction store.

The problem this solves. The proof stores, the pilot store and the rebuilt 25-year store
need not name the same concept the same way, and the harvest was being rebuilt when this
was written. Writing the tools against one guessed column list would produce a service
that works on one store and fails, or worse quietly under-answers, on another.

So: the tools are written against CONCEPTS, and this module binds concepts to the actual
columns of the actual store at start-up, using the alias table in column_aliases.tsv.
A required concept with no matching column is a hard failure that names the store, the
table and the concept. Nothing is defaulted, nothing is inferred from column position.

Three properties are not negotiable:

  1. READ-ONLY. Every connection opens read_only=True. There is no write path in this
     module and there must never be one; corrections queue as proposals elsewhere.
     Opening read-only also means this is safe to run while a harvest writes elsewhere
     under the data root.
  2. The store DECLARES ITSELF. bind() returns what it bound to, and that travels into
     the audit log and into every tool result, so no answer can come from a store the
     operator did not think was being read.
  3. Year labels are read from the column named `year`, never inferred from position,
     and a cell is described by its own header. That rule is in the house data-integrity
     standard because breaking it has cost real money.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config as cfg
from .errors import SchemaContractError, StoreError, WriteRefused

ALIAS_FILE = Path(__file__).resolve().parent / "column_aliases.tsv"

# A table is a points table if it carries these concepts. Used to pick the fact table
# out of a store that also holds reference and manifest tables.
POINTS_SIGNATURE = ("value", "year", "metric_code")

# Any table whose name contains one of these is treated as the skip manifest, which
# get_source must be able to query in order to disclose "3 of 41 documents skipped".
SKIP_TABLE_HINTS = ("skip", "skipped", "manifest")


@dataclass(frozen=True)
class ConceptSpec:
    concept: str
    required: bool
    aliases: tuple[str, ...]


def load_alias_table(path: Path = ALIAS_FILE) -> tuple[ConceptSpec, ...]:
    if not path.is_file():
        raise StoreError(
            f"Column alias table not found at {path}. It is a tracked data file and a "
            f"clone should carry it; a missing copy means an incomplete checkout."
        )
    specs: list[ConceptSpec] = []
    with path.open("r", encoding="utf-8") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
                continue
            if len(row) < 3:
                raise StoreError(f"Malformed alias row in {path}: {row!r}")
            concept, required, aliases = row[0].strip(), row[1].strip(), row[2]
            specs.append(
                ConceptSpec(
                    concept=concept,
                    required=required.casefold() in ("yes", "true", "1"),
                    aliases=tuple(a.strip() for a in aliases.split(",") if a.strip()),
                )
            )
    if not specs:
        raise StoreError(f"Column alias table {path} holds no rows.")
    return tuple(specs)


@dataclass(frozen=True)
class ColumnMap:
    """concept -> actual column name in the bound table. Absent optional concepts are
    simply not present, and callers must test rather than assume."""

    mapping: dict[str, str]
    table: str
    store_file: str
    unmapped_columns: tuple[str, ...]

    def has(self, concept: str) -> bool:
        return concept in self.mapping

    def col(self, concept: str) -> str:
        try:
            return self.mapping[concept]
        except KeyError as exc:
            raise StoreError(
                f"Concept '{concept}' is not present in {self.store_file}:{self.table}. "
                f"Mapped concepts: {sorted(self.mapping)}. "
                f"Test with has() before reading an optional concept."
            ) from exc

    def quoted(self, concept: str) -> str:
        return '"' + self.col(concept).replace('"', '""') + '"'

    def describe(self) -> str:
        return (
            f"{self.store_file}:{self.table} mapped {len(self.mapping)} concept(s); "
            f"{len(self.unmapped_columns)} column(s) carried but unmapped"
        )


def map_columns(
    columns: Sequence[str], specs: Iterable[ConceptSpec], *, table: str, store_file: str
) -> ColumnMap:
    """Bind concepts to real columns. Raises SchemaContractError on a required gap."""
    lookup = {c.casefold(): c for c in columns}
    mapping: dict[str, str] = {}
    missing: list[str] = []

    for spec in specs:
        for alias in spec.aliases:
            actual = lookup.get(alias.casefold())
            if actual is not None:
                mapping[spec.concept] = actual
                break
        else:
            if spec.required:
                missing.append(spec.concept)

    if missing:
        raise SchemaContractError(
            store=store_file, table=table, missing=tuple(missing), present=tuple(columns)
        )

    used = {v.casefold() for v in mapping.values()}
    unmapped = tuple(c for c in columns if c.casefold() not in used)
    return ColumnMap(
        mapping=mapping, table=table, store_file=store_file, unmapped_columns=unmapped
    )


@dataclass(frozen=True)
class BoundStore:
    """What the service actually reads. Travels into the audit log and every result."""

    binding: cfg.StoreBinding
    store_file: Path
    points_table: str
    columns: ColumnMap
    skip_table: str | None
    row_count: int
    resolve_state: dict[str, float]

    def describe(self) -> str:
        resolved = ", ".join(f"{k} {v:.0f}%" for k, v in sorted(self.resolve_state.items()))
        return (
            f"{self.binding.kind} store, {self.store_file.name}, table "
            f"{self.points_table}, {self.row_count:,} rows; populated: "
            f"{resolved or 'not measured'}; skip manifest: {self.skip_table or 'ABSENT'}"
        )

    def provenance_note(self) -> str:
        """One line for the bottom of any answer, so a figure is never unattributed."""
        return (
            f"Source: Avia extraction store ({self.binding.kind}), "
            f"{self.store_file.name}, table {self.points_table}."
        )


class Store:
    """Opens the bound store read-only and answers queries. It never writes."""

    def __init__(self, conf: cfg.Config, specs: tuple[ConceptSpec, ...] | None = None):
        self.conf = conf
        self.specs = specs or load_alias_table()
        self.bound: BoundStore | None = None

    # -- connection ------------------------------------------------------------------

    def _connect(self, path: Path):
        try:
            import duckdb
        except ImportError as exc:
            raise StoreError(
                "duckdb is not installed in this interpreter. Run "
                "pip install -r requirements.txt inside this tool's own virtualenv."
            ) from exc
        try:
            return duckdb.connect(str(path), read_only=True)
        except Exception as exc:  # noqa: BLE001 - the reason must reach the operator
            raise StoreError(
                f"Could not open {path} read-only: {type(exc).__name__}: {exc}. "
                f"If a harvest or rebuild is writing this file, wait for it to finish "
                f"rather than opening it read-write."
            ) from exc

    # -- binding ---------------------------------------------------------------------

    def bind(self) -> BoundStore:
        """Pick the points table, map the columns, measure resolve state. Loud on gaps."""
        candidates = [p for p in self.conf.store.members if p.suffix == ".duckdb"]
        if not candidates:
            raise StoreError(
                f"No .duckdb member in {self.conf.store.path}. Parquet-only stores "
                f"(the pilot store_parts) are not yet supported by this service; bind "
                f"a proof store with {cfg.ENV_STORE_PATH} instead."
            )

        failures: list[str] = []
        for path in candidates:
            con = self._connect(path)
            try:
                tables = [
                    r[0]
                    for r in con.execute(
                        "select table_name from information_schema.tables order by 1"
                    ).fetchall()
                ]
                skip_table = next(
                    (t for t in tables if any(h in t.casefold() for h in SKIP_TABLE_HINTS)),
                    None,
                )
                for table in tables:
                    columns = [
                        r[0]
                        for r in con.execute(
                            "select column_name from information_schema.columns "
                            "where table_name = ? order by ordinal_position",
                            [table],
                        ).fetchall()
                    ]
                    lowered = {c.casefold() for c in columns}
                    if not self._looks_like_points(lowered):
                        continue
                    colmap = map_columns(
                        columns, self.specs, table=table, store_file=path.name
                    )
                    rows = con.execute(f'select count(*) from "{table}"').fetchone()[0]
                    self.bound = BoundStore(
                        binding=self.conf.store,
                        store_file=path,
                        points_table=table,
                        columns=colmap,
                        skip_table=skip_table,
                        row_count=rows,
                        resolve_state=self._measure_resolve(con, table, colmap, rows),
                    )
                    return self.bound
                failures.append(f"{path.name}: no table carries {list(POINTS_SIGNATURE)}")
            except SchemaContractError as exc:
                failures.append(str(exc))
            finally:
                con.close()

        raise StoreError(
            "No bindable points table found. Tried:\n  " + "\n  ".join(failures)
        )

    def _looks_like_points(self, lowered: set[str]) -> bool:
        for concept in POINTS_SIGNATURE:
            spec = next((s for s in self.specs if s.concept == concept), None)
            if spec is None or not any(a.casefold() in lowered for a in spec.aliases):
                return False
        return True

    def _measure_resolve(self, con, table: str, colmap: ColumnMap, rows: int) -> dict[str, float]:
        """Measure, never assume. taxonomy note 03 section 6.6 says the pilot store is
        pre-resolve and the proof stores are post-resolve; this checks rather than
        trusting either, because a pre-resolve store cannot answer a metric question and
        must say so instead of returning nothing."""
        if rows == 0:
            return {}
        state: dict[str, float] = {}
        for concept in ("metric_code", "temporality", "project_id", "entity"):
            if not colmap.has(concept):
                continue
            column = colmap.quoted(concept)
            try:
                filled = con.execute(
                    f'select count(*) from "{table}" where {column} is not null '
                    f"and cast({column} as varchar) <> ''"
                ).fetchone()[0]
            except Exception as exc:  # noqa: BLE001
                raise StoreError(
                    f"Could not measure resolve state for {concept} on {table}: {exc}"
                ) from exc
            state[concept] = 100.0 * filled / rows
        return state

    # -- guards ----------------------------------------------------------------------

    @staticmethod
    def refuse_write(*_args: Any, **_kwargs: Any) -> None:
        raise WriteRefused(
            "ask-avia is read-only over the extracted store. There is no write path."
        )
