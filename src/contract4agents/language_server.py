"""Language Server Protocol adapter for Contract4Agents language intelligence."""

from __future__ import annotations

from collections import defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from lsprotocol import types
from pygls.exceptions import JsonRpcInvalidParams
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path, to_fs_path
from pygls.workspace import TextDocument
from pygls.workspace.position_codec import ServerTextPosition, ServerTextRange

from contract4agents.ast import SourceSpan
from contract4agents.diagnostics import Diagnostic
from contract4agents.language_service._model import (
    CompletionSuggestion,
    SourceDeclaration,
    SourceOccurrence,
    SourcePosition,
    SourceRange,
    SourceSemanticToken,
    SymbolKind,
)
from contract4agents.language_service._service import LanguageService

TOKEN_TYPES = ("type", "class", "function", "interface", "property", "parameter", "enumMember")
TOKEN_MODIFIERS = ("declaration", "readonly")

_SYMBOL_KINDS = {
    "type": types.SymbolKind.Struct,
    "enum": types.SymbolKind.Enum,
    "agent": types.SymbolKind.Class,
    "tool": types.SymbolKind.Function,
    "datasource": types.SymbolKind.Function,
    "external_context": types.SymbolKind.Interface,
    "isolation": types.SymbolKind.Interface,
    "composition": types.SymbolKind.Event,
    "control": types.SymbolKind.Interface,
    "quality": types.SymbolKind.Interface,
    "operational_control": types.SymbolKind.Interface,
    "eval": types.SymbolKind.Method,
    "run_spec": types.SymbolKind.Namespace,
    "stage": types.SymbolKind.Event,
    "field": types.SymbolKind.Field,
}
_COMPLETION_KINDS = {
    "keyword": types.CompletionItemKind.Keyword,
    "property": types.CompletionItemKind.Property,
    "value": types.CompletionItemKind.EnumMember,
    "type": types.CompletionItemKind.Struct,
    "function": types.CompletionItemKind.Function,
    "class": types.CompletionItemKind.Class,
}


class ContractLanguageServer(LanguageServer):
    """Pygls server with a transport-independent Contract4Agents service."""

    def __init__(self) -> None:
        try:
            server_version = version("contract4agents")
        except PackageNotFoundError:
            server_version = "development"
        super().__init__("contract4agents", server_version)
        self.contracts = LanguageService()


def create_server() -> ContractLanguageServer:
    server = ContractLanguageServer()
    _register_lifecycle(server)
    _register_navigation(server)
    _register_completions(server)
    _register_symbols(server)
    _register_semantic_tokens(server)
    _register_code_actions(server)
    _register_inlay_hints(server)
    return server


def _register_lifecycle(server: ContractLanguageServer) -> None:
    @server.feature(types.INITIALIZED)
    def initialized(_params: types.InitializedParams) -> None:
        folders = list(server.workspace.folders.values())
        if folders:
            for folder in folders:
                server.contracts.add_root(_path(folder.uri))
        else:
            root_path = server.workspace.root_path
            if root_path is not None:
                server.contracts.add_root(Path(root_path))
        _publish_all_diagnostics(server)

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(params: types.DidOpenTextDocumentParams) -> None:
        path = _path(params.text_document.uri)
        server.contracts.update_document(path, params.text_document.text)
        _publish_workspace_diagnostics(server, path)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(params: types.DidChangeTextDocumentParams) -> None:
        path = _path(params.text_document.uri)
        document = server.workspace.get_text_document(params.text_document.uri)
        server.contracts.update_document(path, document.source)
        _publish_workspace_diagnostics(server, path)

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def did_save(params: types.DidSaveTextDocumentParams) -> None:
        path = _path(params.text_document.uri)
        document = server.workspace.get_text_document(params.text_document.uri)
        server.contracts.update_document(path, document.source)
        _publish_workspace_diagnostics(server, path)

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    def did_close(params: types.DidCloseTextDocumentParams) -> None:
        path = _path(params.text_document.uri)
        server.contracts.close_document(path)
        _publish_workspace_diagnostics(server, path)

    @server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)
    def did_change_watched_files(params: types.DidChangeWatchedFilesParams) -> None:
        changed_paths: list[Path] = []
        for change in params.changes:
            path = _path(change.uri)
            server.contracts.refresh_document(path)
            changed_paths.append(path)
        for path in changed_paths:
            _publish_workspace_diagnostics(server, path)

    @server.feature(types.WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS)
    def did_change_workspace_folders(params: types.DidChangeWorkspaceFoldersParams) -> None:
        for folder in params.event.removed:
            removed = _path(folder.uri)
            for root in list(server.contracts.workspaces):
                if root == removed or removed in root.parents:
                    server.contracts.workspaces.pop(root)
        for folder in params.event.added:
            server.contracts.add_root(_path(folder.uri))
        _publish_all_diagnostics(server)


