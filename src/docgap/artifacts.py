"""Write and read stage artifacts: Parquet with dtypes taken from the contract."""

from __future__ import annotations

import hashlib
import json
import os
import types
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Union, get_args, get_origin

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import AwareDatetime, BaseModel

from docgap.models import canonical_json

__all__ = ["arrow_schema", "canonical_lines", "read_rows", "rows_sha256", "write_rows"]

# Every timestamp in a contract is UTC (models._require_utc); microseconds match
# Python's datetime, so a round trip loses nothing.
_TIMESTAMP = pa.timestamp("us", tz="UTC")


def _arrow_type(annotation: Any, field: str) -> tuple[pa.DataType, bool]:
    """Map one field annotation to an Arrow type and whether it is nullable."""
    nullable = False
    if get_origin(annotation) in (Union, types.UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(members) != 1:
            raise TypeError(f"{field}: only `X | None` unions map to Parquet")
        annotation, nullable = members[0], True
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    # pydantic's AwareDatetime is a marker class, not a datetime subclass.
    if annotation is AwareDatetime:
        return _TIMESTAMP, nullable
    # Order matters: StrEnum is a str and bool is an int.
    for python_type, arrow_type in (
        (StrEnum, pa.string()),
        (str, pa.string()),
        (bool, pa.bool_()),
        (int, pa.int64()),
        (float, pa.float64()),
        (datetime, _TIMESTAMP),
    ):
        if isinstance(annotation, type) and issubclass(annotation, python_type):
            return arrow_type, nullable
    # ponytail: flat fields only, enough for the snapshot. Add nested types when a
    # contract with maps or tuples is first written to Parquet.
    raise TypeError(f"{field}: no Parquet type for {annotation!r}")


def arrow_schema(model: type[BaseModel]) -> pa.Schema:
    """Derive the Parquet schema from a contract, in field order.

    >>> from docgap.models import Grade
    >>> arrow_schema(Grade).names
    ['qid', 'repetition', 'passed', 'reason']
    """
    fields: list[pa.Field[pa.DataType]] = []
    for name, info in model.model_fields.items():
        arrow_type, nullable = _arrow_type(info.annotation, f"{model.__name__}.{name}")
        fields.append(pa.field(name, arrow_type, nullable=nullable))
    return pa.schema(fields)


def canonical_lines(rows: Iterable[BaseModel]) -> bytes:
    """Serialize rows as canonical JSON Lines, in order: what hashes and golden files compare.

    Parquet bytes carry the writer version and compression settings, which change
    on upgrade, so no hash is taken over them.
    """
    return b"".join(canonical_json(row) + b"\n" for row in rows)


def rows_sha256(rows: Iterable[BaseModel]) -> str:
    """Hash rows by their canonical JSON Lines."""
    return hashlib.sha256(canonical_lines(rows)).hexdigest()


def write_rows[M: BaseModel](rows: Sequence[M], model: type[M], path: Path) -> str:
    """Write rows to Parquet atomically, in the order given, and return their hash.

    The caller sorts by a total key first. The file appears whole or not at all:
    it is written beside its final path, synced, then renamed over it.
    """
    table = pa.Table.from_pylist([row.model_dump() for row in rows], schema=arrow_schema(model))
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("wb") as sink:
            pq.write_table(table, sink)  # pyright: ignore[reportUnknownMemberType]
            sink.flush()
            os.fsync(sink.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return rows_sha256(rows)


def read_rows[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    """Read an artifact and validate every row against its contract.

    Raises:
        ValueError: the file's schema is not the contract's.
        pydantic.ValidationError: a row breaks the contract.
    """
    table = pq.read_table(path)  # pyright: ignore[reportUnknownMemberType]
    expected = arrow_schema(model)
    if not table.schema.equals(expected):
        raise ValueError(f"{path.name}: schema {table.schema} is not {model.__name__}'s {expected}")
    # Through JSON, because strict contracts take enums and datetimes from JSON text only.
    return [
        model.model_validate_json(json.dumps(row, default=datetime.isoformat))
        for row in table.to_pylist()
    ]
