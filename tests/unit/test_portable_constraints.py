from __future__ import annotations

import builtins
import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from contract4agents._portable_validation import is_portable_datetime
from contract4agents.codegen import generate_pydantic_models, generate_typescript_types, generate_zod_schemas
from contract4agents.compiler import build_artifacts
from contract4agents.ir import (
    CanonicalIR,
    FrozenMap,
    ListTypeRef,
    ParameterIR,
    TypeFieldIR,
    TypeIR,
    format_type_ref,
    parse_type_ref,
    semantic_id,
)
from contract4agents.materialization._types import (
    build_agent_input_types,
    build_parameter_model,
    build_pydantic_types,
    type_adapter_for,
)

ROOT = Path(__file__).resolve().parents[2]
ZOD_HARNESS = ROOT / "editors" / "vscode" / "test" / "execute-generated-zod.mjs"


def test_list_type_ref_round_trips_bounds_and_rejects_invalid_bounds() -> None:
    values = (
        ("list[Source](min_items=1,max_items=20)", "list[type:Source](min_items=1,max_items=20)"),
        ("list[Source](max_items=20)", "list[type:Source](max_items=20)"),
        ("list[Source](min_items=1)?", "list[type:Source](min_items=1)?"),
        ("list[list[Source](max_items=5)](min_items=1)?", "list[list[type:Source](max_items=5)](min_items=1)?"),
    )
    for source, expected in values:
        assert format_type_ref(parse_type_ref(source)) == expected

    assert parse_type_ref("list[Source](min_items=1,max_items=20)") == ListTypeRef(
        parse_type_ref("Source"), min_items=1, max_items=20
    )
    for source in (
        "list[Source](min_items=-1)",
        "list[Source](max_items=1.5)",
        "list[Source](min_items=2,max_items=1)",
        "list[Source](min_items=1,min_items=2)",
        "list[Source](minimum=1)",
        "map[string,string](min_items=1)",
        "Source(min_items=1)",
    ):
        with pytest.raises(ValueError):
            parse_type_ref(source)


def test_list_cardinality_is_exact_across_compiler_and_codegen() -> None:
    record = TypeIR(
        semantic_id("type", "Record"),
        "Record",
        (TypeFieldIR("values", parse_type_ref("list[string](min_items=1,max_items=2)")),),
    )
    ir = CanonicalIR.create(types=(record,))

    assert build_artifacts(ir).schemas["Record"]["properties"] == {
        "values": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 2,
        }
    }
    assert "values: Annotated[list[str], Field(min_length=1, max_length=2)]" in generate_pydantic_models(ir)
    assert "values: Array<string>;" in generate_typescript_types(ir)
    assert "values: z.array(z.string()).min(1).max(2)," in generate_zod_schemas(ir)


