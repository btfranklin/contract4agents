"""Build a V2 review graph without collapsing desired and actual truth."""

# mypy: allow-redefinition

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from contract4agents.assurance import ControlResult
from contract4agents.ir import CanonicalIR, SemanticId, TypeRef, contract_digest, format_type_ref
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing import NormalizedTrace
from contract4agents.visualization._types import (
    VisualizationAgentDetail,
    VisualizationEdge,
    VisualizationGraph,
    VisualizationNode,
)
from contract4agents.visualization._utils import add_edge, add_node

VISUALIZATION_VERSION = "2"


def build_visualization_graph(
    ir: CanonicalIR,
    *,
    project_root: Path | str | None = None,
    plan: MaterializationPlan | None = None,
    trace: NormalizedTrace | None = None,
    control_results: Sequence[ControlResult] = (),
) -> VisualizationGraph:
    """Build declared, planned, observed, and assured review layers.

    The layers are deliberately additive. A planned or observed fact never
    overwrites what the contract declared, and an assurance result never turns
    missing runtime evidence into an observed fact.
    """

    digest = contract_digest(ir)
    if plan is not None and plan.contract_digest != digest:
        raise ValueError("Materialization plan contract digest does not match the canonical IR")
    if trace is not None:
        for event in trace.events:
            if event.context.contract_digest != digest:
                raise ValueError("Normalized trace contract digest does not match the canonical IR")
            if plan is not None and event.context.plan_digest != plan.plan_digest:
                raise ValueError("Normalized trace plan digest does not match the materialization plan")

    nodes: dict[str, VisualizationNode] = {}
    edges: dict[str, VisualizationEdge] = {}
    warnings: list[str] = []

    _add_declared(ir, nodes, edges)
    if plan is not None:
        _add_planned(plan, nodes, edges)
    else:
        warnings.append("No materialization plan supplied; planned runtime mappings are not shown.")
    if trace is not None:
        _add_observed(trace, nodes, edges)
    else:
        warnings.append("No normalized trace supplied; observed runtime behavior is not shown.")
    _add_assured(control_results, nodes, edges, warnings)
    if not control_results:
        warnings.append("No control results supplied; declared controls have no assurance status.")

    agents = _agent_details(ir, nodes)
    ordered_nodes = sorted(nodes.values(), key=lambda item: (item["kind"], item["id"]))
    ordered_edges = sorted(edges.values(), key=lambda item: item["id"])
    return {
        "version": VISUALIZATION_VERSION,
        "ir_version": ir.ir_version,
        "contract_digest": digest,
        "plan_digest": plan.plan_digest if plan is not None else None,
        "project_root": str(project_root) if project_root is not None else None,
        "nodes": ordered_nodes,
        "edges": ordered_edges,
        "agents": agents,
        "summary": {
            "declared": sum(bool(node["truth"]["declared"]) for node in ordered_nodes),
            "planned": sum(bool(node["truth"]["planned"]) for node in ordered_nodes),
            "observed": sum(bool(node["truth"]["observed"]) for node in ordered_nodes),
            "assured": sum(bool(node["truth"]["assured"]) for node in ordered_nodes),
        },
        "warnings": warnings,
    }


