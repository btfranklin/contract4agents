"""Semantic analysis for parsed Contract4Agents projects."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.ast import ContractProject, SourceSpan
from contract4agents.diagnostics import Diagnostic
from contract4agents.semantic_checks._agents import check_agent
from contract4agents.semantic_checks._context import check_context_dependencies
from contract4agents.semantic_checks._expressions import check_eval, check_monitor
from contract4agents.semantic_checks._index import ProjectIndex
from contract4agents.semantic_checks._run_contracts import check_run_contract
from contract4agents.semantic_checks._types import check_datasource, check_type


@dataclass(frozen=True)
class SemanticResult:
    diagnostics: list[Diagnostic]

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


def analyze_project(project: ContractProject) -> SemanticResult:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(
        _duplicates([(item.name, item.span) for module in project.modules for item in module.types], "type")
    )
    diagnostics.extend(
        _duplicates([(item.name, item.span) for module in project.modules for item in module.agents], "agent")
    )
    diagnostics.extend(
        _duplicates(
            [
                (item.name, item.span or SourceSpan(module.path, 1))
                for module in project.modules
                for item in module.datasources
            ],
            "datasource",
        )
    )
    diagnostics.extend(
        _duplicates(
            [(item.name, item.span) for module in project.modules for item in module.run_contracts],
            "run contract",
        )
    )
    index = ProjectIndex.from_project(project)
    for type_def in index.type_defs.values():
        diagnostics.extend(check_type(type_def, index))
    for datasource in index.datasource_defs.values():
        diagnostics.extend(check_datasource(datasource, index))
    for agent in index.agent_defs.values():
        diagnostics.extend(check_agent(agent, index))
    diagnostics.extend(check_context_dependencies(index))
    for run_contract in index.run_contract_defs.values():
        diagnostics.extend(check_run_contract(run_contract, index))
    for eval_case in project.evals:
        diagnostics.extend(check_eval(eval_case.agent, eval_case.expects, eval_case.semantic_expects, index))
    for monitor in project.monitors:
        diagnostics.extend(check_monitor(monitor, index))
    return SemanticResult(diagnostics)


def _duplicates(items: list[tuple[str, SourceSpan]], label: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    seen: dict[str, SourceSpan] = {}
    for name, span in items:
        if name in seen:
            diagnostics.append(
                Diagnostic(
                    "SEM000",
                    f"Duplicate {label} declaration `{name}`",
                    span=span,
                    hint=f"First declaration was at {seen[name].display()}",
                )
            )
        else:
            seen[name] = span
    return diagnostics


__all__ = ["SemanticResult", "analyze_project"]
