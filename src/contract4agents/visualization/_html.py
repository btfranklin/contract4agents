"""Render a dependency-free, self-contained system review page."""

# ruff: noqa: E501

from __future__ import annotations

import json

from contract4agents.visualization._html_assets import APP_JS_TEMPLATE, STYLE_CSS
from contract4agents.visualization._presentation import build_visualization_presentation
from contract4agents.visualization._types import VisualizationGraph


def render_html(graph: VisualizationGraph, mermaid: str) -> str:
    """Embed the semantic graph, presentation model, behavior, and styling."""

    presentation = build_visualization_presentation(graph)
    system = presentation["system"]
    script = (
        APP_JS_TEMPLATE.replace("__GRAPH_JSON__", _script_json(graph))
        .replace("__PRESENTATION_JSON__", _script_json(presentation))
        .replace("__MERMAID_JSON__", _script_json(mermaid))
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>{_html_text(str(system["name"]))} · Contract4Agents system review</title>
  <style>{STYLE_CSS}</style>
</head>
<body>
  <header class="site-header">
    <p class="eyebrow">Contract4Agents system review</p>
    <div class="heading-row">
      <div>
        <h1 id="system-title">{_html_text(str(system["name"]))}</h1>
        <p class="system-summary">{_html_text(str(system["summary"]))}</p>
      </div>
      <p class="digest" title="Contract digest">{_html_text(str(system["contract_digest"]))}</p>
    </div>
    <nav class="evidence-nav" aria-label="Evidence progression">
      <button type="button" class="stage-button" data-view="overview" aria-pressed="true">
        <span class="stage-index">00</span><span>System</span><small>Design overview</small>
      </button>
      <span class="stage-connector" aria-hidden="true"></span>
      <div id="stage-buttons" class="stage-buttons"></div>
      <details class="evidence-help">
        <summary aria-label="About evidence progression">?</summary>
        <p>Move from contract intent to resolved runtime, observed behavior, and assessed controls. Missing evidence keeps the system visible and is explained in place.</p>
      </details>
    </nav>
  </header>
  <main
    data-semantic-relationship-count="{system["semantic_relationship_count"]}"
    data-overview-relationship-count="{system["composition_count"]}"
  >
    <section class="workspace" aria-labelledby="workspace-title">
      <div class="workspace-heading">
        <div>
          <p class="section-kicker" id="workspace-kicker">System map</p>
          <h2 id="workspace-title">Declared agent team</h2>
        </div>
        <button type="button" class="quiet-button" id="show-system" hidden>Show whole system</button>
      </div>
      <div class="canvas-shell" id="canvas-shell">
        <svg
          id="system-canvas"
          class="system-canvas"
          role="img"
          aria-labelledby="canvas-title canvas-description"
          preserveAspectRatio="xMinYMin meet"
        >
          <title id="canvas-title">Agent system map</title>
          <desc id="canvas-description">Select an agent to inspect its capabilities, context, collaborators, and controls.</desc>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z"></path>
            </marker>
          </defs>
          <g id="relationship-layer"></g>
          <g id="node-layer"></g>
        </svg>
        <div id="stage-empty" class="stage-empty" hidden role="status"></div>
      </div>
    </section>
    <aside class="inspector" id="inspector" aria-labelledby="inspector-title"></aside>
  </main>
  <section class="review-strip" id="review-strip" aria-live="polite" aria-label="Priority review note"></section>
  <details class="technical-details">
    <summary>Technical details</summary>
    <div class="technical-grid">
      <div><span>Contract digest</span><code>{_html_text(str(system["contract_digest"]))}</code></div>
      <div><span>Plan digest</span><code>{_html_text(str(system["plan_digest"] or "Not supplied"))}</code></div>
      <div><span>Semantic entities</span><strong>{system["semantic_entity_count"]}</strong></div>
      <div><span>Semantic relationships</span><strong>{system["semantic_relationship_count"]}</strong></div>
    </div>
    <details class="mermaid-details"><summary>Mermaid source</summary><pre id="raw-mermaid"></pre></details>
  </details>
  <script>{script}</script>
</body>
</html>
"""


def _script_json(value: object) -> str:
    return json.dumps(value, sort_keys=True).replace("</", "<\\/")


def _html_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


__all__ = ["render_html"]
