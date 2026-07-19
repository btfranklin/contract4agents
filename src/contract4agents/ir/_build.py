"""Build canonical IR from a semantically valid contract project."""

from __future__ import annotations

import json
import re
from typing import Any, cast

from contract4agents.ast import ContractProject, EnumDef, FieldDef
from contract4agents.ast import SourceSpan as AstSourceSpan
from contract4agents.diagnostics import raise_if_errors
from contract4agents.ir._collections import FrozenMap, freeze_json
from contract4agents.ir._ids import SemanticId, semantic_id
from contract4agents.ir._model import (
    AgentIR,
    AssessmentMode,
    Audience,
    Authorization,
    Availability,
    CanonicalIR,
    CapabilityIR,
    CompositionEdgeIR,
    CompositionMode,
    ContextOrigin,
    ContextRequirementIR,
    ControlIR,
    EnumIR,
    EvalIR,
    ExecutionBoundary,
    ExternalContextIR,
    GrantIR,
    GuidanceIR,
    HistoryMode,
    IsolationProfileIR,
    OperationalControlIR,
    ParameterIR,
    QualityIR,
    RunSpecDerivedValueIR,
    RunSpecIR,
    RunSpecStageIR,
    Severity,
    SourceSpan,
    TypeFieldIR,
    TypeIR,
)
from contract4agents.ir._type_refs import TypeRef, parse_type_ref
from contract4agents.parser._values import unquote
from contract4agents.run_specs import (
    normalize_derived_value_type,
    parse_run_spec_derived_value_declaration,
    parse_run_spec_stage_declaration,
)
from contract4agents.semantics import analyze_project

_QUALITY_EXPECTATION = re.compile(r"quality\(([A-Za-z_][A-Za-z0-9_]*)\)")


def build_canonical_ir(project: ContractProject) -> CanonicalIR:
    """Build immutable canonical IR from semantically valid source."""

    raise_if_errors(analyze_project(project).diagnostics)

    types = tuple(_type_ir(project, item) for item in project.types.values()) + tuple(
        _enum_ir(project, item) for item in project.enums.values()
    )
    capabilities = tuple(_tool_ir(project, item) for item in project.tools.values()) + tuple(
        _datasource_ir(project, item) for item in project.datasources.values()
    )
    external_contexts = tuple(_external_context_ir(project, item) for item in project.external_contexts.values())
    isolation_profiles = tuple(_isolation_ir(project, item) for item in project.isolations.values())

    grants = tuple(_grant_ir(project, agent.name, grant) for agent in project.agents.values() for grant in agent.grants)
    contexts = tuple(
        _context_ir(project, agent.name, requirement)
        for agent in project.agents.values()
        for requirement in agent.context
    )
    agents = tuple(_agent_ir(project, item) for item in project.agents.values())
    composition = tuple(_composition_ir(project, item) for item in project.compositions.values())

    explicit_controls = tuple(_control_ir(project, item) for item in project.controls)
    derived_controls = tuple(_approval_control(grant) for grant in grants if grant.authorization == "approval_required")
    output_controls = tuple(_output_control(agent) for agent in agents)
    controls = explicit_controls + derived_controls + output_controls

    qualities = tuple(_quality_ir(project, item) for item in project.qualities)
    operational_controls = tuple(_operational_control_ir(project, item) for item in project.operational_controls)
    evals = tuple(_eval_ir(project, item) for item in project.evals)
    run_specs = tuple(_run_spec_ir(project, item) for item in project.run_specs.values())

    return CanonicalIR.create(
        types=types,
        capabilities=capabilities,
        external_contexts=external_contexts,
        contexts=contexts,
        agents=agents,
        grants=grants,
        composition=composition,
        controls=controls,
        qualities=qualities,
        operational_controls=operational_controls,
        isolation_profiles=isolation_profiles,
        evals=evals,
        run_specs=run_specs,
    )


