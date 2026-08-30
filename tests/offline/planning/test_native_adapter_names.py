from __future__ import annotations

import pytest

from contract4agents.adapters import _native_names
from contract4agents.adapters._native_names import NativeNameRegistry, native_name
from contract4agents.ir import semantic_id


def test_native_adapter_names_are_stable_sdk_safe_and_bounded() -> None:
    identifier = semantic_id("tool", "Team Status / Publish")

    first = native_name("tool", identifier, "Team Status / Publish")
    second = native_name("tool", identifier, "Team Status / Publish")

    assert first == second
    assert first.startswith("c4a_tool_team_status_publish_")
    assert len(first) <= 64
    assert first.rsplit("_", 1)[-1].isalnum()
    assert len(first.rsplit("_", 1)[-1]) == 8


def test_native_name_registry_records_identity_and_rejects_collisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = NativeNameRegistry()
    first = semantic_id("agent", "First")
    second = semantic_id("agent", "Second")

    assigned = registry.assign("agent", first)
    assert registry.semantic_id_for(assigned) == first
    assert registry.mappings[assigned] == first

    monkeypatch.setattr(_native_names, "native_name", lambda *_args, **_kwargs: assigned)
    with pytest.raises(ValueError, match="collides"):
        registry.assign("agent", second)
