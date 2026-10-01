"""Write structured events as JSON lines: what ran, on which item, and how long it took."""

from __future__ import annotations

import json
from typing import TextIO

__all__ = ["EventLog"]

type _Field = str | int | float | bool | None


class EventLog:
    """One run's event log. Callers pass names, counts and durations only.

    An event never holds query text or a sampled value: the log is not redacted, so
    what it holds must be safe as is.

    >>> import io
    >>> sink = io.StringIO()
    >>> EventLog(sink, "r1").event("agent", "q01:1", "model_call", duration_ms=812, cache="miss")
    >>> sink.getvalue()
    '{"cache":"miss","duration_ms":812,"event":"model_call","item":"q01:1","run_id":"r1","stage":"agent"}\\n'
    """

    def __init__(self, sink: TextIO, run_id: str) -> None:
        self._sink = sink
        self._run_id = run_id

    def event(
        self,
        stage: str,
        item: str | None,
        event: str,
        *,
        duration_ms: int | None = None,
        **fields: _Field,
    ) -> None:
        """Write one event and flush it, so a crash keeps every event before it."""
        record: dict[str, _Field] = {
            **fields,
            "run_id": self._run_id,
            "stage": stage,
            "item": item,
            "event": event,
            "duration_ms": duration_ms,
        }
        self._sink.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self._sink.flush()
