"""Honest provider capability descriptor for OpenAI planning."""

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


class OpenAIMappingResolver:
    """Context-sensitive support decisions for the OpenAI materializer."""

    def resolve_binding(
        self,
        *,
        kind: BindingKind,
        locator: Mapping[str, object],
    ) -> BindingResolution:
        description = describe_locator(locator)
        keys = description.keys
        if description.is_python_binding:
            return BindingResolution(
                "host",
                MappingSupport("exact", "host.implementation_binding"),
            )
        aliases_compatible = not ({"tool", "provider_tool"} <= keys) or (
            locator.get("tool") == locator.get("provider_tool")
        )
        hosted_tool = locator.get("tool") or locator.get("provider_tool")
        if (
            kind == "tool"
            and aliases_compatible
            and description.families == {"provider_hosted"}
            and locator.get("provider") == "openai"
            and hosted_tool == "web_search"
        ):
            return BindingResolution(
                "provider_hosted",
                MappingSupport("exact", "openai.web_search"),
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
        if binding.kind == "tool" and (grant.isolation_id is not None or named_environment):
            return MappingSupport("unsupported", None)
        return None

    def approval_support(
        self,
        *,
        grant: GrantIR,
        binding: BindingPlan,
    ) -> MappingSupport | None:
        if binding.kind == "tool" and binding.execution == "host":
            return MappingSupport(
                "exact",
                "openai.function_tool.needs_approval",
                expected_event_types=("approval.requested", "approval.completed", "tool.started"),
            )
        return MappingSupport("unsupported", None)

    def composition_support(
        self,
        *,
        edge: CompositionEdgeIR,
        declared: MappingSupport,
    ) -> MappingSupport | None:
        if edge.mode == "handoff" and edge.isolation_id is not None:
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


_MAPPING_RESOLVER = OpenAIMappingResolver()


def openai_target_binding_validator(
    target_name: str,
    section: BindingSection,
    name: str,
    entry: BindingEntry,
) -> tuple[Diagnostic, ...]:
    """Validate static OpenAI locator shapes without importing the optional SDK."""

    description = describe_locator(entry.values)
    keys = description.keys
    configured_tool = entry.values.get("tool")
    configured_alias = entry.values.get("provider_tool")
    label = f"targets.{target_name}.{section}.{name}"
    if description.has_mixed_families or (
        {"tool", "provider_tool"} <= keys
        and configured_tool != configured_alias
    ):
        return (
            Diagnostic(
                "TGT110",
                f"OpenAI binding `{label}` has ambiguous implementation locators",
                hint="Select one locator family and do not give `tool` and `provider_tool` conflicting values.",
            ),
        )
    python_binding = description.is_python_binding
    hosted_tool = configured_tool or configured_alias
    hosted_web_search = (
        section == "tools"
        and description.families == {"provider_hosted"}
        and entry.values.get("provider") == "openai"
        and hosted_tool == "web_search"
    )
    if python_binding or hosted_web_search:
        return ()
    return (
        Diagnostic(
            "TGT111",
            f"OpenAI binding `{label}` uses an unsupported implementation locator",
            hint=(
                "Use a `python = \"module:function\"` locator, or bind a tool with "
                '`provider = "openai"` and `tool = "web_search"`.'
            ),
        ),
    )


def openai_target_profile_validator(
    ir: CanonicalIR,
    target_name: str,
    target: TargetBinding,
    project_root: Path,
) -> tuple[Diagnostic, ...]:
    """Reject model factories that the OpenAI materializer does not consume."""

    del ir, project_root
    diagnostics: list[Diagnostic] = []
    for profile_name, profile in target.profiles.items():
        if "model_factory" in profile.options:
            diagnostics.append(
                Diagnostic(
                    "TGT115",
                    (
                        f"OpenAI target `{target_name}` profile `{profile_name}` "
                        "does not support `model_factory`"
                    ),
                    hint="Use an OpenAI model identifier and provider options.",
                )
            )
        diagnostics.extend(
            Diagnostic(
                "TGT115",
                (
                    f"OpenAI target `{target_name}` profile `{profile_name}` agent "
                    f"`{agent_name}` does not support `model_factory`"
                ),
                hint="Use an OpenAI model identifier and provider options.",
            )
            for agent_name, agent_profile in profile.agents.items()
            if "model_factory" in agent_profile.options
        )
    return tuple(diagnostics)


def openai_planner_capabilities() -> PlannerCapabilities:
    """Return mappings implemented by the OpenAI Agents SDK materializer."""

    return PlannerCapabilities.create(
        adapter="openai",
        version="1",
        approval=MappingSupport(
            "exact",
            "openai.function_tool.needs_approval",
            expected_event_types=("approval.requested", "approval.completed", "tool.started"),
        ),
        composition={
            "delegate": MappingSupport(
                "emulated",
                "openai.agent_as_tool.model_supplied_typed_input",
                expected_event_types=("composition.started", "composition.completed"),
                host_obligation=(
                    "Verify model-supplied delegate values against declared source mappings when "
                    "source-value equality is required."
                ),
            ),
            "delegate:none": MappingSupport(
                "emulated",
                "openai.agent_as_tool.model_supplied_typed_input",
                expected_event_types=("composition.started", "composition.completed"),
                host_obligation=(
                    "Verify model-supplied delegate values against declared source mappings when "
                    "source-value equality is required."
                ),
            ),
            "delegate:summary": MappingSupport("unsupported", None),
            "delegate:full": MappingSupport("unsupported", None),
            "handoff": MappingSupport(
                "emulated",
                "openai.handoff.model_supplied_transfer",
                expected_event_types=("handoff.started", "handoff.completed"),
                host_obligation="Verify handoff input transfer against the declared mappings.",
            ),
            "handoff:none": MappingSupport(
                "emulated",
                "openai.handoff.input_filter",
                expected_event_types=("handoff.started", "handoff.completed"),
                host_obligation="Supply and verify declared handoff inputs outside conversation history.",
            ),
            "handoff:summary": MappingSupport("unsupported", None),
            "handoff:full": MappingSupport(
                "emulated",
                "openai.handoff.full_history.model_supplied_transfer",
                expected_event_types=("handoff.started", "handoff.completed"),
                host_obligation="Verify handoff input transfer against the declared mappings.",
            ),
        },
        controls={
            "adapter": MappingSupport("exact", "openai.output_type"),
            "runtime": MappingSupport("exact", "contract4agents.runtime_assessor"),
            "host_attested": MappingSupport(
                "host_enforced",
                "contract4agents.host_attestation",
                host_obligation="Provide a signed or recorded host attestation.",
            ),
            "semantic": MappingSupport("emulated", "contract4agents.semantic_judge"),
            "advisory": MappingSupport("unsupported", None),
        },
        isolation=in_process_isolation_support(),
        expected_event_types=("agent.started", "agent.completed", "output.accepted"),
        mapping_resolver=_MAPPING_RESOLVER,
    )


__all__ = [
    "OpenAIMappingResolver",
    "openai_planner_capabilities",
    "openai_target_binding_validator",
    "openai_target_profile_validator",
]
