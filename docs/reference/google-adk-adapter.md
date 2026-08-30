# Google ADK Target Reference

The Google ADK target materializes canonical Contract4Agents IR into ordinary
`google.adk.agents.LlmAgent` and `google.adk.tools.BaseTool` objects. The
materializer constructs and validates the native graph; application code keeps
ownership of `App`, `Runner`, session services, retries, persistence,
confirmation UI, and deployment.

Install the optional target dependencies:

```bash
pdm add "contract4agents[google-adk]"
```

## Target Bindings

Python tools, datasources, and external context use the same
`module:callable` locator as other built-in targets:

```toml
schema_version = "1"

[targets.google_adk]
adapter = "google_adk"

[targets.google_adk.tools."records.lookup"]
python = "my_app.tools:lookup_records"

[targets.google_adk.datasources."records.current"]
python = "my_app.context:current_records"

[targets.google_adk.external_context.request_context]
python = "my_app.context:request_context"

[targets.google_adk.profiles.production]
default_model = "gemini-3-flash"

[targets.google_adk.profiles.production.options]
temperature = 0.2
```

Native model selection requires a `gemini-*` identifier. To supply another ADK
model implementation, configure trusted host code:

```toml
[targets.google_adk.profiles.production]
default_model = "provider-model-name"

[targets.google_adk.profiles.production.options]
model_factory = "my_app.models:create_adk_model"
```

The callable is imported and signature-checked during conformance but is not
called until materialization. It must accept `model` and `options` as keyword
arguments and return `google.adk.models.BaseLlm`:

```python
from collections.abc import Mapping

from google.adk.models import BaseLlm


def create_adk_model(*, model: str, options: Mapping[str, object]) -> BaseLlm:
    ...
```

The factory is called once per configured agent. It owns all remaining model
options; Contract4Agents does not also apply them as Gemini generation
configuration. Agent-level profile options override profile-level options.
Credentials remain environment-owned and are never serialized into the plan.

## Semantic Mapping

| Requested contract behavior | Google ADK outcome |
| --- | --- |
| Python tool, datasource, or external context | `exact` |
| Tool approval | `exact`, through `ToolContext.request_confirmation` |
| Native Gemini output with no tools | `exact`, through `output_schema` |
| Native `gemini-3*` output with ordinary tools | `exact`, through `output_schema` |
| Output with other tool/model combinations | `emulated`, with JSON instruction plus fail-closed terminal validation |
| `delegate` with `history = none` | `emulated`, through a typed `run_node` sub-branch |
| Delegate `summary` or `full` history | `unsupported` |
| Any handoff mode | `unsupported` |
| Tool isolation or remote/TypeScript/MCP locator | `unsupported` |

Delegate tools validate the declared input and output types and run a
single-turn child with no inherited contents. Model-supplied delegate values
cannot prove equality with the declared source expressions, so the plan records
a host obligation when that equality matters. Contract4Agents deliberately
does not substitute ADK transfer agents or workflow nodes for a typed handoff
or host-owned deterministic workflow.

Generated contract tools validate model arguments before calling application
code, run synchronous callables off the event-loop thread, await asynchronous
callables, and validate the returned value. A host tool can return ordinary
structural data or application Pydantic models, including nested models. A
literal Python string remains a string value; JSON text parsing is reserved for
ADK child-agent and hosted-tool results. Approval-required tools do not call
application code until ADK supplies an affirmative `ToolConfirmation`.

The adapter reads back native ADK parameter and output schemas during offline
materialization. Portable list cardinality must remain visible as `minItems`
and `maxItems`; a missing or changed bound fails conformance. This proves the
schema evidence exposed by the installed SDK, not provider-wide constrained
decoding for every model and tool combination.

