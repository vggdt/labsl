"""Typed per-table plan the modeling agent fills in — never Cube YAML
directly. Same shape and validation strategy as lexi's mcfly agent's
plan.py, which targets Malloy; only the rendering step
(``to_cube_yaml``/``_measure_definition``) is Cube-specific.

Cube's measure ``type`` enum (verified against a real compile of this
project's installed @cubejs-backend/schema-compiler) is only:
count, number, string, boolean, time, sum, avg, min, max, countDistinct,
countDistinctApprox — notably no native stddev, unlike Malloy which has a
built-in stddev aggregate. Anything without a native Cube type falls back
to ``type: number`` with a raw SQL aggregate expression instead (the same
approach used here for avg_per_group/ratio_of_sums/percent_of_total, which
have no single-function form in either language). Every one of these raw
SQL patterns — including percent_of_total's ``SUM(...) OVER ()`` window
function — was verified against a real running Cube dev server, not just a
static compile: percent_of_total in particular was checked against a
grouped live query (two groups of an even split) to confirm it computes a
real per-group share of the grand total, not a query-shape-dependent
artifact.
"""

from __future__ import annotations

from typing import Literal

import pydantic

from . import schema_analyst

_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_IDENTIFIER_DESCRIPTION = (
    "letters, digits, and underscores only, starting with a letter or "
    "underscore — no spaces (e.g. 'total_revenue', not 'Total Revenue')"
)


class MeasureSpec(pydantic.BaseModel):
    """Only for avg_per_group and ratio_of_sums — every other candidate
    aggregation (sum/avg/min/max/stddev/count_distinct/percent_of_total/
    avg_scaled_to_percent) is already defined automatically for every
    column that offers it; submitting one here would just duplicate or
    collide with the automatic measure of the same name."""

    name: str = pydantic.Field(
        pattern=_IDENTIFIER_PATTERN,
        description=f"Measure name — {_IDENTIFIER_DESCRIPTION}.",
    )
    source_column: str = pydantic.Field(
        description=(
            "Exact numeric column name from the schema report — the "
            "amount/quantity column being averaged or ratio'd."
        )
    )
    agg: schema_analyst.AggFunc = pydantic.Field(
        description=(
            "avg_per_group or ratio_of_sums — the only two aggregations "
            "that need a second column paired by real judgment. Every "
            "other candidate aggregation is already defined "
            "automatically; don't submit one here."
        )
    )
    group_by_column: str | None = pydantic.Field(
        default=None,
        description=(
            "Required only when agg is avg_per_group: another column "
            "from the schema report to group by before averaging — e.g. "
            "an order-id column on a table with one row per line item, "
            "so the measure is sum(source_column) per order, averaged "
            "across orders (not per line item, which plain avg would "
            "give). Ignored for every other agg value."
        ),
    )
    denominator_column: str | None = pydantic.Field(
        default=None,
        description=(
            "Required only when agg is ratio_of_sums: another numeric "
            "column from the schema report whose sum becomes the "
            "denominator — e.g. source_column='sales_amount', "
            "denominator_column='sales_quantity' for a realized-price "
            "measure. Must be a different, genuinely summable column on "
            "the same table — not a group/id column. Ignored for every "
            "other agg value."
        ),
    )
    description: str | None = None


class DimensionOverride(pydantic.BaseModel):
    """Only for columns needing a non-default treatment (a rename, or a
    Cube SQL expression such as a date truncation) — plain columns are
    exposed automatically and don't need one."""

    name: str = pydantic.Field(
        pattern=_IDENTIFIER_PATTERN,
        description=f"Dimension name — {_IDENTIFIER_DESCRIPTION}.",
    )
    source_column: str
    expression: str | None = pydantic.Field(
        default=None,
        description=(
            "Cube SQL expression referencing {CUBE}, e.g. "
            "\"DATE_TRUNC('month', {CUBE}.order_date)\". Omit for a "
            "plain passthrough of source_column."
        ),
    )
    description: str | None = None


class JoinDecision(pydantic.BaseModel):
    join_index: int = pydantic.Field(
        description="Index into this table's `joins` list in the schema report."
    )
    keep: bool = pydantic.Field(
        description="Confirm (true) or drop (false) this candidate join."
    )
    join_type: Literal["join_one", "join_many"] = pydantic.Field(
        description=(
            "Cardinality of the relationship. Override the candidate "
            "default if it looks wrong."
        )
    )


