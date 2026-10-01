"""The staging key covers the same grain the offline sample is cut by."""

from __future__ import annotations

import re

from offline_sample import FIXTURE_DIR, ROOT, grain_key

STAGING = ROOT / "warehouse" / "dbt" / "models" / "staging" / "stg_damir__prestations.sql"


def test_the_key_hashes_the_sample_grain_fields() -> None:
    # The model's Jinja list, not its SQL: the names are the quoted strings in it.
    block = re.search(r"\{%-? set grain = \[(.*?)\] %\}", STAGING.read_text(), re.DOTALL)
    assert block is not None
    with (FIXTURE_DIR / "A202501.csv").open("rb") as f:
        header = f.readline()
    assert re.findall(r"'(\w+)'", block.group(1)) == grain_key(header).decode().split(";")
