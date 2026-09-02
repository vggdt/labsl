# cube-model-builder

Generates Cube semantic-layer YAML (`model/cubes/*.yml`) from a folder of
CSV/Parquet files, using the same architecture as
[lexi](https://github.com/factoredai/lexi)'s `mcfly` agent — which
generates a **Malloy** model instead. This package ports mcfly's
deterministic schema-analysis engine essentially unchanged, and rebuilds
only the parts that are genuinely Malloy-specific for Cube instead.

## Architecture (same shape as mcfly)

1. **`schema_analyst.py`** — pure Python, no LLM. Reads each table via
   DuckDB, computes candidate aggregations per column (sum/avg/min/max/
   stddev/count_distinct, plus `avg_per_group` and `ratio_of_sums` for
   cases a plain aggregate can't express), detects joins (by shared
   `_id`/`id` column names by default, or by real referential-integrity
   content-matching with `--verify-referential-integrity`), and
   topologically orders tables. **Ported near-verbatim from mcfly** — this
   logic has nothing to do with the output language.
2. **`plan.py`** — a typed `TablePlan` (Pydantic) the modeling agent fills
   in for judgment calls only (custom measures, dimension renames, join
   keep/reject decisions); every mechanical measure is auto-defined
   deterministically. `to_cube_yaml` renders a validated plan into a real
   Cube cube dict — **this is the Cube-specific rewrite** of mcfly's
   `to_malloy_source`.
3. **`agents.py`** — a `pydantic-ai` agent (AWS Bedrock, same as mcfly)
   with one tool, `submit_table_plan`. Every submission is validated,
   rendered, and **actually compiled** — via
   `../scripts/validate-cube-schema.js`, which loads the accumulated
   `model/` directory through `@cubejs-backend/schema-compiler` (the same
   package Cube itself uses to validate `model/` on startup). A real
   compile error is fed back via `pydantic_ai.ModelRetry` so the agent
   fixes just that table. This is Cube's equivalent of mcfly shelling out
   to `malloy-cli compile`.
4. **`main.py`** — runs the above per table in dependency order, writes
   `<destination>/cubes/*.yml`.

## What's different from mcfly, and why

- **No `datasources`/`semlayer` dependency.** Those are internal
  workspace-only packages of the `lexi` monorepo (not on PyPI, not
  importable from another repo). `datasource.py` reimplements the ~40
  lines `schema_analyst.py` actually needs (list tables, get column
  metadata, sample rows via DuckDB) so this package has no dependency on
  `lexi` at all.
- **No reserved-word/`safe_name` mechanism.** mcfly needs one because
  Malloy has real reserved keywords (`year`, `count`, `sum`, ...) that
  can't be used as bare identifiers. Verified against a real compile
  that Cube has no equivalent restriction — dropped entirely rather than
  ported for no reason.
- **A primary-key gap mcfly never needed to handle.** mcfly's join
  detection only ever assigns a table's `primary_key` as a side effect of
  matching a shared column name across ≥2 tables — a fact table's own PK
  column that no other table happens to reference by name (e.g.
  `order_item_id`) never gets one. Apparently fine for Malloy; a real,
  confirmed blocker for Cube, whose compiler rejects a cube that defines
  joins but has no declared primary key. `schema_analyst._fill_unshared_primary_keys`
  is a small added pass that isn't in mcfly's original, found by actually
  compiling the generated model against this project's own `data/`
  tables, not guessed in advance.
- **Cube's real measure-type enum is narrower than Malloy's aggregates**
  (verified against a real compile error: only
  `count, number, string, boolean, time, sum, avg, min, max, countDistinct,
  countDistinctApprox` — no native `stddev`, for instance). Anything
  without a native type falls back to `type: number` with a raw SQL
  aggregate expression. `percent_of_total`'s SQL (a `SUM(...) OVER ()`
  window function) was checked against a real *grouped* live query against
  this project's Postgres data, not just a static compile, to confirm it
  computes a genuine per-group share of the grand total.
- **No MLflow tracing.** Deliberately dropped to keep the dependency
  surface minimal — add it back the same way mcfly does (`mlflow.pydantic_ai.autolog()`
  plus `mlflow.log_metric(...)`) if you need it.

## Status

The entire non-LLM half of the pipeline (steps 1–2, plus compiling the
result) is verified end-to-end against this project's own `data/*.csv`
files and real Postgres database: schema analysis correctly re-derives
the same joins/primary keys/dependency order this project's hand-written
`model/cubes/*.yml` already has, the generated YAML compiles cleanly via
`@cubejs-backend/schema-compiler`, and a live query against a real Cube
server running that generated model returns correct joined, aggregated
data.

**Not yet run end-to-end**: the actual `submit_table_plan` agent loop
(step 3) needs valid AWS credentials with Bedrock model access, which
aren't configured in this environment. `agents.py` and `main.py` import
and construct correctly (verified), but the LLM call itself is untested
here.

## Usage

```bash
cd semantic-model-agent
uv sync

# Requires AWS credentials with Bedrock access (see Status above).
uv run cube-model-builder \
  --connection ../data \
  --destination /tmp/generated-model \
  --labsl-root ..
```

Or point `--connection` at any other folder of `.csv`/`.parquet` files —
table name = file basename, and the generated cube's `sql_table:` assumes
a same-named table exists in whatever database Cube is actually configured
against (`.env`'s `CUBEJS_DB_*`). This project's own `data/*.csv` files
are a convenient proxy for its own Postgres tables — the CSVs and the
`orders`/`customers`/etc. tables in `semantic_layer` are the same data
(see `db/seed.sql`), so analyzing the CSVs and generating cubes that
query the real Postgres tables by the same name is consistent, not
coincidental.

## Testing without an LLM

The deterministic half (steps 1–2 above, plus compiling the result) needs
no AWS credentials and no agent loop:

```python
from cube_model_builder import schema_analyst, plan, agents
import asyncio, pathlib

tables = schema_analyst.analyze_schema("../data")
ordered = schema_analyst.topological_order(tables)
accepted = []
for t in ordered:
    table_plan = plan.TablePlan(
        table_name=t.name,
        join_decisions=[
            plan.JoinDecision(join_index=i, keep=True, join_type=j.join_type)
            for i, j in enumerate(t.joins)
        ],
    )
    accepted.append(plan.to_cube_yaml(table_plan, t))

print(asyncio.run(agents.validate_cube_yaml(accepted, pathlib.Path("..").resolve())))
```
