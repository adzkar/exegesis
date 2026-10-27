"""Shared pytest fixtures: --update-golden regenerates snapshots."""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="regenerate tests/golden/*.json instead of comparing",
    )


@pytest.fixture
def update_golden(request):
    return request.config.getoption("--update-golden")
