"""Typed contracts for static visualization artifacts."""

from __future__ import annotations

from typing import Any, TypedDict


class VisualizationNode(TypedDict):
    id: str
    kind: str
    label: str
    metadata: dict[str, Any]


class VisualizationEdge(TypedDict):
    id: str
    source: str
    target: str
    kind: str
    label: str
    metadata: dict[str, Any]


class VisualizationAgentEval(TypedDict):
    name: str
    expects: list[str]


class VisualizationAgentMonitor(TypedDict):
    name: str
    severity: str


class VisualizationAgentDetail(TypedDict):
    name: str
    signature: str
    goal: str
    description: str
    inputs: list[dict[str, Any]]
    output: dict[str, Any]
    tools: list[dict[str, Any]]
    subagents: list[dict[str, Any]]
    datasources: list[dict[str, Any]]
    policy: list[str]
    success: list[str]
    routes: list[str]
    composition: list[str]
    guards: list[str]
    assertions: list[str]
    evals: list[VisualizationAgentEval]
    monitors: list[VisualizationAgentMonitor]


class VisualizationGraph(TypedDict):
    version: str
    project_root: str
    nodes: list[VisualizationNode]
    edges: list[VisualizationEdge]
    agents: dict[str, VisualizationAgentDetail]
    warnings: list[str]
