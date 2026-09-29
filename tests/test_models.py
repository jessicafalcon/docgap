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
    RankingScope,
    RunCanonical,
    RunManifest,
    RunOperational,
    RunSetup,
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

# The brief's contracts, each with its committed JSON Schema.
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
    "ranking_scope": RankingScope,
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


def _environment(**overrides: Any) -> Environment:
    fields: dict[str, Any] = {
        "python": "3.12.11",
        "packages": ("pydantic==2.13.5", "sqlglot==27.8.0"),
        "code_sha256": HASH,
    }
    return Environment(**(fields | overrides))


def _setup(**overrides: Any) -> RunSetup:
    fields: dict[str, Any] = {
        "schema_version": 4,
        "config": {"rank": HASH, "snapshot": HASH},
        "environment": _environment(),
        "call_sites": {
            "drafter": CallSite(
                model="claude-opus-5-5", prompt_version="v1", prompt_sha256=HASH, sampling={}
            )
        },
    }
    return RunSetup(**(fields | overrides))


def _stage_record(setup: RunSetup) -> StageRecord:
    return StageRecord(
        setup_sha256=setup.sha256(),
        inputs={"query_history": HASH},
        outputs={"query_snapshot": HASH},
        counts={"parsed": 118, "unparseable.syntax": 2},
        gates={"parse_rate": GateCheck(value=0.98, threshold=0.9, passed=True)},
    )


def _stage_run(**overrides: Any) -> StageRun:
    fields: dict[str, Any] = {
        "status": StageStatus.SUCCEEDED,
        "started_at": T0,
        "finished_at": T0 + timedelta(seconds=3),
        "retries": 0,
        "error": None,
    }
    return StageRun(**(fields | overrides))


def _manifest(setup: RunSetup | None = None, **operational: Any) -> RunManifest:
    setup = setup or _setup()
    canonical = RunCanonical(setup=setup, as_of=T0, stages={"snapshot": _stage_record(setup)})
    fields: dict[str, Any] = {
        "run_id": "20260926-abcdef12",
        "git_sha": "a" * 40,
        "stages": {"snapshot": _stage_run()},
        "cache_hits": 0,
        "cache_misses": 0,
        "model_calls": 0,
        "spend_usd": 0.0,
    }
    return RunManifest(canonical=canonical, operational=RunOperational(**(fields | operational)))


def _query_record(**overrides: Any) -> QueryRecord:
    fields: dict[str, Any] = {
        "query_id": "01b2",
        "start_time": T0,
        "role": "AGENT_READER",
        "actor": Actor.AGENT,
        "run_id": "20260926T180000Z-abcdef12",
        "qid": "q01",
        "repetition": 2,
        "database_name": "ANALYTICS",
        "schema_name": "MARTS",
        "succeeded": False,
        "normalized_sql": SQL,
        "fingerprint": hashlib.sha256(SQL.encode()).hexdigest(),
    }
    return QueryRecord(**(fields | overrides))


def _packet(**overrides: Any) -> EvidencePacket:
    fields: dict[str, Any] = {
        "fqn": FQN,
        "data_type": "NUMBER(12,2)",
        "sensitivity": Sensitivity.PUBLIC,
        "table_description": "One row per reimbursement line.",
        "lineage_sql": "select prs_pai_mnt from stg_damir__prestations",
        "upstream_expression": "PRS_PAI_MNT",
        "profile": _profile(),
    }
    return EvidencePacket(**(fields | overrides))