def _type_ir(project: ContractProject, item: Any) -> TypeIR:
    fields = tuple(
        TypeFieldIR(
            name=field.name,
            type_ref=_field_type_ref(field),
            has_default=field.default is not None,
            default=freeze_json(_default_value(field.default)) if field.default is not None else None,
            span=_span(project, field.span),
        )
        for field in item.fields
    )
    return TypeIR(
        id=semantic_id("type", item.name),
        name=item.name,
        fields=fields,
        span=_span(project, item.span),
    )


def _enum_ir(project: ContractProject, item: EnumDef) -> EnumIR:
    return EnumIR(
        id=semantic_id("type", item.name),
        name=item.name,
        values=tuple(item.values),
        span=_span(project, item.span),
    )


def _tool_ir(project: ContractProject, item: Any) -> CapabilityIR:
    return CapabilityIR(
        id=semantic_id("tool", item.name),
        name=item.name,
        kind="tool",
        parameters=tuple(_parameter_ir(project, field) for field in item.parameters),
        output_type=_type_ref(item.return_type),
        description=item.description,
        side_effect=item.side_effect,
        span=_span(project, item.span),
    )


def _datasource_ir(project: ContractProject, item: Any) -> CapabilityIR:
    return CapabilityIR(
        id=semantic_id("datasource", item.name),
        name=item.name,
        kind="datasource",
        parameters=tuple(_parameter_ir(project, field) for field in item.parameters),
        output_type=_type_ref(item.return_type),
        description=item.description,
        render=item.render,
        cache=item.cache,
        span=_span(project, item.span),
    )


def _external_context_ir(project: ContractProject, item: Any) -> ExternalContextIR:
    return ExternalContextIR(
        id=semantic_id("external", item.name),
        name=item.name,
        output_type=_type_ref(item.type_name),
        description=item.description,
        sensitivity=item.sensitivity,
        render=item.render,
        span=_span(project, item.span),
    )


def _grant_ir(project: ContractProject, agent_name: str, item: Any) -> GrantIR:
    isolation_id = semantic_id("isolation", item.isolation) if item.isolation is not None else None
    return GrantIR(
        id=semantic_id("grant", agent_name, item.capability),
        agent_id=semantic_id("agent", agent_name),
        capability_id=semantic_id("tool", item.capability),
        availability=cast(Availability, item.availability),
        authorization=cast(Authorization | None, item.authorization),
        execution=cast(ExecutionBoundary | None, item.execution),
        isolation_id=isolation_id,
        span=_span(project, item.span),
    )


def _context_ir(project: ContractProject, agent_name: str, item: Any) -> ContextRequirementIR:
    return ContextRequirementIR(
        id=semantic_id("context", agent_name, item.name),
        agent_id=semantic_id("agent", agent_name),
        name=item.name,
        type_ref=_type_ref(item.type_name),
        origin=cast(ContextOrigin, item.origin),
        origin_id=_context_origin_id(item.origin, item.source),
        input_mappings=FrozenMap(sorted(item.mappings.items())),
        span=_span(project, item.span),
    )


def _agent_ir(project: ContractProject, item: Any) -> AgentIR:
    return AgentIR(
        id=semantic_id("agent", item.name),
        name=item.name,
        parameters=tuple(_parameter_ir(project, field) for field in item.parameters),
        output_type=_type_ref(item.return_type),
        goal=unquote(item.text_attr("goal")),
        description=unquote(item.text_attr("description")),
        guidance=tuple(GuidanceIR(text) for text in item.list_attr("guidance")),
        grant_ids=tuple(semantic_id("grant", item.name, grant.capability) for grant in item.grants),
        context_ids=tuple(semantic_id("context", item.name, requirement.name) for requirement in item.context),
        span=_span(project, item.span),
    )


