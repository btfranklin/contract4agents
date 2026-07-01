# OpenAI Adapter Reference

The OpenAI adapter projects compiled Contract4Agents artifacts onto the OpenAI
Agents SDK. It is plan-first: host code can inspect exactly what will be mapped
before SDK `Agent` objects are constructed.

V1 maps Contract4Agents manifests to:

- OpenAI `Agent` name, model, instructions, and output type.
- Host tools from SDK tool objects or raw Python callables wrapped with
  `agents.function_tool(...)`.
- Hosted provider tools such as `openai.web_search`.
- Agent-as-tool and handoff registrations supplied by the host.
- Guard-plan metadata for output conformance, denied tools, and approval-required tools.
- Runtime context rendering, SDK approval interruption resolution, normalized
  trace hooks, and post-run assertion checks for one supplied SDK agent.

## Planning And Construction

Use `plan_openai_agents_from_contracts(...)` to inspect the mapping:

```python
from contract4agents.adapters.openai import plan_openai_agents_from_contracts

plan = plan_openai_agents_from_contracts(
    artifacts,
    output_type_registry={"SupportReply": SupportReplyModel},
    model_registry={"SupportCoordinator": config.support_model},
    tool_registry={"crm.create_note": crm_create_note},
    hosted_tool_registry={"openai.web_search": True},
    agent_tool_registry={"BillingSpecialist": billing_specialist_tool},
    default_model=config.default_agent_model,
)
```

Each `OpenAIAgentPlan` includes manifest `source_path`, generated
`instruction_ref`, `output_schema_ref`, instructions, model, output type,
host-tool plans, hosted-tool plans, composition plans, context inputs,
datasources, guards, assertions, and caveats.

Use `build_openai_agents_from_plan(plan)` after inspection, or use
`build_openai_agents_from_contracts(...)` as the convenience function that plans
and builds in one call. `build_openai_agent(...)` remains the low-level helper
for constructing one SDK object from one manifest and instruction string.

## Output Types

Callers can pass explicit output types:

```python
output_type_registry={"SupportReply": SupportReplyModel}
```

If the contract declares `type SupportReply from python
"my_app.models:SupportReplyModel"`, compile with `--allow-python-imports` to
derive the canonical schema and preserve the import path in the manifest. The
adapter still uses an explicit output type registry or generated SDK output
models when constructing OpenAI agents.

Or they can ask the adapter to generate Pydantic v2 models from the compiled
Contract4Agents JSON Schema subset:

```python
from contract4agents.adapters.openai import build_openai_output_type_registry

output_types = build_openai_output_type_registry(artifacts)
```

`generate_output_types=True` enables that helper inside the planner/factory.
Explicit registry entries override generated models. Unsupported schemas fail
closed with `OpenAIAgentFactoryError`.

## Tools, Hosted Tools, And Approvals

Host tools may be supplied as existing SDK tool objects or raw Python callables.
Raw callables are wrapped with `agents.function_tool(name_override=...)`.
Approval-required raw callables are wrapped with `needs_approval=True`.
Prebuilt SDK tools are accepted, but approval enforcement cannot be verified by
Contract4Agents, so the plan returns an `approval_enforcement_unverified`
caveat.

Hosted provider tools are declared separately from host tools:

```contract
use hosted_tool openai.web_search context_size "medium"
```

Pass `hosted_tool_registry={"openai.web_search": True}` to let the adapter build
`agents.WebSearchTool(search_context_size="medium")`. A registry entry can also
be a provider object or a factory callable. Denied host and hosted tools are
omitted and reported as caveats; missing non-denied host or hosted tools are
configuration errors.

Composition declarations are mapped when the host supplies the corresponding
objects:

- `agent_as_tool(...)` and `as_tool(...)` use `agent_tool_registry`.
- `handoff(...)` uses `handoff_registry`.
- `isolated_subagent(...)` is reported as unsupported.

Without an explicit composition declaration, the planner prefers an agent-tool
registration, then a handoff registration. If both are supplied, it uses the
agent tool and emits a `composition_mode_ambiguous` caveat.

## Running With Contract Checks

`run_openai_agent_with_contract(...)` runs one supplied SDK agent, appends
`RuntimeContext.rendered_context()` to the user input, keeps hidden and
sensitive context out of the prompt, resolves SDK approval interruptions through
a host callback, records `approval.requested` and `approval.completed`, evaluates
compiled assertions with `evaluate_run_contract(...)`, records
`assertion.evaluated`, and returns `OpenAIContractRunResult`.

This helper does not run routes, replay workflows, choose specialists, or own
approval UX. Host code still controls orchestration, persistence, credentials,
real tools, hosted-tool enablement, and deployment behavior.

## Live Checks

Live OpenAI tests are opt-in and require `OPENAI_API_KEY` plus an explicit flag:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE=1 pdm run test:openai-agent-live
```
