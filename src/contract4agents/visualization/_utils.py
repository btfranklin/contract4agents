"""Shared graph mutation helpers with explicit truth layers."""

from __future__ import annotations

from collections.abc import Mapping

from contract4agents.ir import CanonicalJsonValue
from contract4agents.visualization._types import (
    TruthView,
    VisualizationEdge,
    VisualizationNode,
    VisualizationTruth,
)


def empty_truth() -> VisualizationTruth:
    return {"declared": {}, "planned": {}, "observed": {}, "assured": {}}


def add_node(
    nodes: dict[str, VisualizationNode],
    node_id: str,
    kind: str,
    label: str,
    *,
    view: TruthView,
    **facts: object,
) -> None:
    node = nodes.get(node_id)
    if node is None:
        node = {"id": node_id, "kind": kind, "label": label, "truth": empty_truth()}
        nodes[node_id] = node
    elif node["kind"] != kind or node["label"] != label:
        raise ValueError(f"Conflicting visualization identity for `{node_id}`")
    node["truth"][view].update(_facts(facts))


def add_edge(
    edges: dict[str, VisualizationEdge],
    source: str,
    target: str,
    kind: str,
    label: str,
    *,
    view: TruthView,
    discriminator: str = "",
    **facts: object,
) -> None:
    edge_id = "|".join((kind, source, target, discriminator))
    edge = edges.get(edge_id)
    if edge is None:
        edge = {
            "id": edge_id,
            "source": source,
            "target": target,
            "kind": kind,
            "label": label,
            "truth": empty_truth(),
        }
        edges[edge_id] = edge
    edge["truth"][view]["present"] = True
    edge["truth"][view].update(_facts(facts))


def merge_facts(node: VisualizationNode, view: TruthView, **facts: object) -> None:
    node["truth"][view].update(_facts(facts))


def _facts(values: dict[str, object]) -> dict[str, CanonicalJsonValue]:
    return {key: json_value(value) for key, value in values.items() if value is not None and value != ""}


def json_value(value: object) -> CanonicalJsonValue:
    """Convert internal facts to JSON values before storing them in the graph."""

    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Mapping):
        return {str(key): json_value(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [json_value(child) for child in value]
    raise TypeError(f"Visualization fact is not JSON-compatible: {type(value).__name__}")


__all__ = ["add_edge", "add_node", "empty_truth", "merge_facts"]