def _add_declared(
    ir: CanonicalIR,
    nodes: dict[str, VisualizationNode],
    edges: dict[str, VisualizationEdge],
) -> None:
    for item in ir.types.values():
        add_node(
            nodes,
            str(item.id),
            "type",
            item.name,
            view="declared",
            description=item.description,
            fields=[
                {
                    "name": field.name,
                    "type": format_type_ref(field.type_ref),
                    "has_default": field.has_default,
                    "default": _json_value(field.default),
                }
                for field in item.fields
            ],
        )
        for field in item.fields:
            _add_type_edge(nodes, edges, str(item.id), field.type_ref, "field_type", field.name)

    for item in ir.capabilities.values():
        add_node(
            nodes,
            str(item.id),
            item.kind,
            item.name,
            view="declared",
            description=item.description,
            side_effect=item.side_effect,
            render=item.render,
            cache=item.cache,
            parameters=[
                {"name": parameter.name, "type": format_type_ref(parameter.type_ref), "required": parameter.required}
                for parameter in item.parameters
            ],
            output_type=format_type_ref(item.output_type),
        )
        for parameter in item.parameters:
            _add_type_edge(nodes, edges, str(item.id), parameter.type_ref, "capability_input", parameter.name)
        _add_type_edge(nodes, edges, str(item.id), item.output_type, "capability_output", "returns")

    for item in ir.external_contexts.values():
        add_node(
            nodes,
            str(item.id),
            "external_context",
            item.name,
            view="declared",
            description=item.description,
            sensitivity=item.sensitivity,
            render=item.render,
            output_type=format_type_ref(item.output_type),
        )
        _add_type_edge(nodes, edges, str(item.id), item.output_type, "external_output", "provides")

    for item in ir.agents.values():
        add_node(
            nodes,
            str(item.id),
            "agent",
            item.name,
            view="declared",
            goal=item.goal,
            description=item.description,
            output_type=format_type_ref(item.output_type),
            guidance=[{"text": value.text, "audience": list(value.audience)} for value in item.guidance],
        )
        for parameter in item.parameters:
            _add_type_edge(nodes, edges, str(item.id), parameter.type_ref, "agent_input", parameter.name)
        _add_type_edge(nodes, edges, str(item.id), item.output_type, "agent_output", "returns")

    for item in ir.contexts.values():
        add_node(
            nodes,
            str(item.id),
            "context",
            item.name,
            view="declared",
            origin=item.origin,
            type=format_type_ref(item.type_ref),
            input_mappings=_json_value(item.input_mappings),
        )
        add_edge(edges, str(item.agent_id), str(item.id), "agent_context", item.origin, view="declared")
        if item.origin_id is not None:
            add_edge(edges, str(item.id), str(item.origin_id), "context_origin", "resolved from", view="declared")
        _add_type_edge(nodes, edges, str(item.id), item.type_ref, "context_type", "typed as")

    for item in ir.grants.values():
        add_node(
            nodes,
            str(item.id),
            "grant",
            item.id.parts[-1],
            view="declared",
            availability=item.availability,
            authorization=item.authorization,
            execution=item.execution,
            isolation_id=str(item.isolation_id) if item.isolation_id is not None else None,
        )
        add_edge(edges, str(item.agent_id), str(item.id), "agent_grant", "grant", view="declared")
        add_edge(edges, str(item.id), str(item.capability_id), "grant_capability", item.availability, view="declared")
        if item.isolation_id is not None:
            add_edge(edges, str(item.id), str(item.isolation_id), "grant_isolation", "isolated by", view="declared")

    for item in ir.composition.values():
        add_node(
            nodes,
            str(item.id),
            "composition",
            item.name,
            view="declared",
            mode=item.mode,
            description=item.description,
            history=item.history,
            input_mappings=_json_value(item.input_mappings),
            audience=list(item.audience),
            isolation_id=str(item.isolation_id) if item.isolation_id is not None else None,
        )
        add_edge(edges, str(item.source_agent_id), str(item.id), "composition_source", item.mode, view="declared")
        add_edge(edges, str(item.id), str(item.target_agent_id), "composition_target", item.mode, view="declared")
        if item.isolation_id is not None:
            add_edge(
                edges,
                str(item.id),
                str(item.isolation_id),
                "composition_isolation",
                "isolated by",
                view="declared",
            )

    for item in ir.isolation_profiles.values():
        add_node(
            nodes,
            str(item.id),
            "isolation",
            item.name,
            view="declared",
            context=item.context,
            capabilities=item.capabilities,
            state=item.state,
            filesystem=item.filesystem,
            network=item.network,
            secrets=item.secrets,
            return_channel=item.return_channel,
        )

    for item in ir.controls.values():
        add_node(
            nodes,
            str(item.id),
            "control",
            item.name,
            view="declared",
            severity=item.severity,
            required=item.required,
            audience=list(item.audience),
            assessment=item.assessment,
            condition=item.condition,
            requirement=item.requirement,
            derived_from=str(item.derived_from) if item.derived_from is not None else None,
            expected_evidence=list(item.expected_evidence),
        )
        add_edge(edges, str(item.id), str(item.agent_id), "control_target", item.assessment, view="declared")
        if item.derived_from is not None:
            add_edge(edges, str(item.id), str(item.derived_from), "control_source", "derived from", view="declared")

    for item in ir.qualities.values():
        add_node(
            nodes,
            str(item.id),
            "quality",
            item.name,
            view="declared",
            rubric=item.rubric,
            audience=list(item.audience),
        )
        add_edge(edges, str(item.id), str(item.agent_id), "quality_target", "evaluates", view="declared")

    for item in ir.operational_controls.values():
        add_node(
            nodes,
            str(item.id),
            "operational_control",
            item.name,
            view="declared",
            severity=item.severity,
            requirement=item.requirement,
            window=item.window,
            audience=list(item.audience),
        )
        add_edge(edges, str(item.id), str(item.agent_id), "operational_target", "governs", view="declared")

    for item in ir.evals.values():
        add_node(
            nodes,
            str(item.id),
            "eval",
            item.name,
            view="declared",
            givens=_json_value(item.givens),
            expectations=list(item.expectations),
            quality_ids=[str(value) for value in item.quality_ids],
        )
        add_edge(edges, str(item.id), str(item.agent_id), "eval_target", "evaluates", view="declared")
        for quality_id in item.quality_ids:
            add_edge(edges, str(item.id), str(quality_id), "eval_quality", "uses rubric", view="declared")

    for item in ir.run_specs.values():
        add_node(
            nodes,
            str(item.id),
            "run_spec",
            item.name,
            view="declared",
            assertions=list(item.assertions),
            stages=[
                {
                    "name": stage.name,
                    "agent_id": str(stage.agent_id),
                    "output_type": format_type_ref(stage.output_type),
                    "cardinality": stage.cardinality,
                }
                for stage in item.stages
            ],
        )
        for stage in item.stages:
            add_edge(
                edges,
                str(item.id),
                str(stage.agent_id),
                "run_stage",
                stage.name,
                view="declared",
                discriminator=stage.name,
                cardinality=stage.cardinality,
                output_type=format_type_ref(stage.output_type),
            )


