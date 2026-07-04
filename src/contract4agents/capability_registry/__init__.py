"""Capability registry loading and host-code drift checks."""

from __future__ import annotations

from contract4agents.capability_registry._drift import check_capability_drift
from contract4agents.capability_registry._load import load_capability_registry
from contract4agents.capability_registry._models import (
    DEFAULT_REGISTRY_FILENAME,
    CapabilityRegistry,
    CapabilityRegistryLoad,
)

__all__ = [
    "CapabilityRegistry",
    "CapabilityRegistryLoad",
    "DEFAULT_REGISTRY_FILENAME",
    "check_capability_drift",
    "load_capability_registry",
]
