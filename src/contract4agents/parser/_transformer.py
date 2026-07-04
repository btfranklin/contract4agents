"""Lark tree to Contract4Agents AST transformation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from lark import Token, Transformer

from contract4agents.ast import (
    AgentDef,
    ContractModule,
    DatasourceDef,
    EvalCase,
    FieldDef,
    MonitorDef,
    Permission,
    RunContractDef,
    SourceSpan,
    TypeDef,
    UseDecl,
    UseKind,
)
from contract4agents.diagnostics import ContractError, Diagnostic
from contract4agents.parser._values import clean_list_item, coerce_name_list, split_csv, split_default, unquote


class _ModuleTransformer(Transformer[Any, Any]):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def start(self, items: list[Any]) -> ContractModule:
        module = ContractModule(path=self.path)
        for item in items:
            if isinstance(item, TypeDef):
                module.types.append(item)
            elif isinstance(item, DatasourceDef):
                module.datasources.append(item)
            elif isinstance(item, AgentDef):
                module.agents.append(item)
            elif isinstance(item, EvalCase):
                module.evals.append(item)
            elif isinstance(item, MonitorDef):
                module.monitors.append(item)
            elif isinstance(item, RunContractDef):
                module.run_contracts.append(item)
        return module

    def type_def(self, items: list[Any]) -> TypeDef:
        name = _token(items[0])
        fields = _type_fields(items[1:])
        return TypeDef(str(name), fields, _span(self.path, name))

    def python_type_def(self, items: list[Any]) -> TypeDef:
        name = _token(items[0])
        return TypeDef(str(name), [], _span(self.path, name), source="python", python_ref=unquote(str(items[1])))

    def field_block(self, items: list[Any]) -> list[FieldDef]:
        return [item for item in items if isinstance(item, FieldDef)]

    def field(self, items: list[Any]) -> FieldDef:
        name = _token(items[0])
        type_name, default = split_default(str(items[1]))
        nullable = type_name.endswith("?")
        return FieldDef(str(name), type_name.rstrip("?").strip(), nullable, default, _span(self.path, name))

    def datasource_def(self, items: list[Any]) -> DatasourceDef:
        name = _token(items[0])
        attrs = _assignment_attrs(items[1:])
        required = coerce_name_list(attrs.get("requires", []))
        python_ref = unquote(str(attrs.get("python", "")))
        produces = str(attrs.get("produces", name)).strip()
        if not python_ref:
            _raise(
                "PARSE005",
                "Datasource requires a python reference",
                self.path,
                name.line or 1,
                column=name.column or 1,
            )
        return DatasourceDef(
            name=str(name),
            python=python_ref,
            requires=required,
            produces=produces,
            render=unquote(str(attrs.get("render", '"markdown"'))),
            cache=unquote(str(attrs.get("cache", '"run"'))),
            span=_span(self.path, name),
        )

    def assignment_block(self, items: list[Any]) -> list[Any]:
        return items

    def agent_def(self, items: list[Any]) -> AgentDef:
        agent_parts = _agent_parts(items)
        uses: list[UseDecl] = []
        attributes: dict[str, Any] = {}
        attribute_spans: dict[str, SourceSpan] = {}
        for item in agent_parts.body:
            if isinstance(item, UseDecl):
                uses.append(item)
            elif isinstance(item, _Assignment):
                attributes[item.key] = item.value
                attribute_spans[item.key] = item.span
        return AgentDef(
            str(agent_parts.name),
            agent_parts.params,
            str(agent_parts.return_type),
            uses,
            attributes,
            _span(self.path, agent_parts.name),
            attribute_spans,
        )

    def agent_block(self, items: list[Any]) -> list[Any]:
        return items

    def params(self, items: list[Any]) -> list[FieldDef]:
        return [item for item in items if isinstance(item, FieldDef)]

    def param(self, items: list[Any]) -> FieldDef:
        name = _token(items[0])
        raw_type = str(items[1])
        return FieldDef(str(name), raw_type.rstrip("?"), raw_type.endswith("?"), span=_span(self.path, name))

    def use_stmt(self, items: list[Any]) -> UseDecl:
        kind = cast(UseKind, str(items[0]))
        name = _token(items[1])
        source = str(items[2])
        permission = cast(Permission, str(items[3])) if len(items) > 3 else "available"
        return UseDecl(kind, str(name), source, permission, _span(self.path, name))

    def hosted_use_stmt(self, items: list[Any]) -> UseDecl:
        name = _token(items[0])
        config: dict[str, str] = {}
        permission: Permission = "available"
        for item in items[1:]:
            if isinstance(item, tuple):
                key, value = cast(tuple[str, str], item)
                config[key] = value
            elif isinstance(item, str):
                permission = cast(Permission, item)
        return UseDecl("hosted_tool", str(name), "", permission, _span(self.path, name), config)

    def hosted_option(self, items: list[Any]) -> tuple[str, str]:
        return str(items[0]), unquote(str(items[1]))

    def preapproved(self, _items: list[Any]) -> str:
        return "preapproved"

    def denied(self, _items: list[Any]) -> str:
        return "denied"

    def available(self, _items: list[Any]) -> str:
        return "available"

    def sandboxed(self, _items: list[Any]) -> str:
        return "sandboxed"

    def requires_approval(self, _items: list[Any]) -> str:
        return "requires_approval"

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
            if not isinstance(item, tuple):
                continue
            kind, key, value = _eval_tuple(item)
            if kind == "given" and key:
                givens[key] = value
            elif value.startswith("semantic("):
                semantic.append(value)
            else:
                expects.append(value)
        return EvalCase(str(name), agent, givens, expects, semantic, _span(self.path, name))

    def eval_block(self, items: list[Any]) -> list[Any]:
        return items

    def given_stmt(self, items: list[Any]) -> tuple[str, str, str]:
        return "given", str(items[0]), str(items[1]).strip()

    def expect_stmt(self, items: list[Any]) -> tuple[str, str, str]:
        return "expect", "", str(items[0]).strip()

    def monitor_def(self, items: list[Any]) -> MonitorDef:
        name = _token(items[0])
        agent = str(items[1])
        severity = "medium"
        condition = ""
        expectation = ""
        for item in _monitor_statements(items[2:]):
            if not isinstance(item, tuple):
                continue
            key, value = cast(tuple[str, str], item)
            if key == "severity":
                severity = unquote(value)
            elif key == "when":
                condition = value
            elif key == "expect":
                expectation = value
        return MonitorDef(str(name), agent, severity, condition, expectation, _span(self.path, name))

    def monitor_block(self, items: list[Any]) -> list[Any]:
        return items

    def severity_stmt(self, items: list[Any]) -> tuple[str, str]:
        return "severity", str(items[0]).strip()

    def when_stmt(self, items: list[Any]) -> tuple[str, str]:
        return "when", str(items[0]).strip()

    def monitor_expect_stmt(self, items: list[Any]) -> tuple[str, str]:
        return "expect", str(items[0]).strip()

    def run_contract_def(self, items: list[Any]) -> RunContractDef:
        name = _token(items[0])
        attributes = _assignment_attrs(items[1:])
        attribute_spans = _assignment_spans(items[1:])
        return RunContractDef(
            str(name),
            _list_attr(attributes, "stages"),
            _list_attr(attributes, "assertions"),
            attributes,
            _span(self.path, name),
            attribute_spans,
        )

    def run_contract_block(self, items: list[Any]) -> list[Any]:
        return items


@dataclass(frozen=True)
class _AgentParts:
    name: Token
    params: list[FieldDef]
    return_type: Token
    body: list[Any]


@dataclass(frozen=True)
class _Assignment:
    key: str
    value: Any
    span: SourceSpan


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


def _agent_payload_items(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if isinstance(item, list) and not _is_field_list(item):
            result.extend(_agent_payload_items(item))
        else:
            result.append(item)
    return result


def _eval_statements(items: list[Any]) -> list[tuple[Any, ...]]:
    statements: list[tuple[Any, ...]] = []
    for item in items:
        if isinstance(item, list):
            statements.extend(_eval_statements(item))
        elif isinstance(item, tuple):
            statements.append(item)
    return statements


def _monitor_statements(items: list[Any]) -> list[tuple[Any, ...]]:
    statements: list[tuple[Any, ...]] = []
    for item in items:
        if isinstance(item, list):
            statements.extend(_monitor_statements(item))
        elif isinstance(item, tuple):
            statements.append(item)
    return statements


def _is_field_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, FieldDef) for item in value)


def _eval_tuple(value: tuple[Any, ...]) -> tuple[str, str, str]:
    if len(value) == 3:
        return str(value[0]), str(value[1]), str(value[2])
    return str(value[0]), "", str(value[-1])


def _list_attr(attributes: dict[str, Any], key: str) -> list[str]:
    value = attributes.get(key, [])
    return value if isinstance(value, list) else []


def _token(value: Any) -> Token:
    if not isinstance(value, Token):
        raise TypeError(f"Expected token, got {type(value).__name__}")
    return value


def _span(path: Path, token: Token) -> SourceSpan:
    return SourceSpan(path, token.line or 1, token.column or 1)


def _raise(
    code: str,
    message: str,
    path: Path,
    line: int,
    hint: str | None = None,
    column: int = 1,
) -> NoReturn:
    raise ContractError([Diagnostic(code, message, span=SourceSpan(path, line, column), hint=hint)])
