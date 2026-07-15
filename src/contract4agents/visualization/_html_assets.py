"""Dependency-free assets for the static system review page."""

# ruff: noqa: E501

STYLE_CSS = r"""
:root {
  color-scheme: light;
  --paper: #f7f5f0;
  --panel: #fffefb;
  --ink: #1f2927;
  --muted: #66716d;
  --quiet: #8b9691;
  --line: #dcded8;
  --line-strong: #b9c1bc;
  --accent: #0d756c;
  --accent-dark: #09574f;
  --accent-soft: #e4f1ee;
  --amber: #a85f13;
  --amber-soft: #fff1dd;
  --red: #a13c38;
  --red-soft: #fbe9e6;
  --green: #397159;
  --green-soft: #e8f3ec;
  --shadow: 0 14px 40px rgba(34, 51, 46, .07);
}
* { box-sizing: border-box; }
html { background: var(--paper); }
body { margin: 0; background: var(--paper); color: var(--ink); font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
button, summary { font: inherit; }
button { color: inherit; }
button:focus-visible, summary:focus-visible { outline: 3px solid rgba(13, 117, 108, .25); outline-offset: 2px; }
.site-header { padding: 30px clamp(18px, 4vw, 56px) 0; background: var(--panel); border-bottom: 1px solid var(--line); }
.eyebrow, .section-kicker { margin: 0 0 5px; color: var(--accent); font-size: 11px; font-weight: 750; letter-spacing: .13em; text-transform: uppercase; }
.heading-row { display: flex; align-items: end; justify-content: space-between; gap: 24px; }
h1, h2, h3, p { overflow-wrap: anywhere; }
h1, h2, h3 { margin: 0; line-height: 1.18; }
h1 { font-size: 28px; font-weight: 720; letter-spacing: -.02em; }
h2 { font-size: 18px; }
h3 { font-size: 14px; }
.system-summary { margin: 6px 0 0; color: var(--muted); }
.digest { max-width: 310px; margin: 0 0 4px; color: var(--quiet); font: 11px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace; text-align: right; }
.evidence-nav { display: flex; align-items: stretch; gap: 0; margin-top: 28px; overflow-x: auto; scrollbar-width: thin; }
.stage-buttons { display: flex; align-items: stretch; gap: 0; }
.stage-button { position: relative; min-width: 128px; padding: 12px 15px 14px; border: 0; border-top: 2px solid transparent; background: transparent; cursor: pointer; text-align: left; }
.stage-button:hover { background: #f6faf8; }
.stage-button[aria-pressed="true"] { border-top-color: var(--accent); background: var(--accent-soft); }
.stage-button[data-available="false"] { color: var(--quiet); }
.stage-index { display: inline-block; min-width: 25px; color: var(--accent); font: 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace; }
.stage-button span:not(.stage-index) { font-weight: 720; }
.stage-button small { display: block; margin: 3px 0 0 25px; color: var(--muted); font-size: 10px; white-space: nowrap; }
.stage-count { float: right; margin-left: 8px; color: var(--muted); font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
.stage-connector { align-self: center; width: 18px; height: 1px; background: var(--line-strong); }
.stage-buttons .stage-button + .stage-button::before { position: absolute; top: 50%; left: -4px; width: 8px; height: 8px; border-top: 1px solid var(--line-strong); border-right: 1px solid var(--line-strong); content: ""; transform: rotate(45deg); }
.evidence-help { align-self: center; margin-left: auto; padding-left: 16px; }
.evidence-help summary { display: grid; width: 26px; height: 26px; border: 1px solid var(--line); border-radius: 50%; cursor: pointer; list-style: none; place-items: center; }
.evidence-help summary::-webkit-details-marker { display: none; }
.evidence-help p { position: absolute; z-index: 5; right: 32px; max-width: 330px; margin: 7px 0 0; padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); box-shadow: var(--shadow); color: var(--muted); }
main { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 18px; max-width: 1500px; margin: 0 auto; padding: 24px clamp(18px, 4vw, 56px) 18px; }
.workspace, .inspector, .technical-details { min-width: 0; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); box-shadow: var(--shadow); }
.workspace { padding: 18px; }
.workspace-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 13px; }
.quiet-button { border: 1px solid var(--line); border-radius: 7px; background: transparent; padding: 7px 10px; cursor: pointer; }
.quiet-button:hover { border-color: var(--accent); color: var(--accent-dark); }
.canvas-shell { position: relative; min-height: 420px; overflow: auto; border: 1px solid #e3e4df; border-radius: 9px; background-color: #fbfaf7; background-image: radial-gradient(#cfd5d1 0.65px, transparent 0.65px); background-size: 18px 18px; }
.system-canvas { display: block; height: auto; margin: 0 auto; transition: opacity 150ms ease; }
.relationship { fill: none; stroke: #aab5b0; stroke-width: 1.5; marker-end: url(#arrow); transition: opacity 150ms ease, stroke 150ms ease, stroke-width 150ms ease; }
.relationship.handoff { stroke-dasharray: 7 5; }
.relationship.active { stroke: var(--accent); stroke-width: 2.3; }
.relationship.muted { opacity: .2; }
.relationship-label { fill: var(--muted); font-size: 10px; paint-order: stroke; stroke: #fbfaf7; stroke-width: 5px; stroke-linejoin: round; }
.relationship-label.muted { opacity: .28; }
.node-card { width: 100%; height: 100%; overflow: hidden; border: 1px solid var(--line-strong); border-radius: 9px; background: rgba(255, 254, 251, .97); box-shadow: 0 6px 16px rgba(39, 56, 51, .08); color: var(--ink); text-align: left; transition: opacity 150ms ease, border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease; }
button.node-card { display: block; padding: 12px 14px; cursor: pointer; }
div.node-card { padding: 11px 13px; }
.node-card:hover, .node-card.selected { border-color: var(--accent); box-shadow: 0 8px 22px rgba(13, 117, 108, .14); }
.node-card.selected { background: #f3faf8; }
.node-card.muted { opacity: .28; }
.node-card.primary { border-left: 4px solid var(--accent); }
.node-kind { display: block; margin-bottom: 4px; color: var(--accent); font-size: 9px; font-weight: 780; letter-spacing: .1em; text-transform: uppercase; }
.node-name { display: block; overflow: hidden; font-size: 14px; font-weight: 750; text-overflow: ellipsis; white-space: nowrap; }
.node-meta { display: block; overflow: hidden; margin-top: 4px; color: var(--muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.node-summary { display: block; overflow: hidden; margin-top: 7px; color: var(--quiet); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.node-status { display: inline-block; margin-top: 8px; padding: 2px 6px; border-radius: 999px; background: #eef0ed; color: var(--muted); font-size: 9px; font-weight: 720; }
.node-status.passed { background: var(--green-soft); color: var(--green); }
.node-status.violated { background: var(--red-soft); color: var(--red); }
.node-status.unverified, .node-status.unsupported, .node-status.approval { background: var(--amber-soft); color: var(--amber); }
.stage-empty { position: sticky; z-index: 4; bottom: 18px; width: min(440px, calc(100% - 28px)); margin: -96px auto 16px; padding: 12px 15px; border: 1px solid #d7c5a7; border-radius: 9px; background: rgba(255, 248, 236, .96); box-shadow: 0 8px 25px rgba(80, 61, 30, .09); }
.stage-empty strong { display: block; }
.stage-empty p { margin: 3px 0 0; color: #755d3d; font-size: 12px; }
.inspector { align-self: start; padding: 20px; }
.inspector-kicker { margin: 0 0 4px; color: var(--accent); font-size: 10px; font-weight: 760; letter-spacing: .1em; text-transform: uppercase; }
.inspector h2 { font-size: 23px; font-weight: 720; letter-spacing: -.015em; }
.inspector-purpose { margin: 8px 0 15px; color: var(--muted); }
.inspector-section { padding: 14px 0; border-top: 1px solid var(--line); }
.inspector-section h3 { margin-bottom: 8px; }
.inspector-list { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.inspector-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: start; }
.inspector-item span:first-child { min-width: 0; overflow-wrap: anywhere; }
.item-meta { color: var(--muted); font-size: 11px; text-align: right; }
.pill { display: inline-block; padding: 2px 7px; border-radius: 999px; background: #eef1ef; color: var(--muted); font-size: 10px; font-weight: 720; white-space: nowrap; }
.pill.approval, .pill.unverified, .pill.unsupported { background: var(--amber-soft); color: var(--amber); }
.pill.passed { background: var(--green-soft); color: var(--green); }
.pill.violated { background: var(--red-soft); color: var(--red); }
.agent-jump { width: 100%; border: 1px solid var(--line); border-radius: 7px; background: transparent; padding: 8px 9px; cursor: pointer; text-align: left; }
.agent-jump:hover { border-color: var(--accent); background: var(--accent-soft); }
.control-button { width: 100%; border: 0; background: transparent; padding: 0; cursor: pointer; text-align: left; }
.control-explanation { margin: 8px 0 0; padding: 11px 12px; border-left: 3px solid var(--accent); background: #f3f8f6; }
.control-explanation p { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.technical-agent { margin-top: 7px; color: var(--muted); }
.technical-agent summary { cursor: pointer; }
.technical-agent pre { max-height: 270px; }
.review-strip { display: none; max-width: calc(1500px - 2 * clamp(18px, 4vw, 56px)); margin: 0 auto 18px; padding: 0 clamp(18px, 4vw, 56px); }
.review-strip.has-note { display: block; }
.review-note { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 13px; align-items: center; padding: 14px 16px; border: 1px solid #d8c5a3; border-radius: 10px; background: #fff9ed; }
.review-mark { display: grid; width: 28px; height: 28px; border-radius: 50%; background: var(--amber-soft); color: var(--amber); font-weight: 800; place-items: center; }
.review-note strong { display: block; }
.review-note p { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
.review-action { max-width: 280px; color: #755d3d; font-size: 11px; text-align: right; }
.technical-details { max-width: calc(1500px - 2 * clamp(18px, 4vw, 56px)); margin: 0 auto 36px; padding: 14px 18px; box-shadow: none; }
.technical-details > summary, .mermaid-details > summary { cursor: pointer; font-weight: 700; }
.technical-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-top: 15px; }
.technical-grid div { min-width: 0; }
.technical-grid span { display: block; color: var(--muted); font-size: 10px; text-transform: uppercase; }
.technical-grid code { display: block; overflow-wrap: anywhere; font-size: 10px; }
.mermaid-details { margin-top: 15px; }
pre { overflow: auto; padding: 12px; border-radius: 7px; background: #202a28; color: #eff5f2; font: 11px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; }
.empty-copy { color: var(--muted); font-style: italic; }
@media (max-width: 900px) {
  .digest { display: none; }
  main { grid-template-columns: 1fr; }
  .inspector { order: 2; }
  .technical-grid { grid-template-columns: 1fr 1fr; }
  .review-note { grid-template-columns: auto minmax(0, 1fr); }
  .review-action { grid-column: 2; max-width: none; text-align: left; }
}
@media (max-width: 520px) {
  .site-header { padding-top: 21px; }
  .heading-row { display: block; }
  h1 { font-size: 25px; }
  .evidence-nav { margin-right: -18px; margin-left: -18px; padding-left: 10px; }
  .stage-button { min-width: 108px; padding-right: 10px; padding-left: 10px; }
  .stage-button small { display: none; }
  .evidence-help { display: none; }
  main { padding-top: 16px; }
  .workspace { padding: 12px; }
  .canvas-shell { min-height: 360px; }
  .technical-grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .001ms !important; }
}
"""

