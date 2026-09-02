"""Schema analysis step — pure Python, no LLM.

Ported from lexi's mcfly agent (packages/agent-crafting/sl-building-agents/
src/sl_building_agents/mcfly/schema_analyst.py), which targets Malloy. This
version is Cube-agnostic in exactly the same way the original is
Malloy-agnostic: nothing below emits any target-language syntax, it only
computes facts (column types, candidate aggregations, candidate joins,
processing order) from real data via DuckDB. Two changes from the original:

- Uses this package's own ``datasource`` module instead of lexi's internal
  ``datasources``/``datasources.duckdb`` (a uv-workspace-only package of the
  ``lexi`` monorepo, not published to PyPI, so not importable here).
- Drops the reserved-word/``safe_name`` mechanism entirely: verified against
  a real compile that Cube has no equivalent problem — dimension/measure
  names like "year", "month", or "count" compile fine as plain YAML keys,
  unlike Malloy's reserved language keywords.

Two join-detection strategies (see ``analyze_schema``'s
``verify_referential_integrity`` flag):

- Default: cheap, name-based — finds a shared ``_id``/``id`` column across
  tables, then confirms direction by type + real uniqueness (not by a
  ``dim_``/``fact_`` table-naming convention). Fast, but only catches
  relationships whose FK and PK columns happen to share the same name.
- ``verify_referential_integrity=True``: content-based — compares real
  column values across every same-type column pair, independent of naming
  entirely (e.g. finds ``flights.carrier`` -> ``carriers.code`` even
  though the column names differ). Slower, but doesn't miss relationships
  that don't follow a naming convention. Also rejects coincidental matches
  a plain "every value exists on the other side" check would accept: a
  column whose non-null values simply aren't a real subset; a
  low-cardinality column that happens to fit entirely inside a large,
  dense PK id space by chance (e.g. a 3-value category code landing
  inside a 20,000-row surrogate key); and — seen in practice on a
  dataset with many small dimension tables, each using its own small
  sequential surrogate key — a column matching several distinct tables'
  keys purely because those key ranges coincidentally overlap, in which
  case an exact column-name match is additionally required to keep any
  of that column's candidates.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import json
import re
from typing import Any

import duckdb

from . import datasource

_KEY_LIKE_PATTERN = re.compile(r"(^id$|_id$|key$|number$)", re.IGNORECASE)
_AVG_ONLY_PATTERN = re.compile(
    r"rating|score|percent|rate|ratio|average", re.IGNORECASE
)
_CALENDAR_PART_PATTERN = re.compile(
    r"(year|quarter|month|week)(_?number)?$"
    r"|day_?of_?(week|month|year)(_?number)?$",
    re.IGNORECASE,
)


class AggFunc(str, enum.Enum):
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    STDDEV = "stddev"
    COUNT_DISTINCT = "count_distinct"
    AVG_PER_GROUP = "avg_per_group"
    RATIO_OF_SUMS = "ratio_of_sums"
    PERCENT_OF_TOTAL = "percent_of_total"
    AVG_SCALED_TO_PERCENT = "avg_scaled_to_percent"


def _measure_candidates(
    column_name: str, atomic_type: datasource.AtomicType
) -> list[AggFunc] | None:
    """Return candidate aggregations for a column, or None if it doesn't
    look like a measure candidate at all. See module docstring in the
    original mcfly schema_analyst.py for the full rationale behind each
    branch — unchanged here, this is target-language-agnostic logic."""
    if atomic_type.kind == "number_type" and _CALENDAR_PART_PATTERN.search(
        column_name
    ):
        return None
    if _KEY_LIKE_PATTERN.search(column_name):
        return [AggFunc.COUNT_DISTINCT]
    if atomic_type.kind == "string_type":
        return [AggFunc.COUNT_DISTINCT]
    if atomic_type.kind != "number_type":
        return None
    if _AVG_ONLY_PATTERN.search(column_name):
        return [
            AggFunc.AVG,
            AggFunc.MIN,
            AggFunc.MAX,
            AggFunc.STDDEV,
            AggFunc.AVG_SCALED_TO_PERCENT,
        ]
    return [
        AggFunc.SUM,
        AggFunc.AVG,
        AggFunc.MIN,
        AggFunc.MAX,
        AggFunc.STDDEV,
        AggFunc.AVG_PER_GROUP,
        AggFunc.RATIO_OF_SUMS,
        AggFunc.PERCENT_OF_TOTAL,
    ]


@dataclasses.dataclass
class ColumnSchema:
    name: str
    atomic_type: datasource.AtomicType
    dialect_type: str
    measure_candidates: list[AggFunc] | None
    count_distinct_needs_trim: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.dialect_type,
            "measure_candidates": (
                [a.value for a in self.measure_candidates]
                if self.measure_candidates is not None
                else None
            ),
        }


@dataclasses.dataclass
class JoinRelation:
    join_type: str  # "join_one" or "join_many" — a candidate, the LLM confirms/overrides
    target_table: str
    fk_column: str
    pk_column: str
    integrity_warning: str | None = None
    verified: bool = False


@dataclasses.dataclass
class TableSchema:
    name: str
    path: str
    columns: list[ColumnSchema]
    sample_rows: list[dict[str, Any]]
    primary_key: str | None = None
    joins: list[JoinRelation] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "primary_key": self.primary_key,
            "joins": [dataclasses.asdict(j) for j in self.joins],
            "columns": [c.to_dict() for c in self.columns],
            "sample_rows": self.sample_rows,
        }


def _candidate_id_columns(tables: list[TableSchema]) -> dict[str, list[str]]:
    col_to_tables: dict[str, list[str]] = {}
    for table in tables:
        for col in table.columns:
            if col.name == "id" or col.name.endswith("_id"):
                col_to_tables.setdefault(col.name, []).append(table.name)
    return {name: owners for name, owners in col_to_tables.items() if len(owners) > 1}


def _column_by_name(table: TableSchema, name: str) -> ColumnSchema | None:
    for col in table.columns:
        if col.name == name:
            return col
    return None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_unique_and_not_null(
    conn: duckdb.DuckDBPyConnection, path: str, column: str
) -> bool:
    quoted = _quote_ident(column)
    reader = datasource.reader_for(path)
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) = COUNT(DISTINCT {quoted}) AS is_unique,
            COUNT(*) FILTER (WHERE {quoted} IS NULL) = 0 AS no_nulls
        FROM {reader}(?)
        """,  # noqa: S608 — reader from the fixed READERS mapping; column escaped by _quote_ident
        [path],
    ).fetchone()
    if row is None:
        return False
    is_unique, no_nulls = row
    return bool(is_unique) and bool(no_nulls)


