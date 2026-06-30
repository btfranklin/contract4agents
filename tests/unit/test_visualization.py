from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

import contract4agents.visualization as visualization
from contract4agents.cli import main
from contract4agents.compiler import build_artifacts
from contract4agents.parser import parse_project
from contract4agents.visualization import build_visualization_graph, render_agent_mermaid, render_html, render_mermaid

ROOT = Path(__file__).resolve().parents[2]
OPS_DESK = ROOT / "tests" / "fixtures" / "contract_projects" / "ops-desk-lab"


def test_build_visualization_graph_for_ops_desk() -> None:
    project = parse_project(OPS_DESK)
    graph = build_visualization_graph(project, build_artifacts(project))

    node_ids = {node["id"] for node in graph["nodes"]}
    edge_keys = {(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]}

    assert "agent:OpsDeskCoordinator" in node_ids
    assert "agent:BillingSpecialist" in node_ids
    assert "tool:billing.create_credit" in node_ids
    assert "datasource:CustomerAccountSource" in node_ids
    assert "type:CustomerAccount" in node_ids
    assert "eval:billing_duplicate_credit_approved" in node_ids
    assert "monitor:billing_credit_requires_approval" in node_ids
    assert (
        "agent:OpsDeskCoordinator",
        "agent:BillingSpecialist",
        "agent_uses_agent",
    ) in edge_keys
    assert (
        "agent:OpsDeskCoordinator",
        "tool:billing.create_credit",
        "agent_uses_tool",
    ) in edge_keys
    assert (
        "datasource:CustomerAccountSource",
        "type:CustomerAccount",
        "datasource_produces_type",
    ) in edge_keys
    assert (
        "eval:billing_duplicate_credit_approved",
        "agent:OpsDeskCoordinator",
        "eval_targets_agent",
    ) in edge_keys
    assert (
        "monitor:billing_credit_requires_approval",
        "agent:OpsDeskCoordinator",
        "monitor_targets_agent",
    ) in edge_keys

    coordinator = graph["agents"]["OpsDeskCoordinator"]
    assert coordinator["tools"][0]["permission"] == "requires_approval"
    assert "agent_as_tool(BillingSpecialist)" in coordinator["composition"]
    assert any("not inferred into graph edges" in warning for warning in graph["warnings"])


def test_visualization_public_facade_exports_expected_functions() -> None:
    assert visualization.build_visualization_graph is build_visualization_graph
    assert visualization.render_agent_mermaid is render_agent_mermaid
    assert visualization.render_html is render_html
    assert visualization.render_mermaid is render_mermaid
    assert "write_visualization_artifacts" in visualization.__all__


def test_mermaid_is_deterministic_and_conservative() -> None:
    project = parse_project(OPS_DESK)
    graph = build_visualization_graph(project, build_artifacts(project))

    first = render_mermaid(graph)
    second = render_mermaid(graph)

    assert first == second
    assert "agent_OpsDeskCoordinator" in first
    assert "agent_BillingSpecialist" in first
    assert "requires_approval" in first
    assert "agent_as_tool" not in first
    assert "n_agent_OpsDeskCoordinator -->|uses agent| n_agent_BillingSpecialist" in first
    assert "class n_agent_OpsDeskCoordinator kind_agent" in first
    assert "classDef kind_agent fill:#d9f2ee" in first
    assert "classDef kind_tool fill:#fff3c4" in first


def test_agent_mermaid_filters_to_selected_agent_neighborhood() -> None:
    project = parse_project(OPS_DESK)
    graph = build_visualization_graph(project, build_artifacts(project))

    focused = render_agent_mermaid(graph, "BillingSpecialist")
    overview = render_mermaid(graph)

    assert len(focused) < len(overview)
    assert "agent_BillingSpecialist" in focused
    assert "tool_billing_lookup_invoice" in focused
    assert "agent_OpsDeskCoordinator" in focused
    assert "agent_SecuritySpecialist" not in focused


def test_html_embeds_overview_and_agent_diagrams() -> None:
    project = parse_project(OPS_DESK)
    graph = build_visualization_graph(project, build_artifacts(project))
    html = render_html(graph, render_mermaid(graph))

    assert "const diagrams =" in html
    assert "Focused:" in html
    assert "pluralKind(kind, count)" in html
    assert 'datasource: ["datasource", "datasources"]' in html
    assert 'button class="count"' in html
    assert "renderBreakdown(button.dataset.kind)" in html
    assert "Back to overview" in html
    assert "nodeRelationships(node.id)" in html
    assert "contract-mermaid-ready" in html
    assert "BillingSpecialist" in html


def test_cli_visualize_writes_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "visualization"
    result = CliRunner().invoke(main, ["visualize", str(OPS_DESK), "--out", str(output_dir)])

    assert result.exit_code == 0, result.output
    assert "Contract4Agents visualization written" in result.output
    graph_path = output_dir / "graph.json"
    assert graph_path.exists()
    assert (output_dir / "graph.mmd").exists()
    assert (output_dir / "index.html").exists()
    graph = json.loads(graph_path.read_text())
    assert graph["version"] == "1"
    assert "OpsDeskCoordinator" in graph["agents"]