def _register_navigation(server: ContractLanguageServer) -> None:
    @server.feature(types.TEXT_DOCUMENT_HOVER)
    def hover(params: types.HoverParams) -> types.Hover | None:
        path = _path(params.text_document.uri)
        position = _source_position(server, params.text_document.uri, params.position)
        contents = server.contracts.hover(path, position)
        if contents is None:
            return None
        occurrence = _occurrence(server, path, position)
        source_range = _lsp_range(server, path, occurrence.range) if occurrence is not None else None
        return types.Hover(types.MarkupContent(types.MarkupKind.Markdown, contents), range=source_range)

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
    def definition(params: types.DefinitionParams) -> list[types.Location]:
        path = _path(params.text_document.uri)
        position = _source_position(server, params.text_document.uri, params.position)
        return [
            types.Location(from_fs_path(str(target)), _lsp_range(server, target, source_range))
            for target, source_range in server.contracts.definition(path, position)
        ]

    @server.feature(types.TEXT_DOCUMENT_REFERENCES)
    def references(params: types.ReferenceParams) -> list[types.Location]:
        path = _path(params.text_document.uri)
        position = _source_position(server, params.text_document.uri, params.position)
        return [
            types.Location(from_fs_path(str(target)), _lsp_range(server, target, source_range))
            for target, source_range in server.contracts.references(
                path,
                position,
                include_declaration=params.context.include_declaration,
            )
        ]

    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)
    def highlights(params: types.DocumentHighlightParams) -> list[types.DocumentHighlight]:
        path = _path(params.text_document.uri)
        position = _source_position(server, params.text_document.uri, params.position)
        return [
            types.DocumentHighlight(_lsp_range(server, target, source_range), types.DocumentHighlightKind.Read)
            for target, source_range in server.contracts.references(path, position)
            if target == path
        ]

    @server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)
    def prepare_rename(params: types.PrepareRenameParams) -> types.Range | None:
        path = _path(params.text_document.uri)
        position = _source_position(server, params.text_document.uri, params.position)
        source_range = server.contracts.rename_target(path, position)
        return _lsp_range(server, path, source_range) if source_range is not None else None

    @server.feature(types.TEXT_DOCUMENT_RENAME, types.RenameOptions(prepare_provider=True))
    def rename(params: types.RenameParams) -> types.WorkspaceEdit:
        path = _path(params.text_document.uri)
        position = _source_position(server, params.text_document.uri, params.position)
        try:
            edits = server.contracts.rename(path, position, params.new_name)
        except ValueError as exc:
            raise JsonRpcInvalidParams(str(exc)) from exc
        changes: dict[str, list[types.TextEdit]] = defaultdict(list)
        for edit in edits:
            changes[from_fs_path(str(edit.path))].append(
                types.TextEdit(_lsp_range(server, edit.path, edit.range), edit.new_text)
            )
        return types.WorkspaceEdit(changes=dict(changes))


def _register_completions(server: ContractLanguageServer) -> None:
    options = types.CompletionOptions(trigger_characters=["=", ":", " ", ".", "[", ","])

    @server.feature(types.TEXT_DOCUMENT_COMPLETION, options)
    def completion(params: types.CompletionParams) -> types.CompletionList:
        path = _path(params.text_document.uri)
        position = _source_position(server, params.text_document.uri, params.position)
        items = [_completion_item(item) for item in server.contracts.completions(path, position)]
        return types.CompletionList(is_incomplete=False, items=items)


def _register_symbols(server: ContractLanguageServer) -> None:
    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
    def document_symbols(params: types.DocumentSymbolParams) -> list[types.DocumentSymbol]:
        path = _path(params.text_document.uri)
        document = server.contracts.document(path)
        if document is None:
            return []
        return [_document_symbol(server, path, item) for item in document.declarations]

    @server.feature(types.WORKSPACE_SYMBOL)
    def workspace_symbols(params: types.WorkspaceSymbolParams) -> list[types.SymbolInformation]:
        query = params.query.casefold()
        symbols: list[types.SymbolInformation] = []
        for workspace in server.contracts.workspaces.values():
            for path, document in workspace.documents.items():
                for declaration in document.declarations:
                    if query and query not in declaration.name.casefold():
                        continue
                    symbols.append(
                        types.SymbolInformation(
                            location=types.Location(
                                from_fs_path(str(path)),
                                _lsp_range(server, path, declaration.selection_range),
                            ),
                            name=declaration.name,
                            kind=_symbol_kind(declaration.kind),
                        )
                    )
        return symbols


