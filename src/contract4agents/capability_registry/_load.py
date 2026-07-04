"""Capability registry file loading."""

from __future__ import annotations

import json
from pathlib import Path

from contract4agents.capability_registry._models import (
    DEFAULT_REGISTRY_FILENAME,
    CapabilityRegistry,
    CapabilityRegistryLoad,
)
from contract4agents.capability_registry._validation import validate_payload
from contract4agents.diagnostics import Diagnostic


def load_capability_registry(
    root: Path | str,
    registry_path: Path | str | None = None,
    *,
    required: bool = False,
) -> CapabilityRegistryLoad:
    """Load and validate the optional project capability registry."""

    root_path = Path(root)
    path = _registry_path(root_path, registry_path)
    explicit = registry_path is not None
    if not path.exists():
        if required or explicit:
            return CapabilityRegistryLoad(
                path=path,
                registry=None,
                diagnostics=[
                    Diagnostic(
                        "CAP002",
                        f"Capability registry `{path}` was not found",
                        hint=f"Add `{DEFAULT_REGISTRY_FILENAME}` or pass --registry PATH.",
                    )
                ],
            )
        return CapabilityRegistryLoad(path=path, registry=None, diagnostics=[])
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return CapabilityRegistryLoad(
            path=path,
            registry=None,
            diagnostics=[Diagnostic("CAP001", f"Invalid capability registry JSON in `{path}`", hint=str(exc))],
        )
    diagnostics = validate_payload(payload, path)
    if diagnostics:
        return CapabilityRegistryLoad(path=path, registry=None, diagnostics=diagnostics)
    assert isinstance(payload, dict)
    return CapabilityRegistryLoad(
        path=path,
        registry=CapabilityRegistry(
            path=path,
            tools=payload.get("tools", {}),
            hosted_tools=payload.get("hosted_tools", {}),
            agents=payload.get("agents", {}),
            output_types=payload.get("output_types", {}),
            prompts=payload.get("prompts", {}),
            host_context=payload.get("host_context", {}),
        ),
        diagnostics=[],
    )


def _registry_path(root: Path, registry_path: Path | str | None) -> Path:
    if registry_path is None:
        return root / DEFAULT_REGISTRY_FILENAME
    path = Path(registry_path)
    return path if path.is_absolute() else root / path


__all__ = ["load_capability_registry"]
