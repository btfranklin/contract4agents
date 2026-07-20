"""Workspace document overlays and semantic project state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from contract4agents.ast import ContractModule, ContractProject
from contract4agents.diagnostics import Diagnostic
from contract4agents.language_service._model import SourceDocument, SourceOccurrence, SymbolId
from contract4agents.language_service._source import parse_document
from contract4agents.semantics import analyze_project


@dataclass
class LanguageWorkspace:
    root: Path
    excluded_roots: tuple[Path, ...] = ()
    documents: dict[Path, SourceDocument] = field(default_factory=dict)
    last_valid_modules: dict[Path, ContractModule] = field(default_factory=dict)

    def load(self) -> None:
        current_paths = set(self._source_paths())
        for path in current_paths:
            if path not in self.documents:
                self.update(path, path.read_text())
        for path in set(self.documents) - current_paths:
            self.documents.pop(path, None)
            self.last_valid_modules.pop(path, None)

    def update(self, path: Path, source: str) -> SourceDocument:
        resolved = path.resolve()
        document = parse_document(resolved, source)
        previous = self.documents.get(resolved)
        if document.module is None and previous is not None:
            document = replace(
                document,
                occurrences=previous.occurrences,
                declarations=previous.declarations,
            )
        self.documents[resolved] = document
        if document.module is not None:
            self.last_valid_modules[resolved] = document.module
        return document

    def reload(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.is_file() and resolved.suffix in {".contract", ".eval"}:
            self.update(resolved, resolved.read_text())
        else:
            self.documents.pop(resolved, None)
            self.last_valid_modules.pop(resolved, None)

    def project(self) -> ContractProject:
        modules = [self.last_valid_modules[path] for path in sorted(self.last_valid_modules)]
        return ContractProject(self.root, modules)

    def diagnostics(self) -> dict[Path, list[Diagnostic]]:
        grouped = {path: list(document.diagnostics) for path, document in self.documents.items()}
        for diagnostic in analyze_project(self.project()).diagnostics:
            if diagnostic.span is None:
                continue
            path = diagnostic.span.path.resolve()
            document = self.documents.get(path)
            if document is not None and document.module is not None:
                grouped.setdefault(path, []).append(diagnostic)
        return grouped

    def definitions(self) -> dict[SymbolId, list[tuple[Path, SourceOccurrence]]]:
        result: dict[SymbolId, list[tuple[Path, SourceOccurrence]]] = {}
        for path, document in self.documents.items():
            for occurrence in document.occurrences:
                if occurrence.role != "definition" or occurrence.symbol is None:
                    continue
                result.setdefault(occurrence.symbol, []).append((path, occurrence))
        return result

    def occurrences(self, symbol: SymbolId) -> list[tuple[Path, SourceOccurrence]]:
        matches: list[tuple[Path, SourceOccurrence]] = []
        for path, document in self.documents.items():
            matches.extend((path, item) for item in document.occurrences if item.symbol == symbol)
        return matches

    def _source_paths(self) -> list[Path]:
        return sorted(
            path.resolve()
            for pattern in ("*.contract", "*.eval")
            for path in self.root.rglob(pattern)
            if path.is_file()
            and ".contract" not in path.parts
            and not any(excluded == path or excluded in path.parents for excluded in self.excluded_roots)
        )


__all__ = ["LanguageWorkspace"]
