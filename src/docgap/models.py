"""The data contracts: every record that crosses a stage boundary.

Each contract is frozen, strict and rejects unknown fields. Its JSON Schema is
committed under `schemas/`, and a test fails when the two drift. Strict mode
takes enums, tuples and datetimes from JSON text but only as instances from
Python objects, so readers validate artifacts with `model_validate_json`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

__all__ = [
    "SCHEMA_VERSION",
    "Actor",
    "Attribution",
    "Band",
    "CallSite",
    "Cause",
    "Clause",
    "ColumnRef",
    "ColumnUsage",
    "Draft",
    "Environment",
    "EvidencePacket",
    "FailureReason",
    "GateCheck",
    "GateResult",
    "Grade",
    "GradeReason",
    "Profile",
    "QueryRecord",
    "RankedGap",
    "RunCanonical",
    "RunManifest",
    "RunOperational",
    "Sensitivity",
    "StageRecord",
    "StageRun",
    "StageStatus",
    "TopValue",
    "canonical_json",
]

# Bump on any change to a contract's shape, and regenerate the committed schemas.
SCHEMA_VERSION: Literal[1] = 1

# A probability map written by the model is rescaled to sum to 1 by the adapter;
# this only absorbs float rounding.
_SUM_TOLERANCE = 1e-6


def _require_utc(value: datetime) -> datetime:
    # One offset for every timestamp, so equal instants serialize, and hash, the same.
    if value.utcoffset() != timedelta(0):
        raise ValueError("must be in UTC")
    return value


# `DATABASE.SCHEMA.TABLE.COLUMN`, uppercased as Snowflake stores unquoted names.
Fqn = Annotated[str, StringConstraints(pattern=r"^[A-Z_][A-Z0-9_$]*(\.[A-Z_][A-Z0-9_$]*){3}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
# Question and run IDs appear in query tags (`agent:<run_id>:<qid>:<rep>`), paths
# and branch names, so they hold no colon, slash or leading dot.
Qid = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
RunId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
# Stage, artifact, count, gate, call-site and config-section names.
Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")]
Probability = Annotated[float, Field(ge=0, le=1)]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


def canonical_json(model: BaseModel) -> bytes:
    """Serialize a contract to the bytes that get hashed: sorted keys, no whitespace.

    >>> canonical_json(Grade(qid="q01", repetition=1, passed=True, reason=None))
    b'{"passed":true,"qid":"q01","reason":null,"repetition":1}'
    """
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _sums_to_one(probabilities: Iterable[float], name: str) -> None:
    if abs(sum(probabilities) - 1) > _SUM_TOLERANCE:
        raise ValueError(f"{name} probabilities must sum to 1")


# Vocabularies


class Actor(StrEnum):
    """Who issued a query, from the role-to-actor mapping in config."""

    AGENT = "agent"
    HUMAN = "human"


class Clause(StrEnum):
    """Where a column appears in a query. `OTHER` keeps a rare clause counted."""

    SELECT = "select"
    WHERE = "where"
    JOIN = "join"
    GROUP_BY = "group_by"
    HAVING = "having"
    ORDER_BY = "order_by"
    OTHER = "other"


class GradeReason(StrEnum):
    """Why the grader failed an agent run."""

    ERROR = "error"
    TIMEOUT = "timeout"
    SHAPE_MISMATCH = "shape_mismatch"
    ROW_COUNT_MISMATCH = "row_count_mismatch"
    VALUE_MISMATCH = "value_mismatch"


class Cause(StrEnum):
    """The attribution question's labels: why the agent's SQL was wrong."""

    COLUMN_MEANING = "column_meaning"
    WRONG_TABLE = "wrong_table"
    WRONG_JOIN = "wrong_join"
    WRONG_FILTER = "wrong_filter"
    WRONG_GRAIN = "wrong_grain"
    OTHER = "other"


class FailureReason(StrEnum):
    """Why a model call produced no answer; the item goes to the human band."""

    TIMEOUT = "timeout"
    MALFORMED_ANSWER = "malformed_answer"
    BUDGET_EXHAUSTED = "budget_exhausted"


class Sensitivity(StrEnum):
    """A mart column's `meta.sensitivity`. A missing tag is read as `RESTRICTED`."""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class Band(StrEnum):
    """What the draft gate decided: use as is, owner confirms, or no draft."""

    READY = "ready"
    CONFIRM = "confirm"
    FLAGGED = "flagged"