class TablePlan(pydantic.BaseModel):
    table_name: str
    measures: list[MeasureSpec] = []
    dimension_overrides: list[DimensionOverride] = []
    join_decisions: list[JoinDecision] = []


class PlanError(ValueError):
    """Raised when a TablePlan doesn't match the schema_analyst's own facts."""


def _column_schema(
    table: schema_analyst.TableSchema, column_name: str
) -> schema_analyst.ColumnSchema:
    for col in table.columns:
        if col.name == column_name:
            return col
    raise PlanError(f"{table.name}: unknown column {column_name!r}")


def validate_plan(plan: TablePlan, table: schema_analyst.TableSchema) -> None:
    """Raise PlanError if the plan references anything schema_analyst
    didn't report for this table."""
    if plan.table_name != table.name:
        raise PlanError(
            f"table_name {plan.table_name!r} does not match {table.name!r}"
        )

    known_columns = {col.name: col for col in table.columns}
    for measure in plan.measures:
        column = known_columns.get(measure.source_column)
        if column is None:
            raise PlanError(
                f"{table.name}: {measure.source_column!r} is not a known column"
            )
        candidates = column.measure_candidates
        if not candidates or measure.agg not in candidates:
            raise PlanError(
                f"{table.name}: {measure.source_column!r} agg must be one of "
                f"{[a.value for a in candidates or []]}, got {measure.agg.value!r}"
            )
        if measure.agg is schema_analyst.AggFunc.AVG_PER_GROUP:
            if measure.group_by_column is None:
                raise PlanError(
                    f"{table.name}: measure {measure.name!r} has "
                    "agg=avg_per_group but no group_by_column"
                )
            if measure.group_by_column not in known_columns:
                raise PlanError(
                    f"{table.name}: group_by_column "
                    f"{measure.group_by_column!r} is not a known column"
                )
        if measure.agg is schema_analyst.AggFunc.RATIO_OF_SUMS:
            if measure.denominator_column is None:
                raise PlanError(
                    f"{table.name}: measure {measure.name!r} has "
                    "agg=ratio_of_sums but no denominator_column"
                )
            denominator = known_columns.get(measure.denominator_column)
            if denominator is None:
                raise PlanError(
                    f"{table.name}: denominator_column "
                    f"{measure.denominator_column!r} is not a known column"
                )
            if denominator.measure_candidates is None:
                raise PlanError(
                    f"{table.name}: denominator_column "
                    f"{measure.denominator_column!r} is not numeric/summable"
                )
            if measure.denominator_column == measure.source_column:
                raise PlanError(
                    f"{table.name}: measure {measure.name!r} has "
                    "denominator_column equal to source_column — a ratio "
                    "of a column to itself is always 1"
                )

    for override in plan.dimension_overrides:
        if override.source_column not in known_columns:
            raise PlanError(
                f"{table.name}: {override.source_column!r} is not a known column"
            )

    for decision in plan.join_decisions:
        if decision.join_index >= len(table.joins) or decision.join_index < 0:
            raise PlanError(
                f"{table.name}: no join candidate #{decision.join_index}"
            )


_JOIN_TYPE_MAP: dict[str, str] = {
    # mcfly's join_one/join_many describe cardinality from *this* table's
    # perspective looking at the target (join_one = this table has at most
    # one matching target row, e.g. a plain FK -> PK). Cube's join
    # `relationship` field describes the same direction: many_to_one is
    # exactly that FK -> PK case; one_to_many is the reverse.
    "join_one": "many_to_one",
    "join_many": "one_to_many",
}


def _cube_dimension_type(column: schema_analyst.ColumnSchema) -> str:
    """Map a DuckDB dialect type to a Cube dimension `type`. Verified
    against a real compile that Cube's dimension type enum accepts
    number/string/time/boolean (and doesn't need any reserved-word
    escaping — see schema_analyst.py's module docstring)."""
    base = column.dialect_type.split("(")[0].upper()
    if base in {
        "DATE",
        "TIME",
        "TIMESTAMP",
        "TIMESTAMP WITH TIME ZONE",
        "TIMESTAMPTZ",
    }:
        return "time"
    if base == "BOOLEAN":
        return "boolean"
    if column.atomic_type.kind == "number_type":
        return "number"
    return "string"


