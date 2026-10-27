"""Explicit, read-only verification of a deployed AgentCore Harness."""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, TypedDict, cast

from contract4agents._strict_json import json_array, json_object, json_string, json_strings
from contract4agents.ir import FrozenJsonValue, FrozenMap
from contract4agents.materialization._harness_configuration import (
    HarnessConfiguration,
    content_digest,
    frozen_object,
    thaw,
)

_ARN = re.compile(
    r"arn:([^:]+):bedrock-agentcore:([a-z0-9-]+):([0-9]{12}):harness/([A-Za-z][A-Za-z0-9_]{0,39}-[A-Za-z0-9]{10})\Z"
)
_ENDPOINT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}\Z")


class HarnessPromptBlock(TypedDict):
    text: str


@dataclass(frozen=True)
class HarnessInvocationSettings:
    """Validated plan-owned AWS invocation fields."""

    model: Mapping[str, object]
    system_prompt: list[HarnessPromptBlock]
    tools: list[Mapping[str, object]] | None
    allowed_tools: list[str] | None
    max_iterations: int
    max_tokens: int
    timeout_seconds: int

    @classmethod
    def from_configuration(cls, request: Mapping[str, object]) -> HarnessInvocationSettings:
        prompts = []
        for value in json_array("Harness system prompt", thaw(request["systemPrompt"])):
            block = json_object("Harness prompt block", value)
            prompts.append(HarnessPromptBlock(text=json_string("Harness prompt text", block["text"])))
        limits = []
        for key in ("maxIterations", "maxTokens", "timeoutSeconds"):
            value = request[key]
            if type(value) is not int or value <= 0:
                raise TypeError(f"Harness `{key}` must be a positive integer")
            limits.append(value)
        has_tools = "tools" in request
        has_allowed_tools = "allowedTools" in request
        if has_tools != has_allowed_tools:
            raise ValueError("Harness configuration must include both `tools` and `allowedTools`, or neither")
        tools = (
            [json_object("Harness inline tool", value) for value in json_array("Harness tools", thaw(request["tools"]))]
            if has_tools
            else None
        )
        allowed_tools = (
            list(json_strings("Harness allowed tools", thaw(request["allowedTools"]))) if has_allowed_tools else None
        )
        if tools is not None and (not tools or not allowed_tools):
            raise ValueError("Harness tool configuration cannot be empty")
        return cls(
            model=json_object("Harness model", thaw(request["model"])),
            system_prompt=prompts,
            tools=tools,
            allowed_tools=allowed_tools,
            max_iterations=limits[0], max_tokens=limits[1], timeout_seconds=limits[2],
        )


class HarnessControlClient(Protocol):
    def get_harness(self, *, harnessId: str, harnessVersion: str) -> Mapping[str, object]: ...

    def get_harness_endpoint(self, *, harnessId: str, endpointName: str) -> Mapping[str, object]: ...


class HarnessRuntimeClient(Protocol):
    def invoke_harness(
        self,
        *,
        harnessArn: str,
        qualifier: str,
        runtimeSessionId: str,
        runtimeUserId: str,
        actorId: str,
        messages: list[dict[str, object]],
        model: Mapping[str, object],
        systemPrompt: list[HarnessPromptBlock],
        maxIterations: int,
        maxTokens: int,
        timeoutSeconds: int,
        tools: list[Mapping[str, object]] | None = None,
        allowedTools: list[str] | None = None,
    ) -> Mapping[str, object]: ...


def aws_client(service: str, *, region: str | None = None) -> object:
    """Create a client with no SDK retries and no resource writes."""

    try:
        boto3 = importlib.import_module("boto3")
        Config = importlib.import_module("botocore.config").Config
    except ImportError as exc:
        raise ImportError("Install contract4agents[agentcore-harness] to use AWS clients") from exc
    return boto3.client(service, region_name=region, config=Config(retries={"total_max_attempts": 1}))


@dataclass(frozen=True)
class HarnessDeployment:
    """Configuration observed at an endpoint during explicit verification.

    The application must keep the endpoint stable after this observation.
    This record does not prove IAM policy or a business effect.
    """

    harness_arn: str
    endpoint_name: str
    harness_version: str
    contract_digest: str
    plan_digest: str
    configuration_digest: str
    observed_configuration: FrozenMap[str, FrozenJsonValue]
    endpoint_digest: str
    observed_environment: FrozenMap[str, FrozenJsonValue] = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        if not _ARN.fullmatch(self.harness_arn) or not _ENDPOINT.fullmatch(self.endpoint_name):
            raise ValueError("Invalid Harness ARN or endpoint name")
        if not re.fullmatch(r"[1-9][0-9]{0,4}", self.harness_version):
            raise ValueError("Harness version must be concrete")
        object.__setattr__(self, "observed_configuration", frozen_object(self.observed_configuration))
        object.__setattr__(self, "observed_environment", frozen_object(self.observed_environment))

    @property
    def identity(self) -> dict[str, str]:
        return {
            "harness_arn": self.harness_arn,
            "endpoint": self.endpoint_name,
            "version": self.harness_version,
            "configuration_digest": self.configuration_digest,
            "endpoint_digest": self.endpoint_digest,
            "environment_digest": content_digest(self.observed_environment),
        }

    def validate(self, configuration: HarnessConfiguration, agent_name: str) -> None:
        agent = configuration.agents[agent_name]
        if (
            self.contract_digest != configuration.system.plan.contract_digest
            or self.plan_digest != configuration.system.plan.plan_digest
            or self.configuration_digest != agent.configuration_digest
        ):
            raise ValueError("Harness deployment evidence does not match the reviewed plan")
        _check_configuration(agent.request, self.observed_configuration)
        _check_environment(self.observed_environment)


