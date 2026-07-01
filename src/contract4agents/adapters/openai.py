"""OpenAI Agents SDK adapter public API."""

from contract4agents.adapters._openai_names import contract_tool_name, openai_tool_name
from contract4agents.adapters._openai_output_types import build_openai_output_type_registry
from contract4agents.adapters._openai_planning import (
    build_openai_agents_from_contracts,
    build_openai_agents_from_plan,
    plan_openai_agents_from_contracts,
)
from contract4agents.adapters._openai_run import run_openai_agent, run_openai_agent_with_contract
from contract4agents.adapters._openai_sdk import build_openai_agent
from contract4agents.adapters._openai_semantic import OpenAISemanticJudge
from contract4agents.adapters._openai_trace import OpenAITraceHooks
from contract4agents.adapters._openai_types import (
    OpenAIAdapterPlan,
    OpenAIAdapterResult,
    OpenAIAdapterUnavailable,
    OpenAIAgentFactoryCaveat,
    OpenAIAgentFactoryError,
    OpenAIAgentFactoryResult,
    OpenAIAgentPlan,
    OpenAIApprovalRequest,
    OpenAICompositionPlan,
    OpenAIContractRunResult,
    OpenAIHostedToolPlan,
    OpenAIToolPlan,
    OpenAIToolRegistration,
)

__all__ = [
    "OpenAIAdapterPlan",
    "OpenAIAdapterResult",
    "OpenAIAdapterUnavailable",
    "OpenAIAgentFactoryCaveat",
    "OpenAIAgentFactoryError",
    "OpenAIAgentFactoryResult",
    "OpenAIAgentPlan",
    "OpenAIApprovalRequest",
    "OpenAICompositionPlan",
    "OpenAIContractRunResult",
    "OpenAIHostedToolPlan",
    "OpenAISemanticJudge",
    "OpenAIToolPlan",
    "OpenAIToolRegistration",
    "OpenAITraceHooks",
    "build_openai_agent",
    "build_openai_agents_from_contracts",
    "build_openai_agents_from_plan",
    "build_openai_output_type_registry",
    "contract_tool_name",
    "openai_tool_name",
    "plan_openai_agents_from_contracts",
    "run_openai_agent",
    "run_openai_agent_with_contract",
]
