from __future__ import annotations

from pathlib import Path

from contract4agents.fixtures import (
    FixtureArtifactError,
    FixtureConfigError,
    FixtureReport,
    FixtureRetryError,
    StartReport,
    load_fixture_metadata,
    run_fixture_project,
    run_fixture_project_sync,
    verify_fixture_artifacts,
)

DEFAULT_PROJECT = Path(__file__).parent / "contract_projects" / "ops-desk-lab"

__all__ = [
    "DEFAULT_PROJECT",
    "FixtureArtifactError",
    "FixtureConfigError",
    "FixtureReport",
    "FixtureRetryError",
    "StartReport",
    "load_fixture_metadata",
    "run_fixture_project",
    "run_fixture_project_sync",
    "verify_fixture_artifacts",
]