EXAMPLES: list[BaseModel] = [
    ColumnRef(query_id="01b2", fqn=FQN, clause=Clause.WHERE),
    _query_record(),
    ColumnUsage(
        fqn=FQN,
        executions_agent=12,
        executions_human=0,
        fingerprints_agent=4,
        fingerprints_human=0,
        questions=3,
        runs=5,
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
    RankingScope(run_id="20260920T180000Z-1a2b3c4d", qids=("q01", "q03")),
    _manifest(),
]


def test_every_contract_has_an_example() -> None:
    assert {type(example) for example in EXAMPLES} == set(CONTRACTS.values())


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_contract_round_trips_through_json(example: BaseModel) -> None:
    # Artifacts are read back with `model_validate_json`, so strict mode must take
    # enums, tuples and datetimes from JSON text.
    assert type(example).model_validate_json(example.model_dump_json()) == example


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
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
        ColumnRef(query_id="01b2", fqn=fqn, clause=Clause.SELECT)


def test_query_record_rejects_a_fingerprint_of_other_sql() -> None:
    with pytest.raises(ValidationError, match="fingerprint must be the SHA-256"):
        _query_record(normalized_sql="SELECT 1")


@pytest.mark.parametrize(
    "started_at",
    [T0.astimezone(timezone(timedelta(hours=2))), T0.replace(tzinfo=None)],
    ids=["paris", "naive"],
)
def test_timestamps_must_be_utc(started_at: datetime) -> None:
    with pytest.raises(ValidationError, match=r"UTC|timezone"):
        _stage_run(started_at=started_at)


@pytest.mark.parametrize(
    "overrides",
    [{"score": float("nan")}, {"score": float("inf")}, {"failure_rate": 1.5}],
    ids=["nan", "inf", "probability-above-1"],
)
def test_out_of_range_numbers_are_rejected(overrides: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "rank": 1,
        "fqn": FQN,
        "score": 1.0,
        "executions": 1,
        "failure_rate": 0.0,
    }
    with pytest.raises(ValidationError):
        RankedGap(**(fields | overrides))


@pytest.mark.parametrize("qid", ["q:01", "q/01", ".q01"], ids=["colon", "slash", "dot"])
def test_qid_cannot_break_a_tag_or_a_path(qid: str) -> None:
    with pytest.raises(ValidationError, match="qid"):
        Grade(qid=qid, repetition=1, passed=True, reason=None)


@pytest.mark.parametrize(
    "overrides",
    [{"repetition": None}, {"qid": None}, {"run_id": None}, {"qid": None, "repetition": None}],
    ids=["no-repetition", "no-qid", "no-run-id", "run-id-only"],
)
def test_query_record_takes_the_tag_fields_together(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="all or none"):
        _query_record(**overrides)


def test_untagged_query_record_has_no_tag_fields() -> None:
    assert _query_record(run_id=None, qid=None, repetition=None).qid is None


def _usage(**overrides: Any) -> ColumnUsage:
    fields: dict[str, Any] = {
        "fqn": FQN,
        "executions_agent": 4,
        "executions_human": 1,
        "fingerprints_agent": 2,
        "fingerprints_human": 1,
        "questions": 2,
        "runs": 3,
    }
    return ColumnUsage(**(fields | overrides))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"fingerprints_agent": 5}, "cannot exceed executions"),
        ({"fingerprints_human": 2}, "cannot exceed executions"),
        ({"questions": 4}, "questions <= runs"),
        ({"runs": 5}, "runs <= agent executions"),
    ],
)
def test_usage_distinct_counts_stay_within_executions(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _usage(**overrides)


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
        (
            {"top_values": (TopValue(value="A", rows=12), TopValue(value="A", rows=11))},
            "must be distinct",
        ),
    ],
)
def test_profile_enforces_k_and_order(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _profile(**overrides)


@pytest.mark.parametrize(
    "overrides", [{}, {"min": None, "max": None}, {"top_values": ()}], ids=["all", "top", "minmax"]
)
def test_restricted_column_carries_no_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="restricted column's profile carries no values"):
        _packet(sensitivity=Sensitivity.RESTRICTED, profile=_profile(**overrides))


def test_restricted_column_with_aggregates_only_is_valid() -> None:
    profile = _profile(min=None, max=None, top_values=())
    assert _packet(sensitivity=Sensitivity.RESTRICTED, profile=profile).profile == profile


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


def test_failed_draft_has_no_unknowns() -> None:
    with pytest.raises(ValidationError, match="no unknowns"):
        Draft(
            fqn=FQN,
            evidence_sha256=HASH,
            description=None,
            unknowns=("currency",),
            failure=FailureReason.REFUSED,
        )


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


