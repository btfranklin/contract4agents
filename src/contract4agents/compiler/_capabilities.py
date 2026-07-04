"""Adapter capability matrix aggregation."""

from __future__ import annotations

from contract4agents.adapter_capabilities import openai_adapter_capabilities
from contract4agents.compiler._types import CapabilityMatrix


def adapter_capability_matrix() -> CapabilityMatrix:
    return openai_adapter_capabilities()


__all__ = ["adapter_capability_matrix"]
