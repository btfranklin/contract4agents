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


class VisualizationTruthMembership(TypedDict):
    declared: bool
    planned: bool
    observed: bool
    assured: bool


class VisualizationPosition(TypedDict):
    x: int
    y: int


class VisualizationLayout(TypedDict):
    width: int
    height: int


class VisualizationEvidenceStage(TypedDict):
    key: TruthView
    label: str
    count: int
    available: bool
    description: str
    empty_title: str
    empty_body: str


class VisualizationOverviewAgent(TypedDict):
    id: str
    name: str
    source_name: str
    purpose: str
    output_type: str
    summary: str
    assurance: str
    attention: str | None
    truth: VisualizationTruthMembership
    coverage: VisualizationSummary
    wide: VisualizationPosition
    compact: VisualizationPosition


class VisualizationOverviewRelationship(TypedDict):
    id: str
    source: str
    target: str
    mode: str
    label: str
    description: str
    truth: VisualizationTruthMembership


class VisualizationFocusNode(TypedDict):
    id: str
    kind: str
    label: str
    meta: str
    status: str | None
    truth: VisualizationTruthMembership
    wide: VisualizationPosition
    compact: VisualizationPosition


class VisualizationFocusRelationship(TypedDict):
    id: str
    source: str
    target: str
    kind: str
    label: str
    mode: str | None
    truth: VisualizationTruthMembership


class VisualizationReviewNote(TypedDict):
    tone: str
    title: str
    detail: str
    action: str


class VisualizationFocus(TypedDict):
    agent_id: str
    name: str
    purpose: str
    output_type: str
    assurance: str
    nodes: list[VisualizationFocusNode]
    relationships: list[VisualizationFocusRelationship]
    coverage: VisualizationSummary
    tools: list[dict[str, Any]]
    contexts: list[dict[str, Any]]
    collaborators: list[dict[str, Any]]
    controls: list[dict[str, Any]]
    technical: dict[str, Any]
    wide_layout: VisualizationLayout
    compact_layout: VisualizationLayout


class VisualizationPresentation(TypedDict):
    system: dict[str, Any]
    stages: list[VisualizationEvidenceStage]
    overview_agents: list[VisualizationOverviewAgent]
    overview_relationships: list[VisualizationOverviewRelationship]
    overview_wide_layout: VisualizationLayout
    overview_compact_layout: VisualizationLayout
    focus: dict[str, VisualizationFocus]
    review_notes: list[VisualizationReviewNote]


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
    "VisualizationEvidenceStage",
    "VisualizationFocus",
    "VisualizationFocusNode",
    "VisualizationFocusRelationship",
    "VisualizationGraph",
    "VisualizationLayout",
    "VisualizationNode",
    "VisualizationOverviewAgent",
    "VisualizationOverviewRelationship",
    "VisualizationPosition",
    "VisualizationPresentation",
    "VisualizationReviewNote",
    "VisualizationSummary",
    "VisualizationTruth",
    "VisualizationTruthMembership",
]