def verify_harness_deployment(
    configuration: HarnessConfiguration,
    agent_name: str,
    *,
    harness_arn: str,
    endpoint_name: str,
    client: HarnessControlClient | None = None,
) -> HarnessDeployment:
    """Read an endpoint and its concrete configuration without provisioning."""

    match = _ARN.fullmatch(harness_arn)
    if match is None or not _ENDPOINT.fullmatch(endpoint_name):
        raise ValueError("Invalid Harness ARN or endpoint name")
    control = client or cast(HarnessControlClient, aws_client("bedrock-agentcore-control", region=match[2]))
    harness_id = match[4]
    first = json_object(
        "Harness endpoint", control.get_harness_endpoint(harnessId=harness_id, endpointName=endpoint_name)["endpoint"]
    )
    _check_endpoint(first, harness_id, endpoint_name)
    version = str(first["liveVersion"])
    observed = json_object(
        "Harness readback", control.get_harness(harnessId=harness_id, harnessVersion=version)["harness"]
    )
    if observed.get("arn") != harness_arn or observed.get("harnessVersion") != version:
        raise ValueError("Harness readback has a different deployment identity")
    if observed.get("status") != "READY":
        raise ValueError("Harness is not ready")
    request = configuration.agents[agent_name].request
    _check_configuration(request, observed)
    second = json_object(
        "Harness endpoint", control.get_harness_endpoint(harnessId=harness_id, endpointName=endpoint_name)["endpoint"]
    )
    _check_endpoint(second, harness_id, endpoint_name)
    if _endpoint_identity(first) != _endpoint_identity(second):
        raise ValueError("Harness endpoint changed during verification")
    # Keep only configuration fields. Provider timestamps and arbitrary metadata
    # are not part of configuration evidence.
    selected = {key: observed[key] for key in request if key != "tags"}
    return HarnessDeployment(
        harness_arn,
        endpoint_name,
        version,
        configuration.system.plan.contract_digest,
        configuration.system.plan.plan_digest,
        configuration.agents[agent_name].configuration_digest,
        frozen_object(selected),
        content_digest(_endpoint_identity(second)),
        frozen_object(observed.get("environment") or {}),
    )


def _check_endpoint(endpoint: Mapping[str, object], harness_id: str, name: str) -> None:
    if (
        endpoint.get("status") != "READY"
        or endpoint.get("harnessId") != harness_id
        or endpoint.get("endpointName") != name
        or endpoint.get("liveVersion") != endpoint.get("targetVersion")
        or not re.fullmatch(r"[1-9][0-9]{0,4}", str(endpoint.get("liveVersion", "")))
    ):
        raise ValueError("Harness endpoint is not stable and ready")


def _endpoint_identity(endpoint: Mapping[str, object]) -> dict[str, object]:
    return {
        key: str(endpoint.get(key, ""))
        for key in ("harnessId", "endpointName", "liveVersion", "targetVersion", "status", "updatedAt")
    }


def _check_configuration(planned: Mapping[str, object], observed: Mapping[str, object]) -> None:
    for key, expected in planned.items():
        if key == "tags":
            continue
        if key not in observed or content_digest(expected) != content_digest(observed[key]):
            raise ValueError(f"Harness configuration mismatch for `{key}`")
    for key in ("tools", "allowedTools"):
        if key not in planned and key in observed and thaw(observed[key]) not in (None, []):
            raise ValueError(f"Harness configuration mismatch for `{key}`")
    for key in ("skills", "hooks", "environmentVariables", "environmentArtifact", "authorizerConfiguration"):
        if thaw(observed.get(key)) not in (None, [], {}):
            raise ValueError(f"Harness has unreviewed `{key}` configuration")
    _check_environment(observed.get("environment") or {})


def _check_environment(environment: object) -> None:
    if not environment:
        return
    if not isinstance(environment, Mapping) or set(environment) != {"agentCoreRuntimeEnvironment"}:
        raise ValueError("Harness has an unsupported runtime environment")
    runtime = environment["agentCoreRuntimeEnvironment"]
    if not isinstance(runtime, Mapping) or set(runtime) - {
        "agentRuntimeArn",
        "agentRuntimeName",
        "agentRuntimeId",
        "lifecycleConfiguration",
        "networkConfiguration",
        "filesystemConfigurations",
    }:
        raise ValueError("Harness has unreviewed runtime settings")
    if runtime.get("filesystemConfigurations"):
        raise ValueError("Harness has unreviewed filesystem mounts")
    network = runtime.get("networkConfiguration")
    if network and thaw(network) != {"networkMode": "PUBLIC"}:
        raise ValueError("Harness has an unsupported network configuration")