def _register_semantic_tokens(server: ContractLanguageServer) -> None:
    legend = types.SemanticTokensLegend(token_types=list(TOKEN_TYPES), token_modifiers=list(TOKEN_MODIFIERS))

    @server.feature(types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL, legend)
    def semantic_tokens(params: types.SemanticTokensParams) -> types.SemanticTokens:
        path = _path(params.text_document.uri)
        semantic_source_tokens = server.contracts.semantic_tokens(path)
        return types.SemanticTokens(data=_encode_semantic_tokens(server, path, semantic_source_tokens))


def _register_code_actions(server: ContractLanguageServer) -> None:
    options = types.CodeActionOptions(code_action_kinds=[types.CodeActionKind.QuickFix])

    @server.feature(types.TEXT_DOCUMENT_CODE_ACTION, options)
    def code_actions(params: types.CodeActionParams) -> list[types.CodeAction]:
        path = _path(params.text_document.uri)
        actions: list[types.CodeAction] = []
        for lsp_diagnostic in params.context.diagnostics:
            if not isinstance(lsp_diagnostic.code, str):
                continue
            start = _source_position(server, params.text_document.uri, lsp_diagnostic.range.start)
            diagnostic = Diagnostic(
                lsp_diagnostic.code,
                lsp_diagnostic.message,
                span=SourceSpan(path, start.line + 1, start.character + 1),
            )
            for fix in server.contracts.quick_fixes(path, diagnostic):
                changes: dict[str, list[types.TextEdit]] = defaultdict(list)
                for edit in fix.edits:
                    changes[from_fs_path(str(edit.path))].append(
                        types.TextEdit(_lsp_range(server, edit.path, edit.range), edit.new_text)
                    )
                actions.append(
                    types.CodeAction(
                        fix.title,
                        kind=types.CodeActionKind.QuickFix,
                        diagnostics=[lsp_diagnostic],
                        is_preferred=fix.preferred,
                        edit=types.WorkspaceEdit(changes=dict(changes)),
                    )
                )
        return actions


def _register_inlay_hints(server: ContractLanguageServer) -> None:
    @server.feature(types.TEXT_DOCUMENT_INLAY_HINT, types.InlayHintOptions(resolve_provider=False))
    def inlay_hints(params: types.InlayHintParams) -> list[types.InlayHint]:
        path = _path(params.text_document.uri)
        source_range = _source_range(server, params.text_document.uri, params.range)
        return [
            types.InlayHint(
                _lsp_position(server, path, item.position),
                item.label,
                kind=types.InlayHintKind.Type,
                tooltip=types.MarkupContent(types.MarkupKind.Markdown, item.tooltip),
                padding_left=True,
            )
            for item in server.contracts.inlay_hints(path, source_range)
        ]


def _publish_all_diagnostics(server: ContractLanguageServer) -> None:
    for workspace in server.contracts.workspaces.values():
        _publish_diagnostics(server, workspace.diagnostics())


def _publish_workspace_diagnostics(server: ContractLanguageServer, path: Path) -> None:
    _publish_diagnostics(server, server.contracts.workspace_for(path).diagnostics())


def _publish_diagnostics(server: ContractLanguageServer, grouped: dict[Path, list[Diagnostic]]) -> None:
    for path, diagnostics in grouped.items():
        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(
                from_fs_path(str(path)),
                [_lsp_diagnostic(server, path, item) for item in diagnostics],
            )
        )


def _lsp_diagnostic(server: ContractLanguageServer, path: Path, diagnostic: Diagnostic) -> types.Diagnostic:
    source_range = _diagnostic_range(server, path, diagnostic)
    message = diagnostic.message
    if diagnostic.hint:
        message += f"\n\nHint: {diagnostic.hint}"
    severity = types.DiagnosticSeverity.Error if diagnostic.severity == "error" else types.DiagnosticSeverity.Warning
    return types.Diagnostic(
        source_range,
        message,
        severity=severity,
        code=diagnostic.code,
        source="contract4agents",
    )


