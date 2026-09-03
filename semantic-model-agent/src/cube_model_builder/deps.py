"""Agent dependency types."""

from __future__ import annotations

import dataclasses
import pathlib

from . import schema_analyst


@dataclasses.dataclass
class SchemaDeps:
    """Per-call dependencies for the modeling agent: the table it's
    currently working on, the cube YAML dicts already accepted for tables
    processed earlier in this run (needed to write the accumulated model/
    directory out for compile-checking), and where to find labsl's
    node_modules (for the validate-cube-schema.js subprocess)."""

    current_table: schema_analyst.TableSchema
    accepted_cubes: list[dict]
    labsl_root: pathlib.Path
