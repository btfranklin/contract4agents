from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.fixture_runner import DEFAULT_PROJECT


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--contract-project",
        action="store",
        default=str(DEFAULT_PROJECT),
        help="Contract4Agents project path for fixture-runner integration tests.",
    )


@pytest.fixture
def contract_project_path(pytestconfig: pytest.Config) -> Path:
    return Path(str(pytestconfig.getoption("--contract-project"))).resolve()
