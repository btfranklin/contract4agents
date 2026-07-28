"""Target adapter capability descriptors."""

from contract4agents.adapters._google_adk import (
    google_adk_planner_capabilities,
    google_adk_target_binding_validator,
    google_adk_target_profile_validator,
)
from contract4agents.adapters._openai import (
    openai_planner_capabilities,
    openai_target_binding_validator,
    openai_target_profile_validator,
)
from contract4agents.adapters._openai_names import contract_tool_name, openai_tool_name
from contract4agents.adapters._strands import (
    strands_planner_capabilities,
    strands_target_binding_validator,
    strands_target_profile_validator,
)

__all__ = [
    "contract_tool_name",
    "google_adk_planner_capabilities",
    "google_adk_target_binding_validator",
    "google_adk_target_profile_validator",
    "openai_planner_capabilities",
    "openai_target_binding_validator",
    "openai_target_profile_validator",
    "openai_tool_name",
    "strands_planner_capabilities",
    "strands_target_binding_validator",
    "strands_target_profile_validator",
]
