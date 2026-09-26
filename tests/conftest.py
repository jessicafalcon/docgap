"""Shared pytest options."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    # Golden files and committed schemas change only when asked, never as a side
    # effect of a test run, so a behavior change always shows as a reviewable diff.
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="Rewrite golden files and committed schemas from the current code.",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))