def test_native_adapter_schema_readback_preserves_list_cardinality() -> None:
    from typing import Annotated

    from pydantic import Field, TypeAdapter, create_model

    from tests.unit.test_google_adk_materialization import FakeGoogleADKSDK
    from tests.unit.test_materialization import FakeOpenAISDK
    from tests.unit.test_strands_materialization import FakeStrandsSDK

    bounded = Annotated[list[str], Field(min_length=1, max_length=2)]
    input_type = create_model("BoundedInput", items=(bounded, ...))
    output_type = create_model("BoundedOutput", items=(bounded, ...))
    output_adapter = TypeAdapter(output_type)

    openai = FakeOpenAISDK()
    openai_tool = openai.create_function_tool(
        name="bounded",
        description="Bounded input",
        implementation=lambda items: {"items": items},
        input_type=input_type,
        output_adapter=output_adapter,
        requires_approval=False,
    )
    openai_delegate = openai.create_delegate_tool(
        name="delegate",
        description="Bounded delegate",
        child=object(),
        input_type=input_type,
    )
    openai_agent = openai.create_agent(
        name="Bounded",
        instructions="Return bounded output.",
        model="test-model",
        model_options={},
        output_type=output_type,
        tools=(openai_tool, openai_delegate),
    )
    assert _property_schema(openai.describe_tool(openai_tool).input_schema, "items") == {
        "items": {"type": "string"},
        "maxItems": 2,
        "minItems": 1,
        "title": "Items",
        "type": "array",
    }
    assert _property_schema(openai.describe_tool(openai_delegate).input_schema, "items")["minItems"] == 1
    assert _property_schema(openai.describe(openai_agent).output_schema, "items")["maxItems"] == 2

    google = FakeGoogleADKSDK()
    google_tool = google.create_function_tool(
        native_name="bounded",
        description="Bounded input",
        implementation=lambda items: {"items": items},
        input_type=input_type,
        output_adapter=output_adapter,
        requires_approval=False,
    )
    google_delegate = google.create_delegate_tool(
        native_name="delegate",
        description="Bounded delegate",
        child=object(),
        input_type=input_type,
        output_adapter=output_adapter,
    )
    google_description = google.describe_tool(google_tool)
    google_delegate_description = google.describe_tool(google_delegate)
    assert _property_schema(google_description.input_schema, "items")["minItems"] == 1
    assert _property_schema(google_description.output_schema, "items")["maxItems"] == 2
    assert _property_schema(google_delegate_description.input_schema, "items")["maxItems"] == 2
    assert _property_schema(google_delegate_description.output_schema, "items")["minItems"] == 1

    strands = FakeStrandsSDK()
    strands_tool = strands.create_function_tool(
        native_name="bounded",
        description="Bounded input",
        implementation=lambda items: {"items": items},
        input_type=input_type,
        output_adapter=output_adapter,
    )
    strands_delegate = strands.create_delegate_tool(
        native_name="delegate",
        description="Bounded delegate",
        child=object(),
        input_type=input_type,
        output_adapter=output_adapter,
    )
    strands_description = strands.describe_tool(strands_tool)
    strands_delegate_description = strands.describe_tool(strands_delegate)
    assert _property_schema(strands_description.input_schema, "items")["minItems"] == 1
    assert _property_schema(strands_description.output_schema, "items")["maxItems"] == 2
    assert _property_schema(strands_delegate_description.input_schema, "items")["maxItems"] == 2
    assert _property_schema(strands_delegate_description.output_schema, "items")["minItems"] == 1


def test_openai_materialization_rejects_native_tool_that_drops_list_bound(tmp_path: Path) -> None:
    from contract4agents import materialize
    from contract4agents.materialization import MaterializationError, OpenAIMaterializationProvider
    from tests.unit.test_materialization import FakeOpenAISDK, _write_project

    _write_project(tmp_path)
    contract_path = tmp_path / "system.contract"
    source = contract_path.read_text()
    source = source.replace(
        "type Result:\n    value: string",
        "type Result:\n    values: list[string](min_items=1,max_items=2)",
    )
    source = source.replace(
        "tool records.lookup(query: string)",
        "tool records.lookup(query: list[string](min_items=1,max_items=2))",
    )
    contract_path.write_text(source)

    with pytest.raises(MaterializationError) as caught:
        materialize(
            tmp_path,
            "openai",
            "test",
            provider=OpenAIMaterializationProvider(FakeOpenAISDK(drift_tool_schema=True)),
        )

    assert "MAT408" in {issue.code for issue in caught.value.issues}