def _count_distinct_needs_trim(
    conn: duckdb.DuckDBPyConnection, path: str, column: str
) -> bool:
    quoted = _quote_ident(column)
    reader = datasource.reader_for(path)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT {quoted}) = COUNT(DISTINCT trim({quoted}))
        FROM {reader}(?)
        """,  # noqa: S608
        [path],
    ).fetchone()
    if row is None:
        return False
    (same,) = row
    return not bool(same)


_FRACTION_SCALE_THRESHOLD = 1.0


def _avg_needs_percent_scaling(
    conn: duckdb.DuckDBPyConnection, path: str, column: str
) -> bool:
    quoted = _quote_ident(column)
    reader = datasource.reader_for(path)
    row = conn.execute(
        f"""
        SELECT MAX(ABS({quoted})) <= {_FRACTION_SCALE_THRESHOLD}
        FROM {reader}(?)
        WHERE {quoted} IS NOT NULL
        """,  # noqa: S608
        [path],
    ).fetchone()
    if row is None or row[0] is None:
        return False
    return bool(row[0])


_LARGE_PK_CARDINALITY = 1000
_MIN_PLAUSIBLE_FK_CARDINALITY = 20
_MAX_TOLERABLE_ORPHAN_RATE = 0.5


@dataclasses.dataclass
class _ReferentialIntegrityResult:
    plausible: bool
    orphaned_count: int = 0
    fk_non_null_count: int = 0


def _referential_integrity_check(
    conn: duckdb.DuckDBPyConnection,
    fk_path: str,
    fk_column: str,
    pk_path: str,
    pk_column: str,
) -> _ReferentialIntegrityResult:
    quoted_fk = _quote_ident(fk_column)
    quoted_pk = _quote_ident(pk_column)
    fk_reader = datasource.reader_for(fk_path)
    pk_reader = datasource.reader_for(pk_path)

    fk_row = conn.execute(
        f"SELECT COUNT(DISTINCT {quoted_fk}) FROM {fk_reader}(?)",  # noqa: S608
        [fk_path],
    ).fetchone()
    pk_row = conn.execute(
        f"SELECT COUNT(DISTINCT {quoted_pk}) FROM {pk_reader}(?)",  # noqa: S608
        [pk_path],
    ).fetchone()
    if fk_row is None or pk_row is None:
        return _ReferentialIntegrityResult(plausible=False)
    fk_distinct, pk_distinct = fk_row[0], pk_row[0]
    if (
        pk_distinct >= _LARGE_PK_CARDINALITY
        and fk_distinct < _MIN_PLAUSIBLE_FK_CARDINALITY
    ):
        return _ReferentialIntegrityResult(plausible=False)

    row = conn.execute(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE fk.{quoted_fk} IS NOT NULL) AS fk_non_null_count,
            COUNT(*) FILTER (
                WHERE fk.{quoted_fk} IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM {pk_reader}(?) AS pk
                    WHERE pk.{quoted_pk} = fk.{quoted_fk}
                )
            ) AS orphaned_count
        FROM {fk_reader}(?) AS fk
        """,  # noqa: S608
        [pk_path, fk_path],
    ).fetchone()
    if row is None:
        return _ReferentialIntegrityResult(plausible=False)
    fk_non_null_count, orphaned_count = row
    if (
        fk_non_null_count > 0
        and orphaned_count / fk_non_null_count > _MAX_TOLERABLE_ORPHAN_RATE
    ):
        return _ReferentialIntegrityResult(plausible=False)
    return _ReferentialIntegrityResult(
        plausible=True,
        orphaned_count=orphaned_count,
        fk_non_null_count=fk_non_null_count,
    )