def _composition_ir(project: ContractProject, item: Any) -> CompositionEdgeIR:
    isolation_id = semantic_id("isolation", item.isolation) if item.isolation is not None else None
    return CompositionEdgeIR(
        id=semantic_id("edge", item.name),
        name=item.name,
        source_agent_id=semantic_id("agent", item.source_agent),
        target_agent_id=semantic_id("agent", item.target_agent),
        mode=cast(CompositionMode, item.mode),
        description=item.description,
        history=cast(HistoryMode, item.history),
        input_mappings=FrozenMap(sorted(item.mappings.items())),
        isolation_id=isolation_id,
        span=_span(project, item.span),
    )


def _isolation_ir(project: ContractProject, item: Any) -> IsolationProfileIR:
    dimensions = item.dimensions
    return IsolationProfileIR(
        id=semantic_id("isolation", item.name),
        name=item.name,
        context=dimensions.get("context"),
        capabilities=dimensions.get("capabilities"),
        state=dimensions.get("state"),
        filesystem=dimensions.get("filesystem"),
        network=dimensions.get("network"),
        secrets=dimensions.get("secrets"),
        return_channel=dimensions.get("return"),
        span=_span(project, item.span),
    )


def _control_ir(project: ContractProject, item: Any) -> ControlIR:
    attrs = item.attributes
    return ControlIR(
        id=semantic_id("control", item.agent, item.name),
        name=item.name,
        agent_id=semantic_id("agent", item.agent),
        severity=cast(Severity, _text(attrs.get("severity"), "medium")),
        required=_boolean(attrs.get("required"), default=True),
        audience=_audiences(attrs.get("audience"), default=("adapter", "host", "evaluator", "reviewer")),
        assessment=cast(AssessmentMode, _text(attrs.get("assessment"))),
        condition=_optional_text(attrs.get("when")),
        requirement=_optional_text(attrs.get("require")),
        expected_evidence=tuple(_list(attrs.get("expected_evidence"))),
        span=_span(project, item.span),
    )


def _approval_control(grant: GrantIR) -> ControlIR:
    capability_name = grant.capability_id.parts[0]
    name = f"approval_required_{capability_name.replace('.', '_')}"
    return ControlIR(
        id=semantic_id("control", grant.agent_id.parts[0], "approval", capability_name),
        name=name,
        agent_id=grant.agent_id,
        severity="high",
        required=True,
        audience=("adapter", "host", "evaluator", "reviewer"),
        assessment="runtime",
        derived_from=grant.id,
        expected_evidence=("approval.requested", "approval.completed", "tool.started"),
    )


def _output_control(agent: AgentIR) -> ControlIR:
    return ControlIR(
        id=semantic_id("control", agent.name, "output_conformance"),
        name="output_conformance",
        agent_id=agent.id,
        severity="high",
        required=True,
        audience=("adapter", "host", "evaluator", "reviewer"),
        assessment="adapter",
        derived_from=agent.id,
        expected_evidence=("output.accepted", "output.schema_failed"),
    )


def _quality_ir(project: ContractProject, item: Any) -> QualityIR:
    return QualityIR(
        id=semantic_id("quality", item.agent, item.name),
        name=item.name,
        agent_id=semantic_id("agent", item.agent),
        rubric=item.rubric,
        audience=_audiences(item.audiences, default=("evaluator", "reviewer")),
        span=_span(project, item.span),
    )


def _operational_control_ir(project: ContractProject, item: Any) -> OperationalControlIR:
    attrs = item.attributes
    return OperationalControlIR(
        id=semantic_id("operational", item.agent, item.name),
        name=item.name,
        agent_id=semantic_id("agent", item.agent),
        severity=cast(Severity, _text(attrs.get("severity"), "medium")),
        requirement=_text(attrs.get("require")),
        window=_optional_text(attrs.get("window")),
        audience=_audiences(attrs.get("audience"), default=("evaluator", "reviewer")),
        span=_span(project, item.span),
    )


