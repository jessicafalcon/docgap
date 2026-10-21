"""The baseline docs follow the recorded rule: the lock regenerates, and the YAML documents exactly it."""

from __future__ import annotations

import json
from random import Random

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from docgap.config import load_config
from docgap.manifest import Marts
from eval.baseline_docs import LOCK, ROOT, document, draw, render_lock
from eval.column_docs import DOCS

SEED = load_config(ROOT / "docgap.toml").seeds.baseline_docs
_FQN = st.from_regex(r"\AA\.S\.T[0-9]\.[A-Z]{1,3}\Z")


def _locked() -> list[str]:
    return [line for line in LOCK.read_text().splitlines() if not line.startswith("#")]


def test_the_lock_regenerates_from_the_parsed_marts(baseline_marts: Marts) -> None:
    # A mart column added or dropped after the lock changes the draw, and fails here.
    fqns = baseline_marts.fqns()
    assert LOCK.read_bytes() == render_lock(draw(fqns, SEED), seed=SEED, total=len(fqns))


def test_the_lock_holds_half_the_mart_columns(baseline_marts: Marts) -> None:
    locked = _locked()
    assert len(baseline_marts.fqns()) == 67
    assert len(locked) == 67 // 2
    assert set(locked) <= set(baseline_marts.fqns())


def test_the_baseline_documents_exactly_the_lock(baseline_marts: Marts) -> None:
    # Through dbt's own parse, as `describe()` and `coverage` read it: baseline coverage
    # is the locked number, and each text is the ceiling arm's (ADR 0025).
    docs = json.loads(DOCS.read_text())
    assert baseline_marts.column_descriptions == {
        fqn: docs[fqn.rsplit(".", 1)[1]] for fqn in _locked()
    }


@settings(max_examples=50)
@given(st.lists(_FQN, min_size=1, max_size=30, unique=True), _FQN, st.randoms())
def test_one_more_column_moves_at_most_one_other_across_the_cut(
    fqns: list[str], added: str, random: Random
) -> None:
    # ADR 0021's reason for a sort over a hash threshold. Input order never matters.
    assume(added not in fqns)
    before = set(draw(fqns, SEED))
    shuffled = [*fqns, added]
    random.shuffle(shuffled)
    after = set(draw(shuffled, SEED)) - {added}
    assert len(before ^ after) <= 1


def test_a_listed_twice_fqn_fails() -> None:
    with pytest.raises(ValueError, match="twice"):
        draw(["A.S.T.X", "A.S.T.X"], SEED)


YAML = """\
models:
  - name: t
    # kept
    columns:
      - name: A
        data_type: integer
        config:
          meta:
            sensitivity: public
      - name: B
        data_type: integer
"""


def test_document_adds_the_text_after_the_type_and_keeps_comments() -> None:
    out = document(YAML, {("T", "A"): "Année\nde soins « x »"})
    assert "    # kept\n" in out
    assert '        data_type: integer\n        description: "Année\\nde soins « x »"\n' in out
    # A re-run replaces the text instead of adding a second one.
    assert document(out, {("T", "A"): "Année\nde soins « x »"}) == out


def test_document_refuses_a_description_outside_the_draw() -> None:
    described = YAML.replace("      - name: B\n", "      - name: B\n        description: b\n")
    with pytest.raises(ValueError, match=r"T\.B: described outside"):
        document(described, {("T", "A"): "a"})


def test_document_refuses_a_column_not_in_the_yaml() -> None:
    with pytest.raises(ValueError, match="not in the marts YAML"):
        document(YAML, {("T", "C"): "c"})
