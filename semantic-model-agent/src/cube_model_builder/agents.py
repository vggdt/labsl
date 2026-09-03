"""The modeling agent: fills a typed TablePlan per table via a single tool,
never Cube YAML directly. Same architecture as lexi's mcfly agent (one
tool, pydantic_ai.ModelRetry on a real compile failure, scoped per-table
retries) — only the compile-validation step differs: mcfly shells out to
malloy-cli, this shells out to this repo's own
scripts/validate-cube-schema.js, which loads the accumulated model/
directory through @cubejs-backend/schema-compiler (the same package Cube
itself uses to validate model/ on startup).
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import shutil
import tempfile

import pydantic_ai
import pydantic_ai.models.bedrock
import yaml

from . import deps as deps_module
from . import plan as plan_module
from . import schema_analyst
from .prompt import MODELING_PROMPT

_log = logging.getLogger(__name__)

BEDROCK_MODEL_ID = os.environ.get(
    "CUBE_MODEL_BUILDER_BEDROCK_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)

_MAX_RETRIES_PER_TABLE = 5


class PlanRejected(Exception):
    """A submitted TablePlan failed validation or did not compile."""


class TableModelingFailed(Exception):
    """The modeling agent finished a run without ever getting a plan
    accepted for the table."""


def write_cube_files(cubes: list[dict], model_dir: pathlib.Path) -> None:
    cubes_dir = model_dir / "cubes"
    cubes_dir.mkdir(parents=True, exist_ok=True)
    for existing in cubes_dir.glob("*.yml"):
        existing.unlink()
    for cube in cubes:
        (cubes_dir / f"{cube['name']}.yml").write_text(
            yaml.dump({"cubes": [cube]}, sort_keys=False)
        )


async def validate_cube_yaml(
    cubes: list[dict], labsl_root: pathlib.Path
) -> str:
    """Write cubes to a scratch model/ dir and compile them with this
    repo's validate-cube-schema.js. Returns "SUCCESS" or "ERRORS:\\n<details>",
    mirroring mcfly's compile_malloy_text's return contract exactly."""
    validator = labsl_root / "scripts" / "validate-cube-schema.js"
    if not validator.exists():
        return f"ERRORS: validator script not found at {validator}"

    with tempfile.TemporaryDirectory() as tmp:
        model_dir = pathlib.Path(tmp) / "model"
        write_cube_files(cubes, model_dir)

        proc = await asyncio.create_subprocess_exec(
            shutil.which("node") or "node",
            str(validator),
            str(model_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(model_dir.parent),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            return "ERRORS: validate-cube-schema.js timed out after 30s"

    if proc.returncode == 0:
        return stdout.decode().strip()
    return stdout.decode().strip() or f"ERRORS:\n{stderr.decode()}"


async def _validate_render_and_compile(
    table_plan: plan_module.TablePlan,
    table: schema_analyst.TableSchema,
    accepted_cubes: list[dict],
    labsl_root: pathlib.Path,
) -> dict:
    """Validate, render, and compile a single table's plan against the
    cubes already accepted. Raises PlanRejected — not
    pydantic_ai.ModelRetry directly — so this stays testable with plain
    pytest, no Agent/LLM involved."""
    try:
        plan_module.validate_plan(table_plan, table)
    except plan_module.PlanError as error:
        raise PlanRejected(str(error)) from error

    new_cube = plan_module.to_cube_yaml(table_plan, table)

    compile_result = await validate_cube_yaml(
        [*accepted_cubes, new_cube], labsl_root
    )
    if compile_result != "SUCCESS":
        raise PlanRejected(compile_result)

    return new_cube


modeling_agent: pydantic_ai.Agent[deps_module.SchemaDeps, str] = pydantic_ai.Agent(
    pydantic_ai.models.bedrock.BedrockConverseModel(BEDROCK_MODEL_ID),
    name="cube-model-builder-modeler",
    deps_type=deps_module.SchemaDeps,
    output_type=str,
    system_prompt=MODELING_PROMPT,
    retries=_MAX_RETRIES_PER_TABLE,
)


@modeling_agent.tool
async def submit_table_plan(
    ctx: pydantic_ai.RunContext[deps_module.SchemaDeps],
    plan: plan_module.TablePlan,
) -> str:
    """Submit the completed plan for the current table.

    Validates, renders, and compiles it against the tables already
    accepted. On any error, raises a retryable error with the details so
    you can fix and resubmit just this table's plan.
    """
    schema_deps = ctx.deps
    try:
        cube = await _validate_render_and_compile(
            plan,
            schema_deps.current_table,
            schema_deps.accepted_cubes,
            schema_deps.labsl_root,
        )
    except PlanRejected as error:
        raise pydantic_ai.ModelRetry(str(error)) from error
    schema_deps.accepted_cubes.append(cube)
    return "accepted"


async def model_table(
    table: schema_analyst.TableSchema,
    accepted_cubes: list[dict],
    labsl_root: pathlib.Path,
) -> tuple[dict, int]:
    """Run the modeling agent for a single table.

    Returns the accepted cube dict plus how many times submit_table_plan
    was called (1 = accepted on the first try)."""
    schema_deps = deps_module.SchemaDeps(
        current_table=table,
        accepted_cubes=list(accepted_cubes),
        labsl_root=labsl_root,
    )
    schema_json = schema_analyst.schema_to_prompt(table)
    result = await modeling_agent.run(
        f"Fill in the plan for table {table.name!r}.\n\n"
        f"Schema report:\n{schema_json}",
        deps=schema_deps,
    )
    attempts = sum(
        1
        for message in result.all_messages()
        for part in message.parts
        if getattr(part, "part_kind", None) == "tool-call"
        and getattr(part, "tool_name", None) == "submit_table_plan"
    )
    if len(schema_deps.accepted_cubes) == len(accepted_cubes):
        _log.error(
            "cube-model-builder: modeling agent finished for table %r "
            "without accepting a plan (submit_table_plan called %d "
            "time(s)). Full message history: %r",
            table.name,
            attempts,
            result.all_messages(),
        )
        how = (
            "submit_table_plan was never called"
            if attempts == 0
            else (
                f"submit_table_plan was called {attempts} time(s) and "
                "rejected every time, then the model stopped retrying "
                f"instead of exhausting the {_MAX_RETRIES_PER_TABLE}-attempt "
                "budget"
            )
        )
        raise TableModelingFailed(
            f"Modeling agent finished for table {table.name!r} without "
            f"ever accepting a plan — {how}. Final agent output: "
            f"{result.output!r}. See logged message history (logger "
            "'cube_model_builder.agents', level ERROR) for the full "
            "conversation."
        )
    return schema_deps.accepted_cubes[-1], attempts
