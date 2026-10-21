"""Draw the baseline docs: half the mart columns by hashed FQN, each with its every-column text.

The M mart column FQNs, as the dbt manifest gives them, are sorted by
`sha256(f"{seed}:{fqn}")` with `[seeds] baseline_docs`, and the first `floor(M / 2)`
are documented (ADR 0021). Each gets its text from `eval/column_docs.json`, the text
the ceiling arm reads, so a column is worded the same way in both (ADR 0025). The
draw is frozen in `warehouse/baseline_docs.lock`: a run that would change it fails,
so a re-run only rewrites the text in the marts YAML.

    uv run python -m eval.baseline_docs
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any, cast

from ruamel.yaml import YAML

from docgap.artifacts import write_atomic
from docgap.config import load_config
from docgap.manifest import read_marts
from eval.column_docs import DOCS, MARTS_YAML
from eval.docs_manifest import build_manifest

__all__ = ["LOCK", "document", "draw", "render_lock"]

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "warehouse" / "baseline_docs.lock"


def draw(fqns: Iterable[str], seed: int) -> list[str]:
    """Return the first half of the FQNs sorted by `sha256(f"{seed}:{fqn}")`, sorted by FQN.

    Half is `floor(M / 2)` of M.

    >>> draw(["A.S.T.X", "A.S.T.Y", "A.S.T.Z"], 3)
    ['A.S.T.X']

    Raises:
        ValueError: an FQN is listed twice.
    """
    listed = list(fqns)
    if len(set(listed)) != len(listed):
        raise ValueError("an FQN is listed twice")
    ordered = sorted(listed, key=lambda fqn: hashlib.sha256(f"{seed}:{fqn}".encode()).digest())
    return sorted(ordered[: len(ordered) // 2])


def render_lock(fqns: Sequence[str], *, seed: int, total: int) -> bytes:
    """Return the lock file: a header naming the rule, then `draw`'s FQNs, one per line."""
    header = (
        "# The baseline's documented mart columns, drawn by eval/baseline_docs.py (ADR 0021):\n"
        f'# the {total} mart column FQNs sorted by sha256(f"{seed}:{{fqn}}"), the first {len(fqns)}.\n'
        "# Frozen: only a decision record that supersedes ADR 0021 changes it.\n"
    )
    return (header + "".join(f"{fqn}\n" for fqn in fqns)).encode()


def _yaml() -> YAML:
    # The marts YAML's own layout, so a round trip changes only the descriptions.
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def document(marts_yaml: str, texts: Mapping[tuple[str, str], str]) -> str:
    """Return the marts YAML with each `(TABLE, COLUMN)` in `texts` described, comments kept.

    A table is its model's name uppercased, which is the FQN's while no mart sets an
    alias. The description goes after the column's `data_type`, replacing any earlier
    one, so a re-run rewrites it. `eval.docs_manifest.document_columns` sets every
    column for a `dbt parse` copy and drops comments; this writes the committed file.

    >>> print(document(
    ...     "models:\\n  - name: t\\n    # kept\\n    columns:\\n"
    ...     "      - name: A\\n        data_type: integer\\n",
    ...     {("T", "A"): "Année de soins"},
    ... ), end="")
    models:
      - name: t
        # kept
        columns:
          - name: A
            data_type: integer
            description: Année de soins

    Raises:
        ValueError: a column in `texts` isn't in the YAML, or a column outside it
            has a description, which the recorded rule didn't give it.
    """
    yaml = _yaml()
    # ruamel leaves `load` and `dump` untyped; the YAML's shape is dbt's.
    parsed = cast("MutableMapping[str, Any]", yaml.load(marts_yaml))  # pyright: ignore[reportUnknownMemberType]
    found: set[tuple[str, str]] = set()
    for model in parsed["models"]:
        for column in model.get("columns", []):
            key = (str(model["name"]).upper(), str(column["name"]))
            if key in texts:
                column.pop("description", None)
                column.insert(list(column).index("data_type") + 1, "description", texts[key])
                found.add(key)
            elif "description" in column:
                raise ValueError(f"{'.'.join(key)}: described outside the baseline draw")
    if missing := sorted(set(texts) - found):
        raise ValueError(f"not in the marts YAML: {missing}")
    out = io.StringIO()
    yaml.dump(parsed, out)  # pyright: ignore[reportUnknownMemberType]
    return out.getvalue()


def main() -> None:
    """Draw the lock from a parsed manifest, or check it, then write the text into the marts YAML."""
    config = load_config(ROOT / "docgap.toml")
    with tempfile.TemporaryDirectory() as scratch:
        manifest = build_manifest(Path(scratch) / "manifest.json", None)
        marts, _ = read_marts(manifest, config.manifest)
    fqns = marts.fqns()
    seed = config.seeds.baseline_docs
    locked = draw(fqns, seed)
    lock = render_lock(locked, seed=seed, total=len(fqns))
    if not LOCK.exists():
        write_atomic(LOCK, lambda sink: sink.write(lock))
    elif LOCK.read_bytes() != lock:
        sys.exit(f"{LOCK.relative_to(ROOT)}: the draw changed, and the lock is frozen (ADR 0021)")
    docs: dict[str, str] = json.loads(DOCS.read_text(encoding="utf-8"))
    texts = {(table, column): docs[column] for table, column in (f.split(".")[2:] for f in locked)}
    content = document(MARTS_YAML.read_text(encoding="utf-8"), texts).encode()
    write_atomic(MARTS_YAML, lambda sink: sink.write(content))
    print(f"{LOCK.relative_to(ROOT)}: {len(locked)} of {len(fqns)} columns documented")


if __name__ == "__main__":
    main()
