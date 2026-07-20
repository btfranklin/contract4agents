"""Small, shared vocabulary for the portable contract language.

Grammar owns syntax.  This module owns closed semantic vocabularies that are
used by both validation and interactive editor help.
"""

from __future__ import annotations

AGENT_TEXT_ATTRIBUTES = ("description", "goal")
AGENT_LIST_ATTRIBUTES = ("guidance",)
AGENT_ATTRIBUTES = AGENT_TEXT_ATTRIBUTES + AGENT_LIST_ATTRIBUTES

AVAILABILITIES = ("enabled", "denied")
AUTHORIZATIONS = ("preapproved", "approval_required")
BUILTIN_EXECUTION_BOUNDARIES = ("host", "provider_hosted", "remote")
CONTEXT_ORIGINS = ("invocation", "parent", "handoff", "stage", "datasource", "external")
AGENT_CONTEXT_ORIGINS = ("datasource", "external")

AUDIENCES = ("model", "adapter", "host", "evaluator", "reviewer")
ASSESSMENTS = ("static", "adapter", "runtime", "host_attested", "post_run", "semantic", "advisory")
SEVERITIES = ("low", "medium", "high", "critical")
RENDER_MODES = ("markdown", "json", "text")
CACHE_SCOPES = ("none", "run", "thread")
SENSITIVITIES = ("public", "internal", "confidential", "restricted")
COMPOSITION_MODES = ("delegate", "handoff")
HISTORY_MODES = ("none", "summary", "full")
BOOLEAN_VALUES = ("true", "false")

ISOLATION_DIMENSIONS = {
    "context": ("explicit_only", "inherited"),
    "capabilities": ("declared_only", "inherited"),
    "state": ("fresh", "shared"),
    "filesystem": ("none", "ephemeral", "inherited_read_only", "inherited"),
    "network": ("denied", "allowlisted", "inherited"),
    "secrets": ("none", "declared_only", "inherited"),
    "return": ("final_output_only", "full_trace"),
}

RUN_SPEC_ATTRIBUTES = ("stages", "assertions", "derived_values")
WORKFLOW_LIKE_ATTRIBUTES = ("branch", "branches", "loop", "loops", "retry", "retries", "checkpoint", "recovery")

PORTABLE_SCALAR_TYPES = ("string", "integer", "float", "boolean", "datetime")
PORTABLE_TYPE_COMPLETIONS = PORTABLE_SCALAR_TYPES + ("list[]", "map[string,]")

__all__ = [
    "AGENT_ATTRIBUTES",
    "AGENT_CONTEXT_ORIGINS",
    "AGENT_LIST_ATTRIBUTES",
    "AGENT_TEXT_ATTRIBUTES",
    "ASSESSMENTS",
    "AUDIENCES",
    "AUTHORIZATIONS",
    "AVAILABILITIES",
    "BOOLEAN_VALUES",
    "BUILTIN_EXECUTION_BOUNDARIES",
    "CACHE_SCOPES",
    "COMPOSITION_MODES",
    "CONTEXT_ORIGINS",
    "HISTORY_MODES",
    "ISOLATION_DIMENSIONS",
    "PORTABLE_SCALAR_TYPES",
    "PORTABLE_TYPE_COMPLETIONS",
    "RENDER_MODES",
    "RUN_SPEC_ATTRIBUTES",
    "SENSITIVITIES",
    "SEVERITIES",
    "WORKFLOW_LIKE_ATTRIBUTES",
]
