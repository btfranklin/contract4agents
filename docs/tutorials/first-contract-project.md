# First Contract Project

This tutorial is for an engineer with an existing or planned agent app who wants
the smallest useful Contract4Agents adoption path. Contract4Agents does not run
your agent for you. It gives your app a typed, reviewable contract that can
compile into instructions, manifests, JSON Schemas, guard metadata, eval packs,
monitor rules, and visualization files. Start with one agent, get one contract
compiling, then add stricter checks only when they help.

## Create A Contract Directory

Put contract source beside your application code:

```text
your-agent-app/
  agent_contracts/
    types/
      support.contract
    agents/
      support_responder.contract
  src/
    your_app/
      agents/
      tools/
      runtime.py
  .contract/
    build/
```

The directory name is up to you. The examples use `agent_contracts` because it is
easy to recognize in commands and CI.

## Write The Types

Create `agent_contracts/types/support.contract`:

```contract
type SupportTicket:
    ticket_id: str
    customer_message: str
    product_area: str

type SupportReply:
    answer: str
    confidence: float
    follow_up_needed: bool
```

Types are the structured values your host app passes into the agent and expects
back from it. Keep the first type small. You can add fields after the first
contract compiles.

## Write One Agent

Create `agent_contracts/agents/support_responder.contract`:

```contract
agent SupportResponder(
    ticket: SupportTicket
) -> SupportReply:

    goal = "Answer the support ticket clearly and flag follow-up when needed."

    policy = [
        "answer only from available ticket details",
        "flag follow_up_needed when the request requires account-specific action",
        "do not claim that account state changed",
    ]

    guards = [
        require(output conforms SupportReply),
    ]

    assertions = [
        expect(output conforms SupportReply),
    ]
```

This is not the agent implementation. Your SDK app still owns models, tools,
credentials, workflow, and execution. The contract records the callable boundary
and the behavior you want humans, CI, and adapters to inspect.

## Run The Local Loop

From your application repo root:

```bash
contract4agents check agent_contracts
contract4agents compile agent_contracts --out .contract/build
contract4agents visualize agent_contracts --out .contract/build/visualization
```

Open or inspect:

- `.contract/build/instructions/SupportResponder.md`
- `.contract/build/schemas/SupportReply.json`
- `.contract/build/manifests/SupportResponder.json`
- `.contract/build/visualization/index.html`

Generated artifacts are disposable. The `.contract` source files are durable.
Keep `.contract/build` ignored unless your team intentionally reviews generated
artifacts in pull requests.

## Consume The Artifacts From Python

Your app can compile the same project at build time or startup:

```python
from pathlib import Path

from contract4agents.compiler import compile_project

artifacts = compile_project(Path("agent_contracts"))
manifest = artifacts["manifests"]["SupportResponder"]
instructions = artifacts["instructions"]["SupportResponder"]
schema = artifacts["schemas"]["SupportReply"]
```

Use the instructions with your SDK agent, use the schema to align structured
outputs, and use the manifest to inspect declared tools, context, guards, and
assertions.

## OpenAI Agents SDK Sketch

If your host app uses the OpenAI Agents SDK, the adapter can plan and build SDK
agents from compiled artifacts. This is a docs-only sketch; your app still
supplies the model choice, real tools, approval handling, and runtime workflow.

```python
from pathlib import Path

from contract4agents.adapters.openai import (
    build_openai_agents_from_plan,
    plan_openai_agents_from_contracts,
)
from contract4agents.compiler import compile_project

artifacts = compile_project(Path("agent_contracts"))

plan = plan_openai_agents_from_contracts(
    artifacts,
    output_type_registry={"SupportReply": SupportReplyModel},
    model_registry={"SupportResponder": config.support_model},
)

factory_result = build_openai_agents_from_plan(plan)
support_agent = factory_result.agents["SupportResponder"]
```

For contracts with host tools, pass a `tool_registry`. For hosted provider tools,
pass a `hosted_tool_registry`. For output types, either pass explicit SDK/Pydantic
models or use the adapter's generated output type support. The detailed adapter
surface is documented in [OpenAI Adapter Reference](../reference/openai-adapter.md).

## What To Add Next

After one contract compiles, add only the next layer you need:

1. Add one `.eval` for the most important scenario.
2. Add a specialist agent or a host tool declaration.
3. Use `contract4agents.registry.json` and `--strict-drift` when CI should compare contracts with host-code surfaces.
4. Capture normalized traces and run monitors when you have real or staged runs.

For the deeper integration path, continue with
[Using Contract4Agents With An Agent App](using-contract4agents-with-an-agent-app.md).
Use [Contract4Agents Language](../language/contract-language.md) when you need
exact syntax, [CLI Reference](../reference/cli.md) for command behavior, and
[Incident Command](../../examples/incident-command/README.md) for a complete
offline example.
