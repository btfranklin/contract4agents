# Agent SDK Pattern Survey

This survey captures common agent-definition patterns across four major SDKs:

- OpenAI Agents SDK
- Google Agent Development Kit
- Anthropic Claude Agent SDK
- Amazon Strands Agents SDK

The goal is not to copy any one SDK. The goal is to make Contract4Agents' source language and compiler target the stable concepts that show up across them.

Survey date: May 15, 2026.

## Sources

- OpenAI Agents SDK guide: https://developers.openai.com/api/docs/guides/agents
- OpenAI Agents SDK JavaScript agent definitions: https://openai.github.io/openai-agents-js/guides/agents/
- OpenAI Agents SDK Python `Agent`: https://openai.github.io/openai-agents-python/ref/agent/
- Google ADK overview: https://adk.dev/get-started/about/
- Google ADK agent categories: https://adk.dev/agents/
- Google ADK LLM agents: https://adk.dev/agents/llm-agents/
- Google ADK agent config: https://adk.dev/agents/config/
- Google ADK callbacks: https://adk.dev/callbacks/types-of-callbacks/
- Claude Agent SDK overview: https://code.claude.com/docs/en/agent-sdk/overview
- Claude Agent SDK loop: https://code.claude.com/docs/en/agent-sdk/agent-loop
- Claude Agent SDK Python reference: https://platform.claude.com/docs/en/agent-sdk/python
- Claude Agent SDK permissions: https://code.claude.com/docs/en/agent-sdk/permissions
- Claude Agent SDK subagents: https://code.claude.com/docs/en/agent-sdk/subagents
- Claude Agent SDK MCP: https://code.claude.com/docs/en/agent-sdk/mcp
- Strands core concepts: https://aws.amazon.com/blogs/opensource/introducing-strands-agents-an-open-source-ai-agents-sdk/
- Strands prompts: https://strandsagents.com/docs/user-guide/concepts/agents/prompts/
- Strands tools: https://strandsagents.com/docs/user-guide/concepts/tools/
- Strands structured output: https://strandsagents.com/docs/user-guide/concepts/agents/structured-output/
- Strands multi-agent patterns: https://strandsagents.com/docs/user-guide/concepts/multi-agent/multi-agent-patterns/
- AWS Prescriptive Guidance for Strands: https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-frameworks/strands-agents.html

## OpenAI Agents SDK

OpenAI's SDK defines agents as code objects with the usual agent-contract parts:

- `name`
- `instructions`
- `model`
- `model_settings`
- `tools`
- `handoffs`
- `outputType` or equivalent structured output
- input and output guardrails
- run-time context
- tracing, results, state, and human-review surfaces

Important patterns for Contract4Agents:

- Context is explicit dependency injection. In the JavaScript docs, an agent is generic over a context type and that context is passed to the runner, then forwarded to tools, guardrails, handoffs, and related runtime surfaces.
- Structured output accepts Zod or JSON-Schema-compatible objects in JavaScript. The Python SDK has typed output support as well.
- Composition has two first-class patterns: manager agents that use other agents as tools, and handoffs where control moves to a specialist.
- OpenAI's docs distinguish when application code owns control flow, tool execution, approvals, and state.

Contract4Agents implication:

- Contract4Agents should compile cleanly to `instructions`, `tools`, `handoffs`, `output schema`, `guardrails`, and `context` for OpenAI.
- Contract4Agents should distinguish `agent as tool` from `handoff` or `transfer`, because OpenAI treats those as different composition modes.

## Google ADK

Google ADK separates agent categories:

- `LlmAgent` or `Agent` for non-deterministic model reasoning, tool use, and dynamic decisions.
- Composition agents such as `SequentialAgent`, `ParallelAgent`, and `LoopAgent` for structured flow inside ADK.
- Custom agents by extending `BaseAgent`.

The LLM agent definition commonly includes:

- `name`
- `model`
- `description`
- `instruction`
- `tools`
- `generate_content_config`
- `input_schema`
- `output_schema`
- `output_key`
- `sub_agents`
- callback hooks

Important patterns for Contract4Agents:

- ADK makes `description` semantically important because it tells other agents when to delegate.
- Tools can be ordinary functions, tool classes, or other agents wrapped as tools.
- ADK has explicit input and output schemas, but its docs warn that combining `output_schema` with tools is model-dependent and may need a formatter subagent on models that do not support that combination.
- `output_key` writes the final agent response into session state, which is a useful pattern for multi-step flows.
- Callbacks are named intervention points around agent, model, and tool execution.
- Agent Config YAML is a data representation for agents, but Contract4Agents should not copy YAML as a source language.

Contract4Agents implication:

- Contract4Agents should preserve model-driven delegation metadata without copying ADK's execution model.
- Contract4Agents should have an adapter warning system: a valid Contract4Agents may compile cleanly but have target-specific caveats, such as ADK schema-plus-tool limitations.
- `description` should be elevated from optional prose to a first-class field for agent discovery and delegation.

## Anthropic Claude Agent SDK

Claude Agent SDK is shaped around embedding Claude Code's autonomous loop. The primary surface is a `query(prompt, options)` or client session rather than a simple persistent `Agent` object.

Common definition fields and options include:

- prompt
- `system_prompt`
- `tools` preset
- `allowed_tools`
- `disallowed_tools`
- `permission_mode`
- MCP server configuration
- programmatic subagents via `agents`
- subagent `description`
- subagent `prompt`
- subagent `tools`
- subagent model selection
- hooks and permission callbacks
- JSON Schema `output_format`
- `max_turns`
- sessions, resume, working directory, sandbox, and settings sources

