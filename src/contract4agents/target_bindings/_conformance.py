"""Conformance checks between target bindings and canonical V2 semantics."""

from __future__ import annotations

import importlib
import inspect
import json
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from contract4agents.diagnostics import Diagnostic
from contract4agents.ir import CanonicalIR, CapabilityIR, ParameterIR, SemanticId
from contract4agents.target_bindings._models import BindingEntry, TargetBinding, TargetBindings

BindingSection = Literal["tools", "datasources", "external_context"]
ParameterKind = Literal["positional_only", "positional_or_keyword", "keyword_only"]

_LOCATOR = re.compile(
    r"(?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):"
    r"(?P<attribute>[A-Za-z_][A-Za-z0-9_]*)"
)


@dataclass(frozen=True)
class ResolvedParameterIdentity:
    """Portable callable-parameter facts retained after inspection."""

    name: str
    required: bool
    kind: ParameterKind

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "name": self.name, "required": self.required}


@dataclass(frozen=True)
class ResolvedImplementationIdentity:
    """Serializable identity for one successfully imported implementation."""

    section: BindingSection
    semantic_id: str
    locator: str
    module: str
    attribute: str
    signature_inspected: bool
    parameters: tuple[ResolvedParameterIdentity, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "attribute": self.attribute,
            "locator": self.locator,
            "module": self.module,
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "section": self.section,
            "semantic_id": self.semantic_id,
            "signature_inspected": self.signature_inspected,
        }


