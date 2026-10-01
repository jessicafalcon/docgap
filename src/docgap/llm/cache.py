"""Store model responses on disk by a hash of everything that shaped them."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from docgap.artifacts import write_atomic

__all__ = ["ResponseCache", "cache_key"]


def _canonical(value: JsonValue) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


class _Inputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    model: str
    prompt_version: str
    state: JsonValue


class _Entry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    inputs: _Inputs
    response: JsonValue


def _inputs(model: str, prompt_version: str, state: JsonValue) -> JsonValue:
    return {"model": model, "prompt_version": prompt_version, "state": state}


def cache_key(model: str, prompt_version: str, state: JsonValue) -> str:
    """Hash a call's inputs: the model, the prompt version and the call site's state.

    >>> cache_key("m", "v1", {"b": 1, "a": [2]}) == cache_key("m", "v1", {"a": [2], "b": 1})
    True
    >>> cache_key("m", "v1", {"repetition": 1}) == cache_key("m", "v1", {"repetition": 2})
    False
    """
    return hashlib.sha256(_canonical(_inputs(model, prompt_version, state))).hexdigest()


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

    def put(self, model: str, prompt_version: str, state: JsonValue, response: JsonValue) -> None:
        """Write an entry atomically, so a crash never leaves half a response to replay."""
        entry = _canonical({"inputs": _inputs(model, prompt_version, state), "response": response})
        self._directory.mkdir(parents=True, exist_ok=True)
        write_atomic(
            self._path(cache_key(model, prompt_version, state)), lambda sink: sink.write(entry)
        )

    def discard(self, key: str) -> None:
        """Delete an entry, if there is one."""
        self._path(key).unlink(missing_ok=True)
