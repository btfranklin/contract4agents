from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.session import get_session  # type: ignore[import-untyped]
from botocore.validate import ParamValidator  # type: ignore[import-untyped]

from contract4agents._strict_json import json_array, json_object
from contract4agents.adapters._native_names import native_name
from contract4agents.adapters.agentcore_harness import (
    HarnessConfiguration,
    agentcore_harness_planner_capabilities,
    build_harness_configuration,
    generate_harness_configuration,
)
from contract4agents.compiler import artifact_digests, compile_project
from contract4agents.ir import semantic_id
from contract4agents.materialization._harness_configuration import (
    frozen_object,
    thaw,
)
from contract4agents.materialization._harness_deployment import verify_harness_deployment
from contract4agents.materialization._harness_stream import HarnessStream
from contract4agents.planning import PlannedSystem, plan_materialization
from contract4agents.target_bindings import load_target_bindings
from tests.support.agentcore_harness import (
    FakeHarnessControlClient,
    FakeHarnessRuntimeClient,
    success_stream,
    tool_stream,
    write_project,
)


class _RetryConfiguration(Protocol):
    retries: dict[str, object]


def _configuration(root: Path) -> HarnessConfiguration:
    return generate_harness_configuration(root, target="agentcore_harness", profile="test")


def test_configuration_is_deterministic_and_writes_no_aws_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_aws_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("Configuration generation must not create an AWS client")

    monkeypatch.setattr(boto3, "client", reject_aws_client)
    root = write_project(tmp_path / "project")
    first = _configuration(root)
    second = _configuration(root)
    agent = first.agents["Analyst"]
    request = json_object("request", thaw(agent.request))

    assert first.agents["Analyst"].configuration_digest == second.agents["Analyst"].configuration_digest
    assert request["allowedTools"] == [f"@*/{name}" for name in agent.tools]
    assert request["memory"] == {"disabled": {}}
    assert request["truncation"] == {"strategy": "none"}
    model = json_object("model", request["model"])
    assert json_object("bedrock config", model["bedrockModelConfig"])["apiFormat"] == "converse_stream"
    assert json_object("bedrock config", model["bedrockModelConfig"])["maxTokens"] == 4096
    assert request["maxIterations"] == 10
    assert request["maxTokens"] == 4096
    assert request["timeoutSeconds"] == 120

    destination = tmp_path / "configuration"
    first.write(destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["agents"]["Analyst"]["configuration_digest"] == agent.configuration_digest
    assert (destination / "Analyst.harness.json").is_file()


def test_delegate_configuration_uses_one_declared_inline_function(tmp_path: Path) -> None:
    root = write_project(tmp_path / "project", delegate=True)
    configuration = _configuration(root)
    parent = configuration.agents["Parent"]
    expected_name = native_name("delegate", semantic_id("edge", "analyze"))

    assert tuple(parent.tools) == (expected_name,)
    assert parent.request_payload()["allowedTools"] == [f"@*/{expected_name}"]
    assert json_object("tool", json_array("tools", parent.request_payload()["tools"])[0])["type"] == "inline_function"


@pytest.mark.parametrize("max_tokens", [512, 8192])
def test_configured_token_limit_reaches_model_and_harness(tmp_path: Path, max_tokens: int) -> None:
    root = write_project(tmp_path / "project")
    bindings = root / "contract4agents.targets.toml"
    bindings.write_text(bindings.read_text() + f"\nmax_tokens = {max_tokens}\n")

    request = _configuration(root).agents["Analyst"].request_payload()
    model = json_object("model", request["model"])
    bedrock = json_object("bedrock config", model["bedrockModelConfig"])

    assert bedrock["maxTokens"] == max_tokens
    assert request["maxTokens"] == max_tokens


def test_long_agent_names_have_stable_distinct_harness_names(tmp_path: Path) -> None:
    root = write_project(tmp_path, delegate=True)
    source = root / "system.contract"
    prefix = "ResearchCoordinatorWithSharedPrefix"
    source.write_text(source.read_text().replace("Analyst", prefix + "Alpha").replace("Parent", prefix + "Beta"))
    first = _configuration(root)
    second = _configuration(root)
    names = {name: agent.request["harnessName"] for name, agent in first.agents.items()}
    assert len(set(names.values())) == len(first.agents) == 2
    assert all(isinstance(name, str) and len(name) <= 40 for name in names.values())
    assert names == {name: agent.request["harnessName"] for name, agent in second.agents.items()}
    shape = get_session().get_service_model("bedrock-agentcore-control").operation_model("CreateHarness").input_shape
    for agent in first.agents.values():
        errors = ParamValidator().validate(agent.request_payload(), shape)
        assert not errors.has_errors(), errors.generate_report()


def test_low_level_configuration_builder_rejects_duplicate_explicit_harness_names(tmp_path: Path) -> None:
    root = write_project(tmp_path, delegate=True)
    target_file = root / "contract4agents.targets.toml"
    target_file.write_text(target_file.read_text() + '\nharness_name = "SharedHarness"\n')
    artifacts = compile_project(root)
    loaded = load_target_bindings(root, required=True)
    assert loaded.bindings is not None
    plan = plan_materialization(
        artifacts.ir,
        loaded.bindings,
        target="agentcore_harness",
        profile="test",
        capabilities=agentcore_harness_planner_capabilities(),
        artifact_digests=artifact_digests(artifacts),
    )
    with pytest.raises(ValueError, match="unique"):
        build_harness_configuration(PlannedSystem(artifacts, plan))


def test_generated_request_matches_installed_aws_create_harness_shape(tmp_path: Path) -> None:
    root = write_project(tmp_path / "project")
    configuration = _configuration(root)
    service = get_session().get_service_model("bedrock-agentcore-control")
    operation = service.operation_model("CreateHarness")
    request = thaw(configuration.agents["Analyst"].request)

    errors = ParamValidator().validate(request, operation.input_shape)

    assert not errors.has_errors(), errors.generate_report()


def test_computed_runtime_identity_is_retained_but_mounts_and_custom_network_are_rejected(tmp_path: Path) -> None:
    configuration = _configuration(write_project(tmp_path / "project"))
    control = FakeHarnessControlClient(configuration)
    runtime = {
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/TestRuntime-abcdefghij",
        "agentRuntimeId": "TestRuntime-abcdefghij",
        "agentRuntimeName": "TestRuntime",
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "filesystemConfigurations": [],
    }
    control.harness_mutation["environment"] = {"agentCoreRuntimeEnvironment": runtime}
    deployment = verify_harness_deployment(
        configuration, "Analyst", harness_arn=control.harness_arn, endpoint_name=control.endpoint_name, client=control
    )
    deployment.validate(configuration, "Analyst")
    assert deployment.observed_environment
    assert deployment.identity["environment_digest"].startswith("sha256:")
    for key, value in (
        ("networkConfiguration", {"networkMode": "VPC"}),
        ("filesystemConfigurations", [{"mountPath": "/unreviewed"}]),
    ):
        changed = {**runtime, key: value}
        control.harness_mutation["environment"] = {"agentCoreRuntimeEnvironment": changed}
        with pytest.raises(ValueError):
            verify_harness_deployment(
                configuration,
                "Analyst",
                harness_arn=control.harness_arn,
                endpoint_name=control.endpoint_name,
                client=control,
            )
        with pytest.raises(ValueError):
            replace(deployment, observed_environment=frozen_object({"agentCoreRuntimeEnvironment": changed})).validate(
                configuration,
                "Analyst",
            )


def test_adapter_created_aws_clients_disable_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    from contract4agents.materialization._harness_deployment import aws_client

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: calls.append((args, kwargs)))
    aws_client("bedrock-agentcore", region="us-west-2")
    assert cast(_RetryConfiguration, calls[0][1]["config"]).retries == {"total_max_attempts": 1}