def _add_planned(
    plan: MaterializationPlan,
    nodes: dict[str, VisualizationNode],
    edges: dict[str, VisualizationEdge],
) -> None:
    adapter_id = f"adapter:{plan.adapter.name}"
    add_node(
        nodes,
        adapter_id,
        "adapter",
        plan.adapter.name,
        view="planned",
        version=plan.adapter.version,
        target=plan.target,
        profile=plan.profile,
        plan_digest=plan.plan_digest,
        artifact_digests=_json_value(plan.artifact_digests),
        expected_telemetry=list(plan.expected_telemetry),
    )
    for item in plan.agents.values():
        add_node(
            nodes,
            str(item.id),
            "agent",
            item.name,
            view="planned",
            model=item.model,
            model_options=_json_value(item.model_options),
            output_type=format_type_ref(item.output_type),
        )
        add_edge(edges, adapter_id, str(item.id), "adapter_materializes", "materializes", view="planned")
    for item in plan.bindings.values():
        label = _node_label(nodes, str(item.id), item.id.parts[-1])
        kind = "external_context" if item.kind == "external" else item.kind
        add_node(
            nodes,
            str(item.id),
            kind,
            str(label),
            view="planned",
            locator=_json_value(item.locator),
            outcome=item.outcome,
            mechanism=item.mechanism,
            execution=item.execution,
        )
        add_edge(edges, adapter_id, str(item.id), "adapter_binding", item.outcome, view="planned")
    for item in plan.grants.values():
        label = _node_label(nodes, str(item.id), item.id.parts[-1])
        add_node(
            nodes,
            str(item.id),
            "grant",
            str(label),
            view="planned",
            availability=item.availability,
            authorization=item.authorization,
            execution=item.execution,
            outcome=item.outcome,
            mechanism=item.mechanism,
        )
    for item in plan.composition.values():
        label = _node_label(nodes, str(item.id), item.id.parts[-1])
        add_node(
            nodes,
            str(item.id),
            "composition",
            str(label),
            view="planned",
            mode=item.mode,
            outcome=item.outcome,
            mechanism=item.mechanism,
            isolation_id=str(item.isolation_id) if item.isolation_id is not None else None,
        )
    for item in plan.controls.values():
        label = _node_label(nodes, str(item.id), item.id.parts[-1])
        add_node(
            nodes,
            str(item.id),
            "control",
            str(label),
            view="planned",
            required=item.required,
            assessment=item.assessment,
            outcome=item.outcome,
            mechanism=item.mechanism,
            expected_evidence=list(item.expected_evidence),
        )
    for item in plan.isolation.values():
        label = _node_label(nodes, str(item.id), item.id.parts[-1])
        add_node(
            nodes,
            str(item.id),
            "isolation",
            str(label),
            view="planned",
            environment=item.environment,
            provider=item.provider,
            dimensions={
                key: {"requested": value.requested, "outcome": value.outcome, "mechanism": value.mechanism}
                for key, value in item.dimensions.items()
            },
        )
    for index, obligation in enumerate(plan.host_obligations):
        node_id = f"host_obligation:{obligation.code}:{index}"
        add_node(
            nodes,
            node_id,
            "host_obligation",
            obligation.code,
            view="planned",
            description=obligation.description,
            semantic_id=str(obligation.semantic_id) if obligation.semantic_id is not None else None,
        )
        if obligation.semantic_id is not None:
            add_edge(edges, node_id, str(obligation.semantic_id), "obligation_target", "requires host", view="planned")