def _measure_definition(
    measure: MeasureSpec, table: schema_analyst.TableSchema
) -> tuple[str, str]:
    """Render a measure's (type, sql) pair for Cube.

    count_distinct maps to Cube's native `countDistinct` type — trimmed
    with TRIM(...) when the source column's count_distinct_needs_trim is
    set (real data has whitespace-only duplicate values), a purely
    mechanical fix with no LLM judgment call, mirroring mcfly's identical
    handling for Malloy's count(distinct ...).

    avg_per_group, ratio_of_sums, percent_of_total, and (unlike Malloy)
    stddev have no native Cube measure type, so all four fall back to
    `type: number` with a raw SQL aggregate expression — verified against
    a real compile and, for percent_of_total specifically, a real grouped
    query (see module docstring)."""
    col = f"{{CUBE}}.{measure.source_column}"
    if measure.agg is schema_analyst.AggFunc.SUM:
        return "sum", measure.source_column
    if measure.agg is schema_analyst.AggFunc.AVG:
        return "avg", measure.source_column
    if measure.agg is schema_analyst.AggFunc.MIN:
        return "min", measure.source_column
    if measure.agg is schema_analyst.AggFunc.MAX:
        return "max", measure.source_column
    if measure.agg is schema_analyst.AggFunc.COUNT_DISTINCT:
        if _column_schema(table, measure.source_column).count_distinct_needs_trim:
            return "countDistinct", f"TRIM({col})"
        return "countDistinct", measure.source_column
    if measure.agg is schema_analyst.AggFunc.STDDEV:
        return "number", f"STDDEV({col})"
    if measure.agg is schema_analyst.AggFunc.AVG_PER_GROUP:
        if measure.group_by_column is None:
            raise ValueError(
                "validate_plan must run before to_cube_yaml: "
                f"measure {measure.name!r} has agg=avg_per_group but no "
                "group_by_column"
            )
        group_col = f"{{CUBE}}.{measure.group_by_column}"
        return "number", f"SUM({col}) / NULLIF(COUNT(DISTINCT {group_col}), 0)"
    if measure.agg is schema_analyst.AggFunc.RATIO_OF_SUMS:
        if measure.denominator_column is None:
            raise ValueError(
                "validate_plan must run before to_cube_yaml: "
                f"measure {measure.name!r} has agg=ratio_of_sums but no "
                "denominator_column"
            )
        denom_col = f"{{CUBE}}.{measure.denominator_column}"
        return "number", f"SUM({col}) / NULLIF(SUM({denom_col}), 0)"
    if measure.agg is schema_analyst.AggFunc.PERCENT_OF_TOTAL:
        return (
            "number",
            f"SUM({col}) / NULLIF(SUM(SUM({col})) OVER (), 0)",
        )
    if measure.agg is schema_analyst.AggFunc.AVG_SCALED_TO_PERCENT:
        return "number", f"AVG({col}) * 100"
    raise ValueError(f"Unhandled agg {measure.agg!r}")  # pragma: no cover


_AUTO_MEASURE_AGGS = frozenset(
    {
        schema_analyst.AggFunc.SUM,
        schema_analyst.AggFunc.AVG,
        schema_analyst.AggFunc.MIN,
        schema_analyst.AggFunc.MAX,
        schema_analyst.AggFunc.STDDEV,
        schema_analyst.AggFunc.COUNT_DISTINCT,
        schema_analyst.AggFunc.PERCENT_OF_TOTAL,
        schema_analyst.AggFunc.AVG_SCALED_TO_PERCENT,
    }
)

_AUTO_MEASURE_NAME_SUFFIX: dict[schema_analyst.AggFunc, str] = {
    schema_analyst.AggFunc.SUM: "sum",
    schema_analyst.AggFunc.AVG: "avg",
    schema_analyst.AggFunc.MIN: "min",
    schema_analyst.AggFunc.MAX: "max",
    schema_analyst.AggFunc.STDDEV: "stddev",
    schema_analyst.AggFunc.PERCENT_OF_TOTAL: "percent_of_total",
    schema_analyst.AggFunc.AVG_SCALED_TO_PERCENT: "avg_percent",
}