Materialization evidence reads public `LlmAgent` identity, instruction, model,
output, and tool properties where ADK exposes them. Tool declarations currently
use ADK's private `_get_declaration()` boundary because no public declaration
accessor exists; this path is version-checked and fails closed when it cannot
read a declaration. A custom model factory proves only exact argument transfer
and the returned `BaseLlm` type. It does not prove that the host or ADK applies
opaque provider settings.

## Google Search

The adapter recognizes one provider-native search locator:

```toml
[targets.google_adk.tools."web.search"]
provider = "google_adk"
tool = "google_search"
model = "gemini-2.5-flash"
```

The contract capability must be side-effect-free and structurally equivalent
to:

```text
tool web.search(query: string) -> SearchResponse

type SearchResponse {
  results: list[SearchResult]
}

type SearchResult {
  title: string
  url: string
  snippet: string
}
```

The binding accepts no other keys and requires an explicit `gemini-2*` search
model. It maps as `emulated`: the adapter creates a stateless search child
containing only ADK's `google_search`, wraps it through `AgentTool` with
grounding propagation, and exposes a contract-safe typed outer tool. Missing or
malformed title, URL, or snippet fields fail validation; the adapter never
invents them.

Google requires production applications to display Search suggestions,
`renderedContent`, and citations when supplied. The trace plugin preserves
grounding-presence evidence, but rendering those provider artifacts remains a
host UI obligation. An agent granted native Search uses emulated terminal
output validation even when its parent model could otherwise use native output
schema enforcement.

## Materialize, Run, and Trace

```python
from contract4agents import materialize
from contract4agents.tracing import GoogleADKNormalizedTraceRouter, TraceAttempt
from google.adk.apps import App
from google.adk.runners import Runner

system = materialize(
    "agent_contracts",
    target="google_adk",
    profile="production",
)

router = GoogleADKNormalizedTraceRouter().attach(system.graph)
app = App(
    name="contract_app",
    root_agent=system.agents["ResearchLead"],
    plugins=[router.plugin()],
)
runner = Runner(app=app, session_service=host_session_service)

attempt = TraceAttempt("research:1", "research:attempt:1", 1)
session = router.open_session(
    system.context.ir,
    system.plan,
    run_id="research:1",
)
with session:
    with session.bind_attempt(attempt, agent="ResearchLead"):
        async for event in runner.run_async(...):
            ...
```

The plugin observes ADK run, agent, model, tool, error, confirmation, and event
boundaries. Provider-owned terminal validation emits `output.accepted` or
`output.schema_failed` evidence when a trace session is active, while remaining
fail-closed when tracing is absent. Grounding metadata is recorded only as
counts and presence flags; raw prompts, outputs, and search payloads are not
copied into normalized trace data.

When the host records an approval request or decision directly, pass the stable
native function-call ID as `provider_identity`. It must match the ID on the tool
callback event. Approval assurance remains unverified when this exact invocation
identity is unavailable.

The host must create a fresh or otherwise safely isolated graph/session for
independent concurrent requests, resolve declared context through
`system.context`, install the plugin in its ADK `App` or `Runner`, drive
confirmation and resume, choose retry and terminal-attempt policy, and persist
the resulting trace and assurance artifacts.

## Provider evidence

The trace plugin passes the public `LlmResponse` and callback exceptions to
content-free normalizers. It inspects usage metadata, response identity,
finish/interruption flags, refusal fields, and safe error codes. A terminal
`provider.response.failed` callback reports a complete provider outcome with a
failure category when structured facts support it; the response-normalization
path can still be unverified because no response was returned. Missing hooks
or missing terminal evidence remain unverified.

Usage reports use the public usage metadata fields and bind one aggregation to
one stable response or callback identity. A missing usage object is
unavailable; a partial object is partial. Streaming and turn callbacks with
the same identity are not counted twice. The plugin never stores callback
exceptions, prompts, outputs, response bodies, grounding content, or search
payloads. A host timeout or cancellation is not labelled as a provider
timeout or cancellation unless ADK reports that structured fact.
