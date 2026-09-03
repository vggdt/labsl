"""Minimal replacement for lexi's internal ``datasources``/``datasources.duckdb``
package (a uv-workspace-only package of the ``lexi`` monorepo, not published
to PyPI — not something this repo can depend on). Provides only the two
things ``schema_analyst.py`` actually needs: per-table column metadata and a
couple of sample rows, read from a folder of Parquet/CSV files via DuckDB.
"""

from __future__ import annotations

import dataclasses
import pathlib

import duckdb

# DuckDB table function per supported file extension — mirrors the same
# extension-to-reader mapping idea as lexi's datasources.duckdb._metadata.READERS,
# just reimplemented locally since that module isn't importable here.
READERS: dict[str, str] = {
    ".parquet": "read_parquet",
    ".csv": "read_csv_auto",
}


@dataclasses.dataclass
class AtomicType:
    """A coarse type classification — just enough for schema_analyst's
    measure-candidate heuristics, which only ever branch on
    ``kind in {"number_type", "string_type"}`` vs. everything else."""

    kind: str  # "number_type" | "string_type" | "other_type"


_NUMBER_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
}
_STRING_TYPES = {"VARCHAR", "CHAR", "TEXT", "BLOB", "UUID"}


def _atomic_type(duckdb_type: str) -> AtomicType:
    base = duckdb_type.split("(")[0].upper()
    if base in _NUMBER_TYPES:
        return AtomicType(kind="number_type")
    if base in _STRING_TYPES:
        return AtomicType(kind="string_type")
    return AtomicType(kind="other_type")


@dataclasses.dataclass
class ColumnMetadata:
    name: str
    atomic_type: AtomicType
    dialect_type: str  # raw DuckDB type name, e.g. "BIGINT", "VARCHAR"


@dataclasses.dataclass
class TableMetadata:
    name: str
    columns: list[ColumnMetadata]


def reader_for(path: str) -> str:
    """Return the DuckDB table function for a data file's extension."""
    suffix = pathlib.Path(path).suffix.lower()
    if suffix not in READERS:
        raise ValueError(
            f"Unsupported file extension {suffix!r} for {path!r} "
            f"(supported: {sorted(READERS)})"
        )
    return READERS[suffix]


def resolve_table_path(connection_folder: str, table_name: str) -> str:
    """Return the data file for a table, whichever supported format it's in."""
    folder = pathlib.Path(connection_folder)
    for suffix in READERS:
        candidate = folder / f"{table_name}{suffix}"
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        f"No data file for table {table_name!r} in {connection_folder!r} "
        f"(looked for suffixes: {sorted(READERS)})"
    )


def list_tables(connection_folder: str) -> list[str]:
    """Table names for every supported data file directly inside the folder."""
    folder = pathlib.Path(connection_folder)
    return sorted(
        {
            p.stem
            for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in READERS
        }
    )


def table_metadata(connection_folder: str, table_name: str) -> TableMetadata:
    path = resolve_table_path(connection_folder, table_name)
    reader = reader_for(path)
    conn = duckdb.connect(":memory:")
    try:
        rows = conn.execute(
            f"DESCRIBE SELECT * FROM {reader}(?)",  # noqa: S608 — reader is from the fixed READERS map, not user input
            [path],
        ).fetchall()
    finally:
        conn.close()
    columns = [
        ColumnMetadata(
            name=row[0], dialect_type=row[1], atomic_type=_atomic_type(row[1])
        )
        for row in rows
    ]
    return TableMetadata(name=table_name, columns=columns)


def sample_rows(connection_folder: str, table_name: str, limit: int) -> list[dict]:
    path = resolve_table_path(connection_folder, table_name)
    reader = reader_for(path)
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(f"SELECT * FROM {reader}(?) LIMIT {int(limit)}", [path])  # noqa: S608
        cols = [d[0] for d in conn.description]
        return [dict(zip(cols, row, strict=True)) for row in conn.fetchall()]
    finally:
        conn.close()
