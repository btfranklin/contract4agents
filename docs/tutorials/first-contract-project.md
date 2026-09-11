# First Contract Project

This tutorial builds one small, runnable OpenAI agent. The contract defines the
agent; the Python code supplies one ordinary tool and starts the finished agent.

Steps 1–7 build and run the agent. Step 8 is an optional offline example of
checking supplied results against the contract. The live run needs an OpenAI
API key and can incur provider charges.

## 1. Create the Project

```text
your-app/
  your_app/
    __init__.py
    run_agent.py
    tools.py
    agent_contracts/
      agents/
        support.contract
      capabilities/
        support.contract
      evals/
        support.eval
      types/
        support.contract
      contract4agents.targets.toml
      eval-data.json
```

Use Python 3.11 or later. In a new directory, initialize a PDM project, then
install Contract4Agents with OpenAI support:

```bash
pdm init
pdm add "contract4agents[openai]"
```

Create the directories shown above and an empty `your_app/__init__.py` file.
The `evals/` directory and `eval-data.json` file are only needed for step 8.

## 2. Define the Data

Create `your_app/agent_contracts/types/support.contract`:

```contract
type KnowledgeResult:
    answer: string
    source_ids: list[string]

type SupportReply:
    answer: string
    source_ids: list[string]
    needs_follow_up: boolean
```

These types are the source of truth for tool and agent outputs.

## 3. Define the Tool

Create `your_app/agent_contracts/capabilities/support.contract`:

```contract
tool knowledge.search(query: string) -> KnowledgeResult:
    description = "Search the approved support knowledge base."
    side_effect = false
```

The contract says what the tool does. The Python implementation comes next.

## 4. Define the Agent

Create `your_app/agent_contracts/agents/support.contract`:

```contract
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

The `use` block grants this agent access to the shared tool. The authorization
decision lives here, not in Python or provider configuration.

## 5. Implement and Bind the Tool

Create `your_app/tools.py`:

This example returns a fixed answer. Replace it with your knowledge-base query
when you connect a real service.

```python
def search_knowledge(query: str) -> dict[str, object]:
    return {
        "answer": "Orders normally leave the warehouse within two business days.",
        "source_ids": ["shipping-policy-2026"],
    }
```

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

The binding connects the tool name to the Python function. The `development`
profile selects the model. Each target needs at least one named profile.

The binding is not a business-policy implementation. A host tool that changes
money, entitlements, or customer records must enforce its own trusted business
rules in application code. See [Enforcing Business Policy with Host
Tools](enforcing-business-policy.md) for a refund-eligibility example.

## 6. Check and Plan

From `your-app/`, run:

```bash
pdm run contract4agents check your_app/agent_contracts
pdm run contract4agents compile your_app/agent_contracts --out .contract/build
pdm run contract4agents plan your_app/agent_contracts --target openai --profile development \
  --out .contract/build/development-plan.json
```

The plan is the useful checkpoint: it shows the exact model and tool binding
that Contract4Agents will materialize, and it fails early if something required
is missing or unsupported.

## 7. Run the Agent

Create `your_app/run_agent.py`:

```python
import asyncio

from agents import Runner
from contract4agents import materialize


async def main() -> None:
    system = materialize(
        "your_app/agent_contracts",
        target="openai",
        profile="development",
    )
    agent = system.agents["SupportResponder"]
    run_input = system.serialize_agent_input(
        "SupportResponder",
        {"question": "When will my order ship?"},
    )
    result = await Runner.run(agent, input=run_input)
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
```

Set `OPENAI_API_KEY` in your environment, then run:

```bash
pdm run python -m your_app.run_agent
```

`materialize` compiles the contract, resolves the development profile, builds
the native OpenAI agent and tool, and verifies the result against the plan.

## 8. Optional: Check a Supplied Result

This example checks a fixed output and event list. It does not test the agent
you ran in step 7. Use it to learn how eval expectations work without another
provider request.

Create `your_app/agent_contracts/evals/support.eval`:

```contract
eval answers_shipping_question for SupportResponder:
    given question = "When will my order ship?"
    expect output conforms SupportReply
    expect trace.tool_called(knowledge.search)
```

Create `your_app/agent_contracts/eval-data.json`:

```json
{
  "schema_version": "1",
  "cases": {
    "eval:SupportResponder:answers_shipping_question": {
      "invocation": {"question": "When will my order ship?"},
      "report": {"fixture": "shipping-question"},
      "trials": [
        {
          "output": {
            "answer": "Orders normally leave within two business days.",
            "source_ids": ["shipping-policy-2026"],
            "needs_follow_up": false
          },
          "events": [
            {
              "event_type": "agent.started",
              "semantic": {"agent_id": "agent:SupportResponder"}
            },
            {
              "event_type": "tool.completed",
              "semantic": {
                "agent_id": "agent:SupportResponder",
                "capability_id": "tool:knowledge.search",
                "grant_id": "grant:SupportResponder:knowledge.search"
              }
            },
            {
              "event_type": "output.accepted",
              "semantic": {"agent_id": "agent:SupportResponder"}
            },
            {
              "event_type": "agent.completed",
              "semantic": {"agent_id": "agent:SupportResponder"}
            }
          ],
          "closure": {
            "status": "complete",
            "reason": "The fixture enumerates every execution path.",
            "channels": ["agent", "output", "provider_response", "tool"],
            "evidence_refs": ["fixture:support:trial-1:closure"]
          },
          "metrics": {"latency_ms": 12.0, "cost_usd": 0.0}
        }
      ]
    }
  }
}
```

Now replay the supplied eval evidence:

```bash
pdm run contract4agents eval replay your_app/agent_contracts \
  --target openai \
  --profile development
```

This first replay is deliberately offline and repeatable. It assesses the
supplied output and trace against the declared output and expected tool call; it
does not invoke the native agent or tool. That makes it useful before you
connect the same contract to live or application-owned execution.

## Next Step

You now have an agent defined by a contract and run by ordinary application
code. Use the [application guide](using-contract4agents-with-an-agent-app.md)
to add agents, context, or approvals. If you need evidence from actual runs,
continue with [Capture and Assure a Run](trace-and-assure.md).
