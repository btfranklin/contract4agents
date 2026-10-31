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


def test_strict_request_projects_all_fields_but_keeps_host_validation_and_nullable_types(tmp_path: Path) -> None:
    root = write_project(tmp_path, result_declaration="type Result:\n    answer: string\n    note: string?")
    target = root / "contract4agents.targets.toml"
    target.write_text(
        target.read_text()
        + '\noutput_mode = "schema"\n'
        + 'additional_model_request_fields = { text = { format = { strict = true } } }\n'
    )

    agent = generate_harness_configuration(root, target="agentcore_harness", profile="test").agents["Analyst"]
    request = agent.request_payload()
    model = json_object("model", request["model"])
    bedrock = json_object("Bedrock model", model["bedrockModelConfig"])
    additional = json_object("additional parameters", bedrock["additionalParams"])
    native_fields = json_object("model request fields", additional["additionalModelRequestFields"])
    assert json_object("text request", native_fields["text"])["format"] == {"strict": True}
    output_config = json_object("output config", additional["outputConfig"])
    text_format = json_object("text format", output_config["textFormat"])
    structure = json_object("structure", text_format["structure"])
    encoded = json_object("schema", structure["jsonSchema"])
    provider_schema = json.loads(str(encoded["schema"]))
    assert provider_schema["required"] == ["answer", "note"]
    note = json_object("note schema", provider_schema["properties"]["note"])
    assert note["anyOf"] == [{"type": "string"}, {"type": "null"}]
    host_value = agent.output_adapter.validate_python({"answer": "ready"}, strict=True)
    assert json_object("host output", agent.output_adapter.dump_python(host_value)) == {
        "answer": "ready", "note": None,
    }
    with pytest.raises(ValidationError):
        agent.output_adapter.validate_python({"answer": 4, "note": None}, strict=True)


def test_schema_output_keeps_repeated_nonrecursive_references() -> None:
    schema: dict[str, object] = {
        "$defs": {"Item": {"type": "string", "enum": ["ready", "unknown"]}},
        "type": "object", "additionalProperties": False,
        "properties": {"left": {"$ref": "#/$defs/Item"}, "right": {"$ref": "#/$defs/Item"}},
        "required": ["left", "right"],
    }
    assert bedrock_output_schema(schema) == schema


def test_strict_schema_projection_requires_fields_without_changing_nullability_or_defaults() -> None:
    schema: dict[str, object] = {
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        "type": "object",
        "properties": {
            "item": {"$ref": "#/$defs/Item"},
            "items": {"type": "array", "items": {
                "type": "object", "properties": {"code": {"type": "string"}},
                "additionalProperties": False,
            }},
            "choice": {"anyOf": [
                {"type": "object", "properties": {"left": {"type": "string"}}, "additionalProperties": False},
                {"type": "object", "properties": {"right": {"type": "integer"}}, "additionalProperties": False},
            ]},
        },
        "required": ["item"],
        "additionalProperties": False,
    }
    original = copy.deepcopy(schema)
    assert bedrock_output_schema(schema) == original

    projected = bedrock_output_schema(schema, strict=True)

    assert projected["required"] == ["item", "items", "choice"]
    item = json_object("item definition", json_object("$defs", projected["$defs"])["Item"])
    assert item["required"] == ["name", "note"]
    original_defs = json_object("original definitions", schema["$defs"])
    original_item = json_object("original item", original_defs["Item"])
    original_properties = json_object("original properties", original_item["properties"])
    item_properties = json_object("item properties", item["properties"])
    assert json_object("note schema", item_properties["note"]) == original_properties["note"]
    items = json_object("items schema", json_object("properties", projected["properties"])["items"])
    assert json_object("array item", items["items"])["required"] == ["code"]
    choice = json_object("choice schema", json_object("properties", projected["properties"])["choice"])
    assert [
        json_object("choice branch", branch)["required"]
        for branch in json_array("choice branches", choice["anyOf"])
    ] == [["left"], ["right"]]
    assert schema == original


def test_strict_schema_projection_rejects_open_objects_and_non_object_roots() -> None:
    with pytest.raises(ValueError, match="closed objects"):
        bedrock_output_schema({
            "type": "object", "properties": {"value": {"type": "object", "properties": {}}},
            "additionalProperties": False,
        }, strict=True)
    with pytest.raises(ValueError, match="object at the root"):
        bedrock_output_schema({"type": "string"}, strict=True)
    with pytest.raises(ValueError, match="does not support oneOf"):
        bedrock_output_schema({
            "type": "object", "properties": {"value": {"oneOf": [{"type": "string"}]}},
            "additionalProperties": False,
        }, strict=True)


def test_strict_schema_projection_closes_object_in_type_union() -> None:
    projected = bedrock_output_schema({
        "type": "object", "properties": {
            "value": {
                "type": ["object", "null"], "properties": {"name": {"type": "string"}},
                "additionalProperties": False,
            },
        }, "additionalProperties": False,
    }, strict=True)
    value = json_object("value schema", json_object("properties", projected["properties"])["value"])
    assert value["required"] == ["name"]
