"""Run the analysis stages under one run manifest, skipping each stage whose record still holds."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from docgap.artifacts import read_rows, rows_sha256, write_atomic
from docgap.config import DocgapConfig
from docgap.coverage import run_coverage
from docgap.models import (
    ColumnRef,
    ColumnUsage,
    QueryRecord,
    RankedGap,
    RankingScope,
    RunCanonical,
    RunManifest,
    RunOperational,
    RunSetup,
    StageRecord,
    StageRun,
    StageStatus,
    UtcDatetime,
    canonical_sha256,
)
from docgap.rank import RANKED_GAPS_FILE, run_rank
from docgap.report import render_report
from docgap.resolve import COLUMN_REFS_FILE, run_resolve
from docgap.snapshot import SNAPSHOT_FILE, run_snapshot
from docgap.usage import COLUMN_USAGE_FILE, run_usage

__all__ = [
    "MANIFEST_FILE",
    "REPORT_FILE",
    "Stage",
    "analyze",
    "analyze_stages",
    "run_id",
    "run_stages",
]

MANIFEST_FILE = "run_manifest.json"
REPORT_FILE = "report.md"
_UTC = TypeAdapter[datetime](UtcDatetime)


def run_id(as_of: datetime, setup: RunSetup) -> str:
    """Name a run by its as-of instant and the first 8 hex digits of its setup hash (ADR 0007).

    >>> from datetime import UTC
    >>> from docgap.models import Environment
    >>> setup = RunSetup(schema_version=3, config={}, call_sites={},
    ...     environment=Environment(python="3.12.8", packages=(), code_sha256="0" * 64))
    >>> run_id(datetime(2026, 9, 21, tzinfo=UTC), setup)
    '20260921T000000Z-0bcf6e2b'
    """
    _UTC.validate_python(as_of)
    if as_of.microsecond:
        # Two instants within one second would share a run directory.
        raise ValueError("as_of must be a whole second")
    return f"{as_of:%Y%m%dT%H%M%SZ}-{setup.sha256()[:8]}"


@dataclass(frozen=True)
class Stage:
    """One stage: the inputs it must read, how to hash its outputs on disk, and how to run it."""

    name: str
    # Inputs whose hash is known before any stage runs: files and the ranking scope.
    inputs: Mapping[str, str]
    # Input key to the (stage, output key) of an earlier stage's output it reads.
    upstream: Mapping[str, tuple[str, str]]
    # Output key to its file in the stage directory and the contract of its rows.
    outputs: Mapping[str, tuple[str, type[BaseModel]]]
    run: Callable[[Path], StageRecord]


def _outputs_hold(stage: Stage, record: StageRecord, directory: Path) -> bool:
    if set(record.outputs) != set(stage.outputs):
        return False
    try:
        return all(
            rows_sha256(read_rows(directory / file, model)) == record.outputs[key]
            for key, (file, model) in stage.outputs.items()
        )
    # A missing or unreadable output: the stage runs again.
    except (OSError, ValueError):
        return False


def run_stages(
    stages: Sequence[Stage],
    *,
    run_dir: Path,
    setup: RunSetup,
    as_of: datetime,
    name: str,
    git_sha: str | None,
    now: Callable[[], datetime],
) -> RunManifest:
    """Run the stages in order, skipping each one whose record from an earlier attempt holds.

    A record holds when it ran under this setup, read the inputs expected now, and
    its outputs are on disk with the recorded hashes. Otherwise the stage's
    directory is cleared, `*.tmp-*` files from a killed write included, and the
    stage runs again. The manifest is rewritten atomically after every stage, and
    a stage's canonical record is its commit marker: an output with no record is
    never trusted.

    Notes:
        `now` times the stages in the operational part, which no hash covers; the
        core reads no clock.

    Raises:
        ValueError: a stage recorded other inputs than expected, so a file changed
            during the run. A stage's own error propagates once it is recorded
            as failed.
    """
    path = run_dir / MANIFEST_FILE
    prior = (
        RunManifest.model_validate_json(path.read_bytes()).canonical.stages if path.exists() else {}
    )
    setup_sha256 = setup.sha256()
    records: dict[str, StageRecord] = {}
    runs: dict[str, StageRun] = {}

    def save() -> RunManifest:
        manifest = RunManifest(
            canonical=RunCanonical(setup=setup, as_of=as_of, stages=dict(records)),
            operational=RunOperational(
                run_id=name,
                git_sha=git_sha,
                stages=dict(runs),
                cache_hits=0,
                cache_misses=0,
                model_calls=0,
                spend_usd=0,
            ),
        )
        text = manifest.model_dump_json(indent=2) + "\n"
        write_atomic(path, lambda sink: sink.write(text.encode()))
        return manifest

    def mark(stage: str, status: StageStatus, started: datetime) -> RunManifest:
        finished = None if status is StageStatus.RUNNING else now()
        runs[stage] = StageRun(status=status, started_at=started, finished_at=finished, retries=0)
        return save()

    run_dir.mkdir(parents=True, exist_ok=True)
    # A killed write of the manifest or the report leaves its temporary file here.
    for tmp in sorted(run_dir.glob("*.tmp-*")):
        tmp.unlink()
    manifest = save()
    for stage in stages:
        expected = dict(stage.inputs) | {
            key: records[source].outputs[output] for key, (source, output) in stage.upstream.items()
        }
        directory = run_dir / stage.name
        started = now()
        old = prior.get(stage.name)
        if (
            old is not None
            and old.setup_sha256 == setup_sha256
            and old.inputs == expected
            and _outputs_hold(stage, old, directory)
        ):
            records[stage.name] = old
            manifest = mark(stage.name, StageStatus.SKIPPED, started)
            continue
        shutil.rmtree(directory, ignore_errors=True)
        mark(stage.name, StageStatus.RUNNING, started)
        try:
            record = stage.run(directory)
            if record.inputs != expected:
                raise ValueError(
                    f"{stage.name}: read inputs {record.inputs}, expected {expected}; "
                    "an input file changed during the run"
                )
        except BaseException:
            mark(stage.name, StageStatus.FAILED, started)
            raise
        records[stage.name] = record
        manifest = mark(stage.name, StageStatus.SUCCEEDED, started)
    return manifest


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze_stages(
    *,
    history: Path,
    manifest: Path,
    scope: RankingScope | None,
    config: DocgapConfig,
    as_of: datetime,
    setup_sha256: str,
    run_dir: Path,
) -> list[Stage]:
    """The offline analysis: snapshot, resolve, usage, coverage and rank, in order."""
    snapshot = run_dir / "snapshot" / SNAPSHOT_FILE
    refs = run_dir / "resolve" / COLUMN_REFS_FILE
    usage = run_dir / "usage" / COLUMN_USAGE_FILE
    manifest_sha256 = _file_sha256(manifest)
    return [
        Stage(
            name="snapshot",
            inputs={"history": _file_sha256(history)},
            upstream={},
            outputs={"query_snapshot": (SNAPSHOT_FILE, QueryRecord)},
            run=lambda out: run_snapshot(
                history,
                as_of=as_of,
                config=config.snapshot,
                actors=config.actors,
                setup_sha256=setup_sha256,
                out_dir=out,
            ),
        ),
        Stage(
            name="resolve",
            inputs={"manifest": manifest_sha256},
            upstream={"query_snapshot": ("snapshot", "query_snapshot")},
            outputs={"column_refs": (COLUMN_REFS_FILE, ColumnRef)},
            run=lambda out: run_resolve(
                snapshot, manifest, config=config.manifest, setup_sha256=setup_sha256, out_dir=out
            ),
        ),
        Stage(
            name="usage",
            inputs={} if scope is None else {"ranking_scope": canonical_sha256(scope)},
            upstream={
                "column_refs": ("resolve", "column_refs"),
                "query_snapshot": ("snapshot", "query_snapshot"),
            },
            outputs={"column_usage": (COLUMN_USAGE_FILE, ColumnUsage)},
            run=lambda out: run_usage(
                snapshot, refs, scope=scope, setup_sha256=setup_sha256, out_dir=out
            ),
        ),
        Stage(
            name="coverage",
            inputs={"manifest": manifest_sha256},
            upstream={"column_usage": ("usage", "column_usage")},
            outputs={},
            run=lambda _: run_coverage(
                usage, manifest, config=config.manifest, setup_sha256=setup_sha256
            ),
        ),
        Stage(
            name="rank",
            inputs={"manifest": manifest_sha256},
            upstream={"column_usage": ("usage", "column_usage")},
            outputs={"ranked_gaps": (RANKED_GAPS_FILE, RankedGap)},
            run=lambda out: run_rank(
                usage,
                manifest,
                manifest_config=config.manifest,
                config=config.rank,
                setup_sha256=setup_sha256,
                out_dir=out,
            ),
        ),
    ]


def analyze(
    *,
    history: Path,
    manifest: Path,
    scope: RankingScope | None,
    config: DocgapConfig,
    as_of: datetime,
    setup: RunSetup,
    run_dir: Path,
    git_sha: str | None,
    now: Callable[[], datetime],
) -> RunManifest:
    """Run the offline analysis into `run_dir`, then write `report.md` from its canonical record.

    Notes:
        `run_dir` is named by `run_id(as_of, setup)`; the name is the run ID.
    """
    stages = analyze_stages(
        history=history,
        manifest=manifest,
        scope=scope,
        config=config,
        as_of=as_of,
        setup_sha256=setup.sha256(),
        run_dir=run_dir,
    )
    result = run_stages(
        stages,
        run_dir=run_dir,
        setup=setup,
        as_of=as_of,
        name=run_dir.name,
        git_sha=git_sha,
        now=now,
    )
    ranked = read_rows(run_dir / "rank" / RANKED_GAPS_FILE, RankedGap)
    report = render_report(result.canonical, ranked)
    write_atomic(run_dir / REPORT_FILE, lambda sink: sink.write(report.encode()))
    return result