def test_google_adk_materialization_rejects_native_tool_that_drops_list_bound(tmp_path: Path) -> None:
    from dataclasses import replace

    from contract4agents.materialization import MaterializationError, RecordingMaterializationTraceSink
    from contract4agents.materialization._context import ContextRuntime
    from contract4agents.materialization._google_adk import (
        GoogleADKMaterializationProvider,
    )
    from tests.unit.test_google_adk_materialization import (
        FakeGoogleADKSDK,
        _provider_ir,
        _target_and_plan,
    )

    base = _provider_ir()
    bounded = "list[string](min_items=1,max_items=2)"
    answer = replace(
        base.types[semantic_id("type", "Answer")],
        fields=(TypeFieldIR("summary", parse_type_ref(bounded)),),
    )
    lookup = replace(
        base.types[semantic_id("type", "Lookup")],
        fields=(TypeFieldIR("query", parse_type_ref(bounded)),),
    )
    tool = replace(
        base.capabilities[semantic_id("tool", "records.lookup")],
        parameters=(ParameterIR("query", parse_type_ref(bounded)),),
    )
    child = replace(
        base.agents[semantic_id("agent", "Child")],
        parameters=(ParameterIR("query", parse_type_ref(bounded)),),
    )
    ir = CanonicalIR.create(
        types=(lookup, answer),
        capabilities=(tool,),
        agents=(base.agents[semantic_id("agent", "Parent")], child),
        grants=base.grants.values(),
        composition=base.composition.values(),
        controls=base.controls.values(),
    )
    target, plan = _target_and_plan(tmp_path, ir)
    output_types = build_pydantic_types(ir)
    lookup_id = semantic_id("tool", "records.lookup")
    implementations = FrozenMap(((lookup_id, lambda query: {"summary": query}),))
    context = ContextRuntime(ir, plan, implementations, output_types)

    with pytest.raises(MaterializationError) as caught:
        GoogleADKMaterializationProvider(
            FakeGoogleADKSDK(drop_list_bounds=True)
        ).build_graph(
            ir=ir,
            artifacts=build_artifacts(ir),
            target=target,
            plan=plan,
            implementations=implementations,
            input_types=build_agent_input_types(ir, output_types),
            output_types=output_types,
            context_runtime=context,
            environment=None,
            materialization_trace_sink=RecordingMaterializationTraceSink(),
        )

    assert "MAT428" in {issue.code for issue in caught.value.issues}


def test_strands_materialization_rejects_native_tool_that_drops_list_bound(tmp_path: Path) -> None:
    from contract4agents import materialize
    from contract4agents.materialization import MaterializationError
    from contract4agents.materialization._strands import (
        StrandsMaterializationProvider,
    )
    from tests.unit.test_strands_materialization import FakeStrandsSDK, _write_project

    _write_project(tmp_path)
    contract_path = tmp_path / "system.contract"
    source = contract_path.read_text()
    source = source.replace(
        "type Result:\n    value: string",
        "type Result:\n    values: list[string](min_items=1,max_items=2)",
    )
    source = source.replace(
        "tool records.lookup(query: string)",
        "tool records.lookup(query: list[string](min_items=1,max_items=2))",
    )
    contract_path.write_text(source)

    with pytest.raises(MaterializationError) as caught:
        materialize(
            tmp_path,
            "strands",
            "test",
            provider=StrandsMaterializationProvider(
                FakeStrandsSDK(drop_list_bounds=True)
            ),
        )

    assert "MAT457" in {issue.code for issue in caught.value.issues}


def test_list_default_validation_checks_container_cardinality_before_items(tmp_path: Path) -> None:
    from contract4agents.parser import parse_project
    from contract4agents.semantics import analyze_project

    source = tmp_path / "types.contract"
    template = """\
type Item:
    value: string(min_length=1)

type Container:
    values: list[Item](min_items=1,max_items=2) = {default}
"""
    defaults = (
        ("[]", False),
        ('[{"value":"a"}]', True),
        ('[{"value":""}]', False),
        ('[{"value":"a"},{"value":"b"}]', True),
        ('[{"value":"a"},{"value":"a"},{"value":"a"}]', False),
        ('"wrong"', False),
    )
    for default, valid in defaults:
        source.write_text(template.format(default=default))
        diagnostics = analyze_project(parse_project(tmp_path)).diagnostics
        assert ([item.code for item in diagnostics] == []) is valid, default


def test_list_default_validation_checks_bounds_for_unconstrained_items(tmp_path: Path) -> None:
    from contract4agents.parser import parse_project
    from contract4agents.semantics import analyze_project

    source = tmp_path / "types.contract"
    template = "type Container:\n    values: list[string](min_items=1,max_items=2) = {default}\n"
    for default, valid in (
        ("[]", False),
        ('["one"]', True),
        ('["one", "two"]', True),
        ('["one", "two", "three"]', False),
        ('"wrong"', False),
    ):
        source.write_text(template.format(default=default))
        diagnostics = analyze_project(parse_project(tmp_path)).diagnostics
        assert ([item.code for item in diagnostics] == []) is valid, default


def test_portable_datetime_profile_is_strict_and_preserves_aware_values() -> None:
    accepted = (
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00+05:30",
        "2026-01-01T00:00:00.123456Z",
    )
    rejected = (
        "2026-01-01T00:00:00",
        "2026-01-01 00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-01-01T00:00:00+24:00",
        "2026-01-01T00:00:00+01:60",
    )
    assert all(is_portable_datetime(value) for value in accepted)
    assert not any(is_portable_datetime(value) for value in rejected)
    assert not is_portable_datetime(datetime(2026, 1, 1))
    assert is_portable_datetime(datetime(2026, 1, 1, tzinfo=UTC))


