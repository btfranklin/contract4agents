"""Sensitive target-option key detection without inspecting values."""

from __future__ import annotations

import re
from collections.abc import Mapping

from contract4agents.target_bindings._models import TargetBinding

_SEPARATORS = re.compile(r"[^a-z0-9]+")
_SENSITIVE_NAMES = frozenset(
    {
        "accesstoken",
        "apikey",
        "apitoken",
        "authorization",
        "authtoken",
        "awsaccesskeyid",
        "awssecretaccesskey",
        "awssessiontoken",
        "bearertoken",
        "clientsecret",
        "credential",
        "credentials",
        "password",
        "privatekey",
        "privatetoken",
        "proxyauthorization",
        "refreshtoken",
        "secret",
        "secretaccesskey",
        "secretkey",
        "sessiontoken",
        "serviceaccountjson",
        "serviceaccountkey",
        "token",
    }
)
_SENSITIVE_SUFFIXES = (
    "accesstoken",
    "apikey",
    "apitoken",
    "authtoken",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "privatekey",
    "refreshtoken",
    "secretkey",
)


def is_sensitive_option_name(name: str) -> bool:
    """Return whether one option name identifies credential material."""

    normalized = _SEPARATORS.sub("", name.casefold())
    return normalized in _SENSITIVE_NAMES or normalized.endswith(_SENSITIVE_SUFFIXES)


def sensitive_option_paths(value: object, path: str) -> tuple[str, ...]:
    """Return deterministic paths for credential-bearing keys in nested options."""

    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in sorted(value.items(), key=lambda item: str(item[0])):
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if is_sensitive_option_name(key):
                found.append(child_path)
            found.extend(sensitive_option_paths(child, child_path))
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            found.extend(sensitive_option_paths(child, f"{path}[{index}]"))
    return tuple(found)


def target_sensitive_option_paths(
    target_name: str,
    target: TargetBinding,
) -> tuple[str, ...]:
    """Return every sensitive option path in one programmatic target binding."""

    root = f"targets.{target_name}"
    found: list[str] = []
    for section_name, entries in (
        ("tools", target.tools),
        ("datasources", target.datasources),
        ("external_context", target.external_context),
        ("environments", target.environments),
    ):
        for name, entry in entries.items():
            found.extend(
                sensitive_option_paths(entry.values, f"{root}.{section_name}.{name}")
            )
    for profile_name, profile in target.profiles.items():
        profile_path = f"{root}.profiles.{profile_name}"
        found.extend(sensitive_option_paths(profile.options, f"{profile_path}.options"))
        for agent_name, agent in profile.agents.items():
            found.extend(
                sensitive_option_paths(
                    agent.options,
                    f"{profile_path}.agents.{agent_name}.options",
                )
            )
    return tuple(sorted(found))


__all__ = [
    "is_sensitive_option_name",
    "sensitive_option_paths",
    "target_sensitive_option_paths",
]
