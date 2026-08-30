from __future__ import annotations

from contract4agents.adapters._registry import get_adapter_registration
from contract4agents.adapters.google_adk import (
    GoogleADKMaterializationProvider,
    GoogleADKSDK,
)
from contract4agents.adapters.strands import (
    StrandsMaterializationProvider,
    StrandsSDK,
)
from contract4agents.tracing import (
    GoogleADKNormalizedTraceRouter,
    GoogleADKNormalizedTraceSession,
    StrandsNormalizedTraceRouter,
    StrandsNormalizedTraceSession,
)


def test_builtin_adapter_registry_keeps_planning_and_default_provider_versions_aligned() -> None:
    for adapter in ("openai", "strands", "google_adk"):
        registration = get_adapter_registration(adapter)
        assert registration is not None
        provider = registration.provider_factory()

        assert registration.adapter == adapter
        assert provider.adapter == adapter
        assert registration.planner_capabilities().adapter == adapter
        assert registration.planner_capabilities().version == provider.planner_capabilities(None).version


def test_builtin_adapter_registry_does_not_claim_unknown_adapters() -> None:
    assert get_adapter_registration("third_party") is None


def test_adapter_facades_expose_materialization_and_trace_interfaces() -> None:
    assert StrandsMaterializationProvider.adapter == "strands"
    assert GoogleADKMaterializationProvider.adapter == "google_adk"
    assert StrandsSDK is not None
    assert GoogleADKSDK is not None
    assert StrandsNormalizedTraceRouter is not None
    assert StrandsNormalizedTraceSession is not None
    assert GoogleADKNormalizedTraceRouter is not None
    assert GoogleADKNormalizedTraceSession is not None
