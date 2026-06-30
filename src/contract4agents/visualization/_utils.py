"""Shared helpers for Contract4Agents visualization artifacts."""

from __future__ import annotations

from typing import Any

from contract4agents.visualization._types import VisualizationEdge, VisualizationNode


def _add_node(nodes: dict[str, VisualizationNode], node_id: str, kind: str, label: str, **metadata: Any) -> None:
    existing = nodes.get(node_id)
    if existing is not None:
        existing["metadata"].update({key: value for key, value in metadata.items() if value not in (None, "")})
        return
    nodes[node_id] = {
        "id": node_id,
        "kind": kind,
        "label": label,
        "metadata": {key: value for key, value in metadata.items() if value not in (None, "")},
    }


def _add_edge(
    edges: dict[str, VisualizationEdge],
    source: str,
    target: str,
    kind: str,
    label: str,
    **metadata: Any,
) -> None:
    edge_id = "|".join([kind, source, target, str(metadata.get("field", ""))])
    edges[edge_id] = {
        "id": edge_id,
        "source": source,
        "target": target,
        "kind": kind,
        "label": label,
        "metadata": {key: value for key, value in metadata.items() if value not in (None, "")},
    }