def _auto_measure_name(column_name: str, agg: schema_analyst.AggFunc) -> str:
    snake = column_name.lower()
    if agg is schema_analyst.AggFunc.COUNT_DISTINCT:
        return f"distinct_{snake}_count"
    return f"{snake}_{_AUTO_MEASURE_NAME_SUFFIX[agg]}"


def _auto_measures(
    table: schema_analyst.TableSchema, plan: TablePlan
) -> list[dict]:
    """Deterministically define a measure for every single-column
    candidate aggregation on every column in ``_AUTO_MEASURE_AGGS`` — same
    rationale as mcfly's identical mechanism for Malloy (see that
    package's plan.py): this is what makes "highest/lowest X" and "how
    many unique X" questions reliably answerable without relying on the
    modeling agent to remember to define them.

    If the modeling agent already submitted its own measure for the same
    (source_column, agg) pair — or one whose name collides with the
    auto-generated name — that entry is skipped here, so the agent's
    explicit choice always wins over the automatic one."""
    explicit_pairs = {(m.source_column, m.agg) for m in plan.measures}
    used_names = {m.name for m in plan.measures}

    measures: list[dict] = []
    for col in table.columns:
        for agg in col.measure_candidates or []:
            if agg not in _AUTO_MEASURE_AGGS:
                continue
            if (col.name, agg) in explicit_pairs:
                continue
            name = _auto_measure_name(col.name, agg)
            if name in used_names:
                continue
            used_names.add(name)
            measure_type, sql = _measure_definition(
                MeasureSpec(name=name, source_column=col.name, agg=agg),
                table,
            )
            measures.append({"name": name, "type": measure_type, "sql": sql})
    return measures


def to_cube_yaml(plan: TablePlan, table: schema_analyst.TableSchema) -> dict:
    """Convert a validated TablePlan into a Cube cube dict, ready to be
    dumped as YAML (one cube per table, matching model/cubes/<table>.yml's
    existing hand-written convention in this project)."""
    dimensions: list[dict] = []
    overridden_columns = {o.source_column for o in plan.dimension_overrides}

    for override in plan.dimension_overrides:
        dim: dict = {
            "name": override.name,
            "sql": override.expression
            or f"{{CUBE}}.{override.source_column}",
            "type": _cube_dimension_type(
                _column_schema(table, override.source_column)
            ),
        }
        if override.description:
            dim["description"] = override.description
        dimensions.append(dim)

    for col in table.columns:
        if col.name in overridden_columns:
            continue
        dim = {
            "name": col.name,
            "sql": col.name,
            "type": _cube_dimension_type(col),
        }
        if table.primary_key == col.name:
            dim["primary_key"] = True
        dimensions.append(dim)

    measures: list[dict] = []
    for measure in plan.measures:
        measure_type, sql = _measure_definition(measure, table)
        entry = {"name": measure.name, "type": measure_type, "sql": sql}
        if measure.description:
            entry["description"] = measure.description
        measures.append(entry)
    measures.extend(_auto_measures(table, plan))
    measures.append({"name": "count", "type": "count"})

    # A verified candidate (zero orphaned FK values, confirmed against real
    # data) is included even without an explicit join_decision — only a
    # genuinely ambiguous candidate (name-based, or kept only via
    # allow_partial_referential_integrity) requires the modeling agent to
    # actively opt in. Mirrors mcfly's identical rule for Malloy.
    decisions_by_index = {d.join_index: d for d in plan.join_decisions}
    joins: list[dict] = []
    for index, candidate in enumerate(table.joins):
        decision = decisions_by_index.get(index)
        if decision is not None:
            if not decision.keep:
                continue
            join_type = decision.join_type
        elif candidate.verified:
            join_type = "join_one"
        else:
            continue
        join: dict = {
            "name": candidate.target_table,
            "sql": (
                f"{{CUBE}}.{candidate.fk_column} = "
                f"{{{candidate.target_table}.{candidate.pk_column}}}"
            ),
            "relationship": _JOIN_TYPE_MAP[join_type],
        }
        joins.append(join)

    cube: dict = {"name": table.name, "sql_table": table.name}
    if joins:
        cube["joins"] = joins
    cube["dimensions"] = dimensions
    cube["measures"] = measures
    return cube
