"""Google ADK native graph and configuration validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, TypeAdapter

from contract4agents.adapters._native_names import NativeNameRegistry
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
    from contract4agents.materialization._google_adk import GoogleADKSDK

OutputMode = Literal["native", "emulated"]


def _input_schema(input_type: type[object] | None) -> Mapping[str, object]:
    if input_type is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    return cast(dict[str, object], cast(Any, input_type).model_json_schema())


def _with_structured_output_instruction(
    instructions: str,
    output_type: type[object],
) -> str:
    schema = cast(type[BaseModel], output_type).model_json_schema()
    rendered = _load_prompt("structured_output.md").replace(
        "{{OUTPUT_SCHEMA}}",
        json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )
    return f"{instructions.rstrip()}\n\n{rendered.strip()}\n"


def _load_prompt(name: str) -> str:
    resource = files("contract4agents.adapters").joinpath("prompts").joinpath("google_adk").joinpath(name)
    return resource.read_text(encoding="utf-8")


def _output_mode(
    ir: CanonicalIR,
    plan: MaterializationPlan,
    agent_id: SemanticId,
) -> OutputMode:
    control = next(
        (
            control
            for control in ir.controls.values()
            if control.agent_id == agent_id and control.assessment == "adapter" and control.derived_from == agent_id
        ),
        None,
    )
    if control is None:
        raise MaterializationError(
            (
                MaterializationIssue(
                    "MAT334",
                    "Agent output conformance control is missing",
                    agent_id,
                ),
            )
        )
    mapping = plan.controls[control.id]
    return "native" if mapping.outcome == "exact" else "emulated"


def validate_google_adk_graph(
    sdk: GoogleADKSDK,
    ir: CanonicalIR,
    artifacts: CompilerArtifacts,
    plan: MaterializationPlan,
    agents: Mapping[SemanticId, object],
    grant_objects: Mapping[SemanticId, object],
    edge_objects: Mapping[SemanticId, object],
    input_types: FrozenMap[SemanticId, type[object] | None],
    output_types: FrozenMap[str, type[object]],
    names: NativeNameRegistry,
) -> tuple[tuple[SchemaConformanceEvidence, ...], tuple[ConfigurationConformanceEvidence, ...]]:
    issues: list[MaterializationIssue] = []
    schema_conformance: list[SchemaConformanceEvidence] = []
    for agent_id, agent_plan in plan.agents.items():
        native = sdk.describe(agents[agent_id])
        expected_name = names.assign("agent", agent_id, agent_plan.name)
        if native.semantic_name != agent_plan.name or native.native_name != expected_name:
            issues.append(
                MaterializationIssue(
                    "MAT421",
                    "Native Google ADK agent identity differs from plan",
                    agent_id,
                )
            )
        expected_instructions = {artifacts.instructions[agent_plan.name]}
        if _output_mode(ir, plan, agent_id) == "emulated":
            expected_instructions.add(
                _with_structured_output_instruction(
                    artifacts.instructions[agent_plan.name],
                    output_type_for(agent_plan.output_type, output_types),
                )
            )
        if native.instructions not in expected_instructions:
            issues.append(
                MaterializationIssue(
                    "MAT422",
                    "Native Google ADK instructions differ from compiler artifact",
                    agent_id,
                )
            )
        if native.model != agent_plan.model:
            issues.append(
                MaterializationIssue(
                    "MAT423",
                    "Native Google ADK model differs from plan",
                    agent_id,
                )
            )
        expected_input = input_types[agent_id]
        input_evidence = SchemaConformanceEvidence(
            semantic_id=agent_id,
            boundary="agent_input",
            declared_schema=dict(_input_schema(expected_input)),
            materialized_schema=dict(_input_schema(native.input_type)),
        )
        schema_conformance.append(input_evidence)
        if not input_evidence.matches:
            issues.append(
                MaterializationIssue(
                    "MAT432",
                    "Native Google ADK input schema differs from contract",
                    agent_id,
                )
            )
        expected_output = output_type_for(agent_plan.output_type, output_types)
        if native.output_type is not expected_output:
            issues.append(
                MaterializationIssue(
                    "MAT424",
                    "Native Google ADK output type differs from plan",
                    agent_id,
                )
            )
        expected_mode = _output_mode(ir, plan, agent_id)
        if native.output_mode != expected_mode:
            issues.append(
                MaterializationIssue(
                    "MAT425",
                    "Native Google ADK output enforcement differs from plan",
                    agent_id,
                )
            )
        expected_output_schema = cast(dict[str, object], TypeAdapter(expected_output).json_schema())
        output_evidence = SchemaConformanceEvidence(
            semantic_id=agent_id,
            boundary=("agent_output" if expected_mode == "native" else "host_structural_output"),
            declared_schema=expected_output_schema,
            materialized_schema=cast(dict[str, object], TypeAdapter(native.output_type).json_schema()),
        )
        schema_conformance.append(output_evidence)
        if not output_evidence.matches:
            issues.append(
                MaterializationIssue(
                    "MAT427",
                    "Native Google ADK output schema differs from contract",
                    agent_id,
                )
            )
        expected_tools = [
            grant_objects[grant_id] for grant_id in ir.agents[agent_id].grant_ids if grant_id in grant_objects
        ] + [
            edge_objects[edge.id]
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "delegate"
        ]
        if len(native.tools) != len(expected_tools) or any(
            all(item is not candidate for candidate in native.tools) for item in expected_tools
        ):
            issues.append(
                MaterializationIssue(
                    "MAT426",
                    "Native Google ADK tools differ from planned grants/edges",
                    agent_id,
                )
            )
    for grant_id, native_tool in grant_objects.items():
        grant = ir.grants[grant_id]
        capability = ir.capabilities[grant.capability_id]
        expected_input = build_parameter_model(
            f"{ir.agents[grant.agent_id].name}_{capability.name.replace('.', '_')}Input",
            capability.parameters,
            output_types,
        )
        expected_output = type_adapter_for(capability.output_type, output_types)
        native_tool_description = sdk.describe_tool(native_tool)
        input_evidence = SchemaConformanceEvidence(
            semantic_id=grant_id,
            boundary="tool_input",
            declared_schema=dict(_input_schema(expected_input)),
            materialized_schema=dict(native_tool_description.input_schema),
        )
        output_evidence = SchemaConformanceEvidence(
            semantic_id=grant_id,
            boundary="tool_output",
            declared_schema=dict(expected_output.json_schema()),
            materialized_schema=dict(native_tool_description.output_schema),
        )
        schema_conformance.extend((input_evidence, output_evidence))
        if (
            native_tool_description.native_name != names.assign("tool", capability.id, capability.name)
            or not input_evidence.matches
            or not output_evidence.matches
        ):
            issues.append(
                MaterializationIssue(
                    "MAT428",
                    "Native Google ADK tool schema differs from contract",
                    grant_id,
                )
            )
    for edge_id, native_tool in edge_objects.items():
        edge = ir.composition[edge_id]
        child = ir.agents[edge.target_agent_id]
        expected_input = input_types[edge.target_agent_id]
        expected_output = type_adapter_for(child.output_type, output_types)
        native_tool_description = sdk.describe_tool(native_tool)
        input_evidence = SchemaConformanceEvidence(
            semantic_id=edge_id,
            boundary="delegate_input",
            declared_schema=dict(_input_schema(expected_input)),
            materialized_schema=dict(native_tool_description.input_schema),
        )
        output_evidence = SchemaConformanceEvidence(
            semantic_id=edge_id,
            boundary="delegate_output",
            declared_schema=dict(expected_output.json_schema()),
            materialized_schema=dict(native_tool_description.output_schema),
        )
        schema_conformance.extend((input_evidence, output_evidence))
        if (
            native_tool_description.native_name != names.assign("delegate", edge_id, edge.name)
            or not input_evidence.matches
            or not output_evidence.matches
        ):
            issues.append(
                MaterializationIssue("MAT429", "Native Google ADK delegate schema differs from contract", edge_id)
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
        names,
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
    sdk: GoogleADKSDK,
    names: NativeNameRegistry,
) -> tuple[tuple[ConfigurationConformanceEvidence, ...], list[MaterializationIssue]]:
    """Read public ADK properties and mark factory/wrapper limits explicitly."""

    records: list[ConfigurationConformanceEvidence] = []
    issues: list[MaterializationIssue] = []

    def add(record: ConfigurationConformanceEvidence, identifier: SemanticId) -> None:
        records.append(record)
        if record.required and record.status != "passed":
            issues.append(
                MaterializationIssue(
                    "MAT430" if record.status == "violated" else "MAT431",
                    (
                        "Native Google ADK configuration differs from the materialization plan"
                        if record.status == "violated"
                        else "Required Google ADK configuration property cannot be read back"
                    ),
                    identifier,
                )
            )

    for agent_id, agent_plan in plan.agents.items():
        native = sdk.describe(agents[agent_id])
        agent_ir = ir.agents[agent_id]
        expected_output = output_type_for(agent_plan.output_type, output_types)
        expected_tools = [
            names.assign(
                "tool",
                ir.capabilities[ir.grants[grant_id].capability_id].id,
                ir.capabilities[ir.grants[grant_id].capability_id].name,
            )
            for grant_id in agent_ir.grant_ids
            if grant_id in grant_objects
        ] + [
            names.assign("delegate", edge.id, edge.name)
            for edge in ir.composition.values()
            if edge.source_agent_id == agent_id and edge.mode == "delegate"
        ]
        actual_tools = tuple(_native_name(item) for item in native.tools)
        planned_options = thaw_mapping(agent_plan.model_options)
        planned_options.pop("environment", None)
        planned_options.pop("model_factory", None)
        model_settings = getattr(agents[agent_id], "generate_content_config", MISSING)
        factory = native.model_observation_source == "adapter_boundary"
        observed_options: dict[str, object] = {}
        for path, planned_value in flatten_mapping(planned_options):
            # A factory call proves argument transfer at this adapter boundary,
            # but it does not prove that ADK applied the option to the model.
            observed = planned_value if factory else read_public_path(model_settings, path)
            if observed is not MISSING:
                _set_nested(observed_options, path, observed)
            add(
                configuration_evidence(
                    agent_id,
                    f"agent.model_options.{path}",
                    planned_value,
                    observed,
                    source=(
                        "adapter_boundary"
                        if native.model_observation_source == "adapter_boundary"
                        else "native_readback"
                    ),
                    safe=path in SAFE_OPTION_PATHS,
                    required=not factory,
                    reason=(
                        "Custom model factory arguments were verified at the adapter boundary; "
                        "ADK model properties are opaque."
                        if native.model_observation_source == "adapter_boundary"
                        else None
                    ),
                ),
                agent_id,
            )
        expected_native_name = names.assign("agent", agent_id, agent_ir.name)
        add(
            configuration_evidence(agent_id, "agent.name", expected_native_name, native.native_name),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.identity",
                {"semantic_id": str(agent_id), "name": expected_native_name},
                {"semantic_id": str(agent_id), "name": native.native_name},
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id,
                "agent.model",
                agent_plan.model,
                native.model if native.model_observed and not factory else MISSING,
                source=cast(ConfigurationObservationSource, native.model_observation_source),
                required=not factory,
                reason=(
                    "Custom model factory transfer and BaseLlm return type were verified at the adapter boundary; "
                    "ADK model identity is not publicly observable."
                    if factory or not native.model_observed
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
                planned_options if factory else observed_options,
                safe=False,
                source=cast(ConfigurationObservationSource, native.model_observation_source),
                required=not factory,
                reason=(
                    "Custom model factory arguments were verified at the adapter boundary; "
                    "ADK model properties are opaque."
                    if factory
                    else None
                ),
            ),
            agent_id,
        )
        add(
            configuration_evidence(
                agent_id, "agent.output_type", expected_output.__qualname__, native.output_type.__qualname__
            ),
            agent_id,
        )
        add(
            configuration_evidence(agent_id, "agent.output_mode", _output_mode(ir, plan, agent_id), native.output_mode),
            agent_id,
        )
        add(configuration_evidence(agent_id, "agent.tools", sorted(expected_tools), sorted(actual_tools)), agent_id)
        add(configuration_evidence(agent_id, "agent.handoffs", [], []), agent_id)

    for grant_id, grant in plan.grants.items():
        native_tool = grant_objects.get(grant_id)
        capability = ir.capabilities[grant.capability_id]
        expected_name = names.assign("tool", capability.id, capability.name)
        actual_name = sdk.describe_tool(native_tool).native_name if native_tool is not None else MISSING
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
        actual_approval: object = MISSING
        source: ConfigurationObservationSource = "generated_wrapper"
        if native_tool is not None:
            actual_approval = sdk.describe_tool(native_tool).requires_approval
            if actual_approval is None:
                actual_approval = getattr(native_tool, "requires_approval", MISSING)
        add(
            configuration_evidence(
                grant_id,
                "grant.approval",
                grant.authorization == "approval_required",
                actual_approval,
                source=source,
                required=grant.availability == "enabled"
                and capability.kind == "tool"
                and grant.authorization == "approval_required",
            ),
            grant_id,
        )

    for edge_id, edge in ir.composition.items():
        native_edge = edge_objects.get(edge_id)
        expected_name = names.assign("delegate", edge.id, edge.name)
        actual_name = _native_name(native_edge) if native_edge is not None else MISSING
        add(configuration_evidence(edge_id, "edge.identity", expected_name, actual_name), edge_id)
        expected_input = input_types[edge.target_agent_id]
        expected_schema = _input_schema(expected_input)
        actual_schema = sdk.describe_tool(native_edge).input_schema if native_edge is not None else MISSING
        add(
            configuration_evidence(
                edge_id, "edge.schema", expected_schema, actual_schema, source="native_schema", safe=False
            ),
            edge_id,
        )
    return tuple(records), issues


def _native_name(value: object) -> str:
    for attr in ("name", "tool_name", "agent_name"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return type(value).__name__


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


__all__ = ["OutputMode", "validate_google_adk_graph"]
