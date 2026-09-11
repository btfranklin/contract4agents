# Contract4Agents

![Contract4Agents banner](https://raw.githubusercontent.com/btfranklin/contract4agents/main/.github/social%20preview/contract4agents_social_preview.jpg "Contract4Agents")

Contract4Agents is a free, open-source toolkit for defining structured agents
in external specifications and turning those specifications into usable agent
components. It includes a typed contract language, a compiler, code generators,
and adapters for OpenAI Agents SDK, Strands Agents, and Google ADK.

Write the agent's inputs, outputs, tools, instructions, and relationships in a
`.contract` file. Contract4Agents derives schemas, documentation, and normal
framework objects from that definition. Your application supplies the tool
implementations and runs the agents.

This gives you one place to read and change the agent design. You can review
its guardrails and expectations without finding them across prompts, SDK
configuration, and application code. Target bindings select implementations
and models without repeating the contract. Validation and evaluation tools
help you check that the implementation matches the design.

## A Complete Example

This example defines one support agent, connects a Python tool, and runs the
agent through the OpenAI Agents SDK.

In a Python 3.11+ project managed by PDM, install the OpenAI adapter:

```bash
pdm add "contract4agents[openai]"
```

Create these files, including an empty `your_app/__init__.py`:

```text
your_app/
  __init__.py
  tools.py
  run_agent.py
  agent_contracts/
    support.contract
    contract4agents.targets.toml
```

### 1. Define the Agent and Its Tool

Put the types, tool interface, and agent definition in
`your_app/agent_contracts/support.contract`:

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

The tool declaration defines the interface. The `use` block gives this agent
access to it. The agent's return type defines the structure of its answer.

### 2. Supply the Implementation and Model

Put the Python tool in `your_app/tools.py`:

```python
def search_knowledge(query: str) -> dict[str, object]:
    return {
        "answer": "Orders normally leave the warehouse within two business days.",
        "source_ids": ["shipping-policy-2026"],
    }
```

This example returns fixed data. In your application, replace the function body
with a lookup in your knowledge base.

Connect that function and select a model in
`your_app/agent_contracts/contract4agents.targets.toml`:

```toml
schema_version = "1"

[targets.openai]
adapter = "openai"

[targets.openai.tools."knowledge.search"]
python = "your_app.tools:search_knowledge"

[targets.openai.profiles.development]
default_model = "gpt-5.6-luna"
```

The target binding contains implementation choices. It does not repeat the
agent's instructions, tool access, or output schema. Choose a model available
to your provider account.

### 3. Check the Definition

Run these commands from the project root:

```bash
pdm run contract4agents check your_app/agent_contracts
pdm run contract4agents compile your_app/agent_contracts --out .contract/build
pdm run contract4agents plan your_app/agent_contracts --target openai --profile development
```

`check` validates the source and target bindings. `compile` writes schemas,
instructions, and documentation. `plan` shows how the selected target will
represent the agent and reports missing bindings or unsupported requirements.

These commands do not call a model. Binding checks import Python modules to
inspect callable signatures, so module-level code runs during those imports.

### 4. Run the Agent

Put this in `your_app/run_agent.py`:

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

This step makes a provider request. `materialize()` constructs an ordinary SDK
agent with its tool and generated output type. The SDK runs it and returns a
structured `SupportReply`. You write the tool and invocation code; the contract
supplies the agent configuration.

Your application continues to own credentials, business rules, approval
decisions, sessions, retries, persistence, and deployment.

## What Gets Generated?

File generation and runtime construction serve different needs:

| Operation | Result | Use it when |
| --- | --- | --- |
| `compile` | Canonical contract data, JSON Schemas, instructions, and review documentation | You want files to inspect or use in other tools |
| `generate --target python` | Python types with Pydantic validation | Application code needs to import the contract types |
| `generate --target typescript` | TypeScript interfaces and Zod schemas | TypeScript code needs the same data definitions |
| `materialize()` | Native Python SDK agents, tools, and supported composition | You want to run the defined agents through a supported framework |

`materialize()` compiles the contract internally; it does not require a prior
`compile` command. It checks the objects it constructs against the target plan.
When generated files are committed, `compile --check` and `generate --check`
can detect stale files in CI.

See [Compiler Outputs](docs/compiler/compiler-outputs.md) for file layouts and
generation commands.

## Framework Support

| Adapter | What it constructs | Reference |
| --- | --- | --- |
| OpenAI Agents SDK | Agents, function tools, delegations, and handoffs | [OpenAI target](docs/reference/openai-adapter.md) |
| Strands Agents | Agents, typed tools, and agents used as tools | [Strands target](docs/reference/strands-adapter.md) |
| Google ADK | Agents, tools, and typed agent sub-branches | [Google ADK target](docs/reference/google-adk-adapter.md) |

Install the corresponding extra: `contract4agents[openai]`,
`contract4agents[strands]`, or `contract4agents[google-adk]`.

Targets have different capabilities. Planning reports those differences and
rejects required mappings that would lose declared behavior. For example,
Strands and Google ADK do not currently support contract handoffs. The target
references describe the supported combinations of tools, approvals, context,
and composition. TypeScript generation produces data types and validators;
the current native agent adapters run in Python.

## Check Behavior Against the Contract

You can add `.eval` scenarios, capture traces, compare contract changes, and
assemble review reports as your application needs them.

- **Evals** describe scenarios and expected results. The current `eval replay`
  command assesses recorded outputs and evidence; it does not run a model.
- **Traces** connect observed agent and tool activity to the contract and plan.
- **Assessment** reports whether available evidence supports a requirement:
  `passed`, `violated`, or `unverified`.
- **Semantic diffs and assurance bundles** help reviewers inspect design
  changes and collected results.

These tools support implementation and review. Missing evidence stays
`unverified`. See [Capture and Assure a Run](docs/tutorials/trace-and-assure.md)
for a worked example and [Evals, Controls, and Assurance](docs/evaluation/evals-controls-assurance.md)
for the assessment rules.

## Editor Support

The [VS Code extension](docs/reference/vscode-extension.md) provides syntax
highlighting, completions, diagnostics, hover help, navigation, rename, and
optional inlay hints for `.contract` and `.eval` files. It uses the package's
Python language server, so editor feedback follows the same language rules as
the CLI.

## Learn More

- [First Contract Project](docs/tutorials/first-contract-project.md): build the support example step by step.
- [Using Contract4Agents in an Application](docs/tutorials/using-contract4agents-with-an-agent-app.md): integrate contracts with application code.
- [Incident Command](examples/incident-command/README.md): a complete multi-agent example with local fixtures.
- [Multi-Lens Research](examples/multi-lens-research/README.md): delegation, typed results, and isolation requirements.
- [Market Research Brief](examples/market-research-brief/README.md): host tools and provider search.
- [Documentation Index](docs/index.md): tutorials, language and API references, and contributor guides.
- [Vision](VISION.md): product purpose and ownership boundaries.

## Development

From a checkout of this repository:

```bash
pdm install
npm --prefix tests/typescript ci
npm --prefix editors/vscode ci
pdm run docs-check
pdm run validate
pdm build
```

See [Validation and Quality Gates](docs/quality/validation.md) for checks by
change type and opt-in live tests. Coding agents should start with [AGENTS.md](AGENTS.md).

## License

MIT. See [LICENSE](LICENSE).
