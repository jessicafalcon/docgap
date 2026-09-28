"""The `docgap` command: reads the clock, the environment and the files, then runs the core."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import socket
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from pydantic import ValidationError

import docgap
from docgap.config import load_config
from docgap.models import Environment, RankingScope, RunSetup
from docgap.pipeline import MANIFEST_FILE, REPORT_FILE, analyze, run_id
from docgap.rank import RANKED_GAPS_FILE

__all__ = ["app"]

app = typer.Typer(no_args_is_help=True, add_completion=False)
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_PACKAGE = Path(docgap.__file__).parent


@app.callback()
def main() -> None:
    """Rank undocumented warehouse columns by how often they are queried."""


def _runtime_packages() -> tuple[str, ...]:
    """docgap's installed runtime dependencies, transitively, as sorted `name==version`.

    Dev tools are not in the closure, so a pytest bump leaves the setup unchanged
    (ADR 0006).
    """
    versions: dict[str, str] = {}
    pending = ["docgap"]
    while pending:
        for spec in metadata.requires(pending.pop()) or []:
            requirement = Requirement(spec)
            # An extra docgap doesn't request, or a marker this interpreter fails.
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            name = canonicalize_name(requirement.name)
            if name not in versions:
                versions[name] = metadata.version(name)
                pending.append(name)
    return tuple(f"{name}=={version}" for name, version in sorted(versions.items()))


def _code_sha256(root: Path = _PACKAGE) -> str:
    """Hash docgap's own package files, by path and content; compiled caches are left out."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(f"{path.relative_to(root).as_posix()}\0".encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _environment() -> Environment:
    return Environment(
        python=platform.python_version(),
        packages=_runtime_packages(),
        code_sha256=_code_sha256(),
    )


def _git_sha() -> str | None:
    """The commit checked out in the working directory, or None outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 (git from PATH, fixed arguments)
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if _GIT_SHA.fullmatch(sha) else None


@contextmanager
def _lock(run_dir: Path) -> Generator[None]:
    """Hold `run_dir/.lock` for the run, so two runs never write one run directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = run_dir / ".lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        holder = lock.read_text(encoding="utf-8").strip()
        # A lock left by a killed run is reported, never broken silently.
        raise typer.BadParameter(
            f"{lock} is held by {holder or 'an unknown process'}. If that process is "
            "no longer running, the lock is stale: delete it and run again."
        ) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(f"pid {os.getpid()} on {socket.gethostname()}\n")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _fail(error: Exception) -> NoReturn:
    typer.echo(f"docgap: {error}", err=True)
    raise typer.Exit(1) from None


def _as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)
    parsed = datetime.fromisoformat(value)
    # The run ID holds the as-of to the second, so two instants within one second
    # would share a run directory.
    if parsed.tzinfo is None or parsed.microsecond:
        raise typer.BadParameter(
            "needs whole seconds and a UTC offset, e.g. 2026-09-21T00:00:00Z",
            param_hint="--as-of",
        )
    return parsed.astimezone(UTC)


@app.command("analyze")
def analyze_command(
    history: Annotated[Path, typer.Option(help="Query history export, JSON Lines.")],
    manifest: Annotated[Path, typer.Option(help="dbt manifest.json.")],
    offline: Annotated[
        bool, typer.Option("--offline", help="Read the history from the export file.")
    ] = False,
    scope: Annotated[
        Path | None,
        typer.Option(help="Ranking scope JSON: one agent run ID and its discovery qids."),
    ] = None,
    config: Annotated[Path, typer.Option(help="docgap.toml.")] = Path("docgap.toml"),
    runs: Annotated[Path, typer.Option(help="Directory holding one folder per run.")] = Path(
        "runs"
    ),
    as_of: Annotated[
        str | None,
        typer.Option(help="End of the history window, ISO 8601 with offset. Default: now."),
    ] = None,
) -> None:
    """Snapshot, resolve, count, measure coverage and rank, into runs/<run_id>/."""
    if not offline:
        # The live Snowflake export is owed by Phase 3's snapshot step.
        raise typer.BadParameter("only --offline is supported so far", param_hint="--offline")
    instant = _as_of(as_of)
    # The config and scope files are inputs: a file that breaks its contract is a
    # data error, reported in one line.
    try:
        loaded = load_config(config)
        ranking_scope = (
            None if scope is None else RankingScope.model_validate_json(scope.read_bytes())
        )
    except (OSError, ValueError) as error:
        _fail(error)
    try:
        setup = RunSetup(
            schema_version=3,
            config=loaded.section_sha256(),
            environment=_environment(),
            call_sites={},
        )
        run_dir = runs / run_id(instant, setup)
        with _lock(run_dir):
            result = analyze(
                history=history,
                manifest=manifest,
                scope=ranking_scope,
                config=loaded,
                as_of=instant,
                setup=setup,
                run_dir=run_dir,
                git_sha=_git_sha(),
                now=lambda: datetime.now(UTC),
            )
    # A contract broken inside the core is a bug, and keeps its traceback.
    except ValidationError:
        raise
    # Data errors: a crossed gate, a bad history line or manifest, a wrong scope.
    except (OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"run {result.operational.run_id}")
    typer.echo(f"canonical sha256 {result.canonical_sha256()}")
    typer.echo(f"ranking {run_dir / 'rank' / RANKED_GAPS_FILE}")
    typer.echo(f"report {run_dir / REPORT_FILE}")
    typer.echo(f"manifest {run_dir / MANIFEST_FILE}")


if __name__ == "__main__":
    app()