class StageStatus(StrEnum):
    """A stage's state in the run manifest. `RUNNING` after a crash marks garbage."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# Stage outputs


class QueryRecord(_Contract):
    """One row of the query snapshot, redacted: raw query text never gets here."""

    query_id: NonEmptyStr
    start_time: UtcDatetime
    role: Annotated[str, StringConstraints(pattern=r"^[A-Z_][A-Z0-9_$]*$")]
    actor: Actor
    qid: Qid | None
    succeeded: bool
    normalized_sql: NonEmptyStr
    fingerprint: Sha256

    @model_validator(mode="after")
    def _fingerprint_is_hash_of_sql(self) -> Self:
        if hashlib.sha256(self.normalized_sql.encode()).hexdigest() != self.fingerprint:
            raise ValueError("fingerprint must be the SHA-256 of normalized_sql")
        return self


class ColumnRef(_Contract):
    """One column a query touches, as `resolve` qualified it against the dbt manifest."""

    query_id: NonEmptyStr
    fqn: Fqn
    clause: Clause
    managed: bool


class ColumnUsage(_Contract):
    """One row of `column_usage.parquet`: per-column counts, split by actor."""

    fqn: Fqn
    executions_agent: NonNegativeInt
    executions_human: NonNegativeInt
    fingerprints_agent: NonNegativeInt
    fingerprints_human: NonNegativeInt
    questions: NonNegativeInt

    @model_validator(mode="after")
    def _fingerprints_within_executions(self) -> Self:
        if (
            self.fingerprints_agent > self.executions_agent
            or self.fingerprints_human > self.executions_human
        ):
            raise ValueError("distinct fingerprints cannot exceed executions")
        return self


class Grade(_Contract):
    """One graded agent run: pass, or fail with a reason code."""

    qid: Qid
    repetition: PositiveInt
    passed: bool
    reason: GradeReason | None

    @model_validator(mode="after")
    def _reason_iff_failed(self) -> Self:
        if self.passed == (self.reason is not None):
            raise ValueError("a failed run needs a reason and a passed run has none")
        return self


class Attribution(_Contract):
    """The attribution judgment for one failed discovery run, with full probability maps."""

    qid: Qid
    repetition: PositiveInt
    cause: dict[Cause, Probability] | None
    column: dict[Fqn, Probability] | None
    failure: FailureReason | None

    @model_validator(mode="after")
    def _maps_or_failure(self) -> Self:
        if self.cause is None or self.column is None:
            if self.cause is not None or self.column is not None or self.failure is None:
                raise ValueError("either both probability maps or a failure reason")
            return self
        if self.failure is not None:
            raise ValueError("either both probability maps or a failure reason")
        if set(self.cause) != set(Cause):
            raise ValueError("cause must carry a probability for every label")
        if not self.column:
            raise ValueError("column needs at least one candidate")
        _sums_to_one(self.cause.values(), "cause")
        _sums_to_one(self.column.values(), "column")
        return self


class TopValue(_Contract):
    """A value shown to the drafter and the number of fact rows carrying it."""

    value: str
    rows: PositiveInt


class Profile(_Contract):
    """Aggregate statistics for one column; no row-level data.

    Top values are carried by at least `k` fact rows and ordered by row count, then
    value. Numeric min and max are already clipped to the `k`-th value.
    """

    k: PositiveInt
    null_rate: Probability
    distinct_count: NonNegativeInt
    min: float | None
    max: float | None
    top_values: tuple[TopValue, ...]

    @model_validator(mode="after")
    def _values_respect_k_and_order(self) -> Self:
        if any(top.rows < self.k for top in self.top_values):
            raise ValueError("every top value must be carried by at least k fact rows")
        if list(self.top_values) != sorted(self.top_values, key=lambda t: (-t.rows, t.value)):
            raise ValueError("top values must be ordered by row count, then value")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min cannot exceed max")
        return self


class EvidencePacket(_Contract):
    """Everything the drafter and the gate may see about one column."""

    fqn: Fqn
    data_type: NonEmptyStr
    sensitivity: Sensitivity
    table_description: str | None
    lineage_sql: NonEmptyStr
    upstream_expression: str | None
    profile: Profile

    @model_validator(mode="after")
    def _restricted_has_no_values(self) -> Self:
        profile = self.profile
        if self.sensitivity is Sensitivity.RESTRICTED and (
            profile.min is not None or profile.max is not None or profile.top_values
        ):
            raise ValueError("a restricted column's profile carries no values")
        return self


class Draft(_Contract):
    """The drafter's description for one column, or why there is none."""

    fqn: Fqn
    evidence_sha256: Sha256
    description: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None
    unknowns: tuple[NonEmptyStr, ...]
    failure: FailureReason | None

    @model_validator(mode="after")
    def _description_or_failure(self) -> Self:
        if (self.description is None) == (self.failure is None):
            raise ValueError("either a description or a failure reason")
        return self


