"""Render `report.md`: the two coverage numbers, every stage's gates and counts, and the ranked gaps."""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

from docgap.models import RankedGap, RunCanonical

__all__ = ["render_report"]


def _share(part: int, whole: int) -> str:
    """Format a share exactly, from whole numbers, so no float rounding reaches the report.

    >>> _share(4, 6), _share(0, 0)
    ('66.7%', 'n/a')
    """
    return f"{Fraction(part, whole):.1%}" if whole else "n/a"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]], numeric_from: int) -> list[str]:
    align = ["---" if i < numeric_from else "---:" for i in range(len(header))]
    return [
        f"| {' | '.join(header)} |",
        f"| {' | '.join(align)} |",
        *(f"| {' | '.join(row)} |" for row in rows),
    ]


def render_report(canonical: RunCanonical, ranked: Sequence[RankedGap]) -> str:
    """Render a run's report from its canonical record and ranking only.

    Everything shown comes from the canonical part, so equal inputs give an
    identical report. Counts are shown as recorded, so nothing a stage counted is
    left out.
    """
    lines = [
        "# docgap report",
        "",
        f"As of {canonical.as_of:%Y-%m-%dT%H:%M:%SZ}.",
        "",
        "## Coverage",
        "",
    ]
    coverage = canonical.stages["coverage"].counts
    lines += _table(
        ["Coverage", "Documented", "Total", "Share"],
        [
            [
                label,
                str(coverage[f"{unit}.documented"]),
                str(coverage[f"{unit}.total"]),
                _share(coverage[f"{unit}.documented"], coverage[f"{unit}.total"]),
            ]
            for label, unit in (("Mart columns", "columns"), ("Executions", "executions"))
        ],
        numeric_from=1,
    )
    lines += [
        "",
        "An execution is one counted query touching one mart column, so a query that",
        "reads five columns counts five times.",
        "",
        "## Gates",
        "",
    ]
    lines += _table(
        ["Gate", "Value", "Threshold", "Passed"],
        [
            [f"{stage}.{name}", f"{gate.value:g}", f"{gate.threshold:g}", str(gate.passed)]
            for stage, record in canonical.stages.items()
            for name, gate in sorted(record.gates.items())
        ],
        numeric_from=1,
    )
    lines += ["", "## Counts", ""]
    lines += _table(
        ["Stage", "Count", "Value"],
        [
            [stage, name, str(value)]
            for stage, record in canonical.stages.items()
            for name, value in sorted(record.counts.items())
        ],
        numeric_from=2,
    )
    lines += [
        "",
        "## Ranked gaps",
        "",
        "Every mart column with no description, by score, then by column FQN. A column",
        "no counted query touched scores 0.",
        "",
    ]
    lines += _table(
        ["Rank", "Column", "Score", "Executions", "Failure rate"],
        [
            [
                str(gap.rank),
                f"`{gap.fqn}`",
                f"{gap.score:.4f}",
                str(gap.executions),
                f"{gap.failure_rate:.2f}",
            ]
            for gap in ranked
        ],
        numeric_from=2,
    )
    return "\n".join(lines) + "\n"
