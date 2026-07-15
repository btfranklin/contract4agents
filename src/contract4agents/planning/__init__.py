"""Provider-neutral target planning for Contract4Agents."""

from contract4agents.planning._errors import PlanningError, PlanningIssue
from contract4agents.planning._models import (
    PLAN_VERSION,
    AdapterPlan,
    AgentPlan,
    BindingKind,
    BindingPlan,
    CompositionMappingPlan,
    ControlMappingPlan,
    GrantMappingPlan,
    HostObligationPlan,
    IsolationDimension,
    IsolationDimensionPlan,
    IsolationMappingPlan,
    MappingOutcome,
    MappingSupport,
    MaterializationPlan,
    PlannerCapabilities,
    in_process_isolation_support,
)
from contract4agents.planning._planner import plan_materialization
from contract4agents.planning._serialization import (
    PlanJsonValue,
    canonical_materialization_plan_json,
    compute_plan_digest,
    materialization_plan_data,
)

__all__ = [
    "PLAN_VERSION",
    "AdapterPlan",
    "AgentPlan",
    "BindingKind",
    "BindingPlan",
    "CompositionMappingPlan",
    "ControlMappingPlan",
    "GrantMappingPlan",
    "HostObligationPlan",
    "IsolationDimension",
    "IsolationDimensionPlan",
    "IsolationMappingPlan",
    "MappingOutcome",
    "MappingSupport",
    "MaterializationPlan",
    "PlanJsonValue",
    "PlannerCapabilities",
    "PlanningError",
    "PlanningIssue",
    "canonical_materialization_plan_json",
    "compute_plan_digest",
    "in_process_isolation_support",
    "materialization_plan_data",
    "plan_materialization",
]
