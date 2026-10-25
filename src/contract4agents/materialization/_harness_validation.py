"""Configuration and schema evidence for verified Harness handles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from contract4agents.ir import SemanticId
from contract4agents.materialization._configuration import configuration_evidence
from contract4agents.materialization._harness_configuration import HarnessConfiguration
from contract4agents.materialization._harness_invocation import HarnessAgent
from contract4agents.materialization._models import (
    ConfigurationConformanceEvidence,
    ConfigurationObservationSource,
    GraphValidationEvidence,
    SchemaConformanceEvidence,
)


def validate_harness_graph(
    configuration: HarnessConfiguration, agents: Mapping[SemanticId, object]
) -> GraphValidationEvidence:
    plan = configuration.system.plan
    schemas: list[SchemaConformanceEvidence] = []
    facts: list[ConfigurationConformanceEvidence] = []
    for configured in configuration.agents.values():
        identifier = configured.agent_id
        handle = cast(HarnessAgent, agents[identifier])
        observed = handle.deployment.observed_configuration
        for path, planned, actual, source, safe in (
            ("agent.name", configured.request["harnessName"], observed["harnessName"], "native_readback", True),
            ("agent.identity", str(identifier), str(handle.configuration.agent_id), "adapter_boundary", True),
            ("agent.model", configured.request["model"], observed["model"], "native_readback", False),
            (
                "agent.model_options",
                {key: value for key, value in configured.request.items() if key != "tags"},
                observed,
                "native_readback",
                False,
            ),
            (
                "agent.output_type",
                configured.output_adapter.json_schema(),
                handle.configuration.output_adapter.json_schema(),
                "generated_wrapper",
                False,
            ),
            ("agent.output_mode", "host_validation", "host_validation", "adapter_boundary", True),
            ("agent.tools", configured.request["tools"], observed["tools"], "native_readback", False),
            ("agent.handoffs", [], [], "adapter_boundary", True),
        ):
            facts.append(
                configuration_evidence(
                    identifier, path, planned, actual, source=cast(ConfigurationObservationSource, source), safe=safe
                )
            )
        input_schema = (
            configured.input_type.model_json_schema()
            if configured.input_type
            else {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        )
        schemas.extend(
            (
                SchemaConformanceEvidence(identifier, "agent_input", input_schema, input_schema),
                SchemaConformanceEvidence(
                    identifier,
                    "host_structural_output",
                    configured.output_adapter.json_schema(),
                    handle.configuration.output_adapter.json_schema(),
                ),
            )
        )
        for tool in configured.tools.values():
            if tool.grant_id:
                facts.extend(
                    (
                        configuration_evidence(
                            tool.grant_id,
                            "grant.identity",
                            str(tool.semantic_id),
                            str(tool.semantic_id),
                            source="adapter_boundary",
                        ),
                        configuration_evidence(
                            tool.grant_id,
                            "grant.approval",
                            tool.approval_required,
                            tool.approval_required,
                            source="generated_wrapper",
                        ),
                    )
                )
            else:
                facts.extend(
                    (
                        configuration_evidence(
                            tool.semantic_id,
                            "edge.identity",
                            str(tool.child_agent_id),
                            str(tool.child_agent_id),
                            source="adapter_boundary",
                        ),
                        configuration_evidence(
                            tool.semantic_id,
                            "edge.schema",
                            tool.input_schema,
                            tool.input_schema,
                            source="generated_wrapper",
                            safe=False,
                        ),
                    )
                )
            schema_id = tool.grant_id or tool.semantic_id
            boundary_prefix = "tool" if tool.grant_id is not None else "delegate"
            schemas.append(
                SchemaConformanceEvidence(
                    schema_id,
                    f"{boundary_prefix}_input",
                    tool.input_schema,
                    tool.input_schema,
                )
            )
            schemas.append(
                SchemaConformanceEvidence(
                    schema_id,
                    f"{boundary_prefix}_output",
                    tool.output_adapter.json_schema(),
                    tool.output_adapter.json_schema(),
                )
            )
    for grant in configuration.system.ir.grants.values():
        if grant.availability != "enabled":
            facts.extend(
                (
                    configuration_evidence(
                        grant.id,
                        "grant.identity",
                        str(grant.capability_id),
                        str(grant.capability_id),
                        source="adapter_boundary",
                    ),
                    configuration_evidence(grant.id, "grant.approval", False, False, source="adapter_boundary"),
                )
            )
    return GraphValidationEvidence(
        adapter="agentcore_harness",
        adapter_version=plan.adapter.version,
        contract_digest=plan.contract_digest,
        plan_digest=plan.plan_digest,
        agent_ids=tuple(plan.agents),
        grant_ids=tuple(plan.grants),
        composition_ids=tuple(plan.composition),
        schema_conformance=tuple(schemas),
        configuration_conformance=tuple(facts),
    )
