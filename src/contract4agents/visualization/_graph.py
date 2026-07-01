"""Static Contract4Agents project graph construction."""

from __future__ import annotations

from contract4agents.ast import ContractProject
from contract4agents.compiler import AgentManifest, CompilerArtifacts, EvalPack, MonitorPack
from contract4agents.visualization._types import (
    VisualizationAgentDetail,
    VisualizationEdge,
    VisualizationGraph,
    VisualizationNode,
)
from contract4agents.visualization._utils import _add_edge, _add_node

VISUALIZATION_VERSION = "1"


def build_visualization_graph(project: ContractProject, artifacts: CompilerArtifacts) -> VisualizationGraph:
    """Build a deterministic, provider-neutral graph for configured contracts."""
    nodes: dict[str, VisualizationNode] = {}
    edges: dict[str, VisualizationEdge] = {}

    manifests = artifacts["manifests"]
    eval_packs = artifacts["evals"]
    monitor_packs = artifacts["monitors"]

    for type_name in sorted(project.types):
        _add_node(nodes, f"type:{type_name}", "type", type_name)

    for datasource_name, datasource in sorted(project.datasources.items()):
        datasource_id = f"datasource:{datasource_name}"
        _add_node(
            nodes,
            datasource_id,
            "datasource",
            datasource_name,
            python=datasource.python,
            render=datasource.render,
            cache=datasource.cache,
        )
        for required_type in sorted(datasource.requires):
            _add_node(nodes, f"type:{required_type}", "type", required_type)
            _add_edge(edges, datasource_id, f"type:{required_type}", "datasource_requires_type", "requires")
        _add_node(nodes, f"type:{datasource.produces}", "type", datasource.produces)
        _add_edge(edges, datasource_id, f"type:{datasource.produces}", "datasource_produces_type", "produces")

    for agent_name, agent in sorted(project.agents.items()):
        manifest = manifests[agent_name]
        agent_id = f"agent:{agent_name}"
        _add_node(nodes, agent_id, "agent", agent_name, return_type=agent.return_type)

        for parameter in agent.parameters:
            type_id = f"type:{parameter.normalized_type}"
            _add_node(nodes, type_id, "type", parameter.normalized_type)
            _add_edge(
                edges,
                agent_id,
                type_id,
                "agent_input_type",
                parameter.name,
                field=parameter.name,
                required=not parameter.nullable and parameter.default is None,
            )

        output_id = f"type:{agent.return_type}"
        _add_node(nodes, output_id, "type", agent.return_type)
        _add_edge(edges, agent_id, output_id, "agent_output_type", "returns")

        for subagent in manifest["agents"]:
            subagent_name = subagent["name"]
            _add_node(nodes, f"agent:{subagent_name}", "agent", subagent_name)
            _add_edge(
                edges,
                agent_id,
                f"agent:{subagent_name}",
                "agent_uses_agent",
                "uses agent",
                module=subagent["module"],
                permission=subagent["permission"],
            )

        for tool in manifest["tools"]:
            tool_name = tool["name"]
            permission = tool["permission"]
            _add_node(nodes, f"tool:{tool_name}", "tool", tool_name, module=tool["module"])
            _add_edge(
                edges,
                agent_id,
                f"tool:{tool_name}",
                "agent_uses_tool",
                permission,
                module=tool["module"],
                permission=permission,
            )

        for hosted_tool in manifest["hosted_tools"]:
            tool_name = hosted_tool["name"]
            permission = hosted_tool["permission"]
            _add_node(
                nodes,
                f"hosted_tool:{tool_name}",
                "hosted_tool",
                tool_name,
                provider=hosted_tool["provider"],
                tool=hosted_tool["tool"],
                config=hosted_tool["config"],
            )
            _add_edge(
                edges,
                agent_id,
                f"hosted_tool:{tool_name}",
                "agent_uses_hosted_tool",
                permission,
                provider=hosted_tool["provider"],
                tool=hosted_tool["tool"],
                config=hosted_tool["config"],
                permission=permission,
            )

        for manifest_datasource in manifest["datasources"]:
            datasource_name = manifest_datasource["name"]
            _add_node(nodes, f"datasource:{datasource_name}", "datasource", datasource_name)
            _add_edge(
                edges,
                agent_id,
                f"datasource:{datasource_name}",
                "agent_uses_datasource",
                "uses datasource",
            )

    for eval_pack in sorted(eval_packs, key=lambda item: (item["agent"], item["name"])):
        eval_name = eval_pack["name"]
        agent_name = eval_pack["agent"]
        _add_node(nodes, f"eval:{eval_name}", "eval", eval_name)
        _add_node(nodes, f"agent:{agent_name}", "agent", agent_name)
        _add_edge(edges, f"eval:{eval_name}", f"agent:{agent_name}", "eval_targets_agent", "evaluates")

    for monitor in sorted(monitor_packs, key=lambda item: (item["agent"], item["name"])):
        monitor_name = monitor["name"]
        agent_name = monitor["agent"]
        severity = monitor["severity"]
        _add_node(nodes, f"monitor:{monitor_name}", "monitor", monitor_name, severity=severity)
        _add_node(nodes, f"agent:{agent_name}", "agent", agent_name)
        _add_edge(
            edges,
            f"monitor:{monitor_name}",
            f"agent:{agent_name}",
            "monitor_targets_agent",
            severity or "monitors",
            severity=severity,
        )

    agent_details: dict[str, VisualizationAgentDetail] = {
        agent_name: _agent_detail(agent_name, manifest, eval_packs, monitor_packs)
        for agent_name, manifest in sorted(manifests.items())
    }

    warnings = [
        "Visualization V1 shows configured/static contract structure only; it does not render runtime traces.",
        "Route and composition declarations are shown in agent details but are not inferred into graph edges.",
    ]

    return {
        "version": VISUALIZATION_VERSION,
        "project_root": str(project.root),
        "nodes": sorted(nodes.values(), key=lambda item: (str(item["kind"]), str(item["id"]))),
        "edges": sorted(edges.values(), key=lambda item: str(item["id"])),
        "agents": agent_details,
        "warnings": warnings,
    }


def _agent_detail(
    agent_name: str,
    manifest: AgentManifest,
    eval_packs: list[EvalPack],
    monitor_packs: list[MonitorPack],
) -> VisualizationAgentDetail:
    inputs = [dict(item) for item in manifest["inputs"]]
    output = dict(manifest["output"])
    signature_inputs = ", ".join(f"{item['name']}: {item['type']}" for item in inputs)
    signature = f"{agent_name}({signature_inputs}) -> {output.get('type', '')}"
    return {
        "name": agent_name,
        "signature": signature,
        "goal": str(manifest.get("goal", "")),
        "description": str(manifest.get("description", "")),
        "inputs": inputs,
        "output": output,
        "tools": [dict(item) for item in manifest["tools"]],
        "hosted_tools": [dict(item) for item in manifest["hosted_tools"]],
        "subagents": [dict(item) for item in manifest["agents"]],
        "datasources": [dict(item) for item in manifest["datasources"]],
        "policy": manifest["policy"],
        "success": manifest["success"],
        "routes": manifest["routes"],
        "composition": manifest["composition"],
        "guards": manifest["guards"],
        "assertions": manifest["assertions"],
        "evals": [
            {"name": item["name"], "expects": item["expects"]}
            for item in eval_packs
            if item["agent"] == agent_name
        ],
        "monitors": [
            {"name": item["name"], "severity": item["severity"]}
            for item in monitor_packs
            if item["agent"] == agent_name
        ],
    }
