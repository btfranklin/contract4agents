"""Contract4Agents Python package."""

from contract4agents.assertions import evaluate_agent_assertions, evaluate_run_assertions, evaluate_run_contract
from contract4agents.compiler import compile_project
from contract4agents.guards import build_guard_plan
from contract4agents.parser import parse_file, parse_project
from contract4agents.semantics import analyze_project

__all__ = [
    "analyze_project",
    "build_guard_plan",
    "compile_project",
    "evaluate_agent_assertions",
    "evaluate_run_assertions",
    "evaluate_run_contract",
    "parse_file",
    "parse_project",
]