def _detect_relationships_by_name(
    tables: list[TableSchema], conn: duckdb.DuckDBPyConnection
) -> None:
    by_name = {t.name: t for t in tables}
    for col_name, owner_names in _candidate_id_columns(tables).items():
        pk_owner: str | None = None
        for owner_name in owner_names:
            table = by_name[owner_name]
            column = _column_by_name(table, col_name)
            if column is None:
                continue
            other_types = {
                _column_by_name(by_name[o], col_name).atomic_type.kind  # type: ignore[union-attr]
                for o in owner_names
                if _column_by_name(by_name[o], col_name) is not None
            }
            if len(other_types) > 1:
                continue
            if _is_unique_and_not_null(conn, table.path, col_name):
                pk_owner = owner_name
                break

        if pk_owner is None:
            continue

        pk_table = by_name[pk_owner]
        for owner_name in owner_names:
            if owner_name == pk_owner:
                continue
            fk_table = by_name[owner_name]
            fk_table.joins.append(
                JoinRelation(
                    join_type="join_one",
                    target_table=pk_owner,
                    fk_column=col_name,
                    pk_column=col_name,
                )
            )
        if pk_table.primary_key is None:
            pk_table.primary_key = col_name


def _pk_candidates(
    tables: list[TableSchema], conn: duckdb.DuckDBPyConnection
) -> dict[str, list[ColumnSchema]]:
    candidates: dict[str, list[ColumnSchema]] = {}
    for table in tables:
        for col in table.columns:
            if _is_unique_and_not_null(conn, table.path, col.name):
                candidates.setdefault(table.name, []).append(col)
    return candidates


_MAX_TARGETS_BEFORE_NAME_CORROBORATION = 1


def _integrity_warning(
    result: _ReferentialIntegrityResult,
    *,
    fk_column_name: str,
    pk_column_name: str,
    pk_table_name: str,
) -> str:
    pct = result.orphaned_count / result.fk_non_null_count * 100
    return (
        f"Referential integrity is imperfect: {result.orphaned_count:,} of "
        f"{result.fk_non_null_count:,} {fk_column_name} values ({pct:.1f}%) "
        f"have no matching {pk_column_name} in {pk_table_name}. This join "
        "may return null for unmatched rows."
    )


