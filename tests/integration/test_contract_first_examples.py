from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from contract4agents import compile_project, materialize
from contract4agents.eval_campaigns import CampaignConfig, FileEvalProvider, run_campaign
from contract4agents.materialization import RecordingMaterializationTraceSink

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ("incident-command", "multi-lens-research", "market-research-brief")


@pytest.mark.integration
@pytest.mark.parametrize("name", EXAMPLES)
def test_public_example_declares_materializes_and_evaluates(name: str) -> None:
    project = ROOT / "examples" / name
    artifacts = compile_project(project)
    trace_sink = RecordingMaterializationTraceSink()

    result = materialize(
        project,
        "openai",
        "test",
        materialization_trace_sink=trace_sink,
    )
    campaign = asyncio.run(
        run_campaign(
            artifacts.ir,
            result.plan,
            FileEvalProvider.load(project / "eval-data.json"),
            CampaignConfig(f"public-example:{name}"),
        )
    )

    assert set(result.agents) == {agent.name for agent in artifacts.ir.agents.values()}
    assert result.plan.contract_digest == artifacts.contract_digest
    assert trace_sink.events
    assert campaign.summary.rates.passed == 1
    assert campaign.summary.rates.violated == 0
    assert campaign.summary.rates.unverified == 0
