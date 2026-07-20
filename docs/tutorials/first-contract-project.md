# First Contract Project

This tutorial builds one small, runnable OpenAI agent. The contract defines the
agent; the Python code supplies one ordinary tool and starts the finished agent.

## 1. Create the Project

```text
your-app/
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
  your_app/
    __init__.py
    run_agent.py
    tools.py
```

Install Contract4Agents with OpenAI support:

```bash
pdm add "contract4agents[openai]"
```

## 2. Define the Data

Create `agent_contracts/types/support.contract`:

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

Create `agent_contracts/capabilities/support.contract`:

```contract
tool knowledge.search(query: string) -> KnowledgeResult:
    description = "Search the approved support knowledge base."
    side_effect = false
```

The contract says what the tool does. The Python implementation comes next.

## 4. Define the Agent

Create `agent_contracts/agents/support.contract`:

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

```python
def search_knowledge(query: str) -> dict[str, object]:
    return {
        "answer": "Orders normally leave the warehouse within two business days.",
        "source_ids": ["shipping-policy-2026"],
    }
```

Create `agent_contracts/contract4agents.targets.toml`:

```toml
schema_version = "2"

[targets.openai]
adapter = "openai"

[targets.openai.tools."knowledge.search"]
python = "your_app.tools:search_knowledge"

[targets.openai.profiles.development]
default_model = "gpt-5.6-luna"
```

The binding connects the portable tool name to this application's Python
function. It also chooses the model for this complete named profile. Every
declared target needs at least one profile; a profile-level default can cover all
canonical agents, while explicit agent overrides must name canonical agents.

## 6. Check and Plan

From `your-app/`, run:

```bash
contract4agents check agent_contracts
contract4agents compile agent_contracts --out .contract/build
contract4agents plan agent_contracts --target openai --profile development \
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
        "agent_contracts",
        target="openai",
        profile="development",
    )
    agent = system.agents["SupportResponder"]
    result = await Runner.run(agent, input="When will my order ship?")
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

## 8. Run One Deterministic Eval

Create `agent_contracts/evals/support.eval`:

```contract
eval answers_shipping_question for SupportResponder:
    given question = "When will my order ship?"
    expect output conforms SupportReply
    expect trace.tool_called(knowledge.search)
```

Create `agent_contracts/eval-data.json`:

```json
{
  "schema_version": "2",
  "cases": {
    "eval:SupportResponder:answers_shipping_question": {
      "inputs": {"question": "When will my order ship?"},
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
                "capability_id": "tool:knowledge.search"
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

Now run the eval:

```bash
contract4agents eval agent_contracts --target openai --profile development
```

This first eval is deliberately offline and repeatable. It proves that the
declared output and expected tool call are assessed correctly before you connect
the same contract to live or recorded executions.

## Next Step

You now have the complete loop: contract, binding, plan, materialized agent, and
eval. When you need multiple agents, context providers, approvals, or production
traces, continue with [the application guide](using-contract4agents-with-an-agent-app.md).
