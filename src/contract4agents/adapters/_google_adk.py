"""Honest provider capability descriptor for Google Agent Development Kit."""

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
    ListTypeRef,
    NamedTypeRef,
    PrimitiveTypeRef,
    TypeIR,
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
from contract4agents.target_bindings import (
    BindingEntry,
    BindingSection,
    TargetBinding,
)

_GOOGLE_SEARCH_KEYS = frozenset({"provider", "tool", "model"})


class GoogleADKMappingResolver:
    """Context-sensitive support decisions for the Google ADK materializer."""

    def resolve_binding(
        self,
        *,
        kind: BindingKind,
        locator: Mapping[str, object],
    ) -> BindingResolution:
        description = describe_locator(locator)
        if description.is_python_binding:
            return BindingResolution(
                "host",
                MappingSupport("exact", "host.implementation_binding"),
            )
        if _is_google_search_locator(kind, locator):
            return BindingResolution(
                "provider_hosted",
                MappingSupport(
                    "emulated",
                    "google_adk.google_search_agent_tool.contract_wrapper",
                    expected_event_types=(
                        "tool.started",
                        "tool.completed",
                        "provider.response.normalized",
                    ),
                    host_obligation=(
                        "Preserve and display Google Search suggestions, renderedContent, "
                        "and citations whenever the provider supplies them."
                    ),
                ),
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
        if not _is_google_search_locator(kind, locator):
            return None
        if capability is not None and _is_google_search_capability(ir, capability):
            return declared
        return BindingResolution(
            "provider_hosted",
            MappingSupport("unsupported", None),
        )

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
        if binding.kind != "tool":
            return MappingSupport("unsupported", None)
        return MappingSupport(
            "exact",
            "google_adk.tool_context.request_confirmation",
            expected_event_types=(
                "approval.requested",
                "approval.completed",
                "tool.started",
            ),
        )

    def composition_support(
        self,
        *,
        edge: CompositionEdgeIR,
        declared: MappingSupport,
    ) -> MappingSupport | None:
        if edge.mode == "handoff":
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
        if (
            control.assessment != "adapter"
            or control.derived_from != control.agent_id
            or agent is None
        ):
            return None
        has_google_search = any(
            binding.locator.get("provider") == "google_adk"
            and binding.locator.get("tool") == "google_search"
            for binding in tool_bindings
        )
        uses_factory = "model_factory" in agent.model_options
        if (
            not uses_factory
            and not has_google_search
            and (not has_tools or agent.model.startswith("gemini-3"))
        ):
            return MappingSupport(
                "exact",
                "google_adk.llm_agent.output_schema",
                expected_event_types=("output.accepted", "output.schema_failed"),
            )
        return MappingSupport(
            "emulated",
            "google_adk.terminal_output_schema_validation",
            expected_event_types=("output.accepted", "output.schema_failed"),
        )


_MAPPING_RESOLVER = GoogleADKMappingResolver()


def google_adk_target_binding_validator(
    target_name: str,
    section: BindingSection,
    name: str,
    entry: BindingEntry,
) -> tuple[Diagnostic, ...]:
    """Validate Google ADK locator shapes without importing the optional SDK."""

    description = describe_locator(entry.values)
    label = f"targets.{target_name}.{section}.{name}"
    if description.has_mixed_families:
        return (
            Diagnostic(
                "TGT110",
                f"Google ADK binding `{label}` has ambiguous implementation locators",
                hint="Select exactly one implementation locator family.",
            ),
        )
    python_binding = description.is_python_binding
    search_binding = (
        section == "tools"
        and _is_google_search_locator("tool", entry.values)
    )
    if python_binding or search_binding:
        return ()
    return (
        Diagnostic(
            "TGT111",
            f"Google ADK binding `{label}` uses an unsupported implementation locator",
            hint=(
                "Use a `python = \"module:function\"` locator, or bind the "
                "schema-compatible Google Search tool with exactly "
                '`provider = "google_adk"`, `tool = "google_search"`, and an '
                'explicit `model = "gemini-2..."`.'
            ),
        ),
    )


def google_adk_target_profile_validator(
    ir: CanonicalIR,
    target_name: str,
    target: TargetBinding,
    project_root: Path,
) -> tuple[Diagnostic, ...]:
    """Require native ADK profiles to use Gemini unless a factory is configured."""

    del project_root
    diagnostics: list[Diagnostic] = []
    for profile_name, profile in target.profiles.items():
        inherited_factory = "model_factory" in profile.options
        for agent in ir.agents.values():
            agent_profile = profile.agents.get(agent.name)
            has_factory = inherited_factory or (
                agent_profile is not None
                and "model_factory" in agent_profile.options
            )
            if has_factory:
                continue
            model = (
                agent_profile.model
                if agent_profile is not None and agent_profile.model is not None
                else profile.default_model
            )
            if model is None or model.startswith("gemini-"):
                continue
            diagnostics.append(
                Diagnostic(
                    "TGT116",
                    (
                        f"Google ADK target `{target_name}` profile `{profile_name}` "
                        f"selects non-Gemini model `{model}` for agent `{agent.name}`"
                    ),
                    hint=(
                        "Use a `gemini-*` model identifier or configure a validated "
                        "`model_factory = \"module:callable\"`."
                    ),
                )
            )
    return tuple(diagnostics)


def google_adk_planner_capabilities() -> PlannerCapabilities:
    """Return mappings implemented by the Google ADK materializer."""

    return PlannerCapabilities.create(
        adapter="google_adk",
        version="1",
        approval=MappingSupport(
            "exact",
            "google_adk.tool_context.request_confirmation",
            expected_event_types=(
                "approval.requested",
                "approval.completed",
                "tool.started",
            ),
        ),
        composition={
            "delegate": MappingSupport(
                "emulated",
                "google_adk.tool_context.run_node.typed_sub_branch",
                expected_event_types=(
                    "composition.started",
                    "composition.completed",
                ),
                host_obligation=(
                    "Verify model-supplied delegate values against declared source "
                    "mappings when source-value equality is required."
                ),
            ),
            "delegate:none": MappingSupport(
                "emulated",
                "google_adk.tool_context.run_node.typed_sub_branch",
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
            "handoff": MappingSupport("unsupported", None),
            "handoff:none": MappingSupport("unsupported", None),
            "handoff:summary": MappingSupport("unsupported", None),
            "handoff:full": MappingSupport("unsupported", None),
        },
        controls={
            "adapter": MappingSupport(
                "emulated",
                "google_adk.terminal_output_schema_validation",
                expected_event_types=("output.accepted", "output.schema_failed"),
            ),
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


def _is_google_search_locator(
    kind: BindingKind,
    locator: Mapping[str, object],
) -> bool:
    return (
        kind == "tool"
        and set(locator) == _GOOGLE_SEARCH_KEYS
        and locator.get("provider") == "google_adk"
        and locator.get("tool") == "google_search"
        and isinstance(locator.get("model"), str)
        and str(locator["model"]).startswith("gemini-2")
    )


def _is_google_search_capability(
    ir: CanonicalIR,
    capability: CapabilityIR,
) -> bool:
    if capability.kind != "tool" or capability.side_effect is not False:
        return False
    if len(capability.parameters) != 1:
        return False
    parameter = capability.parameters[0]
    if (
        parameter.name != "query"
        or not parameter.required
        or not isinstance(parameter.type_ref, PrimitiveTypeRef)
        or parameter.type_ref.name != "string"
    ):
        return False
    output = _named_object(ir, capability.output_type)
    if output is None or [field.name for field in output.fields] != ["results"]:
        return False
    results = output.fields[0].type_ref
    if not isinstance(results, ListTypeRef):
        return False
    item = _named_object(ir, results.item)
    if item is None or {field.name for field in item.fields} != {
        "title",
        "url",
        "snippet",
    }:
        return False
    return all(
        isinstance(field.type_ref, PrimitiveTypeRef)
        and field.type_ref.name == "string"
        for field in item.fields
    )


def _named_object(ir: CanonicalIR, type_ref: object) -> TypeIR | None:
    if not isinstance(type_ref, NamedTypeRef):
        return None
    declaration = ir.types.get(type_ref.type_id)
    return declaration if isinstance(declaration, TypeIR) else None


__all__ = [
    "GoogleADKMappingResolver",
    "google_adk_planner_capabilities",
    "google_adk_target_binding_validator",
    "google_adk_target_profile_validator",
]
