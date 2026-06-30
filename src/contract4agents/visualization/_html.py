"""Self-contained HTML rendering for Contract4Agents visualization graphs."""

from __future__ import annotations

import json

from contract4agents.visualization._mermaid import render_agent_mermaid
from contract4agents.visualization._types import VisualizationGraph


def render_html(graph: VisualizationGraph, mermaid: str) -> str:
    """Render a self-contained static HTML review page."""
    diagrams = {
        "overview": mermaid,
        "agents": {agent_name: render_agent_mermaid(graph, agent_name) for agent_name in sorted(graph["agents"])},
    }
    graph_json = _script_json(graph)
    diagrams_json = _script_json(diagrams)
    title = "Contract4Agents Visualization"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #0f766e;
      --accent-soft: #d9f2ee;
      --warn: #8a5a00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      padding: 16px 24px;
    }}
    h1, h2, h3 {{ margin: 0; line-height: 1.2; }}
    h1 {{ font-size: 22px; }}
    h2 {{ font-size: 18px; margin-bottom: 12px; }}
    h3 {{ font-size: 14px; margin: 18px 0 8px; }}
    .meta {{ color: var(--muted); margin-top: 4px; }}
    main {{
      display: grid;
      grid-template-columns: minmax(220px, 280px) minmax(0, 1fr) minmax(300px, 420px);
      gap: 16px;
      padding: 16px;
    }}
    section, aside {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }}
    .sidebar, .details {{ padding: 16px; }}
    .diagram {{ padding: 16px; overflow: auto; }}
    .diagram-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .diagram-header h2 {{ margin-bottom: 0; }}
    .diagram-title {{ color: var(--muted); }}
    .list {{ display: grid; gap: 8px; }}
    .breakdown {{
      display: none;
      grid-column: 2 / 4;
      padding: 16px;
      overflow: auto;
    }}
    .breakdown-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .breakdown-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
    }}
    .breakdown-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      min-width: 0;
    }}
    .breakdown-card h3 {{
      margin-top: 0;
      overflow-wrap: anywhere;
    }}
    .breakdown-card code {{
      overflow-wrap: anywhere;
    }}
    .field {{
      display: grid;
      grid-template-columns: 86px minmax(0, 1fr);
      gap: 8px;
      margin: 6px 0;
    }}
    .field-label {{
      color: var(--muted);
    }}
    button {{
      font: inherit;
    }}
    button.agent, button.secondary, button.count {{
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      padding: 8px 10px;
      text-align: left;
      color: var(--ink);
      cursor: pointer;
    }}
    button.secondary {{
      width: auto;
      white-space: nowrap;
    }}
    button.count {{
      display: block;
      min-height: 64px;
    }}
    button.agent[aria-pressed="true"] {{
      border-color: var(--accent);
      background: var(--accent-soft);
    }}
    button.count[aria-pressed="true"] {{
      border-color: var(--accent);
      background: var(--accent-soft);
    }}
    .counts {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 18px;
    }}
    .count {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
    }}
    .count strong {{ display: block; font-size: 18px; }}
    .tag {{
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      margin: 2px 4px 2px 0;
      color: var(--muted);
      background: #fff;
    }}
    .warning {{
      border-left: 3px solid var(--warn);
      color: var(--warn);
      padding-left: 10px;
      margin: 8px 0;
    }}
    ul {{ padding-left: 18px; margin: 8px 0; }}
    pre {{
      white-space: pre-wrap;
      background: #101828;
      color: #f9fafb;
      border-radius: 6px;
      padding: 12px;
      overflow: auto;
    }}
    .fallback {{ display: none; }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 1000px) {{
      main {{ grid-template-columns: 1fr; }}
      .breakdown {{ grid-column: 1; }}
    }}
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
    const graph = {graph_json};
    const diagrams = {diagrams_json};
    let currentDiagramKey = "overview";
    let renderSequence = 0;

    function esc(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\\"": "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function list(items) {{
      if (!items || items.length === 0) return '<p class="empty">None.</p>';
      return '<ul>' + items.map((item) => `<li>${{esc(item)}}</li>`).join('') + '</ul>';
    }}

    function objectTags(items, labelKey = "name") {{
      if (!items || items.length === 0) return '<p class="empty">None.</p>';
      return items.map((item) => {{
        const label = item[labelKey] ?? item.name ?? "";
        const suffix = item.permission ? ` (${{item.permission}})` : "";
        return `<span class="tag">${{esc(label + suffix)}}</span>`;
      }}).join('');
    }}

    function pluralKind(kind, count) {{
      const labels = {{
        agent: ["agent", "agents"],
        datasource: ["datasource", "datasources"],
        eval: ["eval", "evals"],
        monitor: ["monitor", "monitors"],
        tool: ["tool", "tools"],
        type: ["type", "types"]
      }};
      const pair = labels[kind] || [kind, `${{kind}}s`];
      return count === 1 ? pair[0] : pair[1];
    }}

    function metadataRows(metadata) {{
      const entries = Object.entries(metadata || {{}})
        .filter(([, value]) => value !== null && value !== undefined && value !== "");
      if (entries.length === 0) return '<p class="empty">No metadata.</p>';
      return entries.map(([key, value]) => `
        <div class="field">
          <div class="field-label">${{esc(key)}}</div>
          <div>${{esc(Array.isArray(value) ? value.join(", ") : value)}}</div>
        </div>
      `).join('');
    }}

    function nodeRelationships(nodeId) {{
      const related = graph.edges
        .filter((edge) => edge.source === nodeId || edge.target === nodeId)
        .map((edge) => {{
          const direction = edge.source === nodeId ? "to" : "from";
          const otherId = edge.source === nodeId ? edge.target : edge.source;
          const other = graph.nodes.find((node) => node.id === otherId);
          const otherLabel = other ? `${{other.label}} (${{other.kind}})` : otherId;
          return `${{edge.label}} ${{direction}} ${{otherLabel}}`;
        }});
      return related.length ? list(related) : '<p class="empty">No relationships.</p>';
    }}

    function agentSummary(node) {{
      if (node.kind !== "agent" || !graph.agents[node.label]) return "";
      const agent = graph.agents[node.label];
      return `
        <h3>Contract4Agents</h3>
        <div class="field">
          <div class="field-label">signature</div><div><code>${{esc(agent.signature)}}</code></div>
        </div>
        <div class="field">
          <div class="field-label">goal</div><div>${{esc(agent.goal || "No goal declared.")}}</div>
        </div>
        <div class="field"><div class="field-label">tools</div><div>${{objectTags(agent.tools)}}</div></div>
      `;
    }}

    function renderCounts() {{
      const byKind = graph.nodes.reduce((acc, node) => {{
        acc[node.kind] = (acc[node.kind] || 0) + 1;
        return acc;
      }}, {{}});
      document.getElementById('counts').innerHTML = Object.entries(byKind)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([kind, count]) => `
          <button class="count" type="button" data-kind="${{esc(kind)}}" aria-pressed="false">
            <strong>${{count}}</strong>${{esc(pluralKind(kind, count))}}
          </button>
        `)
        .join('');
      document.querySelectorAll('button.count').forEach((button) => {{
        button.addEventListener('click', () => renderBreakdown(button.dataset.kind));
      }});
    }}

    function renderAgentList(selected) {{
      const names = Object.keys(graph.agents).sort();
      document.getElementById('agent-list').innerHTML = names.map((name) => `
        <button class="agent" type="button" aria-pressed="${{name === selected}}" data-agent="${{esc(name)}}">
          ${{esc(name)}}
        </button>
      `).join('');
      document.querySelectorAll('button.agent').forEach((button) => {{
        button.addEventListener('click', () => selectAgent(button.dataset.agent));
      }});
    }}

    function selectAgent(name) {{
      const agent = graph.agents[name];
      if (!agent) return;
      showOverviewPanels();
      renderAgentList(name);
      renderDiagram(`agent:${{name}}`);
      document.getElementById('agent-detail').classList.remove('empty');
      document.getElementById('agent-detail').innerHTML = `
        <h3>Signature</h3>
        <p><code>${{esc(agent.signature)}}</code></p>
        <h3>Goal</h3>
        <p>${{esc(agent.goal || "No goal declared.")}}</p>
        <h3>Description</h3>
        <p>${{esc(agent.description || "No description declared.")}}</p>
        <h3>Inputs</h3>
        ${{objectTags(agent.inputs, "name")}}
        <h3>Output</h3>
        <p><span class="tag">${{esc(agent.output.type)}}</span></p>
        <h3>Tools</h3>
        ${{objectTags(agent.tools)}}
        <h3>Subagents</h3>
        ${{objectTags(agent.subagents)}}
        <h3>Datasources</h3>
        ${{objectTags(agent.datasources)}}
        <h3>Policy</h3>
        ${{list(agent.policy)}}
        <h3>Success</h3>
        ${{list(agent.success)}}
        <h3>Routes</h3>
        ${{list(agent.routes)}}
        <h3>Composition</h3>
        ${{list(agent.composition)}}
        <h3>Guards</h3>
        ${{list(agent.guards)}}
        <h3>Assertions</h3>
        ${{list(agent.assertions)}}
        <h3>Evals</h3>
        ${{objectTags(agent.evals)}}
        <h3>Monitors</h3>
        ${{objectTags(agent.monitors)}}
      `;
    }}

    function renderBreakdown(kind) {{
      if (!kind) return;
      const nodes = graph.nodes
        .filter((node) => node.kind === kind)
        .sort((a, b) => a.label.localeCompare(b.label));
      document.querySelectorAll('button.count').forEach((button) => {{
        button.setAttribute('aria-pressed', String(button.dataset.kind === kind));
      }});
      document.getElementById('diagram-title').textContent = "Overview";
      document.getElementById('diagram').style.display = 'none';
      document.querySelector('.diagram').style.display = 'none';
      document.querySelector('.details').style.display = 'none';
      document.getElementById('breakdown').style.display = 'block';
      document.getElementById('breakdown-title').textContent = `${{nodes.length}} ${{pluralKind(kind, nodes.length)}}`;
      document.getElementById('breakdown-subtitle').textContent = `Detail breakdown for ${{kind}} nodes`;
      document.getElementById('breakdown-grid').innerHTML = nodes.map((node) => `
        <article class="breakdown-card">
          <h3>${{esc(node.label)}}</h3>
          <div class="field"><div class="field-label">id</div><div><code>${{esc(node.id)}}</code></div></div>
          <div class="field"><div class="field-label">kind</div><div>${{esc(node.kind)}}</div></div>
          ${{metadataRows(node.metadata)}}
          ${{agentSummary(node)}}
          <h3>Relationships</h3>
          ${{nodeRelationships(node.id)}}
        </article>
      `).join('');
    }}

    function showOverviewPanels() {{
      document.querySelector('.diagram').style.display = 'block';
      document.querySelector('.details').style.display = 'block';
      document.getElementById('diagram').style.display = 'block';
      document.getElementById('breakdown').style.display = 'none';
      document.querySelectorAll('button.count').forEach((button) => {{
        button.setAttribute('aria-pressed', "false");
      }});
    }}

    function renderWarnings() {{
      document.getElementById('warnings').innerHTML = graph.warnings
        .map((warning) => `<p class="warning">${{esc(warning)}}</p>`)
        .join('');
    }}

    function renderDiagramFallback() {{
      document.getElementById('fallback').style.display = 'block';
      document.getElementById('raw-mermaid').textContent = currentDiagramSource();
    }}

    function currentDiagramSource() {{
      if (currentDiagramKey === "overview") return diagrams.overview;
      const agentName = currentDiagramKey.replace(/^agent:/, "");
      return diagrams.agents[agentName] || diagrams.overview;
    }}

    async function renderDiagram(key) {{
      currentDiagramKey = key;
      const source = currentDiagramSource();
      const title = key === "overview" ? "Overview" : `Focused: ${{key.replace(/^agent:/, "")}}`;
      const diagram = document.getElementById('diagram');
      const fallback = document.getElementById('fallback');
      const sequence = ++renderSequence;
      document.getElementById('diagram-title').textContent = title;
      document.getElementById('raw-mermaid').textContent = source;
      fallback.style.display = 'none';
      if (!window.contractMermaid) {{
        diagram.textContent = source;
        return;
      }}
      try {{
        const result = await window.contractMermaid.render(`contract-diagram-${{sequence}}`, source);
        if (sequence !== renderSequence) return;
        diagram.innerHTML = result.svg;
        if (result.bindFunctions) result.bindFunctions(diagram);
      }} catch (_error) {{
        if (sequence !== renderSequence) return;
        diagram.textContent = source;
        renderDiagramFallback();
      }}
    }}

    document.getElementById('project-root').textContent = graph.project_root;
    document.getElementById('overview-button').addEventListener('click', () => {{
      restoreOverview();
    }});
    document.getElementById('breakdown-back').addEventListener('click', () => {{
      restoreOverview();
    }});
    function restoreOverview() {{
      showOverviewPanels();
      renderAgentList("");
      document.getElementById('agent-detail').classList.add('empty');
      document.getElementById('agent-detail').textContent = "Select an agent.";
      renderDiagram("overview");
    }}
    renderCounts();
    renderAgentList("");
    renderWarnings();
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


def _script_json(value: object) -> str:
    return json.dumps(value, sort_keys=True).replace("</", "<\\/")