def _diagnostic_range(server: ContractLanguageServer, path: Path, diagnostic: Diagnostic) -> types.Range:
    if diagnostic.span is None:
        return _lsp_range(
            server,
            path,
            SourceRange(SourcePosition(0, 0), SourcePosition(0, 1)),
        )
    start = SourcePosition(max(diagnostic.span.line - 1, 0), max(diagnostic.span.column - 1, 0))
    document = server.contracts.document(path)
    if document is not None:
        candidates = [item for item in document.occurrences if item.range.start == start]
        if candidates:
            return _lsp_range(server, path, min(candidates, key=lambda item: item.range.specificity).range)
    return _lsp_range(server, path, SourceRange(start, SourcePosition(start.line, start.character + 1)))


def _completion_item(item: CompletionSuggestion) -> types.CompletionItem:
    return types.CompletionItem(
        item.label,
        kind=_COMPLETION_KINDS[item.kind],
        detail=item.detail,
        documentation=types.MarkupContent(types.MarkupKind.Markdown, item.documentation),
        insert_text=item.insert_text,
    )


def _document_symbol(
    server: ContractLanguageServer,
    path: Path,
    declaration: SourceDeclaration,
) -> types.DocumentSymbol:
    return types.DocumentSymbol(
        declaration.name,
        _symbol_kind(declaration.kind),
        _lsp_range(server, path, declaration.range),
        _lsp_range(server, path, declaration.selection_range),
        detail=declaration.detail or None,
        children=[_document_symbol(server, path, item) for item in declaration.children] or None,
    )


def _symbol_kind(kind: SymbolKind) -> types.SymbolKind:
    return _SYMBOL_KINDS[kind]


def _encode_semantic_tokens(
    server: ContractLanguageServer,
    path: Path,
    semantic_source_tokens: tuple[SourceSemanticToken, ...],
) -> list[int]:
    encoded: list[int] = []
    previous_line = 0
    previous_character = 0
    previous_end = SourcePosition(0, 0)
    ordered = sorted(
        semantic_source_tokens,
        key=lambda item: (item.range.start, item.range.specificity, item.kind),
    )
    for semantic_source_token in ordered:
        if semantic_source_token.range.start.line != semantic_source_token.range.end.line:
            continue
        if semantic_source_token.range.start < previous_end:
            continue
        lsp_range = _lsp_range(server, path, semantic_source_token.range)
        start = lsp_range.start
        length = lsp_range.end.character - start.character
        if length <= 0:
            continue
        delta_line = start.line - previous_line
        delta_start = start.character - previous_character if delta_line == 0 else start.character
        modifiers = 1 if semantic_source_token.declaration else 0
        encoded.extend([delta_line, delta_start, length, TOKEN_TYPES.index(semantic_source_token.kind), modifiers])
        previous_line = start.line
        previous_character = start.character
        previous_end = semantic_source_token.range.end
    return encoded


def _occurrence(
    server: ContractLanguageServer,
    path: Path,
    position: SourcePosition,
) -> SourceOccurrence | None:
    document = server.contracts.document(path)
    return document.occurrence_at(position) if document is not None else None


def _path(uri: str) -> Path:
    path = to_fs_path(uri)
    if path is None:
        raise JsonRpcInvalidParams(f"Contract4Agents requires a local file URI, received `{uri}`")
    return Path(path).resolve()


def _text_document(server: ContractLanguageServer, path: Path) -> TextDocument:
    uri = from_fs_path(str(path))
    try:
        return server.workspace.get_text_document(uri)
    except KeyError:
        document = server.contracts.document(path)
        return TextDocument(uri, source=document.source if document is not None else "")


def _source_position(server: ContractLanguageServer, uri: str, position: types.Position) -> SourcePosition:
    document = server.workspace.get_text_document(uri)
    converted = document.position_from_client_units(position)
    return SourcePosition(converted.line, converted.character)


def _source_range(server: ContractLanguageServer, uri: str, source_range: types.Range) -> SourceRange:
    document = server.workspace.get_text_document(uri)
    converted = document.range_from_client_units(source_range)
    return SourceRange(
        SourcePosition(converted.start.line, converted.start.character),
        SourcePosition(converted.end.line, converted.end.character),
    )


def _lsp_position(server: ContractLanguageServer, path: Path, position: SourcePosition) -> types.Position:
    document = _text_document(server, path)
    return document.position_to_client_units(ServerTextPosition(position.line, position.character))


def _lsp_range(server: ContractLanguageServer, path: Path, source_range: SourceRange) -> types.Range:
    document = _text_document(server, path)
    return document.range_to_client_units(
        ServerTextRange(
            ServerTextPosition(source_range.start.line, source_range.start.character),
            ServerTextPosition(source_range.end.line, source_range.end.character),
        )
    )


def main() -> None:
    create_server().start_io()


if __name__ == "__main__":
    main()


__all__ = ["ContractLanguageServer", "create_server", "main"]
