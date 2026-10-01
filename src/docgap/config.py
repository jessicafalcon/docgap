"""Load `docgap.toml` into validated sections and hash each section for the run's setup."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    RootModel,
    Strict,
    model_validator,
)

from docgap.models import (
    CONTRACT_CONFIG,
    Actor,
    Identifier,
    Key,
    ModelSettings,
    NonEmptyStr,
    Probability,
    RoleName,
    canonical_sha256,
)

__all__ = [
    "ActorsConfig",
    "AgentConfig",
    "CallSitesConfig",
    "DocgapConfig",
    "EvidenceConfig",
    "GateConfig",
    "LlmConfig",
    "ManifestConfig",
    "PilotConfig",
    "Price",
    "RankConfig",
    "SeedsConfig",
    "SnapshotConfig",
    "load_config",
]


class _Section(BaseModel):
    # No field has a default: a key missing from the file fails loading.
    model_config = CONTRACT_CONFIG


class SnapshotConfig(_Section):
    """What the snapshot exports, and the gates its output must pass."""

    history_window_days: PositiveInt
    min_rows_kept: PositiveInt
    min_parse_rate: Probability


class ActorsConfig(
    RootModel[Annotated[dict[RoleName, Annotated[Actor, Strict(False)]], Field(min_length=1)]]
):
    """Role to actor class. Only mapped roles count as usage."""

    # Strict mode takes an enum only as an instance from Python objects, and TOML
    # gives strings. Lax mode here still accepts only the enum's values.
    model_config = ConfigDict(frozen=True, strict=True)


class ManifestConfig(_Section):
    """Where the marts live: their dbt models are the columns docgap counts and ranks."""

    mart_database: Identifier
    mart_schema: Identifier


class RankConfig(_Section):
    """The rank weight: how much the attributed failure rate lifts a column's score."""

    w: NonNegativeFloat


class EvidenceConfig(_Section):
    """Profile limits for evidence packets."""

    min_value_count: PositiveInt


class GateConfig(_Section):
    """Draft-gate bands on the "fully supported" probability."""

    ready_min: Probability
    confirm_min: Probability

    @model_validator(mode="after")
    def _bands_ordered(self) -> Self:
        if not 0 < self.confirm_min < self.ready_min:
            raise ValueError("bands must satisfy 0 < confirm_min < ready_min")
        return self


class SeedsConfig(_Section):
    """Seeds for the question split, the random arm and the baseline docs."""

    split: NonNegativeInt
    random_arm: NonNegativeInt
    baseline_docs: NonNegativeInt


class AgentConfig(_Section):
    """Limits on the test agent's `run_sql` tool, per call."""

    statement_timeout_seconds: PositiveInt
    row_cap: PositiveInt


class Price(_Section):
    """A model's rates in USD per million tokens."""

    input: NonNegativeFloat
    output: NonNegativeFloat


class LlmConfig(_Section):
    """Every model call's timeout and retries, and the run's call and spend budget."""

    timeout_seconds: PositiveFloat
    max_retries: NonNegativeInt
    max_calls: PositiveInt
    max_spend_usd: PositiveFloat
    prices: dict[NonEmptyStr, Price]


class PilotConfig(_Section):
    """The pilot's two candidate agent models."""

    models: Annotated[list[NonEmptyStr], Field(min_length=2, max_length=2)]

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len(set(self.models)) != len(self.models):
            raise ValueError("the pilot compares two different models")
        return self


class CallSitesConfig(RootModel[dict[Key, ModelSettings]]):
    """Call site name to its model settings."""

    model_config = ConfigDict(frozen=True, strict=True)


class DocgapConfig(_Section):
    """`docgap.toml`. Every key sits in a section, so the section hashes cover the file."""

    snapshot: SnapshotConfig
    actors: ActorsConfig
    manifest: ManifestConfig
    rank: RankConfig
    evidence: EvidenceConfig
    gate: GateConfig
    seeds: SeedsConfig
    agent: AgentConfig
    llm: LlmConfig
    pilot: PilotConfig
    call_sites: CallSitesConfig

    @model_validator(mode="after")
    def _every_model_priced(self) -> Self:
        # An unpriced model would spend outside the budget.
        models = {site.model for site in self.call_sites.root.values()} | set(self.pilot.models)
        unpriced = sorted(models - set(self.llm.prices))
        if unpriced:
            raise ValueError(f"no price in [llm.prices] for {unpriced}")
        return self

    def section_sha256(self) -> dict[str, str]:
        """Hash each section's validated values, the entries of `RunSetup.config`.

        Values are hashed, not file bytes, so a comment, whitespace or key-order
        edit changes nothing, and `1` and `1.0` for a float field hash the same.
        A `sampling` value keeps its TOML type, so there `1` and `1.0` differ.
        """
        return {
            name: canonical_sha256(getattr(self, name)) for name in sorted(type(self).model_fields)
        }


def load_config(path: Path) -> DocgapConfig:
    """Read and validate a config file.

    Raises:
        tomllib.TOMLDecodeError: the file is not valid TOML.
        pydantic.ValidationError: a key is missing, unknown, of the wrong type or out of range.
    """
    return DocgapConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
