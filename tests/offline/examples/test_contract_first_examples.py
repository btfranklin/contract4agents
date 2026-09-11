from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from contract4agents import materialize
from contract4agents.eval_campaigns import CampaignConfig, FileEvalProvider, run_campaign
from contract4agents.materialization import RecordingMaterializationTraceSink

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ("incident-command", "multi-lens-research", "market-research-brief")
TARGETS = ("openai", "strands", "google_adk")


@pytest.mark.integration
@pytest.mark.parametrize("name", EXAMPLES)
def test_public_example_declares_materializes_and_evaluates(name: str) -> None:
    project = ROOT / "examples" / name
    trace_sink = RecordingMaterializationTraceSink()

    result = materialize(
        project,
        "openai",
        "test",
        materialization_trace_sink=trace_sink,
    )
    artifacts = result.artifacts
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


@pytest.mark.integration
@pytest.mark.parametrize("target", TARGETS)
def test_input_helpers_accept_real_native_agent_objects(target: str) -> None:
    system = materialize(ROOT / "examples" / "incident-command", target, "test")
    agent = system.agents["IncidentCommander"]
    values = {
        "request": {
            "service": "checkout",
            "start": "2026-09-10T12:00:00Z",
            "end": "2026-09-10T12:15:00Z",
            "symptom": "Elevated errors",
        },
        "service": {"id": "svc-1", "name": "Checkout", "owner": "Commerce"},
        "window": {
            "start": "2026-09-10T12:00:00Z",
            "end": "2026-09-10T12:15:00Z",
        },
    }

    validated = system.validate_input_for_agent(agent, values)
    serialized = system.serialize_input_for_agent(agent, values)

    assert validated is not None
    assert json.loads(serialized)["request"]["service"] == "checkout"
