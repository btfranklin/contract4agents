from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from contract4agents.adapters._agentcore_harness import (
    AgentCoreHarnessMappingResolver,
    agentcore_harness_planner_capabilities,
    agentcore_harness_target_binding_validator,
    agentcore_harness_target_profile_validator,
)
from contract4agents.adapters._native_names import native_name
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    CapabilityIR,
    CompositionEdgeIR,
    ControlIR,
    FrozenMap,
    GrantIR,
    IsolationProfileIR,
    ParameterIR,
    TypeFieldIR,
    TypeIR,
    parse_type_ref,
    semantic_id,
)
from contract4agents.planning import MappingSupport, PlanningError, plan_materialization
from contract4agents.target_bindings import (
    AgentProfile,
    BindingEntry,
    TargetBinding,
    TargetBindings,
    TargetProfile,
)

_ROLE_ARN = "arn:aws:iam::123456789012:role/AgentRole"


def test_required_custom_adapter_control_cannot_claim_output_validation_enforces_it() -> None:
    ir = _ir(with_tool=True)
    agent_id = semantic_id("agent", "Researcher")
    control = ControlIR(
        semantic_id("control", "Researcher", "custom_gate"),
        "custom_gate",
        agent_id,
        "high",
        True,
        ("adapter", "host"),
        "adapter",
        requirement="custom.provider_gate",
    )
    ir = replace(ir, controls=FrozenMap((*ir.controls.items(), (control.id, control))))
    with pytest.raises(PlanningError):
        plan_materialization(
            ir,
            _bindings(ir, profile_options={"execution_role_arn": _ROLE_ARN}),
            target="agentcore_harness",
            profile="test",
            capabilities=agentcore_harness_planner_capabilities(),
        )


def test_harness_accepts_only_python_tool_bindings() -> None:
    assert (
        agentcore_harness_target_binding_validator(
            "agentcore_harness",
            "tools",
            "records.lookup",
            BindingEntry({"python": "app_tools:lookup", "tool_name": "lookup-records"}),
        )
        == ()
    )
    assert (
        agentcore_harness_target_binding_validator(
            "agentcore_harness",
            "tools",
            "records.lookup",
            BindingEntry({"mcp": "https://example.test/mcp"}),
        )[0].code
        == "TGT111"
    )
    assert (
        agentcore_harness_target_binding_validator(
            "agentcore_harness",
            "tools",
            "records.lookup",
            BindingEntry({"python": "app_tools:lookup", "tool_name": "*"}),
        )[0].code
        == "TGT111"
    )
    assert (
        agentcore_harness_target_binding_validator(
            "agentcore_harness",
            "datasources",
            "records",
            BindingEntry({"python": "app_tools:lookup"}),
        )[0].code
        == "TGT111"
    )


def test_profile_validator_checks_role_options_and_unsupported_settings(tmp_path: Path) -> None:
    ir = _ir(with_tool=True)
    bindings = _bindings(
        ir,
        profile_options={
            "execution_role_arn": _ROLE_ARN,
            "max_iterations": 4,
            "max_tokens": 2048,
            "timeout_seconds": 600,
            "temperature": 0.2,
            "top_p": 0.8,
            "output_mode": "host_validation",
            "api_format": "converse_stream",
        },
    )

    assert (
        agentcore_harness_target_profile_validator(
            ir,
            "agentcore_harness",
            bindings.targets["agentcore_harness"],
            tmp_path,
        )
        == ()
    )

    invalid = _bindings(
        ir,
        profile_options={
            "execution_role_arn": "arn:aws:iam::bad:role/x",
            "timeout_seconds": True,
            "temperature": float("nan"),
            "api_format": "responses",
            "raw_instructions": "do anything",
        },
    )
    diagnostics = agentcore_harness_target_profile_validator(
        ir,
        "agentcore_harness",
        invalid.targets["agentcore_harness"],
        tmp_path,
    )
    assert {item.code for item in diagnostics} >= {"TGT003", "TGT113"}
    assert any("raw_instructions" in item.message for item in diagnostics)
    assert any("execution_role_arn" in item.message for item in diagnostics)


def test_planning_maps_approval_and_output_validation_to_host_obligations(
    tmp_path: Path,
) -> None:
    ir = _ir(with_tool=True, approval_required=True)
    bindings = _bindings(ir, profile_options={"execution_role_arn": _ROLE_ARN})
    plan = plan_materialization(
        ir,
        bindings,
        target="agentcore_harness",
        profile="test",
        capabilities=agentcore_harness_planner_capabilities(),
    )

    grant = plan.grants[semantic_id("grant", "Researcher", "records.lookup")]
    output = plan.controls[semantic_id("control", "Researcher", "output_conformance")]
    assert grant.outcome == "emulated"
    assert output.outcome == "host_enforced"
    assert output.mechanism == "host.client_output_validator"
    assert any("approve each requested tool call" in item.description for item in plan.host_obligations)
    assert any("validate final Harness text" in item.description for item in plan.host_obligations)
    assert any(
        "do not grant InvokeAgentRuntimeCommand to callers" in item.description for item in plan.host_obligations
    )
    assert plan.adapter.name == "agentcore_harness"


