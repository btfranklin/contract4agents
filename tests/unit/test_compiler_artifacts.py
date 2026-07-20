from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from contract4agents.compiler import artifact_digests, build_artifacts
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    ControlIR,
    FrozenMap,
    GuidanceIR,
    ParameterIR,
    TypeFieldIR,
    TypeIR,
    parse_type_ref,
    semantic_id,
)
from contract4agents.materialization import MaterializationError
from contract4agents.materialization._types import (
    build_parameter_model,
    build_pydantic_types,
    output_type_for,
)


def test_compiler_builds_nested_schema_docs_instructions_and_stable_digests() -> None:
    detail = TypeIR(
        semantic_id("type", "Detail"),
        "Detail",
        (
            TypeFieldIR("at", parse_type_ref("datetime")),
            TypeFieldIR("labels", parse_type_ref("map[string, string]"), True, FrozenMap({"a": "b"})),
        ),
    )
    result = TypeIR(
        semantic_id("type", "Result"),
        "Result",
        (
            TypeFieldIR("detail", parse_type_ref("Detail")),
            TypeFieldIR("items", parse_type_ref("list[Detail?]")),
            TypeFieldIR("score", parse_type_ref("float?")),
        ),
    )
    agent_id = semantic_id("agent", "Worker")
    control = ControlIR(
        semantic_id("control", "Worker", "be_safe"),
        "be_safe",
        agent_id,
        "high",
        True,
        ("model", "evaluator"),
        "runtime",
        requirement="trace.not_called(danger)",
    )
    agent = AgentIR(
        agent_id,
        "Worker",
        (ParameterIR("query", parse_type_ref("string")),),
        parse_type_ref("Result"),
        "Return a result.",
        description="A careful worker.",
        guidance=(GuidanceIR("Do the safe thing.", ("model",)), GuidanceIR("hidden", ("host",))),
    )
    ir = CanonicalIR.create(types=(detail, result), agents=(agent,), controls=(control,))

    artifacts = build_artifacts(ir)

    schema = artifacts.schemas["Result"]
    assert schema["$defs"]["Detail"]["properties"]["at"] == {"type": "string", "format": "date-time"}  # type: ignore[index]
    assert schema["properties"]["score"]["anyOf"][-1] == {"type": "null"}  # type: ignore[index]
    assert "Do the safe thing." in artifacts.instructions["Worker"]
    assert "hidden" not in artifacts.instructions["Worker"]
    assert "trace.not_called(danger)" in artifacts.instructions["Worker"]
    assert "None." in artifacts.docs[Path("agents/Worker.md")]
    digests = artifact_digests(artifacts)
    assert all(value.startswith("sha256:") for value in digests.values())
    assert digests == artifact_digests(artifacts)


def test_materialized_pydantic_types_cover_collections_defaults_and_parameters() -> None:
    child = TypeIR(
        semantic_id("type", "Child"),
        "Child",
        (TypeFieldIR("value", parse_type_ref("integer")),),
    )
    result = TypeIR(
        semantic_id("type", "Result"),
        "Result",
        (
            TypeFieldIR("child", parse_type_ref("Child")),
            TypeFieldIR("names", parse_type_ref("list[string]"), True, ("a", "b")),
            TypeFieldIR("scores", parse_type_ref("map[string, float]"), True, FrozenMap({"x": 1.5})),
            TypeFieldIR("when", parse_type_ref("datetime?")),
            TypeFieldIR("enabled", parse_type_ref("boolean"), True, True),
        ),
    )
    output_types = build_pydantic_types(CanonicalIR.create(types=(child, result)))
    result_type = cast(type, output_types["Result"])

    instance = result_type(child={"value": 3})
    assert instance.child.value == 3
    assert instance.names == ["a", "b"]
    assert instance.scores == {"x": 1.5}
    assert instance.when is None
    assert instance.enabled is True
    with pytest.raises(ValidationError):
        result_type(child={"value": "wrong"})

    parameter_type = cast(
        type,
        build_parameter_model(
            "Input",
            (
                ParameterIR("result", parse_type_ref("Result")),
                ParameterIR("at", parse_type_ref("datetime?"), required=False),
                ParameterIR("limit", parse_type_ref("integer"), required=False, has_default=True, default=2),
                ParameterIR(
                    "flags",
                    parse_type_ref("list[boolean]"),
                    required=False,
                    has_default=True,
                    default=(True,),
                ),
            ),
            output_types,
        ),
    )
    parameters = parameter_type(result={"child": {"value": 1}}, at=datetime(2026, 1, 1))
    assert parameters.limit == 2
    assert parameters.flags == [True]
    assert build_parameter_model("Empty", (), output_types) is None
    assert output_type_for(parse_type_ref("Result"), output_types) is result_type
    with pytest.raises(MaterializationError, match="MAT204"):
        output_type_for(parse_type_ref("string"), output_types)
