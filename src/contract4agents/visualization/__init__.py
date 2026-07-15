"""Public visualization API for Contract4Agents projects."""

from __future__ import annotations

from contract4agents.visualization._artifacts import write_visualization_artifacts
from contract4agents.visualization._graph import build_visualization_graph
from contract4agents.visualization._html import render_html
from contract4agents.visualization._mermaid import render_agent_mermaid, render_mermaid
from contract4agents.visualization._types import (
    VisualizationAgentDetail,
    VisualizationEdge,
    VisualizationGraph,
    VisualizationNode,
    VisualizationSummary,
    VisualizationTruth,
)

__all__ = [
    "VisualizationAgentDetail",
    "VisualizationEdge",
    "VisualizationGraph",
    "VisualizationNode",
    "VisualizationSummary",
    "VisualizationTruth",
    "build_visualization_graph",
    "render_agent_mermaid",
    "render_html",
    "render_mermaid",
    "write_visualization_artifacts",
]