@pytest.mark.parametrize("include_target", [True, False])
def test_deployment_verification_checks_readback_and_endpoint_stability(
    tmp_path: Path, include_target: bool,
) -> None:
    root = write_project(tmp_path / "project")
    configuration = _configuration(root)
    control = FakeHarnessControlClient(configuration)
    if not include_target:
        endpoint: dict[str, object] = {
            "harnessId": control.harness_id, "endpointName": control.endpoint_name,
            "liveVersion": "1", "status": "READY",
        }
        control.endpoint_responses.extend([endpoint, endpoint])
    deployment = verify_harness_deployment(
        configuration,
        "Analyst",
        harness_arn=control.harness_arn,
        endpoint_name=control.endpoint_name,
        client=control,
    )

    assert deployment.harness_version == "1"
    assert deployment.configuration_digest == configuration.agents["Analyst"].configuration_digest
    assert len(control.get_endpoint_calls) == 2
    assert control.get_harness_calls == [{"harnessId": control.harness_id, "harnessVersion": "1"}]

    for changed in (
        replace(deployment, contract_digest="sha256:wrong"),
        replace(deployment, plan_digest="sha256:wrong"),
        replace(deployment, configuration_digest="sha256:wrong"),
    ):
        with pytest.raises(ValueError, match="does not match"):
            changed.validate(configuration, "Analyst")