def test_zero_tool_agent_is_a_valid_profile_and_plan(tmp_path: Path) -> None:
    ir = _ir(with_tool=False)
    bindings = _bindings(ir, profile_options={"execution_role_arn": _ROLE_ARN})
    diagnostics = agentcore_harness_target_profile_validator(
        ir,
        "agentcore_harness",
        bindings.targets["agentcore_harness"],
        tmp_path,
    )
    assert diagnostics == ()
    plan = plan_materialization(
        ir,
        bindings,
        target="agentcore_harness",
        profile="test",
        capabilities=agentcore_harness_planner_capabilities(),
    )
    assert plan.adapter.name == "agentcore_harness"


@pytest.mark.parametrize(
    "edge",
    [
        CompositionEdgeIR(
            semantic_id("edge", "delegate"),
            "delegate",
            semantic_id("agent", "Researcher"),
            semantic_id("agent", "Reviewer"),
            "delegate",
            "Delegate work.",
            "summary",
        ),
        CompositionEdgeIR(
            semantic_id("edge", "handoff"),
            "handoff",
            semantic_id("agent", "Researcher"),
            semantic_id("agent", "Reviewer"),
            "handoff",
            "Transfer work.",
            "none",
        ),
    ],
)
def test_planning_rejects_unsupported_composition(edge: CompositionEdgeIR) -> None:
    ir = _ir(with_tool=True, extra_agents=True, composition=(edge,))
    with pytest.raises(PlanningError) as caught:
        plan_materialization(
            ir,
            _bindings(ir, profile_options={"execution_role_arn": _ROLE_ARN}),
            target="agentcore_harness",
            profile="test",
            capabilities=agentcore_harness_planner_capabilities(),
        )
    assert any(issue.semantic_id == edge.id for issue in caught.value.issues)


def test_explicit_isolation_is_unsupported(tmp_path: Path) -> None:
    ir = _ir(
        with_tool=True,
        isolation_profiles=(IsolationProfileIR(semantic_id("isolation", "restricted"), "restricted", state="fresh"),),
    )
    diagnostics = agentcore_harness_target_profile_validator(
        ir,
        "agentcore_harness",
        _bindings(ir, profile_options={"execution_role_arn": _ROLE_ARN}).targets["agentcore_harness"],
        tmp_path,
    )
    assert any("does not support isolation profiles" in item.message for item in diagnostics)
    with pytest.raises(PlanningError):
        plan_materialization(
            ir,
            _bindings(ir, profile_options={"execution_role_arn": _ROLE_ARN}),
            target="agentcore_harness",
            profile="test",
            capabilities=agentcore_harness_planner_capabilities(),
        )


def test_resolver_rejects_isolated_delegation_and_non_input_mappings() -> None:
    resolver = AgentCoreHarnessMappingResolver()
    researcher_id = semantic_id("agent", "Researcher")
    reviewer_id = semantic_id("agent", "Reviewer")
    isolated = CompositionEdgeIR(
        semantic_id("edge", "isolated"),
        "isolated",
        researcher_id,
        reviewer_id,
        "delegate",
        "Delegate work.",
        "none",
        isolation_id=semantic_id("isolation", "restricted"),
    )
    context_mapped = CompositionEdgeIR(
        semantic_id("edge", "context_mapped"),
        "context_mapped",
        researcher_id,
        reviewer_id,
        "delegate",
        "Delegate work.",
        "none",
        input_mappings=FrozenMap({"question": "context.previous"}),
    )
    root_input = CompositionEdgeIR(
        semantic_id("edge", "root_input"),
        "root_input",
        researcher_id,
        reviewer_id,
        "delegate",
        "Delegate work.",
        "none",
        input_mappings=FrozenMap({"question": "input.request.value"}),
    )

    declared = MappingSupport("unsupported", None)
    isolated_support = resolver.composition_support(edge=isolated, declared=declared)
    assert isolated_support is not None
    assert isolated_support.outcome == "unsupported"
    context_mapped_support = resolver.composition_support(edge=context_mapped, declared=declared)
    assert context_mapped_support is not None
    assert context_mapped_support.outcome == "unsupported"
    root_input_support = resolver.composition_support(edge=root_input, declared=declared)
    assert root_input_support is not None
    assert root_input_support.outcome == "emulated"


