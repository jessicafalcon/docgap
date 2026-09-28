"""Stage resume: a finished stage is skipped by hash, anything else is cleared and run again."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import AS_OF, HISTORY, MANIFEST, SCOPE, SETUP

import docgap.pipeline as pipeline
from docgap.config import load_config
from docgap.models import Environment, RunManifest, RunSetup, StageRecord, StageStatus
from docgap.pipeline import MANIFEST_FILE, Stage, analyze, run_id, run_stages
from docgap.rank import RANKED_GAPS_FILE

CONFIG = load_config(Path(__file__).resolve().parents[1] / "docgap.toml")
RUN_SETUP = RunSetup(
    schema_version=3,
    config=CONFIG.section_sha256(),
    environment=Environment(python="3.12.8", packages=(), code_sha256=SETUP),
    call_sites={},
)
RUN_ID = run_id(AS_OF, RUN_SETUP)


def _now() -> datetime:
    return datetime(2026, 9, 21, 1, tzinfo=UTC)


def _analyze(runs: Path) -> RunManifest:
    return analyze(
        history=HISTORY,
        manifest=MANIFEST,
        scope=SCOPE,
        config=CONFIG,
        as_of=AS_OF,
        setup=RUN_SETUP,
        runs=runs,
        git_sha=None,
        now=_now,
    )


def _statuses(manifest: RunManifest) -> dict[str, StageStatus]:
    return {name: run.status for name, run in manifest.operational.stages.items()}


def test_a_second_run_skips_every_stage(tmp_path: Path) -> None:
    first = _analyze(tmp_path)
    second = _analyze(tmp_path)
    assert set(_statuses(second).values()) == {StageStatus.SKIPPED}
    assert second.canonical == first.canonical


def test_a_deleted_output_reruns_only_its_stage(tmp_path: Path) -> None:
    first = _analyze(tmp_path)
    (tmp_path / RUN_ID / "rank" / RANKED_GAPS_FILE).unlink()
    second = _analyze(tmp_path)
    assert _statuses(second) == {
        "snapshot": StageStatus.SKIPPED,
        "resolve": StageStatus.SKIPPED,
        "usage": StageStatus.SKIPPED,
        "coverage": StageStatus.SKIPPED,
        "rank": StageStatus.SUCCEEDED,
    }
    assert second.canonical == first.canonical


def test_a_crash_mid_stage_leaves_no_record_and_the_rerun_clears_its_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def killed(*args: object, out_dir: Path, **kwargs: object) -> StageRecord:
        out_dir.mkdir(parents=True)
        (out_dir / f"{RANKED_GAPS_FILE}.tmp-123").write_bytes(b"half a file")
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(pipeline, "run_rank", killed)
        with pytest.raises(KeyboardInterrupt):
            _analyze(tmp_path)
    manifest = RunManifest.model_validate_json((tmp_path / RUN_ID / MANIFEST_FILE).read_bytes())
    assert "rank" not in manifest.canonical.stages
    assert manifest.operational.stages["rank"].status is StageStatus.FAILED

    resumed = _analyze(tmp_path)
    assert _statuses(resumed)["rank"] is StageStatus.SUCCEEDED
    assert _statuses(resumed)["usage"] is StageStatus.SKIPPED
    assert sorted(path.name for path in (tmp_path / RUN_ID / "rank").iterdir()) == [
        RANKED_GAPS_FILE
    ]


def test_a_changed_input_reruns_the_stages_that_read_it(tmp_path: Path) -> None:
    _analyze(tmp_path)
    path = tmp_path / RUN_ID / MANIFEST_FILE
    manifest = RunManifest.model_validate_json(path.read_bytes())
    # As if the scope file had changed since the first attempt.
    usage = manifest.canonical.stages["usage"]
    stale = usage.model_copy(update={"inputs": usage.inputs | {"ranking_scope": "f" * 64}})
    stages = manifest.canonical.stages | {"usage": stale}
    path.write_text(
        manifest.model_copy(
            update={"canonical": manifest.canonical.model_copy(update={"stages": stages})}
        ).model_dump_json()
    )
    statuses = _statuses(_analyze(tmp_path))
    assert statuses["resolve"] is StageStatus.SKIPPED
    assert statuses["usage"] is StageStatus.SUCCEEDED
    # Usage came out the same, so the stages after it still hold.
    assert statuses["rank"] is StageStatus.SKIPPED


def test_a_stage_that_read_other_inputs_fails(tmp_path: Path) -> None:
    record = StageRecord(
        setup_sha256=RUN_SETUP.sha256(), inputs={"x": "a" * 64}, outputs={}, counts={}, gates={}
    )
    stage = Stage(name="odd", inputs={"x": "b" * 64}, upstream={}, outputs={}, run=lambda _: record)
    with pytest.raises(ValueError, match="an input file changed during the run"):
        run_stages(
            [stage],
            run_dir=tmp_path,
            setup=RUN_SETUP,
            as_of=AS_OF,
            run_id=RUN_ID,
            git_sha=None,
            now=_now,
        )
    manifest = RunManifest.model_validate_json((tmp_path / MANIFEST_FILE).read_bytes())
    assert manifest.operational.stages["odd"].status is StageStatus.FAILED
