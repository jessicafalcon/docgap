"""The contracts accept what the pipeline writes, reject what it must not, and match their schemas."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from docgap.models import (
    Actor,
    Attribution,
    Band,
    CallSite,
    Cause,
    Clause,
    ColumnRef,
    ColumnUsage,
    Draft,
    Environment,
    EvidencePacket,
    FailureReason,
    GateCheck,
    GateResult,
    Grade,
    GradeReason,
    Profile,
    QueryRecord,
    RankedGap,
    RunCanonical,
    RunManifest,
    RunOperational,
    Sensitivity,
    StageRecord,
    StageRun,
    StageStatus,
    TopValue,
)

SCHEMAS = Path(__file__).parents[1] / "src" / "docgap" / "schemas"
T0 = datetime(2026, 9, 26, tzinfo=UTC)
FQN = "ANALYTICS.MARTS.FCT_REIMBURSEMENTS.PRS_PAI_MNT"
SQL = "SELECT PRS_PAI_MNT FROM FCT_REIMBURSEMENTS WHERE BEN_CMU_TOP = ?"
HASH = "0" * 64

# The brief's ten contracts, each with its committed JSON Schema.
CONTRACTS: dict[str, type[BaseModel]] = {
    "column_ref": ColumnRef,
    "query_record": QueryRecord,
    "column_usage": ColumnUsage,
    "grade": Grade,
    "attribution": Attribution,
    "evidence_packet": EvidencePacket,
    "draft": Draft,
    "gate_result": GateResult,
    "ranked_gap": RankedGap,
    "run_manifest": RunManifest,
}


def _profile(**overrides: Any) -> Profile:
    fields: dict[str, Any] = {
        "k": 11,
        "null_rate": 0.1,
        "distinct_count": 40,
        "min": 0.0,
        "max": 950.5,
        "top_values": (TopValue(value="A", rows=30), TopValue(value="B", rows=11)),
    }
    return Profile(**(fields | overrides))


def _manifest(**operational: Any) -> RunManifest:
    canonical = RunCanonical(
        schema_version=1,
        as_of=T0,
        config={"rank": HASH},
        environment=Environment(
            python="3.12.11", packages=("pydantic==2.13.5", "sqlglot==27.8.0"), code_sha256=HASH
        ),
        call_sites={
            "drafter": CallSite(
                model="claude-opus-5-5", prompt_version="v1", prompt_sha256=HASH, sampling={}
            )
        },
        stages={
            "snapshot": StageRecord(
                inputs={"query_history": HASH},
                outputs={"query_snapshot": HASH},
                counts={"parsed": 118, "unparseable.syntax": 2},
                gates={"parse_rate": GateCheck(value=0.98, threshold=0.9, passed=True)},
            )
        },
    )
    fields: dict[str, Any] = {
        "run_id": "20260926-abcdef12",
        "git_sha": "a" * 40,
        "stages": {
            "snapshot": StageRun(
                status=StageStatus.SUCCEEDED,
                started_at=T0,
                finished_at=T0 + timedelta(seconds=3),
                retries=0,
            )
        },
        "cache_hits": 0,
        "cache_misses": 0,
        "model_calls": 0,
        "spend_usd": 0.0,
    }
    return RunManifest(canonical=canonical, operational=RunOperational(**(fields | operational)))


def _query_record() -> QueryRecord:
    return QueryRecord(
        query_id="01b2",
        start_time=T0,
        role="AGENT_READER",
        actor=Actor.AGENT,
        qid="q01",
        succeeded=False,
        normalized_sql=SQL,
        fingerprint=hashlib.sha256(SQL.encode()).hexdigest(),
    )


def _packet() -> EvidencePacket:
    return EvidencePacket(
        fqn=FQN,
        data_type="NUMBER(12,2)",
        sensitivity=Sensitivity.PUBLIC,
        table_description="One row per reimbursement line.",
        lineage_sql="select prs_pai_mnt from stg_damir__prestations",
        upstream_expression="PRS_PAI_MNT",
        profile=_profile(),
    )


def _examples() -> list[BaseModel]:
    return [
        ColumnRef(query_id="01b2", fqn=FQN, clause=Clause.WHERE, managed=True),
        _query_record(),
        ColumnUsage(
            fqn=FQN,
            executions_agent=12,
            executions_human=0,
            fingerprints_agent=4,
            fingerprints_human=0,
            questions=3,
        ),
        Grade(qid="q01", repetition=2, passed=False, reason=GradeReason.VALUE_MISMATCH),
        Attribution(
            qid="q01",
            repetition=2,
            cause={cause: 1.0 if cause is Cause.COLUMN_MEANING else 0.0 for cause in Cause},
            column={FQN: 0.75, "ANALYTICS.MARTS.FCT_REIMBURSEMENTS.BEN_CMU_TOP": 0.25},
            failure=None,
        ),
        _packet(),
        Draft(
            fqn=FQN,
            evidence_sha256=HASH,
            description="Amount paid, in euros.",
            unknowns=("currency",),
            failure=None,
        ),
        GateResult(fqn=FQN, draft_sha256=HASH, p_supported=0.9, band=Band.READY, failure=None),
        RankedGap(rank=1, fqn=FQN, score=2.3, executions=9, failure_rate=0.2),
        _manifest(),
    ]


def test_every_contract_has_an_example() -> None:
    assert {type(example) for example in _examples()} == set(CONTRACTS.values())


@pytest.mark.parametrize("example", _examples(), ids=lambda example: type(example).__name__)
def test_contract_round_trips_through_json(example: BaseModel) -> None:
    # Artifacts are read back with `model_validate_json`, so strict mode must take
    # enums, tuples and datetimes from JSON text.
    assert type(example).model_validate_json(example.model_dump_json()) == example


@pytest.mark.parametrize("example", _examples(), ids=lambda example: type(example).__name__)
def test_contract_rejects_unknown_fields(example: BaseModel) -> None:
    with pytest.raises(ValidationError, match="extra"):
        type(example).model_validate(example.model_dump() | {"unexpected": 1})


@pytest.mark.parametrize(("name", "contract"), CONTRACTS.items())
def test_committed_schema_matches_contract(
    name: str, contract: type[BaseModel], update_golden: bool
) -> None:
    path = SCHEMAS / f"{name}.schema.json"
    generated = json.dumps(contract.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if update_golden:
        path.parent.mkdir(exist_ok=True)
        path.write_text(generated)
    assert path.read_text() == generated, "rerun with `pytest --update-golden` and review the diff"


def test_no_schema_without_a_contract() -> None:
    assert sorted(path.name for path in SCHEMAS.iterdir()) == sorted(
        f"{name}.schema.json" for name in CONTRACTS
    )


@pytest.mark.parametrize(
    "fqn",
    [
        "analytics.marts.fct.col",  # lowercase
        "MARTS.FCT.COL",  # three parts
        "A.B.C.D.E",  # five parts
        "A.B.C.1COL",  # leading digit
    ],
)
def test_fqn_rejects_anything_but_four_uppercase_parts(fqn: str) -> None:
    with pytest.raises(ValidationError, match="fqn"):
        ColumnRef(query_id="01b2", fqn=fqn, clause=Clause.SELECT, managed=True)


def test_query_record_rejects_a_fingerprint_of_other_sql() -> None:
    record = _query_record()
    with pytest.raises(ValidationError, match="fingerprint must be the SHA-256"):
        QueryRecord.model_validate(record.model_dump() | {"normalized_sql": "SELECT 1"})


def test_timestamps_must_be_utc() -> None:
    paris = timezone(timedelta(hours=2))
    with pytest.raises(ValidationError, match="must be in UTC"):
        StageRun(
            status=StageStatus.RUNNING, started_at=T0.astimezone(paris), finished_at=None, retries=0
        )


def test_nan_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RankedGap(rank=1, fqn=FQN, score=float("nan"), executions=1, failure_rate=0.0)


def test_usage_rejects_more_fingerprints_than_executions() -> None:
    with pytest.raises(ValidationError, match="cannot exceed executions"):
        ColumnUsage(
            fqn=FQN,
            executions_agent=1,
            executions_human=0,
            fingerprints_agent=2,
            fingerprints_human=0,
            questions=1,
        )


@pytest.mark.parametrize(("passed", "reason"), [(True, GradeReason.ERROR), (False, None)])
def test_grade_needs_a_reason_exactly_when_failed(passed: bool, reason: GradeReason | None) -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        Grade(qid="q01", repetition=1, passed=passed, reason=reason)


def _attribution(**overrides: Any) -> Attribution:
    fields: dict[str, Any] = {
        "qid": "q01",
        "repetition": 1,
        "cause": {cause: 1 / len(Cause) for cause in Cause},
        "column": {FQN: 1.0},
        "failure": None,
    }
    return Attribution(**(fields | overrides))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"failure": FailureReason.TIMEOUT}, "maps or a failure"),
        ({"cause": None}, "maps or a failure"),
        ({"cause": None, "column": None}, "maps or a failure"),
        ({"cause": {Cause.OTHER: 1.0}}, "every label"),
        ({"column": {}}, "at least one candidate"),
        ({"column": {FQN: 0.5}}, "column probabilities must sum to 1"),
        ({"cause": {cause: 0.5 for cause in Cause}}, "cause probabilities must sum to 1"),
    ],
)
def test_attribution_rejects_inconsistent_answers(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _attribution(**overrides)


def test_attribution_records_a_failed_call_without_maps() -> None:
    failed = _attribution(cause=None, column=None, failure=FailureReason.MALFORMED_ANSWER)
    assert failed.failure is FailureReason.MALFORMED_ANSWER


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"top_values": (TopValue(value="A", rows=10),)}, "at least k fact rows"),
        (
            {"top_values": (TopValue(value="B", rows=11), TopValue(value="A", rows=11))},
            "ordered by row count, then value",
        ),
        ({"min": 2.0, "max": 1.0}, "min cannot exceed max"),
    ],
)
def test_profile_enforces_k_and_order(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _profile(**overrides)


@pytest.mark.parametrize(
    "overrides", [{}, {"min": None, "max": None}, {"top_values": ()}], ids=["all", "top", "minmax"]
)
def test_restricted_column_carries_no_values(overrides: dict[str, Any]) -> None:
    packet = _packet()
    with pytest.raises(ValidationError, match="restricted column's profile carries no values"):
        EvidencePacket.model_validate(
            packet.model_dump()
            | {"sensitivity": Sensitivity.RESTRICTED, "profile": _profile(**overrides)}
        )


def test_restricted_column_with_aggregates_only_is_valid() -> None:
    profile = _profile(min=None, max=None, top_values=())
    packet = _packet().model_copy(update={"sensitivity": Sensitivity.RESTRICTED})
    assert EvidencePacket.model_validate(packet.model_dump() | {"profile": profile})


@pytest.mark.parametrize(
    ("description", "failure"),
    [(None, None), ("Amount paid.", FailureReason.TIMEOUT)],
    ids=["neither", "both"],
)
def test_draft_has_a_description_or_a_failure(
    description: str | None, failure: FailureReason | None
) -> None:
    with pytest.raises(ValidationError, match="description or a failure"):
        Draft(fqn=FQN, evidence_sha256=HASH, description=description, unknowns=(), failure=failure)


def test_draft_description_is_at_most_200_characters() -> None:
    with pytest.raises(ValidationError, match="200"):
        Draft(fqn=FQN, evidence_sha256=HASH, description="x" * 201, unknowns=(), failure=None)


@pytest.mark.parametrize(
    ("p_supported", "band", "failure", "message"),
    [
        (None, Band.FLAGGED, None, "probability or a failure"),
        (0.9, Band.READY, FailureReason.TIMEOUT, "probability or a failure"),
        (None, Band.CONFIRM, FailureReason.TIMEOUT, "always flagged"),
    ],
)
def test_gate_failure_is_flagged(
    p_supported: float | None, band: Band, failure: FailureReason | None, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        GateResult(fqn=FQN, draft_sha256=HASH, p_supported=p_supported, band=band, failure=failure)


@pytest.mark.parametrize(
    "packages",
    [("sqlglot==27.8.0", "pydantic==2.13.5"), ("pydantic==2.13.5", "pydantic==2.13.6")],
    ids=["unsorted", "duplicate"],
)
def test_environment_packages_are_sorted_and_unique(packages: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="sorted by name, one version each"):
        Environment(python="3.12.11", packages=packages, code_sha256=HASH)


def test_stage_cannot_finish_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="cannot precede"):
        StageRun(
            status=StageStatus.SUCCEEDED,
            started_at=T0,
            finished_at=T0 - timedelta(seconds=1),
            retries=0,
        )


def test_canonical_stage_needs_an_operational_entry() -> None:
    with pytest.raises(ValidationError, match=r"no operational entry: \['snapshot'\]"):
        _manifest(stages={})


def test_canonical_hash_ignores_the_operational_part() -> None:
    rerun = _manifest(run_id="20260927-abcdef12", git_sha=None, cache_hits=40, spend_usd=1.25)
    assert rerun.canonical_sha256() == _manifest().canonical_sha256()


def test_canonical_hash_changes_with_the_environment() -> None:
    manifest = _manifest()
    bumped = manifest.canonical.environment.model_copy(
        update={"packages": ("pydantic==2.13.5", "sqlglot==27.9.0")}
    )
    moved = manifest.model_copy(
        update={"canonical": manifest.canonical.model_copy(update={"environment": bumped})}
    )
    assert moved.canonical_sha256() != manifest.canonical_sha256()


def test_canonical_hash_is_pinned() -> None:
    # Every run hash depends on the canonical bytes. A change to the serialization or
    # to the example shows here and must be deliberate.
    assert _manifest().canonical_sha256() == (
        "6ef838f91b8ef4715f539790c5eb13ee563aebbebd882857543289023b234285"
    )
