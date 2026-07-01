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

    def reachable_agent_names(self, agent_name: str) -> set[str]:
        reachable: set[str] = set()
        pending = [agent_name]
        while pending:
            current_name = pending.pop()
            if current_name in reachable:
                continue
            current_agent = self.agent_defs.get(current_name)
            if current_agent is None:
                continue
            reachable.add(current_name)
            pending.extend(use.name for use in current_agent.uses if use.kind == "agent")
        return reachable

    def reachable_tools(self, agent_name: str) -> set[str]:
        return {
            use.name
            for reachable_agent in self._reachable_agents(agent_name)
            for use in reachable_agent.uses
            if use.kind == "tool"
        }

    def reachable_hosted_tools(self, agent_name: str) -> set[str]:
        return {
            use.name
            for reachable_agent in self._reachable_agents(agent_name)
            for use in reachable_agent.uses
            if use.kind == "hosted_tool"
        }

    def reachable_datasource_targets(self, agent_name: str) -> set[str]:
        targets: set[str] = set()
        for reachable_agent in self._reachable_agents(agent_name):
            for use in reachable_agent.uses:
                if use.kind != "datasource":
                    continue
                datasource = self.datasource_defs.get(use.name)
                if datasource is not None:
                    targets.update({datasource.name, datasource.produces})
        return targets

    def _reachable_agents(self, agent_name: str) -> list[AgentDef]:
        return [
            self.agent_defs[name]
            for name in sorted(self.reachable_agent_names(agent_name))
            if name in self.agent_defs
        ]


__all__ = ["ProjectIndex"]
