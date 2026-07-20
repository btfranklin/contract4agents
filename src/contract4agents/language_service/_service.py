"""Workspace-aware language intelligence for Contract4Agents source."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, cast

from contract4agents.diagnostics import Diagnostic
from contract4agents.language_service._help import (
    CONTEXT_PROPERTIES,
    TOP_LEVEL_KEYWORDS,
    property_help,
    property_values,
    value_help,
)
from contract4agents.language_service._model import (
    CompletionSuggestion,
    QuickFix,
    SemanticTokenKind,
    SourceDocument,
    SourceEdit,
    SourceInlayHint,
    SourceOccurrence,
    SourcePosition,
    SourceRange,
    SourceSemanticToken,
    SymbolId,
    SymbolKind,
)
from contract4agents.language_service._render import callable_signature, render_symbol
from contract4agents.language_service._workspace import LanguageWorkspace
from contract4agents.language_spec import PORTABLE_TYPE_COMPLETIONS

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DOTTED_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\Z")
_DECLARATION_RE = re.compile(
    r"^(?P<kind>type|enum|tool|datasource|external_context|isolation|composition|agent|control|quality|"
    r"operational_control|eval|run_spec)\b"
)


class LanguageService:
    """Stateful multi-root language intelligence independent of an editor protocol."""

    def __init__(self) -> None:
        self.workspaces: dict[Path, LanguageWorkspace] = {}

    def add_root(self, root: Path) -> LanguageWorkspace:
        resolved = root.resolve()
        nested_roots = {
            marker.parent.resolve()
            for marker in resolved.rglob("contract4agents.targets.toml")
            if marker.parent.resolve() != resolved and ".contract" not in marker.parts
        }
        roots = (resolved, *sorted(nested_roots))
        for candidate in roots:
            excluded = tuple(sorted(project for project in nested_roots if candidate in project.parents))
            workspace = self.workspaces.setdefault(candidate, LanguageWorkspace(candidate, excluded))
            workspace.excluded_roots = excluded
            workspace.load()
        return self.workspaces[resolved]

    def workspace_for(self, path: Path) -> LanguageWorkspace:
        resolved = path.resolve()
        candidates = [root for root in self.workspaces if resolved == root or root in resolved.parents]
        if candidates:
            return self.workspaces[max(candidates, key=lambda item: len(item.parts))]
        return self.add_root(resolved.parent)

    def update_document(self, path: Path, source: str) -> SourceDocument:
        return self.workspace_for(path).update(path, source)

    def close_document(self, path: Path) -> None:
        self.workspace_for(path).reload(path)

    def refresh_document(self, path: Path) -> None:
        self.workspace_for(path).reload(path)

    def document(self, path: Path) -> SourceDocument | None:
        return self.workspace_for(path).documents.get(path.resolve())

    def hover(self, path: Path, position: SourcePosition) -> str | None:
        workspace = self.workspace_for(path)
        document = workspace.documents.get(path.resolve())
        if document is None:
            return None
        occurrence = document.occurrence_at(position)
        if occurrence is None:
            return None
        if occurrence.role == "property" and occurrence.context and "." in occurrence.context:
            context, name = occurrence.context.split(".", 1)
            description = property_help(context, name)
            if description is not None:
                return f"**`{occurrence.text}`**\n\n{description}"
        if occurrence.role in {"value", "keyword"}:
            description = value_help(occurrence.context, occurrence.text.strip('"'))
            if description is not None:
                return f"**`{occurrence.text.strip(chr(34))}`**\n\n{description}"
            if occurrence.context == "type.primitive":
                return f"**`{occurrence.text}`**\n\nPortable Contract4Agents primitive type."
        key = self._resolve_key(workspace, occurrence)
        if key is None:
            return None
        return render_symbol(workspace.project(), key)

    def definition(self, path: Path, position: SourcePosition) -> list[tuple[Path, SourceRange]]:
        workspace = self.workspace_for(path)
        occurrence = self._occurrence(workspace, path, position)
        if occurrence is None:
            return []
        key = self._resolve_key(workspace, occurrence)
        if key is None:
            return []
        return [(target_path, target.range) for target_path, target in workspace.definitions().get(key, [])]

    def references(
        self,
        path: Path,
        position: SourcePosition,
        *,
        include_declaration: bool = True,
    ) -> list[tuple[Path, SourceRange]]:
        workspace = self.workspace_for(path)
        occurrence = self._occurrence(workspace, path, position)
        if occurrence is None:
            return []
        key = self._resolve_key(workspace, occurrence)
        if key is None:
            return []
        return [
            (item_path, item.range)
            for item_path, item in workspace.occurrences(key)
            if include_declaration or item.role != "definition"
        ]

    def rename(self, path: Path, position: SourcePosition, new_name: str) -> list[SourceEdit]:
        workspace = self.workspace_for(path)
        occurrence = self._occurrence(workspace, path, position)
        if occurrence is None:
            return []
        key = self._resolve_key(workspace, occurrence)
        if key is None or key.kind in {"field", "stage"}:
            return []
        pattern = _DOTTED_NAME_RE if key.kind in {"tool", "datasource", "external_context"} else _NAME_RE
        if pattern.fullmatch(new_name) is None:
            raise ValueError(f"`{new_name}` is not a valid {key.kind.replace('_', ' ')} name")
        return [SourceEdit(item_path, item.range, new_name) for item_path, item in workspace.occurrences(key)]

    def rename_target(self, path: Path, position: SourcePosition) -> SourceRange | None:
        workspace = self.workspace_for(path)
        occurrence = self._occurrence(workspace, path, position)
        if occurrence is None:
            return None
        key = self._resolve_key(workspace, occurrence)
        if key is None or key.kind in {"field", "stage"}:
            return None
        return occurrence.range

    def completions(self, path: Path, position: SourcePosition) -> list[CompletionSuggestion]:
        workspace = self.workspace_for(path)
        document = workspace.documents.get(path.resolve())
        if document is None:
            return []
        line = _line_at(document.source, position.line)
        prefix = line[: position.character]
        stripped = prefix.lstrip()
        suggestions: list[CompletionSuggestion] = []

        assignment = re.search(r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*[^=]*$", prefix)
        if assignment is not None:
            key = assignment.group("key")
            context = _source_context(document.source, position.line)
            if key == "isolation":
                suggestions.extend(self._symbol_completions(workspace, "isolation"))
            values = property_values(context, key)
            suggestions.extend(self._value_completions(values))
            return _dedupe_completions(suggestions)

        if re.search(r"\buse\s+[A-Za-z0-9_.]*$", prefix):
            return self._symbol_completions(workspace, "tool")
        if re.search(r"\bfrom\s+datasource\s+[A-Za-z0-9_.]*$", prefix):
            return self._symbol_completions(workspace, "datasource")
        if re.search(r"\bfrom\s+external\s+[A-Za-z0-9_.]*$", prefix):
            return self._symbol_completions(workspace, "external_context")
        if re.search(r"\bfrom\s+[A-Za-z_]*$", prefix):
            return self._value_completions(("datasource", "external"))
        if _looks_like_type_position(prefix):
            suggestions.extend(
                CompletionSuggestion(
                    item,
                    "portable type",
                    value_help(None, item) or "Portable Contract4Agents type.",
                    "type",
                )
                for item in PORTABLE_TYPE_COMPLETIONS
            )
            suggestions.extend(self._symbol_completions(workspace, "type"))
            return _dedupe_completions(suggestions)

        context = _source_context(document.source, position.line)
        if not line[: len(line) - len(line.lstrip())] and not stripped:
            return [
                CompletionSuggestion(
                    item,
                    "declaration",
                    f"Declare a Contract4Agents {item.replace('_', ' ')}.",
                    "keyword",
                )
                for item in TOP_LEVEL_KEYWORDS
            ]
        for key in CONTEXT_PROPERTIES.get(context, ()):
            suggestions.append(
                CompletionSuggestion(
                    key,
                    f"{context.replace('_', ' ')} property",
                    property_help(context, key) or f"Contract4Agents `{key}` property.",
                    "property",
                    f"{key} = ",
                )
            )
        return _dedupe_completions(suggestions)

    def inlay_hints(self, path: Path, source_range: SourceRange) -> list[SourceInlayHint]:
        workspace = self.workspace_for(path)
        document = workspace.documents.get(path.resolve())
        if document is None:
            return []
        project = workspace.project()
        hints: list[SourceInlayHint] = []
        for occurrence in document.occurrences:
            if occurrence.context != "grant.capability" or not _ranges_overlap(occurrence.range, source_range):
                continue
            tool = project.tools.get(occurrence.text)
            if tool is None:
                continue
            signature = callable_signature(tool)
            hints.append(
                SourceInlayHint(
                    occurrence.range.end,
                    f"  {signature}",
                    tool.description or "Declared tool interface.",
                )
            )
        return hints

    def quick_fixes(self, path: Path, diagnostic: Diagnostic) -> list[QuickFix]:
        if diagnostic.span is None or diagnostic.code not in {"SEM105", "SEM107", "SEM108"}:
            return []
        document = self.document(path)
        if document is None:
            return []
        line_index = diagnostic.span.line - 1
        lines = document.source.splitlines(keepends=True)
        if line_index < 0 or line_index >= len(lines) or not lines[line_index].lstrip().startswith("use "):
            return []
        indent = len(lines[line_index]) - len(lines[line_index].lstrip()) + 4

        if diagnostic.code == "SEM105":
            values: tuple[tuple[str, bool], ...] = (("enabled", False), ("denied", False))
            key = "availability"
        elif diagnostic.code == "SEM107":
            values = (("approval_required", True), ("preapproved", False))
            key = "authorization"
        else:
            values = (("host", False), ("provider_hosted", False), ("remote", False))
            key = "execution"
        insertion = _grant_property_insertion(lines, line_index, key)
        insertion_range = SourceRange(insertion, insertion)
        return [
            QuickFix(
                f"Set {key} to {value}",
                (SourceEdit(path.resolve(), insertion_range, f"{' ' * indent}{key} = {value}\n"),),
                preferred,
            )
            for value, preferred in values
        ]

    def semantic_tokens(self, path: Path) -> tuple[SourceSemanticToken, ...]:
        document = self.document(path)
        if document is None:
            return ()
        tokens: list[SourceSemanticToken] = []
        for occurrence in document.occurrences:
            kind = _semantic_kind(occurrence)
            if kind is not None:
                tokens.append(SourceSemanticToken(occurrence.range, kind, occurrence.role == "definition"))
        return tuple(tokens)

    def _occurrence(
        self,
        workspace: LanguageWorkspace,
        path: Path,
        position: SourcePosition,
    ) -> SourceOccurrence | None:
        document = workspace.documents.get(path.resolve())
        return document.occurrence_at(position) if document is not None else None

    def _resolve_key(
        self,
        workspace: LanguageWorkspace,
        occurrence: SourceOccurrence,
    ) -> SymbolId | None:
        if occurrence.symbol is not None:
            return occurrence.symbol
        if not occurrence.text:
            return None
        definitions = workspace.definitions()
        matching = [symbol for symbol in definitions if symbol.name == occurrence.text]
        priority: tuple[SymbolKind, ...] = ("stage", "agent", "tool", "datasource", "type")
        if not matching:
            return None
        return min(
            matching,
            key=lambda symbol: priority.index(symbol.kind) if symbol.kind in priority else 99,
        )

    def _symbol_completions(
        self,
        workspace: LanguageWorkspace,
        kind: SymbolKind,
    ) -> list[CompletionSuggestion]:
        project = workspace.project()
        if kind == "type":
            names = sorted(set(project.types) | set(project.enums))
            return [
                CompletionSuggestion(
                    name,
                    "declared type",
                    render_symbol(project, SymbolId("type", name)) or "",
                    "type",
                )
                for name in names
            ]
        mapping: dict[str, object]
        if kind == "tool":
            mapping = cast(dict[str, object], project.tools)
        elif kind == "datasource":
            mapping = cast(dict[str, object], project.datasources)
        elif kind == "external_context":
            mapping = cast(dict[str, object], project.external_contexts)
        elif kind == "isolation":
            mapping = cast(dict[str, object], project.isolations)
        else:
            mapping = {}
        completion_kind: Literal["function", "class"] = (
            "function" if kind in {"tool", "datasource"} else "class"
        )
        return [
            CompletionSuggestion(
                name,
                f"declared {kind.replace('_', ' ')}",
                render_symbol(project, SymbolId(kind, name)) or "",
                completion_kind,
            )
            for name in sorted(mapping)
        ]

    @staticmethod
    def _value_completions(values: tuple[str, ...]) -> list[CompletionSuggestion]:
        return [
            CompletionSuggestion(value, "Contract4Agents value", value_help(None, value) or "Accepted value.", "value")
            for value in values
        ]

def _line_at(source: str, line: int) -> str:
    lines = source.splitlines()
    return lines[line] if 0 <= line < len(lines) else ""


def _source_context(source: str, line: int) -> str:
    lines = source.splitlines()
    current_indent = len(lines[line]) - len(lines[line].lstrip()) if 0 <= line < len(lines) else 0
    for candidate in range(min(line, len(lines) - 1), -1, -1):
        text = lines[candidate]
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(text) - len(text.lstrip())
        if stripped.startswith("use ") and current_indent > indent:
            return "grant"
        if indent == 0:
            match = _DECLARATION_RE.match(stripped)
            return match.group("kind") if match is not None else ""
    return ""


def _looks_like_type_position(prefix: str) -> bool:
    return bool(
        re.search(r"(?:\(|,)\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z0-9_.,?\[\]]*$", prefix)
        or re.search(r"->\s*[A-Za-z0-9_.,?\[\]]*$", prefix)
        or re.search(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z0-9_.,?\[\]]*$", prefix)
    )


def _dedupe_completions(items: list[CompletionSuggestion]) -> list[CompletionSuggestion]:
    result: dict[str, CompletionSuggestion] = {}
    for item in items:
        result.setdefault(item.label, item)
    return list(result.values())


def _ranges_overlap(left: SourceRange, right: SourceRange) -> bool:
    return left.start <= right.end and right.start <= left.end


def _grant_property_insertion(lines: list[str], use_line: int, new_property: str) -> SourcePosition:
    order = {name: index for index, name in enumerate(("availability", "authorization", "execution", "isolation"))}
    use_indent = len(lines[use_line]) - len(lines[use_line].lstrip())
    properties: list[tuple[int, str]] = []
    for line_index in range(use_line + 1, len(lines)):
        line = lines[line_index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= use_indent:
            break
        match = re.match(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=", stripped)
        if match is not None:
            properties.append((line_index, match.group("name")))
    target_order = order[new_property]
    later = next((line for line, name in properties if order.get(name, len(order)) > target_order), None)
    if later is not None:
        return SourcePosition(later, 0)
    if properties:
        return SourcePosition(properties[-1][0] + 1, 0)
    return SourcePosition(use_line + 1, 0)


def _semantic_kind(occurrence: SourceOccurrence) -> SemanticTokenKind | None:
    if occurrence.role == "property":
        return "property"
    if occurrence.role == "value":
        if occurrence.context == "type.primitive":
            return "type"
        if occurrence.context in {"context.origin", "enum.member"}:
            return "enumMember"
        if occurrence.context and "." in occurrence.context:
            context, name = occurrence.context.split(".", 1)
            if property_values(context, name):
                return "enumMember"
        return None
    if occurrence.symbol is None:
        return None
    if occurrence.symbol.kind == "type":
        return "type"
    if occurrence.symbol.kind == "agent":
        return "class"
    if occurrence.symbol.kind in {"tool", "datasource"}:
        return "function"
    if occurrence.symbol.kind == "field":
        return "parameter" if occurrence.context == "parameter" else "property"
    return "interface"


__all__ = ["LanguageService", "LanguageWorkspace"]
