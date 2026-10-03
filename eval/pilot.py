"""Run the offline pilot: the agent on the pilot questions in four configurations, graded and resumable.

uv run python -m eval.pilot --pass 1 --limit 8   # a smoke run: the first 8 runs of pass 1
uv run python -m eval.pilot --pass 1             # the rest of the pass
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib import metadata
from pathlib import Path
from typing import Self

import anthropic
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, NonNegativeFloat, NonNegativeInt, model_validator

from docgap.artifacts import write_atomic
from docgap.config import AgentConfig, DocgapConfig, load_config
from docgap.grade import Outcome, Result, grade
from docgap.llm import Budget, BudgetExhausted, LlmClient, ResponseCache, anthropic_transport
from docgap.log import EventLog
from docgap.manifest import read_marts
from docgap.models import (
    CONTRACT_CONFIG,
    Grade,
    ModelSettings,
    NonEmptyStr,
    Sha256,
    canonical_sha256,
)
from eval.agent.loop import Question, Transcript, call_site, run_agent
from eval.agent.tools import AgentTools
from eval.agent.warehouse import Warehouse
from eval.column_docs import DOCS
from eval.docs_manifest import build_manifest
from eval.questions import (
    PILOT_GOLD,
    PILOT_QUESTIONS,
    SAMPLE_LOCK,
    GoldQuestion,
    load_questions,
    read_gold,
)

__all__ = [
    "REPETITIONS",
    "Configuration",
    "ConfigurationCounts",
    "Docs",
    "PassStatus",
    "PilotInputs",
    "PilotManifest",
    "PlannedRun",
    "RunGrade",
    "configurations",
    "grade_run",
    "input_hashes",
    "lock_pilot",
    "plan",
    "refuse_pass",
    "run_pass",
]

ROOT = Path(__file__).resolve().parents[1]
# A pass's transcripts, grades and final rows, committed; its model cache and event
# log stay local, since no later step replays them.
PILOT_DIR = ROOT / "fixtures" / "pilot"
WORK_DIR = ROOT / "data" / "pilot"
# The protocol's repetitions per question per configuration ("Runs" item 2).
REPETITIONS = 3
# The first pass and at most two reruns (ADR 0024).
MAX_PASSES = 3
# The code that shapes what the agent reads and how a run is graded: the tools'
# output, the transpiler, the manifest reader behind `describe`, the model calls'
# error handling and the grader. A resumed pass refuses a change to any of them.
CODE = (
    *sorted((ROOT / "eval" / "agent").glob("*.py")),
    *sorted((ROOT / "src" / "docgap" / "llm").glob("*.py")),
    ROOT / "src" / "docgap" / "grade.py",
    ROOT / "src" / "docgap" / "manifest.py",
)
# The installed packages a run goes through, by version: not all of `uv.lock`, so a
# dependency no run uses, added mid-pass, can't strand a pass that counts (ADR 0031).
PACKAGES = ("anthropic", "duckdb", "httpx2", "pyarrow", "pydantic", "sqlglot")

TRANSCRIPT = "transcript.json"
GRADE = "grade.json"
ROWS = "rows.parquet"
MANIFEST = "manifest.json"


class Docs(StrEnum):
    """The pilot's two docs settings: no column documented, or every one from the dictionary."""

    NO_DOCS = "no_docs"
    FULL_DOCS = "full_docs"


@dataclass(frozen=True, slots=True)
class Configuration:
    """A candidate model with one docs setting."""

    model: str
    docs: Docs

    @property
    def name(self) -> str:
        """The configuration's directory name, such as `claude-sonnet-5-5.full_docs`."""
        return f"{self.model}.{self.docs}"


def configurations(models: Sequence[str]) -> list[Configuration]:
    """Each model with each docs setting, in the order a repetition runs them.

    >>> [c.name for c in configurations(["a", "b"])]
    ['a.no_docs', 'a.full_docs', 'b.no_docs', 'b.full_docs']
    """
    return [Configuration(model, docs) for model in models for docs in Docs]


