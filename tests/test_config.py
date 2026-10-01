"""The committed config loads, every key is required, and each section hashes by value."""

from __future__ import annotations

import copy
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from docgap.config import DocgapConfig, load_config
from docgap.models import Environment, RunSetup

CONFIG = Path(__file__).parents[1] / "docgap.toml"
HASH = "0" * 64


def _raw() -> dict[str, Any]:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def _key_paths(table: dict[str, Any], prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    for key, value in table.items():
        path = (*prefix, key)
        # One call site's entry is optional: a site is added when its step lands.
        if prefix != ("call_sites",):
            yield path
        if isinstance(value, dict) and value:
            yield from _key_paths(value, path)


def _without(raw: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    edited = copy.deepcopy(raw)
    table = edited
    for key in path[:-1]:
        table = table[key]
    del table[path[-1]]
    return edited


def test_committed_config_feeds_the_run_setup() -> None:
    config = load_config(CONFIG)
    setup = RunSetup(
        schema_version=4,
        config=config.section_sha256(),
        environment=Environment(python="3.12.8", packages=(), code_sha256=HASH),
        call_sites={},
    )
    assert sorted(setup.config) == sorted(DocgapConfig.model_fields)


@pytest.mark.parametrize("path", list(_key_paths(_raw())), ids=".".join)
def test_every_key_is_required(path: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        DocgapConfig.model_validate(_without(_raw(), path))


def test_committed_limits_match_the_governance_table() -> None:
    # Keep in sync with the brief's "What each model call can see" table and ADR 0003:
    # a looser value here would widen what the drafter and the agent see.
    config = load_config(CONFIG)
    assert config.evidence.min_value_count == 11
    assert config.agent.row_cap == 200
    assert config.agent.max_tool_calls == 8
    assert config.agent.run_attempts == 3
    # Set by the evaluation protocol, and frozen at `preregistered`.
    assert config.rank.w == 1


# A top-level key would sit in no section, so no section hash would cover it.
@pytest.mark.parametrize(
    "path", [("k",), ("gate", "flagged_max"), ("call_sites", "drafter", "timeout")], ids=".".join
)
def test_unknown_key_is_rejected(path: tuple[str, ...]) -> None:
    raw = _raw()
    table = raw
    for key in path[:-1]:
        table = table[key]
    table[path[-1]] = 11
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DocgapConfig.model_validate(raw)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("snapshot", "history_window_days", "7"),
        ("snapshot", "history_window_days", 0),
        ("snapshot", "min_rows_kept", 0),
        ("snapshot", "min_parse_rate", 1.5),
        ("rank", "w", -1),
        ("evidence", "min_value_count", 11.0),
        ("gate", "ready_min", 1.5),
        ("gate", "confirm_min", 0.8),
        ("gate", "confirm_min", 0),
        ("seeds", "split", -1),
        ("agent", "row_cap", True),
        ("actors", "AGENT_READER", "robot"),
        ("actors", "agent_reader", "agent"),
        ("call_sites", "drafter", {"model": "", "sampling": {}}),
        # Its spend would go uncounted.
        ("call_sites", "drafter", {"model": "claude-unpriced", "sampling": {}}),
        ("llm", "max_retries", -1),
        ("llm", "max_spend_usd", 0),
        ("pilot", "models", ["claude-opus-5-5", "claude-opus-5-5"]),
        ("pilot", "models", ["claude-opus-5-5"]),
    ],
)
def test_wrong_type_or_range_is_rejected(section: str, key: str, value: object) -> None:
    raw = _raw()
    raw[section][key] = value
    with pytest.raises(ValidationError):
        DocgapConfig.model_validate(raw)


def test_hash_ignores_comments_whitespace_and_key_order(tmp_path: Path) -> None:
    lines = [
        line for line in CONFIG.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    gate = lines.index("[gate]")
    lines[gate + 1 : gate + 3] = ["confirm_min   =   0.5", "ready_min = 0.8  # moved"]
    path = tmp_path / "docgap.toml"
    path.write_text("\n\n".join(lines), encoding="utf-8")
    assert load_config(path).section_sha256() == load_config(CONFIG).section_sha256()


def test_float_spelled_as_int_hashes_the_same() -> None:
    whole, decimal = _raw(), _raw()
    whole["gate"]["ready_min"] = 1
    decimal["gate"]["ready_min"] = 1.0
    assert (
        DocgapConfig.model_validate(whole).section_sha256()
        == DocgapConfig.model_validate(decimal).section_sha256()
    )


def test_value_change_moves_only_its_section_hash() -> None:
    raw = _raw()
    raw["evidence"]["min_value_count"] = 12
    before = load_config(CONFIG).section_sha256()
    after = DocgapConfig.model_validate(raw).section_sha256()
    assert {name for name in before if before[name] != after[name]} == {"evidence"}


@pytest.mark.parametrize("value", ["marts", "ANALYTICS.MARTS", ""])
def test_mart_schema_must_be_an_uppercase_identifier(value: str) -> None:
    # The marts are matched against uppercased manifest names and FQNs.
    raw = _raw()
    raw["manifest"]["mart_schema"] = value
    with pytest.raises(ValidationError):
        DocgapConfig.model_validate(raw)
