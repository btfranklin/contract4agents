"""Application-owned synthetic Harness flow, with an optional deployed route."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from contract4agents import materialize
from contract4agents.adapters.agentcore_harness import (
    AgentCoreHarnessMaterializationProvider,
    HarnessAgent,
    HarnessCompleted,
    HarnessConfiguration,
    HarnessInterrupted,
    HarnessRunContext,
    generate_harness_configuration,
    verify_harness_deployment,
)
from contract4agents.tracing import (
    NormalizedTrace,
    RecordingNormalizedTraceSink,
    TraceAttempt,
    write_trace_jsonl,
)

ROOT = Path(__file__).parent


class SyntheticAWS:
    """Script the AWS transport for the offline example; no model is called."""

    def __init__(self, configuration: HarnessConfiguration) -> None:
        self.configuration = configuration
        self.names = {self.arn(name): name for name in configuration.agents}

    @staticmethod
    def arn(name: str) -> str:
        return f"arn:aws:bedrock-agentcore:us-west-2:123456789012:harness/{name}-abcdefghij"

    def get_harness_endpoint(self, **kwargs: Any) -> dict[str, object]:
        return {
            "endpoint": {
                "harnessId": kwargs["harnessId"],
                "endpointName": kwargs["endpointName"],
                "status": "READY",
                "liveVersion": "1",
                "targetVersion": "1",
            }
        }

    def get_harness(self, **kwargs: Any) -> dict[str, object]:
        name = kwargs["harnessId"].removesuffix("-abcdefghij")
        return {
            "harness": {
                **self.configuration.agents[name].request_payload(),
                "arn": self.arn(name),
                "status": "READY",
                "harnessVersion": "1",
            }
        }

    def invoke_harness(self, **kwargs: Any) -> dict[str, object]:
        name = self.names[kwargs["harnessArn"]]
        content = kwargs["messages"][0]["content"][0]
        start: dict[str, object] = {}
        if "toolResult" in content:
            delta: dict[str, object] = {"text": json.dumps(content["toolResult"]["content"][0]["json"])}
            reason = "end_turn"
        else:
            tool = next(iter(self.configuration.agents[name].tools))
            start = {"toolUse": {"name": tool, "toolUseId": f"synthetic-{uuid.uuid4()}"}}
            delta = {"toolUse": {"input": content["text"]}}
            reason = "tool_use"
        return {
            "stream": [
                {"messageStart": {"role": "assistant"}},
                *([{"contentBlockStart": {"contentBlockIndex": 0, "start": start}}] if start else []),
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": delta}},
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": reason}},
            ]
        }


def run_context(run_id: str, *, parent: str | None = None) -> HarnessRunContext:
    return HarnessRunContext(
        session_id=str(uuid.uuid4()),
        principal_id="synthetic-demo-user",
        authorization_context="synthetic-demo-account",
        run_id=run_id,
        thread_id=run_id,
        attempt=TraceAttempt(str(uuid.uuid4()), str(uuid.uuid4()), 1),
        parent_event_id=parent,
    )


def run_demo(
    *,
    offline: bool,
    bindings: Path | None = None,
    deployment_map: Mapping[str, Mapping[str, str]] | None = None,
    output: Path = Path(".contract/harness-demo"),
) -> object:
    """Run only synthetic data, with a maximum of two continuations per agent."""

    configuration = generate_harness_configuration(ROOT, target="harness", profile="test", bindings=bindings)
    synthetic = SyntheticAWS(configuration) if offline else None
    if not offline and deployment_map is None:
        raise ValueError("Live execution requires an explicit deployment map")
    deployments = {}
    for name in configuration.agents:
        reference = (
            {"harness_arn": SyntheticAWS.arn(name), "endpoint_name": "stable"}
            if offline
            else (deployment_map or {})[name]
        )
        deployments[name] = verify_harness_deployment(configuration, name, client=synthetic, **reference)
    sink = RecordingNormalizedTraceSink()
    system = materialize(
        ROOT,
        target="harness",
        profile="test",
        bindings=bindings,
        provider=AgentCoreHarnessMaterializationProvider(deployments=deployments, client=synthetic),
        normalized_trace_sink=sink,
    )
    parent = cast(HarnessAgent, system.agents["ResearchCoordinator"])
    child = cast(HarnessAgent, system.agents["EvidenceAnalyst"])
    context = run_context(str(uuid.uuid4()))
    result = parent.invoke({"request": {"question": "What is the fictional Northstar check status?"}}, context=context)
    if isinstance(result, HarnessInterrupted):
        child_scope = run_context(context.run_id, parent=result.pending.event_id)
        child_result = parent.invoke_delegate(result.pending, child, context=context, child_context=child_scope)
        if isinstance(child_result, HarnessInterrupted):
            tool = child.configuration.tools[child_result.pending.tool_name]
            # This is the application's explicit dispatch to the approved local tool.
            implementation = system.graph.implementations[tool.semantic_id]
            value = cast(Any, implementation)(**child.tool_arguments(child_result.pending, context=child_scope))
            child_result = child.resume(child_result.pending, value, context=child_scope)
        if isinstance(child_result, HarnessCompleted):
            result = parent.resume(result.pending, child_result.value, context=context)
        else:
            result = child_result
    output.mkdir(parents=True, exist_ok=True)
    write_trace_jsonl(output / "trace.jsonl", NormalizedTrace(tuple(sink.events)))
    if not isinstance(result, HarnessCompleted):
        raise RuntimeError(f"Synthetic demonstration did not complete: {result.status}")
    return system.validate_output_for_agent(parent, result.value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Use explicitly deployed AWS resources and incur model cost"
    )
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--deployment-map", type=Path)
    args = parser.parse_args()
    deployment_map = json.loads(args.deployment_map.read_text()) if args.deployment_map else None
    result = run_demo(offline=not args.live, bindings=args.bindings, deployment_map=deployment_map)
    print(cast(Any, result).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