def test_tool_limit_counts_inline_delegates(tmp_path: Path) -> None:
    ir = _ir(
        with_tool=True,
        extra_agents=True,
        composition=tuple(
            CompositionEdgeIR(
                semantic_id("edge", f"delegate_{index}"),
                f"delegate_{index}",
                semantic_id("agent", "Researcher"),
                semantic_id("agent", "Reviewer"),
                "delegate",
                "Delegate work.",
                "none",
            )
            for index in range(64)
        ),
    )
    diagnostics = agentcore_harness_target_profile_validator(
        ir,
        "agentcore_harness",
        _bindings(ir, profile_options={"execution_role_arn": _ROLE_ARN}).targets["agentcore_harness"],
        tmp_path,
    )

    assert any("more than 64 enabled tools" in item.message for item in diagnostics)


def test_profile_rejects_tool_delegate_and_harness_name_collisions(tmp_path: Path) -> None:
    edge = CompositionEdgeIR(
        semantic_id("edge", "delegate"),
        "delegate",
        semantic_id("agent", "Researcher"),
        semantic_id("agent", "Reviewer"),
        "delegate",
        "Delegate work.",
        "none",
    )
    ir = _ir(with_tool=True, extra_agents=True, composition=(edge,))
    bindings = _bindings(
        ir,
        profile_options={
            "execution_role_arn": _ROLE_ARN,
            "harness_name": "sharedHarness",
        },
        tool_name=native_name("delegate", edge.id),
    )
    diagnostics = agentcore_harness_target_profile_validator(
        ir,
        "agentcore_harness",
        bindings.targets["agentcore_harness"],
        tmp_path,
    )

    assert any("tool name" in item.message and "collides" in item.message for item in diagnostics)
    assert any("Harness name `sharedHarness` is used by both" in item.message for item in diagnostics)


def test_planner_capability_descriptors_limit_the_supported_route() -> None:
    resolver = AgentCoreHarnessMappingResolver()
    assert (
        resolver.resolve_binding(
            kind="tool",
            locator={"python": "app_tools:lookup"},
        ).support.outcome
        == "exact"
    )
    capabilities = agentcore_harness_planner_capabilities()
    assert capabilities.controls["adapter"].outcome == "host_enforced"
    assert capabilities.composition["delegate:none"].outcome == "emulated"
    assert capabilities.composition["delegate:full"].outcome == "unsupported"


def _ir(
    *,
    with_tool: bool,
    approval_required: bool = False,
    extra_agents: bool = False,
    composition: tuple[CompositionEdgeIR, ...] = (),
    isolation_profiles: tuple[IsolationProfileIR, ...] = (),
) -> CanonicalIR:
    answer = TypeIR(
        semantic_id("type", "Answer"),
        "Answer",
        (TypeFieldIR("summary", parse_type_ref("string")),),
    )
    researcher_id = semantic_id("agent", "Researcher")
    reviewer_id = semantic_id("agent", "Reviewer")
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
    grant = (
        GrantIR(
            semantic_id("grant", "Researcher", "records.lookup"),
            researcher_id,
            tool.id,
            "enabled",
            "approval_required" if approval_required else "preapproved",
        )
        if tool is not None
        else None
    )
    researcher = AgentIR(
        researcher_id,
        "Researcher",
        (),
        parse_type_ref("Answer"),
        "Produce an answer.",
        grant_ids=(grant.id,) if grant is not None else (),
    )
    agents = [researcher]
    if extra_agents:
        agents.append(AgentIR(reviewer_id, "Reviewer", (), parse_type_ref("Answer"), "Review the answer."))
    controls = [
        ControlIR(
            semantic_id("control", "Researcher", "output_conformance"),
            "output_conformance",
            researcher_id,
            "high",
            True,
            ("adapter", "host"),
            "adapter",
            derived_from=researcher_id,
            expected_evidence=("output.accepted", "output.schema_failed"),
        )
    ]
    if extra_agents:
        controls.append(
            ControlIR(
                semantic_id("control", "Reviewer", "output_conformance"),
                "output_conformance",
                reviewer_id,
                "high",
                True,
                ("adapter", "host"),
                "adapter",
                derived_from=reviewer_id,
            )
        )
    return CanonicalIR.create(
        types=(answer,),
        capabilities=(tool,) if tool is not None else (),
        agents=tuple(agents),
        grants=(grant,) if grant is not None else (),
        controls=tuple(controls),
        composition=composition,
        isolation_profiles=isolation_profiles,
    )


def _bindings(
    ir: CanonicalIR,
    *,
    profile_options: dict[str, object],
    tool_name: str | None = None,
) -> TargetBindings:
    tools = {
        capability.name: BindingEntry({"python": "app_tools:lookup", **({"tool_name": tool_name} if tool_name else {})})
        for capability in ir.capabilities.values()
        if capability.kind == "tool"
    }
    profiles = {
        "test": TargetProfile(
            default_model="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            agents={agent.name: AgentProfile(options={}) for agent in ir.agents.values()},
            options=profile_options,
        )
    }
    return TargetBindings(
        path=Path("contract4agents.targets.toml"),
        targets={
            "agentcore_harness": TargetBinding(
                adapter="agentcore_harness",
                tools=tools,
                profiles=profiles,
            )
        },
    )