def _eval_ir(project: ContractProject, item: Any) -> EvalIR:
    all_expectations = tuple(item.expects) + tuple(item.semantic_expects)
    quality_names = tuple(
        match.group(1)
        for expectation in all_expectations
        if (match := _QUALITY_EXPECTATION.fullmatch(expectation)) is not None
    )
    expectations = tuple(
        expectation for expectation in all_expectations if _QUALITY_EXPECTATION.fullmatch(expectation) is None
    )
    return EvalIR(
        id=semantic_id("eval", item.agent, item.name),
        name=item.name,
        agent_id=semantic_id("agent", item.agent),
        givens=FrozenMap(sorted((name, freeze_json(value)) for name, value in item.givens.items())),
        expectations=expectations,
        quality_ids=tuple(semantic_id("quality", item.agent, name) for name in quality_names),
        span=_span(project, item.span),
    )


def _run_spec_ir(project: ContractProject, item: Any) -> RunSpecIR:
    stages: list[RunSpecStageIR] = []
    for raw in item.stages:
        stage = parse_run_spec_stage_declaration(raw)
        if stage is None:
            raise AssertionError("Invalid run spec passed semantic validation")
        stages.append(
            RunSpecStageIR(
                name=stage.name,
                agent_id=semantic_id("agent", stage.agent),
                output_type=_type_ref(stage.output_type),
                cardinality=stage.cardinality,
            )
        )
    derived_values: list[RunSpecDerivedValueIR] = []
    for raw in item.attributes.get("derived_values", []):
        declaration = parse_run_spec_derived_value_declaration(str(raw))
        if declaration is None:
            raise AssertionError("Invalid run spec derived value passed semantic validation")
        normalized_type = normalize_derived_value_type(declaration.type_name)
        if normalized_type is None:
            raise AssertionError("Unsupported run spec derived value passed semantic validation")
        derived_values.append(RunSpecDerivedValueIR(declaration.name, normalized_type))
    return RunSpecIR(
        id=semantic_id("run_spec", item.name),
        name=item.name,
        stages=tuple(stages),
        derived_values=tuple(derived_values),
        assertions=tuple(item.assertions),
        span=_span(project, item.span),
    )


def _parameter_ir(project: ContractProject, field: FieldDef) -> ParameterIR:
    return ParameterIR(
        name=field.name,
        type_ref=_field_type_ref(field),
        required=not field.nullable and field.default is None,
        has_default=field.default is not None,
        default=freeze_json(_default_value(field.default)) if field.default is not None else None,
        span=_span(project, field.span),
    )


def _field_type_ref(field: FieldDef) -> TypeRef:
    source = field.type_name + ("?" if field.nullable else "")
    return _type_ref(source)


def _type_ref(source: str) -> TypeRef:
    return parse_type_ref(source.strip())


def _context_origin_id(origin: str, source: str | None) -> SemanticId | None:
    if source is None:
        return None
    kinds = {
        "datasource": "datasource",
        "external": "external",
        "handoff": "edge",
        "stage": "run_spec",
        "parent": "agent",
    }
    kind = kinds.get(origin)
    if kind is None:
        return None
    return semantic_id(cast(Any, kind), source)


def _span(project: ContractProject, span: AstSourceSpan | None) -> SourceSpan | None:
    if span is None:
        return None
    try:
        relative = span.path.resolve().relative_to(project.root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("Contract source must be inside the project root") from exc
    return SourceSpan(relative, span.line, span.column)


def _default_value(raw: str | None) -> object:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return unquote(raw)


def _text(value: Any, default: str = "") -> str:
    return unquote(str(value)) if value is not None else default


def _optional_text(value: Any) -> str | None:
    return _text(value) if value is not None else None


def _boolean(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    return _text(value).lower() == "true"


def _list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _audiences(value: Any, *, default: tuple[Audience, ...]) -> tuple[Audience, ...]:
    raw = _list(value)
    if not raw:
        return default
    return tuple(cast(Audience, item) for item in raw)


__all__ = ["build_canonical_ir"]
