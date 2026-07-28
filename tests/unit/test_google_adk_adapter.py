from __future__ import annotations

from pathlib import Path

import pytest

from contract4agents.adapters.google_adk import (
    google_adk_planner_capabilities,
    google_adk_target_binding_validator,
    google_adk_target_profile_validator,
)
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    CapabilityIR,
    ControlIR,
    GrantIR,
    ParameterIR,
    TypeFieldIR,
    TypeIR,
    parse_type_ref,
    semantic_id,
)
from contract4agents.planning import PlanningError, plan_materialization
from contract4agents.target_bindings import (
    AgentProfile,
    BindingEntry,
    TargetBinding,
    TargetBindings,
    TargetProfile,
)


def test_google_search_binding_is_schema_aware_and_emulated(tmp_path: Path) -> None:
    ir = _search_ir()
    bindings = _bindings(
        tmp_path,
        BindingEntry(
            {
                "provider": "google_adk",
                "tool": "google_search",
                "model": "gemini-2.5-flash",
            }
        ),
    )

    plan = plan_materialization(
        ir,
        bindings,
        target="google_adk",
        profile="test",
        capabilities=google_adk_planner_capabilities(),
    )

    search = plan.bindings[semantic_id("tool", "web.search")]
    assert search.outcome == "emulated"
    assert search.execution == "provider_hosted"
    output = plan.controls[
        semantic_id("control", "Researcher", "output_conformance")
    ]
    assert output.outcome == "emulated"
    assert any(
        "display Google Search suggestions" in obligation.description
        for obligation in plan.host_obligations
    )


@pytest.mark.parametrize(
    ("side_effect", "query_type", "output_field"),
    [
        (True, "string", "results"),
        (False, "integer", "results"),
        (False, "string", "links"),
    ],
)
def test_google_search_binding_rejects_false_structural_equivalence(
    tmp_path: Path,
    side_effect: bool,
    query_type: str,
    output_field: str,
) -> None:
    ir = _search_ir(
        side_effect=side_effect,
        query_type=query_type,
        output_field=output_field,
    )
    bindings = _bindings(
        tmp_path,
        BindingEntry(
            {
                "provider": "google_adk",
                "tool": "google_search",
                "model": "gemini-2.5-flash",
            }
        ),
    )

    with pytest.raises(PlanningError) as caught:
        plan_materialization(
            ir,
            bindings,
            target="google_adk",
            profile="test",
            capabilities=google_adk_planner_capabilities(),
        )

    assert {issue.code for issue in caught.value.issues} == {"PLN009"}


@pytest.mark.parametrize(
    "entry",
    [
        BindingEntry(
            {
                "provider": "google_adk",
                "tool": "google_search",
                "model": "gemini-3-flash",
            }
        ),
        BindingEntry(
            {
                "provider": "google_adk",
                "tool": "google_search",
                "model": "gemini-2.5-flash",
                "temperature": 0,
            }
        ),
        BindingEntry({"provider": "google_adk", "tool": "other"}),
        BindingEntry({"mcp": "search-server"}),
    ],
)
def test_google_search_binding_requires_the_one_supported_locator_shape(
    entry: BindingEntry,
) -> None:
    diagnostics = google_adk_target_binding_validator(
        "google_adk",
        "tools",
        "web.search",
        entry,
    )

    assert [item.code for item in diagnostics] == ["TGT111"]


def test_google_adk_output_support_depends_on_model_tools_and_factory(
    tmp_path: Path,
) -> None:
    python_binding = BindingEntry({"python": "app:search"})
    no_tools = _plain_ir(with_tool=False)
    gemini_two_with_tools = _plain_ir(with_tool=True)
    gemini_three_with_tools = _plain_ir(with_tool=True)

    no_tools_plan = plan_materialization(
        no_tools,
        _bindings(tmp_path, None, model="gemini-2.5-flash"),
        target="google_adk",
        profile="test",
        capabilities=google_adk_planner_capabilities(),
    )
    gemini_two_plan = plan_materialization(
        gemini_two_with_tools,
        _bindings(tmp_path, python_binding, model="gemini-2.5-flash"),
        target="google_adk",
        profile="test",
        capabilities=google_adk_planner_capabilities(),
    )
    gemini_three_plan = plan_materialization(
        gemini_three_with_tools,
        _bindings(tmp_path, python_binding, model="gemini-3-flash"),
        target="google_adk",
        profile="test",
        capabilities=google_adk_planner_capabilities(),
    )

    control_id = semantic_id("control", "Researcher", "output_conformance")
    assert no_tools_plan.controls[control_id].outcome == "exact"
    assert gemini_two_plan.controls[control_id].outcome == "emulated"
    assert gemini_three_plan.controls[control_id].outcome == "exact"


