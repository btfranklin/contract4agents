"""Deterministic Mermaid rendering of one or all truth layers."""

from __future__ import annotations

import html

from contract4agents.visualization._types import TruthView, VisualizationGraph, VisualizationTruth

MERMAID_CLASS_STYLES = {
    "adapter": "fill:#ede9fe,stroke:#7c3aed,color:#4c1d95",
    "agent": "fill:#d9f2ee,stroke:#0f766e,color:#134e4a",
    "composition": "fill:#ccfbf1,stroke:#0d9488,color:#134e4a",
    "context": "fill:#dbeafe,stroke:#2563eb,color:#1e3a8a",
    "control": "fill:#ffe4e6,stroke:#e11d48,color:#881337",
    "datasource": "fill:#dbeafe,stroke:#2563eb,color:#1e3a8a",
    "eval": "fill:#dcfce7,stroke:#16a34a,color:#14532d",
    "event": "fill:#fef3c7,stroke:#d97706,color:#78350f",
    "external_context": "fill:#e0e7ff,stroke:#4f46e5,color:#312e81",
    "grant": "fill:#fff7ed,stroke:#ea580c,color:#7c2d12",
    "host_obligation": "fill:#fef2f2,stroke:#dc2626,color:#7f1d1d",
    "isolation": "fill:#fae8ff,stroke:#c026d3,color:#701a75",
    "operational_control": "fill:#ffe4e6,stroke:#be123c,color:#881337",
    "quality": "fill:#ecfccb,stroke:#65a30d,color:#365314",
    "run": "fill:#fef9c3,stroke:#ca8a04,color:#713f12",
    "run_spec": "fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e",
    "tool": "fill:#fff3c4,stroke:#b7791f,color:#744210",
    "type": "fill:#f2f4f7,stroke:#667085,color:#344054",
    "type_ref": "fill:#f8fafc,stroke:#94a3b8,color:#475569",
}


def render_mermaid(
    graph: VisualizationGraph,
    *,
    view: TruthView | None = None,
    node_ids: set[str] | None = None,
) -> str:
    """Render a combined graph or one explicit truth layer."""

    visible_nodes = [node for node in graph["nodes"] if _visible(node["truth"], view)]
    if node_ids is not None:
        visible_nodes = [node for node in visible_nodes if node["id"] in node_ids]
    selected = {node["id"] for node in visible_nodes}
    visible_edges = [
        edge
        for edge in graph["edges"]
        if edge["source"] in selected and edge["target"] in selected and _visible(edge["truth"], view)
    ]
    selected_kinds: set[str] = set()
    lines = ["flowchart LR"]
    for node in sorted(visible_nodes, key=lambda item: item["id"]):
        kind = node["kind"]
        selected_kinds.add(kind)
        truth = ", ".join(name for name, facts in node["truth"].items() if facts)
        label = _escape_mermaid_label(f"{node['label']}\\n{kind} [{truth}]")
        mermaid_id = _mermaid_id(node["id"])
        lines.append(f'    {mermaid_id}["{label}"]')
        lines.append(f"    class {mermaid_id} {_mermaid_class(kind)}")
    for edge in sorted(visible_edges, key=lambda item: item["id"]):
        lines.append(
            f"    {_mermaid_id(edge['source'])} -->|{_escape_mermaid_label(edge['label'])}| "
            f"{_mermaid_id(edge['target'])}"
        )
    for kind in sorted(selected_kinds):
        style = MERMAID_CLASS_STYLES.get(kind)
        if style:
            lines.append(f"    classDef {_mermaid_class(kind)} {style}")
    return "\n".join(lines) + "\n"


def render_agent_mermaid(
    graph: VisualizationGraph,
    agent_name: str,
    *,
    view: TruthView | None = None,
) -> str:
    """Render an agent and its immediate declared/planned/observed neighbors."""

    detail = graph["agents"].get(agent_name)
    if detail is None:
        raise ValueError(f"Unknown agent `{agent_name}`")
    focus_id = detail["id"]
    node_ids = {focus_id}
    for edge in graph["edges"]:
        if not _visible(edge["truth"], view):
            continue
        if edge["source"] == focus_id:
            node_ids.add(edge["target"])
        if edge["target"] == focus_id:
            node_ids.add(edge["source"])
    return render_mermaid(graph, view=view, node_ids=node_ids)


def _visible(truth: VisualizationTruth, view: TruthView | None) -> bool:
    return any(truth.values()) if view is None else bool(truth[view])


def _mermaid_id(value: str) -> str:
    return "n_" + "".join(char if char.isalnum() else "_" for char in value)


def _mermaid_class(value: str) -> str:
    return "kind_" + "".join(char if char.isalnum() else "_" for char in value)


def _escape_mermaid_label(value: str) -> str:
    return html.escape(value.replace('"', "'"), quote=False)


__all__ = ["MERMAID_CLASS_STYLES", "render_agent_mermaid", "render_mermaid"]
