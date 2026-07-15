from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    CapabilityIR,
    CompositionEdgeIR,
    ControlIR,
    ExternalContextIR,
    GrantIR,
    IsolationProfileIR,
    ParameterIR,
    TypeFieldIR,
    TypeIR,
    parse_type_ref,
    semantic_id,
)
from contract4agents.planning import (
    PLAN_VERSION,
    MappingSupport,
    PlannerCapabilities,
    PlanningError,
    canonical_materialization_plan_json,
    in_process_isolation_support,
    materialization_plan_data,
    plan_materialization,
)
from contract4agents.target_bindings import (
    AgentProfile,
    BindingEntry,
    TargetBinding,
    TargetBindings,
    TargetProfile,
)


def test_plans_complete_provider_neutral_target_with_deterministic_digest() -> None:
    ir = _sample_ir(network="inherited")
    bindings = _bindings()
    capabilities = _capabilities()

    first = plan_materialization(ir, bindings, target="openai", profile="production", capabilities=capabilities)
    second = plan_materialization(ir, bindings, target="openai", profile="production", capabilities=capabilities)
    data = materialization_plan_data(first)

    assert first.plan_version == PLAN_VERSION == "1"
    assert first.contract_digest.startswith("sha256:")
    assert first.plan_digest.startswith("sha256:")
    assert first.plan_digest == second.plan_digest == data["plan_digest"]
    assert first.adapter.name == "openai"
    assert first.adapter.version == "test-adapter-1"
    assert first.agents[semantic_id("agent", "IncidentCommander")].model == "gpt-main"
    assert first.agents[semantic_id("agent", "LogInvestigator")].model == "gpt-small"
    assert first.bindings[semantic_id("tool", "status.publish")].execution == "host"
    assert first.bindings[semantic_id("datasource", "incident.timeline")].mechanism == "host.implementation_binding"
    assert first.bindings[semantic_id("external", "incident_record")].execution == "host"
    grant = first.grants[semantic_id("grant", "IncidentCommander", "status.publish")]
    assert grant.outcome == "exact"
    assert grant.mechanism == "host.implementation_binding+openai.approval_interrupt"
    edge = first.composition[semantic_id("edge", "investigate_logs")]
    assert edge.outcome == "exact"
    assert edge.mechanism == "openai.agent_as_tool"
    control = first.controls[semantic_id("control", "IncidentCommander", "approval", "status.publish")]
    assert control.outcome == "exact"
    assert control.expected_evidence == ("approval.completed", "approval.requested", "tool.started")
    isolation = first.isolation[semantic_id("isolation", "EvidenceWorker")]
    assert isolation.environment == "in_process"
    assert isolation.dimensions["context"].outcome == "emulated"
    assert isolation.dimensions["filesystem"].outcome == "exact"
    assert isolation.dimensions["network"].mechanism == "in_process.inherited"
    assert {item.semantic_id for item in first.host_obligations} >= {
        semantic_id("tool", "status.publish"),
        semantic_id("datasource", "incident.timeline"),
        semantic_id("external", "incident_record"),
    }
    assert first.expected_telemetry == (
        "agent.completed",
        "agent.started",
        "output.accepted",
    )
    encoded = canonical_materialization_plan_json(first)
    assert str(bindings.path) not in encoded
    assert "0x" not in encoded
    assert '"plan_digest":"sha256:' in encoded
    assert canonical_materialization_plan_json(first) == canonical_materialization_plan_json(second)


@pytest.mark.parametrize(
    ("target", "profile", "code"),
    [
        ("missing", "production", "PLN001"),
        ("openai", "missing", "PLN002"),
    ],
)
def test_unknown_target_and_profile_are_structured_errors(target: str, profile: str, code: str) -> None:
    with pytest.raises(PlanningError) as caught:
        plan_materialization(_sample_ir(), _bindings(), target=target, profile=profile, capabilities=_capabilities())

    assert [issue.code for issue in caught.value.issues] == [code]


