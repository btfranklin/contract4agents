"""Deterministic, domain-aware presentation model for the static review page."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, cast

from contract4agents.visualization._types import (
    TruthView,
    VisualizationAgentDetail,
    VisualizationEvidenceStage,
    VisualizationFocus,
    VisualizationFocusNode,
    VisualizationFocusRelationship,
    VisualizationGraph,
    VisualizationLayout,
    VisualizationNode,
    VisualizationOverviewAgent,
    VisualizationOverviewRelationship,
    VisualizationPosition,
    VisualizationPresentation,
    VisualizationReviewNote,
    VisualizationSummary,
    VisualizationTruth,
    VisualizationTruthMembership,
)

_TRUTH_VIEWS: tuple[TruthView, ...] = ("declared", "planned", "observed", "assured")


def build_visualization_presentation(graph: VisualizationGraph) -> VisualizationPresentation:
    """Derive stable display labels, joins, layouts, and review guidance."""

    nodes = {node["id"]: node for node in graph["nodes"]}
    agent_details = sorted(graph["agents"].values(), key=lambda item: (_humanize(item["name"]), item["id"]))
    relationships = _composition_relationships(graph, nodes)
    agent_ids = [item["id"] for item in agent_details]
    levels = _agent_levels(agent_ids, relationships, nodes)
    wide_positions, wide_layout = _overview_wide_positions(agent_ids, levels, nodes)
    compact_positions, compact_layout = _overview_compact_positions(agent_ids, levels, nodes)

    focus = {
        detail["id"]: _focus_model(graph, detail, relationships, nodes)
        for detail in agent_details
    }
    overview_agents = [
        _overview_agent(detail, focus[detail["id"]], wide_positions, compact_positions, nodes)
        for detail in agent_details
    ]
    overview_agents.sort(key=lambda item: (levels[item["id"]], item["wide"]["y"], item["id"]))
    stages = _evidence_stages(graph["summary"], graph)
    coordinator_id = overview_agents[0]["id"] if overview_agents else None
    coordinator_name = overview_agents[0]["name"] if overview_agents else None
    return {
        "system": {
            "name": _system_name(graph["project_root"]),
            "summary": _system_summary(len(overview_agents), len(relationships)),
            "agent_count": len(overview_agents),
            "composition_count": len(relationships),
            "semantic_entity_count": len(graph["nodes"]),
            "semantic_relationship_count": len(graph["edges"]),
            "coordinator_id": coordinator_id,
            "coordinator_name": coordinator_name,
            "contract_digest": graph["contract_digest"],
            "plan_digest": graph["plan_digest"],
            "project_root": graph["project_root"],
        },
        "stages": stages,
        "overview_agents": overview_agents,
        "overview_relationships": relationships,
        "overview_wide_layout": wide_layout,
        "overview_compact_layout": compact_layout,
        "focus": focus,
        "review_notes": _review_notes(graph, focus),
    }


def _composition_relationships(
    graph: VisualizationGraph,
    nodes: dict[str, VisualizationNode],
) -> list[VisualizationOverviewRelationship]:
    incoming: dict[str, str] = {}
    outgoing: dict[str, str] = {}
    for edge in graph["edges"]:
        if edge["kind"] == "composition_source":
            incoming[edge["target"]] = edge["source"]
        elif edge["kind"] == "composition_target":
            outgoing[edge["source"]] = edge["target"]

    result: list[VisualizationOverviewRelationship] = []
    for node in sorted(nodes.values(), key=lambda item: item["id"]):
        if node["kind"] != "composition" or node["id"] not in incoming or node["id"] not in outgoing:
            continue
        declared = node["truth"]["declared"]
        planned = node["truth"]["planned"]
        mode = str(declared.get("mode", planned.get("mode", "delegate")))
        result.append(
            {
                "id": node["id"],
                "source": incoming[node["id"]],
                "target": outgoing[node["id"]],
                "mode": mode,
                "label": "hands off" if mode == "handoff" else "delegates",
                "description": str(declared.get("description", "")),
                "truth": _membership(node["truth"]),
            }
        )
    return result


def _agent_levels(
    agent_ids: list[str],
    relationships: list[VisualizationOverviewRelationship],
    nodes: dict[str, VisualizationNode],
) -> dict[str, int]:
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: set[str] = set()
    for relationship in relationships:
        outgoing[relationship["source"]].append(relationship["target"])
        incoming.add(relationship["target"])
    for values in outgoing.values():
        values.sort(key=lambda value: (_node_name(nodes, value), value))

    roots = [value for value in agent_ids if value not in incoming]
    roots.sort(key=lambda value: (-len(outgoing[value]), _node_name(nodes, value), value))
    levels: dict[str, int] = {}

    def visit(root: str, base: int = 0) -> None:
        queue: deque[tuple[str, int]] = deque([(root, base)])
        while queue:
            current, level = queue.popleft()
            existing = levels.get(current)
            if existing is not None and existing <= level:
                continue
            levels[current] = level
            for target in outgoing[current]:
                queue.append((target, level + 1))

    for root in roots:
        visit(root)
    for agent_id in sorted(agent_ids, key=lambda value: (_node_name(nodes, value), value)):
        if agent_id not in levels:
            visit(agent_id)
    return levels


def _overview_wide_positions(
    agent_ids: list[str],
    levels: dict[str, int],
    nodes: dict[str, VisualizationNode],
) -> tuple[dict[str, VisualizationPosition], VisualizationLayout]:
    lanes: dict[int, list[str]] = defaultdict(list)
    for agent_id in agent_ids:
        lanes[levels[agent_id]].append(agent_id)
    for lane in lanes.values():
        lane.sort(key=lambda value: (_node_name(nodes, value), value))
    max_level = max(lanes, default=0)
    max_lane = max((len(values) for values in lanes.values()), default=1)
    height = max(420, 56 + max_lane * 142)
    width = max(620, 296 + max_level * 304)
    result: dict[str, VisualizationPosition] = {}
    for level, lane in sorted(lanes.items()):
        used = len(lane) * 142 - 30
        start = max(30, (height - used) // 2)
        for index, agent_id in enumerate(lane):
            result[agent_id] = {"x": 36 + level * 304, "y": start + index * 142}
    return result, {"width": width, "height": height}


def _overview_compact_positions(
    agent_ids: list[str],
    levels: dict[str, int],
    nodes: dict[str, VisualizationNode],
) -> tuple[dict[str, VisualizationPosition], VisualizationLayout]:
    ordered = sorted(agent_ids, key=lambda value: (levels[value], _node_name(nodes, value), value))
    positions: dict[str, VisualizationPosition] = {
        agent_id: {"x": 20, "y": 24 + index * 142}
        for index, agent_id in enumerate(ordered)
    }
    return positions, {"width": 344, "height": max(260, 44 + len(ordered) * 142)}


def _overview_agent(
    detail: VisualizationAgentDetail,
    focus: VisualizationFocus,
    wide_positions: dict[str, VisualizationPosition],
    compact_positions: dict[str, VisualizationPosition],
    nodes: dict[str, VisualizationNode],
) -> VisualizationOverviewAgent:
    tools = focus["tools"]
    collaborators = [item for item in focus["collaborators"] if item["direction"] == "outgoing"]
    context_count = len(focus["contexts"])
    pieces: list[str] = []
    if tools:
        pieces.append(f"{len(tools)} tool{'s' if len(tools) != 1 else ''}")
    if collaborators:
        pieces.append(f"{len(collaborators)} delegate{'s' if len(collaborators) != 1 else ''}")
    if not pieces and context_count:
        pieces.append(f"{context_count} context source{'s' if context_count != 1 else ''}")
    attention: str | None = None
    if focus["assurance"] in {"violated", "unverified", "unsupported"}:
        attention = "Review required"
    elif any(bool(item.get("approval_sensitive")) for item in tools):
        attention = "Approval required"
    node = nodes[detail["id"]]
    return {
        "id": detail["id"],
        "name": _humanize(str(detail["name"])),
        "source_name": str(detail["name"]),
        "purpose": str(detail.get("description") or detail.get("goal") or "No purpose declared."),
        "output_type": _type_label(str(detail["output_type"])),
        "summary": " · ".join(pieces) if pieces else "No delegated access",
        "assurance": focus["assurance"],
        "attention": attention,
        "truth": _membership(node["truth"]),
        "coverage": focus["coverage"],
        "wide": wide_positions[detail["id"]],
        "compact": compact_positions[detail["id"]],
    }


def _focus_model(
    graph: VisualizationGraph,
    detail: VisualizationAgentDetail,
    overview_relationships: list[VisualizationOverviewRelationship],
    nodes: dict[str, VisualizationNode],
) -> VisualizationFocus:
    agent_id = str(detail["id"])
    collaborators = _collaborators(agent_id, overview_relationships, nodes)
    tools = _tools(detail, nodes)
    contexts = _contexts(detail, graph, nodes)
    controls = _controls(detail, nodes)
    assurance = _agent_assurance(controls)
    focus_nodes: dict[str, VisualizationFocusNode] = {}
    focus_relationships: list[VisualizationFocusRelationship] = []

    agent_node = nodes[agent_id]
    focus_nodes[agent_id] = _focus_node(
        agent_id,
        "agent",
        _humanize(str(detail["name"])),
        _type_label(str(detail["output_type"])),
        assurance,
        agent_node,
    )
    for item in collaborators:
        other_id = str(item["agent_id"])
        other_node = nodes[other_id]
        focus_nodes.setdefault(
            other_id,
            _focus_node(
                other_id,
                "agent",
                str(item["name"]),
                "Handoff agent" if item["mode"] == "handoff" else "Delegated agent",
                None,
                other_node,
            ),
        )
        source, target = (agent_id, other_id) if item["direction"] == "outgoing" else (other_id, agent_id)
        focus_relationships.append(
            {
                "id": str(item["id"]),
                "source": source,
                "target": target,
                "kind": "composition",
                "label": "hands off" if item["mode"] == "handoff" else "delegates",
                "mode": str(item["mode"]),
                "truth": cast(VisualizationTruthMembership, item["truth"]),
            }
        )

    for item in tools:
        capability_id = str(item["id"])
        capability_node = nodes[capability_id]
        focus_nodes.setdefault(
            capability_id,
            _focus_node(
                capability_id,
                str(item["kind"]),
                str(item["name"]),
                str(item["access_label"]),
                "approval" if item["approval_sensitive"] else None,
                capability_node,
            ),
        )
        focus_relationships.append(
            {
                "id": f"access:{agent_id}:{capability_id}",
                "source": agent_id,
                "target": capability_id,
                "kind": "access",
                "label": "may use",
                "mode": None,
                "truth": _combined_membership(agent_node, capability_node),
            }
        )

    for item in contexts:
        context_id = str(item["id"])
        context_node = nodes[context_id]
        focus_nodes.setdefault(
            context_id,
            _focus_node(
                context_id,
                "context",
                str(item["name"]),
                str(item["source_label"]),
                None,
                context_node,
            ),
        )
        focus_relationships.append(
            {
                "id": f"context:{context_id}:{agent_id}",
                "source": context_id,
                "target": agent_id,
                "kind": "context",
                "label": "supplies",
                "mode": None,
                "truth": _membership(context_node["truth"]),
            }
        )
        source_id = item.get("source_id")
        if isinstance(source_id, str) and source_id in nodes:
            source_node = nodes[source_id]
            focus_nodes.setdefault(
                source_id,
                _focus_node(
                    source_id,
                    source_node["kind"],
                    _humanize(source_node["label"]),
                    "Context source",
                    None,
                    source_node,
                ),
            )
            focus_relationships.append(
                {
                    "id": f"origin:{source_id}:{context_id}",
                    "source": source_id,
                    "target": context_id,
                    "kind": "context_origin",
                    "label": "provides",
                    "mode": None,
                    "truth": _combined_membership(source_node, context_node),
                }
            )

    for item in controls:
        control_id = str(item["id"])
        control_node = nodes[control_id]
        focus_nodes.setdefault(
            control_id,
            _focus_node(
                control_id,
                "control",
                str(item["name"]),
                str(item["summary"]),
                str(item["status"]),
                control_node,
            ),
        )
        focus_relationships.append(
            {
                "id": f"control:{control_id}:{agent_id}",
                "source": control_id,
                "target": agent_id,
                "kind": "control",
                "label": "governs",
                "mode": None,
                "truth": _membership(control_node["truth"]),
            }
        )

    ordered_nodes = [focus_nodes[agent_id]] + sorted(
        (node for node_id, node in focus_nodes.items() if node_id != agent_id),
        key=lambda item: (_focus_kind_order(item["kind"]), item["label"], item["id"]),
    )
    wide_layout = _focus_wide_layout(ordered_nodes)
    compact_layout = _focus_compact_layout(ordered_nodes)
    coverage = _coverage([nodes[node["id"]] for node in ordered_nodes])
    focus_relationships.sort(key=lambda item: (item["kind"], item["source"], item["target"], item["id"]))
    return {
        "agent_id": agent_id,
        "name": _humanize(str(detail["name"])),
        "purpose": str(detail.get("description") or detail.get("goal") or "No purpose declared."),
        "output_type": _type_label(str(detail["output_type"])),
        "assurance": assurance,
        "nodes": ordered_nodes,
        "relationships": focus_relationships,
        "coverage": coverage,
        "tools": tools,
        "contexts": contexts,
        "collaborators": collaborators,
        "controls": controls,
        "technical": {
            "semantic_id": agent_id,
            "source_name": detail["name"],
            "signature": detail["signature"],
            "inputs": detail["inputs"],
            "output_type": detail["output_type"],
            "goal": detail["goal"],
            "description": detail["description"],
            "guidance": detail["guidance"],
            "planned": detail["planned"],
            "observed": detail["observed"],
            "tools": tools,
            "contexts": contexts,
            "controls": controls,
        },
        "wide_layout": wide_layout,
        "compact_layout": compact_layout,
    }


def _collaborators(
    agent_id: str,
    relationships: list[VisualizationOverviewRelationship],
    nodes: dict[str, VisualizationNode],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for relationship in relationships:
        if relationship["source"] == agent_id:
            other_id = relationship["target"]
            direction = "outgoing"
        elif relationship["target"] == agent_id:
            other_id = relationship["source"]
            direction = "incoming"
        else:
            continue
        result.append(
            {
                "id": relationship["id"],
                "agent_id": other_id,
                "name": _humanize(_node_name(nodes, other_id)),
                "direction": direction,
                "mode": relationship["mode"],
                "description": relationship["description"],
                "truth": relationship["truth"],
            }
        )
    return sorted(result, key=lambda item: (item["direction"], item["mode"], item["name"], item["id"]))


def _tools(
    detail: VisualizationAgentDetail,
    nodes: dict[str, VisualizationNode],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for grant in detail["grants"]:
        capability_id = str(grant["capability_id"])
        capability = nodes[capability_id]
        grant_node = nodes[str(grant["id"])]
        authorization = grant.get("authorization")
        access_label = "Approval required" if authorization == "approval_required" else "Preapproved"
        result.append(
            {
                "id": capability_id,
                "grant_id": grant["id"],
                "name": _capability_label(capability["label"]),
                "source_name": capability["label"],
                "kind": capability["kind"],
                "availability": grant.get("availability"),
                "authorization": authorization,
                "execution": grant.get("execution"),
                "access_label": access_label,
                "approval_sensitive": authorization == "approval_required",
                "planned": grant_node["truth"]["planned"],
                "implementation": capability["truth"]["planned"],
                "observed": capability["truth"]["observed"],
                "truth": _combined_membership(grant_node, capability),
            }
        )
    return sorted(result, key=lambda item: (item["name"], item["id"]))


def _contexts(
    detail: VisualizationAgentDetail,
    graph: VisualizationGraph,
    nodes: dict[str, VisualizationNode],
) -> list[dict[str, Any]]:
    origins = {
        edge["source"]: edge["target"]
        for edge in graph["edges"]
        if edge["kind"] == "context_origin"
    }
    result: list[dict[str, Any]] = []
    for context in detail["contexts"]:
        context_id = str(context["id"])
        source_id = origins.get(context_id)
        source_name = _humanize(_node_name(nodes, source_id)) if source_id in nodes else "Invocation input"
        origin = str(context["origin"])
        result.append(
            {
                "id": context_id,
                "name": _humanize(str(context["name"])),
                "source_name": source_name,
                "source_id": source_id,
                "source_label": f"From {source_name}",
                "origin": origin,
                "type": _type_label(str(context["type"])),
                "input_mappings": context["input_mappings"],
                "planned": nodes[context_id]["truth"]["planned"],
                "observed": nodes[context_id]["truth"]["observed"],
            }
        )
    return sorted(result, key=lambda item: (item["name"], item["id"]))


def _controls(detail: VisualizationAgentDetail, nodes: dict[str, VisualizationNode]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for control in detail["controls"]:
        node = nodes[str(control["id"])]
        declared = node["truth"]["declared"]
        planned = node["truth"]["planned"]
        assured = node["truth"]["assured"]
        status = str(assured.get("status", "declared_only"))
        if status == "declared_only" and planned.get("outcome") in {"degraded", "unsupported"}:
            status = "unsupported"
        elif status == "declared_only" and planned:
            status = "unverified"
        severity = str(declared.get("severity", control.get("severity", "medium")))
        requirement = declared.get("requirement") or declared.get("condition") or _humanize(str(control["name"]))
        result.append(
            {
                "id": control["id"],
                "name": _humanize(str(control["name"])),
                "source_name": control["name"],
                "status": status,
                "status_label": _status_label(status),
                "summary": f"{_humanize(severity)} · {_status_label(status)}",
                "severity": severity,
                "required": bool(declared.get("required", True)),
                "assessment": declared.get("assessment"),
                "requirement": requirement,
                "expected_evidence": declared.get("expected_evidence", []),
                "planned": planned,
                "assurance": assured,
                "truth": _membership(node["truth"]),
            }
        )
    return sorted(result, key=lambda item: (_control_priority(str(item["status"])), item["name"], item["id"]))


def _agent_assurance(controls: list[dict[str, Any]]) -> str:
    relevant = [item for item in controls if item["required"]] or controls
    statuses = {str(item["status"]) for item in relevant}
    for status in ("violated", "unsupported", "unverified"):
        if status in statuses:
            return status
    if statuses and statuses == {"passed"}:
        return "passed"
    return "declared_only"


def _focus_node(
    node_id: str,
    kind: str,
    label: str,
    meta: str,
    status: str | None,
    node: VisualizationNode,
) -> VisualizationFocusNode:
    return {
        "id": node_id,
        "kind": kind,
        "label": label,
        "meta": meta,
        "status": status,
        "truth": _membership(node["truth"]),
        "wide": {"x": 0, "y": 0},
        "compact": {"x": 0, "y": 0},
    }


def _focus_wide_layout(nodes: list[VisualizationFocusNode]) -> VisualizationLayout:
    primary = nodes[0]
    origins = [
        node
        for node in nodes[1:]
        if node["kind"] == "external_context" or (node["kind"] == "datasource" and node["meta"] == "Context source")
    ]
    incoming = [node for node in nodes[1:] if node["kind"] in {"context", "control"}]
    outgoing = [node for node in nodes[1:] if node not in origins and node not in incoming]
    height = max(420, 48 + max(len(origins), len(incoming), len(outgoing), 1) * 104)
    primary["wide"] = {"x": 560, "y": max(32, (height - 84) // 2)}
    for index, node in enumerate(origins):
        node["wide"] = {"x": 24, "y": 34 + index * 104}
    for index, node in enumerate(incoming):
        node["wide"] = {"x": 292, "y": 34 + index * 104}
    for index, node in enumerate(outgoing):
        node["wide"] = {"x": 828, "y": 34 + index * 104}
    return {"width": 1084, "height": height}


def _focus_compact_layout(nodes: list[VisualizationFocusNode]) -> VisualizationLayout:
    for index, node in enumerate(nodes):
        node["compact"] = {"x": 20, "y": 24 + index * 104}
    return {"width": 344, "height": max(260, 44 + len(nodes) * 104)}


def _evidence_stages(summary: VisualizationSummary, graph: VisualizationGraph) -> list[VisualizationEvidenceStage]:
    available = {
        "declared": summary["declared"] > 0,
        "planned": graph["plan_digest"] is not None,
        "observed": summary["observed"] > 0,
        "assured": summary["assured"] > 0,
    }
    copy = {
        "declared": (
            "Contract intent",
            "No contract structure was supplied.",
            "Compile a contract project to review its declared system.",
        ),
        "planned": (
            "Resolved runtime",
            "No materialization plan was supplied.",
            "Choose a target and profile to compare intent with runtime support.",
        ),
        "observed": (
            "Runtime evidence",
            "No run trace was supplied.",
            "Add a normalized trace to compare planned and observed behavior.",
        ),
        "assured": (
            "Control results",
            "No assurance results are available.",
            "Supply a plan and trace to assess contract-derived controls.",
        ),
    }
    return [
        {
            "key": view,
            "label": _humanize(view),
            "count": summary[view],
            "available": available[view],
            "description": copy[view][0],
            "empty_title": copy[view][1],
            "empty_body": copy[view][2],
        }
        for view in _TRUTH_VIEWS
    ]


def _review_notes(graph: VisualizationGraph, focus: dict[str, VisualizationFocus]) -> list[VisualizationReviewNote]:
    if graph["warnings"]:
        return [
            {
                "tone": "warning",
                "title": "Review data inconsistency",
                "detail": graph["warnings"][0],
                "action": "Inspect technical details and the source evidence before relying on this view.",
            }
        ]
    controls = [control for item in focus.values() for control in item["controls"] if control["required"]]
    violated = [item for item in controls if item["status"] == "violated"]
    if violated:
        return [
            {
                "tone": "danger",
                "title": f"{len(violated)} required control{'s' if len(violated) != 1 else ''} violated",
                "detail": "Observed evidence conflicts with a required contract guarantee.",
                "action": "Review the affected agent and its control evidence before approving this run.",
            }
        ]
    unsupported = [item for item in controls if item["status"] == "unsupported"]
    if unsupported:
        return [
            {
                "tone": "attention",
                "title": (
                    f"{len(unsupported)} required control"
                    f"{'s are' if len(unsupported) != 1 else ' is'} unsupported"
                ),
                "detail": "The selected target cannot enforce every required guarantee.",
                "action": "Open the affected agent to review the unsupported mechanism.",
            }
        ]
    if graph["plan_digest"] is None:
        return [
            {
                "tone": "gap",
                "title": "Only declared structure is available",
                "detail": "Runtime support and enforcement have not been resolved for a target profile.",
                "action": "Add a target and profile when generating the visualization.",
            }
        ]
    if graph["summary"]["observed"] == 0:
        return [
            {
                "tone": "gap",
                "title": "Plan available; runtime evidence not supplied",
                "detail": "The materialized design can be reviewed, but actual agent behavior is not yet visible.",
                "action": "Add a normalized trace to compare planned and observed behavior.",
            }
        ]
    unverified = [item for item in controls if item["status"] == "unverified"]
    if unverified:
        return [
            {
                "tone": "attention",
                "title": f"{len(unverified)} required control{'s are' if len(unverified) != 1 else ' is'} unverified",
                "detail": "The available evidence cannot prove every required guarantee.",
                "action": "Open the affected agent to see the missing mechanism or evidence.",
            }
        ]
    if graph["summary"]["assured"] == 0:
        return [
            {
                "tone": "gap",
                "title": "Runtime evidence is available; controls are not assessed",
                "detail": "The trace is visible, but no control result establishes assurance status.",
                "action": "Assess the trace against the contract-derived controls.",
            }
        ]
    return []


def _coverage(nodes: list[VisualizationNode]) -> VisualizationSummary:
    return {
        "declared": sum(bool(node["truth"]["declared"]) for node in nodes),
        "planned": sum(bool(node["truth"]["planned"]) for node in nodes),
        "observed": sum(bool(node["truth"]["observed"]) for node in nodes),
        "assured": sum(bool(node["truth"]["assured"]) for node in nodes),
    }


def _membership(truth: VisualizationTruth) -> VisualizationTruthMembership:
    return {
        "declared": bool(truth["declared"]),
        "planned": bool(truth["planned"]),
        "observed": bool(truth["observed"]),
        "assured": bool(truth["assured"]),
    }


def _combined_membership(*nodes: VisualizationNode) -> VisualizationTruthMembership:
    return {
        "declared": any(bool(node["truth"]["declared"]) for node in nodes),
        "planned": any(bool(node["truth"]["planned"]) for node in nodes),
        "observed": any(bool(node["truth"]["observed"]) for node in nodes),
        "assured": any(bool(node["truth"]["assured"]) for node in nodes),
    }


def _node_name(nodes: dict[str, VisualizationNode], node_id: str | None) -> str:
    if node_id is None:
        return ""
    node = nodes.get(node_id)
    return node["label"] if node is not None else node_id.rsplit(":", 1)[-1]


def _system_name(project_root: str | None) -> str:
    if not project_root:
        return "Contract4Agents System"
    name = Path(project_root).name
    return _humanize(name) or "Contract4Agents System"


def _system_summary(agent_count: int, relationship_count: int) -> str:
    agents = f"{agent_count} agent{'s' if agent_count != 1 else ''}"
    relationships = f"{relationship_count} composition relationship{'s' if relationship_count != 1 else ''}"
    return f"{agents} · {relationships}"


def _capability_label(value: str) -> str:
    return " · ".join(_humanize(part) for part in value.split("."))


def _type_label(value: str) -> str:
    return _humanize(value.removeprefix("type:").rsplit(":", 1)[-1])


def _humanize(value: str) -> str:
    spaced = value.replace("_", " ").replace("-", " ")
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", spaced)
    return " ".join(word if word.isupper() else word[:1].upper() + word[1:] for word in spaced.split())


def _status_label(value: str) -> str:
    return {
        "declared_only": "Declared only",
        "passed": "Passed",
        "violated": "Violated",
        "unverified": "Unverified",
        "unsupported": "Unsupported",
    }.get(value, _humanize(value))


def _control_priority(value: str) -> int:
    return {"violated": 0, "unsupported": 1, "unverified": 2, "declared_only": 3, "passed": 4}.get(value, 5)


def _focus_kind_order(value: str) -> int:
    return {
        "agent": 0,
        "tool": 1,
        "datasource": 2,
        "context": 3,
        "external_context": 4,
        "control": 5,
    }.get(value, 6)


__all__ = ["build_visualization_presentation"]
