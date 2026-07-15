"""Honest provider capability descriptor for OpenAI planning."""

from __future__ import annotations

from contract4agents.planning import MappingSupport, PlannerCapabilities, in_process_isolation_support


def openai_planner_capabilities() -> PlannerCapabilities:
    """Return mappings implemented by the OpenAI Agents SDK materializer."""

    return PlannerCapabilities.create(
        adapter="openai",
        version="1",
        approval=MappingSupport(
            "exact",
            "openai.function_tool.needs_approval",
            expected_telemetry=("approval.requested", "approval.completed", "tool.started"),
        ),
        composition={
            "delegate": MappingSupport(
                "emulated",
                "openai.agent_as_tool.model_supplied_typed_input",
                expected_telemetry=("composition.started", "composition.completed"),
                host_obligation=(
                    "Verify model-supplied delegate values against declared source mappings when "
                    "source-value equality is required."
                ),
            ),
            "delegate:none": MappingSupport(
                "emulated",
                "openai.agent_as_tool.model_supplied_typed_input",
                expected_telemetry=("composition.started", "composition.completed"),
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
                expected_telemetry=("handoff.started", "handoff.completed"),
                host_obligation="Verify handoff input transfer against the declared mappings.",
            ),
            "handoff:none": MappingSupport(
                "emulated",
                "openai.handoff.input_filter",
                expected_telemetry=("handoff.started", "handoff.completed"),
                host_obligation="Supply and verify declared handoff inputs outside conversation history.",
            ),
            "handoff:summary": MappingSupport("unsupported", None),
            "handoff:full": MappingSupport(
                "emulated",
                "openai.handoff.full_history.model_supplied_transfer",
                expected_telemetry=("handoff.started", "handoff.completed"),
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
        expected_telemetry=("agent.started", "agent.completed", "output.accepted"),
    )


__all__ = ["openai_planner_capabilities"]
