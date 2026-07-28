"""Private registry for Contract4Agents' built-in target adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

from contract4agents.planning import PlannerCapabilities
from contract4agents.target_bindings import (
    AdapterBindingValidator,
    AdapterProfileValidator,
)

if TYPE_CHECKING:
    from contract4agents.materialization import MaterializationProvider


@dataclass(frozen=True)
class AdapterRegistration:
    """All entry points needed to use one built-in adapter."""

    adapter: str
    planner_capabilities: Callable[[], PlannerCapabilities]
    provider_factory: Callable[[], MaterializationProvider]
    binding_validator: AdapterBindingValidator
    profile_validator: AdapterProfileValidator


@cache
def get_adapter_registration(adapter: str) -> AdapterRegistration | None:
    """Resolve a built-in adapter without importing optional provider SDKs."""

    factory = _REGISTRATION_FACTORIES.get(adapter)
    return factory() if factory is not None else None


def _openai_registration() -> AdapterRegistration:
    from contract4agents.adapters._openai import (
        openai_target_binding_validator,
        openai_target_profile_validator,
    )
    from contract4agents.materialization._openai import OpenAIMaterializationProvider

    def planner_capabilities() -> PlannerCapabilities:
        return OpenAIMaterializationProvider().planner_capabilities(None)

    return AdapterRegistration(
        adapter="openai",
        planner_capabilities=planner_capabilities,
        provider_factory=OpenAIMaterializationProvider,
        binding_validator=openai_target_binding_validator,
        profile_validator=openai_target_profile_validator,
    )


def _strands_registration() -> AdapterRegistration:
    from contract4agents.adapters._strands import (
        strands_target_binding_validator,
        strands_target_profile_validator,
    )
    from contract4agents.materialization._strands import StrandsMaterializationProvider

    def planner_capabilities() -> PlannerCapabilities:
        return StrandsMaterializationProvider().planner_capabilities(None)

    return AdapterRegistration(
        adapter="strands",
        planner_capabilities=planner_capabilities,
        provider_factory=StrandsMaterializationProvider,
        binding_validator=strands_target_binding_validator,
        profile_validator=strands_target_profile_validator,
    )


def _google_adk_registration() -> AdapterRegistration:
    from contract4agents.adapters._google_adk import (
        google_adk_target_binding_validator,
        google_adk_target_profile_validator,
    )
    from contract4agents.materialization._google_adk import (
        GoogleADKMaterializationProvider,
    )

    def planner_capabilities() -> PlannerCapabilities:
        return GoogleADKMaterializationProvider().planner_capabilities(None)

    return AdapterRegistration(
        adapter="google_adk",
        planner_capabilities=planner_capabilities,
        provider_factory=GoogleADKMaterializationProvider,
        binding_validator=google_adk_target_binding_validator,
        profile_validator=google_adk_target_profile_validator,
    )


_REGISTRATION_FACTORIES: dict[str, Callable[[], AdapterRegistration]] = {
    "google_adk": _google_adk_registration,
    "openai": _openai_registration,
    "strands": _strands_registration,
}


__all__ = ["AdapterRegistration", "get_adapter_registration"]
