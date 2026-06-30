from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.fixtures.fixture_runner import DEFAULT_PROJECT, run_fixture_project

ROOT = Path(__file__).resolve().parents[2]


def _load_env_var_from_dotenv(name: str) -> None:
    if os.getenv(name):
        return
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        cleaned = value.strip().strip('"').strip("'")
        if cleaned and cleaned != "replace-me":
            os.environ[name] = cleaned
        return


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_agents_sdk_live_ops_desk_flow(tmp_path: Path) -> None:
    if os.getenv("CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE") != "1":
        pytest.skip("set CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE=1 to run live OpenAI Agents SDK checks")

    _load_env_var_from_dotenv("OPENAI_API_KEY")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set")

    run_root = tmp_path / "openai-agent-live"
    report = await run_fixture_project(project_root=DEFAULT_PROJECT, run_root=run_root, mode="openai")

    assert report.passed
    assert len(report.starts) == 12
    assert report.cleaned
    assert run_root.exists()
    assert sorted(path.relative_to(run_root) for path in run_root.rglob("*") if path.is_file()) == [
        Path("reports/report.json"),
        Path("reports/report.md"),
    ]
