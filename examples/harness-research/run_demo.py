"""Application-owned synthetic Harness flow, with an optional deployed route."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast

from pydantic import BaseModel

from contract4agents import materialize
from contract4agents._strict_json import json_array, json_object, json_string
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


class _DeploymentReference(TypedDict):
    harness_arn: str
    endpoint_name: str


def _deployment_references(value: object) -> dict[str, _DeploymentReference]:
    references: dict[str, _DeploymentReference] = {}
    for name, item in json_object("deployments", value).items():
        reference = json_object("deployment", item)
        references[name] = {
            "harness_arn": json_string("harness_arn", reference["harness_arn"]),
            "endpoint_name": json_string("endpoint_name", reference["endpoint_name"]),
        }
    return references


class SyntheticAWS:
    """Script the AWS transport for the offline example; no model is called."""

    def __init__(self, configuration: HarnessConfiguration) -> None:
        self.configuration = configuration
        self.names = {self.arn(name): name for name in configuration.agents}

    @staticmethod
    def arn(name: str) -> str:
        return f"arn:aws:bedrock-agentcore:us-west-2:123456789012:harness/{name}-abcdefghij"

    def get_harness_endpoint(self, *, harnessId: str, endpointName: str) -> dict[str, object]:
        return {
            "endpoint": {
                "harnessId": harnessId,
                "endpointName": endpointName,
                "status": "READY",
                "liveVersion": "1",
                "targetVersion": "1",
            }
        }

    def get_harness(self, *, harnessId: str, harnessVersion: str) -> dict[str, object]:
        name = harnessId.removesuffix("-abcdefghij")
        return {
            "harness": {
                **self.configuration.agents[name].request_payload(),
                "arn": self.arn(name),
                "status": "READY",
                "harnessVersion": "1",
            }
        }

    def invoke_harness(
        self, *, harnessArn: str, messages: list[dict[str, object]], **_kwargs: object,
    ) -> dict[str, object]:
        name = self.names[harnessArn]
        content = json_object("content", json_array("content", messages[0]["content"])[0])
        start: dict[str, object] = {}
        if "toolResult" in content:
            tool_result = json_object("toolResult", content["toolResult"])
            result_content = json_object("tool result content", json_array("content", tool_result["content"])[0])
            delta: dict[str, object] = {"text": json.dumps(result_content["json"])}
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
    deployment_map: Mapping[str, _DeploymentReference] | None = None,
    output: Path = Path(".contract/harness-demo"),
) -> BaseModel:
    """Run only synthetic data, with a maximum of two continuations per agent."""

    configuration = generate_harness_configuration(ROOT, target="harness", profile="test", bindings=bindings)
    synthetic = SyntheticAWS(configuration) if offline else None
    if not offline and deployment_map is None:
        raise ValueError("Live execution requires an explicit deployment map")
    deployments = {}
    for name in configuration.agents:
        reference: _DeploymentReference = (
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
            if not callable(implementation):
                raise TypeError("The bound tool implementation must be callable")
            value = implementation(**child.tool_arguments(child_result.pending, context=child_scope))
            child_result = child.resume(child_result.pending, value, context=child_scope)
        if isinstance(child_result, HarnessCompleted):
            result = parent.resume(result.pending, child_result.value, context=context)
        else:
            result = child_result
    output.mkdir(parents=True, exist_ok=True)
    write_trace_jsonl(output / "trace.jsonl", NormalizedTrace(tuple(sink.events)))
    if not isinstance(result, HarnessCompleted):
        raise RuntimeError(f"Synthetic demonstration did not complete: {result.status}")
    validated = system.validate_output_for_agent(parent, result.value)
    if not isinstance(validated, BaseModel):
        raise TypeError("The research report must be a record")
    return validated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Use explicitly deployed AWS resources and incur model cost"
    )
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--deployment-map", type=Path)
    args = parser.parse_args()
    deployment_map = (
        _deployment_references(json.loads(args.deployment_map.read_text())) if args.deployment_map else None
    )
    result = run_demo(offline=not args.live, bindings=args.bindings, deployment_map=deployment_map)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
