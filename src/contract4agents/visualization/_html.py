"""Self-contained HTML rendering for Contract4Agents visualization graphs."""

from __future__ import annotations

import json

from contract4agents.visualization._html_assets import APP_JS_TEMPLATE, STYLE_CSS
from contract4agents.visualization._mermaid import render_agent_mermaid
from contract4agents.visualization._types import VisualizationGraph


def render_html(graph: VisualizationGraph, mermaid: str) -> str:
    """Render a self-contained static HTML review page."""
    diagrams = _diagrams(graph, mermaid)
    app_js = _app_js(_script_json(graph), _script_json(diagrams))
    title = "Contract4Agents Visualization"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
{STYLE_CSS}
  </style>
</head>
<body>
  <header>
    <h1>Contract4Agents Visualization</h1>
    <div class="meta" id="project-root"></div>
  </header>
  <main>
    <aside class="sidebar">
      <h2>Project</h2>
      <div class="counts" id="counts"></div>
      <h2>Agents</h2>
      <div class="list" id="agent-list"></div>
      <h2>Warnings</h2>
      <div id="warnings"></div>
    </aside>
    <section class="diagram">
      <div class="diagram-header">
        <div>
          <h2>Diagram</h2>
          <div class="diagram-title" id="diagram-title">Overview</div>
        </div>
        <button class="secondary" id="overview-button" type="button">Overview</button>
      </div>
      <div class="mermaid" id="diagram"></div>
      <div class="fallback" id="fallback">
        <p class="warning">Mermaid did not render. Raw diagram source is shown below.</p>
        <pre id="raw-mermaid"></pre>
      </div>
    </section>
    <aside class="details">
      <h2>Agent Detail</h2>
      <div id="agent-detail" class="empty">Select an agent.</div>
    </aside>
    <section class="breakdown" id="breakdown">
      <div class="breakdown-header">
        <div>
          <h2 id="breakdown-title">Breakdown</h2>
          <div class="diagram-title" id="breakdown-subtitle"></div>
        </div>
        <button class="secondary" id="breakdown-back" type="button">Back to overview</button>
      </div>
      <div class="breakdown-grid" id="breakdown-grid"></div>
    </section>
  </main>
  <script>
{app_js}
  </script>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
    mermaid.initialize({{ startOnLoad: false, securityLevel: 'strict' }});
    window.contractMermaid = mermaid;
    window.dispatchEvent(new Event('contract-mermaid-ready'));
  </script>
  <script>
    window.addEventListener('contract-mermaid-ready', () => renderDiagram(currentDiagramKey), {{ once: true }});
    window.addEventListener('error', renderDiagramFallback, {{ once: true }});
    renderDiagram("overview");
  </script>
</body>
</html>
"""


def _diagrams(graph: VisualizationGraph, mermaid: str) -> dict[str, object]:
    return {
        "overview": mermaid,
        "agents": {agent_name: render_agent_mermaid(graph, agent_name) for agent_name in sorted(graph["agents"])},
    }


def _app_js(graph_json: str, diagrams_json: str) -> str:
    return APP_JS_TEMPLATE.replace("__GRAPH_JSON__", graph_json).replace("__DIAGRAMS_JSON__", diagrams_json)


def _script_json(value: object) -> str:
    return json.dumps(value, sort_keys=True).replace("</", "<\\/")
