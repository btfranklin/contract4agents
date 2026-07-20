"""Contextual language help and completion metadata."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.language_service._model import SymbolKind
from contract4agents.language_spec import (
    ASSESSMENTS,
    AUDIENCES,
    AUTHORIZATIONS,
    AVAILABILITIES,
    BOOLEAN_VALUES,
    BUILTIN_EXECUTION_BOUNDARIES,
    CACHE_SCOPES,
    COMPOSITION_MODES,
    HISTORY_MODES,
    ISOLATION_DIMENSIONS,
    RENDER_MODES,
    SENSITIVITIES,
    SEVERITIES,
)


@dataclass(frozen=True)
class PropertySpec:
    context: str
    name: str
    description: str
    values: tuple[str, ...] = ()
    reference_kind: SymbolKind | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.context, self.name)


def _property(
    context: str,
    name: str,
    description: str,
    values: tuple[str, ...] = (),
    reference_kind: SymbolKind | None = None,
) -> PropertySpec:
    return PropertySpec(context, name, description, values, reference_kind)


_PROPERTIES = (
    _property("grant", "availability", "Whether this agent may use the capability.", AVAILABILITIES),
    _property(
        "grant",
        "authorization",
        "Whether invocation requires an explicit runtime approval.",
        AUTHORIZATIONS,
    ),
    _property(
        "grant",
        "execution",
        "The boundary responsible for executing the capability.",
        BUILTIN_EXECUTION_BOUNDARIES,
    ),
    _property(
        "grant",
        "isolation",
        "A named isolation policy applied to this capability grant.",
        reference_kind="isolation",
    ),
    _property("agent", "description", "A human-readable description of the agent contract."),
    _property("agent", "goal", "The outcome the agent is contractually expected to pursue."),
    _property("agent", "guidance", "Portable behavioral guidance supplied to the agent."),
    _property("tool", "description", "A human-readable description of the tool interface."),
    _property("tool", "side_effect", "Whether invoking the tool can modify external state.", BOOLEAN_VALUES),
    _property("datasource", "description", "A human-readable description of the datasource."),
    _property("datasource", "render", "How resolved datasource content is rendered for the agent.", RENDER_MODES),
    _property("datasource", "cache", "The lifetime for which a datasource result may be reused.", CACHE_SCOPES),
    _property("external_context", "description", "A human-readable description of host-supplied context."),
    _property(
        "external_context",
        "sensitivity",
        "The sensitivity classification of the external context.",
        SENSITIVITIES,
    ),
    _property("external_context", "render", "How external context is rendered for the agent.", RENDER_MODES),
    _property(
        "composition",
        "mode",
        "Whether the edge delegates work or transfers control through a handoff.",
        COMPOSITION_MODES,
    ),
    _property(
        "composition",
        "history",
        "The conversation history made available across the composition edge.",
        HISTORY_MODES,
    ),
    _property(
        "composition",
        "isolation",
        "A named isolation policy applied to the composed worker.",
        reference_kind="isolation",
    ),
    _property("control", "severity", "The consequence level assigned to a control failure.", SEVERITIES),
    _property("control", "required", "Whether the control must be satisfied for assurance to pass.", BOOLEAN_VALUES),
    _property(
        "control",
        "audience",
        "The components responsible for observing or enforcing the control.",
        AUDIENCES,
    ),
    _property("control", "assessment", "The phase or mechanism that assesses the control.", ASSESSMENTS),
    _property("control", "require", "A fail-closed expression that must be observed as true."),
    _property("quality", "rubric", "The rubric used to assess this quality requirement."),
    _property(
        "quality",
        "audience",
        "The components responsible for assessing this quality requirement.",
        AUDIENCES,
    ),
    _property(
        "operational_control",
        "severity",
        "The consequence level assigned to an operational-control failure.",
        SEVERITIES,
    ),
    _property("operational_control", "require", "A measurable operational condition that must hold."),
    _property("run_spec", "stages", "Host-owned workflow stages whose observed behavior will be verified."),
    _property("run_spec", "assertions", "Trace or data-relation assertions applied to the run."),
    _property("run_spec", "derived_values", "Named host-supplied values available to run assertions."),
    *(
        _property("isolation", name, f"The `{name}` isolation dimension.", values)
        for name, values in ISOLATION_DIMENSIONS.items()
    ),
)

PROPERTY_SPECS = {item.key: item for item in _PROPERTIES}
CONTEXT_PROPERTIES = {
    context: tuple(item.name for item in _PROPERTIES if item.context == context)
    for context in {item.context for item in _PROPERTIES}
}

VALUE_DOCS = {
    "enabled": "The agent is permitted to use this capability when authorization and execution are also valid.",
    "denied": "The capability is explicitly unavailable. Authorization, execution, and isolation must be omitted.",
    "preapproved": "The agent may invoke the capability without pausing for runtime approval.",
    "approval_required": "Each invocation requires an explicit approval before execution.",
    "host": "The host application executes the capability.",
    "provider_hosted": "The selected provider executes the capability on its hosted infrastructure.",
    "remote": "A remote system outside the host process executes the capability.",
    "delegate": "The source agent delegates a task and later receives the worker's result.",
    "none": "No value or access is provided for this dimension.",
    "summary": "Only summarized history is made available.",
    "full": "Full available history is made available.",
    "markdown": "Render the value as Markdown.",
    "json": "Render the value as JSON.",
    "text": "Render the value as plain text.",
    "run": "Cache the value for one run.",
    "thread": "Cache the value for the current thread.",
    "public": "The data is suitable for public disclosure.",
    "internal": "The data is intended for internal use.",
    "confidential": "The data requires confidential handling.",
    "restricted": "The data requires the strongest declared handling restrictions.",
    "true": "This condition is enabled.",
    "false": "This condition is disabled.",
    "invocation": "The value originates in invocation input.",
    "parent": "The value originates from a parent agent.",
    "stage": "The value originates from a host-owned run stage.",
    "datasource": "The value is resolved through a declared datasource.",
    "external": "The value is supplied through a declared external-context interface.",
}

CONTEXTUAL_VALUE_DOCS = {
    ("composition.mode", "handoff"): "Control transfers from the source agent to the target agent.",
    ("context.origin", "handoff"): "The value originates from a handoff.",
}

TOP_LEVEL_KEYWORDS = (
    "type",
    "enum",
    "tool",
    "datasource",
    "external_context",
    "isolation",
    "agent",
    "composition",
    "control",
    "quality",
    "operational_control",
    "eval",
    "run_spec",
)


def property_spec(context: str, name: str) -> PropertySpec | None:
    return PROPERTY_SPECS.get((context, name))


def property_help(context: str, name: str) -> str | None:
    spec = property_spec(context, name)
    return spec.description if spec is not None else None


def property_values(context: str, name: str) -> tuple[str, ...]:
    spec = property_spec(context, name)
    return spec.values if spec is not None else ()


def value_help(context: str | None, value: str) -> str | None:
    if context is not None and (context, value) in CONTEXTUAL_VALUE_DOCS:
        return CONTEXTUAL_VALUE_DOCS[(context, value)]
    return VALUE_DOCS.get(value)


__all__ = [
    "CONTEXT_PROPERTIES",
    "PROPERTY_SPECS",
    "PropertySpec",
    "TOP_LEVEL_KEYWORDS",
    "property_help",
    "property_spec",
    "property_values",
    "value_help",
]
