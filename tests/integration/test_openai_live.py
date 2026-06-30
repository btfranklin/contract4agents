from __future__ import annotations

import os
from pathlib import Path

import pytest

from contract4agents.adapters.openai import OpenAIAdapterUnavailable, OpenAISemanticJudge

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
async def test_openai_semantic_judge_live_incident_brief() -> None:
    if os.getenv("CONTRACT4AGENTS_RUN_OPENAI_LIVE") != "1":
        pytest.skip("set CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 to run live OpenAI API checks")

    _load_env_var_from_dotenv("OPENAI_API_KEY")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set")

    judge = OpenAISemanticJudge(model=os.getenv("CONTRACT4AGENTS_OPENAI_JUDGE_MODEL", "gpt-5.5"))
    incident_brief = {
        "summary": "Checkout errors are most likely tied to deploy 8f31c2 changing checkout cache behavior.",
        "likely_cause": "deploy 8f31c2",
        "impact": "Checkout returned elevated 503 responses for api-checkout during the incident window.",
        "evidence": [
            "logs.search found repeated api-checkout 503 errors after the deploy window",
            "deploys.list identified deploy 8f31c2 as the only checkout deploy near the regression",
            "metrics.query showed checkout error rate rising after the deploy",
        ],
        "next_actions": ["roll back deploy 8f31c2", "keep status page in draft until approval"],
    }

    try:
        passed = await judge.judge(
            output=incident_brief,
            criterion=(
                "The output should identify deploy 8f31c2 as the likely cause, cite concrete evidence, "
                "describe customer impact, and avoid claiming that a status-page update was already published."
            ),
        )
    except OpenAIAdapterUnavailable as exc:
        pytest.skip(str(exc))

    assert passed
