"""Artifact writing for Contract4Agents static visualizations."""

from __future__ import annotations

import json
from pathlib import Path

from contract4agents.visualization._html import render_html
from contract4agents.visualization._mermaid import render_mermaid
from contract4agents.visualization._types import VisualizationGraph


def write_visualization_artifacts(graph: VisualizationGraph, output_dir: Path) -> None:
    """Write graph JSON, Mermaid, and static HTML visualization artifacts."""
    mermaid = render_mermaid(graph)
    files = {
        output_dir / "graph.json": json.dumps(graph, indent=2, sort_keys=True) + "\n",
        output_dir / "graph.mmd": mermaid,
        output_dir / "index.html": render_html(graph, mermaid),
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