@dataclass(frozen=True)
class TargetBindingConformanceResult:
    """Structured binding diagnostics and non-executable implementation identities."""

    target: str
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)
    implementations: tuple[ResolvedImplementationIdentity, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostics",
            tuple(sorted(self.diagnostics, key=lambda item: (item.code, item.message, item.severity, item.hint or ""))),
        )
        object.__setattr__(
            self,
            "implementations",
            tuple(sorted(self.implementations, key=lambda item: (item.section, item.semantic_id, item.locator))),
        )

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)

    def to_dict(self) -> dict[str, object]:
        return {
            "diagnostics": [_diagnostic_dict(item) for item in self.diagnostics],
            "implementations": [item.to_dict() for item in self.implementations],
            "target": self.target,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def validate_target_binding_conformance(
    ir: CanonicalIR,
    bindings: TargetBindings,
    target_name: str,
    *,
    project_root: Path | None = None,
) -> TargetBindingConformanceResult:
    """Validate one target without retaining or invoking imported application callables."""

    target = bindings.targets.get(target_name)
    if target is None:
        return TargetBindingConformanceResult(
            target_name,
            diagnostics=(
                Diagnostic(
                    "TGT101",
                    f"Target bindings do not declare target `{target_name}`",
                    hint=f"Add `[targets.{target_name}]` before planning this target.",
                ),
            ),
        )

    expected = _expected_bindings(ir)
    diagnostics: list[Diagnostic] = []
    implementations: list[ResolvedImplementationIdentity] = []
    root = (project_root or bindings.path.parent).resolve()
    for section, expected_entries in expected.items():
        configured = _binding_section(target, section)
        diagnostics.extend(_coverage_diagnostics(target_name, section, expected_entries, configured))
        for name in sorted(set(expected_entries) & set(configured)):
            entry = configured[name]
            if "python" not in entry.values:
                continue
            resolved, entry_diagnostics = _resolve_python_binding(
                root,
                target_name,
                section,
                expected_entries[name],
                entry,
            )
            diagnostics.extend(entry_diagnostics)
            if resolved is not None:
                implementations.append(resolved)
    return TargetBindingConformanceResult(target_name, tuple(diagnostics), tuple(implementations))


def _expected_bindings(ir: CanonicalIR) -> dict[BindingSection, dict[str, CapabilityIR | SemanticId]]:
    enabled_tool_ids = {
        grant.capability_id
        for grant in ir.grants.values()
        if grant.availability == "enabled" and grant.capability_id.kind == "tool"
    }
    tools: dict[str, CapabilityIR | SemanticId] = {
        capability.name: capability
        for identifier, capability in ir.capabilities.items()
        if identifier in enabled_tool_ids and capability.kind == "tool"
    }
    datasources: dict[str, CapabilityIR | SemanticId] = {
        capability.name: capability
        for capability in ir.capabilities.values()
        if capability.kind == "datasource"
    }
    referenced_external_ids = {
        context.origin_id
        for context in ir.contexts.values()
        if context.origin == "external" and context.origin_id is not None
    }
    external_context: dict[str, CapabilityIR | SemanticId] = {
        external.name: external.id
        for identifier, external in ir.external_contexts.items()
        if identifier in referenced_external_ids
    }
    return {"tools": tools, "datasources": datasources, "external_context": external_context}


def _binding_section(target: TargetBinding, section: BindingSection) -> Mapping[str, BindingEntry]:
    if section == "tools":
        return target.tools
    if section == "datasources":
        return target.datasources
    return target.external_context


def _coverage_diagnostics(
    target_name: str,
    section: BindingSection,
    expected: Mapping[str, object],
    configured: Mapping[str, BindingEntry],
) -> list[Diagnostic]:
    diagnostics = [
        Diagnostic(
            "TGT102",
            f"Target `{target_name}` is missing `{section}` binding `{name}` required by canonical IR",
        )
        for name in sorted(set(expected) - set(configured))
    ]
    diagnostics.extend(
        Diagnostic(
            "TGT103",
            f"Target `{target_name}` has stale `{section}` binding `{name}` not required by canonical IR",
            hint="Delete the stale target-specific entry.",
        )
        for name in sorted(set(configured) - set(expected))
    )
    return diagnostics


def _resolve_python_binding(
    project_root: Path,
    target_name: str,
    section: BindingSection,
    semantic: CapabilityIR | SemanticId,
    entry: BindingEntry,
) -> tuple[ResolvedImplementationIdentity | None, list[Diagnostic]]:
    raw_locator = entry.values["python"]
    binding_name = semantic.name if isinstance(semantic, CapabilityIR) else semantic.parts[0]
    entry_name = f"targets.{target_name}.{section}.{binding_name}.python"
    if not isinstance(raw_locator, str) or _LOCATOR.fullmatch(raw_locator) is None:
        return None, [
            Diagnostic(
                "TGT104",
                f"Python locator for `{entry_name}` must use `module:function` syntax",
            )
        ]

    match = _LOCATOR.fullmatch(raw_locator)
    assert match is not None
    module_name = match.group("module")
    attribute = match.group("attribute")
    try:
        _evict_module_outside_root(project_root, module_name)
        with _project_import_path(project_root):
            module = importlib.import_module(module_name)
        if attribute not in vars(module):
            raise AttributeError(f"module `{module_name}` has no direct attribute `{attribute}`")
        implementation = vars(module)[attribute]
    except Exception as exc:  # noqa: BLE001 - imports are a diagnostic boundary.
        return None, [
            Diagnostic(
                "TGT105",
                f"Could not import `{raw_locator}` for `{entry_name}`",
                hint=f"{type(exc).__name__}: {exc}",
            )
        ]

    if not callable(implementation):
        return None, [
            Diagnostic(
                "TGT106",
                f"Python locator `{raw_locator}` for `{entry_name}` is not callable",
            )
        ]

    parameters, signature_inspected = _inspect_parameters(cast(Callable[..., object], implementation))
    diagnostics: list[Diagnostic] = []
    if isinstance(semantic, CapabilityIR) and signature_inspected:
        diagnostics.extend(_signature_diagnostics(entry_name, semantic.parameters, parameters))
    semantic_id = str(semantic.id if isinstance(semantic, CapabilityIR) else semantic)
    return (
        ResolvedImplementationIdentity(
            section=section,
            semantic_id=semantic_id,
            locator=raw_locator,
            module=module_name,
            attribute=attribute,
            signature_inspected=signature_inspected,
            parameters=parameters,
        ),
        diagnostics,
    )


def _inspect_parameters(
    implementation: Callable[..., object],
) -> tuple[tuple[ResolvedParameterIdentity, ...], bool]:
    try:
        signature = inspect.signature(implementation, follow_wrapped=False)
    except (TypeError, ValueError):
        return (), False
    parameters: list[ResolvedParameterIdentity] = []
    for parameter in signature.parameters.values():
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            kind: ParameterKind = "positional_only"
        elif parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            kind = "keyword_only"
        else:
            kind = "positional_or_keyword"
        parameters.append(
            ResolvedParameterIdentity(
                name=parameter.name,
                required=parameter.default is inspect.Parameter.empty,
                kind=kind,
            )
        )
    return tuple(parameters), True


def _signature_diagnostics(
    entry_name: str,
    expected_parameters: tuple[ParameterIR, ...],
    actual_parameters: tuple[ResolvedParameterIdentity, ...],
) -> list[Diagnostic]:
    expected = {parameter.name: parameter.required for parameter in expected_parameters}
    actual = {parameter.name: parameter.required for parameter in actual_parameters}
    diagnostics: list[Diagnostic] = []
    if set(expected) != set(actual):
        diagnostics.append(
            Diagnostic(
                "TGT107",
                f"Callable for `{entry_name}` has parameter names that do not match canonical IR",
                hint=f"Expected {sorted(expected)}; found {sorted(actual)}.",
            )
        )
        return diagnostics
    mismatches = [name for name in sorted(expected) if expected[name] != actual[name]]
    if mismatches:
        diagnostics.append(
            Diagnostic(
                "TGT107",
                f"Callable for `{entry_name}` has parameter requiredness that does not match canonical IR",
                hint="Mismatched parameters: " + ", ".join(mismatches),
            )
        )
    return diagnostics


@contextmanager
def _project_import_path(project_root: Path) -> Iterator[None]:
    path = str(project_root)
    inserted = path not in sys.path
    if inserted:
        sys.path.insert(0, path)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


def _evict_module_outside_root(project_root: Path, module_name: str) -> None:
    loaded = sys.modules.get(module_name)
    loaded_file = getattr(loaded, "__file__", None) if loaded is not None else None
    if loaded is None:
        return
    if isinstance(loaded_file, str) and Path(loaded_file).resolve().is_relative_to(project_root):
        return
    for name in tuple(sys.modules):
        if name == module_name or name.startswith(f"{module_name}."):
            sys.modules.pop(name, None)


def _diagnostic_dict(diagnostic: Diagnostic) -> dict[str, object]:
    result: dict[str, object] = {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "severity": diagnostic.severity,
    }
    if diagnostic.hint is not None:
        result["hint"] = diagnostic.hint
    return result


__all__ = [
    "BindingSection",
    "ParameterKind",
    "ResolvedImplementationIdentity",
    "ResolvedParameterIdentity",
    "TargetBindingConformanceResult",
    "validate_target_binding_conformance",
]