Important patterns for Contract4Agents:

- The agent loop is explicit: prompt plus system prompt plus tools plus history, repeated tool calls, and final result.
- Permissions are not the same as tool listing. `allowed_tools` auto-approves listed tools but does not restrict the agent to only those tools unless combined with an appropriate permission mode or deny rules.
- Subagents isolate context. The parent receives the subagent's final response rather than every intermediate tool call.
- MCP tools have explicit naming and permission patterns.
- The SDK exposes streaming message events, tool-use blocks, result messages, and session IDs.

Contract4Agents implication:

- Contract4Agents must separate "available", "allowed", "pre-approved", "denied", and "requires approval." A single `tools = [...]` list is not precise enough.
- Contract4Agents' trace schema needs to represent tool calls, tool results, approvals, final output, and isolated subagent traces.
- Contract4Agents should model subagent context isolation separately from manager-style agent calls.

## Amazon Strands Agents SDK

Strands presents an agent as a model-driven combination of:

- model
- tools
- prompt or system prompt

Common definition surfaces include:

- `Agent(...)`
- `model`
- `system_prompt`
- `tools`
- `structured_output_model` or structured output schema
- invocation state
- session state
- direct tool calls through `agent.tool`
- multi-agent primitives

Important patterns for Contract4Agents:

- Strands strongly emphasizes the model-driven loop: the model chooses tools and steps dynamically from prompt, context, and tool descriptions.
- Tools can be added during initialization, loaded from Python modules, or called directly.
- Tool auto-loading is powerful but risky because files in a tools directory execute as code.
- Structured output uses Pydantic in Python and schema-style objects in TypeScript.
- Multi-agent patterns include agents-as-tools and other SDK-specific composition helpers.
- Shared state can be passed through invocation state without exposing it to the model.

Contract4Agents implication:

- Contract4Agents should represent hidden application state separately from rendered model context.
- Contract4Agents should make direct tool calls and model-selected tool calls distinct trace events.
- Contract4Agents should preserve composition intent while leaving execution mechanics to adapters and host code.

## Common Agent Definition Pattern

Across all four SDKs, a production agent definition tends to contain these parts:

| Pattern | OpenAI | Google ADK | Claude Agent SDK | Strands |
| --- | --- | --- | --- | --- |
| Identity | `name` | `name`, `description` | subagent name, description | agent name/description where used |
| Model | `model`, settings | `model`, generation config | model options, subagent model | model provider |
| Instructions | `instructions` | `instruction`, global instruction | prompt, system prompt | system prompt, user prompt |
| Tools | function tools, built-in tools, MCP | functions, tool classes, agent tools | built-ins, MCP, custom tools | Python tools, MCP, modules |
| Subagents | handoffs, agents as tools | sub_agents, AgentTool | programmatic/file subagents | agents-as-tools, composition helpers |
| Context/state | runner context | session state, output_key | session/history/settings/cwd | invocation state, session state |
| Output contract | output type/schema | input/output schema | JSON Schema output format | Pydantic/Zod structured output |
| Guardrails | input/output guardrails, approvals | callbacks | permissions, hooks, deny rules | hooks, tool policy, app controls |
| Runtime loop | runner | runner/session/event model | Claude Code loop | Strands event loop |
| Trace/results | traces/results/state | events/callbacks/state | streaming messages/result/cost | result, metrics, trace output |

## Contract4Agents Design Requirements From The Survey

Contract4Agents should make these concepts first-class:

- `agent` identity: `name`, `description`, and callable signature.
- Model intent: provider-neutral model choice and model settings, with adapter-specific overrides.
- Instructions: generated from goal, policy, success criteria, routes, guards, and output contract.
- Typed context slots: values may be rendered to the model or kept hidden in runtime state.
- Capabilities: tools, MCP tools, code executors, built-in tools, datasources, and agents.
- Permission semantics: available, allowed, denied, approval-required, pre-approved, and sandboxed.
- Output contracts: JSON Schema as canonical interchange, with Pydantic/Zod generated or accepted at adapter boundaries.
- Composition semantics: agent-as-tool, handoff, and isolated subagent.
- Hooks and guards: before/after agent, model, tool, datasource, and output-validation intervention points.
- Trace contract: normalized trace events across tool calls, subagent calls, approvals, and final output.
- Eval contract: deterministic output checks, trace spies, semantic judgments, and monitor rules.

## Adapter Strategy

Contract4Agents should not choose one SDK's object model as its internal model. Instead:

1. Compile `.contract` files into a provider-neutral manifest.
2. Compile the manifest into adapter-specific code or configuration.
3. Run adapter conformance tests against the same demo teams.
4. Record target-specific warnings when an adapter cannot faithfully represent a Contract4Agents feature.

Recommended first adapter order:

1. OpenAI Agents SDK: best fit for the current Contract4Agents mental model because it already names agents, instructions, context, tools, handoffs, guardrails, output schemas, results, and traces.
2. Google ADK: important second target because it has config loading, session state, callbacks, and multi-language pressure.
3. Strands: important for model-neutral and multi-agent pattern breadth.
4. Claude Agent SDK: important for permissions, coding-agent loop semantics, MCP, subagent isolation, and session controls, but its surface is less like a simple agent class.

This is a recommended implementation order, not a language dependency. Contract4Agents should preserve all common patterns from the beginning.
