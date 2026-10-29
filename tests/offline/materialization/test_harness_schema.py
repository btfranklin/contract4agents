from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from botocore.session import get_session  # type: ignore[import-untyped]
from botocore.validate import validate_parameters  # type: ignore[import-untyped]
from pydantic import ValidationError

from contract4agents._strict_json import json_array, json_object
from contract4agents.adapters.agentcore_harness import generate_harness_configuration, verify_harness_deployment
from contract4agents.materialization._harness_schema import bedrock_output_schema
from tests.support.agentcore_harness import FakeHarnessControlClient, write_project


def test_schema_output_uses_top_level_bedrock_configuration_and_keeps_strict_contract(tmp_path: Path) -> None:
    root = write_project(tmp_path, result_declaration=(
        'enum Status:\n    "ready"\n    "unknown"\n\n'
        'type Item:\n    label: string(min_length=2,max_length=12)\n    status: Status\n\n'
        'type Result:\n    items: list[Item](min_items=2,max_items=4)\n'
        '    score: integer(minimum=0,maximum=10)\n    maximum: string\n'
    ))
    target = root / "contract4agents.targets.toml"
    target.write_text(target.read_text() + '\noutput_mode = "schema"\nadditional_model_request_fields = { seed = 1 }\n')
    configuration = generate_harness_configuration(root, target="agentcore_harness", profile="test")
    agent = configuration.agents["Analyst"]
    request = agent.request_payload()
    model = json_object("model", request["model"])
    bedrock = json_object("bedrock", model["bedrockModelConfig"])
    extra = json_object("additional parameters", bedrock["additionalParams"])
    output_config = json_object("output config", extra["outputConfig"])
    format_config = json_object("text format", output_config["textFormat"])
    structure = json_object("structure", format_config["structure"])
    encoded = json_object("schema", structure["jsonSchema"])
    provider_schema = json.loads(str(encoded["schema"]))
    assert extra["additionalModelRequestFields"] == {"seed": 1}
    assert format_config["type"] == "json_schema"
    assert agent.output_mode == "schema" and agent.result_tool_name is None
    assert len(json_array("tools", request["tools"])) == len(agent.tools) == 1
    full_schema = agent.output_adapter.json_schema()
    assert full_schema["properties"]["score"]["minimum"] == 0
    assert provider_schema["properties"]["score"] == {"title": "Score", "type": "integer"}
    assert "maximum" in provider_schema["properties"]
    assert provider_schema["properties"]["items"]["minItems"] == 1
    assert "$defs" in provider_schema
    assert '"maxLength":12' in str(request["systemPrompt"])
    # Native generation can permit a missing bound; the unchanged host schema must reject it.
    invalid = {"items": [{"label": "x", "status": "ready"}], "score": 11, "maximum": "field name"}
    jsonschema.validate(invalid, provider_schema)
    with pytest.raises(ValidationError):
        agent.output_adapter.validate_python(invalid, strict=True)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**invalid, "items": [{"label": "ok", "status": "invented"}]}, provider_schema)
    control_service = get_session().get_service_model("bedrock-agentcore-control")
    create_shape = control_service.operation_model("CreateHarness").input_shape
    validate_parameters(request, create_shape)
    converse_shape = get_session().get_service_model("bedrock-runtime").operation_model("ConverseStream").input_shape
    validate_parameters({"modelId": bedrock["modelId"], "outputConfig": output_config}, converse_shape)
    control = FakeHarnessControlClient(configuration)
    control.harness_mutation["model"] = {"bedrockModelConfig": {**bedrock, "additionalParams": {}}}
    with pytest.raises(ValueError, match="configuration"):
        verify_harness_deployment(
            configuration, "Analyst", harness_arn=control.harness_arn,
            endpoint_name=control.endpoint_name, client=control,
        )


@pytest.mark.parametrize(("optional", "nullable", "error"), [(13, False, "optional"), (17, True, "union")])
def test_schema_output_rejects_provider_quotas_without_changing_required_fields(
    optional: int, nullable: bool, error: str,
) -> None:
    properties = {
        f"field_{index}": {"anyOf": [{"type": "string"}, {"type": "null"}]} if nullable else {"type": "string"}
        for index in range(optional)
    }
    schema: dict[str, object] = {
        "type": "object", "properties": properties, "additionalProperties": False,
        "required": list(properties) if nullable else [],
    }
    original = copy.deepcopy(schema)
    with pytest.raises(ValueError, match=error):
        bedrock_output_schema(schema)
    assert schema == original


def test_schema_output_does_not_close_an_open_contract_object() -> None:
    with pytest.raises(ValueError, match="closed objects"):
        bedrock_output_schema({"type": "object", "additionalProperties": {"type": "string"}})


def test_schema_output_rejects_recursive_contracts_before_deployment(tmp_path: Path) -> None:
    root = write_project(tmp_path, result_declaration='type Result:\n    children: list[Result]')
    target = root / "contract4agents.targets.toml"
    target.write_text(target.read_text() + '\noutput_mode = "schema"\n')
    with pytest.raises(ValueError, match="recursive schemas"):
        generate_harness_configuration(root, target="agentcore_harness", profile="test")


def test_schema_output_keeps_repeated_nonrecursive_references() -> None:
    schema: dict[str, object] = {
        "$defs": {"Item": {"type": "string", "enum": ["ready", "unknown"]}},
        "type": "object", "additionalProperties": False,
        "properties": {"left": {"$ref": "#/$defs/Item"}, "right": {"$ref": "#/$defs/Item"}},
        "required": ["left", "right"],
    }
    assert bedrock_output_schema(schema) == schema
