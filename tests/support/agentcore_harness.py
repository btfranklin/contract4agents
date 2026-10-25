from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from contract4agents.materialization._harness_configuration import HarnessConfiguration


def write_project(
    tmp_path: Path,
    *,
    approval: bool = False,
    delegate: bool = False,
    request_fields: str = "",
) -> Path:
    """Write one small project for offline Harness tests."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    sys.modules.pop("app_harness", None)
    authorization = "approval_required" if approval else "preapproved"
    parent = """\
agent Parent(request: Request) -> Result:
    goal = "Ask the analyst to answer the request."

composition analyze from Parent to Analyst:
    mode = delegate
    description = "Ask the analyst for a result."
    history = none
    map request = input.request
""" if delegate else ""
    (tmp_path / "system.contract").write_text(
        f'''\
type Request:
    question: string
{request_fields}

type Result:
    answer: string
    evidence_ids: list[string]

tool records.lookup(request: Request) -> Result:
    description = "Look up evidence for a request."
    side_effect = false

agent Analyst(request: Request) -> Result:
    use records.lookup:
        availability = enabled
        authorization = {authorization}
        execution = host
    goal = "Answer the request with evidence."

{parent}'''
    )
    (tmp_path / "app_harness.py").write_text(
        "def lookup(request):\n"
        "    return {\"answer\": request[\"question\"], \"evidence_ids\": []}\n"
    )
    (tmp_path / "contract4agents.targets.toml").write_text(
        '''\
schema_version = "1"

[targets.agentcore_harness]
adapter = "agentcore_harness"

[targets.agentcore_harness.tools."records.lookup"]
python = "app_harness:lookup"

[targets.agentcore_harness.profiles.test]
default_model = "synthetic-bedrock-model"

[targets.agentcore_harness.profiles.test.options]
execution_role_arn = "arn:aws:iam::123456789012:role/TestAgentRole"
'''
    )
    return tmp_path


class FakeHarnessControlClient:
    """Return configurable readback values without calling AWS."""

    harness_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:harness/TestAgent-abcdefghij"
    harness_id = "TestAgent-abcdefghij"
    endpoint_name = "stable"

    def __init__(
        self,
        configuration: HarnessConfiguration,
        agent_name: str = "Analyst",
    ) -> None:
        from contract4agents.materialization._harness_configuration import thaw

        self.request = thaw(configuration.agents[agent_name].request)
        self.agent_name = agent_name
        self.harness_mutation: dict[str, object] = {}
        self.endpoint_mutation: dict[str, object] = {}
        self.endpoint_responses: list[dict[str, object]] = []
        self.get_harness_calls: list[dict[str, object]] = []
        self.get_endpoint_calls: list[dict[str, object]] = []

    def get_harness(self, **kwargs: Any) -> Mapping[str, Any]:
        self.get_harness_calls.append(dict(kwargs))
        harness = {
            **self.request,
            "arn": self.harness_arn,
            "harnessVersion": "1",
            "status": "READY",
            **self.harness_mutation,
        }
        return {"harness": harness}

    def get_harness_endpoint(self, **kwargs: Any) -> Mapping[str, Any]:
        self.get_endpoint_calls.append(dict(kwargs))
        if self.endpoint_responses:
            endpoint = dict(self.endpoint_responses.pop(0))
        else:
            endpoint = {
                "harnessId": self.harness_id,
                "endpointName": self.endpoint_name,
                "liveVersion": "1",
                "targetVersion": "1",
                "status": "READY",
                "updatedAt": "2026-10-24T12:00:00Z",
                **self.endpoint_mutation,
            }
        return {"endpoint": endpoint}


class FakeHarnessRuntimeClient:
    """Return scripted Harness streams and retain request arguments."""

    def __init__(self, streams: list[list[dict[str, object]]] | None = None) -> None:
        self.streams = list(streams or [])
        self.requests: list[dict[str, object]] = []

    def invoke_harness(self, **kwargs: Any) -> Mapping[str, Any]:
        self.requests.append(dict(kwargs))
        if not self.streams:
            raise AssertionError("No Harness stream was queued")
        return {"stream": self.streams.pop(0)}


def success_stream(
    text: str = "{\"answer\": \"ready\", \"evidence_ids\": []}",
    *,
    metadata: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    if metadata is not None:
        usage = {"inputTokens": 0, "outputTokens": 0, **metadata}
        input_tokens, output_tokens = usage["inputTokens"], usage["outputTokens"]
        total_tokens = (
            input_tokens + output_tokens
            if type(input_tokens) is int and type(output_tokens) is int
            else 0
        )
        usage.setdefault("totalTokens", total_tokens)
        events.append({"metadata": {"usage": usage, "metrics": {"latencyMs": 1}}})
    return events


def tool_stream(
    tool_name: str,
    arguments: Mapping[str, object],
    tool_use_id: str = "call-1",
    *,
    metadata: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"name": tool_name, "toolUseId": tool_use_id}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": json.dumps(arguments, sort_keys=True)}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]
    if metadata is not None:
        usage = {"inputTokens": 0, "outputTokens": 0, **metadata}
        input_tokens, output_tokens = usage["inputTokens"], usage["outputTokens"]
        total_tokens = (
            input_tokens + output_tokens
            if type(input_tokens) is int and type(output_tokens) is int
            else 0
        )
        usage.setdefault("totalTokens", total_tokens)
        events.append({"metadata": {"usage": usage, "metrics": {"latencyMs": 1}}})
    return events
