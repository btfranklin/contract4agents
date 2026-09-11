# First Contract Project

This tutorial builds one small OpenAI agent. One contract file defines the
agent, its tool, and the data that they exchange. Python supplies the tool
implementation and runs the native agent.

You can complete the check and materialization steps offline. The final agent
run needs an OpenAI API key and can incur provider charges.

## 1. Create the Project

```text
your-app/
  your_app/
    __init__.py
    run_agent.py
    tools.py
    agent_contracts/
      support.contract
      contract4agents.targets.toml
```

Use Python 3.11 or later. In a new directory, initialize a PDM project and add
Contract4Agents with OpenAI support:

```bash
pdm init
pdm add "contract4agents[openai]"
```

Create the files and directories shown above. Leave `your_app/__init__.py`
empty.

## 2. Define the Agent System

Create `your_app/agent_contracts/support.contract`:

```contract
type KnowledgeResult:
    answer: string
    source_ids: list[string]

type SupportReply:
    answer: string
    source_ids: list[string]
    needs_follow_up: boolean

tool knowledge.search(query: string) -> KnowledgeResult:
    description = "Search the approved support knowledge base."
    side_effect = false

agent SupportResponder(question: string) -> SupportReply:
    use knowledge.search:
        availability = enabled
        authorization = preapproved
        execution = host

    goal = "Answer the support question from approved evidence."
    description = "Handles first-line support questions."
    guidance = [
        "Use the knowledge tool before answering.",
        "Include the source IDs behind the answer.",
        "Set needs_follow_up when the evidence is insufficient.",
    ]
```

The contract is the source of truth for the agent shape, tool access, and
structured outputs. It does not contain the tool implementation.

## 3. Implement and Bind the Tool

Create `your_app/tools.py`:

```python
def search_knowledge(query: str) -> dict[str, object]:
    return {
        "answer": "Orders normally leave the warehouse within two business days.",
        "source_ids": ["shipping-policy-2026"],
    }
```

This fixed result keeps the example small. A real application can replace the
function body with a knowledge-base query.

Create `your_app/agent_contracts/contract4agents.targets.toml`:

```toml
schema_version = "1"

[targets.openai]
adapter = "openai"

[targets.openai.tools."knowledge.search"]
python = "your_app.tools:search_knowledge"

[targets.openai.profiles.development]
default_model = "gpt-5.6-luna"
```

The binding connects the contract tool to the Python function. The profile
selects the model. Each target needs at least one named profile.

A host tool that changes money, entitlements, or customer records must enforce
its trusted business rules in application code. See [Enforcing Business Policy
with Host Tools](enforcing-business-policy.md) for a refund example.

## 4. Check the Contract

From `your-app/`, run:

```bash
pdm run contract4agents check your_app/agent_contracts
```

This command checks the contract and its target binding. It does not create SDK
objects or call a provider.

## 5. Materialize the Native Agent

Create `your_app/run_agent.py`:

```python
import argparse
import asyncio

from agents import Runner
from contract4agents import materialize


async def main(*, run: bool) -> None:
    system = materialize(
        "your_app/agent_contracts",
        target="openai",
        profile="development",
    )
    agent = system.agents["SupportResponder"]

    if not run:
        print("Materialized SupportResponder without a provider call.")
        return

    run_input = system.serialize_input_for_agent(
        agent,
        {"question": "When will my order ship?"},
    )
    result = await Runner.run(agent, input=run_input)
    print(result.final_output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(run=args.run))
```

Materialize the system offline:

```bash
pdm run python -m your_app.run_agent
```

`materialize(...)` compiles the contract, resolves the profile, builds the
native OpenAI agent and tool, and validates the result against the plan. It
does not call the model.

## 6. Run the Agent

Set `OPENAI_API_KEY` in your environment. Then make the provider call:

```bash
pdm run python -m your_app.run_agent --run
```

The application selects the agent once and reuses that native SDK object for
input serialization and execution.

## Next Steps

You now have a native agent defined by an external contract and run by ordinary
application code.

- Use the [application guide](using-contract4agents-with-an-agent-app.md) for
  more materialization, context, and approval patterns.
- Use [Capture and Assure a Run](trace-and-assure.md) when you need trace and
  assurance evidence.
- Use [Evals, Controls, and Assurance](../evaluation/evals-controls-assurance.md)
  when you are ready to add eval fixtures and assessment workflows.

Compiler artifact output and the `plan` command remain available as optional
inspection tools. They are not required for this first path.
