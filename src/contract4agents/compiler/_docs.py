"""Human-review documentation artifact generation."""

from __future__ import annotations

from collections.abc import Iterable

from contract4agents.ast import ContractProject
from contract4agents.compiler._types import (
    AgentManifest,
    ManifestDatasource,
    ManifestHostContext,
    ManifestHostedTool,
    ManifestUse,
)


def generated_docs(project: ContractProject, manifests: dict[str, AgentManifest]) -> dict[str, str]:
    docs = {"summary.md": _summary_doc(project, manifests)}
    for name, manifest in sorted(manifests.items()):
        docs[f"agents/{name}.md"] = _agent_doc(name, manifest, project)
    return docs


def _summary_doc(project: ContractProject, manifests: dict[str, AgentManifest]) -> str:
    lines = ["# Generated Contract4Agents Summary", ""]
    lines.append("## Agents")
    for name, manifest in sorted(manifests.items()):
        lines.append(f"- [`{name}`](agents/{name}.md) -> `{manifest['output']['type']}`")
    lines.extend(["", "## Types"])
    for name in sorted(project.types):
        lines.append(f"- `{name}`")
    if project.evals:
        lines.extend(["", "## Evals"])
        for eval_case in sorted(project.evals, key=lambda item: (item.agent, item.name)):
            lines.append(f"- `{eval_case.name}` for `{eval_case.agent}`")
    if project.monitors:
        lines.extend(["", "## Monitors"])
        for monitor in sorted(project.monitors, key=lambda item: (item.agent, item.name)):
            lines.append(f"- `{monitor.name}` for `{monitor.agent}` ({monitor.severity})")
    if project.run_contracts:
        lines.extend(["", "## Run Contracts"])
        for run_contract in sorted(project.run_contracts.values(), key=lambda item: item.name):
            lines.append(f"- `{run_contract.name}`")
    hosted_tools = [
        (agent_name, tool)
        for agent_name, manifest in sorted(manifests.items())
        for tool in manifest["hosted_tools"]
    ]
    if hosted_tools:
        lines.extend(["", "## Hosted Tools"])
        for agent_name, tool in hosted_tools:
            config = ", ".join(f"{key}={value}" for key, value in sorted(tool["config"].items()))
            suffix = f" ({config})" if config else ""
            lines.append(f"- `{agent_name}` may use `{tool['name']}`{suffix}")
    return "\n".join(lines) + "\n"


def _agent_doc(name: str, manifest: AgentManifest, project: ContractProject) -> str:
    lines = [
        f"# {name}",
        "",
        f"Source: `{manifest['source_path']}`",
        "",
        "```contract",
        f"agent {name}({_signature_inputs(manifest)}) -> {manifest['output']['type']}",
        "```",
        "",
        "## Intent",
        "",
    ]
    lines.extend(_field("Goal", manifest["goal"]))
    lines.extend(_field("Description", manifest["description"]))
    lines.extend(
        [
            "## Inputs",
            "",
            *_table(
                ["Name", "Type", "Required", "Python ref"],
                [
                    [item["name"], item["type"], str(item["required"]).lower(), item["python_ref"] or ""]
                    for item in manifest["inputs"]
                ],
            ),
            "",
            "## Output",
            "",
            *_table(
                ["Type", "Schema", "Python ref"],
                [
                    [
                        manifest["output"]["type"],
                        manifest["output"]["schema_ref"],
                        manifest["output"]["python_ref"] or "",
                    ]
                ],
            ),
            "",
            "## Host Context",
            "",
            *_table(["Type", "Python ref"], _host_context_rows(manifest["host_context"])),
            "",
            "## Capabilities",
            "",
            "### Tools",
            "",
            *_table(["Name", "Source", "Permission"], _manifest_use_rows(manifest["tools"])),
            "",
            "### Hosted Tools",
            "",
            *_table(["Name", "Provider", "Tool", "Config", "Permission"], _hosted_tool_rows(manifest["hosted_tools"])),
            "",
            "### Agent Dependencies",
            "",
            *_table(["Name", "Source", "Permission"], _manifest_use_rows(manifest["agents"])),
            "",
            "### Datasources",
            "",
            *_table(["Name", "Produces", "Requires", "Cache", "Python"], _datasource_rows(manifest["datasources"])),
            "",
        ]
    )
    lines.extend(_list_section("Policy", manifest["policy"]))
    lines.extend(_list_section("Success Criteria", manifest["success"]))
    lines.extend(_list_section("Routes", manifest["routes"]))
    lines.extend(_list_section("Composition", manifest["composition"]))
    lines.extend(_list_section("Guards", manifest["guards"]))
    lines.extend(_list_section("Assertions", manifest["assertions"]))
    lines.extend(
        [
            "## Evals",
            "",
            *_simple_list(item.name for item in project.evals if item.agent == name),
            "",
            "## Monitors",
            "",
            *_simple_list(f"{item.name} ({item.severity})" for item in project.monitors if item.agent == name),
            "",
            "## Artifact Links",
            "",
            f"- Manifest: `manifests/{name}.json`",
            f"- Instructions: `instructions/{name}.md`",
            f"- Output schema: `{manifest['output']['schema_ref']}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _signature_inputs(manifest: AgentManifest) -> str:
    return ", ".join(f"{item['name']}: {item['type']}" for item in manifest["inputs"])


def _field(label: str, value: str) -> list[str]:
    if not value:
        return []
    return [f"**{label}:** {value}", ""]


def _list_section(label: str, values: list[str]) -> list[str]:
    return [f"## {label}", "", *_simple_list(values), ""]


def _simple_list(values: Iterable[object]) -> list[str]:
    items = sorted(str(item) for item in values)
    if not items:
        return ["None."]
    return [f"- `{item}`" for item in items]


def _manifest_use_rows(values: list[ManifestUse]) -> list[list[str]]:
    return [
        [item["name"], item["module"], item["permission"]]
        for item in sorted(values, key=lambda item: item["name"])
    ]


def _hosted_tool_rows(values: list[ManifestHostedTool]) -> list[list[str]]:
    return [
        [
            item["name"],
            item["provider"],
            item["tool"],
            ", ".join(f"{key}={value}" for key, value in sorted(item["config"].items())),
            item["permission"],
        ]
        for item in sorted(values, key=lambda item: item["name"])
    ]


def _host_context_rows(values: list[ManifestHostContext]) -> list[list[str]]:
    return [[item["type"], item["python_ref"] or ""] for item in values]


def _datasource_rows(values: list[ManifestDatasource]) -> list[list[str]]:
    return [
        [item["name"], item["produces"], ", ".join(item["requires"]), item["cache"], item["python"]]
        for item in sorted(values, key=lambda item: item["name"])
    ]


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["None."]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return lines


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


__all__ = ["generated_docs"]
