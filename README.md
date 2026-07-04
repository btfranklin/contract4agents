# Contract4Agents

![Contract4Agents banner](https://raw.githubusercontent.com/btfranklin/contract4agents/main/.github/social%20preview/contract4agents_social_preview.jpg "Contract4Agents")

Contract4Agents is a typed declarative language and local toolchain for defining AI agents as inspectable contracts.

The source artifact is a `.contract` file. It describes an agent's callable interface, context requirements, allowed capabilities, policies, guards, assertions, and output contract. The compiler turns that source into prompts, provider-neutral manifests, JSON Schemas, guard plans, eval packs, monitor rules, run specs, and visualization artifacts.

Contract4Agents does not replace your agent SDK. It gives your SDK implementation a reviewable contract layer that can be checked, compiled, inspected, evaluated, monitored, and compared against host-code drift.

## Quickstart

For an application managed with PDM, add the package to your project:

```bash
pdm add contract4agents
```

For local development on this repository, install the project and inspect the CLI:

```bash
pdm install
pdm run contract4agents --help
```

The fastest complete loop is the public Incident Command example. From the repository root:

```bash
pdm run python examples/incident-command/data/seed.py
pdm run contract4agents check examples/incident-command
pdm run contract4agents compile examples/incident-command --out .contract/build
pdm run contract4agents visualize examples/incident-command --out .contract/build/visualization
pdm run contract4agents eval examples/incident-command
```

That loop validates the source files, writes generated artifacts, builds a static review graph, and runs the deterministic fixture eval. Open `.contract/build/visualization/index.html` after `visualize` to inspect the agent, tool, type, eval, and monitor graph.

For CI drift checks against host-code surfaces, also run:

```bash
pdm run contract4agents check examples/incident-command --strict-drift
```

The `.contract/` directory is generated local output. It is safe to delete and regenerate.

## First 15 Minutes With Your Own Agent App

Start with one existing agent. Put a small Contract4Agents project beside your
application code:

```text
your-agent-app/
  agent_contracts/
    types/
      support.contract
    agents/
      support_responder.contract
  src/
    your_app/
```

Write the input and output shapes first:

```contract
type SupportTicket:
    ticket_id: str
    customer_message: str

type SupportReply:
    answer: str
    confidence: float
    follow_up_needed: bool
```

Then write one agent contract:

```contract
agent SupportResponder(
    ticket: SupportTicket
) -> SupportReply:

    goal = "Answer the support ticket clearly and flag follow-up when needed."

    policy = [
        "answer only from available ticket details",
        "flag follow_up_needed when the request requires account-specific action",
    ]

    guards = [
        require(output conforms SupportReply),
    ]
```

Run the local loop:

```bash
contract4agents check agent_contracts
contract4agents compile agent_contracts --out .contract/build
contract4agents visualize agent_contracts --out .contract/build/visualization
```

Use `check` to validate the contract source. Use `compile` to generate files your
app or reviewers can inspect. Then open these three files first:

- `.contract/build/instructions/SupportResponder.md`
- `.contract/build/schemas/SupportReply.json`
- `.contract/build/manifests/SupportResponder.json`

That is enough for the first pass: reviewable instructions, a structured output
schema, and a machine-readable agent contract.

Add the rest only when it helps:

1. Add one `.eval` for an important scenario.
2. Use `visualize` for human review.
3. Add `contract4agents.registry.json` and `--strict-drift` when CI should compare contracts with host code.
4. Capture traces and add monitors after you have real or staged runs.
5. Use the OpenAI adapter if you want help constructing SDK `Agent` objects.

Ignore run specs, trace JSONL, drift registries, adapter caveats, and live model
checks until one contract compiles and you know which artifact your app should
consume.

## Use It Beside Your Agent App

Your application still owns runtime execution:

- model selection and SDK runner setup;
- Python functions, remote tools, hosted provider tools, and credentials;
- approval UI and approval decisions;
- database, search, document, API, and deployment plumbing;
- final business workflow and stage sequencing.

Contract4Agents owns the contract layer:

- what agents exist;
- what inputs and outputs they accept;
- which tools, hosted tools, subagents, and datasources each agent may use;
- what policies, guards, assertions, evals, monitors, and run specs travel with the team;
- what generated artifacts should be reviewed or consumed by an adapter.

At build time or startup, your app can compile a contract project and consume the same artifacts the CLI writes to disk:

```python
from pathlib import Path

from contract4agents.compiler import compile_project

artifacts = compile_project(Path("agent_contracts"))
support_manifest = artifacts["manifests"]["SupportResponder"]
support_instructions = artifacts["instructions"]["SupportResponder"]
support_schema = artifacts["schemas"]["SupportReply"]
```

The first OpenAI Agents SDK adapter can plan and build OpenAI `Agent` objects from compiled artifacts, but caller code still supplies real tools, handoffs or agents-as-tools, approvals, models, hosted-tool enablement, and workflow control. See the [First Contract Project](docs/tutorials/first-contract-project.md) tutorial for the smallest adoption path and the [OpenAI Adapter Reference](docs/reference/openai-adapter.md) for the adapter surface.

## Generated Artifacts By Job

`contract4agents compile ROOT --out .contract/build` writes provider-neutral build output:

| Job | Artifact |
| --- | --- |
| Feed SDK instructions or review derived prompt text. | `instructions/*.md` |
| Align structured inputs and outputs. | `schemas/*.json` and `types/type-bindings.json` |
| Inspect declared agents, tools, context needs, policies, guards, assertions, and checks. | `manifests/*.json` |
| Wire approval-required or denied-tool behavior in host code or an adapter. | `guards/guard-plan.json` |
| Evaluate controlled runs after you have fixtures, outputs, or traces. | `evals/evals.json`, `monitors/monitors.json`, and `run-specs/*.json` |
| Inspect adapter support and caveats before SDK construction. | `adapters/capability-matrix.json` |
| Review the team design with humans or coding agents. | `docs/summary.md`, `docs/agents/*.md`, and `visualization/index.html` |

Generated artifacts are disposable. The durable source files are the `.contract` and `.eval` files.

## What Contract4Agents Does Not Own

Contract4Agents does not:

- replace OpenAI Agents SDK, Google ADK, Claude Agent SDK, Strands, or your own runtime;
- create your real tool implementations, API clients, credentials, or approval UI;
- choose workflow routes or stage sequencing for your application;
- deploy, host, or observe your production system by itself;
- guarantee runtime guard enforcement unless your host app or adapter wires the compiled guard plan into the execution boundary;
- require live OpenAI calls for normal local checks.

Use Contract4Agents to make the agent team's shape, permissions, policies, review artifacts, evals, monitors, and drift checks explicit. Keep application control flow in application code.

## Examples

- [Incident Command](examples/incident-command/README.md): the recommended first read. It shows typed incident-response agents, specialist subagents, fake local tools, approval-sensitive behavior, evals, monitors, strict drift checks, and generated artifacts.
- [Multi-Lens Research](examples/multi-lens-research/README.md): a complex research team split into focused expert lenses with source-backed synthesis and counterargument handling.
- [Market Research Brief](examples/market-research-brief/README.md): document-driven market research against dated current-fact snapshots, including an OpenAI hosted web-search declaration.

The reusable public example pattern is documented in [examples/README.md](examples/README.md).

## Documentation

- [First Contract Project](docs/tutorials/first-contract-project.md): the smallest path from an existing agent app to one compiled contract.
- [User Guide: Using Contract4Agents With An Agent App](docs/tutorials/using-contract4agents-with-an-agent-app.md): the deeper integration guide for SDK wiring, guards, traces, evals, monitors, and drift checks.
- [Vision](VISION.md): product intent and the contract-layer mental model.
- [Documentation Index](docs/index.md): the repo-local system of record.
- [Language Reference](docs/language/contract-language.md): `.contract` syntax and semantics.
- [CLI Reference](docs/reference/cli.md): command behavior, options, side effects, and diagnostics.
- [OpenAI Adapter Reference](docs/reference/openai-adapter.md): OpenAI Agents SDK planning, construction, and run helper notes.
- [Validation And Quality Gates](docs/quality/validation.md): local validation, packaging checks, CI behavior, and live-test boundaries.
- [Releasing](docs/releasing.md): tag-first release notes and PyPI publishing flow.

## Contributor And Agent Navigation

Coding agents should start with [AGENTS.md](AGENTS.md). It is the operating map for this repo: task routing, package management rules, documentation rules, validation expectations, and current implementation state.

Durable design, implementation, quality, and release knowledge belongs under [docs/](docs/index.md). Keep `README.md` as the public front door, `AGENTS.md` as the coding-agent map, and `docs/index.md` as the deeper system of record.

## Development

```bash
pdm install
pdm run contract4agents --help
pdm run docs-check
pdm run validate
```

Run `pdm build` when changing packaging metadata, `README.md`, `LICENSE`, build configuration, or public package files.

Live OpenAI checks are opt-in and require `OPENAI_API_KEY`:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE=1 pdm run test:openai-agent-live
```

## License

MIT. See [LICENSE](LICENSE).
