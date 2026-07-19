"""Lark tree to Contract4Agents AST transformation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from lark import Token, Transformer

from contract4agents.ast import (
    AgentDef,
    CompositionDef,
    ContextRequirement,
    ContractModule,
    ControlDef,
    DatasourceDef,
    EnumDef,
    EvalCase,
    ExternalContextDef,
    FieldDef,
    GrantDef,
    IsolationDef,
    OperationalControlDef,
    QualityDef,
    RunSpecDef,
    SourceSpan,
    ToolDef,
    TypeDef,
)
from contract4agents.parser._values import clean_list_item, split_csv, split_default, unquote


class _ModuleTransformer(Transformer[Any, Any]):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def start(self, items: list[Any]) -> ContractModule:
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

    def type_def(self, items: list[Any]) -> TypeDef:
        name = _token(items[0])
        fields = _type_fields(items[1:])
        return TypeDef(str(name), fields, _span(self.path, name))

    def enum_def(self, items: list[Any]) -> EnumDef:
        name = _token(items[0])
        values = next((item for item in items[1:] if isinstance(item, list)), [])
        return EnumDef(str(name), values, _span(self.path, name))

    def enum_block(self, items: list[Any]) -> list[str]:
        return [str(item) for item in items]

    def enum_value(self, items: list[Any]) -> str:
        return cast(str, json.loads(str(items[0])))

    def field_block(self, items: list[Any]) -> list[FieldDef]:
        return [item for item in items if isinstance(item, FieldDef)]

    def field(self, items: list[Any]) -> FieldDef:
        name = _token(items[0])
        type_name, default = split_default(str(items[1]))
        nullable = type_name.endswith("?")
        return FieldDef(str(name), type_name.rstrip("?").strip(), nullable, default, _span(self.path, name))

    def tool_def(self, items: list[Any]) -> ToolDef:
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

    def datasource_def(self, items: list[Any]) -> DatasourceDef:
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

    def assignment_block(self, items: list[Any]) -> list[Any]:
        return items

    def external_context_def(self, items: list[Any]) -> ExternalContextDef:
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

    def isolation_def(self, items: list[Any]) -> IsolationDef:
        name = _token(items[0])
        dimensions = {key: unquote(str(value)) for key, value in _assignment_attrs(items[1:]).items()}
        return IsolationDef(str(name), dimensions, _span(self.path, name))

    def composition_def(self, items: list[Any]) -> CompositionDef:
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

    def composition_block(self, items: list[Any]) -> list[Any]:
        return items

    def map_stmt(self, items: list[Any]) -> _Mapping:
        name = _token(items[0])
        return _Mapping(str(name), str(items[1]).strip(), _span(self.path, name))

    def control_def(self, items: list[Any]) -> ControlDef:
        name = _token(items[0])
        return ControlDef(str(name), str(items[1]), _assignment_attrs(items[2:]), _span(self.path, name))

    def quality_def(self, items: list[Any]) -> QualityDef:
        name = _token(items[0])
        attrs = _assignment_attrs(items[2:])
        return QualityDef(
            str(name),
            str(items[1]),
            unquote(str(attrs.get("rubric", '""'))),
            _list_attr(attrs, "audience"),
            _span(self.path, name),
        )

    def operational_control_def(self, items: list[Any]) -> OperationalControlDef:
        name = _token(items[0])
        return OperationalControlDef(str(name), str(items[1]), _assignment_attrs(items[2:]), _span(self.path, name))

    def agent_def(self, items: list[Any]) -> AgentDef:
        agent_parts = _agent_parts(items)
        grants: list[GrantDef] = []
        context: list[ContextRequirement] = []
        attributes: dict[str, Any] = {}
        attribute_spans: dict[str, SourceSpan] = {}
        for item in agent_parts.body:
            if isinstance(item, GrantDef):
                grants.append(item)
            elif isinstance(item, ContextRequirement):
                context.append(item)
            elif isinstance(item, _Assignment):
                attributes[item.key] = item.value
                attribute_spans[item.key] = item.span
        return AgentDef(
            str(agent_parts.name),
            agent_parts.params,
            str(agent_parts.return_type),
            attributes,
            _span(self.path, agent_parts.name),
            attribute_spans,
            grants,
            context,
        )

    def agent_block(self, items: list[Any]) -> list[Any]:
        return items

    def params(self, items: list[Any]) -> list[FieldDef]:
        return [item for item in items if isinstance(item, FieldDef)]

    def param(self, items: list[Any]) -> FieldDef:
        name = _token(items[0])
        raw_type = str(items[1])
        return FieldDef(str(name), raw_type.rstrip("?"), raw_type.endswith("?"), span=_span(self.path, name))

    def grant_stmt(self, items: list[Any]) -> GrantDef:
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

    def context_stmt(self, items: list[Any]) -> ContextRequirement:
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

    def context_block(self, items: list[Any]) -> list[Any]:
        return items

    def assignment(self, items: list[Any]) -> _Assignment:
        name = _token(items[0])
        return _Assignment(str(name), items[1], _span(self.path, name))

    def scalar_value(self, items: list[Any]) -> str:
        return str(items[0]).strip()

    def list_value(self, items: list[Any]) -> list[str]:
        for item in items:
            if isinstance(item, list):
                return cast(list[str], item)
        return []

    def inline_list(self, items: list[Any]) -> list[str]:
        content = next(
            (str(item) for item in items if isinstance(item, Token) and item.type == "INLINE_LIST_CONTENT"),
            "",
        )
        return [clean_list_item(item) for item in split_csv(content)]

    def block_list(self, items: list[Any]) -> list[str]:
        return [str(item) for item in items if isinstance(item, str) and not isinstance(item, Token)]

    def block_list_item(self, items: list[Any]) -> str:
        return clean_list_item(str(items[0]))

    def eval_def(self, items: list[Any]) -> EvalCase:
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

    def eval_block(self, items: list[Any]) -> list[Any]:
        return items

    def given_stmt(self, items: list[Any]) -> _EvalStatement:
        return _EvalStatement("given", str(items[0]), str(items[1]).strip())

    def expect_stmt(self, items: list[Any]) -> _EvalStatement:
        return _EvalStatement("expect", "", str(items[0]).strip())

    def run_spec_def(self, items: list[Any]) -> RunSpecDef:
        name = _token(items[0])
        attributes = _assignment_attrs(items[1:])
        attribute_spans = _assignment_spans(items[1:])
        return RunSpecDef(
            str(name),
            _list_attr(attributes, "stages"),
            _list_attr(attributes, "assertions"),
            attributes,
            _span(self.path, name),
            attribute_spans,
        )

    def run_spec_block(self, items: list[Any]) -> list[Any]:
        return items


@dataclass(frozen=True)
class _AgentParts:
    name: Token
    params: list[FieldDef]
    return_type: Token
    body: list[Any]


@dataclass(frozen=True)
class _CallableParts:
    name: Token
    params: list[FieldDef]
    return_type: Token
    body: list[Any]


@dataclass(frozen=True)
class _Assignment:
    key: str
    value: Any
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


def _agent_parts(items: list[Any]) -> _AgentParts:
    payload = _agent_payload_items(items)
    name = _token(payload[0])
    cursor = 1
    params: list[FieldDef] = []
    if cursor < len(payload) and _is_field_list(payload[cursor]):
        params = cast(list[FieldDef], payload[cursor])
        cursor += 1
    return_type = _token(payload[cursor])
    return _AgentParts(name, params, return_type, payload[cursor + 1 :])


def _callable_parts(items: list[Any]) -> _CallableParts:
    name = _token(items[0])
    cursor = 1
    params: list[FieldDef] = []
    if cursor < len(items) and _is_field_list(items[cursor]):
        params = cast(list[FieldDef], items[cursor])
        cursor += 1
    return_type = _token(items[cursor])
    return _CallableParts(name, params, return_type, items[cursor + 1 :])


def _type_fields(items: list[Any]) -> list[FieldDef]:
    fields: list[FieldDef] = []
    for item in items:
        if isinstance(item, list):
            fields.extend(_type_fields(item))
        elif isinstance(item, FieldDef):
            fields.append(item)
    return fields


def _assignment_attrs(items: list[Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for item in items:
        if isinstance(item, list):
            attrs.update(_assignment_attrs(item))
        elif isinstance(item, _Assignment):
            attrs[item.key] = item.value
    return attrs


def _assignment_spans(items: list[Any]) -> dict[str, SourceSpan]:
    spans: dict[str, SourceSpan] = {}
    for item in items:
        if isinstance(item, list):
            spans.update(_assignment_spans(item))
        elif isinstance(item, _Assignment):
            spans[item.key] = item.span
    return spans


def _mapping_attrs(items: list[Any]) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for item in items:
        if isinstance(item, list):
            mappings.update(_mapping_attrs(item))
        elif isinstance(item, _Mapping):
            mappings[item.key] = item.value
    return mappings


def _agent_payload_items(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if isinstance(item, list) and not _is_field_list(item):
            result.extend(_agent_payload_items(item))
        else:
            result.append(item)
    return result


def _eval_statements(items: list[Any]) -> list[_EvalStatement]:
    statements: list[_EvalStatement] = []
    for item in items:
        if isinstance(item, list):
            statements.extend(_eval_statements(item))
        elif isinstance(item, _EvalStatement):
            statements.append(item)
    return statements


def _is_field_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, FieldDef) for item in value)


def _list_attr(attributes: dict[str, Any], key: str) -> list[str]:
    value = attributes.get(key, [])
    return value if isinstance(value, list) else []


def _optional_unquoted(value: Any) -> str | None:
    if value is None:
        return None
    return unquote(str(value))


def _optional_bool_attr(attributes: dict[str, Any], key: str) -> bool | None:
    value = attributes.get(key)
    if value is None:
        return None
    normalized = unquote(str(value)).lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _token(value: Any) -> Token:
    if not isinstance(value, Token):
        raise TypeError(f"Expected token, got {type(value).__name__}")
    return value


def _span(path: Path, token: Token) -> SourceSpan:
    return SourceSpan(path, token.line or 1, token.column or 1)
