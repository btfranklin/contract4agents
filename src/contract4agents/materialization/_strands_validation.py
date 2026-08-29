"""Strands native graph and configuration validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

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
from contract4agents.materialization._models import (
    ConfigurationConformanceEvidence,
    ConfigurationObservationSource,
    SchemaConformanceEvidence,
)
from contract4agents.materialization._options import thaw_mapping
from contract4agents.materialization._types import build_parameter_model, output_type_for, type_adapter_for
from contract4agents.planning import MaterializationPlan

if TYPE_CHECKING:
    from contract4agents.materialization._strands import StrandsSDK


def validate_strands_graph(
    sdk: StrandsSDK,
    ir: CanonicalIR,
    artifacts: CompilerArtifacts,
    plan: MaterializationPlan,
    agents: Mapping[SemanticId, object],
    grant_objects: Mapping[SemanticId, object],
    edge_objects: Mapping[SemanticId, object],
    input_types: FrozenMap[SemanticId, type[object] | None],
    output_types: FrozenMap[str, type[object]],
    agent_names: Mapping[SemanticId, str],
    capability_names: Mapping[SemanticId, str],
    edge_names: Mapping[SemanticId, str],
) -> tuple[tuple[SchemaConformanceEvidence, ...], tuple[ConfigurationConformanceEvidence, ...]]:
    issues: list[MaterializationIssue] = []
    schema_conformance: list[SchemaConformanceEvidence] = []
    for agent_id, agent_plan in plan.agents.items():
        native_agent_description = sdk.describe_agent(agents[agent_id])
        if native_agent_description.native_name != agent_names[agent_id]:
            issues.append(
                MaterializationIssue(
                    "MAT451",
                    "Native Strands agent name differs from plan",
                    agent_id,
                )
            )
        if native_agent_description.instructions != artifacts.instructions[agent_plan.name]:
            issues.append(
                MaterializationIssue(
                    "MAT452",
                    "Native Strands instructions differ from compiler artifact",
                    agent_id,
                )
            )
        if native_agent_description.model_identity != agent_plan.model:
            issues.append(
                MaterializationIssue(
                    "MAT453",
                    "Native Strands model differs from plan",
                    agent_id,
                )
            )
        expected_output = output_type_for(agent_plan.output_type, output_types)
        if native_agent_description.output_type is not expected_output:
            issues.append(
                MaterializationIssue(
                    "MAT454",
                    "Native Strands output type differs from plan",
                    agent_id,
                )
            )
        output_evidence = SchemaConformanceEvidence(
            semantic_id=agent_id,
            boundary="agent_output",
            declared_schema=dict(TypeAdapter(expected_output).json_schema()),
            materialized_schema=dict(TypeAdapter(native_agent_description.output_type).json_schema()),
        )
        schema_conformance.append(output_evidence)
        if not output_evidence.matches:
            issues.append(
                MaterializationIssue(
                    "MAT459",
                    "Native Strands output schema differs from contract",
                    agent_id,
                )
            )
        expected_tools = [
            sdk.describe_tool(grant_objects[grant_id]).native_name
            for grant_id in ir.agents[agent_id].grant_ids
            if grant_id in grant_objects
        ] + [
            sdk.describe_tool(edge_objects[edge.id]).native_name
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id
        ]
        if sorted(native_agent_description.tool_names) != sorted(expected_tools):
            issues.append(
                MaterializationIssue(
                    "MAT455",
                    "Native Strands tools differ from planned grants/edges",
                    agent_id,
                )
            )
        approval_required = any(
            (grant := ir.grants[grant_id]).availability == "enabled"
            and grant.capability_id.kind == "tool"
            and grant.authorization == "approval_required"
            for grant_id in ir.agents[agent_id].grant_ids
        )
        expected_allowed: tuple[str, ...] | None = None
        if approval_required:
            expected_output_type = cast(
                type[object],
                output_type_for(agent_plan.output_type, output_types),
            )
            expected_allowed = tuple(
                sorted(
                    {
                        capability_names[grant.capability_id]
                        for grant_id in ir.agents[agent_id].grant_ids
                        if (grant := ir.grants[grant_id]).availability == "enabled"
                        and grant.capability_id.kind == "tool"
                        and grant.authorization != "approval_required"
                    }
                    | {edge_names[edge.id] for edge in ir.composition.values() if edge.source_agent_id == agent_id}
                    | {expected_output_type.__name__}
                )
            )
        if native_agent_description.approval_allowed_tools != expected_allowed:
            issues.append(
                MaterializationIssue(
                    "MAT456",
                    "Native Strands approval policy differs from planned grants",
                    agent_id,
                )
            )

    for grant_id, native_tool in grant_objects.items():
        grant = ir.grants[grant_id]
        capability = ir.capabilities[grant.capability_id]
        native_tool_description = sdk.describe_tool(native_tool)
        expected_input = build_parameter_model(
            f"{ir.agents[grant.agent_id].name}_{capability.name}_Input",
            capability.parameters,
            output_types,
        )
        expected_output = type_adapter_for(
            capability.output_type,
            output_types,
        )
        input_evidence = SchemaConformanceEvidence(
            semantic_id=grant_id,
            boundary="tool_input",
            declared_schema=_stable_schema(_input_schema(expected_input)),
            materialized_schema=_stable_schema(native_tool_description.input_schema),
        )
        output_evidence = SchemaConformanceEvidence(
            semantic_id=grant_id,
            boundary="tool_output",
            declared_schema=_stable_schema(_output_schema(expected_output)),
            materialized_schema=_stable_schema(native_tool_description.output_schema),
        )
        schema_conformance.extend((input_evidence, output_evidence))
        if (
            native_tool_description.native_name != capability_names[capability.id]
            or not input_evidence.matches
            or not output_evidence.matches
        ):
            issues.append(
                MaterializationIssue(
                    "MAT457",
                    "Native Strands tool schema differs from contract",
                    grant_id,
                )
            )
    for edge_id, native_tool in edge_objects.items():
        edge = ir.composition[edge_id]
        child = ir.agents[edge.target_agent_id]
        native_tool_description = sdk.describe_tool(native_tool)
        expected_input = input_types[edge.target_agent_id]
        expected_output = type_adapter_for(child.output_type, output_types)
        input_evidence = SchemaConformanceEvidence(
            semantic_id=edge_id,
            boundary="delegate_input",
            declared_schema=_stable_schema(_input_schema(expected_input)),
            materialized_schema=_stable_schema(native_tool_description.input_schema),
        )
        output_evidence = SchemaConformanceEvidence(
            semantic_id=edge_id,
            boundary="delegate_output",
            declared_schema=_stable_schema(_output_schema(expected_output)),
            materialized_schema=_stable_schema(native_tool_description.output_schema),
        )
        schema_conformance.extend((input_evidence, output_evidence))
        if (
            native_tool_description.native_name != edge_names[edge_id]
            or not input_evidence.matches
            or not output_evidence.matches
        ):
            issues.append(
                MaterializationIssue(
                    "MAT458",
                    "Native Strands delegate schema differs from contract",
                    edge_id,
                )
            )
    configuration_conformance, configuration_issues = _validate_configuration(
        ir,
        plan,
        agents,
        grant_objects,
        edge_objects,
        input_types,
        output_types,
        sdk,
        agent_names,
        capability_names,
        edge_names,
    )
    issues.extend(configuration_issues)
    if issues:
        raise MaterializationError(tuple(issues))
    return tuple(schema_conformance), configuration_conformance


def _validate_configuration(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    agents: Mapping[SemanticId, object],
    grant_objects: Mapping[SemanticId, object],
    edge_objects: Mapping[SemanticId, object],
    input_types: FrozenMap[SemanticId, type[object] | None],
    output_types: FrozenMap[str, type[object]],
    sdk: StrandsSDK,
    agent_names: Mapping[SemanticId, str],
    capability_names: Mapping[SemanticId, str],
    edge_names: Mapping[SemanticId, str],
) -> tuple[tuple[ConfigurationConformanceEvidence, ...], list[MaterializationIssue]]:
    """Compare public Strands agent/tool properties with the immutable plan."""

    records: list[ConfigurationConformanceEvidence] = []
    issues: list[MaterializationIssue] = []

    def add(record: ConfigurationConformanceEvidence, identifier: SemanticId) -> None:
        records.append(record)
        if record.required and record.status != "passed":
            issues.append(
                MaterializationIssue(
                    "MAT460" if record.status == "violated" else "MAT461",
                    (
                        "Native Strands configuration differs from the materialization plan"
                        if record.status == "violated"
                        else "Required Strands configuration property cannot be read back"
                    ),
                    identifier,
                )
            )

    for agent_id, agent_plan in plan.agents.items():
        native = sdk.describe_agent(agents[agent_id])
        agent_ir = ir.agents[agent_id]
        expected_output = output_type_for(agent_plan.output_type, output_types)
        expected_tools = [
            capability_names[ir.grants[grant_id].capability_id]
            for grant_id in agent_ir.grant_ids
            if grant_id in grant_objects
        ] + [edge_names[edge.id] for edge in ir.composition.values() if edge.source_agent_id == agent_id]
        planned_options = thaw_mapping(agent_plan.model_options)
        planned_options.pop("environment", None)
        factory = "model_factory" in planned_options
        planned_options.pop("model_factory", None)
        native_model_config = native.model_options or {}
        factory_boundary = factory or native.model_observation_source == "adapter_boundary"
        observed_options: dict[str, object] = {}
        for path, planned_value in flatten_mapping(planned_options):
            # The factory call proves the exact arguments passed to the
            # adapter, but not that Strands applied them to its model.
            observed = planned_value if factory_boundary else read_public_path(native_model_config, path)
            if observed is not MISSING:
                _set_nested(observed_options, path, observed)
            add(
                configuration_evidence(
                    agent_id,
                    f"agent.model_options.{path}",
                    planned_value,
                    observed,
                    source=cast(
                        ConfigurationObservationSource,
                        "adapter_boundary" if factory else native.model_observation_source,
                    ),
                    required=not factory_boundary,
                    safe=path in SAFE_OPTION_PATHS,
                    reason=(
                        "Custom model factory arguments were verified at the adapter boundary; "
                        "Strands model properties are opaque."
                        if factory
                        else None
                    ),
                ),
                agent_id,
            )
        add(configuration_evidence(agent_id, "agent.name", agent_names[agent_id], native.native_name), agent_id)
        add(configuration_evidence(agent_id, "agent.identity", agent_names[agent_id], native.native_name), agent_id)
        add(
            configuration_evidence(
                agent_id,
                "agent.model",
                agent_plan.model,
                MISSING if factory_boundary or not native.model_observed else native.model_identity,
                source=cast(ConfigurationObservationSource, native.model_observation_source),
                required=not factory_boundary,
                reason=(
                    "Custom model factory transfer and Model return type were verified at the adapter boundary; "
                    "Strands model identity is not publicly observable."
                    if factory_boundary or not native.model_observed
                    else None
                ),
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.model_options",
                planned_options,
                planned_options if factory_boundary else observed_options,
                safe=False,
                source=cast(ConfigurationObservationSource, native.model_observation_source),
                required=not factory_boundary,
                reason=(
                    "Custom model factory arguments were verified at the adapter boundary; "
                    "Strands model properties are opaque."
                    if factory_boundary
                    else None
                ),
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.output_type",
                expected_output.__qualname__,
                native.output_type.__qualname__,
                source=cast(ConfigurationObservationSource, native.output_observation_source),
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.output_mode",
                "structured_output_model",
                "structured_output_model",
                source="generated_wrapper",
            ),
            agent_id,
        )
        add(
            configuration_evidence(agent_id, "agent.tools", sorted(expected_tools), sorted(native.tool_names)), agent_id
        )
        add(configuration_evidence(agent_id, "agent.handoffs", [], []), agent_id)
        add(
            configuration_evidence(
                agent_id,
                "agent.approval_required",
                sorted(
                    capability_names[ir.grants[grant_id].capability_id]
                    for grant_id in agent_ir.grant_ids
                    if ir.grants[grant_id].availability == "enabled"
                    and ir.grants[grant_id].authorization == "approval_required"
                ),
                MISSING,
                source=cast(ConfigurationObservationSource, native.approval_observation_source),
                required=False,
                reason=(
                    "Strands approval intervention policy has no stable public readback accessor; "
                    "constructor wrapper evidence is retained."
                ),
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.retry_strategy",
                None,
                MISSING,
                source="generated_wrapper",
                required=False,
                reason="The native retry strategy is private; this record does not prove host retry behavior.",
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.session_manager",
                None,
                MISSING,
                source="generated_wrapper",
                required=False,
                reason="The native session manager is private; this record does not prove host persistence behavior.",
            ),
            agent_id,
        )

    for grant_id, grant in plan.grants.items():
        native_tool = grant_objects.get(grant_id)
        capability = ir.capabilities[grant.capability_id]
        expected_name = capability_names.get(capability.id, capability.name)
        actual_description = sdk.describe_tool(native_tool) if native_tool is not None else None
        actual_name = actual_description.native_name if actual_description is not None else MISSING
        add(
            configuration_evidence(
                grant_id,
                "grant.identity",
                {"name": expected_name, "capability": str(grant.capability_id)},
                {"name": actual_name, "capability": str(grant.capability_id)}
                if actual_name is not MISSING
                else MISSING,
                required=grant.availability == "enabled" and capability.kind == "tool",
            ),
            grant_id,
        )
        # Strands uses the generated HumanInTheLoop wrapper.  Readback of the
        # policy is not public, so this remains adapter-bound and optional.
        add(
            configuration_evidence(
                grant_id,
                "grant.approval",
                grant.authorization == "approval_required",
                getattr(native_tool, "requires_approval", MISSING) if native_tool is not None else MISSING,
                source="generated_wrapper",
                required=False,
                reason="Strands approval is configured through HumanInTheLoop; native policy readback is not public.",
            ),
            grant_id,
        )

    for edge_id, edge in ir.composition.items():
        native_edge = edge_objects.get(edge_id)
        expected_name = edge_names[edge.id]
        actual_name = sdk.describe_tool(native_edge).native_name if native_edge is not None else MISSING
        add(configuration_evidence(edge_id, "edge.identity", expected_name, actual_name), edge_id)
        expected_input = input_types[edge.target_agent_id]
        expected_schema = _stable_schema(_input_schema(expected_input))
        actual_schema = sdk.describe_tool(native_edge).input_schema if native_edge is not None else MISSING
        add(
            configuration_evidence(
                edge_id,
                "edge.schema",
                expected_schema,
                _stable_schema(cast(Mapping[str, object], actual_schema)) if actual_schema is not MISSING else MISSING,
                source="native_schema",
                safe=False,
            ),
            edge_id,
        )
    return tuple(records), issues


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


def _stable_schema(value: Mapping[str, object]) -> dict[str, object]:
    """Ignore harmless provider-generated field descriptions while retaining shape."""

    def clean(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): clean(child) for key, child in item.items() if key != "description"}
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item

    cleaned = clean(value)
    if not isinstance(cleaned, dict):
        raise TypeError("Native Strands schema must be an object")
    return cleaned


def _input_schema(input_type: type[object] | None) -> Mapping[str, object]:
    if input_type is None:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    return cast(dict[str, object], cast(Any, input_type).model_json_schema())


def _output_schema(adapter: TypeAdapter[Any]) -> Mapping[str, object]:
    return cast(dict[str, object], adapter.json_schema())


__all__ = ["validate_strands_graph"]
