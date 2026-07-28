"""Deterministic SDK-safe names for built-in adapters."""

from __future__ import annotations

import hashlib
import re

from contract4agents.ir import FrozenMap, SemanticId

_UNSAFE = re.compile(r"[^A-Za-z0-9_]+")
_MAX_NAME_LENGTH = 64


def native_name(kind: str, identifier: SemanticId, display_name: str | None = None) -> str:
    """Return a stable identifier accepted by Strands and Google ADK."""

    safe_kind = _slug(kind) or "item"
    safe_name = _slug(display_name or "_".join(identifier.parts)) or "item"
    digest = hashlib.sha256(f"{kind}:{identifier}".encode()).hexdigest()[:8]
    prefix = f"c4a_{safe_kind}_"
    suffix = f"_{digest}"
    available = _MAX_NAME_LENGTH - len(prefix) - len(suffix)
    if available < 1:
        raise ValueError(f"Native name kind `{kind}` is too long")
    return f"{prefix}{safe_name[:available]}{suffix}"


class NativeNameRegistry:
    """Track native-to-contract identity mappings and reject collisions."""

    def __init__(self) -> None:
        self._native_to_semantic: dict[str, SemanticId] = {}

    def assign(
        self,
        kind: str,
        identifier: SemanticId,
        display_name: str | None = None,
    ) -> str:
        name = native_name(kind, identifier, display_name)
        existing = self._native_to_semantic.get(name)
        if existing is not None and existing != identifier:
            raise ValueError(
                f"Native name `{name}` collides for `{existing}` and `{identifier}`"
            )
        self._native_to_semantic[name] = identifier
        return name

    def semantic_id_for(self, name: str) -> SemanticId:
        return self._native_to_semantic[name]

    @property
    def mappings(self) -> FrozenMap[str, SemanticId]:
        return FrozenMap(self._native_to_semantic)


def _slug(value: str) -> str:
    return _UNSAFE.sub("_", value).strip("_").lower()


__all__ = ["NativeNameRegistry", "native_name"]
