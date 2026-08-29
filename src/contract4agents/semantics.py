"""Semantic analysis for parsed Contract4Agents projects."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.ast import ContractProject, EnumDef, SourceSpan, TypeDef
from contract4agents.diagnostics import Diagnostic
from contract4agents.semantic_checks._agents import check_agent
from contract4agents.semantic_checks._contracts import (
    check_agent_contract,
    check_composition,
    check_control,
    check_external_context,
    check_isolation,
    check_operational_control,
    check_quality,
    check_tool,
)
from contract4agents.semantic_checks._expressions import check_eval
from contract4agents.semantic_checks._index import ProjectIndex
from contract4agents.semantic_checks._run_specs import check_run_spec
from contract4agents.semantic_checks._types import check_datasource, check_enum, check_type


@dataclass(frozen=True)
class SemanticResult:
    diagnostics: list[Diagnostic]

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


def analyze_project(project: ContractProject) -> SemanticResult:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(
        _duplicates(
            [(item.name, item.span) for module in project.modules for item in module.types]
            + [(item.name, item.span) for module in project.modules for item in module.enums],
            "type",
        )
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
        _duplicates([(item.name, item.span) for module in project.modules for item in module.tools], "tool")
    )
    diagnostics.extend(
        _duplicates(
            [(item.name, item.span) for module in project.modules for item in module.external_contexts],
            "external context",
        )
    )
    diagnostics.extend(
        _duplicates(
            [(item.name, item.span) for module in project.modules for item in module.compositions],
            "composition",
        )
    )
    diagnostics.extend(
        _duplicates([(item.name, item.span) for module in project.modules for item in module.isolations], "isolation")
    )
    diagnostics.extend(
        _duplicates(
            [(f"{item.agent}:{item.name}", item.span) for module in project.modules for item in module.controls],
            "control",
        )
    )
    diagnostics.extend(
        _duplicates(
            [(f"{item.agent}:{item.name}", item.span) for module in project.modules for item in module.qualities],
            "quality",
        )
    )
    diagnostics.extend(
        _duplicates(
            [
                (f"{item.agent}:{item.name}", item.span)
                for module in project.modules
                for item in module.operational_controls
            ],
            "operational control",
        )
    )
    diagnostics.extend(
        _duplicates(
            [(f"{item.agent}:{item.name}", item.span) for module in project.modules for item in module.evals],
            "eval",
        )
    )
    diagnostics.extend(
        _duplicates(
            [(item.name, item.span) for module in project.modules for item in module.run_specs],
            "run spec",
        )
    )
    diagnostics.extend(_generated_control_identity_collisions(project))
    index = ProjectIndex.from_project(project)
    for type_def in index.type_defs.values():
        if isinstance(type_def, TypeDef):
            diagnostics.extend(check_type(type_def, index))
        elif isinstance(type_def, EnumDef):
            diagnostics.extend(check_enum(type_def))
    for datasource in index.datasource_defs.values():
        diagnostics.extend(check_datasource(datasource, index))
    for tool in index.tool_defs.values():
        diagnostics.extend(check_tool(tool, index))
    for external_context in index.external_context_defs.values():
        diagnostics.extend(check_external_context(external_context, index))
    for isolation in index.isolation_defs.values():
        diagnostics.extend(check_isolation(isolation))
    for agent in index.agent_defs.values():
        diagnostics.extend(check_agent(agent, index))
        diagnostics.extend(check_agent_contract(agent, index))
    for composition in index.composition_defs.values():
        diagnostics.extend(check_composition(composition, index))
    for control in project.controls:
        diagnostics.extend(check_control(control, index))
    for quality in project.qualities:
        diagnostics.extend(check_quality(quality, index))
    for operational_control in project.operational_controls:
        diagnostics.extend(check_operational_control(operational_control, index))
    for run_spec in index.run_spec_defs.values():
        diagnostics.extend(check_run_spec(run_spec, index))
    for eval_case in project.evals:
        diagnostics.extend(check_eval(eval_case.agent, eval_case.expects, eval_case.semantic_expects, index))
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


def _generated_control_identity_collisions(project: ContractProject) -> list[Diagnostic]:
    generated: dict[tuple[str, ...], tuple[str, SourceSpan]] = {}
    for module in project.modules:
        for agent in module.agents:
            output_identity = ("control", agent.name, "output_conformance")
            generated.setdefault(
                output_identity,
                (f"the output-conformance control for agent `{agent.name}`", agent.span),
            )
            for grant in agent.grants:
                if grant.authorization != "approval_required":
                    continue
                approval_identity = ("control", agent.name, "approval", grant.capability)
                generated.setdefault(
                    approval_identity,
                    (
                        f"the approval control for grant `{agent.name}:{grant.capability}`",
                        grant.span or agent.span,
                    ),
                )

    diagnostics: list[Diagnostic] = []
    for module in project.modules:
        for control in module.controls:
            identity = ("control", control.agent, control.name)
            owner = generated.get(identity)
            if owner is None:
                continue
            owner_label, owner_span = owner
            canonical_identity = ":".join(identity)
            diagnostics.append(
                Diagnostic(
                    "SEM000",
                    f"Control declaration `{control.agent}:{control.name}` conflicts with generated "
                    f"canonical identity `{canonical_identity}`",
                    span=control.span,
                    hint=f"This identity is reserved for {owner_label} at {owner_span.display()}. Rename the control.",
                )
            )
    return diagnostics


__all__ = ["SemanticResult", "analyze_project"]
