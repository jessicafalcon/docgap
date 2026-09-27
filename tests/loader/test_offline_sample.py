"""The offline sample keeps a line by its hashed dimension key, and the committed fixture follows the rule."""

from __future__ import annotations

import gzip
import hashlib
import tomllib
from pathlib import Path

import offline_sample
import pytest
from offline_sample import (
    FIXTURE_ONE_IN,
    ROOT,
    SAMPLE_LOCK,
    SAMPLE_ONE_IN,
    SOURCES_LOCK,
    Facts,
    cut,
    digest_file,
    grain_key,
    keeps,
    read_lock,
    refresh,
)

HEADER = b"FLX_ANN_MOI;" + b";".join(b"V%d" % i for i in range(2, 57)) + b";\n"

# Real lines of A202501.csv.gz, the first 8 bytes of their key's SHA-256, and whether
# the sample (1 in 50) and the fixture (1 in 5,000) keep them. They pin the rule
# itself: the fields in the key, the hash, and how its bytes are read.
REAL = [
    (
        b"202501;99;20;93;0;1;2;121;9999;99;99;11;2205;9;99;0;5;5;5;0;0;0;-5;5;5;5;0;0;-5;2025;"
        b"01;10;0;0;1;11;9;41;0;1976;2;100;0;31;53;24;6;0;1;99;0;0;1;2;1;Z;\n",
        "0000ee23fe1f9c67",
        (True, True),
    ),
    (
        b"202501;53;20;53;1;1;2;121;9999;99;99;99;9999;9;99;0;30;4;4;0;93;93;93;30;4;4;93;0;93;2024;"
        b"11;30;0;0;1;36;9;10;0;1911;2;100;0;31;53;21;5;0;1;99;0;99;0;9;1;Z;\n",
        "028b87f70269bea8",
        (True, False),
    ),
    (
        b"202501;32;70;99;0;1;1;121;9999;99;99;99;9999;9;99;0;23.21;22;22;0;696.3;696.3;696.3;"
        b"23.21;22;22;696.3;0;696.3;2025;01;10;0;0;1;35;9;42;0;3134;2;100;0;31;32;27;8;0;1;32;"
        b"27;8;0;1;1;Z;\n",
        "0590e277b721d679",
        (False, False),
    ),
    (
        b"202501;99;60;99;1;1;2;121;9999;99;99;32;1102;9;99;0;3;3;3;0;33.75;0;5.1;0;0;0;0;0;0;"
        b"2025;01;10;0;2;7;36;9;62;1;1848;2;20;5;31;32;24;6;0;1;99;0;0;9;2;0;Z;\n",
        "1fc1214320861290",
        (False, False),
    ),
]


def _line(dims: int, measure: int = 0) -> bytes:
    """A synthetic source line: every dimension field carries `dims`, every measure `measure`."""
    fields = [str(measure if 16 <= i < 29 else dims) for i in range(56)]
    return ";".join(fields).encode() + b";\n"


def _gz(path: Path, lines: list[bytes]) -> Path:
    with gzip.open(path, "wb") as f:
        f.writelines([HEADER, *lines])
    return path


@pytest.mark.parametrize(("line", "prefix", "kept"), REAL, ids=[r[1] for r in REAL])
def test_rule_is_pinned_on_real_lines(line: bytes, prefix: str, kept: tuple[bool, bool]) -> None:
    assert hashlib.sha256(grain_key(line)).digest()[:8].hex() == prefix
    assert (keeps(line, SAMPLE_ONE_IN), keeps(line, FIXTURE_ONE_IN)) == kept


def test_measures_never_change_the_pick() -> None:
    # Rows that repeat a dimension combination are kept or dropped together, so the
    # staging aggregate over the sample is exact for every combination it holds.
    assert grain_key(_line(7, measure=1)) == grain_key(_line(7, measure=999))


def test_a_smaller_rate_keeps_a_subset_of_a_larger_one() -> None:
    lines = [_line(i) for i in range(100_000)]
    sample = {line for line in lines if keeps(line, SAMPLE_ONE_IN)}
    fixture = {line for line in lines if keeps(line, FIXTURE_ONE_IN)}
    assert fixture
    assert fixture <= sample
    assert 1_800 < len(sample) < 2_200  # 2,000 expected at 1 in 50


def test_a_line_without_57_fields_fails() -> None:
    with pytest.raises(ValueError, match="expected 57 fields, got 56"):
        grain_key(_line(1).removesuffix(b";\n") + b"\n")


def test_cut_writes_the_header_and_kept_lines_in_source_order(tmp_path: Path) -> None:
    lines = [_line(i) for i in range(200)]
    out = tmp_path / "A.csv"

    facts = cut(_gz(tmp_path / "A.csv.gz", lines), out, 2, None)

    kept = [line for line in lines if keeps(line, 2)]
    assert out.read_bytes() == HEADER + b"".join(kept)
    assert facts == Facts(len(kept), *digest_file(out))
    assert sorted(tmp_path.iterdir()) == [out, tmp_path / "A.csv.gz"]


def test_cut_off_the_lock_leaves_no_file(tmp_path: Path) -> None:
    source = _gz(tmp_path / "A.csv.gz", [_line(i) for i in range(200)])
    out = tmp_path / "A.csv"

    with pytest.raises(ValueError, match="lock pins"):
        cut(source, out, 2, Facts(1, 1, "0" * 64))

    assert sorted(tmp_path.iterdir()) == [source]