def test_missing_models_and_bindings_are_reported_together() -> None:
    target = TargetBinding(
        adapter="openai",
        profiles={"production": TargetProfile()},
        environments={"in_process": BindingEntry({"provider": "runtime:in_process"})},
    )
    bindings = TargetBindings(Path("contract4agents.targets.toml"), {"openai": target})

    with pytest.raises(PlanningError) as caught:
        plan_materialization(
            _sample_ir(),
            bindings,
            target="openai",
            profile="production",
            capabilities=_capabilities(),
        )

    codes = [issue.code for issue in caught.value.issues]
    assert codes.count("PLN005") == 2
    assert codes.count("PLN006") == 3


def test_approval_requirement_fails_closed_without_adapter_support() -> None:
    unsupported = PlannerCapabilities.create(
        adapter="openai",
        version="1",
        composition={"delegate": MappingSupport("exact", "openai.agent_as_tool")},
        isolation=in_process_isolation_support(),
    )

    with pytest.raises(PlanningError) as caught:
        plan_materialization(
            _sample_ir(),
            _bindings(),
            target="openai",
            profile="production",
            capabilities=unsupported,
        )

    failed_ids = {issue.semantic_id for issue in caught.value.issues if issue.code == "PLN009"}
    assert semantic_id("grant", "IncidentCommander", "status.publish") in failed_ids
    assert semantic_id("control", "IncidentCommander", "approval", "status.publish") in failed_ids


def test_in_process_network_denial_is_reported_honestly_and_blocks_plan() -> None:
    with pytest.raises(PlanningError) as caught:
        plan_materialization(
            _sample_ir(network="denied"),
            _bindings(),
            target="openai",
            profile="production",
            capabilities=_capabilities(),
        )

    issue = next(item for item in caught.value.issues if item.semantic_id == semantic_id("isolation", "EvidenceWorker"))
    assert issue.code == "PLN009"
    assert "network" in issue.message
    assert "unsupported" in issue.message


def test_required_degraded_control_blocks_while_advisory_unsupported_is_visible() -> None:
    required = _sample_ir(
        extra_control=ControlIR(
            id=semantic_id("control", "IncidentCommander", "runtime_check"),
            name="runtime_check",
            agent_id=semantic_id("agent", "IncidentCommander"),
            severity="high",
            required=True,
            audience=("adapter", "host"),
            assessment="adapter",
            requirement="must be enforced",
        )
    )
    degraded = _capabilities(
        controls={"adapter": MappingSupport("degraded", "best_effort.adapter")}
    )
    with pytest.raises(PlanningError) as caught:
        plan_materialization(required, _bindings(), target="openai", profile="production", capabilities=degraded)
    assert any(
        item.semantic_id == semantic_id("control", "IncidentCommander", "runtime_check")
        for item in caught.value.issues
    )

    advisory = _sample_ir(
        extra_control=ControlIR(
            id=semantic_id("control", "IncidentCommander", "advice"),
            name="advice",
            agent_id=semantic_id("agent", "IncidentCommander"),
            severity="low",
            required=False,
            audience=("reviewer",),
            assessment="advisory",
            requirement="review this manually",
        )
    )
    plan = plan_materialization(
        advisory, _bindings(), target="openai", profile="production", capabilities=_capabilities()
    )
    assert plan.controls[semantic_id("control", "IncidentCommander", "advice")].outcome == "unsupported"


def test_binding_mechanisms_distinguish_provider_hosted_and_remote() -> None:
    ir = _sample_ir()
    target = _bindings().targets["openai"]
    rebound = TargetBinding(
        adapter=target.adapter,
        tools={"status.publish": BindingEntry({"provider": "openai", "tool": "publish"})},
        datasources={"incident.timeline": BindingEntry({"endpoint": "https://example.test/timeline"})},
        external_context=target.external_context,
        environments=target.environments,
        profiles=target.profiles,
    )
    bindings = TargetBindings(Path("different/local/path.toml"), {"openai": rebound})

    with pytest.raises(PlanningError) as caught:
        plan_materialization(ir, bindings, target="openai", profile="production", capabilities=_capabilities())

    assert any(
        "binding execution `provider_hosted` does not satisfy `host`" in issue.message
        for issue in caught.value.issues
    )

    # Bindings themselves still resolve to target-specific mechanisms before the grant mismatch blocks the plan.
    no_grant_ir = CanonicalIR.create(
        types=ir.types.values(),
        capabilities=(next(item for item in ir.capabilities.values() if item.kind == "datasource"),),
        external_contexts=ir.external_contexts.values(),
        agents=ir.agents.values(),
    )
    plan = plan_materialization(
        no_grant_ir,
        bindings,
        target="openai",
        profile="production",
        capabilities=_capabilities(),
    )
    assert plan.bindings[semantic_id("datasource", "incident.timeline")].execution == "remote"