class GateResult(_Contract):
    """The draft gate's support probability for one draft, and the band it sets."""

    fqn: Fqn
    draft_sha256: Sha256
    p_supported: Probability | None
    band: Band
    failure: FailureReason | None

    @model_validator(mode="after")
    def _probability_or_flagged_failure(self) -> Self:
        if (self.p_supported is None) == (self.failure is None):
            raise ValueError("either a probability or a failure reason")
        if self.failure is not None and self.band is not Band.FLAGGED:
            raise ValueError("a failed gate call is always flagged")
        return self


class RankedGap(_Contract):
    """One row of `ranked_gaps.parquet`: an undocumented column and its score."""

    rank: PositiveInt
    fqn: Fqn
    score: NonNegativeFloat
    executions: NonNegativeInt
    failure_rate: Probability


# Run manifest


class Environment(_Contract):
    """What ran: the interpreter, the runtime dependency set and docgap's own files."""

    python: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    packages: tuple[Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*==\S+$")], ...]
    code_sha256: Sha256

    @model_validator(mode="after")
    def _packages_sorted_and_unique(self) -> Self:
        names = [package.split("==", 1)[0] for package in self.packages]
        if names != sorted(set(names)):
            raise ValueError("packages must be sorted by name, one version each")
        return self


class CallSite(_Contract):
    """One model call site: the pinned model, the prompt, and the sampling settings sent."""

    model: NonEmptyStr
    prompt_version: NonEmptyStr
    prompt_sha256: Sha256
    sampling: dict[Key, str | int | float | bool]


class GateCheck(_Contract):
    """A data-quality gate: the observed value, the threshold from config, the verdict."""

    value: float
    threshold: float
    passed: bool


class StageRecord(_Contract):
    """What one stage read and wrote, by canonical content hash, and what it counted."""

    inputs: dict[Key, Sha256]
    outputs: dict[Key, Sha256]
    counts: dict[Key, NonNegativeInt]
    gates: dict[Key, GateCheck]


class RunCanonical(_Contract):
    """The part of the manifest that equal inputs must reproduce byte for byte.

    Config is hashed per section. The git SHA is operational: the arms run from
    branches that differ only in dbt YAML, and a docs-only commit changes the SHA
    without changing any output. `environment.code_sha256` pins the code instead.
    """

    schema_version: Literal[1]
    as_of: UtcDatetime
    config: dict[Key, Sha256]
    environment: Environment
    call_sites: dict[Key, CallSite]
    stages: dict[Key, StageRecord]


class StageRun(_Contract):
    """How one stage ran this time: status, timing, retries."""

    status: StageStatus
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    retries: NonNegativeInt

    @model_validator(mode="after")
    def _finishes_after_start(self) -> Self:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        return self


class RunOperational(_Contract):
    """The part that may differ between two runs of the same inputs."""

    run_id: RunId
    git_sha: GitSha | None
    stages: dict[Key, StageRun]
    cache_hits: NonNegativeInt
    cache_misses: NonNegativeInt
    model_calls: NonNegativeInt
    spend_usd: NonNegativeFloat


class RunManifest(_Contract):
    """`run_manifest.json`: the audit record of one run."""

    canonical: RunCanonical
    operational: RunOperational

    @model_validator(mode="after")
    def _canonical_stages_have_run(self) -> Self:
        # A stage writes its canonical record last, as its commit marker, so every
        # recorded stage has an operational entry but not the other way round.
        missing = sorted(set(self.canonical.stages) - set(self.operational.stages))
        if missing:
            raise ValueError(f"stages with no operational entry: {missing}")
        return self

    def canonical_sha256(self) -> str:
        """Hash the canonical part only; this is the hash runs and arms are compared by."""
        return hashlib.sha256(canonical_json(self.canonical)).hexdigest()
