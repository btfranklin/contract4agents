"""OpenAI native graph configuration validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.compiler import CompilerArtifacts
from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId
from contract4agents.materialization._configuration import (
    MISSING,
    SAFE_OPTION_PATHS,
    configuration_evidence,
    flatten_mapping,
    read_public_path,
)
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue
from contract4agents.materialization._models import ConfigurationConformanceEvidence, SchemaConformanceEvidence
from contract4agents.materialization._options import thaw_mapping
from contract4agents.materialization._types import build_parameter_model, output_type_for
from contract4agents.planning import MaterializationPlan

if TYPE_CHECKING:
    from contract4agents.materialization._openai import OpenAISDK


def validate_openai_configuration(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    agents: Mapping[SemanticId, object],
    grant_objects: Mapping[SemanticId, object],
    edge_objects: Mapping[SemanticId, object],
    input_types: FrozenMap[SemanticId, type[object] | None],
    output_types: FrozenMap[str, type[object]],
    sdk: OpenAISDK,
) -> tuple[tuple[ConfigurationConformanceEvidence, ...], list[MaterializationIssue]]:
    """Read OpenAI public properties and compare them with the plan."""

    records: list[ConfigurationConformanceEvidence] = []
    issues: list[MaterializationIssue] = []

    def add(record: ConfigurationConformanceEvidence, identifier: SemanticId) -> None:
        records.append(record)
        status = cast(Any, record).status
        if cast(Any, record).required and status != "passed":
            issues.append(
                MaterializationIssue(
                    "MAT410" if status == "violated" else "MAT411",
                    (
                        "Native OpenAI configuration differs from the materialization plan"
                        if status == "violated"
                        else "Required OpenAI configuration property cannot be read back"
                    ),
                    identifier,
                )
            )

    for agent_id, agent_plan in plan.agents.items():
        native = sdk.describe(agents[agent_id])
        agent_ir = ir.agents[agent_id]
        expected_output = output_type_for(agent_plan.output_type, output_types)
        expected_tools = [
            _planned_tool_name(
                plan,
                ir.capabilities[ir.grants[grant_id].capability_id].id,
                ir.capabilities[ir.grants[grant_id].capability_id].name,
            )
            for grant_id in agent_ir.grant_ids
            if grant_id in grant_objects
        ] + [
            _native_name(edge_objects[edge.id])
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "delegate"
        ]
        expected_handoffs = [
            _native_name(edge_objects[edge.id])
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "handoff"
        ]
        actual_tools = tuple(_native_name(item) for item in native.tools)
        actual_handoffs = tuple(_native_name(item) for item in native.handoffs)
        add(configuration_evidence(agent_id, "agent.name", agent_ir.name, native.name), agent_id)
        add(
            configuration_evidence(
                agent_id,
                "agent.identity",
                {"semantic_id": str(agent_id), "name": agent_ir.name},
                {"semantic_id": str(agent_id), "name": native.name},
            ),
            agent_id,
        )
        add(configuration_evidence(agent_id, "agent.model", agent_plan.model, native.model), agent_id)
        planned_options = thaw_mapping(agent_plan.model_options)
        planned_options.pop("environment", None)
        observed_settings: object = native.model_settings
        if observed_settings is None:
            observed_settings = getattr(agents[agent_id], "model_options", MISSING)
        observed_options: dict[str, object] = {}
        for path, planned_value in flatten_mapping(planned_options):
            observed = read_public_path(observed_settings, path)
            if observed is not MISSING:
                _set_nested(observed_options, path, observed)
            add(
                configuration_evidence(
                    agent_id,
                    f"agent.model_settings.{path}",
                    planned_value,
                    observed,
                    safe=path in SAFE_OPTION_PATHS,
                ),
                agent_id,
            )
        add(
            configuration_evidence(agent_id, "agent.model_options", planned_options, observed_options, safe=False),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.output_type",
                expected_output.__qualname__,
                native.output_type.__qualname__,
            ),
            agent_id,
        )
        add(configuration_evidence(agent_id, "agent.output_mode", "native", native.output_mode), agent_id)
        add(configuration_evidence(agent_id, "agent.tools", sorted(expected_tools), sorted(actual_tools)), agent_id)
        add(
            configuration_evidence(agent_id, "agent.handoffs", sorted(expected_handoffs), sorted(actual_handoffs)),
            agent_id,
        )

    for grant_id, grant in plan.grants.items():
        native_tool = grant_objects.get(grant_id)
        capability = ir.capabilities[grant.capability_id]
        actual_name = _native_name(native_tool) if native_tool is not None else MISSING
        expected_name = _planned_tool_name(plan, capability.id, capability.name)
        add(
            configuration_evidence(
                grant_id,
                "grant.identity",
                {"name": expected_name, "capability": str(grant.capability_id)},
                {"name": actual_name, "capability": str(grant.capability_id)}
                if actual_name is not MISSING
                else MISSING,
                required=grant.availability == "enabled" and grant.capability_id.kind == "tool",
            ),
            grant_id,
        )
        expected_approval = grant.authorization == "approval_required"
        actual_approval: object = MISSING
        if native_tool is not None:
            try:
                description = sdk.describe_tool(native_tool)
            except MaterializationError:
                description = None
            actual_approval = description.needs_approval if description is not None else MISSING
            if actual_approval is None:
                actual_approval = getattr(
                    native_tool, "needs_approval", getattr(native_tool, "requires_approval", MISSING)
                )
        add(
            configuration_evidence(
                grant_id,
                "grant.approval",
                expected_approval,
                actual_approval,
                required=(
                    grant.availability == "enabled"
                    and grant.capability_id.kind == "tool"
                    and grant.authorization == "approval_required"
                ),
            ),
            grant_id,
        )

    for edge_id, edge in ir.composition.items():
        native_edge = edge_objects.get(edge_id)
        expected_name = (
            openai_tool_name(edge.name) if edge.mode == "delegate" or hasattr(native_edge, "tool_name") else edge.name
        )
        actual_name = _native_name(native_edge) if native_edge is not None else MISSING
        add(configuration_evidence(edge_id, "edge.identity", expected_name, actual_name), edge_id)
        expected_input = input_types[edge.target_agent_id]
        expected_schema = (
            sdk.input_schema(expected_input)
            if edge.mode == "delegate"
            else {"mode": "handoff", "history": edge.history}
        )
        actual_schema = (
            sdk.describe_tool(native_edge).input_schema
            if native_edge is not None and edge.mode == "delegate"
            else ({"mode": "handoff", "history": edge.history} if native_edge is not None else MISSING)
        )
        add(
            configuration_evidence(
                edge_id,
                "edge.schema",
                expected_schema,
                actual_schema,
                source="native_schema",
                safe=False,
            ),
            edge_id,
        )
    return tuple(records), issues


def _native_name(value: object) -> str:
    for attr in ("name", "tool_name", "tool_name_override", "agent_name"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return type(value).__name__


def _planned_tool_name(plan: MaterializationPlan, capability_id: SemanticId, capability_name: str) -> str:
    binding = plan.bindings.get(capability_id)
    if binding is not None and binding.execution == "provider_hosted":
        provider_name = binding.locator.get("tool") or binding.locator.get("provider_tool")
        if isinstance(provider_name, str):
            return provider_name
    return openai_tool_name(capability_name)


def _set_nested(target: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    current = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def validate_openai_graph(
    sdk: OpenAISDK,
    ir: CanonicalIR,
    artifacts: CompilerArtifacts,
    plan: MaterializationPlan,
    agents: Mapping[SemanticId, object],
    grant_objects: Mapping[SemanticId, object],
    edge_objects: Mapping[SemanticId, object],
    input_types: FrozenMap[SemanticId, type[object] | None],
    output_types: FrozenMap[str, type[object]],
) -> tuple[tuple[SchemaConformanceEvidence, ...], tuple[ConfigurationConformanceEvidence, ...]]:
    issues: list[MaterializationIssue] = []
    schema_conformance: list[SchemaConformanceEvidence] = []
    for agent_id, agent_plan in plan.agents.items():
        native = sdk.describe(agents[agent_id])
        if native.name != agent_plan.name:
            issues.append(MaterializationIssue("MAT401", "Native agent name differs from plan", agent_id))
        if native.instructions != artifacts.instructions[agent_plan.name]:
            issues.append(
                MaterializationIssue("MAT406", "Native agent instructions differ from compiler artifact", agent_id)
            )
        if native.model != agent_plan.model:
            issues.append(MaterializationIssue("MAT402", "Native agent model differs from plan", agent_id))
        expected_output = output_type_for(agent_plan.output_type, output_types)
        if native.output_type is not expected_output:
            issues.append(MaterializationIssue("MAT403", "Native output type differs from plan", agent_id))
        output_evidence = SchemaConformanceEvidence(
            semantic_id=agent_id,
            boundary="agent_output",
            declared_schema=dict(sdk.output_schema(expected_output)),
            materialized_schema=dict(native.output_schema),
        )
        schema_conformance.append(output_evidence)
        if not output_evidence.matches:
            issues.append(MaterializationIssue("MAT407", "Native output schema differs from contract", agent_id))

        expected_tools = [
            grant_objects[grant_id] for grant_id in ir.agents[agent_id].grant_ids if grant_id in grant_objects
        ] + [
            edge_objects[edge.id]
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "delegate"
        ]
        expected_handoffs = [
            edge_objects[edge.id]
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "handoff"
        ]
        if len(native.tools) != len(expected_tools) or any(
            all(item is not candidate for candidate in native.tools) for item in expected_tools
        ):
            issues.append(MaterializationIssue("MAT404", "Native tools differ from planned grants/edges", agent_id))
        if len(native.handoffs) != len(expected_handoffs) or any(
            all(item is not candidate for candidate in native.handoffs) for item in expected_handoffs
        ):
            issues.append(MaterializationIssue("MAT405", "Native handoffs differ from planned edges", agent_id))
    for grant_id, native_tool in grant_objects.items():
        grant = ir.grants[grant_id]
        capability = ir.capabilities[grant.capability_id]
        if plan.bindings[capability.id].execution == "provider_hosted":
            continue
        input_type = build_parameter_model(
            f"{ir.agents[grant.agent_id].name}_{capability.name}_Input",
            capability.parameters,
            output_types,
        )
        native_tool_description = sdk.describe_tool(native_tool)
        evidence = SchemaConformanceEvidence(
            semantic_id=grant_id,
            boundary="tool_input",
            declared_schema=dict(sdk.input_schema(input_type)),
            materialized_schema=dict(native_tool_description.input_schema),
        )
        schema_conformance.append(evidence)
        if native_tool_description.name != openai_tool_name(capability.name) or not evidence.matches:
            issues.append(MaterializationIssue("MAT408", "Native tool schema differs from contract", grant_id))
    for edge_id, native_tool in edge_objects.items():
        edge = ir.composition[edge_id]
        if edge.mode != "delegate":
            continue
        input_type = input_types[edge.target_agent_id]
        if input_type is None:
            continue
        native_tool_description = sdk.describe_tool(native_tool)
        evidence = SchemaConformanceEvidence(
            semantic_id=edge_id,
            boundary="delegate_input",
            declared_schema=dict(sdk.input_schema(input_type)),
            materialized_schema=dict(native_tool_description.input_schema),
        )
        schema_conformance.append(evidence)
        if native_tool_description.name != openai_tool_name(edge.name) or not evidence.matches:
            issues.append(MaterializationIssue("MAT409", "Native delegate schema differs from contract", edge_id))
    configuration_conformance, configuration_issues = validate_openai_configuration(
        ir,
        plan,
        agents,
        grant_objects,
        edge_objects,
        input_types,
        output_types,
        sdk,
    )
    issues.extend(configuration_issues)
    if issues:
        raise MaterializationError(tuple(issues))
    return tuple(schema_conformance), configuration_conformance


__all__ = ["validate_openai_configuration", "validate_openai_graph"]
