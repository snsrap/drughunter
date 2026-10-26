"""Shared by the whole suite: the environment report ``pytest -m health`` prints."""

import pytest

_HEALTH = pytest.StashKey[list]()


@pytest.fixture
def health_report(request):
    """Lines for the environment summary, printed once at the end of the run.

    The health tests add to it whether they pass, skip or fail, so one run
    leaves a single block a user can paste into a bug report.
    """
    return request.config.stash.setdefault(_HEALTH, [])


def pytest_terminal_summary(terminalreporter, config):
    lines = config.stash.get(_HEALTH, [])
    if lines:
        terminalreporter.section("drughunter environment")
        for line in lines:
            terminalreporter.write_line(line)
