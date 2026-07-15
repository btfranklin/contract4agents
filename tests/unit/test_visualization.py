from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import contract4agents.visualization as visualization
from contract4agents.assurance import AssessorIdentity, ControlResult
from contract4agents.cli import main
from contract4agents.ir import CanonicalIR, FrozenMap, SemanticId, build_canonical_ir, contract_digest, semantic_id
from contract4agents.parser import parse_project
from contract4agents.planning import AdapterPlan, AgentPlan, MaterializationPlan
from contract4agents.tracing import (
    NormalizedTrace,
    ProviderCorrelation,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
)
from contract4agents.visualization import (
    build_visualization_graph,
    render_agent_mermaid,
    render_html,
    render_mermaid,
    write_visualization_artifacts,
)

ROOT = Path(__file__).resolve().parents[2]
INCIDENT_COMMAND = ROOT / "examples" / "incident-command"


def test_declared_graph_is_ir_native_and_represents_v2_semantics() -> None:
    ir = _ir()
    graph = build_visualization_graph(ir, project_root=INCIDENT_COMMAND)

    node_ids = {node["id"] for node in graph["nodes"]}
    edge_kinds = {edge["kind"] for edge in graph["edges"]}
    encoded = json.dumps(graph, sort_keys=True)

    assert graph["version"] == "2"
    assert graph["ir_version"] == "2"
    assert graph["contract_digest"] == contract_digest(ir)
    assert "agent:IncidentCommander" in node_ids
    assert "tool:logs.search" in node_ids
    assert "edge:investigate_logs" in node_ids
    assert "control:IncidentCommander:evidence_required" in node_ids
    assert "quality:IncidentCommander:concise_operational_summary" in node_ids
    assert {"agent_grant", "grant_capability", "composition_source", "composition_target"} <= edge_kinds
    assert graph["edges"]
    assert all(any(layer.get("present") is True for layer in edge["truth"].values()) for edge in graph["edges"])
    assert graph["summary"]["declared"] > 0
    assert graph["summary"]["planned"] == graph["summary"]["observed"] == 0
    assert "hosted_tool" not in encoded
    assert '"policy"' not in encoded
    assert '"success"' not in encoded
    commander = graph["agents"]["IncidentCommander"]
    assert commander["grants"][0]["authorization"] == "approval_required"
    assert {item["direction"] for item in commander["composition"]} == {"outgoing"}
    assert commander["controls"]
    assert commander["qualities"]


def test_graph_keeps_declared_planned_observed_and_assured_truth_separate() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace, control_id = _trace(ir, plan)
    result = ControlResult(
        control_id=str(control_id),
        status="passed",
        reason="Required evidence was observed.",
        assessment="runtime",
        assessor=AssessorIdentity("test-assessor", "1"),
        evidence_event_ids=("event-1",),
        evidence_refs=("evidence:log-query",),
    )

    graph = build_visualization_graph(ir, plan=plan, trace=trace, control_results=(result,))
    nodes = {node["id"]: node for node in graph["nodes"]}

    agent = nodes["agent:IncidentCommander"]
    assert agent["truth"]["declared"]["goal"]
    assert agent["truth"]["planned"]["model"] == "gpt-test"
    assert agent["truth"]["observed"] == {
        "event_count": 1,
        "event_types": ["agent.completed"],
        "evidence_refs": ["evidence:log-query"],
        "providers": ["openai"],
        "run_ids": ["run-1"],
    }
    assert agent["truth"]["assured"] == {}
    control = nodes[str(control_id)]
    assert control["truth"]["declared"]["assessment"] == "post_run"
    assert control["truth"]["assured"]["status"] == "passed"
    assert nodes["event:event-1"]["truth"]["observed"]["data"] == {"evidence": "present"}
    assert graph["summary"]["planned"] > 0
    assert graph["summary"]["observed"] > 0
    assert graph["summary"]["assured"] == 1
    evidence_edges = [edge for edge in graph["edges"] if edge["kind"] == "assurance_evidence"]
    assert [(edge["source"], edge["target"]) for edge in evidence_edges] == [
        (str(control_id), "event:event-1")
    ]


def test_graph_rejects_cross_contract_and_cross_plan_overlays() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace, _ = _trace(ir, plan)
    wrong_plan = MaterializationPlan(
        contract_digest="sha256:" + "0" * 64,
        target=plan.target,
        profile=plan.profile,
        adapter=plan.adapter,
        agents=plan.agents,
        bindings=plan.bindings,
        grants=plan.grants,
        composition=plan.composition,
        controls=plan.controls,
        isolation=plan.isolation,
        artifact_digests=plan.artifact_digests,
        host_obligations=plan.host_obligations,
        expected_telemetry=plan.expected_telemetry,
    )

    with pytest.raises(ValueError, match="contract digest"):
        build_visualization_graph(ir, plan=wrong_plan)

    altered_trace = NormalizedTrace(
        (
            TraceEvent(
                context=TraceRunContext(
                    "run-1",
                    "thread-1",
                    contract_digest(ir),
                    "sha256:" + "1" * 64,
                ),
                event_id="event-1",
                parent_event_id=None,
                event_type="agent.completed",
                timestamp=1.0,
                semantic=trace.events[0].semantic,
            ),
        )
    )
    with pytest.raises(ValueError, match="plan digest"):
        build_visualization_graph(ir, plan=plan, trace=altered_trace)


