# Contract4Agents

![Contract4Agents banner](https://raw.githubusercontent.com/btfranklin/contract4agents/main/.github/social%20preview/contract4agents_social_preview.jpg "Contract4Agents")

Contract4Agents is a free, open-source, typed contract language for building
agent systems that are reviewable before they run and accountable afterward.
The contract is the source of truth for agents, shared capabilities,
authorization, composition, controls, quality criteria, and expected evidence.
Target bindings supply only the framework-specific implementation details.

The product loop is:

```text
Declare -> Compile -> Plan -> Materialize -> Run -> Trace -> Assure
```

That separation is the point. You can change a model, provider profile, or tool
implementation without rewriting the portable agent design. Before execution,
the plan shows exactly how the selected target will implement each requested
semantic and blocks required guarantees it cannot honestly enforce. After
execution, contract-bound traces and assurance results distinguish `passed`,
`violated`, and `unverified` instead of treating missing evidence as success.

## Quickstart

Install the core package and the OpenAI target:

```bash
pdm add "contract4agents[openai]"
```

To explore this repository's complete Incident Command example:

```bash
pdm install
pdm run python examples/incident-command/data/seed.py
pdm run contract4agents check examples/incident-command
pdm run contract4agents compile examples/incident-command --out .contract/build/incident-command
pdm run contract4agents plan examples/incident-command --target openai --profile test
```

The first two commands need no provider credentials and do not import or call
application implementations. `plan` loads target bindings only far enough to
validate coverage and safely inspect callable signatures; it does not construct
agents or execute business code.

## A Small Contract-First Team

Define portable types and a shared capability:

```contract
type SupportRequest:
    ticket_id: string
    question: string

type SupportReply:
    answer: string
    needs_follow_up: boolean

tool knowledge.search(query: string) -> SupportReply:
    description = "Search the approved support knowledge base."
    side_effect = false
```

Grant the capability to an agent. Availability, authorization, and execution
are independent and explicit:

```contract
agent SupportResponder(request: SupportRequest) -> SupportReply:
    use knowledge.search:
        availability = enabled
        authorization = preapproved
        execution = host

    goal = "Answer the support request accurately."
    description = "Handles first-line support questions."
    guidance = [
        "Use only evidence returned by approved capabilities.",
        "Say when the available evidence is insufficient.",
    ]
```

Bind the portable name to one target implementation in
`contract4agents.targets.toml`:

```toml
schema_version = "1"

[targets.openai]
adapter = "openai"

[targets.openai.tools."knowledge.search"]
python = "your_app.tools:search_knowledge"

[targets.openai.profiles.test]
default_model = "test-model"

[targets.openai.profiles.production]
default_model = "gpt-5.2"
```

The binding does not repeat prompts, permissions, schemas, agent factories, or
controls. Those remain contract-owned.

## Inspect Before Construction

Compile provider-neutral artifacts and review the resolved target plan:

```bash
contract4agents check agent_contracts
contract4agents compile agent_contracts --out .contract/build
contract4agents generate agent_contracts --out .contract/generated
contract4agents plan agent_contracts --target openai --profile production \
  --out .contract/build/production-plan.json
```

Compilation produces deterministic canonical IR, its digest, JSON Schemas,
audience-safe instructions, reviewer documentation, and generated Pydantic,
TypeScript, and Zod types. `compile --check` and `generate --check` make stale
generated artifacts a CI failure.

The plan resolves models, bindings, grants, approvals, composition, controls,
isolation mechanisms, host obligations, and expected telemetry. Each mapping is
reported as `exact`, `host_enforced`, `emulated`, `degraded`, or `unsupported`.
Required degraded or unsupported guarantees fail closed.

## Materialize Normal Framework Objects

Contract4Agents constructs the complete native agent graph at runtime:

```python
from agents import Runner
from contract4agents import materialize

result = materialize(
    "agent_contracts",
    target="openai",
    profile="production",
)

support_agent = result.agents["SupportResponder"]
reviewed_plan = result.plan
run_result = await Runner.run(support_agent, input="Where is my order?")
```

`result.agents` contains ordinary OpenAI Agents SDK `Agent` objects. Generated
output types, host tools, approval hooks, delegations, and handoffs are wired
from the contract graph and target bindings; host code does not maintain a
parallel agent registry.

The host still owns credentials, approval decisions and UI, persistence,
external services, and deterministic application workflow. Contract4Agents is
not a general workflow language and does not hide provider differences.

## Evidence, Evals, and Assurance

The normalized trace schema binds every event to a contract digest, plan digest,
stable semantic IDs, provider-native correlation, provenance, and audience-safe
redaction metadata. The same control assessor is used for controlled evals and
imported production traces.

`.eval` files name scenarios and expectations. The target/profile eval workflow
derives its agent, capability, grant, control, and telemetry inventory from the
contract and plan; users do not restate the runtime in a fixture manifest.
Repeated campaigns report pass, violation, and unverified rates with uncertainty,
latency and cost summaries, thresholds, and optional baseline comparisons.

Assurance bundles join the canonical contract, materialization plan, normalized
traces, control results, eval summaries, and semantic diffs into one portable
review package. Missing or incomplete evidence remains explicitly `unverified`.
This is useful evidence for compliance and release review; it is not a legal
certification by itself.

## Public Examples

- [Incident Command](examples/incident-command/README.md) is the recommended
  first read. It demonstrates shared capabilities, different authorization
  grants, explicit context origins, generated delegations, controls, target
  bindings, and deterministic eval data.
- [Multi-Lens Research](examples/multi-lens-research/README.md) demonstrates a
  larger delegation graph, typed workflow boundaries, and an explicit
  isolation profile.
- [Market Research Brief](examples/market-research-brief/README.md) demonstrates
  host tools alongside a provider-native web-search binding.

See [the examples guide](examples/README.md) for the common project structure.

## Documentation

- [First Contract Project](docs/tutorials/first-contract-project.md)
- [Using Contract4Agents in an Application](docs/tutorials/using-contract4agents-with-an-agent-app.md)
- [Language Reference](docs/language/contract-language.md)
- [CLI Reference](docs/reference/cli.md)
- [OpenAI Target Reference](docs/reference/openai-adapter.md)
- [Trace Schema](docs/reference/trace-schema.md)
- [Evals, Controls, and Assurance](docs/evaluation/evals-controls-assurance.md)
- [Documentation Index](docs/index.md)
- [Vision](VISION.md)

The [semantic model](docs/architecture/semantic-model.md) is the detailed
architecture specification. Coding agents should begin with [AGENTS.md](AGENTS.md).

## Development

```bash
pdm install
pdm run docs-check
pdm run validate
pdm build
```

Normal local checks do not require an API key. Opt-in OpenAI live checks are
documented in [Validation and Quality Gates](docs/quality/validation.md).

## License

MIT. See [LICENSE](LICENSE).
