"""`docgap analyze --offline` end to end: the committed ranking and report, byte for byte."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import GOLDEN, HISTORY, MANIFEST, SCOPE_FILE
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from typer.testing import CliRunner

import docgap.cli as cli
from docgap.artifacts import canonical_lines, read_rows
from docgap.cli import _code_sha256, _runtime_packages, app
from docgap.models import GateCheck, RankedGap, RunManifest, StageStatus
from docgap.pipeline import MANIFEST_FILE, REPORT_FILE
from docgap.rank import RANKED_GAPS_FILE

CONFIG = Path(__file__).resolve().parents[1] / "docgap.toml"
AS_OF = "2026-09-21T00:00:00Z"


def _args(runs: Path, *, history: Path = HISTORY, config: Path = CONFIG) -> list[str]:
    return [
        "analyze",
        "--offline",
        f"--history={history}",
        f"--manifest={MANIFEST}",
        f"--scope={SCOPE_FILE}",
        f"--config={config}",
        f"--runs={runs}",
        f"--as-of={AS_OF}",
    ]


def _analyze(runs: Path, **kwargs: Path) -> tuple[Path, RunManifest]:
    result = CliRunner().invoke(app, _args(runs, **kwargs))
    assert result.exit_code == 0, result.output
    (run_dir,) = runs.iterdir()
    return run_dir, RunManifest.model_validate_json((run_dir / MANIFEST_FILE).read_bytes())


def test_analyze_reproduces_the_committed_ranking_and_report(
    tmp_path: Path, update_golden: bool
) -> None:
    run_dir, manifest = _analyze(tmp_path)
    ranking = canonical_lines(read_rows(run_dir / "rank" / RANKED_GAPS_FILE, RankedGap))
    report = (run_dir / REPORT_FILE).read_bytes()
    golden = GOLDEN / "analyze"
    if update_golden:
        golden.mkdir(exist_ok=True)
        (golden / "ranked_gaps.jsonl").write_bytes(ranking)
        (golden / REPORT_FILE).write_bytes(report)
    assert ranking == (golden / "ranked_gaps.jsonl").read_bytes()
    assert report == (golden / REPORT_FILE).read_bytes()
    assert run_dir.name == manifest.operational.run_id
    assert list(manifest.canonical.stages) == ["snapshot", "resolve", "usage", "coverage", "rank"]


@pytest.mark.parametrize("seed", ["0", "1"])
def test_another_process_and_hash_seed_gives_the_same_canonical_hash(
    tmp_path: Path, seed: str
) -> None:
    # A set or dict whose order leaked into an output would differ between hash seeds.
    _, manifest = _analyze(tmp_path / "in_process")
    result = subprocess.run(  # noqa: S603 (this interpreter, fixed arguments)
        [sys.executable, "-m", "docgap.cli", *_args(tmp_path / "child")],
        capture_output=True,
        text=True,
        check=True,
        env=os.environ | {"PYTHONHASHSEED": seed},
    )
    assert f"canonical sha256 {manifest.canonical_sha256()}" in result.stdout


@settings(
    max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.permutations(HISTORY.read_bytes().splitlines(keepends=True)))
def test_history_row_order_changes_no_output(tmp_path: Path, lines: list[bytes]) -> None:
    history = tmp_path / "shuffled" / "history.jsonl"
    history.parent.mkdir(exist_ok=True)
    history.write_bytes(b"".join(lines))
    # One tmp_path serves every example, so each starts from an empty runs directory.
    runs = tmp_path / "runs"
    shutil.rmtree(runs, ignore_errors=True)
    _, shuffled = _analyze(runs, history=history)
    # The history's bytes hash differently; every stage's output hashes the same.
    expected = json.loads((GOLDEN / "analyze" / "outputs.json").read_text())
    assert {name: stage.outputs for name, stage in shuffled.canonical.stages.items()} == expected


def test_output_hashes_match_golden(tmp_path: Path, update_golden: bool) -> None:
    _, manifest = _analyze(tmp_path)
    outputs = {name: stage.outputs for name, stage in manifest.canonical.stages.items()}
    path = GOLDEN / "analyze" / "outputs.json"
    if update_golden:
        path.write_text(json.dumps(outputs, indent=2, sort_keys=True) + "\n")
    assert outputs == json.loads(path.read_text())


def test_a_crossed_gate_fails_the_run_and_is_recorded(tmp_path: Path) -> None:
    config = tmp_path / "docgap.toml"
    config.write_text(
        CONFIG.read_text().replace("min_rows_kept = 1", "min_rows_kept = 100"), encoding="utf-8"
    )
    result = CliRunner().invoke(app, _args(tmp_path / "runs", config=config))
    assert result.exit_code == 1
    assert "snapshot gates crossed: rows_kept 9 < 100" in result.output
    (run_dir,) = (tmp_path / "runs").iterdir()
    manifest = RunManifest.model_validate_json((run_dir / MANIFEST_FILE).read_bytes())
    assert manifest.canonical.stages == {}
    assert manifest.operational.stages["snapshot"].status is StageStatus.FAILED
    # A failed run releases its lock.
    assert not (run_dir / ".lock").exists()


def test_a_config_that_breaks_its_contract_fails_in_one_line(tmp_path: Path) -> None:
    config = tmp_path / "docgap.toml"
    config.write_text(CONFIG.read_text().replace("w = 1", "w = -1"), encoding="utf-8")
    result = CliRunner().invoke(app, _args(tmp_path / "runs", config=config))
    assert result.exit_code == 1
    assert result.output.startswith("docgap: 1 validation error")


def test_a_contract_broken_inside_the_core_keeps_its_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(**_: object) -> RunManifest:
        GateCheck.model_validate({})
        raise AssertionError

    monkeypatch.setattr(cli, "analyze", broken)
    result = CliRunner().invoke(app, _args(tmp_path))
    assert isinstance(result.exception, ValidationError)


def test_tagged_traffic_without_a_scope_fails(tmp_path: Path) -> None:
    args = [arg for arg in _args(tmp_path) if not arg.startswith("--scope")]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "tagged agent traffic needs a ranking scope" in result.output


def test_a_held_lock_refuses_a_second_run(tmp_path: Path) -> None:
    run_dir, _ = _analyze(tmp_path)
    (run_dir / ".lock").write_text("pid 1 on elsewhere\n")
    result = CliRunner().invoke(app, _args(tmp_path))
    assert result.exit_code == 2
    assert "held by pid 1 on elsewhere" in result.output
    # A finished run leaves no lock behind.
    (run_dir / ".lock").unlink()
    assert CliRunner().invoke(app, _args(tmp_path)).exit_code == 0
    assert not (run_dir / ".lock").exists()


def test_only_offline_is_supported(tmp_path: Path) -> None:
    args = [arg for arg in _args(tmp_path) if arg != "--offline"]
    assert CliRunner().invoke(app, args).exit_code == 2


@pytest.mark.parametrize("value", ["2026-09-21T00:00:00", "2026-09-21T00:00:00.5+00:00"])
def test_as_of_needs_whole_seconds_and_an_offset(tmp_path: Path, value: str) -> None:
    args = [*_args(tmp_path)[:-1], f"--as-of={value}"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "needs whole seconds and a UTC offset" in result.output


def test_code_hash_follows_the_package_files(tmp_path: Path) -> None:
    (tmp_path / "rank.py").write_text("w = 1\n")
    before = _code_sha256(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "rank.cpython-312.pyc").write_bytes(b"compiled")
    assert _code_sha256(tmp_path) == before
    (tmp_path / "rank.py").write_text("w = 2\n")
    changed = _code_sha256(tmp_path)
    assert changed != before
    (tmp_path / "report.py").write_text("")
    assert _code_sha256(tmp_path) != changed


def test_environment_holds_runtime_dependencies_only() -> None:
    names = {package.split("==")[0] for package in _runtime_packages()}
    assert {"pydantic", "pyarrow", "sqlglot", "typer"} <= names
    assert not names & {"docgap", "pytest", "ruff", "pyright", "hypothesis"}
