"""Mermaid rendering for Contract4Agents visualization graphs."""

from __future__ import annotations

import html

from contract4agents.visualization._types import VisualizationGraph

MERMAID_CLASS_STYLES = {
    "agent": "fill:#d9f2ee,stroke:#0f766e,color:#134e4a",
    "tool": "fill:#fff3c4,stroke:#b7791f,color:#744210",
    "hosted_tool": "fill:#ede9fe,stroke:#7c3aed,color:#4c1d95",
    "datasource": "fill:#dbeafe,stroke:#2563eb,color:#1e3a8a",
    "type": "fill:#f2f4f7,stroke:#667085,color:#344054",
    "eval": "fill:#dcfce7,stroke:#16a34a,color:#14532d",
    "monitor": "fill:#ffe4e6,stroke:#e11d48,color:#881337",
}


def render_mermaid(graph: VisualizationGraph, *, node_ids: set[str] | None = None) -> str:
    """Render a conservative Mermaid flowchart from a visualization graph."""
    lines = ["flowchart LR"]
    nodes = graph["nodes"]
    edges = graph["edges"]
    selected_nodes = {str(node["id"]) for node in nodes} if node_ids is None else node_ids
    selected_edges = [
        edge for edge in edges if str(edge["source"]) in selected_nodes and str(edge["target"]) in selected_nodes
    ]
    connected_node_ids = {str(edge["source"]) for edge in selected_edges} | {
        str(edge["target"]) for edge in selected_edges
    }
    selected_nodes = selected_nodes | connected_node_ids
    selected_kinds: set[str] = set()

    for node in sorted(nodes, key=lambda item: str(item["id"])):
        if str(node["id"]) not in selected_nodes:
            continue
        node_id = _mermaid_id(str(node["id"]))
        kind = str(node["kind"])
        selected_kinds.add(kind)
        label = _escape_mermaid_label(f"{node['label']}\\n{node['kind']}")
        lines.append(f"    {node_id}[\"{label}\"]")
        lines.append(f"    class {node_id} {_mermaid_class(kind)}")
    for edge in sorted(selected_edges, key=lambda item: str(item["id"])):
        source = _mermaid_id(str(edge["source"]))
        target = _mermaid_id(str(edge["target"]))
        label = _escape_mermaid_label(str(edge["label"]))
        lines.append(f"    {source} -->|{label}| {target}")
    for kind in sorted(selected_kinds):
        style = MERMAID_CLASS_STYLES.get(kind)
        if style:
            lines.append(f"    classDef {_mermaid_class(kind)} {style}")
    return "\n".join(lines) + "\n"


def render_agent_mermaid(graph: VisualizationGraph, agent_name: str) -> str:
    """Render a focused diagram for one agent and its configured neighbors."""
    focus_id = f"agent:{agent_name}"
    edges = graph["edges"]
    node_ids = {focus_id}
    for edge in edges:
        if edge["source"] == focus_id:
            node_ids.add(str(edge["target"]))
        if edge["target"] == focus_id:
            node_ids.add(str(edge["source"]))
    for edge in edges:
        if edge["source"] in node_ids and str(edge["source"]).startswith("datasource:"):
            node_ids.add(str(edge["target"]))
        if edge["target"] in node_ids and str(edge["target"]).startswith("datasource:"):
            node_ids.add(str(edge["source"]))
    return render_mermaid(graph, node_ids=node_ids)


def _mermaid_id(value: str) -> str:
    return "n_" + "".join(char if char.isalnum() else "_" for char in value)


def _mermaid_class(value: str) -> str:
    return "kind_" + "".join(char if char.isalnum() else "_" for char in value)


def _escape_mermaid_label(value: str) -> str:
    return html.escape(value.replace('"', "'"), quote=False)
