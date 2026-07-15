"""Target adapter capability descriptors."""

from contract4agents.adapters._openai_names import contract_tool_name, openai_tool_name
from contract4agents.adapters._openai_v2 import openai_planner_capabilities

__all__ = ["contract_tool_name", "openai_planner_capabilities", "openai_tool_name"]
