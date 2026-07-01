"""Shared semantic-analysis project index."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.ast import AgentDef, ContractProject, DatasourceDef, TypeDef


@dataclass(frozen=True)
class ProjectIndex:
    type_defs: dict[str, TypeDef]
    agent_defs: dict[str, AgentDef]
    datasource_defs: dict[str, DatasourceDef]
    project_tools: set[str]
    project_hosted_tools: set[str]
    datasource_targets: set[str]

    @classmethod
    def from_project(cls, project: ContractProject) -> ProjectIndex:
        agent_defs = project.agents
        datasource_defs = project.datasources
        return cls(
            type_defs=project.types,
            agent_defs=agent_defs,
            datasource_defs=datasource_defs,
            project_tools={use.name for agent in agent_defs.values() for use in agent.uses if use.kind == "tool"},
            project_hosted_tools={
                use.name for agent in agent_defs.values() for use in agent.uses if use.kind == "hosted_tool"
            },
            datasource_targets=set(datasource_defs) | {item.produces for item in datasource_defs.values()},
        )

    @property
    def agent_names(self) -> set[str]:
        return set(self.agent_defs)


__all__ = ["ProjectIndex"]
