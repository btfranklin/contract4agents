"""Target adapter capability descriptors."""

from contract4agents.adapters._openai import openai_planner_capabilities
from contract4agents.adapters._openai_names import contract_tool_name, openai_tool_name

__all__ = ["contract_tool_name", "openai_planner_capabilities", "openai_tool_name"]