def _detect_relationships_by_content(
    tables: list[TableSchema],
    conn: duckdb.DuckDBPyConnection,
    *,
    allow_partial_referential_integrity: bool,
) -> None:
    by_name = {t.name: t for t in tables}
    pk_candidates = _pk_candidates(tables, conn)

    raw_matches: dict[tuple[str, str], list[tuple[str, str, str | None]]] = {}

    for pk_table_name, pk_columns in pk_candidates.items():
        pk_table = by_name[pk_table_name]
        for pk_column in pk_columns:
            for fk_table in tables:
                if fk_table.name == pk_table_name:
                    continue
                for fk_column in fk_table.columns:
                    if fk_column.atomic_type.kind != pk_column.atomic_type.kind:
                        continue
                    result = _referential_integrity_check(
                        conn,
                        fk_table.path,
                        fk_column.name,
                        pk_table.path,
                        pk_column.name,
                    )
                    if not result.plausible:
                        continue
                    if result.orphaned_count > 0:
                        if not allow_partial_referential_integrity:
                            continue
                        warning = _integrity_warning(
                            result,
                            fk_column_name=fk_column.name,
                            pk_column_name=pk_column.name,
                            pk_table_name=pk_table_name,
                        )
                    else:
                        warning = None
                    raw_matches.setdefault(
                        (fk_table.name, fk_column.name), []
                    ).append((pk_table_name, pk_column.name, warning))

    for (fk_table_name, fk_column_name), matches in raw_matches.items():
        if len(matches) > _MAX_TARGETS_BEFORE_NAME_CORROBORATION:
            matches = [
                (target_table_name, pk_column_name, warning)
                for target_table_name, pk_column_name, warning in matches
                if pk_column_name.lower() == fk_column_name.lower()
            ]
        fk_table = by_name[fk_table_name]
        for target_table_name, pk_column_name, warning in matches:
            fk_table.joins.append(
                JoinRelation(
                    join_type="join_one",
                    target_table=target_table_name,
                    fk_column=fk_column_name,
                    pk_column=pk_column_name,
                    integrity_warning=warning,
                    verified=warning is None,
                )
            )
            pk_table = by_name[target_table_name]
            if pk_table.primary_key is None:
                pk_table.primary_key = pk_column_name


def _fill_unshared_primary_keys(
    tables: list[TableSchema], conn: duckdb.DuckDBPyConnection
) -> None:
    """Assign a primary_key to any table that still doesn't have one after
    join detection, by checking its own key-like columns for real
    uniqueness — independent of whether that column name is shared with
    any other table.

    This has no equivalent in mcfly's original (Malloy) schema_analyst.py:
    there, ``primary_key`` is only ever set as a side effect of relating
    two tables (a column name shared across >=2 tables, confirmed unique
    on one side). A fact table whose own PK column isn't referenced by
    name anywhere else (e.g. ``order_item_id`` when no other table has a
    column literally named that) never gets one from that mechanism —
    apparently fine for Malloy, but a real, confirmed Cube-specific
    requirement: Cube's compiler rejects a cube that defines joins but has
    no declared primary key ("primary key ... is required when join is
    defined in order to make aggregates work properly"), caught by
    actually compiling a generated cube with a real join and no assigned
    primary key. Scoped to key-like column names only (matching
    ``_KEY_LIKE_PATTERN``, not just any unique column) so an incidentally-
    unique business column (e.g. a unique email on a small sample) doesn't
    get misidentified as the table's identity column."""
    for table in tables:
        if table.primary_key is not None:
            continue
        for col in table.columns:
            if not _KEY_LIKE_PATTERN.search(col.name):
                continue
            if _is_unique_and_not_null(conn, table.path, col.name):
                table.primary_key = col.name
                break


def _detect_relationships(
    tables: list[TableSchema],
    *,
    verify_referential_integrity: bool,
    allow_partial_referential_integrity: bool,
) -> None:
    conn = duckdb.connect(":memory:")
    try:
        if verify_referential_integrity:
            _detect_relationships_by_content(
                tables,
                conn,
                allow_partial_referential_integrity=allow_partial_referential_integrity,
            )
        else:
            _detect_relationships_by_name(tables, conn)
        _fill_unshared_primary_keys(tables, conn)
    finally:
        conn.close()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode()
    return value