def test_mermaid_is_deterministic_and_can_select_truth_layers() -> None:
    ir = _ir()
    plan = _plan(ir)
    graph = build_visualization_graph(ir, plan=plan)

    first = render_mermaid(graph)
    assert first == render_mermaid(graph)
    assert "IncidentCommander" in first
    assert "[declared, planned]" in first
    planned = render_mermaid(graph, view="planned")
    declared = render_mermaid(graph, view="declared")
    assert "adapter_openai" in planned
    assert "adapter_openai" not in declared
    assert "edge_investigate_logs" in declared
    assert "-->|grant|" in declared


def test_agent_mermaid_filters_to_agent_neighborhood() -> None:
    graph = build_visualization_graph(_ir())

    focused = render_agent_mermaid(graph, "IncidentCommander")
    overview = render_mermaid(graph)

    assert len(focused) < len(overview)
    assert "IncidentCommander" in focused
    assert "grant_IncidentCommander_logs_search" in focused
    assert "n_tool_logs_search" not in focused  # The explicit grant node is the immediate neighbor.
    assert "CustomerImpactWriter" not in focused
    with pytest.raises(ValueError, match="Unknown agent"):
        render_agent_mermaid(graph, "Missing")


def test_html_is_dependency_free_and_exposes_truth_layer_review() -> None:
    ir = _ir()
    graph = build_visualization_graph(ir, plan=_plan(ir))
    html = render_html(graph, render_mermaid(graph))

    assert "Contract4Agents Truth Review" in html
    assert 'data-view="declared"' in html
    assert 'data-view="planned"' in html
    assert 'data-view="observed"' in html
    assert 'data-view="assured"' in html
    assert "visibleTruth(edge.truth)" in html
    assert "IncidentCommander" in html
    assert "flowchart LR" in html
    assert "https://" not in html


def test_artifact_writer_emits_graph_mermaid_and_self_contained_html(tmp_path: Path) -> None:
    graph = build_visualization_graph(_ir(), project_root=INCIDENT_COMMAND)

    write_visualization_artifacts(graph, tmp_path)

    encoded = json.loads((tmp_path / "graph.json").read_text())
    assert encoded["version"] == "2"
    assert encoded["contract_digest"].startswith("sha256:")
    assert (tmp_path / "graph.mmd").read_text().startswith("flowchart LR\n")
    assert "Contract4Agents Truth Review" in (tmp_path / "index.html").read_text()


def test_cli_visualize_writes_v2_review_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "visualization"
    result = CliRunner().invoke(main, ["visualize", str(INCIDENT_COMMAND), "--out", str(output_dir)])

    assert result.exit_code == 0, result.output
    assert "Contract4Agents visualization written" in result.output
    graph = json.loads((output_dir / "graph.json").read_text())
    assert graph["version"] == "2"
    assert "IncidentCommander" in graph["agents"]


def test_visualization_public_facade_exports_v2_api() -> None:
    assert visualization.build_visualization_graph is build_visualization_graph
    assert visualization.render_agent_mermaid is render_agent_mermaid
    assert visualization.render_html is render_html
    assert visualization.render_mermaid is render_mermaid
    assert visualization.write_visualization_artifacts is write_visualization_artifacts


def _ir() -> CanonicalIR:
    return build_canonical_ir(parse_project(INCIDENT_COMMAND))


def _plan(ir: CanonicalIR) -> MaterializationPlan:
    agents = FrozenMap(
        (
            agent.id,
            AgentPlan(
                id=agent.id,
                name=agent.name,
                model="gpt-test",
                model_options=FrozenMap({"temperature": 0}),
                output_type=agent.output_type,
            ),
        )
        for agent in ir.agents.values()
    )
    return MaterializationPlan(
        contract_digest=contract_digest(ir),
        target="openai",
        profile="test",
        adapter=AdapterPlan("openai", "test-adapter-1"),
        agents=agents,
        bindings=FrozenMap(),
        grants=FrozenMap(),
        composition=FrozenMap(),
        controls=FrozenMap(),
        isolation=FrozenMap(),
        artifact_digests=FrozenMap(),
        host_obligations=(),
        expected_telemetry=("agent.completed",),
    )


def _trace(ir: CanonicalIR, plan: MaterializationPlan) -> tuple[NormalizedTrace, SemanticId]:
    control_id = semantic_id("control", "IncidentCommander", "evidence_required")
    event = TraceEvent(
        context=TraceRunContext("run-1", "thread-1", contract_digest(ir), plan.plan_digest),
        event_id="event-1",
        parent_event_id=None,
        event_type="agent.completed",
        timestamp=1.0,
        semantic=TraceSemanticRefs(
            agent_id=semantic_id("agent", "IncidentCommander"),
            control_ids=(control_id,),
        ),
        data={"evidence": "present"},
        provider=ProviderCorrelation("openai", request_id="request-1"),
        evidence_refs=("evidence:log-query",),
    )
    return NormalizedTrace((event,)), control_id
