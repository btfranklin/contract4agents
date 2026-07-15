"""Render a dependency-free, self-contained V2 review page."""

from __future__ import annotations

import json

from contract4agents.visualization._html_assets import APP_JS_TEMPLATE, STYLE_CSS
from contract4agents.visualization._types import VisualizationGraph


def render_html(graph: VisualizationGraph, mermaid: str) -> str:
    """Embed all graph data, behavior, styling, and Mermaid source in one file."""

    script = APP_JS_TEMPLATE.replace("__GRAPH_JSON__", _script_json(graph)).replace(
        "__MERMAID_JSON__", _script_json(mermaid)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Contract4Agents Truth Review</title>
  <style>{STYLE_CSS}</style>
</head>
<body>
  <header>
    <h1>Contract4Agents Truth Review</h1>
    <div class="meta" id="project-meta"></div>
    <div class="toolbar" aria-label="Truth layer">
      <button type="button" data-view="all" aria-pressed="true">All layers</button>
      <button type="button" data-view="declared" aria-pressed="false">Declared</button>
      <button type="button" data-view="planned" aria-pressed="false">Planned</button>
      <button type="button" data-view="observed" aria-pressed="false">Observed</button>
      <button type="button" data-view="assured" aria-pressed="false">Assured</button>
    </div>
  </header>
  <main>
    <aside class="sidebar">
      <h2>Agents</h2>
      <div class="agent-list" id="agent-list"></div>
      <button id="clear-agent" type="button">Show whole system</button>
      <h2 style="margin-top:20px">Warnings</h2>
      <div id="warnings"></div>
    </aside>
    <section><h2>Truth coverage</h2><div class="summary" id="summary"></div></section>
    <section><h2 id="nodes-title">Entities</h2><div class="grid" id="node-grid"></div></section>
    <section>
      <h2 id="edges-title">Relationships</h2><div id="edge-list"></div>
      <details><summary>Mermaid source</summary><pre id="raw-mermaid"></pre></details>
    </section>
  </main>
  <script>{script}</script>
</body>
</html>
"""


def _script_json(value: object) -> str:
    return json.dumps(value, sort_keys=True).replace("</", "<\\/")


__all__ = ["render_html"]
