"""Public contract-to-native materialization pipeline."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from contract4agents.compiler import artifact_digests, compile_project
from contract4agents.ir import FrozenMap, SemanticId
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue
from contract4agents.materialization._models import (
    MaterializationProvider,
    MaterializationResult,
)
from contract4agents.materialization._openai import OpenAIMaterializationProvider
from contract4agents.materialization._tracing import NOOP_MATERIALIZATION_TRACE_SINK, MaterializationTraceSink
from contract4agents.materialization._types import build_pydantic_types
from contract4agents.planning import plan_materialization
from contract4agents.runtime import EnvironmentProvider, InProcessEnvironment, load_python_ref
from contract4agents.target_bindings import (
    TargetBinding,
    TargetBindings,
    load_target_bindings,
    validate_target_binding_conformance,
)
from contract4agents.tracing import NormalizedTraceSink


def materialize(
    root: Path | str,
    target: str,
    profile: str,
    bindings: TargetBindings | Path | str | None = None,
    *,
    provider: MaterializationProvider | None = None,
    materialization_trace_sink: MaterializationTraceSink | None = None,
    normalized_trace_sink: NormalizedTraceSink | None = None,
) -> MaterializationResult:
    """Compile, plan, construct, and validate one framework-native agent graph."""

    project_root = Path(root).resolve()
    artifacts = compile_project(project_root)
    resolved_bindings = _load_bindings(project_root, bindings)
    target_binding = resolved_bindings.targets.get(target)
    if target_binding is None:
        raise MaterializationError(
            (MaterializationIssue("MAT101", f"Target bindings do not declare `{target}`"),)
        )

    conformance = validate_target_binding_conformance(
        artifacts.ir,
        resolved_bindings,
        target,
        project_root=project_root,
    )
    conformance_issues = tuple(
        MaterializationIssue(diagnostic.code, diagnostic.message)
        for diagnostic in conformance.diagnostics
        if diagnostic.severity == "error"
    )
    if conformance_issues:
        raise MaterializationError(conformance_issues)

    selected_provider = provider or _default_provider(target_binding)
    if selected_provider.adapter != target_binding.adapter:
        raise MaterializationError(
            (
                MaterializationIssue(
                    "MAT102",
                    f"Provider `{selected_provider.adapter}` does not match adapter `{target_binding.adapter}`",
                ),
            )
        )
    environment = _load_environment(project_root, target_binding, profile, bool(artifacts.ir.isolation_profiles))
    plan = plan_materialization(
        artifacts.ir,
        resolved_bindings,
        target=target,
        profile=profile,
        capabilities=selected_provider.planner_capabilities(environment),
        artifact_digests=artifact_digests(artifacts),
    )
    implementations = _resolve_implementations(project_root, plan)
    output_types = build_pydantic_types(artifacts.ir)
    context_runtime = ContextRuntime(
        artifacts.ir,
        plan,
        implementations,
        output_types,
        trace_sink=normalized_trace_sink,
    )
    graph = selected_provider.build_graph(
        ir=artifacts.ir,
        artifacts=artifacts,
        target=target_binding,
        plan=plan,
        implementations=implementations,
        output_types=output_types,
        context_runtime=context_runtime,
        environment=environment,
        materialization_trace_sink=(
            materialization_trace_sink or NOOP_MATERIALIZATION_TRACE_SINK
        ),
    )
    return MaterializationResult(graph=graph, plan=plan)


def _load_bindings(
    root: Path,
    bindings: TargetBindings | Path | str | None,
) -> TargetBindings:
    if isinstance(bindings, TargetBindings):
        return bindings
    loaded = load_target_bindings(root, bindings_path=bindings, required=True)
    if loaded.bindings is None or not loaded.ok:
        issues = tuple(
            MaterializationIssue(item.code, item.message) for item in loaded.diagnostics
        ) or (MaterializationIssue("MAT103", f"Could not load target bindings `{loaded.path}`"),)
        raise MaterializationError(issues)
    return loaded.bindings


def _default_provider(target: TargetBinding) -> MaterializationProvider:
    if target.adapter == "openai":
        return OpenAIMaterializationProvider()
    raise MaterializationError(
        (MaterializationIssue("MAT104", f"No materialization provider for adapter `{target.adapter}`"),)
    )


def _load_environment(
    root: Path,
    target: TargetBinding,
    profile_name: str,
    required: bool,
) -> EnvironmentProvider | None:
    if not required:
        return None
    profile = target.profiles.get(profile_name)
    if profile is None:
        return None  # The generic planner emits the precise unknown-profile issue.
    selected = profile.options.get("environment")
    if selected is None and "in_process" in target.environments:
        selected = "in_process"
    if selected is None and len(target.environments) == 1:
        selected = next(iter(target.environments))
    if not isinstance(selected, str) or selected not in target.environments:
        return None  # The generic planner emits the precise environment issue.
    locator = target.environments[selected].values.get("provider")
    if locator == InProcessEnvironment.provider_id:
        return InProcessEnvironment()
    if not isinstance(locator, str):
        return None
    try:
        factory = _load_project_python_ref(root, locator)
        instance = factory() if callable(factory) else factory
    except Exception as exc:  # noqa: BLE001 - provider loading boundary.
        raise MaterializationError(
            (MaterializationIssue("MAT105", f"Could not load environment provider `{locator}`: {exc}"),)
        ) from exc
    if not isinstance(instance, EnvironmentProvider):
        raise MaterializationError(
            (MaterializationIssue("MAT106", f"`{locator}` does not implement EnvironmentProvider"),)
        )
    return instance


def _resolve_implementations(root: Path, plan: object) -> FrozenMap[SemanticId, object]:
    from contract4agents.planning import MaterializationPlan

    native_plan = cast(MaterializationPlan, plan)
    values: list[tuple[SemanticId, object]] = []
    issues: list[MaterializationIssue] = []
    with _project_import_path(root):
        for identifier, binding in native_plan.bindings.items():
            locator = binding.locator.get("python")
            if locator is None:
                if binding.execution == "host":
                    issues.append(
                        MaterializationIssue(
                            "MAT107",
                            f"Host binding `{identifier}` has no Python locator for this materializer",
                            identifier,
                        )
                    )
                elif binding.execution == "remote":
                    issues.append(
                        MaterializationIssue(
                            "MAT108",
                            f"OpenAI materialization does not implement remote binding `{identifier}`",
                            identifier,
                        )
                    )
                continue
            if not isinstance(locator, str):
                issues.append(MaterializationIssue("MAT109", "Python locator must be a string", identifier))
                continue
            try:
                values.append((identifier, _load_project_python_ref(root, locator)))
            except Exception as exc:  # noqa: BLE001 - implementation loading boundary.
                issues.append(
                    MaterializationIssue(
                        "MAT110",
                        f"Could not import `{locator}`: {type(exc).__name__}: {exc}",
                        identifier,
                    )
                )
    if issues:
        raise MaterializationError(tuple(issues))
    return FrozenMap(values)


def _load_project_python_ref(root: Path, locator: str) -> object:
    module_name, _, _attribute = locator.partition(":")
    loaded = sys.modules.get(module_name)
    loaded_file = getattr(loaded, "__file__", None) if loaded is not None else None
    if loaded is not None and (
        not isinstance(loaded_file, str)
        or not Path(loaded_file).resolve().is_relative_to(root)
    ):
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(f"{module_name}."):
                sys.modules.pop(name, None)
    with _project_import_path(root):
        return load_python_ref(locator)


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


__all__ = ["materialize"]
