from __future__ import annotations

import pytest

from contract4agents.planning import BindingExecution, LocatorFamily, describe_locator


@pytest.mark.parametrize(
    ("locator", "families", "execution", "is_python", "is_mixed"),
    (
        ({}, frozenset(), "host", False, False),
        (
            {"python": "app.tools:lookup", "timeout": 5},
            frozenset({"host"}),
            "host",
            True,
            False,
        ),
        (
            {"typescript": "app/tools:lookup"},
            frozenset({"host"}),
            "host",
            False,
            False,
        ),
        (
            {"provider": "openai", "tool": "web_search"},
            frozenset({"provider_hosted"}),
            "provider_hosted",
            False,
            False,
        ),
        (
            {"endpoint": "https://example.test/tool"},
            frozenset({"remote"}),
            "remote",
            False,
            False,
        ),
        (
            {"python": "app.tools:lookup", "provider": "openai"},
            frozenset({"host", "provider_hosted"}),
            "provider_hosted",
            False,
            True,
        ),
        (
            {"provider": "openai", "endpoint": "https://example.test/tool"},
            frozenset({"provider_hosted", "remote"}),
            "provider_hosted",
            False,
            True,
        ),
    ),
)
def test_locator_description_is_the_shared_provider_neutral_classifier(
    locator: dict[str, object],
    families: frozenset[LocatorFamily],
    execution: BindingExecution,
    is_python: bool,
    is_mixed: bool,
) -> None:
    description = describe_locator(locator)

    assert description.keys == frozenset(locator)
    assert description.families == families
    assert description.execution == execution
    assert description.is_python_binding is is_python
    assert description.has_mixed_families is is_mixed
