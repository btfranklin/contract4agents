from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.awsrequest import AWSResponse  # type: ignore[import-untyped]
from botocore.parsers import ResponseParserError  # type: ignore[import-untyped]

from contract4agents._strict_json import json_object
from contract4agents.adapters.agentcore_harness import generate_harness_configuration, verify_harness_deployment
from contract4agents.materialization._harness_deployment import HarnessControlClient, aws_client
from tests.support.agentcore_harness import FakeHarnessControlClient, write_project


class _Body:
    def __init__(self, value: bytes) -> None:
        self.value = value

    def stream(self) -> Iterator[bytes]:
        yield self.value


def _client(
    monkeypatch: pytest.MonkeyPatch, truncation: dict[str, object], *,
    adapter_created: bool = True, harness: dict[str, object] | None = None,
) -> HarnessControlClient:
    original = boto3.client
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: original(
        *args, **kwargs, aws_access_key_id="synthetic-key", aws_secret_access_key="synthetic-secret"
    ))
    client = (aws_client("bedrock-agentcore-control", region="us-east-1") if adapter_created
              else boto3.client("bedrock-agentcore-control", region_name="us-east-1"))
    body = json.dumps({"harness": {
        "harnessId": "TestAgent-abcdefghij", "status": "READY",
        "allowedTools": ["read_frozen_source"],
        **(harness or {}), "truncation": truncation,
    }}).encode()
    monkeypatch.setattr("botocore.httpsession.URLLib3Session.send", lambda _session, request: AWSResponse(
        request.url, 200, {"content-type": "application/json"}, _Body(body)
    ))
    return cast(HarnessControlClient, client)


def test_none_truncation_empty_optional_config_survives_the_real_sdk_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, {"strategy": "none", "config": {}})
    result = client.get_harness(harnessId="TestAgent-abcdefghij", harnessVersion="1")
    harness = json_object("Harness", result["harness"])
    assert harness["truncation"] == {"strategy": "none"}
    assert harness["allowedTools"] == ["read_frozen_source"]
    assert harness["status"] == "READY"


def test_empty_config_for_an_active_strategy_still_fails_sdk_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, {"strategy": "sliding_window", "config": {}})
    with pytest.raises(ResponseParserError, match="one and only one member"):
        client.get_harness(harnessId="TestAgent-abcdefghij", harnessVersion="1")


def test_caller_supplied_sdk_client_supports_the_same_service_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = generate_harness_configuration(
        write_project(tmp_path / "project"), target="agentcore_harness", profile="test"
    )
    fake = FakeHarnessControlClient(configuration)
    harness = dict(json_object("Harness", fake.get_harness()["harness"]))
    client = _client(monkeypatch, {"strategy": "none", "config": {}}, adapter_created=False, harness=harness)
    monkeypatch.setattr(client, "get_harness_endpoint", fake.get_harness_endpoint)
    for _ in range(2):
        deployment = verify_harness_deployment(
            configuration, "Analyst", harness_arn=fake.harness_arn,
            endpoint_name=fake.endpoint_name, client=client,
        )
        assert deployment.harness_version == "1"
