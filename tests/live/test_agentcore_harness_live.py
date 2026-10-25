"""Opt-in synthetic invocation against explicitly deployed Harness resources."""

from __future__ import annotations

import importlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from contract4agents import materialize
from contract4agents.adapters.agentcore_harness import (
    AgentCoreHarnessMaterializationProvider,
    HarnessAgent,
    HarnessCompleted,
    HarnessInterrupted,
    HarnessRunContext,
    generate_harness_configuration,
    verify_harness_deployment,
)
from contract4agents.tracing import TraceAttempt

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/harness-research"


@pytest.mark.live
def test_synthetic_harness_tool_continuation() -> None:
    if os.environ.get("CONTRACT4AGENTS_RUN_HARNESS_LIVE") != "1":
        pytest.skip("Set CONTRACT4AGENTS_RUN_HARNESS_LIVE=1 to enable AWS cost")
    bindings = Path(os.environ["CONTRACT4AGENTS_HARNESS_LIVE_BINDINGS"])
    references = json.loads(Path(os.environ["CONTRACT4AGENTS_HARNESS_LIVE_DEPLOYMENTS"]).read_text())
    configuration = generate_harness_configuration(EXAMPLE, target="harness", profile="test", bindings=bindings)
    region = references["EvidenceAnalyst"]["harness_arn"].split(":")[3]
    boto3 = importlib.import_module("boto3")
    Config = importlib.import_module("botocore.config").Config
    control = boto3.client(
        "bedrock-agentcore-control", region_name=region, config=Config(retries={"total_max_attempts": 1})
    )
    runtime = boto3.client("bedrock-agentcore", region_name=region, config=Config(retries={"total_max_attempts": 1}))
    deployments = {}
    for name, configured in configuration.agents.items():
        # Bound cost before any invocation. The test creates no AWS resources.
        assert configured.request["maxIterations"] <= 4
        assert configured.request["maxTokens"] <= 1024
        assert configured.request["timeoutSeconds"] <= 60
        deployments[name] = verify_harness_deployment(configuration, name, client=control, **references[name])
    selected = deployments["EvidenceAnalyst"]
    deployed = control.get_harness(
        harnessId=selected.harness_arn.rsplit("/", 1)[1], harnessVersion=selected.harness_version
    )["harness"]
    runtime_arn = deployed["environment"]["agentCoreRuntimeEnvironment"]["agentRuntimeArn"]
    system = materialize(
        EXAMPLE,
        target="harness",
        profile="test",
        bindings=bindings,
        provider=AgentCoreHarnessMaterializationProvider(deployments=deployments, client=runtime),
    )
    agent = cast(HarnessAgent, system.agents["EvidenceAnalyst"])
    scope = HarnessRunContext(
        str(uuid.uuid4()),
        "synthetic-test-user",
        "synthetic-test-account",
        str(uuid.uuid4()),
        "synthetic-harness-test",
        TraceAttempt(str(uuid.uuid4()), str(uuid.uuid4()), 1),
    )
    try:
        interrupted = agent.invoke(
            {"request": {"question": "What is the fictional Northstar check status?"}}, context=scope
        )
        assert isinstance(interrupted, HarnessInterrupted), "The synthetic route must exercise its source tool"
        tool = agent.configuration.tools[interrupted.pending.tool_name]
        implementation = cast(Any, system.graph.implementations[tool.semantic_id])
        value = implementation(**agent.tool_arguments(interrupted.pending, context=scope))
        completed = agent.resume(interrupted.pending, value, context=scope)
        assert isinstance(completed, HarnessCompleted)
        validated = system.validate_output_for_agent(agent, completed.value)
        assert "synthetic-record-1" in cast(Any, validated).evidence_ids
    finally:
        # This is application cleanup of the new synthetic session only.
        # Existing Harness resources and endpoints remain deployment-owned.
        runtime.stop_runtime_session(agentRuntimeArn=runtime_arn, runtimeSessionId=scope.session_id)
