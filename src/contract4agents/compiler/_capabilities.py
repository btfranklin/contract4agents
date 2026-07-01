"""Adapter capability matrix generation."""

from __future__ import annotations

from contract4agents.compiler._types import CapabilityEntry, CapabilityMatrix, CapabilityStatus


def adapter_capability_matrix() -> CapabilityMatrix:
    return {
        "openai": {
            "instructions": _capability("supported"),
            "tools": _capability("partial", "Host code supplies SDK function tools from manifest capabilities."),
            "hosted_tools": _capability(
                "partial", "Host code enables provider-native hosted tools through explicit adapter registries."
            ),
            "output_schema": _capability(
                "partial", "Host code supplies SDK output types or uses adapter-generated Pydantic models."
            ),
            "context": _capability(
                "partial",
                "Contract4Agents resolves context; the OpenAI run helper renders non-sensitive runtime context.",
            ),
            "handoff": _capability("partial", "Host code supplies SDK handoff objects when used."),
            "agent_as_tool": _capability("emulated", "Host code wraps child agents as SDK tools."),
            "isolated_subagent": _capability(
                "unsupported",
                "OpenAI adapter planning reports isolated subagent composition as unsupported because it cannot "
                "safely map isolated child context semantics to SDK objects.",
            ),
            "trace_capture": _capability(
                "partial", "SDK hooks emit normalized lifecycle events; host tools own custom data."
            ),
            "approval_gates": _capability(
                "partial", "The OpenAI run helper resolves SDK approval interruptions through host callbacks."
            ),
            "guards": _capability(
                "partial",
                "Guard plan classifies output conformance, denied tools, and approval-required tools; "
                "host code enforces approvals.",
            ),
            "semantic_judge": _capability("supported"),
        },
    }


def _capability(status: CapabilityStatus, *caveats: str) -> CapabilityEntry:
    return {"status": status, "caveats": list(caveats)}


__all__ = ["adapter_capability_matrix"]