def analyze_schema(
    connection_folder: str,
    *,
    verify_referential_integrity: bool = False,
    allow_partial_referential_integrity: bool = False,
) -> list[TableSchema]:
    """Read table schemas and samples; annotate measure candidates and
    candidate joins. See module docstring for the two join-detection modes."""
    table_names = datasource.list_tables(connection_folder)

    tables: list[TableSchema] = []
    analysis_conn = duckdb.connect(":memory:")
    try:
        for table_name in table_names:
            table_path = datasource.resolve_table_path(
                connection_folder, table_name
            )
            table_meta = datasource.table_metadata(connection_folder, table_name)
            columns: list[ColumnSchema] = []
            for col in table_meta.columns:
                candidates = _measure_candidates(col.name, col.atomic_type)
                needs_trim = (
                    candidates == [AggFunc.COUNT_DISTINCT]
                    and col.atomic_type.kind == "string_type"
                )
                if (
                    candidates is not None
                    and AggFunc.AVG_SCALED_TO_PERCENT in candidates
                ):
                    if _avg_needs_percent_scaling(
                        analysis_conn, table_path, col.name
                    ):
                        candidates = [c for c in candidates if c != AggFunc.AVG]
                    else:
                        candidates = [
                            c
                            for c in candidates
                            if c != AggFunc.AVG_SCALED_TO_PERCENT
                        ]
                columns.append(
                    ColumnSchema(
                        name=col.name,
                        atomic_type=col.atomic_type,
                        dialect_type=col.dialect_type,
                        measure_candidates=candidates,
                        count_distinct_needs_trim=(
                            needs_trim
                            and _count_distinct_needs_trim(
                                analysis_conn, table_path, col.name
                            )
                        ),
                    )
                )

            raw = datasource.sample_rows(connection_folder, table_name, 2)
            samples = [{k: _json_safe(v) for k, v in row.items()} for row in raw]

            tables.append(
                TableSchema(
                    name=table_name,
                    path=table_path,
                    columns=columns,
                    sample_rows=samples,
                )
            )
    finally:
        analysis_conn.close()

    _detect_relationships(
        tables,
        verify_referential_integrity=verify_referential_integrity,
        allow_partial_referential_integrity=allow_partial_referential_integrity,
    )

    return tables


def schema_to_prompt(table: TableSchema) -> str:
    """Serialise a single table's schema report as a JSON string for the
    modeling agent — one table at a time, not the whole schema."""
    return json.dumps(table.to_dict(), indent=2)


def _strongly_connected_components(
    nodes: set[str], edges: dict[str, set[str]]
) -> list[set[str]]:
    """Tarjan's SCC algorithm, restricted to ``nodes``."""
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    components: list[set[str]] = []

    def strongconnect(v: str) -> None:
        indices[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in edges.get(v, ()):
            if w not in nodes:
                continue
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])

        if lowlink[v] == indices[v]:
            component: set[str] = set()
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.add(w)
                if w == v:
                    break
            components.append(component)

    for v in nodes:
        if v not in indices:
            strongconnect(v)
    return components


def topological_order(tables: list[TableSchema]) -> list[TableSchema]:
    """Order tables so join targets are processed before the tables that
    join to them. See the original mcfly schema_analyst.py for the full
    rationale behind restricting cycle-breaking to actual SCC members."""
    by_name = {t.name: t for t in tables}
    deps: dict[str, set[str]] = {
        t.name: {j.target_table for j in t.joins if j.target_table in by_name}
        for t in tables
    }
    ordered: list[TableSchema] = []
    placed: set[str] = set()
    remaining = set(by_name)
    while remaining:
        ready = sorted(n for n in remaining if deps[n].issubset(placed))
        if not ready:
            sccs = _strongly_connected_components(remaining, deps)
            cyclic_nodes = {n for scc in sccs if len(scc) > 1 for n in scc}
            candidates = cyclic_nodes or remaining
            name = min(candidates, key=lambda n: (len(deps[n] - placed), n))
            ready = [name]
        for name in ready:
            ordered.append(by_name[name])
            remaining.discard(name)
            placed.add(name)
    return ordered
