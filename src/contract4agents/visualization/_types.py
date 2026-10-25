"""JSON-compatible contracts for review artifacts."""

from __future__ import annotations

from typing import Literal, TypedDict

from contract4agents.ir import CanonicalJsonValue

TruthView = Literal["declared", "planned", "observed", "assured"]


class VisualizationTruth(TypedDict):
    declared: dict[str, CanonicalJsonValue]
    planned: dict[str, CanonicalJsonValue]
    observed: dict[str, CanonicalJsonValue]
    assured: dict[str, CanonicalJsonValue]


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


class VisualizationGuidance(TypedDict):
    text: str
    audience: list[str]


class VisualizationInput(TypedDict):
    name: str
    type: str
    required: bool


class VisualizationGrant(TypedDict):
    id: str
    capability_id: str
    availability: str
    authorization: str | None
    execution: str | None


class VisualizationContext(TypedDict):
    id: str
    name: str
    origin: str
    type: str
    input_mappings: dict[str, str]


class VisualizationComposition(TypedDict):
    id: str
    name: str
    direction: str
    mode: str
    other_agent_id: str


class VisualizationControl(TypedDict):
    id: str
    name: str
    severity: str
    assessment: str
    assurance: dict[str, CanonicalJsonValue]


class VisualizationQuality(TypedDict):
    id: str
    name: str
    rubric: str


class VisualizationOperationalControl(TypedDict):
    id: str
    name: str
    severity: str
    requirement: str


class VisualizationEval(TypedDict):
    id: str
    name: str
    expectations: list[str]


class VisualizationAgentDetail(TypedDict):
    id: str
    name: str
    signature: str
    goal: str
    description: str
    guidance: list[VisualizationGuidance]
    inputs: list[VisualizationInput]
    output_type: str
    grants: list[VisualizationGrant]
    contexts: list[VisualizationContext]
    composition: list[VisualizationComposition]
    controls: list[VisualizationControl]
    qualities: list[VisualizationQuality]
    operational_controls: list[VisualizationOperationalControl]
    evals: list[VisualizationEval]
    planned: dict[str, CanonicalJsonValue]
    observed: dict[str, CanonicalJsonValue]


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


class VisualizationCollaborator(TypedDict):
    id: str
    agent_id: str
    name: str
    direction: str
    mode: str
    description: str
    truth: VisualizationTruthMembership


class VisualizationTool(TypedDict):
    id: str
    grant_id: str
    name: str
    source_name: str
    kind: str
    availability: str
    authorization: str | None
    execution: str | None
    access_label: str
    approval_sensitive: bool
    planned: dict[str, CanonicalJsonValue]
    implementation: dict[str, CanonicalJsonValue]
    observed: dict[str, CanonicalJsonValue]
    truth: VisualizationTruthMembership


class VisualizationContextSource(TypedDict):
    id: str
    name: str
    source_name: str
    source_id: str | None
    source_label: str
    origin: str
    type: str
    input_mappings: dict[str, str]
    planned: dict[str, CanonicalJsonValue]
    observed: dict[str, CanonicalJsonValue]


class VisualizationControlPresentation(TypedDict):
    id: str
    name: str
    source_name: str
    status: str
    status_label: str
    summary: str
    severity: str
    required: bool
    assessment: CanonicalJsonValue
    requirement: CanonicalJsonValue
    expected_evidence: CanonicalJsonValue
    planned: dict[str, CanonicalJsonValue]
    assurance: dict[str, CanonicalJsonValue]
    truth: VisualizationTruthMembership


class VisualizationTechnical(TypedDict):
    semantic_id: str
    source_name: str
    signature: str
    inputs: list[VisualizationInput]
    output_type: str
    goal: str
    description: str
    guidance: list[VisualizationGuidance]
    planned: dict[str, CanonicalJsonValue]
    observed: dict[str, CanonicalJsonValue]
    tools: list[VisualizationTool]
    contexts: list[VisualizationContextSource]
    controls: list[VisualizationControlPresentation]


class VisualizationSystem(TypedDict):
    name: str
    summary: str
    agent_count: int
    composition_count: int
    semantic_entity_count: int
    semantic_relationship_count: int
    coordinator_id: str | None
    coordinator_name: str | None
    contract_digest: str
    plan_digest: str | None
    project_root: str | None


class VisualizationFocus(TypedDict):
    agent_id: str
    name: str
    purpose: str
    output_type: str
    assurance: str
    nodes: list[VisualizationFocusNode]
    relationships: list[VisualizationFocusRelationship]
    coverage: VisualizationSummary
    tools: list[VisualizationTool]
    contexts: list[VisualizationContextSource]
    collaborators: list[VisualizationCollaborator]
    controls: list[VisualizationControlPresentation]
    technical: VisualizationTechnical
    wide_layout: VisualizationLayout
    compact_layout: VisualizationLayout


class VisualizationPresentation(TypedDict):
    system: VisualizationSystem
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
