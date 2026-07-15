# OpenAI Target Reference

The OpenAI target materializes canonical Contract4Agents IR into ordinary
OpenAI Agents SDK objects. It is contract-first and plan-first: users review a
provider-neutral materialization plan before the same mapping constructs the
native graph.

Install the optional target dependencies:

```bash
pdm add "contract4agents[openai]"
```

## Target Bindings

`contract4agents.targets.toml` contains target-specific locators and options:

```toml
schema_version = "1"

[targets.openai]
adapter = "openai"

[targets.openai.tools."documents.search"]
python = "my_app.tools:search_documents"

[targets.openai.tools."web.search"]
provider = "web_search"
search_context_size = "medium"

[targets.openai.datasources."account.history"]
python = "my_app.context:account_history"

[targets.openai.external_context.authenticated_account]
python = "my_app.context:authenticated_account"

[targets.openai.environments.in_process]
provider = "contract4agents.runtime:InProcessEnvironment"

[targets.openai.profiles.test]
default_model = "test-model"

[targets.openai.profiles.production]
default_model = "gpt-5.2"

[targets.openai.profiles.production.agents.ResearchAgent]
model = "gpt-5.6-luna"
```

The first implementation has no profile inheritance. A profile must be
complete. Bindings cannot contain permissions, prompts, controls, schemas,
agent factories, output-type mappings, or composition registries.

Python locators use `module:attribute`. Planning may import a locator to inspect
its callable signature, but it never calls application code.

## Plan Without Constructing Agents

```bash
contract4agents plan agent_contracts --target openai --profile production \
  --out .contract/build/openai-production-plan.json
```

The plan contains the contract digest, resolved model for every agent, binding
identities, grants and authorization, native composition mappings, control and
isolation mechanisms, expected telemetry, host obligations, and target caveats.
It contains no live SDK objects or process-specific callable addresses.

Each mapping reports one outcome:

- `exact`: the SDK natively implements the requested semantic.
- `host_enforced`: a named host or environment provider enforces it.
- `emulated`: generated runtime behavior preserves it.
- `degraded`: construction would lose a documented semantic.
- `unsupported`: no honest implementation exists.

Required degraded and unsupported mappings fail planning.

## Materialize the Complete Graph

```python
from contract4agents import materialize

system = materialize(
    "agent_contracts",
    target="openai",
    profile="production",
)

lead = system.agents["ResearchLead"]
plan = system.plan
```

`system.agents` is keyed by the names written in contracts. Its values are
native OpenAI Agents SDK `Agent` objects. `system.graph` also exposes generated
output types, resolved implementations, native grant and composition objects,
the typed context runtime, environment-enforcement evidence, and
graph-validation evidence.

Construction uses two passes so source-file order does not constrain the graph:

1. Build agent shells, instructions, generated Pydantic output types, tools,
   approval configuration, and hooks.
2. Resolve named delegation and handoff edges across the complete graph.

The adapter then validates models, tools, grants, approvals, outputs,
composition, and hooks against the immutable plan.

## Tools and Approvals

A contract tool is a shared portable interface. Its grant selects `host`,
`provider_hosted`, `remote`, or a named environment execution boundary. The
OpenAI target currently resolves Python host callables and supported
provider-native tools from target bindings.

Raw Python callables are converted to native function tools. Dotted contract
names are mapped to reversible SDK-safe names. An `approval_required` grant
sets the native tool's approval requirement and creates expected approval
telemetry. The application remains responsible for approval decisions and UI.

Missing implementations, signature mismatches, unverified required approval
enforcement, and unsupported remote bindings fail closed.

## Delegation and Handoff

For a named `composition` edge:

- `delegate` creates an agent-as-tool that returns the target agent's typed
  final output to the source agent.
- `handoff` creates a native SDK handoff that transfers control.

Descriptions, typed input mappings, history transfer, and isolation profiles
come from the edge. OpenAI agent-as-tool and handoff payloads are model supplied,
so the plan classifies source-value transfer as emulated and exposes a host
obligation when equality with the declared source mapping must be proven. The
host does not supply agent factories, agent-tool registries, or ordinary
handoff registries.

Provider-specific callback behavior that cannot be expressed portably belongs
in an explicitly nonportable target binding, never in portable source.

## Isolation

The built-in `InProcessEnvironment` can report and enforce only the dimensions
it actually controls, such as fresh context, capability allowlisting, fresh
state, and return-channel restriction. It does not claim an operating-system,
filesystem, network, process, or secret boundary.

A contract requiring stronger isolation must select an environment provider
that implements `EnvironmentProvider` and supplies enforcement evidence. If the
selected provider cannot satisfy a required dimension, materialization stops.

## Running

Run the returned object with the normal SDK API:

```python
from agents import Runner

result = await Runner.run(system.agents["ResearchLead"], input=user_request)
```

If the entry agent declares datasource or external context, resolve it through
`system.context.resolve_agent(...)` first and pass the returned rendered values
through the application's normal SDK context or input strategy. Resolution
validates contract types, enforces declared cache scopes, and emits normalized
provenance events. Contract4Agents deliberately does not hide the remaining
provider-specific injection step: the OpenAI SDK has no framework-native typed
entry-input channel.

Contract4Agents does not replace the SDK runner, provider trace backend, session
store, or deployment environment. It constructs and validates the graph those
systems execute.

## Materialization Evidence

Pass a `TraceSink` to `materialize` to capture deterministic construction
evidence. `RecordingTraceSink` is useful for tests and small host integrations:

```python
from contract4agents.materialization import RecordingTraceSink

sink = RecordingTraceSink()
system = materialize(
    "agent_contracts",
    target="openai",
    profile="production",
    trace_sink=sink,
)
```

Events identify the contract and plan digests and the stable semantic IDs for
constructed agents, tools, grants, approvals, composition edges, output types,
context, datasources, and isolation mechanisms. Runtime provider spans should
be correlated into normalized trace schema V2.

Use the supplied Agents SDK tracing processor for runtime correlation:

```python
from agents import add_trace_processor
from contract4agents.tracing import OpenAINormalizedTraceProcessor

processor = OpenAINormalizedTraceProcessor(
    artifacts.ir,
    system.plan,
    run_id=run_id,
    thread_id=thread_id,
)
add_trace_processor(processor)

# Run the native graph, then obtain strict normalized evidence.
trace = processor.normalized_trace()
```

The processor maps native agent, function-tool, delegation, and handoff spans
to stable contract IDs, adds output-validation evidence for successful agent
spans, and preserves provider trace/span correlation. It intentionally does not
copy raw provider inputs or outputs into normalized payloads.

## Offline and Live Validation

The default test suite constructs real SDK objects with deterministic local
models and no credentials. Live provider checks are opt-in:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
```

This single smoke test exercises contract compilation, production-profile
planning, native graph materialization, typed context resolution, three
agent-as-tool delegations, structured output, and SDK-span correlation.

See [Validation and Quality Gates](../quality/validation.md) before interpreting
a skipped live check as coverage.
