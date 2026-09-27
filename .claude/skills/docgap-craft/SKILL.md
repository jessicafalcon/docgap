---
name: docgap-craft
description: >
  How code is written in docgap: the reuse-first ladder, stage and module shape,
  typing, pydantic contracts, Polars/Parquet style, SQL against Snowflake, dbt YAML,
  Terraform, Airflow DAG rules, and tooling (uv, ruff, pyright, pytest). Read this
  BEFORE writing or refactoring any Python, SQL, dbt, Terraform or DAG code, or
  choosing a dependency. Pairs with docgap-correctness (what keeps a change safe),
  docgap-resilience (failure design), and docgap-voice (comments, commits).
---

# docgap-craft

docgap is small on purpose: a deterministic core of pure stages over files, with
the warehouse and the model at the edges. Every file should read as one hand:
quiet, typed, and covered by a check that doubles as documentation.

## Reuse before writing

Read the task and trace the real flow end to end first. Then stop at the first
rung that holds:

1. **Does it need to exist?** Speculative need → skip it, say so in one line.
2. **Already in the repo?** Reuse the contract, helper or stage that's here.
3. **Standard library?** `pathlib`, `hashlib`, `json`, `dataclasses`, `enum`, `itertools`.
4. **An installed dependency?** sqlglot (parse, qualify, lineage, transpile),
   Polars/pyarrow, pydantic, ruamel.yaml, typer. Don't hand-roll what sqlglot does.
5. **One line?** Make it one line.
6. **Only then** write the minimum that works.

No abstraction with one implementation, no factory for one product, no config for
a value that never changes. A deliberate shortcut gets a `# ponytail:` comment
with its ceiling and upgrade trigger.

## Stage shape

Each pipeline stage is a function from input artifacts to one output artifact:

```python
def run_rank(usage: Path, attributions: Path, config: RankConfig, *, out_dir: Path) -> Artifact:
    """Score undocumented columns and write ranked_gaps.parquet."""
```

- **Pure core, I/O at the edges.** Read inputs, call pure functions over frames and
  models, write one artifact. The pure part is what the tests pin.
- **Inputs are explicit.** `as_of`, seeds, thresholds and paths are parameters.
  Only `cli.py` reads the clock and the environment, and passes values in.
- **One artifact per stage** under `runs/<run_id>/<stage>/`, written atomically
  (docgap-resilience), with its hash recorded in the run manifest.
- **The CLI is thin.** typer commands parse arguments, build config, call the
  stage. No logic in the CLI or in the DAG.

## Modules and typing

- One job per module (`snapshot.py`, `resolve.py`, `rank.py`), no `utils.py`.
- Each module opens with a one-line docstring and `from __future__ import annotations`.
- Declare the public surface with `__all__`.
- Modern typing only: `X | None`, `list[str]`. Type every public signature. `Any`
  is a smell; prefer a model or a `TypedDict`. pyright runs in strict mode on `src/`.

## Contracts are pydantic models

Every record that crosses a stage boundary is a model in `src/docgap/models.py`:
frozen, strict, no extra fields.

```python
class ColumnRef(BaseModel):
    """A fully qualified column, uppercased as Snowflake stores it."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    fqn: Annotated[str, StringConstraints(pattern=r"^[A-Z_][A-Z0-9_$]*(\.[A-Z_][A-Z0-9_$]*){3}$")]
```

- Validate at the boundary (Snowflake export, manifest load, model output), then
  trust the type inside.
- A fixed vocabulary (reason codes, bands, actor classes) is a `StrEnum`.
- A schema change to a contract is a versioned change: bump
  `RunSetup.schema_version` (one version for every contract, recorded in each run
  manifest) and regenerate the committed schemas with `pytest --update-golden`,
  never silently reshape.

## Polars and Parquet

- Stages work on lists of contracts in plain Python and write Parquet with
  pyarrow through `artifacts.py`, which takes each column's dtype from the
  contract. Inputs are small (about 1,000 queries per run), so Polars isn't a
  dependency; the rules below apply once a stage needs frame operations.
- Never mutate an input frame. Transforms are `frame -> frame`.
- Prefer lazy (`scan_parquet` → `collect`) and vectorized expressions over row loops.
- **Sort before every write** by a total key (e.g. `fqn`, then `fingerprint`), so
  output order never depends on hash joins or thread scheduling.
- Declare dtypes explicitly at read. Never rely on inference for IDs or codes.
- Hash the **canonical content** (sorted rows, fixed column order, serialized to
  JSON Lines or Arrow IPC) for manifests and golden tests, not the Parquet bytes:
  the writer version and compression settings live in the file bytes and change
  on upgrade.

## SQL against Snowflake

- Bind values (`%(name)s` / `?`), never f-string them into SQL. Identifiers come
  from the manifest and are quoted by sqlglot, not by string concatenation.
- Every session sets `QUERY_TAG` and `STATEMENT_TIMEOUT_IN_SECONDS`.
- Each role reads only what the governance table grants it. A new read needs a
  Terraform grant first, then the access-matrix test.
- Parse and transform SQL with sqlglot (`dialect="snowflake"`). Regex on SQL is a bug.

## dbt, Terraform, Airflow

- **dbt:** YAML edits go through ruamel.yaml round-trip mode (order and comments
  kept). Every mart column has `meta.sensitivity`; every model has `meta.owner`.
  Contracts enforced on marts.
- **Terraform:** exact provider pin, `terraform fmt`, one resource file per concern
  (`roles.tf`, `grants.tf`, `warehouses.tf`, `users.tf`). No secret values in
  variables with defaults. `ACCOUNTADMIN`-only objects live in `bootstrap.sql`.
- **Airflow:** the DAG file has no top-level work (it's parsed constantly), tasks
  shell out to the CLI with `{{ data_interval_end }}` as `--as-of`, only paths go
  through XCom. `max_active_runs=1`, `catchup=False` unless backfilling on purpose.

## Tooling

- **uv** for env and lockfile (`uv sync --locked` in CI, so a stale lock fails; `uv run --frozen` after it).
- **ruff** format + lint, fixed on every edit by the hook.
- **pyright** strict on `src/`.
- **pre-commit** runs uv-lock, ruff, pyright, gitleaks, and the determinism guard
  (`python3 .claude/hooks/determinism_guard.py` on staged `src/docgap/**/*.py`).
- Python 3.12, pinned in `.python-version`.

Let hooks and pre-commit do the mechanical enforcement. Spend attention on the
logic and on the check that proves it.