def test_named_execution_boundary_must_be_declared_by_the_target() -> None:
    with pytest.raises(PlanningError) as caught:
        plan_materialization(
            _sample_ir(execution="clean_room"),
            _bindings(),
            target="openai",
            profile="production",
            capabilities=_capabilities(),
        )

    issue = next(
        item
        for item in caught.value.issues
        if item.semantic_id == semantic_id("grant", "IncidentCommander", "status.publish")
    )
    assert issue.code == "PLN009"
    assert "target environment `clean_room` is not declared" in issue.message


def test_materialization_plan_is_deeply_immutable() -> None:
    plan = plan_materialization(
        _sample_ir(), _bindings(), target="openai", profile="production", capabilities=_capabilities()
    )

    with pytest.raises(FrozenInstanceError):
        plan.target = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        plan.agents[semantic_id("agent", "Other")] = plan.agents[semantic_id("agent", "IncidentCommander")]  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.agents[semantic_id("agent", "IncidentCommander")].model_options["temperature"] = 1  # type: ignore[index]


def test_planner_rejects_native_or_callable_binding_values() -> None:
    target = _bindings().targets["openai"]
    invalid = TargetBinding(
        adapter=target.adapter,
        tools={"status.publish": BindingEntry({"python": "app:publish", "native": lambda: None})},
        datasources=target.datasources,
        external_context=target.external_context,
        environments=target.environments,
        profiles=target.profiles,
    )

    with pytest.raises(PlanningError) as caught:
        plan_materialization(
            _sample_ir(),
            TargetBindings(Path("bindings.toml"), {"openai": invalid}),
            target="openai",
            profile="production",
            capabilities=_capabilities(),
        )

    assert any(issue.code == "PLN010" for issue in caught.value.issues)
    assert "canonical JSON" in str(caught.value)


def _capabilities(
    *,
    controls: dict[str, MappingSupport] | None = None,
) -> PlannerCapabilities:
    return PlannerCapabilities.create(
        adapter="openai",
        version="test-adapter-1",
        approval=MappingSupport(
            "exact",
            "openai.approval_interrupt",
            expected_telemetry=("approval.requested", "approval.completed", "tool.started"),
        ),
        composition={
            "delegate": MappingSupport(
                "exact",
                "openai.agent_as_tool",
                expected_telemetry=("agent.started", "agent.completed"),
            ),
            "handoff": MappingSupport("exact", "openai.handoff"),
        },
        controls=controls or {},
        isolation=in_process_isolation_support(),
        expected_telemetry=("agent.started", "agent.completed", "output.accepted"),
    )


def _bindings() -> TargetBindings:
    target = TargetBinding(
        adapter="openai",
        tools={"status.publish": BindingEntry({"python": "incident_app.tools:publish"})},
        datasources={"incident.timeline": BindingEntry({"python": "incident_app.data:timeline"})},
        external_context={"incident_record": BindingEntry({"python": "incident_app.context:record"})},
        environments={"in_process": BindingEntry({"provider": "runtime:in_process"})},
        profiles={
            "production": TargetProfile(
                default_model="gpt-main",
                agents={"LogInvestigator": AgentProfile(model="gpt-small")},
                options={"environment": "in_process", "temperature": 0.0},
            )
        },
    )
    return TargetBindings(Path("/local/not/canonical/contract4agents.targets.toml"), {"openai": target})


