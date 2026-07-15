"""TOML loading and model construction for target bindings."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

from contract4agents.diagnostics import Diagnostic
from contract4agents.target_bindings._models import (
    DEFAULT_TARGET_BINDINGS_FILENAME,
    AgentProfile,
    BindingEntry,
    TargetBinding,
    TargetBindings,
    TargetBindingsLoad,
    TargetProfile,
)
from contract4agents.target_bindings._validation import validate_target_bindings_payload


def load_target_bindings(
    root: Path | str,
    bindings_path: Path | str | None = None,
    *,
    required: bool = False,
) -> TargetBindingsLoad:
    """Load target bindings without importing or executing bound application code."""

    root_path = Path(root)
    path = _target_bindings_path(root_path, bindings_path)
    explicit = bindings_path is not None
    if not path.exists():
        if required or explicit:
            return TargetBindingsLoad(
                path=path,
                bindings=None,
                diagnostics=(
                    Diagnostic(
                        "TGT002",
                        f"Target bindings `{path}` were not found",
                        hint=f"Add `{DEFAULT_TARGET_BINDINGS_FILENAME}` or supply a bindings path.",
                    ),
                ),
            )
        return TargetBindingsLoad(path=path, bindings=None)

    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return TargetBindingsLoad(
            path=path,
            bindings=None,
            diagnostics=(Diagnostic("TGT001", f"Invalid target bindings TOML in `{path}`", hint=str(exc)),),
        )

    diagnostics = validate_target_bindings_payload(payload, path)
    if diagnostics:
        return TargetBindingsLoad(path=path, bindings=None, diagnostics=tuple(diagnostics))
    return TargetBindingsLoad(path=path, bindings=_build_target_bindings(payload, path))


def _target_bindings_path(root: Path, bindings_path: Path | str | None) -> Path:
    if bindings_path is None:
        return root / DEFAULT_TARGET_BINDINGS_FILENAME
    path = Path(bindings_path)
    return path if path.is_absolute() else root / path


def _build_target_bindings(payload: dict[str, Any], path: Path) -> TargetBindings:
    raw_targets = cast(dict[str, dict[str, Any]], payload["targets"])
    targets = {name: _build_target(raw_targets[name]) for name in sorted(raw_targets)}
    return TargetBindings(path=path, schema_version=str(payload["schema_version"]), targets=targets)


def _build_target(payload: dict[str, Any]) -> TargetBinding:
    return TargetBinding(
        adapter=str(payload["adapter"]),
        tools=_build_entries(payload.get("tools", {})),
        datasources=_build_entries(payload.get("datasources", {})),
        external_context=_build_entries(payload.get("external_context", {})),
        environments=_build_entries(payload.get("environments", {})),
        profiles=_build_profiles(payload.get("profiles", {})),
    )


def _build_entries(payload: dict[str, dict[str, object]]) -> dict[str, BindingEntry]:
    return {name: BindingEntry(dict(payload[name])) for name in sorted(payload)}


def _build_profiles(payload: dict[str, dict[str, Any]]) -> dict[str, TargetProfile]:
    return {name: _build_profile(payload[name]) for name in sorted(payload)}


def _build_profile(payload: dict[str, Any]) -> TargetProfile:
    raw_agents = cast(dict[str, dict[str, Any]], payload.get("agents", {}))
    agents = {name: _build_agent_profile(raw_agents[name]) for name in sorted(raw_agents)}
    return TargetProfile(
        default_model=cast(str | None, payload.get("default_model")),
        agents=agents,
        options=dict(cast(dict[str, object], payload.get("options", {}))),
    )


def _build_agent_profile(payload: dict[str, Any]) -> AgentProfile:
    return AgentProfile(
        model=cast(str | None, payload.get("model")),
        options=dict(cast(dict[str, object], payload.get("options", {}))),
    )


__all__ = ["load_target_bindings"]
