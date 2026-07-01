"""OpenAI Agents SDK adapter.

The adapter is intentionally thin: Contract4Agents compiles to provider-neutral
manifests first, and this module projects those manifests onto OpenAI's SDK.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from contract4agents.compiler import AgentManifest, CompilerArtifacts
from contract4agents.runtime import TraceRecorder

_RunHooksBase: type[Any]
try:
    from agents import RunHooks as _ImportedRunHooks

    _RunHooksBase = _ImportedRunHooks
except Exception:  # noqa: BLE001 - optional adapter import boundary.
    _RunHooksBase = object


@dataclass(frozen=True)
class OpenAIAdapterResult:
    final_output: Any
    last_agent: str | None
    raw_result: Any


@dataclass(frozen=True)
class OpenAIAgentFactoryCaveat:
    agent: str
    kind: str
    message: str


@dataclass(frozen=True)
class OpenAIAgentFactoryResult:
    agents: dict[str, Any]
    caveats: list[OpenAIAgentFactoryCaveat]


class OpenAIAdapterUnavailable(RuntimeError):
    pass


class OpenAIAgentFactoryError(ValueError):
    pass


def openai_tool_name(contract_name: str) -> str:
    """Convert a Contract4Agents capability name into an OpenAI-safe tool name."""
    return contract_name.replace(".", "__")


def contract_tool_name(openai_name: str) -> str:
    """Convert a generated OpenAI tool name back into the Contract4Agents capability name."""
    return openai_name.replace("__", ".")


class OpenAITraceHooks(_RunHooksBase):  # type: ignore[misc]
    """Minimal hook object that normalizes Agents SDK lifecycle events to Contract4Agents traces."""

    def __init__(self, trace: TraceRecorder) -> None:
        super().__init__()
        self.trace = trace

    async def on_agent_start(self, _context: Any, agent: Any) -> None:
        self.trace.record("agent.started", agent=getattr(agent, "name", str(agent)))

    async def on_agent_end(self, _context: Any, agent: Any, output: Any) -> None:
        self.trace.record("agent.completed", agent=getattr(agent, "name", str(agent)), output=_serializable(output))

    async def on_handoff(self, _context: Any, from_agent: Any, to_agent: Any) -> None:
        self.trace.record(
            "agent.handoff",
            from_agent=getattr(from_agent, "name", str(from_agent)),
            to_agent=getattr(to_agent, "name", str(to_agent)),
        )

    async def on_tool_start(self, _context: Any, agent: Any, tool: Any) -> None:
        self.trace.record(
            "tool.started",
            agent=getattr(agent, "name", str(agent)),
            tool=contract_tool_name(getattr(tool, "name", str(tool))),
        )

    async def on_tool_end(self, _context: Any, agent: Any, tool: Any, result: str) -> None:
        self.trace.record(
            "tool.completed",
            agent=getattr(agent, "name", str(agent)),
            tool=contract_tool_name(getattr(tool, "name", str(tool))),
            result=_serializable(result),
        )

    async def on_llm_start(self, _context: Any, agent: Any, _system_prompt: str | None, input_items: list[Any]) -> None:
        self.trace.record("llm.started", agent=getattr(agent, "name", str(agent)), input_count=len(input_items))

    async def on_llm_end(self, _context: Any, agent: Any, _response: Any) -> None:
        self.trace.record("llm.completed", agent=getattr(agent, "name", str(agent)))


def build_openai_agent(
    manifest: AgentManifest,
    instructions: str,
    tools: list[Any] | None = None,
    handoffs: list[Any] | None = None,
    output_type: Any | None = None,
    hooks: Any | None = None,
    input_guardrails: list[Any] | None = None,
) -> Any:
    try:
        from agents import Agent
    except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
        raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
    kwargs: dict[str, Any] = {
        "name": manifest["agent"],
        "instructions": instructions,
        "model": manifest.get("model", "gpt-5.5"),
        "tools": tools or [],
        "handoffs": handoffs or [],
    }
    if output_type is not None:
        kwargs["output_type"] = output_type
    if hooks is not None:
        kwargs["hooks"] = hooks
    if input_guardrails is not None:
        kwargs["input_guardrails"] = input_guardrails
    return Agent(**kwargs)


def build_openai_agents_from_contracts(
    artifacts: CompilerArtifacts,
    *,
    output_type_registry: Mapping[str, Any],
    model_registry: Mapping[str, Any],
    tool_registry: Mapping[str, Any] | None = None,
    agent_tool_registry: Mapping[str, Any] | None = None,
    handoff_registry: Mapping[str, Any] | None = None,
    instruction_overrides: Mapping[str, str] | None = None,
    default_model: Any | None = None,
) -> OpenAIAgentFactoryResult:
    """Build OpenAI Agents SDK objects from compiled artifacts plus explicit registries."""
    agents: dict[str, Any] = {}
    caveats: list[OpenAIAgentFactoryCaveat] = []
    for agent_name, manifest in artifacts["manifests"].items():
        model = model_registry.get(agent_name, default_model)
        if model is None:
            raise OpenAIAgentFactoryError(f"No model configured for agent `{agent_name}`")
        output_type_name = manifest["output"]["type"]
        if output_type_name not in output_type_registry:
            raise OpenAIAgentFactoryError(
                f"No output type registered for `{output_type_name}` used by agent `{agent_name}`"
            )
        tools = _registered_tools(agent_name, manifest, tool_registry)
        handoffs: list[Any] = []
        for dependency in manifest["agents"]:
            child = dependency["name"]
            wired = False
            if agent_tool_registry and child in agent_tool_registry:
                tools.append(agent_tool_registry[child])
                wired = True
            if handoff_registry and child in handoff_registry:
                handoffs.append(handoff_registry[child])
                wired = True
            if not wired:
                caveats.append(
                    OpenAIAgentFactoryCaveat(
                        agent_name,
                        "agent_dependency_unwired",
                        f"Declared agent dependency `{child}` was not supplied as an agent tool or handoff.",
                    )
                )
        manifest_with_model = dict(manifest)
        manifest_with_model["model"] = model
        agents[agent_name] = build_openai_agent(
            cast(AgentManifest, manifest_with_model),
            _instructions_for(agent_name, artifacts, instruction_overrides),
            tools=tools,
            handoffs=handoffs,
            output_type=output_type_registry[output_type_name],
        )
    return OpenAIAgentFactoryResult(agents, caveats)


async def run_openai_agent(
    agent: Any,
    user_input: str,
    *,
    context: Any | None = None,
    max_turns: int | None = 10,
    hooks: Any | None = None,
) -> OpenAIAdapterResult:
    try:
        from agents import Runner
    except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
        raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
    result = await Runner.run(agent, user_input, context=context, max_turns=max_turns, hooks=hooks)
    last_agent = getattr(getattr(result, "last_agent", None), "name", None)
    return OpenAIAdapterResult(getattr(result, "final_output", None), last_agent, result)


class OpenAISemanticJudge:
    def __init__(self, model: str = "gpt-5.5", api_key_env: str = "OPENAI_API_KEY") -> None:
        self.model = model
        self.api_key_env = api_key_env

    async def judge(self, *, output: dict[str, Any], criterion: str) -> bool:
        if not os.getenv(self.api_key_env):
            raise OpenAIAdapterUnavailable(f"{self.api_key_env} is not set")
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
            raise OpenAIAdapterUnavailable("openai package is not installed") from exc
        client = AsyncOpenAI()
        response = await client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": "Return only PASS or FAIL. Evaluate whether the output satisfies the criterion.",
                },
                {
                    "role": "user",
                    "content": f"Criterion: {criterion}\nOutput: {output}",
                },
            ],
        )
        text = getattr(response, "output_text", "")
        return str(text).strip().upper() == "PASS"


def _serializable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)


def _registered_tools(
    agent_name: str,
    manifest: AgentManifest,
    tool_registry: Mapping[str, Any] | None,
) -> list[Any]:
    tools: list[Any] = []
    for tool in manifest["tools"]:
        name = tool["name"]
        if not tool_registry or name not in tool_registry:
            raise OpenAIAgentFactoryError(f"No host tool registered for `{name}` used by agent `{agent_name}`")
        tools.append(tool_registry[name])
    return tools


def _instructions_for(
    agent_name: str,
    artifacts: CompilerArtifacts,
    instruction_overrides: Mapping[str, str] | None,
) -> str:
    if instruction_overrides and agent_name in instruction_overrides:
        return instruction_overrides[agent_name]
    try:
        return artifacts["instructions"][agent_name]
    except KeyError as exc:
        raise OpenAIAgentFactoryError(f"No instructions compiled for agent `{agent_name}`") from exc
