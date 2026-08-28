# Strands Target Reference

The Strands target materializes canonical Contract4Agents IR into native
`strands.Agent` and `AgentTool` objects. Contract4Agents constructs and validates
the graph; application code owns invocation, approval UI, retries, sessions,
persistence, recovery, concurrency, and deployment.

Install the optional target dependency:

```bash
pdm add "contract4agents[strands]"
```

`check`, `plan`, and `visualize` do not import Strands and work without this
extra. Default materialization reports the required extra when the SDK is not
installed.

## Target Bindings

Strands currently accepts Python implementations for portable tools,
datasources, and external context:

```toml
schema_version = "1"

[targets.strands]
adapter = "strands"

[targets.strands.tools."records.lookup"]
python = "my_app.tools:lookup"

[targets.strands.datasources."records.current"]
python = "my_app.context:current_record"

[targets.strands.external_context.request_context]
python = "my_app.context:request_context"

[targets.strands.profiles.production]
default_model = "us.amazon.nova-pro-v1:0"
```

Python locators use `module:callable`. Checking and planning import the callable
to inspect its signature but never invoke it. Remote, TypeScript, module, MCP,
and provider-hosted tool locators are unsupported. Bind search as an ordinary
Python tool; the adapter does not silently substitute a Strands or provider
search package.

The default model path constructs
`strands.models.bedrock.BedrockModel(model_id=..., **options)`. Credentials stay
in the host environment rather than target bindings.

For another Strands model provider, configure a model factory:

```toml
[targets.strands.profiles.production.options]
model_factory = "my_app.models:make_model"
temperature = 0.2
```

```python
from strands.models import Model


def make_model(*, model: str, options: dict[str, object]) -> Model:
    ...
```

Profile options are merged with agent overrides. Checking and planning inspect
but do not call the factory. Materialization calls it once per agent with the
selected model and remaining options; `model_factory` and `environment` are
excluded. The return value must implement `strands.models.Model`.
Nested options reach `BedrockModel` or the configured model factory as ordinary
dictionaries and lists.

## Semantic Support

| Requested mapping | Strands outcome |
| --- | --- |
| Python tool, datasource, or external context | `exact` |
| Approval on a Python tool | `exact` through `HumanInTheLoop` |
| Agent structured output | `exact` through `structured_output_model` |
| `delegate` with `history = none` | `emulated` by a typed agent-as-tool wrapper |
| Named-environment delegate isolation | Determined by the selected `EnvironmentProvider` |
| `delegate` with `summary` or `full` history | `unsupported` |
| Any handoff mode | `unsupported` |
| Tool isolation, remote, TypeScript, module, MCP, or provider-hosted locator | `unsupported` |

Delegate arguments originate with the model. The plan therefore records a host
obligation when the host must prove that a model-supplied argument equals the
declared source expression. Contract4Agents does not substitute Strands Swarm,
Graph, or Workflow behavior for a contract handoff.

Agents are constructed with `retry_strategy=None` and without a Strands session
manager. Delegate tools use `child.as_tool(preserve_context=False)`, so each
delegate invocation starts from the child agent's construction baseline.
Applications requiring independent concurrent state must materialize
independent graphs or otherwise provide independent agent instances.

## Materialize and Run

```python
from contract4agents import materialize
from contract4agents.adapters.strands import StrandsMaterializationProvider

provider = StrandsMaterializationProvider()
system = materialize(
    "agent_contracts",
    target="strands",
    profile="production",
    provider=provider,
)

result = await system.agents["IncidentCommander"].invoke_async(user_request)
output = provider.validate_result(
    system.agents["IncidentCommander"],
    result,
)
```

`system.agents` contains native Strands agents keyed by contract name.
`system.graph` also exposes generated output types, resolved implementations,
native grant and composition objects, context runtime, environment evidence,
and graph-validation evidence. `validate_result` fails closed if the returned
`AgentResult` lacks valid contract structured output.

An approval-required tool returns a normal Strands interrupt before the host
callable executes. The host presents the decision, records approval evidence,
and resumes the native agent with Strands `interruptResponse` content. The
materializer does not own the UI or decide whether approval is granted.

Datasource and external-context values are resolved through
`system.context.resolve_agent(...)`. The host then supplies those rendered,
typed values through its application-specific invocation strategy.

## Runtime Trace and Assurance

Attach the normalized router to a materialized graph once, then bind one
host-owned attempt around each invocation:

```python
from contract4agents.tracing import (
    StrandsNormalizedTraceRouter,
    TraceAttempt,
)

router = StrandsNormalizedTraceRouter()
router.attach(system.graph)
session = router.open_session(
    system.context.ir,
    system.plan,
    run_id=run_id,
)
attempt = TraceAttempt("incident:1", "incident:attempt:1", 1)

with session:
    with session.bind_attempt(attempt, agent="IncidentCommander"):
        result = await system.agents["IncidentCommander"].invoke_async(prompt)

snapshot = session.closed_snapshot
trace = snapshot.trace
trace_closure = snapshot.closure
```

The bridge uses public Strands hooks and maps native agent, contract tool, and
delegate identities back to semantic IDs. Nested child-agent invocations remain
part of the bound host attempt. Raw prompts, tool arguments, and results are not
copied into normalized trace payloads.

For an approval pause, record the request and decision against the corresponding
native grant object before resuming. Closing the session produces
`TraceClosureEvidence`; missing, failed, or unbound instrumentation remains
incomplete or unverified and cannot support assurance claims.

Contract4Agents does not install a runner, callback handler, session manager,
retry loop, observability backend, or deployment controller. Those remain host
or platform responsibilities.