@pytest.mark.parametrize("package", ["typing_extensions==4.15.0", "PyYAML==6.0.3"])
def test_environment_package_names_are_normalized(package: str) -> None:
    with pytest.raises(ValidationError, match="packages"):
        Environment(python="3.12.11", packages=(package,), code_sha256=HASH)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"finished_at": T0 - timedelta(seconds=1)}, "cannot precede"),
        ({"finished_at": None}, "running stage has no finished_at"),
        ({"status": StageStatus.RUNNING}, "running stage has no finished_at"),
        ({"status": StageStatus.FAILED}, "a failed stage has an error"),
        ({"error": "ValueError: gate"}, "any other stage has none"),
    ],
    ids=[
        "before-start",
        "succeeded-unfinished",
        "running-finished",
        "failed-without-error",
        "succeeded-with-error",
    ],
)
def test_stage_run_timing_and_error_match_its_status(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _stage_run(**overrides)


@pytest.mark.parametrize(
    "stages",
    [
        {},
        {"snapshot": _stage_run(status=StageStatus.RUNNING, finished_at=None)},
        {"snapshot": _stage_run(status=StageStatus.FAILED, error="ValueError: gate")},
    ],
    ids=["missing", "running", "failed"],
)
def test_canonical_stage_must_have_finished(stages: dict[str, StageRun]) -> None:
    with pytest.raises(ValidationError, match=r"recorded without finishing: \['snapshot'\]"):
        _manifest(stages=stages)


def test_skipped_stage_keeps_its_canonical_record() -> None:
    assert _manifest(stages={"snapshot": _stage_run(status=StageStatus.SKIPPED)})


def test_stage_recorded_under_another_setup_is_rejected() -> None:
    # A resume after a dependency bump must re-run the stage, not relabel its output.
    old = _setup()
    new = _setup(environment=_environment(code_sha256="1" * 64))
    with pytest.raises(ValidationError, match=r"another setup: \['snapshot'\]"):
        RunCanonical(setup=new, as_of=T0, stages={"snapshot": _stage_record(old)})


def test_canonical_hash_ignores_the_operational_part() -> None:
    rerun = _manifest(run_id="20260927-abcdef12", git_sha=None, cache_hits=40, spend_usd=1.25)
    assert rerun.canonical_sha256() == _manifest().canonical_sha256()


def test_canonical_hash_ignores_key_order() -> None:
    reordered = _setup(config={"snapshot": HASH, "rank": HASH})
    assert _manifest(reordered).canonical_sha256() == _manifest().canonical_sha256()


@pytest.mark.parametrize(
    "environment",
    [
        _environment(packages=("pydantic==2.13.5", "sqlglot==27.9.0")),
        _environment(code_sha256="1" * 64),
        _environment(python="3.12.12"),
    ],
    ids=["dependency", "code", "python"],
)
def test_setup_and_canonical_hashes_change_with_the_environment(environment: Environment) -> None:
    moved = _manifest(_setup(environment=environment))
    assert moved.canonical.setup.sha256() != _setup().sha256()
    assert moved.canonical_sha256() != _manifest().canonical_sha256()


def test_arms_share_a_setup_hash_despite_different_outputs() -> None:
    # Arm parity compares the setup; outputs and as_of legitimately differ between arms.
    setup = _setup()
    other_arm = RunCanonical(
        setup=setup,
        as_of=T0 + timedelta(hours=2),
        stages={"snapshot": _stage_record(setup).model_copy(update={"outputs": {"grades": HASH}})},
    )
    assert other_arm.setup.sha256() == _manifest().canonical.setup.sha256()
    assert other_arm != _manifest().canonical


def test_canonical_hash_is_pinned() -> None:
    # Every run hash depends on the canonical bytes. A change to the serialization or
    # to the example shows here and must be deliberate.
    assert _manifest().canonical_sha256() == (
        "379e46d47b7f00784115765b8d49bcf12cccda5bdcca0d850d99525c17f1ae03"
    )
