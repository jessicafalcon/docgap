"""Parquet artifacts: dtypes from the contract, atomic writes, validated reads."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest

from docgap.artifacts import arrow_schema, read_rows, rows_sha256, write_rows
from docgap.models import Actor, Attribution, Grade, GradeReason, QueryRecord

SQL = "SELECT A FROM T WHERE B = ?"


def _records() -> list[QueryRecord]:
    common = {
        "role": "AGENT_READER",
        "actor": Actor.AGENT,
        "succeeded": True,
        "normalized_sql": SQL,
        "fingerprint": hashlib.sha256(SQL.encode()).hexdigest(),
    }
    return [
        QueryRecord(
            query_id="01",
            start_time=datetime(2026, 9, 20, 18, 0, 0, 123456, tzinfo=UTC),
            run_id="20260920T180000Z-1a2b3c4d",
            qid="q01",
            repetition=1,
            database_name="ANALYTICS",
            schema_name="MARTS",
            **common,
        ),
        QueryRecord(
            query_id="02",
            start_time=datetime(2026, 9, 20, 18, 1, tzinfo=UTC),
            run_id=None,
            qid=None,
            repetition=None,
            database_name=None,
            schema_name=None,
            **common,
        ),
    ]


def test_schema_follows_the_contract_fields_and_nullability() -> None:
    schema = arrow_schema(QueryRecord)
    assert schema.names == list(QueryRecord.model_fields)
    assert schema.field("start_time").type == pa.timestamp("us", tz="UTC")
    assert schema.field("actor").type == pa.string()
    assert schema.field("repetition").type == pa.int64()
    assert not schema.field("query_id").nullable
    assert schema.field("run_id").nullable


def test_rows_round_trip_and_the_hash_is_over_content(tmp_path: Path) -> None:
    rows = _records()
    path = tmp_path / "query_snapshot.parquet"
    assert write_rows(rows, QueryRecord, path) == rows_sha256(rows)
    assert read_rows(path, QueryRecord) == rows


def test_empty_artifact_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "grades.parquet"
    write_rows([], Grade, path)
    assert read_rows(path, Grade) == []


def _killed(self: Path, target: Path) -> Path:
    raise OSError("killed")


def test_crash_before_rename_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "query_snapshot.parquet"
    monkeypatch.setattr(Path, "replace", _killed)
    with pytest.raises(OSError, match="killed"):
        write_rows(_records(), QueryRecord, path)
    assert list(tmp_path.iterdir()) == []


def test_crash_before_rename_keeps_the_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "query_snapshot.parquet"
    write_rows(_records()[:1], QueryRecord, path)
    monkeypatch.setattr(Path, "replace", _killed)
    with pytest.raises(OSError, match="killed"):
        write_rows(_records(), QueryRecord, path)
    assert read_rows(path, QueryRecord) == _records()[:1]
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


def test_reading_under_another_contract_fails(tmp_path: Path) -> None:
    path = tmp_path / "grades.parquet"
    write_rows(
        [Grade(qid="q01", repetition=1, passed=False, reason=GradeReason.ERROR)], Grade, path
    )
    with pytest.raises(ValueError, match="QueryRecord"):
        read_rows(path, QueryRecord)


def test_a_field_with_no_parquet_type_is_refused() -> None:
    with pytest.raises(TypeError, match=r"Attribution\.cause"):
        arrow_schema(Attribution)