def _sample_ir(
    *,
    network: str = "inherited",
    execution: str = "host",
    extra_control: ControlIR | None = None,
) -> CanonicalIR:
    request = TypeIR(
        semantic_id("type", "IncidentRequest"),
        "IncidentRequest",
        (TypeFieldIR("incident_id", parse_type_ref("string")),),
    )
    result = TypeIR(
        semantic_id("type", "IncidentDecision"),
        "IncidentDecision",
        (TypeFieldIR("summary", parse_type_ref("string")),),
    )
    finding = TypeIR(
        semantic_id("type", "LogFinding"),
        "LogFinding",
        (TypeFieldIR("summary", parse_type_ref("string")),),
    )
    tool = CapabilityIR(
        id=semantic_id("tool", "status.publish"),
        name="status.publish",
        kind="tool",
        parameters=(ParameterIR("decision", parse_type_ref("IncidentDecision")),),
        output_type=parse_type_ref("IncidentDecision"),
        description="Publish an approved update.",
        side_effect=True,
    )
    datasource = CapabilityIR(
        id=semantic_id("datasource", "incident.timeline"),
        name="incident.timeline",
        kind="datasource",
        parameters=(ParameterIR("request", parse_type_ref("IncidentRequest")),),
        output_type=parse_type_ref("IncidentDecision"),
        description="Load the incident timeline.",
        render="markdown",
        cache="run",
    )
    external = ExternalContextIR(
        id=semantic_id("external", "incident_record"),
        name="incident_record",
        output_type=parse_type_ref("IncidentRequest"),
        description="Current incident.",
        sensitivity="confidential",
        render="markdown",
    )
    isolation = IsolationProfileIR(
        id=semantic_id("isolation", "EvidenceWorker"),
        name="EvidenceWorker",
        context="explicit_only",
        capabilities="declared_only",
        state="fresh",
        filesystem="inherited",
        network=network,  # type: ignore[arg-type]
        secrets="inherited",
        return_channel="final_output_only",
    )
    grant_id = semantic_id("grant", "IncidentCommander", "status.publish")
    grant = GrantIR(
        id=grant_id,
        agent_id=semantic_id("agent", "IncidentCommander"),
        capability_id=tool.id,
        availability="enabled",
        authorization="approval_required",
        execution=execution,
        isolation_id=isolation.id,
    )
    commander = AgentIR(
        id=semantic_id("agent", "IncidentCommander"),
        name="IncidentCommander",
        parameters=(ParameterIR("request", parse_type_ref("IncidentRequest")),),
        output_type=parse_type_ref("IncidentDecision"),
        goal="Decide the response.",
        grant_ids=(grant_id,),
    )
    investigator = AgentIR(
        id=semantic_id("agent", "LogInvestigator"),
        name="LogInvestigator",
        parameters=(ParameterIR("request", parse_type_ref("IncidentRequest")),),
        output_type=parse_type_ref("LogFinding"),
        goal="Investigate logs.",
    )
    edge = CompositionEdgeIR(
        id=semantic_id("edge", "investigate_logs"),
        name="investigate_logs",
        source_agent_id=commander.id,
        target_agent_id=investigator.id,
        mode="delegate",
        description="Investigate logs.",
        history="none",
        isolation_id=isolation.id,
    )
    approval = ControlIR(
        id=semantic_id("control", "IncidentCommander", "approval", "status.publish"),
        name="approval_status_publish",
        agent_id=commander.id,
        severity="high",
        required=True,
        audience=("adapter", "host", "evaluator", "reviewer"),
        assessment="runtime",
        derived_from=grant.id,
        expected_evidence=("approval.requested", "approval.completed", "tool.started"),
    )
    controls = (approval,) if extra_control is None else (approval, extra_control)
    return CanonicalIR.create(
        types=(request, result, finding),
        capabilities=(tool, datasource),
        external_contexts=(external,),
        agents=(commander, investigator),
        grants=(grant,),
        composition=(edge,),
        controls=controls,
        isolation_profiles=(isolation,),
    )
