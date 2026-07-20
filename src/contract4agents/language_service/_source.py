"""Parse source text and retain cursor-addressable language occurrences."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from lark import Token, Tree

from contract4agents.diagnostics import ContractError
from contract4agents.expressions import expression_source_references
from contract4agents.language_service._help import property_spec
from contract4agents.language_service._model import (
    OccurrenceRole,
    SourceDeclaration,
    SourceDocument,
    SourceOccurrence,
    SourcePosition,
    SourceRange,
    SymbolId,
    SymbolKind,
)
from contract4agents.parser._parse import parse_source_syntax
from contract4agents.run_specs import run_spec_stage_source_components

_PRIMITIVE_TYPES = frozenset({"string", "integer", "float", "boolean", "datetime", "list", "map"})
_TYPE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LIST_ITEM_RE = re.compile(r'"(?:\\.|[^"\\])*"|[^,]+')


def parse_document(path: Path, source: str) -> SourceDocument:
    """Parse an in-memory document with the canonical parser and build its source index."""

    try:
        parsed = parse_source_syntax(path, source)
    except ContractError as exc:
        return SourceDocument(path, source, None, diagnostics=tuple(exc.diagnostics))

    normalized = source if not source or source.endswith("\n") else source + "\n"
    indexer = _TreeIndexer(path, normalized)
    indexer.index(parsed.tree)
    return SourceDocument(
        path,
        source,
        parsed.module,
        tuple(indexer.occurrences),
        tuple(indexer.declarations),
    )


class _TreeIndexer:
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.occurrences: list[SourceOccurrence] = []
        self.declarations: list[SourceDeclaration] = []

    def index(self, tree: Tree[Any]) -> None:
        for declaration in _child_trees(tree):
            self._declaration(declaration)
        self.occurrences.sort(
            key=lambda item: (
                item.range.start.line,
                item.range.start.character,
                item.range.end.line,
                item.range.end.character,
                item.role,
            )
        )

    def _declaration(self, tree: Tree[Any]) -> None:
        handler = getattr(self, f"_index_{tree.data}", None)
        if handler is not None:
            handler(tree)

    def _index_type_def(self, tree: Tree[Any]) -> None:
        tokens = _direct_tokens(tree)
        name = tokens[0]
        selection = self._definition(name, "type")
        fields: list[SourceDeclaration] = []
        block = _child(tree, "field_block")
        if block is not None:
            for field_tree in _children(block, "field"):
                field_tokens = _direct_tokens(field_tree)
                field_name, type_token = field_tokens[0], field_tokens[1]
                field_range = _token_range(field_name)
                self._add(
                    str(field_name),
                    field_range,
                    "definition",
                    symbol=SymbolId("field", str(field_name), str(name)),
                    context="type.field",
                    container=str(name),
                )
                self._type_references(type_token, owner=str(name))
                fields.append(
                    SourceDeclaration(
                        str(field_name),
                        "field",
                        _tree_range(field_tree),
                        field_range,
                        str(type_token).strip(),
                    )
                )
        self._record_declaration(tree, str(name), "type", selection, tuple(fields))

    def _index_enum_def(self, tree: Tree[Any]) -> None:
        name = _direct_tokens(tree)[0]
        selection = self._definition(name, "type", context="enum")
        block = _child(tree, "enum_block")
        children: list[SourceDeclaration] = []
        if block is not None:
            for value_tree in _children(block, "enum_value"):
                token = _direct_tokens(value_tree)[0]
                value_range = _token_range(token)
                value = str(token).strip('"')
                self._add(value, value_range, "value", context="enum.member", container=str(name))
                children.append(SourceDeclaration(value, "field", _tree_range(value_tree), value_range, "enum member"))
        self._record_declaration(tree, str(name), "enum", selection, tuple(children))

    def _index_tool_def(self, tree: Tree[Any]) -> None:
        self._callable(tree, "tool")

    def _index_datasource_def(self, tree: Tree[Any]) -> None:
        self._callable(tree, "datasource")

    def _callable(self, tree: Tree[Any], kind: SymbolKind) -> None:
        tokens = _direct_tokens(tree)
        name, return_type = tokens[0], tokens[-1]
        selection = self._definition(name, kind)
        children = self._parameters(tree, str(name))
        self._type_references(return_type, owner=str(name))
        block = _child(tree, "assignment_block")
        if block is not None:
            self._assignment_block(block, kind, str(name))
        detail = f"{name}(...) -> {return_type}"
        self._record_declaration(tree, str(name), kind, selection, tuple(children), detail)

    def _index_external_context_def(self, tree: Tree[Any]) -> None:
        name, type_token = _direct_tokens(tree)
        selection = self._definition(name, "external_context")
        self._type_references(type_token, owner=str(name))
        block = _child(tree, "assignment_block")
        if block is not None:
            self._assignment_block(block, "external_context", str(name))
        self._record_declaration(
            tree,
            str(name),
            "external_context",
            selection,
            detail=f"{name} -> {type_token}",
        )

    def _index_isolation_def(self, tree: Tree[Any]) -> None:
        name = _direct_tokens(tree)[0]
        selection = self._definition(name, "isolation")
        block = _child(tree, "assignment_block")
        if block is not None:
            self._assignment_block(block, "isolation", str(name))
        self._record_declaration(tree, str(name), "isolation", selection)

    def _index_agent_def(self, tree: Tree[Any]) -> None:
        tokens = _direct_tokens(tree)
        name, return_type = tokens[0], tokens[-1]
        selection = self._definition(name, "agent")
        children = self._parameters(tree, str(name))
        self._type_references(return_type, owner=str(name))
        block = _child(tree, "agent_block")
        if block is not None:
            for statement in _child_trees(block):
                if statement.data == "grant_stmt":
                    self._grant(statement, str(name))
                elif statement.data == "context_stmt":
                    self._context(statement, str(name))
                elif statement.data == "assignment":
                    self._assignment(statement, "agent", str(name))
        detail = f"agent {name}(...) -> {return_type}"
        self._record_declaration(tree, str(name), "agent", selection, tuple(children), detail)

    def _index_composition_def(self, tree: Tree[Any]) -> None:
        name, source, target = _direct_tokens(tree)
        selection = self._definition(name, "composition")
        self._reference(source, "agent", context="composition.source", container=str(name))
        self._reference(target, "agent", context="composition.target", container=str(name))
        block = _child(tree, "composition_block")
        if block is not None:
            for statement in _child_trees(block):
                if statement.data == "assignment":
                    self._assignment(statement, "composition", str(name))
                elif statement.data == "map_stmt":
                    key = _direct_tokens(statement)[0]
                    self._add(
                        str(key),
                        _token_range(key),
                        "property",
                        context="composition.map",
                        container=str(name),
                    )
        detail = f"composition {name} from {source} to {target}"
        self._record_declaration(tree, str(name), "composition", selection, detail=detail)

    def _index_control_def(self, tree: Tree[Any]) -> None:
        self._agent_scoped_declaration(tree, "control")

    def _index_quality_def(self, tree: Tree[Any]) -> None:
        self._agent_scoped_declaration(tree, "quality")

    def _index_operational_control_def(self, tree: Tree[Any]) -> None:
        self._agent_scoped_declaration(tree, "operational_control")

    def _agent_scoped_declaration(self, tree: Tree[Any], kind: SymbolKind) -> None:
        name, agent = _direct_tokens(tree)
        container = f"{agent}:{name}"
        selection = self._definition(name, kind, symbol_owner=str(agent), container=str(agent))
        self._reference(agent, "agent", context=f"{kind}.agent", container=str(name))
        block = _child(tree, "assignment_block")
        if block is not None:
            self._assignment_block(block, kind, container)
        self._record_declaration(tree, str(name), kind, selection, detail=f"{kind} for {agent}")

    def _index_eval_def(self, tree: Tree[Any]) -> None:
        name, agent = _direct_tokens(tree)
        container = f"{agent}:{name}"
        selection = self._definition(name, "eval", symbol_owner=str(agent), container=str(agent))
        self._reference(agent, "agent", context="eval.agent", container=str(name))
        block = _child(tree, "eval_block")
        if block is not None:
            for statement in _child_trees(block):
                tokens = _direct_tokens(statement)
                if statement.data == "given_stmt" and len(tokens) > 1:
                    self._add(
                        str(tokens[0]),
                        _token_range(tokens[0]),
                        "property",
                        context="eval.given",
                        container=container,
                    )
                    self._index_expression_token(tokens[1], owner=container)
                elif statement.data == "expect_stmt" and tokens:
                    self._index_expression_token(tokens[0], owner=container)
        self._record_declaration(tree, str(name), "eval", selection, detail=f"eval for {agent}")

    def _index_run_spec_def(self, tree: Tree[Any]) -> None:
        name = _direct_tokens(tree)[0]
        selection = self._definition(name, "run_spec")
        block = _child(tree, "run_spec_block")
        if block is not None:
            self._assignment_block(block, "run_spec", str(name))
        self._record_declaration(tree, str(name), "run_spec", selection)

    def _parameters(self, tree: Tree[Any], owner: str) -> list[SourceDeclaration]:
        params = _child(tree, "params")
        declarations: list[SourceDeclaration] = []
        if params is None:
            return declarations
        for param in _children(params, "param"):
            name, type_token = _direct_tokens(param)
            name_range = _token_range(name)
            self._add(
                str(name),
                name_range,
                "definition",
                symbol=SymbolId("field", str(name), owner),
                context="parameter",
                container=owner,
            )
            self._type_references(type_token, owner=owner)
            declarations.append(
                SourceDeclaration(str(name), "field", _tree_range(param), name_range, str(type_token))
            )
        return declarations

    def _grant(self, tree: Tree[Any], agent: str) -> None:
        capability = _direct_tokens(tree)[0]
        self._reference(capability, "tool", context="grant.capability", container=agent)
        block = _child(tree, "assignment_block")
        if block is not None:
            self._assignment_block(block, "grant", f"{agent}:{capability}")

    def _context(self, tree: Tree[Any], agent: str) -> None:
        tokens = _direct_tokens(tree)
        name, type_token, origin = tokens[:3]
        self._add(
            str(name),
            _token_range(name),
            "definition",
            symbol=SymbolId("field", str(name), agent),
            context="context.slot",
            container=agent,
        )
        self._type_references(type_token, owner=agent)
        self._add(str(origin), _token_range(origin), "value", context="context.origin", container=agent)
        if len(tokens) > 3:
            source = tokens[3]
            if str(origin) == "datasource":
                self._reference(source, "datasource", context="context.source", container=agent)
            elif str(origin) == "external":
                self._reference(source, "external_context", context="context.source", container=agent)
        block = _child(tree, "context_block")
        if block is not None:
            for mapping in _children(block, "map_stmt"):
                key = _direct_tokens(mapping)[0]
                self._add(str(key), _token_range(key), "property", context="context.map", container=agent)

    def _assignment_block(self, block: Tree[Any], context: str, owner: str) -> None:
        for assignment in _children(block, "assignment"):
            self._assignment(assignment, context, owner)

    def _assignment(self, tree: Tree[Any], context: str, owner: str) -> None:
        key = _direct_tokens(tree)[0]
        key_name = str(key)
        self._add(key_name, _token_range(key), "property", context=f"{context}.{key_name}", container=owner)
        for _value_token, value_range, value in self._assignment_values(tree):
            value_context = f"{context}.{key_name}"
            reference_kind = self._assignment_reference_kind(context, key_name)
            if reference_kind is not None:
                self._add(
                    value,
                    value_range,
                    "reference",
                    symbol=SymbolId(reference_kind, value),
                    context=value_context,
                    container=owner,
                )
            else:
                self._add(value, value_range, "value", context=value_context, container=owner)
            if context == "run_spec" and key_name == "stages":
                self._run_stage_references(value_range, owner)
            elif key_name in {"require", "assertions"} or context in {"eval", "operational_control"}:
                self._index_expression(value, value_range, owner)

    def _assignment_values(self, tree: Tree[Any]) -> Iterable[tuple[Token, SourceRange, str]]:
        scalar = next((item for item in tree.iter_subtrees() if item.data == "scalar_value"), None)
        if scalar is not None:
            token = _direct_tokens(scalar)[0]
            value = str(token).strip()
            start = len(str(token)) - len(str(token).lstrip())
            end = len(str(token).rstrip())
            yield token, _token_slice_range(token, start, end), value
            return
        inline = next((item for item in tree.iter_subtrees() if item.data == "inline_list"), None)
        if inline is not None:
            tokens = _direct_tokens(inline)
            if not tokens:
                return
            token = tokens[0]
            for match in _LIST_ITEM_RE.finditer(str(token)):
                raw = match.group(0)
                leading = len(raw) - len(raw.lstrip())
                trailing = len(raw.rstrip())
                value = raw.strip().strip('"')
                yield token, _token_slice_range(token, match.start() + leading, match.start() + trailing), value
            return
        for item in tree.iter_subtrees():
            if item.data != "block_list_item":
                continue
            token = _direct_tokens(item)[0]
            raw = str(token)
            leading = len(raw) - len(raw.lstrip())
            trimmed = raw.rstrip()
            if trimmed.endswith(","):
                trimmed = trimmed[:-1].rstrip()
            trailing = len(trimmed)
            yield token, _token_slice_range(token, leading, trailing), raw[leading:trailing].strip('"')

    @staticmethod
    def _assignment_reference_kind(context: str, key: str) -> SymbolKind | None:
        spec = property_spec(context, key)
        return spec.reference_kind if spec is not None else None

    def _run_stage_references(self, item_range: SourceRange, run_spec: str) -> None:
        raw = _source_text(self.source, item_range)
        leading = len(raw) - len(raw.lstrip())
        for component in run_spec_stage_source_components(raw.strip()):
            kind: SymbolKind = "type" if component.role == "output" else cast(SymbolKind, component.role)
            role: OccurrenceRole = "definition" if component.role == "stage" else "reference"
            self._add(
                component.value,
                _range_inside(item_range, leading + component.start, leading + component.end),
                role,
                symbol=SymbolId(
                    kind,
                    component.value,
                    run_spec if component.role == "stage" else None,
                ),
                context=f"run_spec.{component.role}",
                container=run_spec,
            )

    def _index_expression_token(self, token: Token, *, owner: str) -> None:
        self._index_expression(str(token), _token_range(token), owner)

    def _index_expression(self, expression: str, source_range: SourceRange, owner: str) -> None:
        for reference in expression_source_references(expression):
            kind = None if reference.kind == "any" else cast(SymbolKind, reference.kind)
            self._add(
                reference.name,
                _range_inside(source_range, reference.start, reference.end),
                "reference",
                symbol=SymbolId(kind, reference.name) if kind is not None else None,
                context=f"expression.reference.{reference.kind}",
                container=owner,
            )

    def _type_references(self, token: Token, *, owner: str) -> None:
        for match in _TYPE_NAME_RE.finditer(str(token)):
            name = match.group(0)
            if name in _PRIMITIVE_TYPES:
                self._add(
                    name,
                    _token_slice_range(token, match.start(), match.end()),
                    "value",
                    context="type.primitive",
                    container=owner,
                )
                continue
            self._add(
                name,
                _token_slice_range(token, match.start(), match.end()),
                "reference",
                symbol=SymbolId("type", name),
                context="type.reference",
                container=owner,
            )

    def _definition(
        self,
        token: Token,
        kind: SymbolKind,
        *,
        symbol_owner: str | None = None,
        context: str | None = None,
        container: str | None = None,
    ) -> SourceRange:
        source_range = _token_range(token)
        self._add(
            str(token),
            source_range,
            "definition",
            symbol=SymbolId(kind, str(token), symbol_owner),
            context=context,
            container=container,
        )
        return source_range

    def _reference(
        self,
        token: Token,
        kind: SymbolKind,
        *,
        context: str,
        container: str | None = None,
    ) -> None:
        self._add(
            str(token),
            _token_range(token),
            "reference",
            symbol=SymbolId(kind, str(token)),
            context=context,
            container=container,
        )

    def _add(
        self,
        text: str,
        source_range: SourceRange,
        role: OccurrenceRole,
        *,
        symbol: SymbolId | None = None,
        context: str | None = None,
        container: str | None = None,
    ) -> None:
        self.occurrences.append(
            SourceOccurrence(text, source_range, role, symbol, context, container)
        )

    def _record_declaration(
        self,
        tree: Tree[Any],
        name: str,
        kind: SymbolKind,
        selection: SourceRange,
        children: tuple[SourceDeclaration, ...] = (),
        detail: str = "",
    ) -> None:
        self.declarations.append(SourceDeclaration(name, kind, _tree_range(tree), selection, detail, children))


def _child(tree: Tree[Any], data: str) -> Tree[Any] | None:
    return next((item for item in tree.children if isinstance(item, Tree) and item.data == data), None)


def _children(tree: Tree[Any], data: str) -> list[Tree[Any]]:
    return [item for item in tree.children if isinstance(item, Tree) and item.data == data]


def _child_trees(tree: Tree[Any]) -> list[Tree[Any]]:
    return [item for item in tree.children if isinstance(item, Tree)]


def _direct_tokens(tree: Tree[Any]) -> list[Token]:
    return [item for item in tree.children if isinstance(item, Token)]


def _token_range(token: Token) -> SourceRange:
    line = (token.line or 1) - 1
    column = (token.column or 1) - 1
    end_line = (token.end_line or token.line or 1) - 1
    end_column = (token.end_column or ((token.column or 1) + len(str(token)))) - 1
    return SourceRange(SourcePosition(line, column), SourcePosition(end_line, end_column))


def _token_slice_range(token: Token, start: int, end: int) -> SourceRange:
    token_range = _token_range(token)
    return _range_inside(token_range, start, end)


def _range_inside(source_range: SourceRange, start: int, end: int) -> SourceRange:
    if source_range.start.line != source_range.end.line:
        return source_range
    return SourceRange(
        SourcePosition(source_range.start.line, source_range.start.character + start),
        SourcePosition(source_range.start.line, source_range.start.character + end),
    )


def _tree_range(tree: Tree[Any]) -> SourceRange:
    meta = tree.meta
    return SourceRange(
        SourcePosition((meta.line or 1) - 1, (meta.column or 1) - 1),
        SourcePosition((meta.end_line or meta.line or 1) - 1, (meta.end_column or meta.column or 1) - 1),
    )


def _source_text(source: str, source_range: SourceRange) -> str:
    lines = source.splitlines(keepends=True)
    if source_range.start.line != source_range.end.line or source_range.start.line >= len(lines):
        return ""
    return lines[source_range.start.line][source_range.start.character : source_range.end.character]


__all__ = ["parse_document"]
