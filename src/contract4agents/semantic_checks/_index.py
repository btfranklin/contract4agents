"""Shared semantic-analysis project index."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.ast import (
    AgentDef,
    CompositionDef,
    ContractProject,
    DatasourceDef,
    ExternalContextDef,
    IsolationDef,
    RunSpecDef,
    ToolDef,
    TypeDef,
)


@dataclass(frozen=True)
class ProjectIndex:
    type_defs: dict[str, TypeDef]
    agent_defs: dict[str, AgentDef]
    datasource_defs: dict[str, DatasourceDef]
    tool_defs: dict[str, ToolDef]
    external_context_defs: dict[str, ExternalContextDef]
    composition_defs: dict[str, CompositionDef]
    isolation_defs: dict[str, IsolationDef]
    run_spec_defs: dict[str, RunSpecDef]
    quality_ids: set[tuple[str, str]]
    project_tools: set[str]
    datasource_targets: set[str]

    @classmethod
    def from_project(cls, project: ContractProject) -> ProjectIndex:
        agent_defs = project.agents
        datasource_defs = project.datasources
        return cls(
            type_defs=project.types,
            agent_defs=agent_defs,
            datasource_defs=datasource_defs,
            tool_defs=project.tools,
            external_context_defs=project.external_contexts,
            composition_defs=project.compositions,
            isolation_defs=project.isolations,
            run_spec_defs=project.run_specs,
            quality_ids={(item.agent, item.name) for item in project.qualities},
            project_tools=set(project.tools),
            datasource_targets=set(datasource_defs) | {item.return_type for item in datasource_defs.values()},
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
            if current_name not in self.agent_defs:
                continue
            reachable.add(current_name)
            pending.extend(
                edge.target_agent for edge in self.composition_defs.values() if edge.source_agent == current_name
            )
        return reachable

    def reachable_tools(self, agent_name: str) -> set[str]:
        return {
            grant.capability
            for reachable_agent in self._reachable_agents(agent_name)
            for grant in reachable_agent.grants
        }

    def reachable_datasource_targets(self, agent_name: str) -> set[str]:
        targets: set[str] = set()
        for reachable_agent in self._reachable_agents(agent_name):
            targets.update(
                requirement.source
                for requirement in reachable_agent.context
                if requirement.origin == "datasource" and requirement.source is not None
            )
        return targets

    def _reachable_agents(self, agent_name: str) -> list[AgentDef]:
        return [
            self.agent_defs[name] for name in sorted(self.reachable_agent_names(agent_name)) if name in self.agent_defs
        ]


__all__ = ["ProjectIndex"]