def _add_observed(
    trace: NormalizedTrace,
    nodes: dict[str, VisualizationNode],
    edges: dict[str, VisualizationEdge],
) -> None:
    for run_id in trace.run_ids:
        events = trace.for_run(run_id).events
        run_node = f"run:{run_id}"
        add_node(
            nodes,
            run_node,
            "run",
            run_id,
            view="observed",
            thread_id=events[0].context.thread_id,
            event_count=len(events),
            event_types=sorted({event.event_type for event in events}),
            providers=sorted({event.provider.name for event in events}),
            plan_digest=events[0].context.plan_digest,
            contract_digest=events[0].context.contract_digest,
        )
    for event in trace.events:
        event_node = f"event:{event.event_id}"
        reviewer_event = event.to_dict(audience="reviewer")
        add_node(
            nodes,
            event_node,
            "event",
            event.event_type,
            view="observed",
            event_id=event.event_id,
            timestamp=event.timestamp,
            run_id=event.context.run_id,
            provider=reviewer_event["provider"],
            data=reviewer_event["data"],
            provenance=reviewer_event["provenance"],
            evidence_refs=list(event.evidence_refs),
            redaction=reviewer_event["redaction"],
        )
        add_edge(edges, event_node, f"run:{event.context.run_id}", "event_run", "observed in", view="observed")
        if event.parent_event_id is not None:
            add_edge(edges, f"event:{event.parent_event_id}", event_node, "event_parent", "precedes", view="observed")
        refs: list[tuple[str, str]] = []
        if event.semantic.agent_id is not None:
            refs.append((str(event.semantic.agent_id), "agent"))
        if event.semantic.capability_id is not None:
            refs.append((str(event.semantic.capability_id), "capability"))
        if event.semantic.grant_id is not None:
            refs.append((str(event.semantic.grant_id), "grant"))
        refs.extend((str(control_id), "control") for control_id in event.semantic.control_ids)
        for semantic_id, role in refs:
            if semantic_id not in nodes:
                raise ValueError(f"Normalized trace references unknown semantic ID `{semantic_id}`")
            add_edge(
                edges,
                event_node,
                semantic_id,
                "event_semantic",
                role,
                view="observed",
                discriminator=role,
            )
            _record_observation(nodes[semantic_id], event)


def _record_observation(node: VisualizationNode, event: Any) -> None:
    facts = node["truth"]["observed"]
    facts["event_count"] = int(facts.get("event_count", 0)) + 1
    for key, value in (
        ("event_types", event.event_type),
        ("run_ids", event.context.run_id),
        ("providers", event.provider.name),
    ):
        values = set(facts.get(key, []))
        values.add(value)
        facts[key] = sorted(values)
    evidence = set(facts.get("evidence_refs", []))
    evidence.update(event.evidence_refs)
    facts["evidence_refs"] = sorted(evidence)


def _add_assured(
    control_results: Sequence[ControlResult],
    nodes: dict[str, VisualizationNode],
    edges: dict[str, VisualizationEdge],
    warnings: list[str],
) -> None:
    seen: set[str] = set()
    for result in sorted(control_results, key=lambda item: item.control_id):
        if result.control_id in seen:
            raise ValueError(f"Duplicate assurance result for control `{result.control_id}`")
        seen.add(result.control_id)
        node = nodes.get(result.control_id)
        if node is None:
            warnings.append(f"Assurance result references unknown control `{result.control_id}`.")
            add_node(
                nodes,
                result.control_id,
                "control",
                result.control_id.rsplit(":", 1)[-1],
                view="assured",
                orphaned=True,
            )
            node = nodes[result.control_id]
        node["truth"]["assured"].update(result.to_dict())
        for event_id in result.evidence_event_ids:
            target = f"event:{event_id}"
            if target in nodes:
                add_edge(edges, result.control_id, target, "assurance_evidence", "supported by", view="assured")
            else:
                warnings.append(
                    f"Assurance result for `{result.control_id}` references missing trace event `{event_id}`."
                )


