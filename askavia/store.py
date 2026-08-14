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

# When several tables carry the signature, bind these first, in order. `ask_points` is
# the service view over core_points that adds data_class; prefer it so every record can
# carry a class. Falls back to core_points, then to any other signature-matching table.
PREFERRED_POINTS = ("ask_points", "core_points")

# The harvest manifest, imported into the store as its own table, lets get_source
# disclose "N of M in-scope documents skipped". Matched by name.
MANIFEST_TABLE_HINTS = ("harvest_manifest", "skip", "manifest")

# Any table whose name contains one of these is treated as the skip manifest, which
# get_source must be able to query in order to disclose "3 of 41 documents skipped".
SKIP_TABLE_HINTS = ("skip", "skipped", "manifest")

# The document deduplication table written by build_dedup.py: one row per source_file,
# carrying is_canonical and the path of the canonical copy. Every new project at Avia
# copied the previous project's folder in wholesale, so the same workbook was harvested
# three and four times over; counting those as separate evidence made a figure stated
# once look corroborated. Filtering to canonical documents cut aeronautical revenue at
# Newcastle from 521,875 points to 68,870 while losing 142 of 14,113 distinct values.
CANONICAL_TABLE = "doc_canonical"


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
    # The document deduplication table written by build_dedup.py, if the store carries
    # one. None means an older store that predates it; queries then run undeduplicated
    # and say so, rather than silently counting copies as separate evidence.
    canonical_table: str | None = None

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
                # prefer the ask_points surface (has data_class), then core_points, then
                # any other signature-matching table, so binding is deterministic.
                preferred = [t for t in PREFERRED_POINTS if t in tables]
                ordered = preferred + [t for t in tables if t not in preferred]
                for table in ordered:
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
                        canonical_table=(
                            CANONICAL_TABLE if CANONICAL_TABLE in tables else None
                        ),
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

    # -- queries (read-only) ---------------------------------------------------------

    _RETURN_CONCEPTS = (
        "record_id", "metric_code", "entity", "entity_id", "year", "value", "unit",
        "currency", "value_scale", "data_class", "verification_status", "source",
        "source_type_flag", "temporality", "project_id",
    )

    def _conn(self):
        """Open ONE read-only connection to the bound store, reused across queries and
        closed by close(). bind() must have run first."""
        if self.bound is None:
            raise StoreError("query attempted before bind(); call Store.bind() first.")
        con = getattr(self, "_con", None)
        if con is None:
            con = self._connect(self.bound.store_file)
            self._con = con
        return con

    def close(self) -> None:
        con = getattr(self, "_con", None)
        if con is not None:
            con.close()
            self._con = None

    def _select(self, concepts):
        """Build the SELECT list from the concepts the store actually carries."""
        cm = self.bound.columns
        cols, names = [], []
        for concept in concepts:
            if cm.has(concept):
                cols.append(f"{cm.quoted(concept)} AS \"{concept}\"")
                names.append(concept)
        return cols, names

    def _build_where(self, *, metric=None, entity=None, geography=None, year_from=None,
                     year_to=None, data_class=None, status=None,
                     require_metric_code=False, canonical_only=True):
        """Build the WHERE clause shared by search() and summarise().

        Extracted so the two cannot drift: a summary that counted a different population
        from the one a subsequent search returns would be worse than no summary at all.

        Returns (where_fragments, params, applied, ignored). A filter on an ABSENT concept
        is never silently dropped; it lands in `ignored` so the caller can report it."""
        cm = self.bound.columns
        where, params, applied, ignored = [], [], {}, []

        def eq(concept, value, like=False):
            if value in (None, ""):
                return
            if not cm.has(concept):
                ignored.append(concept)
                return
            col = cm.quoted(concept)
            if like:
                where.append(f"{col} ILIKE ?"); params.append(f"%{value}%")
            else:
                where.append(f"{col} = ?"); params.append(value)
            applied[concept] = value

        eq("metric_code", metric, like=True)
        if entity not in (None, ""):
            parts = []
            for c in ("entity", "entity_id"):
                if cm.has(c):
                    parts.append(f"{cm.quoted(c)} ILIKE ?"); params.append(f"%{entity}%")
            if parts:
                where.append("(" + " OR ".join(parts) + ")"); applied["entity"] = entity
            else:
                ignored.append("entity")
        if geography not in (None, ""):
            if cm.has("entity"):
                where.append(f"{cm.quoted('entity')} ILIKE ?"); params.append(f"%{geography}%")
                applied["geography(via entity)"] = geography
            else:
                ignored.append("geography")
        if year_from is not None and cm.has("year"):
            where.append(f"TRY_CAST({cm.quoted('year')} AS INTEGER) >= ?")
            params.append(int(year_from)); applied["year_from"] = int(year_from)
        if year_to is not None and cm.has("year"):
            where.append(f"TRY_CAST({cm.quoted('year')} AS INTEGER) <= ?")
            params.append(int(year_to)); applied["year_to"] = int(year_to)
        eq("data_class", data_class)
        eq("verification_status", status)

        # A point with a blank metric_code does not know what it measures. It is still
        # held, and still returned by default, because hiding it would misrepresent the
        # store. But a caller that wants only points that know what they are can say so,
        # which is the difference between an answerable query and a fishing expedition.
        if require_metric_code:
            if cm.has("metric_code"):
                col = cm.quoted("metric_code")
                where.append(f"({col} IS NOT NULL AND CAST({col} AS VARCHAR) <> '')")
                applied["require_metric_code"] = True
            else:
                ignored.append("require_metric_code")

        # Defence in depth on the quarantine. The Benchmark folder is the exam paper for
        # the pipeline; the harvest excludes it and search() filters it again on the way
        # out, but excluding it in SQL means a COUNT can never be inflated by it either.
        if cm.has("source"):
            src = cm.quoted("source")
            for excluded in cfg.EXCLUDED_CORPUS_PATHS:
                where.append(f"COALESCE(CAST({src} AS VARCHAR), '') NOT ILIKE ?")
                params.append(f"{excluded}%")

        # Count each document once. Reported separately from `not_applicable`, which is
        # about filters the CALLER asked for; this is the store's own condition.
        # An UNCORRELATED IN, deliberately, not a correlated EXISTS. With
        #     EXISTS (SELECT 1 FROM doc_canonical dc WHERE dc.source_file = "source_file")
        # the unqualified outer reference binds to the INNER table first, so the predicate
        # compares dc.source_file with itself, is always true, and the filter silently does
        # nothing: the first build reported "applied" and returned all 521,875 rows. The
        # form below has nothing to shadow and DuckDB plans it as a semi-join.
        dedup = "unavailable"
        if canonical_only:
            if self.bound.canonical_table and cm.has("source"):
                src = cm.quoted("source")
                where.append(
                    f'{src} IN (SELECT source_file FROM '
                    f'"{self.bound.canonical_table}" WHERE is_canonical)'
                )
                dedup = "applied"
        else:
            dedup = "off (caller asked for every copy)"

        return where, params, applied, ignored, dedup

    def search(self, *, metric=None, entity=None, geography=None, year_from=None,
               year_to=None, data_class=None, status=None, limit=25,
               require_metric_code=False, canonical_only=True):
        """Parameterised read-only structured query. Returns (records, echoed_filters).
        Every filter is optional; a filter on an ABSENT concept is not silently dropped,
        it is reported in echoed_filters['not_applicable'] so the caller can see what was
        and was not applied and correct it in plain language."""
        where, params, applied, ignored, dedup = self._build_where(
            metric=metric, entity=entity, geography=geography,
            year_from=year_from, year_to=year_to, data_class=data_class,
            status=status, require_metric_code=require_metric_code,
            canonical_only=canonical_only,
        )

        cols, names = self._select(self._RETURN_CONCEPTS)
        sql = f'SELECT {", ".join(cols)} FROM "{self.bound.points_table}"'
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" LIMIT {int(limit)}"
        try:
            rows = self._conn().execute(sql, params).fetchall()
        except Exception as exc:  # noqa: BLE001 - the reason must reach the caller
            raise StoreError(
                f"search failed: {type(exc).__name__}: {exc}\nSQL: {sql}"
            ) from exc
        found = [Record(dict(zip(names, r)), self.bound) for r in rows]
        # defence in depth: the Benchmark folder is the exam paper and must never be
        # read or indexed. The pipeline excludes it, but if a record's source is inside
        # it, drop it here too rather than ever returning it.
        records = [r for r in found if not cfg.is_excluded(r.get("source", "") or "")]
        echoed = {
            "understood_as": applied,
            "not_applicable": sorted(set(ignored)),
            "limit": int(limit),
            "returned": len(records),
            "deduplication": dedup,
        }
        # Say which layer refused the quarantine, rather than leaving it silent. The
        # exclusion now happens in the WHERE clause, so the post-filter above normally
        # sees nothing to drop; it stays as belt and braces and reports separately if it
        # ever does fire, which would mean the SQL exclusion had a hole.
        if self.bound.columns.has("source"):
            echoed["quarantine"] = "excluded in query"
        if len(found) != len(records):
            echoed["quarantined_excluded"] = len(found) - len(records)
        return records, echoed

    # Facets a summary reports on, in the order they are most useful for narrowing.
    # metric_code first because a blank one is the defect that sends a caller hunting.
    _SUMMARY_FACETS = ("metric_code", "unit", "data_class", "temporality", "year")

    # How many path segments make a useful folder grouping. A full Egnyte path is far too
    # granular to count by; the first few segments name the area of the business.
    _SOURCE_FOLDER_DEPTH = 5

    def summarise(self, *, metric=None, entity=None, geography=None, year_from=None,
                  year_to=None, data_class=None, status=None,
                  require_metric_code=False, canonical_only=True, top=15):
        """What the store HOLDS for a filter, as grouped counts, without returning rows.

        The problem this solves. Asked a question it cannot answer from 25 records with
        blank metric codes, a model issues query after query with different filters, and
        every one is a full scan of 722m rows. One summary call tells it what is actually
        held, so the next call can be aimed. Ten scans become two.

        It is also the honest view of the store's condition: if a scope holds 40,000
        points and 38,000 of them have a blank metric_code, that is the first thing the
        answer should say, not something the reader infers from odd-looking records.

        Counts are exact, over the whole filtered population, not a sample. Facets are
        computed in ONE pass over a materialised subset rather than one scan per facet.
        Returns (summary, echoed_filters)."""
        cm = self.bound.columns
        where, params, applied, ignored, dedup = self._build_where(
            metric=metric, entity=entity, geography=geography,
            year_from=year_from, year_to=year_to, data_class=data_class,
            status=status, require_metric_code=require_metric_code,
            canonical_only=canonical_only,
        )

        # An unfiltered summary would materialise the entire store. Refuse rather than
        # spend minutes producing a number nobody asked a question about.
        if not applied:
            return (
                {"note": "a summary needs at least one filter; summarising the whole "
                         "store would scan every row and answer no question."},
                {"understood_as": applied, "not_applicable": sorted(set(ignored)),
                 "deduplication": dedup},
            )

        facets = [c for c in self._SUMMARY_FACETS if cm.has(c)]
        has_source = cm.has("source")
        if not facets and not has_source:
            return (
                {"note": "the bound store carries none of the concepts a summary groups "
                         f"by: {list(self._SUMMARY_FACETS)} or source."},
                {"understood_as": applied, "not_applicable": sorted(set(ignored)),
                 "deduplication": dedup},
            )

        select_bits = [f'{cm.quoted(c)} AS "{c}"' for c in facets]
        if has_source:
            select_bits.append(f'{cm.quoted("source")} AS "source"')

        sql = f'WITH f AS MATERIALIZED (SELECT {", ".join(select_bits)} '
        sql += f'FROM "{self.bound.points_table}"'
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += "), facets AS (\n"

        # One SELECT per facet over the materialised subset, unioned. '(blank)' is used
        # rather than dropping empties, because the count of blanks is the finding.
        unions = ["SELECT '_total' AS facet, 'all' AS key, COUNT(*) AS n FROM f"]
        for concept in facets:
            unions.append(
                f"SELECT '{concept}', COALESCE(NULLIF(CAST(\"{concept}\" AS VARCHAR), ''),"
                f" '(blank)'), COUNT(*) FROM f GROUP BY 2"
            )
        if has_source:
            folder = (
                "COALESCE(NULLIF(array_to_string(list_slice(string_split("
                f"COALESCE(CAST(\"source\" AS VARCHAR), ''), '/'), 1, "
                f"{self._SOURCE_FOLDER_DEPTH}), '/'), ''), '(blank)')"
            )
            unions.append(
                f"SELECT 'source_folder', {folder}, COUNT(*) FROM f GROUP BY 2"
            )
        sql += "\n  UNION ALL ".join(unions)
        sql += "\n) SELECT facet, key, n FROM facets "
        sql += "QUALIFY row_number() OVER (PARTITION BY facet ORDER BY n DESC) <= ? "
        sql += "ORDER BY facet, n DESC"
        params = list(params) + [int(top)]

        try:
            rows = self._conn().execute(sql, params).fetchall()
        except Exception as exc:  # noqa: BLE001 - the reason must reach the caller
            raise StoreError(
                f"summarise failed: {type(exc).__name__}: {exc}\nSQL: {sql}"
            ) from exc

        grouped: dict[str, list[dict]] = {}
        total = 0
        for facet, key, n in rows:
            if facet == "_total":
                total = int(n)
                continue
            grouped.setdefault(facet, []).append({"value": key, "count": int(n)})

        summary: dict[str, Any] = {"matching_points": total, "by": grouped,
                                   "top_per_facet": int(top)}

        # State the condition of the scope plainly rather than leaving it to be inferred.
        blank_metric = next(
            (e["count"] for e in grouped.get("metric_code", []) if e["value"] == "(blank)"),
            0,
        )
        if total and blank_metric:
            summary["untagged_metric_points"] = blank_metric
            summary["untagged_metric_share"] = round(100.0 * blank_metric / total, 1)
            summary["caveat"] = (
                f"{blank_metric:,} of {total:,} points in this scope ({summary['untagged_metric_share']}%) "
                f"carry NO metric_code, so they do not record what they measure. Filter "
                f"with require_metric_code to exclude them. Known defect: metric tagging "
                f"is incomplete and conflates totals, rates and factors; figures from this "
                f"scope are not yet validated."
            )
        echoed = {"understood_as": applied, "not_applicable": sorted(set(ignored)),
                  "deduplication": dedup}
        return summary, echoed

    def get_point(self, record_id):
        """Fetch one record by id (point_id), or None if the store does not hold it.
        None means 'no evidence held', which is different from an error."""
        cm = self.bound.columns
        if not cm.has("record_id"):
            raise StoreError("store has no record_id/point_id; get_source needs it.")
        concepts = self._RETURN_CONCEPTS + ("extract_text", "cell")
        cols, names = self._select(concepts)
        sql = (f'SELECT {", ".join(cols)} FROM "{self.bound.points_table}" '
               f'WHERE {cm.quoted("record_id")} = ? LIMIT 1')
        row = self._conn().execute(sql, [record_id]).fetchone()
        return Record(dict(zip(names, row)), self.bound) if row is not None else None

    def skip_disclosure(self, path_prefix=""):
        """Count in-scope files by status from the imported harvest manifest, so
        get_source can state 'N of M in-scope documents skipped'. If no manifest is
        bound, says so rather than implying zero skips."""
        if not self.bound.skip_table:
            return {"manifest": "ABSENT",
                    "note": "no harvest manifest in store; skip count cannot be disclosed"}
        con = self._conn()
        t = self.bound.skip_table
        cols = [r[0] for r in con.execute(
            "select column_name from information_schema.columns where table_name = ?",
            [t]).fetchall()]
        pc = next((c for c in cols if "path" in c.lower()), cols[0] if cols else None)
        sc = next((c for c in cols if "status" in c.lower()), None)
        if not sc:
            return {"manifest": t, "note": f"manifest has no status column; columns={cols}"}
        pcol = '"' + pc.replace('"', '""') + '"'
        scol = '"' + sc.replace('"', '""') + '"'
        where, params = "", []
        if path_prefix:
            where = f" WHERE {pcol} ILIKE ?"; params = [f"{path_prefix}%"]
        total = con.execute(f'select count(*) from "{t}"{where}', params).fetchone()[0]
        by_status = {r[0]: r[1] for r in con.execute(
            f'select {scol}, count(*) from "{t}"{where} group by 1 order by 2 desc',
            params).fetchall()}
        done = by_status.get("done", 0)
        return {"manifest": t, "scope": path_prefix or "(whole corpus)",
                "in_scope": total, "done": done, "not_done": total - done,
                "by_status": by_status}

    # -- guards ----------------------------------------------------------------------

    @staticmethod
    def refuse_write(*_args: Any, **_kwargs: Any) -> None:
        raise WriteRefused(
            "ask-avia is read-only over the extracted store. There is no write path."
        )


@dataclass(frozen=True)
class Record:
    """One returned data point, concept-keyed, carrying its own provenance. Never a bare
    number: a value travels with unit, year, class, verification status and source, so a
    figure can always be attributed and can never be quoted naked."""

    fields: dict
    bound: "BoundStore"

    def get(self, concept, default=None):
        return self.fields.get(concept, default)

    def citation(self) -> str:
        f = self.fields
        return (
            f"{f.get('metric_code', '?')}={f.get('value', '?')} {f.get('unit', '')} "
            f"({f.get('entity', '?')} {f.get('year', '?')}) "
            f"[class={f.get('data_class', 'unclassified')}, "
            f"status={f.get('verification_status', 'unverified')}] "
            f"source: {f.get('source', 'UNSOURCED')}"
        )

    def as_dict(self) -> dict:
        d = dict(self.fields)
        if self.bound is not None:
            d["_store"] = self.bound.provenance_note()
        return d