def test_semantic_datetime_defaults_use_the_portable_profile(tmp_path: Path) -> None:
    from contract4agents.parser import parse_project
    from contract4agents.semantics import analyze_project

    source = tmp_path / "types.contract"
    template = "type Record:\n    when: datetime = {default}\n"
    for default, valid in (
        ('"2026-01-01T00:00:00Z"', True),
        ('"2026-01-01 00:00:00Z"', False),
        ('"2026-01-01T00:00:00"', False),
    ):
        source.write_text(template.format(default=default))
        diagnostics = analyze_project(parse_project(tmp_path)).diagnostics
        assert ([item.code for item in diagnostics] == []) is valid, default


def test_generated_and_materialized_python_types_enforce_portable_corpus() -> None:
    ir = _corpus_ir()
    artifacts = build_artifacts(ir)
    generated_source = generate_pydantic_models(ir)
    namespace: dict[str, Any] = {"__name__": "generated_portable_models"}
    exec(compile(generated_source, "<generated>", "exec"), namespace)
    generated_type = namespace["Record"]
    materialized_type = build_pydantic_types(ir)["Record"]
    checker = FormatChecker()
    checker.checks("date-time")(is_portable_datetime)
    validator = Draft202012Validator(artifacts.schemas["Record"], format_checker=checker)

    for entry in _corpus():
        value = entry["value"]
        expected = entry["valid"]
        schema_valid = _schema_valid(validator, value)
        assert schema_valid is expected, entry["name"]
        for model in (generated_type, materialized_type):
            if expected:
                model(**value)
            else:
                with pytest.raises(ValidationError):
                    model(**value)


def test_parameter_models_and_primitive_adapters_reject_json_type_coercions() -> None:
    ir = _corpus_ir()
    output_types = build_pydantic_types(ir)
    parameter_type = build_parameter_model(
        "PortableParameters",
        (
            ParameterIR("count", parse_type_ref("integer")),
            ParameterIR("label", parse_type_ref("string")),
            ParameterIR("active", parse_type_ref("boolean")),
        ),
        output_types,
    )
    assert parameter_type is not None
    for payload in (
        {"count": "1", "label": "ok", "active": True},
        {"count": 1, "label": 1, "active": True},
        {"count": 1, "label": "ok", "active": 1},
    ):
        with pytest.raises(ValidationError):
            parameter_type(**payload)

    for type_name, invalid in (("integer", "1"), ("string", 1), ("boolean", 1)):
        adapter = type_adapter_for(parse_type_ref(type_name), output_types)
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)

    datetime_adapter = type_adapter_for(parse_type_ref("datetime"), output_types)
    assert datetime_adapter.validate_python("2026-01-01T00:00:00Z") == datetime(
        2026, 1, 1, tzinfo=UTC
    )


