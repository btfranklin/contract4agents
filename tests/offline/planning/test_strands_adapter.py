from __future__ import annotations

from contract4agents.adapters.strands import (
    StrandsMappingResolver,
    strands_planner_capabilities,
    strands_target_binding_validator,
)
from contract4agents.target_bindings import BindingEntry


def test_strands_mapping_support_is_explicit_and_narrow() -> None:
    capabilities = strands_planner_capabilities()
    resolver = StrandsMappingResolver()

    python = resolver.resolve_binding(
        kind="tool",
        locator={"python": "app_tools:lookup"},
    )
    hosted = resolver.resolve_binding(
        kind="tool",
        locator={"provider": "strands", "tool": "web_search"},
    )
    remote = resolver.resolve_binding(
        kind="tool",
        locator={"mcp": "https://example.test/mcp"},
    )

    assert (python.execution, python.support.outcome) == ("host", "exact")
    assert (hosted.execution, hosted.support.outcome) == (
        "provider_hosted",
        "unsupported",
    )
    assert (remote.execution, remote.support.outcome) == (
        "remote",
        "unsupported",
    )
    assert capabilities.composition["delegate:none"].outcome == "emulated"
    assert capabilities.composition["delegate:summary"].outcome == "unsupported"
    assert capabilities.composition["handoff:none"].outcome == "unsupported"
    assert capabilities.controls["adapter"].outcome == "exact"


def test_strands_target_validator_accepts_only_unambiguous_python_locators() -> None:
    assert (
        strands_target_binding_validator(
            "strands",
            "tools",
            "records.lookup",
            BindingEntry({"python": "app_tools:lookup"}),
        )
        == ()
    )

    ambiguous = strands_target_binding_validator(
        "strands",
        "tools",
        "records.lookup",
        BindingEntry(
            {
                "python": "app_tools:lookup",
                "provider": "strands",
                "tool": "search",
            }
        ),
    )
    unsupported = strands_target_binding_validator(
        "strands",
        "tools",
        "records.lookup",
        BindingEntry({"typescript": "app/tools:lookup"}),
    )

    assert [item.code for item in ambiguous] == ["TGT110"]
    assert [item.code for item in unsupported] == ["TGT111"]
    assert unsupported[0].hint is not None
    assert "Provider-hosted" in unsupported[0].hint
