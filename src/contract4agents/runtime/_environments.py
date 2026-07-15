"""Runtime environment-provider protocol for isolated agent execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contract4agents.ir import FrozenMap, SemanticId
    from contract4agents.planning import IsolationMappingPlan, MappingSupport


@dataclass(frozen=True)
class EnvironmentEnforcementEvidence:
    """Deterministic evidence that an isolation mapping was configured."""

    isolation_id: SemanticId
    environment: str
    provider: str
    dimensions: FrozenMap[str, str]
    mechanisms: FrozenMap[str, str]


@dataclass(frozen=True)
class EnvironmentRunRequest:
    """One isolated child invocation, expressed without provider SDK types."""

    isolation_id: SemanticId
    input_payload: object
    requested_dimensions: FrozenMap[str, str]
    declared_capabilities: tuple[str, ...]
    parent_context: object | None = None
    parent_state: object | None = None


EnvironmentInvocation = Callable[
    [object, object | None, object | None, tuple[str, ...] | None],
    Awaitable[object],
]


@runtime_checkable
class EnvironmentProvider(Protocol):
    """A runtime capable of enforcing declared isolation dimensions."""

    provider_id: str

    def planning_support(self) -> Mapping[str, MappingSupport]:
        """Return the dimensions this provider can honestly enforce."""

    def enforcement_evidence(self, plan: IsolationMappingPlan) -> EnvironmentEnforcementEvidence:
        """Describe the concrete mechanisms configured for one planned profile."""

    async def run(self, request: EnvironmentRunRequest, invoke: EnvironmentInvocation) -> object:
        """Run a child through the provider's isolation boundary."""


class InProcessEnvironment:
    """Fresh-context isolation without an OS filesystem or network boundary."""

    provider_id = "contract4agents.runtime:InProcessEnvironment"

    def planning_support(self) -> Mapping[str, MappingSupport]:
        from contract4agents.planning import in_process_isolation_support

        return in_process_isolation_support()

    def enforcement_evidence(self, plan: IsolationMappingPlan) -> EnvironmentEnforcementEvidence:
        from contract4agents.ir import FrozenMap

        dimensions = FrozenMap(
            (name, dimension.requested) for name, dimension in plan.dimensions.items()
        )
        mechanisms = FrozenMap(
            (name, dimension.mechanism or "") for name, dimension in plan.dimensions.items()
        )
        return EnvironmentEnforcementEvidence(
            isolation_id=plan.id,
            environment=plan.environment,
            provider=self.provider_id,
            dimensions=dimensions,
            mechanisms=mechanisms,
        )

    async def run(self, request: EnvironmentRunRequest, invoke: EnvironmentInvocation) -> object:
        requested = request.requested_dimensions
        strong_dimensions = {
            "filesystem": {"none", "ephemeral", "inherited_read_only"},
            "network": {"denied", "allowlisted"},
            "secrets": {"none", "declared_only"},
        }
        for dimension, unsupported in strong_dimensions.items():
            value = requested.get(dimension)
            if value in unsupported:
                raise RuntimeError(
                    f"In-process execution cannot enforce isolation `{dimension}:{value}`"
                )

        context = None if requested.get("context") == "explicit_only" else request.parent_context
        state = object() if requested.get("state") == "fresh" else request.parent_state
        capabilities = (
            request.declared_capabilities
            if requested.get("capabilities") == "declared_only"
            else None
        )
        return await invoke(request.input_payload, context, state, capabilities)


__all__ = [
    "EnvironmentEnforcementEvidence",
    "EnvironmentInvocation",
    "EnvironmentProvider",
    "EnvironmentRunRequest",
    "InProcessEnvironment",
]