@dataclass(frozen=True, slots=True)
class PlannedRun:
    """One run of a pass: a question, a repetition and a configuration."""

    repetition: int
    question: GoldQuestion
    configuration: Configuration

    @property
    def key(self) -> str:
        """The run's directory under the pass, such as `claude-sonnet-5-5.no_docs/P01.r1`."""
        return f"{self.configuration.name}/{self.question.id}.r{self.repetition}"


def plan(
    questions: Sequence[GoldQuestion], models: Sequence[str], repetitions: int
) -> list[PlannedRun]:
    """Every run of a pass in the order it runs: repetition, then question, then configuration.

    A pass the budget stops then leaves each configuration with about as many runs
    as the others, and a smoke run of the first runs covers all four configurations.
    """
    return [
        PlannedRun(repetition, question, configuration)
        for repetition in range(1, repetitions + 1)
        for question in questions
        for configuration in configurations(models)
    ]


class RunGrade(BaseModel):
    """`grade.json`: a run's grade, or the harness error that kept it from one."""

    model_config = CONTRACT_CONFIG

    grade: Grade | None
    harness_error: NonEmptyStr | None

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.grade is None) == (self.harness_error is None):
            raise ValueError("a run has exactly one of a grade or a harness error")
        return self


class ConfigurationCounts(BaseModel):
    """What one configuration's runs did, as the pilot's report reads them."""

    model_config = CONTRACT_CONFIG

    runs: NonNegativeInt
    passed: NonNegativeInt
    # Infrastructure failures: model calls that failed after the retries and were sent
    # again. A run ended by them counts under the error cause `infrastructure`.
    failed_model_calls: NonNegativeInt
    # Runs that ended as `error`, by cause: `transpile` apart from `sql`, as the
    # protocol's "The agent model" reports it.
    error_causes: dict[str, NonNegativeInt]
    harness_errors: NonNegativeInt
    not_made: NonNegativeInt


class PassStatus(StrEnum):
    """Where a pass stands."""

    # Runs remain, and no harness fault stopped the last process: a smoke run, or a crash.
    IN_PROGRESS = "in_progress"
    # A harness fault stopped the last process: the budget, or a permanent API error.
    STOPPED = "stopped"
    COMPLETED = "completed"


class PilotManifest(BaseModel):
    """`manifest.json`: a pass's inputs, the budget it used, and its counts per configuration."""

    model_config = CONTRACT_CONFIG

    inputs: dict[str, Sha256]
    status: PassStatus
    # Every stop's cause, in order: a pass resumed after a stop keeps them.
    stops: list[NonEmptyStr]
    # Counted over every process that ran the pass, so a resumed pass starts from them.
    model_calls: NonNegativeInt
    spend_usd: NonNegativeFloat
    configurations: dict[str, ConfigurationCounts]
    not_made: list[str]

    @model_validator(mode="after")
    def _status_matches(self) -> Self:
        if (self.status is PassStatus.COMPLETED) != (not self.not_made):
            raise ValueError("a pass is completed exactly when no run is left to make")
        if self.status is PassStatus.STOPPED and not self.stops:
            raise ValueError("a stopped pass records why")
        return self


def _made_a_run(directory: Path) -> bool:
    return any(directory.glob(f"*/*/{TRANSCRIPT}"))


def _completed(directory: Path) -> bool:
    path = directory / MANIFEST
    return (
        path.exists()
        and PilotManifest.model_validate_json(path.read_bytes()).status is PassStatus.COMPLETED
    )


def refuse_pass(pilot_dir: Path, current: Path) -> str | None:
    """Say why the pass in `current` may not run, or return None.

    A pass counts once it has written a run, read from its transcripts so a crash
    before its first manifest still counts. No other pass runs while one that
    counts is unfinished, so a partial result can't be set aside for a fresh pass,
    and no new pass starts once three count (ADRs 0024, 0031).
    """
    counting = [d for d in sorted(pilot_dir.glob("pass-*")) if d != current and _made_a_run(d)]
    if unfinished := [d.name for d in counting if not _completed(d)]:
        return f"resume {unfinished} to completion first: a pass that made a run counts"
    if not _made_a_run(current) and len(counting) >= MAX_PASSES:
        return f"{len(counting)} passes count already, the first and two reruns (ADR 0024)"
    return None


