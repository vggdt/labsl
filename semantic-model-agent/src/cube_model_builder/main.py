"""CLI entry point for the Cube semantic model builder agent.

Same overall shape as lexi's mcfly agent's main.py: analyze schema
deterministically, then run the modeling agent per table in dependency
order, accumulating accepted cubes, writing the final model/cubes/*.yml
files at the end. Deliberately drops mcfly's MLflow tracing (observability,
not core logic) to keep this package's dependency surface minimal — add it
back the same way mcfly does if you need it.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib

from . import agents, schema_analyst
from .agents import write_cube_files


async def run(
    connection_folder: str,
    destination_dir: pathlib.Path,
    labsl_root: pathlib.Path,
    *,
    verify_referential_integrity: bool = False,
    allow_partial_referential_integrity: bool = False,
) -> pathlib.Path:
    destination_dir = destination_dir.resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)

    tables = schema_analyst.analyze_schema(
        connection_folder,
        verify_referential_integrity=verify_referential_integrity,
        allow_partial_referential_integrity=allow_partial_referential_integrity,
    )
    ordered = schema_analyst.topological_order(tables)

    accepted: list[dict] = []
    for table in ordered:
        cube, _attempts = await agents.model_table(table, accepted, labsl_root)
        accepted.append(cube)

    write_cube_files(accepted, destination_dir)
    return destination_dir / "cubes"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Cube semantic-layer YAML (model/cubes/*.yml) from a "
            "folder of CSV/Parquet files."
        )
    )
    parser.add_argument(
        "--connection",
        required=True,
        help="Filesystem path to a directory containing .parquet and/or .csv files.",
    )
    parser.add_argument(
        "--destination",
        required=True,
        type=pathlib.Path,
        help=(
            "Directory to write the generated cubes/ subdirectory into "
            "(e.g. a labsl checkout's model/ directory)."
        ),
    )
    parser.add_argument(
        "--labsl-root",
        required=True,
        type=pathlib.Path,
        help=(
            "Path to the labsl checkout, needed to locate "
            "scripts/validate-cube-schema.js and its node_modules for "
            "compile validation."
        ),
    )
    parser.add_argument(
        "--verify-referential-integrity",
        action="store_true",
        help=(
            "Detect joins by real column content instead of shared "
            "naming (more expensive, off by default)."
        ),
    )
    parser.add_argument(
        "--allow-partial-referential-integrity",
        action="store_true",
        help=(
            "Only relevant with --verify-referential-integrity. Keep a "
            "candidate join even when some FK values have no match, "
            "instead of dropping it entirely (off by default)."
        ),
    )
    args = parser.parse_args()

    output_path = asyncio.run(
        run(
            args.connection,
            args.destination,
            args.labsl_root.resolve(),
            verify_referential_integrity=args.verify_referential_integrity,
            allow_partial_referential_integrity=(
                args.allow_partial_referential_integrity
            ),
        )
    )
    print(output_path)


if __name__ == "__main__":
    main()
