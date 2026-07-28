"""Honest provider capability descriptor for OpenAI planning."""

from __future__ import annotations

from collections.abc import Mapping

from contract4agents.diagnostics import Diagnostic
from contract4agents.ir import CompositionEdgeIR, GrantIR
from contract4agents.planning import (
    BindingExecution,
    BindingKind,
    BindingPlan,
    BindingResolution,
    MappingSupport,
    PlannerCapabilities,
    in_process_isolation_support,
)
from contract4agents.target_bindings import BindingEntry, BindingSection

_HOST_LOCATORS = frozenset({"python", "typescript", "module"})
_PROVIDER_LOCATORS = frozenset({"provider", "provider_tool", "tool"})
_REMOTE_LOCATORS = frozenset({"endpoint", "url", "remote", "mcp"})


class OpenAIMappingResolver:
    """Context-sensitive support decisions for the OpenAI materializer."""

    def resolve_binding(
        self,
        *,
        kind: BindingKind,
        locator: Mapping[str, object],
    ) -> BindingResolution:
        keys = set(locator)
        if "python" in keys and not (keys & ((_HOST_LOCATORS - {"python"}) | _PROVIDER_LOCATORS | _REMOTE_LOCATORS)):
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
            and not (keys & (_HOST_LOCATORS | _REMOTE_LOCATORS))
            and locator.get("provider") == "openai"
            and hosted_tool == "web_search"
        ):
            return BindingResolution(
                "provider_hosted",
                MappingSupport("exact", "openai.web_search"),
            )
        execution: BindingExecution
        if keys & _PROVIDER_LOCATORS:
            execution = "provider_hosted"
        elif keys & _REMOTE_LOCATORS:
            execution = "remote"
        else:
            execution = "host"
        return BindingResolution(execution, MappingSupport("unsupported", None))

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


_MAPPING_RESOLVER = OpenAIMappingResolver()


def openai_target_binding_validator(
    target_name: str,
    section: BindingSection,
    name: str,
    entry: BindingEntry,
) -> tuple[Diagnostic, ...]:
    """Validate static OpenAI locator shapes without importing the optional SDK."""

    keys = set(entry.values)
    families = {
        family
        for family, locators in (
            ("host", _HOST_LOCATORS),
            ("provider_hosted", _PROVIDER_LOCATORS),
            ("remote", _REMOTE_LOCATORS),
        )
        if keys & locators
    }
    configured_tool = entry.values.get("tool")
    configured_alias = entry.values.get("provider_tool")
    label = f"targets.{target_name}.{section}.{name}"
    if len(families) > 1 or (
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
    python_binding = "python" in keys and not (
        keys
        & (
            (_HOST_LOCATORS - {"python"})
            | _PROVIDER_LOCATORS
            | _REMOTE_LOCATORS
        )
    )
    hosted_tool = configured_tool or configured_alias
    hosted_web_search = (
        section == "tools"
        and not (keys & (_HOST_LOCATORS | _REMOTE_LOCATORS))
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
]
