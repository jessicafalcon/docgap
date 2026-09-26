"""Load `docgap.toml` into validated sections and hash each section for the run's setup."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    RootModel,
    Strict,
    StringConstraints,
    model_validator,
)

from docgap.models import Actor, Key, NonEmptyStr, Probability, canonical_json

__all__ = [
    "ActorsConfig",
    "AgentConfig",
    "CallSiteConfig",
    "CallSitesConfig",
    "DocgapConfig",
    "EvidenceConfig",
    "GateConfig",
    "SeedsConfig",
    "SnapshotConfig",
    "load_config",
]

# An unquoted Snowflake role name, uppercased as Snowflake stores it.
RoleName = Annotated[str, StringConstraints(pattern=r"^[A-Z_][A-Z0-9_$]*$")]


class _Section(BaseModel):
    # No field has a default: a key missing from the file fails loading.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class SnapshotConfig(_Section):
    """What the snapshot exports."""

    history_window_days: PositiveInt


class ActorsConfig(
    RootModel[Annotated[dict[RoleName, Annotated[Actor, Strict(False)]], Field(min_length=1)]]
):
    """Role to actor class. Only mapped roles count as usage."""

    # Strict mode takes an enum only as an instance from Python objects, and TOML
    # gives strings. Lax mode here still accepts only the enum's values.
    model_config = ConfigDict(frozen=True, strict=True)


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


class CallSiteConfig(_Section):
    """The pinned model and the sampling settings sent at one call site."""

    model: NonEmptyStr
    sampling: dict[Key, str | int | float | bool]


class CallSitesConfig(RootModel[dict[Key, CallSiteConfig]]):
    """Call site name to its model settings."""

    model_config = ConfigDict(frozen=True, strict=True)


class DocgapConfig(_Section):
    """`docgap.toml`. Every key sits in a section, so the section hashes cover the file."""

    snapshot: SnapshotConfig
    actors: ActorsConfig
    evidence: EvidenceConfig
    gate: GateConfig
    seeds: SeedsConfig
    agent: AgentConfig
    call_sites: CallSitesConfig

    def section_sha256(self) -> dict[str, str]:
        """Hash each section's validated values, the entries of `RunSetup.config`.

        Values are hashed, not file bytes, so a comment, whitespace or key-order
        edit changes nothing, and `1` and `1.0` for a float hash the same.
        """
        return {
            name: hashlib.sha256(canonical_json(getattr(self, name))).hexdigest()
            for name in sorted(type(self).model_fields)
        }


def load_config(path: Path) -> DocgapConfig:
    """Read and validate a config file.

    Raises:
        tomllib.TOMLDecodeError: the file is not valid TOML.
        pydantic.ValidationError: a key is missing, unknown, of the wrong type or out of range.
    """
    return DocgapConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
