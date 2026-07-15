"""Target-specific implementation bindings for Contract4Agents V2."""

from __future__ import annotations

from contract4agents.target_bindings._conformance import (
    BindingSection,
    ParameterKind,
    ResolvedImplementationIdentity,
    ResolvedParameterIdentity,
    TargetBindingConformanceResult,
    validate_target_binding_conformance,
)
from contract4agents.target_bindings._load import load_target_bindings
from contract4agents.target_bindings._models import (
    DEFAULT_TARGET_BINDINGS_FILENAME,
    TARGET_BINDINGS_SCHEMA_VERSION,
    AgentProfile,
    BindingEntry,
    TargetBinding,
    TargetBindings,
    TargetBindingsLoad,
    TargetProfile,
)
from contract4agents.target_bindings._serialization import (
    canonical_target_bindings_json,
    target_bindings_dict,
)
from contract4agents.target_bindings._validation import validate_target_bindings_payload

__all__ = [
    "AgentProfile",
    "BindingSection",
    "BindingEntry",
    "DEFAULT_TARGET_BINDINGS_FILENAME",
    "ParameterKind",
    "ResolvedImplementationIdentity",
    "ResolvedParameterIdentity",
    "TARGET_BINDINGS_SCHEMA_VERSION",
    "TargetBinding",
    "TargetBindingConformanceResult",
    "TargetBindings",
    "TargetBindingsLoad",
    "TargetProfile",
    "canonical_target_bindings_json",
    "load_target_bindings",
    "target_bindings_dict",
    "validate_target_binding_conformance",
    "validate_target_bindings_payload",
]