APP_JS_TEMPLATE = r"""
const graph = __GRAPH_JSON__;
const presentation = __PRESENTATION_JSON__;
const mermaidSource = __MERMAID_JSON__;
const SVG_NS = "http://www.w3.org/2000/svg";
let activeView = "overview";
let activeAgent = null;
let activeControl = null;
let compact = window.matchMedia("(max-width: 900px)").matches;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[character]));
}

function statusLabel(value) {
  return String(value || "declared_only").replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}

function stageFor(key) {
  return presentation.stages.find(stage => stage.key === key);
}

function stageMembership(item) {
  return activeView === "overview" || Boolean(item.truth && item.truth[activeView]);
}

function createSvg(tag, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
  return element;
}

function positionFor(item) {
  return item[compact ? "compact" : "wide"];
}

function edgePath(source, target, width, height) {
  const start = positionFor(source);
  const end = positionFor(target);
  if (compact) {
    const x1 = start.x;
    const y1 = start.y + height / 2;
    const x2 = end.x;
    const y2 = end.y + height / 2;
    const gutter = Math.max(6, Math.min(x1, x2) - 12);
    return {d: `M ${x1} ${y1} C ${gutter} ${y1}, ${gutter} ${y2}, ${x2} ${y2}`, x: gutter, y: (y1 + y2) / 2};
  }
  const forward = end.x >= start.x;
  const x1 = forward ? start.x + width : start.x;
  const y1 = start.y + height / 2;
  const x2 = forward ? end.x : end.x + width;
  const y2 = end.y + height / 2;
  const bend = Math.max(46, Math.abs(x2 - x1) * .45);
  return {d: `M ${x1} ${y1} C ${x1 + (forward ? bend : -bend)} ${y1}, ${x2 + (forward ? -bend : bend)} ${y2}, ${x2} ${y2}`, x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 7};
}

function renderStages() {
  document.getElementById("stage-buttons").innerHTML = presentation.stages.map((stage, index) => `
    <button type="button" class="stage-button" data-view="${esc(stage.key)}" data-available="${stage.available}" aria-pressed="${activeView === stage.key}">
      <span class="stage-index">0${index + 1}</span><span>${esc(stage.label)}</span><span class="stage-count" data-stage-count="${esc(stage.key)}">${stage.count}</span><small>${esc(stage.description)}</small>
    </button>`).join("");
  document.querySelectorAll("[data-view]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.view === activeView));
    button.addEventListener("click", () => {
      activeView = button.dataset.view;
      render();
    });
  });
}

function overviewModel() {
  return {
    nodes: presentation.overview_agents,
    relationships: presentation.overview_relationships,
    layout: presentation[compact ? "overview_compact_layout" : "overview_wide_layout"],
    width: compact ? 304 : 224,
    height: 126,
  };
}

function focusModel() {
  const focus = presentation.focus[activeAgent];
  return {
    nodes: focus.nodes,
    relationships: focus.relationships,
    layout: focus[compact ? "compact_layout" : "wide_layout"],
    width: compact ? 304 : 232,
    height: 84,
  };
}

function renderCanvas() {
  const svg = document.getElementById("system-canvas");
  const relationshipLayer = document.getElementById("relationship-layer");
  const nodeLayer = document.getElementById("node-layer");
  const model = activeAgent ? focusModel() : overviewModel();
  svg.setAttribute("viewBox", `0 0 ${model.layout.width} ${model.layout.height}`);
  svg.setAttribute("width", model.layout.width);
  svg.setAttribute("height", model.layout.height);
  svg.style.width = model.layout.width > document.getElementById("canvas-shell").clientWidth ? "100%" : `${model.layout.width}px`;
  relationshipLayer.replaceChildren();
  nodeLayer.replaceChildren();
  const nodes = new Map(model.nodes.map(node => [node.id, node]));

  model.relationships.forEach(relationship => {
    const source = nodes.get(relationship.source);
    const target = nodes.get(relationship.target);
    if (!source || !target) return;
    const geometry = edgePath(source, target, model.width, model.height);
    const visible = stageMembership(relationship);
    const path = createSvg("path", {
      d: geometry.d,
      class: `relationship ${relationship.mode === "handoff" ? "handoff" : ""} ${visible ? "active" : "muted"}`,
      "aria-hidden": "true",
    });
    relationshipLayer.appendChild(path);
    if (relationship.mode === "handoff") {
      const label = createSvg("text", {x: geometry.x, y: geometry.y, class: `relationship-label ${visible ? "" : "muted"}`, "text-anchor": "middle", "aria-hidden": "true"});
      label.textContent = relationship.label;
      relationshipLayer.appendChild(label);
    }
  });

  model.nodes.forEach(node => {
    const position = positionFor(node);
    const foreign = createSvg("foreignObject", {x: position.x, y: position.y, width: model.width, height: model.height});
    const isAgent = node.kind === "agent" || !activeAgent;
    const isControl = activeAgent && node.kind === "control";
    const element = document.createElement(isAgent || isControl ? "button" : "div");
    if (isAgent || isControl) {
      element.type = "button";
      if (isAgent) element.dataset.agentNode = node.id;
      if (isControl) element.dataset.controlNode = node.id;
      element.setAttribute("aria-pressed", String(isAgent ? activeAgent === node.id : activeControl === node.id));
      element.setAttribute("aria-label", `${node.name || node.label}. ${node.purpose || node.meta || (isControl ? "Control" : "Agent")}`);
    }
    const label = node.name || node.label;
    const meta = activeAgent ? (node.meta || node.summary || "") : (node.purpose || "No purpose declared.");
    const summary = activeAgent ? "" : (node.summary || "");
    const status = activeAgent || node.assurance !== "declared_only" ? (node.assurance || node.status) : null;
    element.className = `node-card ${node.id === activeAgent || node.id === activeControl ? "selected primary" : ""} ${!stageMembership(node) ? "muted" : ""}`;
    element.innerHTML = `<span class="node-kind">${esc(node.kind || "agent")}</span><span class="node-name">${esc(label)}</span><span class="node-meta">${esc(meta)}</span>${summary ? `<span class="node-summary">${esc(summary)}</span>` : ""}${status ? `<span class="node-status ${esc(status)}">${esc(statusLabel(status))}</span>` : ""}`;
    foreign.appendChild(element);
    nodeLayer.appendChild(foreign);
  });

  document.querySelectorAll("[data-agent-node]").forEach(button => button.addEventListener("click", () => selectAgent(button.dataset.agentNode)));
  document.querySelectorAll("[data-control-node]").forEach(button => button.addEventListener("click", () => selectControl(button.dataset.controlNode)));
  const empty = document.getElementById("stage-empty");
  const stage = stageFor(activeView);
  const available = activeView === "overview" || (activeAgent ? presentation.focus[activeAgent].coverage[activeView] > 0 : stage.available);
  empty.hidden = available;
  empty.innerHTML = stage && !available ? `<strong>${esc(stage.empty_title)}</strong><p>${esc(stage.empty_body)}</p>` : "";
}

function emptyList() {
  return '<li class="empty-copy">None declared</li>';
}

function renderInspector() {
  const inspector = document.getElementById("inspector");
  if (!activeAgent) {
    inspector.innerHTML = `
      <p class="inspector-kicker">System orientation</p>
      <h2 id="inspector-title">${esc(presentation.system.name)}</h2>
      <p class="inspector-purpose">${esc(presentation.system.summary)}. Select an agent to see only the relationships that explain its work.</p>
      <section class="inspector-section"><h3>Agent team</h3><div class="inspector-list">${presentation.overview_agents.map(agent => `<button type="button" class="agent-jump" data-agent-jump="${esc(agent.id)}"><strong>${esc(agent.name)}</strong><br><span class="item-meta">${esc(agent.summary)}</span></button>`).join("")}</div></section>
      <section class="inspector-section"><h3>How to read this map</h3><p class="inspector-purpose">Solid arrows are delegations. Dashed arrows are handoffs. The evidence progression changes emphasis without hiding the system around a gap.</p></section>`;
  } else {
    const focus = presentation.focus[activeAgent];
    const collaborators = focus.collaborators.map(item => `<li class="inspector-item"><span>${esc(item.name)}</span><span class="item-meta">${esc(item.direction)} · ${esc(item.mode)}</span></li>`).join("") || emptyList();
    const tools = focus.tools.map(item => `<li class="inspector-item"><span>${esc(item.name)}</span><span class="pill ${item.approval_sensitive ? "approval" : ""}">${esc(item.access_label)}</span></li>`).join("") || emptyList();
    const contexts = focus.contexts.map(item => `<li class="inspector-item"><span>${esc(item.name)}</span><span class="item-meta">${esc(item.source_label)}</span></li>`).join("") || emptyList();
    const controls = focus.controls.map(item => `<li><button type="button" class="control-button inspector-item" data-control="${esc(item.id)}" aria-pressed="${activeControl === item.id}"><span>${esc(item.name)}</span><span class="pill ${esc(item.status)}">${esc(item.status_label)}</span></button>${activeControl === item.id ? `<div class="control-explanation"><strong>${esc(item.requirement)}</strong><p>${esc(item.assessment || "Assessment not declared")} · ${esc(item.expected_evidence.join(", ") || "No expected evidence declared")}</p></div>` : ""}</li>`).join("") || emptyList();
    inspector.innerHTML = `
      <p class="inspector-kicker">Agent focus</p>
      <h2 id="inspector-title">${esc(focus.name)}</h2>
      <p class="inspector-purpose">${esc(focus.purpose)}</p>
      <section class="inspector-section"><h3>Returns</h3><span class="pill">${esc(focus.output_type)}</span></section>
      <section class="inspector-section"><h3>Collaborators</h3><ul class="inspector-list">${collaborators}</ul></section>
      <section class="inspector-section"><h3>Capabilities</h3><ul class="inspector-list">${tools}</ul></section>
      <section class="inspector-section"><h3>Context</h3><ul class="inspector-list">${contexts}</ul></section>
      <section class="inspector-section"><h3>Controls</h3><ul class="inspector-list">${controls}</ul></section>
      <details class="technical-agent"><summary>Agent technical details</summary><pre>${esc(JSON.stringify(focus.technical, null, 2))}</pre></details>`;
  }
  document.querySelectorAll("[data-agent-jump]").forEach(button => button.addEventListener("click", () => selectAgent(button.dataset.agentJump)));
  document.querySelectorAll("[data-control]").forEach(button => button.addEventListener("click", () => selectControl(button.dataset.control)));
}

function renderReviewNote() {
  const strip = document.getElementById("review-strip");
  const note = presentation.review_notes[0];
  strip.classList.toggle("has-note", Boolean(note));
  strip.innerHTML = note ? `<div class="review-note ${esc(note.tone)}"><span class="review-mark" aria-hidden="true">!</span><div><strong>${esc(note.title)}</strong><p>${esc(note.detail)}</p></div><span class="review-action">${esc(note.action)}</span></div>` : "";
}

function selectAgent(agentId) {
  if (!presentation.focus[agentId]) return;
  activeAgent = agentId;
  activeControl = null;
  document.getElementById("show-system").hidden = false;
  render();
  document.getElementById("inspector-title").focus?.();
}

function selectControl(controlId) {
  activeControl = activeControl === controlId ? null : controlId;
  renderCanvas();
  renderInspector();
}

function render() {
  document.querySelectorAll("[data-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === activeView)));
  const focus = activeAgent ? presentation.focus[activeAgent] : null;
  document.getElementById("workspace-kicker").textContent = focus ? "Agent neighborhood" : "System map";
  document.getElementById("workspace-title").textContent = focus ? focus.name : "Declared agent team";
  presentation.stages.forEach(stage => {
    const element = document.querySelector(`[data-stage-count="${stage.key}"]`);
    if (element) element.textContent = focus ? focus.coverage[stage.key] : stage.count;
  });
  renderCanvas();
  renderInspector();
  renderReviewNote();
}

document.getElementById("show-system").addEventListener("click", event => {
  activeAgent = null;
  event.currentTarget.hidden = true;
  render();
  document.querySelector('[data-view="overview"]').focus();
});
document.getElementById("raw-mermaid").textContent = mermaidSource;
const media = window.matchMedia("(max-width: 900px)");
media.addEventListener("change", event => { compact = event.matches; renderCanvas(); });
renderStages();
render();
"""

__all__ = ["APP_JS_TEMPLATE", "STYLE_CSS"]
