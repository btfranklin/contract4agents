"""Honest Strands Agents capability descriptor and target validators."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from contract4agents.diagnostics import Diagnostic
from contract4agents.ir import (
    CanonicalIR,
    CapabilityIR,
    CompositionEdgeIR,
    ControlIR,
    GrantIR,
)
from contract4agents.planning import (
    AgentPlan,
    BindingKind,
    BindingPlan,
    BindingResolution,
    MappingSupport,
    PlannerCapabilities,
    describe_locator,
    in_process_isolation_support,
)
from contract4agents.target_bindings import BindingEntry, BindingSection, TargetBinding


class StrandsMappingResolver:
    """Context-sensitive support decisions for the Strands materializer."""

    def resolve_binding(
        self,
        *,
        kind: BindingKind,
        locator: Mapping[str, object],
    ) -> BindingResolution:
        del kind
        description = describe_locator(locator)
        if description.is_python_binding:
            return BindingResolution(
                "host",
                MappingSupport("exact", "host.implementation_binding"),
            )
        return BindingResolution(description.execution, MappingSupport("unsupported", None))

    def binding_support(
        self,
        *,
        ir: CanonicalIR,
        capability: CapabilityIR | None,
        kind: BindingKind,
        locator: Mapping[str, object],
        declared: BindingResolution,
    ) -> BindingResolution | None:
        del ir, capability, kind, locator, declared
        return None

    def grant_support(
        self,
        *,
        grant: GrantIR,
        binding: BindingPlan,
        named_environment: bool,
    ) -> MappingSupport | None:
        if binding.kind == "tool" and (
            grant.isolation_id is not None or named_environment
        ):
            return MappingSupport("unsupported", None)
        return None

    def approval_support(
        self,
        *,
        grant: GrantIR,
        binding: BindingPlan,
    ) -> MappingSupport | None:
        del grant
        if binding.kind == "tool" and binding.execution == "host":
            return MappingSupport(
                "exact",
                "strands.human_in_the_loop",
                expected_event_types=(
                    "approval.requested",
                    "approval.completed",
                    "tool.started",
                ),
            )
        return MappingSupport("unsupported", None)

    def composition_support(
        self,
        *,
        edge: CompositionEdgeIR,
        declared: MappingSupport,
    ) -> MappingSupport | None:
        del declared
        if edge.mode == "handoff" or edge.history != "none":
            return MappingSupport("unsupported", None)
        return None

    def control_support(
        self,
        *,
        control: ControlIR,
        agent: AgentPlan | None,
        has_tools: bool,
        tool_bindings: tuple[BindingPlan, ...],
        declared: MappingSupport,
    ) -> MappingSupport | None:
        del control, agent, has_tools, tool_bindings, declared
        return None


_MAPPING_RESOLVER = StrandsMappingResolver()


def strands_target_binding_validator(
    target_name: str,
    section: BindingSection,
    name: str,
    entry: BindingEntry,
) -> tuple[Diagnostic, ...]:
    """Validate locator shapes without importing the optional Strands SDK."""

    description = describe_locator(entry.values)
    label = f"targets.{target_name}.{section}.{name}"
    if description.has_mixed_families:
        return (
            Diagnostic(
                "TGT110",
                f"Strands binding `{label}` has ambiguous implementation locators",
                hint="Select one implementation locator family.",
            ),
        )
    python_binding = description.is_python_binding
    if python_binding:
        return ()
    return (
        Diagnostic(
            "TGT111",
            f"Strands binding `{label}` uses an unsupported implementation locator",
            hint=(
                'Use a `python = "module:function"` locator. Provider-hosted, '
                "remote, TypeScript, module, and MCP locators are not supported."
            ),
        ),
    )


def strands_target_profile_validator(
    ir: CanonicalIR,
    target_name: str,
    target: TargetBinding,
    project_root: Path,
) -> tuple[Diagnostic, ...]:
    """Return Strands-specific profile diagnostics.

    Generic conformance owns model-factory locator and signature validation.
    Strands accepts any non-empty model ID through its native Bedrock path, so
    there are no additional static profile restrictions.
    """

    del ir, target_name, target, project_root
    return ()


def strands_planner_capabilities() -> PlannerCapabilities:
    """Return mappings implemented by the Strands Agents materializer."""

    return PlannerCapabilities.create(
        adapter="strands",
        version="1",
        approval=MappingSupport(
            "exact",
            "strands.human_in_the_loop",
            expected_event_types=(
                "approval.requested",
                "approval.completed",
                "tool.started",
            ),
        ),
        composition={
            "delegate:none": MappingSupport(
                "emulated",
                "strands.agent_as_tool.typed_wrapper",
                expected_event_types=(
                    "composition.started",
                    "composition.completed",
                ),
                host_obligation=(
                    "Verify model-supplied delegate values against declared source "
                    "mappings when source-value equality is required."
                ),
            ),
            "delegate:summary": MappingSupport("unsupported", None),
            "delegate:full": MappingSupport("unsupported", None),
            "handoff:none": MappingSupport("unsupported", None),
            "handoff:summary": MappingSupport("unsupported", None),
            "handoff:full": MappingSupport("unsupported", None),
        },
        controls={
            "adapter": MappingSupport("exact", "strands.structured_output_model"),
            "runtime": MappingSupport(
                "exact",
                "contract4agents.runtime_assessor",
            ),
            "host_attested": MappingSupport(
                "host_enforced",
                "contract4agents.host_attestation",
                host_obligation="Provide a signed or recorded host attestation.",
            ),
            "semantic": MappingSupport(
                "emulated",
                "contract4agents.semantic_judge",
            ),
            "advisory": MappingSupport("unsupported", None),
        },
        isolation=in_process_isolation_support(),
        expected_event_types=(
            "agent.started",
            "agent.completed",
            "output.accepted",
        ),
        mapping_resolver=_MAPPING_RESOLVER,
    )


__all__ = [
    "StrandsMappingResolver",
    "strands_planner_capabilities",
    "strands_target_binding_validator",
    "strands_target_profile_validator",
]
