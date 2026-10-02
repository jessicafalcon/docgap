"""Store model responses on disk by a hash of everything that shaped them."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, JsonValue, ValidationError

from docgap.artifacts import write_atomic
from docgap.models import CONTRACT_CONFIG, canonical_json, canonical_sha256

__all__ = ["ResponseCache", "cache_key"]


class _Inputs(BaseModel):
    model_config = CONTRACT_CONFIG

    model: str
    prompt_version: str
    state: JsonValue


class _Entry(BaseModel):
    model_config = CONTRACT_CONFIG

    inputs: _Inputs
    response: JsonValue


def cache_key(model: str, prompt_version: str, state: JsonValue) -> str:
    """Hash a call's inputs: the model, the prompt version and the call site's state.

    >>> cache_key("m", "v1", {"b": 1, "a": [2]}) == cache_key("m", "v1", {"a": [2], "b": 1})
    True
    >>> cache_key("m", "v1", {"repetition": 1}) == cache_key("m", "v1", {"repetition": 2})
    False
    """
    return canonical_sha256(_Inputs(model=model, prompt_version=prompt_version, state=state))


class ResponseCache:
    """One JSON file per call, holding the key's inputs beside the response.

    An entry that doesn't parse, or whose inputs don't hash to its key, is deleted
    and read as a miss, so a corrupt file is never replayed.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path(self, key: str) -> Path:
        return self._directory / f"{key}.json"

    def get(self, key: str) -> JsonValue | None:
        """Return the cached response for a key, or None."""
        try:
            entry = _Entry.model_validate_json(self._path(key).read_bytes())
        except FileNotFoundError:
            return None
        except ValidationError:
            self.discard(key)
            return None
        inputs = entry.inputs
        if cache_key(inputs.model, inputs.prompt_version, inputs.state) != key:
            self.discard(key)
            return None
        return entry.response

    def put(
        self, key: str, model: str, prompt_version: str, state: JsonValue, response: JsonValue
    ) -> None:
        """Write an entry atomically, so a crash never leaves half a response to replay.

        `key` is `cache_key` of the inputs, which the caller has already computed;
        `get` checks it before replaying the entry.
        """
        inputs = _Inputs(model=model, prompt_version=prompt_version, state=state)
        entry = canonical_json(_Entry(inputs=inputs, response=response))
        self._directory.mkdir(parents=True, exist_ok=True)
        write_atomic(self._path(key), lambda sink: sink.write(entry))

    def discard(self, key: str) -> None:
        """Delete an entry, if there is one."""
        self._path(key).unlink(missing_ok=True)
