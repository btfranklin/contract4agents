"""Lark tree to Contract4Agents AST transformation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeGuard, cast

from lark import Token, Transformer

from contract4agents.ast import (
    AgentAttributes,
    AgentDef,
    CompositionDef,
    ContextRequirement,
    ContractModule,
    ControlAttributes,
    ControlDef,
    DatasourceDef,
    EnumDef,
    EvalCase,
    ExternalContextDef,
    FieldDef,
    GrantDef,
    IsolationDef,
    OperationalControlAttributes,
    OperationalControlDef,
    QualityDef,
    RunSpecAttributes,
    RunSpecDef,
    SourceAttribute,
    SourceAttributeValue,
    SourceSpan,
    ToolDef,
    TypeDef,
)
from contract4agents.parser._values import clean_list_item, split_csv, split_default, unquote


class _ModuleTransformer(Transformer[Token, ContractModule]):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def start(self, items: list[object]) -> ContractModule:
        module = ContractModule(path=self.path)
        for item in items:
            if isinstance(item, TypeDef):
                module.types.append(item)
            elif isinstance(item, EnumDef):
                module.enums.append(item)
            elif isinstance(item, ToolDef):
                module.tools.append(item)
            elif isinstance(item, DatasourceDef):
                module.datasources.append(item)
            elif isinstance(item, ExternalContextDef):
                module.external_contexts.append(item)
            elif isinstance(item, AgentDef):
                module.agents.append(item)
            elif isinstance(item, CompositionDef):
                module.compositions.append(item)
            elif isinstance(item, IsolationDef):
                module.isolations.append(item)
            elif isinstance(item, ControlDef):
                module.controls.append(item)
            elif isinstance(item, QualityDef):
                module.qualities.append(item)
            elif isinstance(item, OperationalControlDef):
                module.operational_controls.append(item)
            elif isinstance(item, EvalCase):
                module.evals.append(item)
            elif isinstance(item, RunSpecDef):
                module.run_specs.append(item)
        return module

    def type_def(self, items: list[object]) -> TypeDef:
        name = _token(items[0])
        fields = _type_fields(items[1:])
        return TypeDef(str(name), fields, _span(self.path, name))

    def enum_def(self, items: list[object]) -> EnumDef:
        name = _token(items[0])
        values = next((item for item in items[1:] if _is_string_list(item)), [])
        return EnumDef(str(name), values, _span(self.path, name))

    def enum_block(self, items: list[object]) -> list[str]:
        return [str(item) for item in items]

    def enum_value(self, items: list[object]) -> str:
        return cast(str, json.loads(str(items[0])))

    def field_block(self, items: list[object]) -> list[FieldDef]:
        return [item for item in items if isinstance(item, FieldDef)]

    def field(self, items: list[object]) -> FieldDef:
        name = _token(items[0])
        type_name, default = split_default(str(items[1]))
        nullable = type_name.endswith("?")
        return FieldDef(str(name), type_name.rstrip("?").strip(), nullable, default, _span(self.path, name))

    def tool_def(self, items: list[object]) -> ToolDef:
        parts = _callable_parts(items)
        attrs = _assignment_attrs(parts.body)
        return ToolDef(
            name=str(parts.name),
            parameters=parts.params,
            return_type=str(parts.return_type),
            description=unquote(str(attrs.get("description", '""'))),
            side_effect=_optional_bool_attr(attrs, "side_effect"),
            span=_span(self.path, parts.name),
        )

    def datasource_def(self, items: list[object]) -> DatasourceDef:
        parts = _callable_parts(items)
        attrs = _assignment_attrs(parts.body)
        return_type = str(parts.return_type)
        return DatasourceDef(
            name=str(parts.name),
            parameters=parts.params,
            return_type=return_type,
            description=unquote(str(attrs.get("description", '""'))),
            render=unquote(str(attrs.get("render", "markdown"))),
            cache=unquote(str(attrs.get("cache", "run"))),
            span=_span(self.path, parts.name),
        )

    def assignment_block(self, items: list[object]) -> list[_Assignment]:
        return [item for item in items if isinstance(item, _Assignment)]

    def external_context_def(self, items: list[object]) -> ExternalContextDef:
        name = _token(items[0])
        attrs = _assignment_attrs(items[2:])
        return ExternalContextDef(
            name=str(name),
            type_name=str(items[1]),
            description=unquote(str(attrs.get("description", '""'))),
            sensitivity=unquote(str(attrs.get("sensitivity", "internal"))),
            render=unquote(str(attrs.get("render", "markdown"))),
            span=_span(self.path, name),
        )

    def isolation_def(self, items: list[object]) -> IsolationDef:
        name = _token(items[0])
        dimensions = {key: unquote(str(value)) for key, value in _assignment_attrs(items[1:]).items()}
        return IsolationDef(str(name), dimensions, _span(self.path, name))

    def composition_def(self, items: list[object]) -> CompositionDef:
        name = _token(items[0])
        attrs = _assignment_attrs(items[3:])
        mappings = _mapping_attrs(items[3:])
        return CompositionDef(
            name=str(name),
            source_agent=str(items[1]),
            target_agent=str(items[2]),
            mode=unquote(str(attrs.get("mode", ""))),
            description=unquote(str(attrs.get("description", '""'))),
            history=unquote(str(attrs.get("history", "none"))),
            mappings=mappings,
            isolation=_optional_unquoted(attrs.get("isolation")),
            span=_span(self.path, name),
        )

    def composition_block(self, items: list[object]) -> list[_Assignment | _Mapping]:
        return [item for item in items if isinstance(item, _Assignment | _Mapping)]

    def map_stmt(self, items: list[object]) -> _Mapping:
        name = _token(items[0])
        return _Mapping(str(name), str(items[1]).strip(), _span(self.path, name))

    def control_def(self, items: list[object]) -> ControlDef:
        name = _token(items[0])
        attributes = _source_attributes(items[2:])
        return ControlDef(
            str(name),
            str(items[1]),
            ControlAttributes(
                assessment=attributes.pop("assessment", None),
                requirement=attributes.pop("require", None),
                severity=attributes.pop("severity", None),
                required=attributes.pop("required", None),
                audience=attributes.pop("audience", None),
                condition=attributes.pop("when", None),
                expected_evidence=attributes.pop("expected_evidence", None),
                unknown=tuple(attributes.values()),
            ),
            _span(self.path, name),
        )

    def quality_def(self, items: list[object]) -> QualityDef:
        name = _token(items[0])
        attrs = _assignment_attrs(items[2:])
        return QualityDef(
            str(name),
            str(items[1]),
            unquote(str(attrs.get("rubric", '""'))),
            _list_attr(attrs, "audience"),
            _span(self.path, name),
        )

    def operational_control_def(self, items: list[object]) -> OperationalControlDef:
        name = _token(items[0])
        attributes = _source_attributes(items[2:])
        return OperationalControlDef(
            str(name),
            str(items[1]),
            OperationalControlAttributes(
                requirement=attributes.pop("require", None),
                severity=attributes.pop("severity", None),
                audience=attributes.pop("audience", None),
                window=attributes.pop("window", None),
                unknown=tuple(attributes.values()),
            ),
            _span(self.path, name),
        )

    def agent_def(self, items: list[object]) -> AgentDef:
        agent_parts = _agent_parts(items)
        grants: list[GrantDef] = []
        context: list[ContextRequirement] = []
        source_attributes: list[SourceAttribute] = []
        for item in agent_parts.body:
            if isinstance(item, GrantDef):
                grants.append(item)
            elif isinstance(item, ContextRequirement):
                context.append(item)
            elif isinstance(item, _Assignment):
                source_attributes.append(_source_attribute(item))
        attributes = {item.name: item for item in source_attributes}
        return AgentDef(
            str(agent_parts.name),
            agent_parts.params,
            str(agent_parts.return_type),
            AgentAttributes(
                goal=attributes.pop("goal", None),
                description=attributes.pop("description", None),
                guidance=attributes.pop("guidance", None),
                unknown=tuple(attributes.values()),
            ),
            _span(self.path, agent_parts.name),
            grants,
            context,
        )

    def agent_block(self, items: list[object]) -> list[_Assignment | GrantDef | ContextRequirement]:
        return [item for item in items if isinstance(item, _Assignment | GrantDef | ContextRequirement)]

    def params(self, items: list[object]) -> list[FieldDef]:
        return [item for item in items if isinstance(item, FieldDef)]

    def param(self, items: list[object]) -> FieldDef:
        name = _token(items[0])
        raw_type = str(items[1])
        return FieldDef(str(name), raw_type.rstrip("?"), raw_type.endswith("?"), span=_span(self.path, name))

    def named_type_ref(self, items: list[object]) -> str:
        return str(items[0])

    def list_type_ref(self, items: list[object]) -> str:
        return f"list[{items[0]}]"

    def map_type_ref(self, items: list[object]) -> str:
        return f"map[string,{items[0]}]"

    def type_constraint(self, items: list[object]) -> str:
        return f"{items[0]}={items[1]}"

    def constraint_block(self, items: list[object]) -> tuple[str, ...]:
        return tuple(str(item) for item in items)

    def nullable_suffix(self, items: list[object]) -> str:
        return "?"

    def type_ref(self, items: list[object]) -> str:
        source = str(items[0])
        constraints = next((item for item in items[1:] if _is_string_tuple(item)), ())
        if constraints:
            source += f"({','.join(constraints)})"
        if "?" in items[1:]:
            source += "?"
        return source

    def grant_stmt(self, items: list[object]) -> GrantDef:
        name = _token(items[0])
        attrs = _assignment_attrs(items[1:])
        return GrantDef(
            capability=str(name),
            availability=_optional_unquoted(attrs.get("availability")),
            authorization=_optional_unquoted(attrs.get("authorization")),
            execution=_optional_unquoted(attrs.get("execution")),
            isolation=_optional_unquoted(attrs.get("isolation")),
            span=_span(self.path, name),
        )

    def context_stmt(self, items: list[object]) -> ContextRequirement:
        name = _token(items[0])
        source = next(
            (str(item) for item in items[3:] if isinstance(item, Token)),
            None,
        )
        mappings = _mapping_attrs(items[3:])
        return ContextRequirement(
            name=str(name),
            type_name=str(items[1]),
            origin=str(items[2]),
            source=source,
            mappings=mappings,
            span=_span(self.path, name),
        )

    def context_block(self, items: list[object]) -> list[_Mapping]:
        return [item for item in items if isinstance(item, _Mapping)]

    def assignment(self, items: list[object]) -> _Assignment:
        name = _token(items[0])
        value = items[1]
        if not isinstance(value, str) and not _is_string_list(value):
            raise TypeError("Assignment values must be text or a list of text")
        return _Assignment(str(name), value, _span(self.path, name))

    def scalar_value(self, items: list[object]) -> str:
        return str(items[0]).strip()

    def list_value(self, items: list[object]) -> list[str]:
        for item in items:
            if _is_string_list(item):
                return item
        return []

    def inline_list(self, items: list[object]) -> list[str]:
        content = next(
            (str(item) for item in items if isinstance(item, Token) and item.type == "INLINE_LIST_CONTENT"),
            "",
        )
        return [clean_list_item(item) for item in split_csv(content)]

    def block_list(self, items: list[object]) -> list[str]:
        return [str(item) for item in items if isinstance(item, str) and not isinstance(item, Token)]

    def block_list_item(self, items: list[object]) -> str:
        return clean_list_item(str(items[0]))

    def eval_def(self, items: list[object]) -> EvalCase:
        name = _token(items[0])
        agent = str(items[1])
        givens: dict[str, str] = {}
        expects: list[str] = []
        semantic: list[str] = []
        for item in _eval_statements(items[2:]):
            if item.kind == "given" and item.key:
                givens[item.key] = item.value
            elif item.value.startswith(("semantic(", "quality(")):
                semantic.append(item.value)
            else:
                expects.append(item.value)
        return EvalCase(str(name), agent, givens, expects, semantic, _span(self.path, name))

    def eval_block(self, items: list[object]) -> list[_EvalStatement]:
        return [item for item in items if isinstance(item, _EvalStatement)]

    def given_stmt(self, items: list[object]) -> _EvalStatement:
        return _EvalStatement("given", str(items[0]), str(items[1]).strip())

    def expect_stmt(self, items: list[object]) -> _EvalStatement:
        return _EvalStatement("expect", "", str(items[0]).strip())

    def run_spec_def(self, items: list[object]) -> RunSpecDef:
        name = _token(items[0])
        attributes = _source_attributes(items[1:])
        return RunSpecDef(
            str(name),
            RunSpecAttributes(
                stages=attributes.pop("stages", None),
                assertions=attributes.pop("assertions", None),
                derived_values=attributes.pop("derived_values", None),
                unknown=tuple(attributes.values()),
            ),
            _span(self.path, name),
        )

    def run_spec_block(self, items: list[object]) -> list[_Assignment]:
        return [item for item in items if isinstance(item, _Assignment)]


@dataclass(frozen=True)
class _AgentParts:
    name: Token
    params: list[FieldDef]
    return_type: Token
    body: list[_Assignment | GrantDef | ContextRequirement]


@dataclass(frozen=True)
class _CallableParts:
    name: Token
    params: list[FieldDef]
    return_type: str
    body: list[_Assignment]


@dataclass(frozen=True)
class _Assignment:
    key: str
    value: str | list[str]
    span: SourceSpan


@dataclass(frozen=True)
class _Mapping:
    key: str
    value: str
    span: SourceSpan


@dataclass(frozen=True)
class _EvalStatement:
    kind: Literal["given", "expect"]
    key: str
    value: str


def _agent_parts(items: list[object]) -> _AgentParts:
    payload = _agent_payload_items(items)
    name = _token(payload[0])
    cursor = 1
    params: list[FieldDef] = []
    candidate = payload[cursor] if cursor < len(payload) else None
    if _is_field_list(candidate):
        params = candidate
        cursor += 1
    return_type = _token(payload[cursor])
    body = [item for item in payload[cursor + 1 :] if isinstance(item, _Assignment | GrantDef | ContextRequirement)]
    return _AgentParts(name, params, return_type, body)


def _callable_parts(items: list[object]) -> _CallableParts:
    name = _token(items[0])
    cursor = 1
    params: list[FieldDef] = []
    candidate = items[cursor] if cursor < len(items) else None
    if _is_field_list(candidate):
        params = candidate
        cursor += 1
    return_type = str(items[cursor])
    return _CallableParts(name, params, return_type, _assignment_items(items[cursor + 1 :]))


def _type_fields(items: Sequence[object]) -> list[FieldDef]:
    fields: list[FieldDef] = []
    for item in items:
        if isinstance(item, list):
            fields.extend(_type_fields(item))
        elif isinstance(item, FieldDef):
            fields.append(item)
    return fields


def _assignment_items(items: Sequence[object]) -> list[_Assignment]:
    assignments: list[_Assignment] = []
    for item in items:
        if isinstance(item, list):
            assignments.extend(_assignment_items(item))
        elif isinstance(item, _Assignment):
            assignments.append(item)
    return assignments


def _assignment_attrs(items: Sequence[object]) -> dict[str, str | list[str]]:
    return {item.key: item.value for item in _assignment_items(items)}


def _source_attribute(assignment: _Assignment) -> SourceAttribute:
    value: SourceAttributeValue = tuple(assignment.value) if isinstance(assignment.value, list) else assignment.value
    return SourceAttribute(assignment.key, value, assignment.span)


def _source_attributes(items: Sequence[object]) -> dict[str, SourceAttribute]:
    attributes = (_source_attribute(item) for item in _assignment_items(items))
    return {item.name: item for item in attributes}


def _mapping_attrs(items: Sequence[object]) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for item in items:
        if isinstance(item, list):
            mappings.update(_mapping_attrs(item))
        elif isinstance(item, _Mapping):
            mappings[item.key] = item.value
    return mappings


def _agent_payload_items(items: Sequence[object]) -> list[object]:
    result: list[object] = []
    for item in items:
        if isinstance(item, list) and not _is_field_list(item):
            result.extend(_agent_payload_items(item))
        else:
            result.append(item)
    return result


def _eval_statements(items: Sequence[object]) -> list[_EvalStatement]:
    statements: list[_EvalStatement] = []
    for item in items:
        if isinstance(item, list):
            statements.extend(_eval_statements(item))
        elif isinstance(item, _EvalStatement):
            statements.append(item)
    return statements


def _is_field_list(value: object) -> TypeGuard[list[FieldDef]]:
    return isinstance(value, list) and all(isinstance(item, FieldDef) for item in value)


def _is_string_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_string_tuple(value: object) -> TypeGuard[tuple[str, ...]]:
    return isinstance(value, tuple) and all(isinstance(item, str) for item in value)


def _list_attr(attributes: Mapping[str, str | list[str]], key: str) -> list[str]:
    value = attributes.get(key, [])
    return value if isinstance(value, list) else []


def _optional_unquoted(value: object) -> str | None:
    if value is None:
        return None
    return unquote(str(value))


def _optional_bool_attr(attributes: Mapping[str, str | list[str]], key: str) -> bool | None:
    value = attributes.get(key)
    if value is None:
        return None
    normalized = unquote(str(value)).lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _token(value: object) -> Token:
    if not isinstance(value, Token):
        raise TypeError(f"Expected token, got {type(value).__name__}")
    return value


def _span(path: Path, token: Token) -> SourceSpan:
    return SourceSpan(path, token.line or 1, token.column or 1)
