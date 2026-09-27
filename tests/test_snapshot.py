"""The snapshot stage: redaction, fingerprints, tags, the window and every drop counted.

`fixtures/query_history/basic.jsonl` is hand-made. Its values that must never
reach disk all contain `SENTINEL`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import docgap.snapshot as snapshot_module
from docgap.artifacts import canonical_lines, read_rows
from docgap.config import ActorsConfig, SnapshotConfig
from docgap.models import Actor, QueryRecord, StageRecord
from docgap.snapshot import (
    SNAPSHOT_FILE,
    DropReason,
    HistoryRow,
    load_history,
    normalize,
    run_snapshot,
    snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "fixtures" / "query_history" / "basic.jsonl"
GOLDEN = ROOT / "tests" / "golden" / "snapshot"
AS_OF = datetime(2026, 9, 21, tzinfo=UTC)
ACTORS = {"AGENT_READER": Actor.AGENT}
SETUP = "0" * 64


def _snapshot(rows: list[HistoryRow]) -> tuple[list[QueryRecord], dict[str, int]]:
    return snapshot(rows, as_of=AS_OF, window_days=7, actors=ACTORS)


def _row(**overrides: Any) -> HistoryRow:
    fields: dict[str, Any] = {
        "QUERY_ID": "01",
        "QUERY_TEXT": "select a from t",
        "START_TIME": "2026-09-20T18:00:00Z",
        "ROLE_NAME": "AGENT_READER",
        "QUERY_TAG": None,
        "EXECUTION_STATUS": "SUCCESS",
        "DATABASE_NAME": "ANALYTICS",
        "SCHEMA_NAME": "MARTS",
    }
    return HistoryRow.model_validate_json(json.dumps(fields | overrides))


# Golden output


def _run(out_dir: Path) -> StageRecord:
    return run_snapshot(
        HISTORY,
        as_of=AS_OF,
        config=SnapshotConfig(history_window_days=7),
        actors=ActorsConfig.model_validate(ACTORS),
        setup_sha256=SETUP,
        out_dir=out_dir,
    )


def test_snapshot_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    stage = _run(tmp_path)
    lines = canonical_lines(read_rows(tmp_path / SNAPSHOT_FILE, QueryRecord))
    counts = json.dumps(stage.counts, indent=2, sort_keys=True) + "\n"
    if update_golden:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        (GOLDEN / "query_snapshot.jsonl").write_bytes(lines)
        (GOLDEN / "counts.json").write_text(counts)
    assert lines == (GOLDEN / "query_snapshot.jsonl").read_bytes()
    assert counts == (GOLDEN / "counts.json").read_text()
    assert stage.inputs == {"history": hashlib.sha256(HISTORY.read_bytes()).hexdigest()}
    assert stage.outputs == {"query_snapshot": hashlib.sha256(lines).hexdigest()}


def test_every_row_read_is_kept_or_counted() -> None:
    rows = load_history(HISTORY.read_bytes())
    records, counts = _snapshot(rows)
    dropped = sum(value for key, value in counts.items() if key.startswith("dropped."))
    assert counts["read"] == len(rows) == counts["kept"] + dropped
    assert counts["kept"] == len(records)


def test_no_value_from_the_history_reaches_disk(tmp_path: Path) -> None:
    _run(tmp_path)
    assert b"SENTINEL" in HISTORY.read_bytes()
    for path in tmp_path.iterdir():
        assert b"SENTINEL" not in path.read_bytes()
    assert b"SENTINEL" not in canonical_lines(read_rows(tmp_path / SNAPSHOT_FILE, QueryRecord))


@settings(max_examples=25, deadline=None)
@given(st.permutations(load_history(HISTORY.read_bytes())))
def test_row_order_does_not_change_the_snapshot(rows: list[HistoryRow]) -> None:
    assert _snapshot(rows) == _snapshot(load_history(HISTORY.read_bytes()))


def test_two_agent_runs_in_one_window_stay_apart() -> None:
    records, counts = _snapshot(load_history(HISTORY.read_bytes()))
    assert counts["agent_run_ids"] == 2
    q01_rep1 = {r.run_id for r in records if (r.qid, r.repetition) == ("q01", 1)}
    assert len(q01_rep1) == 2


# Redaction


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("select a from t where b = 'x' and c = 2", "SELECT A FROM T WHERE B = ? AND C = ?"),
        (
            "select a, count(*) from t group by 1 order by 2",
            "SELECT A, COUNT(*) FROM T GROUP BY 1 ORDER BY 2",
        ),
        ("select cast(a as number(10, 2)) from t", "SELECT CAST(A AS DECIMAL(10, 2)) FROM T"),
        (
            "select a from t where d > current_date - interval '1 day'",
            "SELECT A FROM T WHERE D > CURRENT_DATE - ?",
        ),
        ("select a from t where b in (1, 2, 3)", "SELECT A FROM T WHERE B IN (?)"),
        ("select a from t where b in (-1, date '2025-01-01')", "SELECT A FROM T WHERE B IN (?)"),
        ("select a from t where b in (c, 1)", "SELECT A FROM T WHERE B IN (C, ?)"),
        ("select a from t; -- done", "SELECT A FROM T"),
        (
            "select a from t where b in (select c from u)",
            "SELECT A FROM T WHERE B IN (SELECT C FROM U)",
        ),
        ("select $$raw$$, x'ff' from t", "SELECT ?, ? FROM T"),
        ("select a /* id 42 */ from t -- note", "SELECT A FROM T"),
        ("select a from t limit 10", "SELECT A FROM T LIMIT ?"),
        ("select date_trunc('month', d) from t", "SELECT DATE_TRUNC('MONTH', D) FROM T"),
        ('select "lower" from t', 'SELECT "lower" FROM T'),
    ],
    ids=[
        "values",
        "ordinals-kept",
        "type-params-kept",
        "interval",
        "in-list-collapsed",
        "in-list-of-expressions-collapsed",
        "in-list-reading-a-column-kept",
        "trailing-semicolon-and-comment",
        "in-subquery",
        "raw-and-hex-strings",
        "comments",
        "limit",
        "date-part",
        "quoted-identifier",
    ],
)
def test_normalize(sql: str, expected: str) -> None:
    assert normalize(sql) == expected


@pytest.mark.parametrize(
    "value",
    ["'S4242'", "4242", "4.242e3", "$$S4242$$", "x'4242'", "n'S4242'", "interval '4242 day'"],
    ids=["string", "integer", "float", "raw-string", "hex-string", "national-string", "interval"],
)
def test_no_value_form_survives_redaction(value: str) -> None:
    # The query is only parsed, never run.
    assert "4242" not in normalize(f"select a from t where b = {value}")  # noqa: S608


def test_a_redacted_snapshot_normalizes_to_itself() -> None:
    records, _ = _snapshot(load_history(HISTORY.read_bytes()))
    assert records
    assert all(normalize(record.normalized_sql) == record.normalized_sql for record in records)


def test_sqlglot_never_logs_query_text(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    # sqlglot falls back to a `Command` for these, and would log their text first.
    caplog.set_level(logging.DEBUG)
    for sql in ("grant role SENTINEL to user x", "call SENTINEL_proc()"):
        with pytest.raises(ValueError, match="not_a_query"):
            normalize(sql)
    assert "SENTINEL" not in caplog.text + capsys.readouterr().err


@pytest.mark.parametrize(
    "second_pass",
    [
        lambda normalize_once, sql: normalize_once(sql) + " -- changed",
        lambda normalize_once, sql: normalize_once("select from where"),
    ],
    ids=["changes", "fails-to-parse"],
)
def test_an_unstable_normalization_is_counted(
    monkeypatch: pytest.MonkeyPatch, second_pass: Callable[[Callable[[str], str], str], str]
) -> None:
    first_pass = snapshot_module._normalize_once
    calls: list[str] = []

    def flaky(sql: str) -> str:
        calls.append(sql)
        return first_pass(sql) if len(calls) == 1 else second_pass(first_pass, sql)

    monkeypatch.setattr(snapshot_module, "_normalize_once", flaky)
    records, counts = _snapshot([_row()])
    assert records == []
    assert counts["dropped.unstable_normalization"] == 1


def test_the_fingerprint_ignores_values_and_identifier_case() -> None:
    first = normalize("select a from t where b = 1")
    assert first == normalize("SELECT A FROM T WHERE B = 99")


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("select from where", DropReason.PARSE_ERROR),
        ("select 1; select 2", DropReason.MULTIPLE_STATEMENTS),
        ("alter session set query_tag = 'x'", DropReason.NOT_A_QUERY),
        ("show tables", DropReason.NOT_A_QUERY),
        ("insert into t select 1", DropReason.NOT_A_QUERY),
    ],
)
def test_a_query_that_cannot_be_redacted_is_counted_not_kept(sql: str, reason: DropReason) -> None:
    records, counts = _snapshot([_row(QUERY_TEXT=sql)])
    assert records == []
    assert counts[f"dropped.{reason.value}"] == 1


# Tags, roles, window, status


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        (None, (None, None, None)),
        ("", (None, None, None)),
        ("dashboard:weekly", (None, None, None)),
        ("agent:r1:q01:3", ("r1", "q01", 3)),
    ],
)
def test_tag_fields(tag: str | None, expected: tuple[str | None, str | None, int | None]) -> None:
    (record,), _ = _snapshot([_row(QUERY_TAG=tag)])
    assert (record.run_id, record.qid, record.repetition) == expected


@pytest.mark.parametrize(
    "tag",
    [
        "agent:r1:q01",
        "agent:r1:q01:0",
        "agent:r1:q01:1:x",
        "agent:r/1:q01:1",
        "agent:r1::1",
        "agent:",
    ],
)
def test_malformed_agent_tag_is_counted(tag: str) -> None:
    records, counts = _snapshot([_row(QUERY_TAG=tag)])
    assert records == []
    assert counts["dropped.malformed_tag"] == 1


@pytest.mark.parametrize(
    ("start_time", "kept"),
    [
        (AS_OF - timedelta(days=7), True),
        (AS_OF - timedelta(days=7, microseconds=1), False),
        (AS_OF - timedelta(microseconds=1), True),
        (AS_OF, False),
    ],
    ids=["window-start", "before-start", "just-before-as-of", "as-of"],
)
def test_window_includes_its_start_and_excludes_as_of(start_time: datetime, kept: bool) -> None:
    records, counts = _snapshot([_row(START_TIME=start_time.isoformat())])
    assert len(records) == int(kept)
    assert counts["dropped.outside_window"] == int(not kept)


def test_unmapped_role_is_counted() -> None:
    records, counts = _snapshot([_row(ROLE_NAME="DOCGAP_AUDITOR")])
    assert records == []
    assert counts["dropped.unmapped_role"] == 1


@pytest.mark.parametrize(
    ("status", "succeeded"), [("SUCCESS", True), ("FAIL", False), ("INCIDENT", False)]
)
def test_failed_queries_are_kept(status: str, succeeded: bool) -> None:
    (record,), _ = _snapshot([_row(EXECUTION_STATUS=status)])
    assert record.succeeded is succeeded


def test_duplicate_query_id_fails() -> None:
    with pytest.raises(ValueError, match="appears twice"):
        _snapshot([_row(), _row()])


def test_as_of_must_be_utc() -> None:
    with pytest.raises(ValueError, match="UTC"):
        snapshot(
            [], as_of=AS_OF.astimezone(timezone(timedelta(hours=2))), window_days=7, actors=ACTORS
        )


# The history boundary


@pytest.mark.parametrize(
    ("change", "column"),
    [
        ({"EXTRA": 1}, "EXTRA"),
        ({"QUERY_TEXT": None}, "QUERY_TEXT"),
        ({"START_TIME": "2026-09-20T18:00:00+02:00"}, "START_TIME"),
        ({"EXECUTION_STATUS": "RUNNING"}, "EXECUTION_STATUS"),
        ({"DATABASE_NAME": "analytics"}, "DATABASE_NAME"),
    ],
    ids=["extra-column", "missing-text", "not-utc", "unknown-status", "lowercase-context"],
)
def test_bad_history_line_fails_with_line_and_column(change: dict[str, Any], column: str) -> None:
    good = _row().model_dump(mode="json", by_alias=True)
    export = f"{json.dumps(good)}\n{json.dumps(good | change)}\n".encode()
    with pytest.raises(ValueError, match=rf"line 2:[\s\S]*{column}") as error:
        load_history(export)
    assert "input_value" not in str(error.value)


def test_missing_column_fails() -> None:
    good = _row().model_dump(mode="json", by_alias=True)
    del good["QUERY_TAG"]
    with pytest.raises(ValueError, match="QUERY_TAG"):
        load_history(json.dumps(good).encode())