def test_google_adk_factory_forces_emulated_output_and_allows_non_gemini(
    tmp_path: Path,
) -> None:
    ir = _plain_ir(with_tool=False)
    target = TargetBinding(
        adapter="google_adk",
        profiles={
            "test": TargetProfile(
                default_model="claude-provider-model",
                options={"model_factory": "app:create_model"},
            )
        },
    )
    bindings = TargetBindings(
        path=tmp_path / "contract4agents.targets.toml",
        targets={"google_adk": target},
    )

    assert google_adk_target_profile_validator(
        ir,
        "google_adk",
        target,
        tmp_path,
    ) == ()
    plan = plan_materialization(
        ir,
        bindings,
        target="google_adk",
        profile="test",
        capabilities=google_adk_planner_capabilities(),
    )

    control_id = semantic_id("control", "Researcher", "output_conformance")
    assert plan.controls[control_id].outcome == "emulated"


def test_google_adk_profile_requires_gemini_without_factory(tmp_path: Path) -> None:
    ir = _plain_ir(with_tool=False)
    target = TargetBinding(
        adapter="google_adk",
        profiles={
            "test": TargetProfile(
                default_model="claude-provider-model",
                agents={
                    "Researcher": AgentProfile(model="other-provider-model")
                },
            )
        },
    )

    diagnostics = google_adk_target_profile_validator(
        ir,
        "google_adk",
        target,
        tmp_path,
    )

    assert [item.code for item in diagnostics] == ["TGT116"]
    assert "other-provider-model" in diagnostics[0].message


def _search_ir(
    *,
    side_effect: bool = False,
    query_type: str = "string",
    output_field: str = "results",
) -> CanonicalIR:
    result = TypeIR(
        semantic_id("type", "SearchResult"),
        "SearchResult",
        (
            TypeFieldIR("title", parse_type_ref("string")),
            TypeFieldIR("url", parse_type_ref("string")),
            TypeFieldIR("snippet", parse_type_ref("string")),
        ),
    )
    response = TypeIR(
        semantic_id("type", "SearchResponse"),
        "SearchResponse",
        (
            TypeFieldIR(
                output_field,
                parse_type_ref("list[SearchResult]"),
            ),
        ),
    )
    tool = CapabilityIR(
        semantic_id("tool", "web.search"),
        "web.search",
        "tool",
        (ParameterIR("query", parse_type_ref(query_type)),),
        parse_type_ref("SearchResponse"),
        "Search the public web.",
        side_effect=side_effect,
    )
    return _agent_ir(types=(result, response), tool=tool)


def _plain_ir(*, with_tool: bool) -> CanonicalIR:
    answer = TypeIR(
        semantic_id("type", "Answer"),
        "Answer",
        (TypeFieldIR("summary", parse_type_ref("string")),),
    )
    tool = (
        CapabilityIR(
            semantic_id("tool", "records.lookup"),
            "records.lookup",
            "tool",
            (ParameterIR("query", parse_type_ref("string")),),
            parse_type_ref("Answer"),
            "Look up records.",
            side_effect=False,
        )
        if with_tool
        else None
    )
    return _agent_ir(types=(answer,), tool=tool)


def _agent_ir(
    *,
    types: tuple[TypeIR, ...],
    tool: CapabilityIR | None,
) -> CanonicalIR:
    agent_id = semantic_id("agent", "Researcher")
    grant = (
        GrantIR(
            semantic_id("grant", "Researcher", tool.name),
            agent_id,
            tool.id,
            "enabled",
            "automatic",
        )
        if tool is not None
        else None
    )
    agent = AgentIR(
        agent_id,
        "Researcher",
        (),
        parse_type_ref(types[-1].name),
        "Produce a researched answer.",
        grant_ids=(grant.id,) if grant is not None else (),
    )
    output_control = ControlIR(
        semantic_id("control", "Researcher", "output_conformance"),
        "output_conformance",
        agent_id,
        "high",
        True,
        ("adapter", "host"),
        "adapter",
        derived_from=agent_id,
        expected_evidence=("output.accepted", "output.schema_failed"),
    )
    return CanonicalIR.create(
        types=types,
        capabilities=(tool,) if tool is not None else (),
        agents=(agent,),
        grants=(grant,) if grant is not None else (),
        controls=(output_control,),
    )


def _bindings(
    root: Path,
    tool: BindingEntry | None,
    *,
    model: str = "gemini-3-flash",
) -> TargetBindings:
    target = TargetBinding(
        adapter="google_adk",
        tools={"web.search": tool, "records.lookup": tool} if tool else {},
        profiles={"test": TargetProfile(default_model=model)},
    )
    return TargetBindings(
        path=root / "contract4agents.targets.toml",
        targets={"google_adk": target},
    )
