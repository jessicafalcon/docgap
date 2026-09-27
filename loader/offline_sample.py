"""Cut the offline sample and the CI fixture from the pinned Open DAMIR files (ADR 0013).

A line is kept when the first 8 bytes of the SHA-256 of its dimension fields, read
as a big-endian integer, fall under 2**64 // one_in. Measures don't enter the key,
so rows that share a dimension combination are kept or dropped together. A smaller
rate keeps a subset of a larger one, so the fixture (1 in 5,000) is cut from the
sample (1 in 50) and is the same as cutting it from the source files.

    uv run python loader/offline_sample.py [--update-lock]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import tomllib
from pathlib import Path
from typing import NamedTuple

__all__ = ["Facts", "cut", "digest_file", "grain_key", "keeps", "main", "read_lock", "refresh"]

ROOT = Path(__file__).resolve().parents[1]
SOURCES_LOCK = ROOT / "loader" / "sources.lock"
SAMPLE_LOCK = ROOT / "loader" / "sample.lock"
SOURCE_DIR = ROOT / "data" / "open_damir"
SAMPLE_DIR = ROOT / "data" / "sample"
FIXTURE_DIR = ROOT / "fixtures" / "damir"

# Keep in sync with ADR 0013.
SAMPLE_ONE_IN = 50
FIXTURE_ONE_IN = 5000

# 56 variables and the empty field after each line's trailing `;` (ADR 0012).
FIELDS = 57


class Facts(NamedTuple):
    """What pins a cut file: its data rows, its size and the SHA-256 of its bytes."""

    rows: int
    bytes: int
    sha256: str


def grain_key(line: bytes) -> bytes:
    """Return the dimension fields of a source line, 1 to 16 and 30 to 56: the staging grain.

    >>> line = b";".join(b"d%d" % i for i in range(1, 57)) + b";\\n"
    >>> grain_key(line) == b";".join([b"d%d" % i for i in [*range(1, 17), *range(30, 57)]])
    True
    """
    fields = line.split(b";")
    if len(fields) != FIELDS:
        raise ValueError(f"expected {FIELDS} fields, got {len(fields)}: {line[:80]!r}")
    return b";".join(fields[:16] + fields[29:56])


def keeps(line: bytes, one_in: int) -> bool:
    """Whether the sampling rule keeps `line` at a rate of 1 in `one_in`."""
    digest = hashlib.sha256(grain_key(line)).digest()
    return int.from_bytes(digest[:8], "big") < 2**64 // one_in


def digest_file(path: Path) -> tuple[int, str]:
    """Return the size and SHA-256 of the file at `path`."""
    with path.open("rb") as f:
        return path.stat().st_size, hashlib.file_digest(f, "sha256").hexdigest()


def _open(path: Path) -> io.BufferedIOBase:
    return gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")


def cut(source: Path, out: Path, one_in: int, pinned: Facts | None) -> Facts:
    """Write the header and the kept lines of `source` to `out`, in source order.

    `out` appears only once it holds every kept line and, when `pinned` is given,
    matches it; on any failure it is left as it was.

    Raises:
        ValueError: a line without 57 fields, no line kept, or a result that
            differs from `pinned`.
    """
    tmp = out.with_name(f"{out.name}.tmp-{os.getpid()}")
    rows = 0
    try:
        with _open(source) as lines, tmp.open("wb") as sink:
            sink.write(next(lines))
            for line in lines:
                if keeps(line, one_in):
                    sink.write(line)
                    rows += 1
            sink.flush()
            os.fsync(sink.fileno())
        facts = Facts(rows, *digest_file(tmp))
        if rows == 0:
            raise ValueError(f"{out.name}: no line kept at 1 in {one_in}")
        if pinned is not None and facts != pinned:
            raise ValueError(f"{out.name}: cut gives {facts}, lock pins {pinned}")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return facts


def refresh(
    source: Path, source_pin: tuple[int, str], out: Path, one_in: int, pinned: Facts | None
) -> Facts:
    """Cut `out` from `source` after checking the source's size and SHA-256 against `source_pin`."""
    # An output that already matches the lock is not cut again, so a re-run after
    # a crash redoes only the files it didn't finish.
    if pinned is not None and out.exists() and digest_file(out) == pinned[1:]:
        return pinned
    if (found := digest_file(source)) != source_pin:
        raise ValueError(f"{source.name}: expected {source_pin}, found {found}")
    return cut(source, out, one_in, pinned)


def read_lock(path: Path) -> dict[str, Facts]:
    """Return the facts `sample.lock` pins, by the output's repo-relative path."""
    files = tomllib.loads(path.read_text(encoding="utf-8"))["files"]
    return {f["name"]: Facts(f["rows"], f["bytes"], f["sha256"]) for f in files}


def _write_lock(facts: dict[str, Facts]) -> None:
    lines = [
        "# Offline sample and CI fixture cut by loader/offline_sample.py (ADR 0013), in TOML.",
        "# `sha256` is over the uncompressed file bytes. Rewritten only by `--update-lock`.",
    ]
    for name, f in facts.items():
        lines += ["", "[[files]]", f'name = "{name}"', f"rows = {f.rows}", f"bytes = {f.bytes}"]
        lines.append(f'sha256 = "{f.sha256}"')
    tmp = SAMPLE_LOCK.with_name(f"{SAMPLE_LOCK.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(SAMPLE_LOCK)
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    """Cut the sample from each pinned source file, then the fixture from each sample file."""
    parser = argparse.ArgumentParser(
        description="Cut the offline sample and the CI fixture (ADR 0013)."
    )
    parser.add_argument(
        "--update-lock",
        action="store_true",
        help="Cut every file again and write its facts to loader/sample.lock instead of checking them.",
    )
    args = parser.parse_args(argv)
    sources = tomllib.loads(SOURCES_LOCK.read_text(encoding="utf-8"))["files"]
    locked = {} if args.update_lock else read_lock(SAMPLE_LOCK)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    facts: dict[str, Facts] = {}

    def step(source: Path, source_pin: tuple[int, str], out: Path, one_in: int) -> Facts:
        name = out.relative_to(ROOT).as_posix()
        if not args.update_lock and name not in locked:
            raise ValueError(f"{name}: not in {SAMPLE_LOCK.name}; run with --update-lock")
        facts[name] = refresh(source, source_pin, out, one_in, locked.get(name))
        return facts[name]

    for source in sources:
        month = source["name"].removesuffix(".gz")
        pin = (source["bytes"], source["sha256"])
        sample = step(SOURCE_DIR / source["name"], pin, SAMPLE_DIR / month, SAMPLE_ONE_IN)
        step(SAMPLE_DIR / month, sample[1:], FIXTURE_DIR / month, FIXTURE_ONE_IN)
    for name, f in facts.items():
        print(f"{name}: {f.rows} rows, {f.bytes} bytes, sha256 {f.sha256}")
    if args.update_lock:
        _write_lock(facts)


if __name__ == "__main__":
    main()
