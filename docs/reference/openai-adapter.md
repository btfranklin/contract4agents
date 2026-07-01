# OpenAI Adapter Reference

The OpenAI adapter is the first SDK execution target. It is intentionally thin:
Contract4Agents compiles provider-neutral manifests first, and host or fixture code
supplies provider SDK objects that cannot be represented safely from the
manifest alone.

V1 maps Contract4Agents manifests to:

- OpenAI `Agent` name and instructions.
- Caller-supplied function tools from local callables.
- Caller-supplied handoffs or agents-as-tools where used.
- Caller-supplied output model types.
- SDK lifecycle hooks normalized to Contract4Agents trace events.

Use `build_openai_agent(...)` when constructing one SDK object directly from a
manifest and instructions.

Use `build_openai_agents_from_contracts(...)` when constructing a team from
compiled artifacts plus explicit registries:

```python
from contract4agents.adapters.openai import build_openai_agents_from_contracts

factory_result = build_openai_agents_from_contracts(
    artifacts,
    output_type_registry={"SupportReply": SupportReplyModel},
    model_registry={"SupportCoordinator": config.support_model},
    tool_registry={"crm.create_note": crm_create_note_tool},
    agent_tool_registry={"BillingSpecialist": billing_specialist_tool},
    default_model=config.default_agent_model,
)

agents = factory_result.agents
```

The helper is registry-driven. It does not import application models, discover
tools, resolve approvals, or run the workflow. Missing declared host tools or
output types are configuration errors. Declared agent dependencies without
handoff or agent-tool wiring are returned as explicit caveats.

The adapter capability matrix uses structured `status` and `caveats` entries.
Features that depend on host code are marked `partial` or `emulated` rather than
fully supported.

Live OpenAI tests are opt-in and require `OPENAI_API_KEY` plus an explicit integration-test flag.

Use the semantic judge live test when changing OpenAI client setup:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
```

This is intentionally scoped to the semantic judge first. It is a low-flake credential and Responses API smoke check.

Use the live agent fixture when changing OpenAI Agents SDK execution behavior:

```bash
CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE=1 pdm run test:openai-agent-live
```

That path builds SDK `Agent` objects from compiled Contract4Agents manifests, wraps fake local Python tools as function tools, uses agents-as-tools for specialists, runs input guardrails, resolves approval interruptions in fixture code, and normalizes SDK lifecycle hooks back into Contract4Agents trace events.
