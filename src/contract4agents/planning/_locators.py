"""Provider-neutral implementation locator classification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from contract4agents.planning._models import BindingExecution

LocatorFamily = Literal["host", "provider_hosted", "remote"]

_HOST_KEYS = frozenset({"python", "typescript", "module"})
_PROVIDER_KEYS = frozenset({"provider", "provider_tool", "tool"})
_REMOTE_KEYS = frozenset({"endpoint", "url", "remote", "mcp"})


@dataclass(frozen=True)
class LocatorDescription:
    """Describe the provider-neutral structure of one binding locator."""

    keys: frozenset[str]
    families: frozenset[LocatorFamily]

    @property
    def is_python_binding(self) -> bool:
        """Return true only when Python is the sole implementation locator."""

        return "python" in self.keys and self.families == {"host"} and not (
            self.keys & (_HOST_KEYS - {"python"})
        )

    @property
    def has_mixed_families(self) -> bool:
        return len(self.families) > 1

    @property
    def execution(self) -> BindingExecution:
        if "provider_hosted" in self.families:
            return "provider_hosted"
        if "remote" in self.families:
            return "remote"
        return "host"


def describe_locator(locator: Mapping[str, object]) -> LocatorDescription:
    """Classify implementation keys without making provider support claims."""

    keys = frozenset(locator)
    families: set[LocatorFamily] = set()
    if keys & _HOST_KEYS:
        families.add("host")
    if keys & _PROVIDER_KEYS:
        families.add("provider_hosted")
    if keys & _REMOTE_KEYS:
        families.add("remote")
    return LocatorDescription(keys, frozenset(families))


__all__ = ["LocatorDescription", "LocatorFamily", "describe_locator"]