def _agent_details(ir: CanonicalIR, nodes: dict[str, VisualizationNode]) -> dict[str, VisualizationAgentDetail]:
    result: dict[str, VisualizationAgentDetail] = {}
    grants = list(ir.grants.values())
    contexts = list(ir.contexts.values())
    composition = list(ir.composition.values())
    controls = list(ir.controls.values())
    qualities = list(ir.qualities.values())
    operational = list(ir.operational_controls.values())
    evals = list(ir.evals.values())
    for agent in sorted(ir.agents.values(), key=lambda item: item.name):
        inputs = [
            {"name": value.name, "type": format_type_ref(value.type_ref), "required": value.required}
            for value in agent.parameters
        ]
        rendered_inputs = ", ".join(f"{item['name']}: {item['type']}" for item in inputs)
        signature = f"{agent.name}({rendered_inputs})"
        signature += f" -> {format_type_ref(agent.output_type)}"
        agent_node = nodes[str(agent.id)]
        result[agent.name] = {
            "id": str(agent.id),
            "name": agent.name,
            "signature": signature,
            "goal": agent.goal,
            "description": agent.description,
            "guidance": [{"text": item.text, "audience": list(item.audience)} for item in agent.guidance],
            "inputs": inputs,
            "output_type": format_type_ref(agent.output_type),
            "grants": [
                {
                    "id": str(item.id),
                    "capability_id": str(item.capability_id),
                    "availability": item.availability,
                    "authorization": item.authorization,
                    "execution": item.execution,
                }
                for item in grants
                if item.agent_id == agent.id
            ],
            "contexts": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "origin": item.origin,
                    "type": format_type_ref(item.type_ref),
                    "input_mappings": dict(item.input_mappings),
                }
                for item in contexts
                if item.agent_id == agent.id
            ],
            "composition": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "direction": "outgoing" if item.source_agent_id == agent.id else "incoming",
                    "mode": item.mode,
                    "other_agent_id": str(
                        item.target_agent_id if item.source_agent_id == agent.id else item.source_agent_id
                    ),
                }
                for item in composition
                if agent.id in {item.source_agent_id, item.target_agent_id}
            ],
            "controls": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "severity": item.severity,
                    "assessment": item.assessment,
                    "assurance": nodes[str(item.id)]["truth"]["assured"],
                }
                for item in controls
                if item.agent_id == agent.id
            ],
            "qualities": [
                {"id": str(item.id), "name": item.name, "rubric": item.rubric}
                for item in qualities
                if item.agent_id == agent.id
            ],
            "operational_controls": [
                {"id": str(item.id), "name": item.name, "severity": item.severity, "requirement": item.requirement}
                for item in operational
                if item.agent_id == agent.id
            ],
            "evals": [
                {"id": str(item.id), "name": item.name, "expectations": list(item.expectations)}
                for item in evals
                if item.agent_id == agent.id
            ],
            "planned": dict(agent_node["truth"]["planned"]),
            "observed": dict(agent_node["truth"]["observed"]),
        }
    return result


def _add_type_edge(
    nodes: dict[str, VisualizationNode],
    edges: dict[str, VisualizationEdge],
    source: str,
    type_ref: TypeRef,
    kind: str,
    label: str,
) -> None:
    rendered = format_type_ref(type_ref)
    identifier = getattr(type_ref, "type_id", None)
    target = str(identifier) if isinstance(identifier, SemanticId) else f"type_ref:{rendered}"
    if target not in nodes:
        add_node(nodes, target, "type_ref", rendered, view="declared", type=rendered)
    add_edge(edges, source, target, kind, label, view="declared", discriminator=label)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value


def _node_label(nodes: dict[str, VisualizationNode], node_id: str, fallback: str) -> str:
    node = nodes.get(node_id)
    return node["label"] if node is not None else fallback


__all__ = ["VISUALIZATION_VERSION", "build_visualization_graph"]
