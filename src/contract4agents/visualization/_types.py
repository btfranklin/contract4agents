"""JSON-compatible contracts for review artifacts."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

TruthView = Literal["declared", "planned", "observed", "assured"]


class VisualizationTruth(TypedDict):
    declared: dict[str, Any]
    planned: dict[str, Any]
    observed: dict[str, Any]
    assured: dict[str, Any]


class VisualizationNode(TypedDict):
    id: str
    kind: str
    label: str
    truth: VisualizationTruth


class VisualizationEdge(TypedDict):
    id: str
    source: str
    target: str
    kind: str
    label: str
    truth: VisualizationTruth


class VisualizationAgentDetail(TypedDict):
    id: str
    name: str
    signature: str
    goal: str
    description: str
    guidance: list[dict[str, Any]]
    inputs: list[dict[str, Any]]
    output_type: str
    grants: list[dict[str, Any]]
    contexts: list[dict[str, Any]]
    composition: list[dict[str, Any]]
    controls: list[dict[str, Any]]
    qualities: list[dict[str, Any]]
    operational_controls: list[dict[str, Any]]
    evals: list[dict[str, Any]]
    planned: dict[str, Any]
    observed: dict[str, Any]


class VisualizationSummary(TypedDict):
    declared: int
    planned: int
    observed: int
    assured: int


class VisualizationGraph(TypedDict):
    version: str
    ir_version: str
    contract_digest: str
    plan_digest: str | None
    project_root: str | None
    nodes: list[VisualizationNode]
    edges: list[VisualizationEdge]
    agents: dict[str, VisualizationAgentDetail]
    summary: VisualizationSummary
    warnings: list[str]


__all__ = [
    "TruthView",
    "VisualizationAgentDetail",
    "VisualizationEdge",
    "VisualizationGraph",
    "VisualizationNode",
    "VisualizationSummary",
    "VisualizationTruth",
]
