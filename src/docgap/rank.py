"""Rank the undocumented mart columns by usage and attributed failure, in a stable order."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Context, Decimal
from pathlib import Path

from docgap.artifacts import read_rows, rows_sha256, write_rows
from docgap.config import ManifestConfig, RankConfig
from docgap.manifest import Marts, load_marts
from docgap.models import ColumnUsage, RankedGap, StageRecord
from docgap.usage import executions

__all__ = ["RANKED_GAPS_FILE", "rank", "run_rank", "score"]

RANKED_GAPS_FILE = "ranked_gaps.parquet"
# Well past a float's 17 significant digits, so the one rounding that matters is
# the final conversion to float.
_CONTEXT = Context(prec=40, rounding=ROUND_HALF_EVEN)


def score(executions: int, failure_rate: float, w: float) -> float:
    """Score a column: `ln(1 + u) * (1 + w * r)`.

    Computed in decimal: `math.log` goes through the platform's math library,
    which can differ in the last bit between macOS and Linux, while `Decimal.ln()`
    is correctly rounded everywhere. The score is not rounded, so no new ties appear.

    >>> score(0, 0.0, 1.0)
    0.0
    >>> score(1, 0.0, 1.0)
    0.6931471805599453
    >>> score(1, 1.0, 1.0)  # every run failed on this column: twice the usage term
    1.3862943611198906
    """
    usage = _CONTEXT.ln(Decimal(1 + executions))
    lift = _CONTEXT.add(Decimal(1), _CONTEXT.multiply(Decimal(w), Decimal(failure_rate)))
    return float(_CONTEXT.multiply(usage, lift))


def rank(marts: Marts, usage: Iterable[ColumnUsage], *, w: float) -> list[RankedGap]:
    """Rank every mart column with no description, by score descending, then by FQN.

    A column no counted query touched scores 0 and still ranks, after every used
    column, so the ranking is the whole gap list (ADR 0020).

    Raises:
        ValueError: a usage row names a column that isn't in the marts.
    """
    counts = executions(usage, marts)
    # r is 0 until Phase 5's attribution stage supplies it.
    scored = sorted(
        ((score(counts[fqn], 0.0, w), fqn) for fqn in counts if fqn not in marts.documented),
        key=lambda item: (-item[0], item[1]),
    )
    return [
        RankedGap(rank=position, fqn=fqn, score=value, executions=counts[fqn], failure_rate=0.0)
        for position, (value, fqn) in enumerate(scored, start=1)
    ]


def run_rank(
    column_usage: Path,
    manifest: Path,
    *,
    manifest_config: ManifestConfig,
    config: RankConfig,
    setup_sha256: str,
    out_dir: Path,
) -> StageRecord:
    """Rank the gaps from `column_usage.parquet` and the dbt manifest into `ranked_gaps.parquet`."""
    usage = read_rows(column_usage, ColumnUsage)
    # Read once, so the input hash covers exactly the bytes parsed.
    manifest_bytes = manifest.read_bytes()
    rows = rank(load_marts(manifest_bytes, manifest_config), usage, w=config.w)
    out_dir.mkdir(parents=True, exist_ok=True)
    return StageRecord(
        setup_sha256=setup_sha256,
        inputs={
            "column_usage": rows_sha256(usage),
            "manifest": hashlib.sha256(manifest_bytes).hexdigest(),
        },
        outputs={"ranked_gaps": write_rows(rows, RankedGap, out_dir / RANKED_GAPS_FILE)},
        counts={
            "columns.ranked": len(rows),
            "columns.ranked_untouched": sum(row.executions == 0 for row in rows),
        },
        gates={},
    )
