"""Dependency-free assets for the static review page."""

# ruff: noqa: E501

STYLE_CSS = r"""
:root {
  color-scheme: light;
  --bg: #f5f7fa; --panel: #fff; --ink: #17202a; --muted: #667085;
  --line: #d9dee7; --accent: #0f766e; --accent-soft: #d9f2ee;
  --declared: #2563eb; --planned: #7c3aed; --observed: #d97706; --assured: #15803d;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.5 system-ui, sans-serif; }
header { padding: 18px 24px; background: var(--panel); border-bottom: 1px solid var(--line); }
h1, h2, h3 { margin: 0; line-height: 1.25; }
h1 { font-size: 22px; } h2 { font-size: 17px; margin-bottom: 10px; } h3 { font-size: 14px; }
.meta { color: var(--muted); margin-top: 4px; overflow-wrap: anywhere; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
button { font: inherit; border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 7px 10px; cursor: pointer; }
button[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); }
main { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 16px; padding: 16px; }
aside, section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; min-width: 0; }
.sidebar { grid-row: span 3; }
.agent-list { display: grid; gap: 6px; margin-bottom: 20px; }
.agent-list button { text-align: left; width: 100%; }
.summary { display: grid; grid-template-columns: repeat(4, minmax(110px, 1fr)); gap: 8px; }
.summary-card { border: 1px solid var(--line); border-radius: 6px; padding: 10px; }
.summary-card strong { display: block; font-size: 20px; }
.truth { display: inline-block; border-left: 3px solid; padding: 2px 6px; margin: 2px 3px 2px 0; background: #f8fafc; }
.truth.declared { border-color: var(--declared); } .truth.planned { border-color: var(--planned); }
.truth.observed { border-color: var(--observed); } .truth.assured { border-color: var(--assured); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 10px; }
.card { border: 1px solid var(--line); border-radius: 7px; padding: 12px; min-width: 0; }
.card-id, .muted { color: var(--muted); overflow-wrap: anywhere; }
.facts { margin-top: 8px; }
.fact { display: grid; grid-template-columns: minmax(90px, 30%) 1fr; gap: 8px; margin: 4px 0; }
.fact-key { color: var(--muted); overflow-wrap: anywhere; }
.fact-value { overflow-wrap: anywhere; white-space: pre-wrap; }
.edge { padding: 8px 0; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
.warning { border-left: 3px solid #b45309; color: #854d0e; padding-left: 9px; }
pre { white-space: pre-wrap; overflow: auto; background: #101828; color: #f9fafb; border-radius: 6px; padding: 12px; }
details { margin-top: 12px; }
.empty { color: var(--muted); }
@media (max-width: 850px) { main { grid-template-columns: 1fr; } .sidebar { grid-row: auto; } .summary { grid-template-columns: 1fr 1fr; } }
"""

APP_JS_TEMPLATE = r"""
const graph = __GRAPH_JSON__;
const mermaidSource = __MERMAID_JSON__;
let activeView = "all";
let activeAgent = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function printable(value) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
function factsHtml(facts) {
  const entries = Object.entries(facts || {}).filter(([key]) => key !== "present");
  if (!entries.length) return '';
  return '<div class="facts">' + entries.map(([key, value]) =>
    `<div class="fact"><div class="fact-key">${esc(key)}</div><div class="fact-value">${esc(printable(value))}</div></div>`
  ).join('') + '</div>';
}
function visibleTruth(truth) {
  return activeView === "all" ? Object.values(truth).some(v => Object.keys(v).length) : Object.keys(truth[activeView]).length > 0;
}
function relevantNode(node) {
  if (!visibleTruth(node.truth)) return false;
  if (!activeAgent) return true;
  if (node.id === activeAgent) return true;
  return graph.edges.some(edge => visibleTruth(edge.truth) &&
    ((edge.source === activeAgent && edge.target === node.id) || (edge.target === activeAgent && edge.source === node.id)));
}
function truthHtml(truth) {
  return Object.entries(truth).filter(([, facts]) => Object.keys(facts).length).map(([view, facts]) =>
    `<div class="truth ${view}"><strong>${esc(view)}</strong>${factsHtml(facts)}</div>`
  ).join('');
}
function renderSummary() {
  document.getElementById("summary").innerHTML = Object.entries(graph.summary).map(([view, count]) =>
    `<div class="summary-card"><strong>${count}</strong>${esc(view)} entities</div>`
  ).join('');
}
function renderAgents() {
  document.getElementById("agent-list").innerHTML = Object.values(graph.agents).map(agent =>
    `<button type="button" data-agent="${esc(agent.id)}" aria-pressed="${activeAgent === agent.id}">${esc(agent.name)}</button>`
  ).join('');
  document.querySelectorAll("[data-agent]").forEach(button => button.addEventListener("click", () => {
    activeAgent = activeAgent === button.dataset.agent ? null : button.dataset.agent;
    render();
  }));
}
function renderNodes() {
  const nodes = graph.nodes.filter(relevantNode);
  document.getElementById("nodes-title").textContent = `${nodes.length} entities`;
  document.getElementById("node-grid").innerHTML = nodes.map(node => `
    <article class="card">
      <h3>${esc(node.label)} <span class="muted">(${esc(node.kind)})</span></h3>
      <div class="card-id">${esc(node.id)}</div>
      ${truthHtml(node.truth)}
    </article>`).join('') || '<p class="empty">No entities in this view.</p>';
}
function renderEdges() {
  const visibleIds = new Set(graph.nodes.filter(relevantNode).map(node => node.id));
  const edges = graph.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target) && visibleTruth(edge.truth));
  document.getElementById("edges-title").textContent = `${edges.length} relationships`;
  document.getElementById("edge-list").innerHTML = edges.map(edge =>
    `<div class="edge"><strong>${esc(edge.source)}</strong> —${esc(edge.label)}→ <strong>${esc(edge.target)}</strong>${truthHtml(edge.truth)}</div>`
  ).join('') || '<p class="empty">No relationships in this view.</p>';
}
function renderWarnings() {
  document.getElementById("warnings").innerHTML = graph.warnings.map(value => `<p class="warning">${esc(value)}</p>`).join('');
}
function render() {
  document.querySelectorAll("[data-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === activeView)));
  renderAgents(); renderNodes(); renderEdges();
}
document.getElementById("project-meta").textContent = `${graph.project_root || "No project path"} · ${graph.contract_digest}`;
document.getElementById("raw-mermaid").textContent = mermaidSource;
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => { activeView = button.dataset.view; render(); }));
document.getElementById("clear-agent").addEventListener("click", () => { activeAgent = null; render(); });
renderSummary(); renderWarnings(); render();
"""

__all__ = ["APP_JS_TEMPLATE", "STYLE_CSS"]