def test_generated_python_is_self_contained_when_contract4agents_import_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir = CanonicalIR.create(
        types=(
            TypeIR(
                semantic_id("type", "Record"),
                "Record",
                (TypeFieldIR("when", parse_type_ref("datetime")),),
            ),
        )
    )
    source = generate_pydantic_models(ir)
    assert "from contract4agents" not in source
    original_import = builtins.__import__

    def blocked_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "contract4agents" or name.startswith("contract4agents."):
            raise ModuleNotFoundError("contract4agents is blocked for this test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    namespace: dict[str, Any] = {"__name__": "generated_standalone_models"}
    exec(compile(source, "<generated-standalone>", "exec"), namespace)

    record_type = namespace["Record"]
    value = record_type(when="2026-01-01T00:00:00Z")
    assert value.when == datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        record_type(when="2026-01-01T00:00:00")


def test_generated_zod_executes_the_same_portable_corpus(tmp_path: Path) -> None:
    ir = _corpus_ir()
    schema_path = tmp_path / "schemas.ts"
    corpus_path = tmp_path / "corpus.json"
    schema_path.write_text(generate_zod_schemas(ir))
    corpus_path.write_text(json.dumps(_corpus(), ensure_ascii=False))
    result = subprocess.run(
        ["node", str(ZOD_HARNESS), str(schema_path), "Record", str(corpus_path)],
        cwd=ROOT / "editors" / "vscode",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _corpus_ir() -> CanonicalIR:
    child = TypeIR(
        semantic_id("type", "Child"),
        "Child",
        (TypeFieldIR("label", parse_type_ref("string(min_length=1,max_length=3)")),),
    )
    record = TypeIR(
        semantic_id("type", "Record"),
        "Record",
        (
            TypeFieldIR("ascii", parse_type_ref("string(min_length=1,max_length=2)")),
            TypeFieldIR("emoji", parse_type_ref("string(min_length=1,max_length=1)")),
            TypeFieldIR("combining", parse_type_ref("string(min_length=2,max_length=2)")),
            TypeFieldIR("empty", parse_type_ref("string(max_length=0)")),
            TypeFieldIR("children", parse_type_ref("list[Child](min_items=1,max_items=2)")),
            TypeFieldIR("count", parse_type_ref("integer")),
            TypeFieldIR("ratio", parse_type_ref("float")),
            TypeFieldIR("active", parse_type_ref("boolean")),
            TypeFieldIR("metadata", parse_type_ref("map[string,integer]")),
            TypeFieldIR("when", parse_type_ref("datetime")),
        ),
    )
    return CanonicalIR.create(types=(child, record))


def _corpus() -> list[dict[str, object]]:
    base: dict[str, object] = {
        "ascii": "ok",
        "emoji": "🍩",
        "combining": "e\u0301",
        "empty": "",
        "children": [{"label": "one"}],
        "count": 1,
        "ratio": 1.5,
        "active": True,
        "metadata": {"attempts": 2},
        "when": "2026-01-01T00:00:00Z",
    }
    return [
        {"name": "ascii", "value": base, "valid": True},
        {"name": "emoji", "value": {**base, "emoji": "🍩"}, "valid": True},
        {"name": "combining code points", "value": base, "valid": True},
        {"name": "empty string", "value": base, "valid": True},
        {"name": "nested constrained strings", "value": base, "valid": True},
        {"name": "offset datetime", "value": {**base, "when": "2026-01-01T00:00:00+05:30"}, "valid": True},
        {"name": "fractional datetime", "value": {**base, "when": "2026-01-01T00:00:00.123Z"}, "valid": True},
        {"name": "too few items", "value": {**base, "children": []}, "valid": False},
        {"name": "too many items", "value": {**base, "children": [{"label": "one"}] * 3}, "valid": False},
        {"name": "invalid nested member", "value": {**base, "children": [{"label": "toolong"}]}, "valid": False},
        {"name": "string to integer", "value": {**base, "count": "1"}, "valid": False},
        {"name": "boolean to integer", "value": {**base, "count": True}, "valid": False},
        {"name": "integer to string", "value": {**base, "ascii": 1}, "valid": False},
        {"name": "string to float", "value": {**base, "ratio": "1.5"}, "valid": False},
        {"name": "boolean to float", "value": {**base, "ratio": True}, "valid": False},
        {"name": "integer to boolean", "value": {**base, "active": 1}, "valid": False},
        {"name": "string to boolean", "value": {**base, "active": "true"}, "valid": False},
        {"name": "string to nested integer", "value": {**base, "metadata": {"attempts": "2"}}, "valid": False},
        {"name": "naive datetime", "value": {**base, "when": "2026-01-01T00:00:00"}, "valid": False},
        {"name": "space datetime", "value": {**base, "when": "2026-01-01 00:00:00Z"}, "valid": False},
        {"name": "impossible date", "value": {**base, "when": "2026-02-30T00:00:00Z"}, "valid": False},
        {"name": "invalid offset", "value": {**base, "when": "2026-01-01T00:00:00+24:00"}, "valid": False},
    ]


def _schema_valid(validator: Draft202012Validator, value: object) -> bool:
    return not list(validator.iter_errors(value))


def _property_schema(schema: Mapping[str, object], name: str) -> dict[str, object]:
    properties = cast(dict[str, object], schema["properties"])
    return cast(dict[str, object], properties[name])