def test_a_failure_mid_cut_keeps_the_previous_file(tmp_path: Path) -> None:
    source = _gz(tmp_path / "A.csv.gz", [_line(i) for i in range(200)] + [b"truncated;\n"])
    out = tmp_path / "A.csv"
    out.write_bytes(b"previous")

    with pytest.raises(ValueError, match="expected 57 fields"):
        cut(source, out, 2, None)

    assert out.read_bytes() == b"previous"
    assert sorted(tmp_path.iterdir()) == [out, source]


def test_a_cut_that_keeps_nothing_fails(tmp_path: Path) -> None:
    kept_at_two = [_line(i) for i in range(200) if not keeps(_line(i), 2)]

    with pytest.raises(ValueError, match="no line kept at 1 in 2"):
        cut(_gz(tmp_path / "A.csv.gz", kept_at_two), tmp_path / "A.csv", 2, None)


def test_a_source_off_its_checksum_fails_before_writing(tmp_path: Path) -> None:
    source = _gz(tmp_path / "A.csv.gz", [_line(i) for i in range(200)])
    out = tmp_path / "A.csv"

    with pytest.raises(ValueError, match=r"A\.csv\.gz: expected"):
        refresh(source, (1, "0" * 64), out, 2, None)

    assert not out.exists()


def test_a_stale_output_is_cut_again_from_the_checked_source(tmp_path: Path) -> None:
    source = _gz(tmp_path / "A.csv.gz", [_line(i) for i in range(200)])
    expected = cut(source, tmp_path / "expected.csv", 2, None)
    out = tmp_path / "A.csv"
    out.write_bytes(b"stale")

    assert refresh(source, digest_file(source), out, 2, expected) == expected
    assert out.read_bytes() == (tmp_path / "expected.csv").read_bytes()


def test_an_output_matching_the_lock_is_not_cut_again(tmp_path: Path) -> None:
    out = tmp_path / "A.csv"
    out.write_bytes(HEADER)
    pinned = Facts(0, *digest_file(out))

    # The source doesn't exist: reaching it would raise.
    assert refresh(tmp_path / "missing.csv.gz", (0, ""), out, 2, pinned) == pinned


def test_lock_pins_a_sample_and_a_fixture_file_per_source() -> None:
    sources = tomllib.loads(SOURCES_LOCK.read_text(encoding="utf-8"))["files"]
    months = [s["name"].removesuffix(".gz") for s in sources]
    expected = [f"{d}/{m}" for m in months for d in ("data/sample", "fixtures/damir")]
    assert list(read_lock(SAMPLE_LOCK)) == expected


@pytest.mark.parametrize("name", [n for n in read_lock(SAMPLE_LOCK) if n.startswith("fixtures/")])
def test_committed_fixture_matches_the_lock_and_the_rule(name: str) -> None:
    path = ROOT / name
    header, *lines = path.read_bytes().splitlines(keepends=True)
    month = path.stem.removeprefix("A").encode()

    assert Facts(len(lines), *digest_file(path)) == read_lock(SAMPLE_LOCK)[name]
    assert header.startswith(b"FLX_ANN_MOI;ORG_CLE_REG;")
    assert all(keeps(line, FIXTURE_ONE_IN) for line in lines)
    # One processing month per file: every month of the source appears.
    assert {line.split(b";", 1)[0] for line in lines} == {month}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with one pinned source month, cut at 1 in 2 and 1 in 4 to keep it small."""
    source = _gz(tmp_path / "A202501.csv.gz", [_line(i) for i in range(200)])
    size, sha = digest_file(source)
    lock = tmp_path / "sources.lock"
    lock.write_text(f'[[files]]\nname = "A202501.csv.gz"\nbytes = {size}\nsha256 = "{sha}"\n')
    for name, value in {
        "ROOT": tmp_path,
        "SOURCES_LOCK": lock,
        "SAMPLE_LOCK": tmp_path / "sample.lock",
        "SOURCE_DIR": tmp_path,
        "SAMPLE_DIR": tmp_path / "sample",
        "FIXTURE_DIR": tmp_path / "fixture",
        "SAMPLE_ONE_IN": 2,
        "FIXTURE_ONE_IN": 4,
    }.items():
        monkeypatch.setattr(offline_sample, name, value)
    return tmp_path


def test_update_lock_pins_what_a_check_run_then_accepts(repo: Path) -> None:
    offline_sample.main(["--update-lock"])
    locked = read_lock(repo / "sample.lock")

    assert list(locked) == ["sample/A202501.csv", "fixture/A202501.csv"]
    assert locked["sample/A202501.csv"] == Facts(
        locked["sample/A202501.csv"].rows, *digest_file(repo / "sample" / "A202501.csv")
    )
    assert 0 < locked["fixture/A202501.csv"].rows < locked["sample/A202501.csv"].rows
    offline_sample.main([])  # every output matches the lock
    assert not list(repo.rglob("*.tmp-*"))


def test_an_output_missing_from_the_lock_fails(repo: Path) -> None:
    offline_sample.main(["--update-lock"])
    lock = repo / "sample.lock"
    lock.write_text(lock.read_text().split('\n\n[[files]]\nname = "fixture/')[0] + "\n")

    with pytest.raises(ValueError, match=r"fixture/A202501\.csv: not in sample\.lock"):
        offline_sample.main([])