def test_ready_endpoint_with_a_different_target_version_is_refused(tmp_path: Path) -> None:
    configuration = _configuration(write_project(tmp_path / "project"))
    control = FakeHarnessControlClient(configuration)
    control.endpoint_mutation["targetVersion"] = "2"
    with pytest.raises(ValueError, match="not stable and ready"):
        verify_harness_deployment(
            configuration, "Analyst", harness_arn=control.harness_arn,
            endpoint_name=control.endpoint_name, client=control,
        )


def test_fake_runtime_client_returns_scripted_complete_streams() -> None:
    runtime = FakeHarnessRuntimeClient(
        [
            success_stream(metadata={"inputTokens": 2, "outputTokens": 3}),
            tool_stream("records_lookup", {"request": {"question": "where?"}}),
        ]
    )

    completed = runtime.invoke_harness(harnessId="runtime-id", messages=[])
    handoff = runtime.invoke_harness(harnessId="runtime-id", messages=[])

    assert HarnessStream.read(completed["stream"]).text == ('{"answer": "ready", "evidence_ids": []}')
    assert HarnessStream.read(completed["stream"]).usage == [{"inputTokens": 2, "outputTokens": 3, "totalTokens": 5}]
    parsed_handoff = HarnessStream.read(handoff["stream"])
    assert parsed_handoff.stop_reason == "tool_use"
    assert parsed_handoff.tool_name == "records_lookup"
    assert parsed_handoff.arguments == {"request": {"question": "where?"}}
    assert len(runtime.requests) == 2


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"maxTokens": 17}, "configuration mismatch"),
        ({"status": "CREATING"}, "not ready"),
        ({"skills": [{"path": "unreviewed"}]}, "unreviewed `skills`"),
        ({"hooks": [{"name": "unreviewed"}]}, "unreviewed `hooks`"),
        ({"environmentVariables": {"EXTRA": "value"}}, "unreviewed `environmentVariables`"),
    ],
)
def test_deployment_verification_rejects_unreviewed_harness_state(
    tmp_path: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    root = write_project(tmp_path / "project")
    configuration = _configuration(root)
    control = FakeHarnessControlClient(configuration)
    control.harness_mutation.update(mutation)

    with pytest.raises(ValueError, match=message):
        verify_harness_deployment(
            configuration,
            "Analyst",
            harness_arn=control.harness_arn,
            endpoint_name=control.endpoint_name,
            client=control,
        )


def test_deployment_verification_rejects_endpoint_change_during_readback(
    tmp_path: Path,
) -> None:
    root = write_project(tmp_path / "project")
    configuration = _configuration(root)
    control = FakeHarnessControlClient(configuration)
    first: dict[str, object] = {
        "harnessId": control.harness_id,
        "endpointName": control.endpoint_name,
        "liveVersion": "1",
        "targetVersion": "1",
        "status": "READY",
        "updatedAt": "2026-10-24T12:00:00Z",
    }
    second = {**first, "updatedAt": "2026-10-24T12:01:00Z"}
    control.endpoint_responses.extend([first, second])

    with pytest.raises(ValueError, match="endpoint changed"):
        verify_harness_deployment(
            configuration,
            "Analyst",
            harness_arn=control.harness_arn,
            endpoint_name=control.endpoint_name,
            client=control,
        )
