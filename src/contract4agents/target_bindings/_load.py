"""TOML loading and model construction for target bindings."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

from contract4agents._strict_json import json_object
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
            payload: object = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return TargetBindingsLoad(
            path=path,
            bindings=None,
            diagnostics=(Diagnostic("TGT001", f"Invalid target bindings TOML in `{path}`", hint=str(exc)),),
        )

    diagnostics = validate_target_bindings_payload(payload, path)
    if diagnostics:
        return TargetBindingsLoad(path=path, bindings=None, diagnostics=tuple(diagnostics))
    return TargetBindingsLoad(path=path, bindings=_build_target_bindings(json_object("target bindings", payload), path))


def _target_bindings_path(root: Path, bindings_path: Path | str | None) -> Path:
    if bindings_path is None:
        return root / DEFAULT_TARGET_BINDINGS_FILENAME
    path = Path(bindings_path)
    return path if path.is_absolute() else root / path


def _build_target_bindings(payload: Mapping[str, object], path: Path) -> TargetBindings:
    raw_targets = json_object("targets", payload["targets"])
    targets = {name: _build_target(json_object(f"targets.{name}", raw_targets[name])) for name in sorted(raw_targets)}
    return TargetBindings(path=path, schema_version=str(payload["schema_version"]), targets=targets)


def _build_target(payload: Mapping[str, object]) -> TargetBinding:
    return TargetBinding(
        adapter=str(payload["adapter"]),
        tools=_build_entries(payload.get("tools", {})),
        datasources=_build_entries(payload.get("datasources", {})),
        external_context=_build_entries(payload.get("external_context", {})),
        environments=_build_entries(payload.get("environments", {})),
        profiles=_build_profiles(payload.get("profiles", {})),
    )


def _build_entries(payload: object) -> dict[str, BindingEntry]:
    entries = json_object("binding entries", payload)
    return {name: BindingEntry(dict(json_object(f"binding {name}", entries[name]))) for name in sorted(entries)}


def _build_profiles(payload: object) -> dict[str, TargetProfile]:
    profiles = json_object("profiles", payload)
    return {name: _build_profile(json_object(f"profile {name}", profiles[name])) for name in sorted(profiles)}


def _build_profile(payload: Mapping[str, object]) -> TargetProfile:
    raw_agents = json_object("profile agents", payload.get("agents", {}))
    agents = {name: _build_agent_profile(json_object(f"agent {name}", raw_agents[name])) for name in sorted(raw_agents)}
    return TargetProfile(
        default_model=_optional_model(payload.get("default_model")),
        agents=agents,
        options=dict(json_object("options", payload.get("options", {}))),
    )


def _build_agent_profile(payload: Mapping[str, object]) -> AgentProfile:
    return AgentProfile(
        model=_optional_model(payload.get("model")),
        options=dict(json_object("options", payload.get("options", {}))),
    )


def _optional_model(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError("Model must be a string")


__all__ = ["load_target_bindings"]