@dataclass(frozen=True, slots=True)
class PilotInputs:
    """What a pass reads: the questions, their gold results, each docs setting's tools, and their hashes."""

    questions: Sequence[GoldQuestion]
    gold: Mapping[str, Result]
    models: Sequence[str]
    tools: Mapping[Docs, AgentTools]
    hashes: Mapping[str, str]


def _file_sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def input_hashes(
    config: DocgapConfig,
    *,
    manifests: Mapping[Docs, Path],
    agent_db: Path,
    questions: Sequence[GoldQuestion],
) -> dict[str, str]:
    """Hash everything a pass's runs depend on, so a resumed pass refuses changed inputs."""
    files = {
        "agent_db": agent_db,
        "column_docs": DOCS,
        "questions": PILOT_QUESTIONS,
        "sample_lock": SAMPLE_LOCK,
        **{f"manifest.{docs}": path for docs, path in manifests.items()},
        **{f"gold.{question.id}": PILOT_GOLD / f"{question.id}.parquet" for question in questions},
        **{f"code.{path.relative_to(ROOT).as_posix()}": path for path in CODE},
    }
    hashes = {name: _file_sha256(path) for name, path in files.items()}
    versions = "\n".join(f"{name}=={metadata.version(name)}" for name in PACKAGES)
    hashes["packages"] = hashlib.sha256(versions.encode()).hexdigest()
    # `max_calls` and `max_spend_usd` may rise after the smoke run, within the pass;
    # the timeout, the retries and the prices decide outcomes and costs, and may not.
    llm = config.llm.model_dump(mode="json", exclude={"max_calls", "max_spend_usd"})
    hashes["config.llm"] = hashlib.sha256(json.dumps(llm, sort_keys=True).encode()).hexdigest()
    hashes["config.agent"] = canonical_sha256(config.agent)
    hashes["config.pilot"] = canonical_sha256(config.pilot)
    for model in config.pilot.models:
        site = call_site(ModelSettings(model=model, sampling={}), config.agent)
        hashes[f"call_site.{model}"] = canonical_sha256(site)
    return dict(sorted(hashes.items()))


def _write_result(result: Result, path: Path) -> None:
    # By position, as the gold results are compared: two columns may share a name.
    # pyarrow types each column from its Python values, as DuckDB returned them.
    columns = {str(i): [row[i] for row in result.rows] for i in range(result.width)}
    table = pa.Table.from_pydict(columns)  # pyright: ignore[reportUnknownMemberType]
    write_atomic(path, lambda sink: pq.write_table(table, sink))  # pyright: ignore[reportUnknownMemberType, reportUnknownLambdaType]


def grade_run(
    transcript: Transcript, outcome: Outcome, gold: Result, *, ordered: bool, rows: Path
) -> RunGrade:
    """Grade a run, and store a final result's rows typed as Parquet at `rows`.

    A value the grader refuses (an interval, a UUID, bytes) or Parquet can't store
    (an integer past 64 bits) is a harness error: the run is recorded, not graded,
    and the pass goes on.
    """
    try:
        graded = grade(transcript.qid, transcript.repetition, outcome, gold, ordered=ordered)
        if isinstance(outcome, Result):
            _write_result(outcome, rows)
    except (TypeError, OverflowError, pa.ArrowException) as error:
        return RunGrade(grade=None, harness_error=f"{type(error).__name__}: {error}")
    return RunGrade(grade=graded, harness_error=None)


def _write_json(model: BaseModel, path: Path) -> None:
    text = model.model_dump_json(indent=2) + "\n"
    write_atomic(path, lambda sink: sink.write(text.encode()))


def _read(directory: Path) -> tuple[Transcript, RunGrade]:
    return (
        Transcript.model_validate_json((directory / TRANSCRIPT).read_bytes()),
        RunGrade.model_validate_json((directory / GRADE).read_bytes()),
    )


def _counts(
    runs: Sequence[PlannedRun], made: Mapping[str, tuple[Transcript, RunGrade]]
) -> ConfigurationCounts:
    done = [made[run.key] for run in runs if run.key in made]
    causes = Counter(t.error_cause.value for t, _ in done if t.error_cause is not None)
    return ConfigurationCounts(
        runs=len(done),
        passed=sum(1 for _, g in done if g.grade is not None and g.grade.passed),
        failed_model_calls=sum(t.failed_calls for t, _ in done),
        error_causes=dict(sorted(causes.items())),
        harness_errors=sum(1 for _, g in done if g.harness_error is not None),
        not_made=len(runs) - len(done),
    )


def run_pass(
    inputs: PilotInputs,
    *,
    llm: LlmClient,
    agent_config: AgentConfig,
    out_dir: Path,
    run_id: str,
    limit: int | None,
    log: EventLog,
) -> PilotManifest:
    """Make a pass's runs, or the first `limit` of them, skipping every run already written.

    A resumed pass starts its budget from the calls and spend its manifest recorded,
    so all its processes together stay within one pass's `[llm]` limit. The budget
    running out, or a permanent API error, stops the pass: a harness fault every
    later run would meet too. The manifest is written after each run, so a crash
    loses the count of one run's calls at most.

    Raises:
        ValueError: the pass's manifest records other inputs than `inputs.hashes`.
    """
    path = out_dir / MANIFEST
    stops: list[str] = []
    if path.exists():
        previous = PilotManifest.model_validate_json(path.read_bytes())
        keys = previous.inputs.keys() | inputs.hashes.keys()
        if changed := sorted(k for k in keys if previous.inputs.get(k) != inputs.hashes.get(k)):
            raise ValueError(f"{out_dir}: the pass ran on other inputs {changed}; start a new pass")
        llm.budget.calls = previous.model_calls
        llm.budget.spend_usd = previous.spend_usd
        stops = list(previous.stops)
    runs = plan(inputs.questions, inputs.models, REPETITIONS)
    made = {
        run.key: _read(out_dir / run.key)
        for run in runs
        if (out_dir / run.key / TRANSCRIPT).exists()
    }
    stopped = False

    def make(run: PlannedRun) -> tuple[Transcript, RunGrade]:
        """Make one run and write its rows, its grade, then its transcript, the commit marker."""
        directory = out_dir / run.key
        # A directory without a transcript is what a crash left: never trusted, made again.
        shutil.rmtree(directory, ignore_errors=True)
        question = run.question
        agent = run_agent(
            Question(question.id, question.text),
            run.repetition,
            # Names the configuration, so a transcript read apart from its path still says it.
            run_id=f"{run_id}.{run.configuration.name}",
            site=ModelSettings(model=run.configuration.model, sampling={}),
            llm=llm,
            tools=inputs.tools[run.configuration.docs],
            config=agent_config,
            log=log,
        )
        directory.mkdir(parents=True)
        graded = grade_run(
            agent.transcript,
            agent.outcome,
            inputs.gold[question.id],
            ordered=question.ordered,
            rows=directory / ROWS,
        )
        _write_json(graded, directory / GRADE)
        _write_json(agent.transcript, directory / TRANSCRIPT)
        log.event(
            "pilot",
            run.key,
            "run_written",
            passed=graded.grade is not None and graded.grade.passed,
            harness_error=graded.harness_error is not None,
        )
        return agent.transcript, graded

    def save() -> PilotManifest:
        not_made = [run.key for run in runs if run.key not in made]
        manifest = PilotManifest(
            inputs=dict(inputs.hashes),
            status=(
                PassStatus.STOPPED
                if stopped
                else PassStatus.IN_PROGRESS
                if not_made
                else PassStatus.COMPLETED
            ),
            stops=stops,
            model_calls=llm.budget.calls,
            spend_usd=llm.budget.spend_usd,
            configurations={
                configuration.name: _counts(
                    [run for run in runs if run.configuration == configuration], made
                )
                for configuration in configurations(inputs.models)
            },
            not_made=not_made,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_json(manifest, path)
        return manifest

    try:
        for run in runs[:limit]:
            if run.key in made:
                continue
            try:
                made[run.key] = make(run)
            except (BudgetExhausted, anthropic.APIStatusError) as error:
                stopped = True
                stops.append(f"{type(error).__name__}: {error}")
                log.event("pilot", run.key, "pass_stopped", exception_type=type(error).__name__)
                break
            save()
    finally:
        manifest = save()
    return manifest


def lock_pilot(work_dir: Path) -> Path:
    """Claim the pilot for this process: one pass at a time, so two processes never write
    the same runs, and two new passes never both get past `refuse_pass`.

    The lock sits in the gitignored work directory, so one a killed process leaves
    is never committed.

    Raises:
        FileExistsError: another process holds the lock, or a killed one left it.
    """
    lock = work_dir / ".lock"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise FileExistsError(
            f"{lock}: another process runs the pilot, or one was killed; delete it if none runs"
        ) from None
    os.write(descriptor, f"{os.getpid()} {socket.gethostname()}\n".encode())
    os.close(descriptor)
    return lock


def main(argv: list[str] | None = None) -> None:
    """Run or resume one pilot pass on the offline sample, with live model calls."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    # A pass that made no run is void and counts toward no rerun (ADR 0031), so a
    # fourth pass directory may exist.
    parser.add_argument("--pass", dest="pass_number", type=int, required=True)
    parser.add_argument("--limit", type=int, help="make the pass's first N runs only: a smoke run")
    args = parser.parse_args(argv)
    if args.pass_number < 1 or (args.limit is not None and args.limit < 1):
        parser.error("--pass and --limit must be at least 1")
    config = load_config(ROOT / "docgap.toml")
    out_dir = PILOT_DIR / f"pass-{args.pass_number}"
    work = WORK_DIR / f"pass-{args.pass_number}"
    run_id = f"pilot-{args.pass_number}"
    try:
        lock = lock_pilot(WORK_DIR)
    except FileExistsError as error:
        sys.exit(str(error))
    try:
        if refusal := refuse_pass(PILOT_DIR, out_dir):
            sys.exit(refusal)
        questions = load_questions(PILOT_QUESTIONS)
        docs_text: dict[str, str] = json.loads(DOCS.read_text(encoding="utf-8"))
        manifests: dict[Docs, Path] = {}
        for docs in Docs:
            manifests[docs] = out_dir / "dbt" / f"{docs}.json"
            # A resumed pass reads the manifests its first process built.
            if not manifests[docs].exists():
                build_manifest(manifests[docs], docs_text if docs is Docs.FULL_DOCS else None)
        database, schema = config.manifest.mart_database, config.manifest.mart_schema
        agent_db = ROOT / "data" / "agent" / "sample" / f"{database}.duckdb"
        hashes = input_hashes(config, manifests=manifests, agent_db=agent_db, questions=questions)
        gold = {q.id: read_gold(PILOT_GOLD / f"{q.id}.parquet") for q in questions}
        warehouse = Warehouse(
            agent_db,
            database=database,
            schema=schema,
            timeout_seconds=config.agent.statement_timeout_seconds,
        )
        try:
            tools = {
                docs: AgentTools(
                    warehouse, read_marts(path, config.manifest)[0], row_cap=config.agent.row_cap
                )
                for docs, path in manifests.items()
            }
            inputs = PilotInputs(questions, gold, config.pilot.models, tools, hashes)
            work.mkdir(parents=True, exist_ok=True)
            with (work / "events.jsonl").open("a") as sink:
                log = EventLog(sink, run_id)
                llm = LlmClient(
                    anthropic_transport(config.llm),
                    ResponseCache(work / "cache"),
                    Budget(config.llm),
                    log,
                )
                manifest = run_pass(
                    inputs,
                    llm=llm,
                    agent_config=config.agent,
                    out_dir=out_dir,
                    run_id=run_id,
                    limit=args.limit,
                    log=log,
                )
        finally:
            warehouse.close()
    finally:
        lock.unlink()
    for name, counts in manifest.configurations.items():
        print(f"{name}: {counts.passed}/{counts.runs} passed, {counts.not_made} not made")
    print(
        f"pass {args.pass_number}: {manifest.status}, {manifest.model_calls} model calls,"
        f" ${manifest.spend_usd:.2f} counted"
    )
    if manifest.status is PassStatus.STOPPED:
        print(f"stopped: {manifest.stops[-1]}")
    sys.exit(1 if manifest.status is PassStatus.STOPPED else 0)


if __name__ == "__main__":
    main()
