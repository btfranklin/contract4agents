# Context And Datasources

Contract4Agents treats agent parameters as typed context slots. Host integrations use runtime primitives to build, resolve, render, and trace those slots.

## Typed Context Slots

An agent signature defines required context:

```contract
agent SupportAgent(
    user_message: UserMessage,
    customer_profile: CustomerProfile,
    problem_summary: AccountRejectionStatus
) -> SupportResult:
```

The host integration must produce a context frame containing each required slot before the agent can run.

## Context Value Envelope

Runtime primitives preserve more than the string rendered to the model.

```python
@dataclass(frozen=True)
class ContextValue:
    type_name: str
    value: object
    rendered: str
    source: str
    provenance: dict[str, object]
    sensitive: bool = False
```

`value` is for host logic, validation, caching, and adapters. `rendered` is for the model. This keeps the system practical while avoiding premature loss of structure.

Some context is intentionally hidden from the model. Hidden context should still be available to tools, guards, callbacks, and datasources when the contract allows it.

```python
@dataclass(frozen=True)
class RuntimeStateValue:
    type_name: str
    value: object
    source: str
    provenance: dict[str, object]
    sensitive: bool = True
```

This distinction maps to SDK patterns such as OpenAI runner context, Google ADK session state, Claude session/options state, and Strands invocation state.

## Datasource Interface

Datasources are Python resolvers for typed context slots. They should be ordinary Python code following a small interface.

Function-style interface:

```python
@datasource(
    produces="AccountRejectionStatus",
    requires=["CustomerProfile"],
    render="markdown",
    cache="run",
)
async def resolve_account_rejection_status(ctx: DatasourceContext) -> ContextValue:
    customer_profile = ctx.get("CustomerProfile")
    status = await lookup_account_status(customer_profile.value["id"])
    return ctx.value(
        type_name="AccountRejectionStatus",
        value=status,
        rendered=render_status(status),
        source="account_status.lookup",
        provenance={"customer_id": customer_profile.value["id"]},
    )
```

## Datasource Context

`DatasourceContext` should expose:

- `get(type_name)`: fetch an already resolved context value.
- `value(...)`: create a `ContextValue` with normalized metadata.
- `trace(...)`: emit datasource trace events.
- `cache`: scoped cache for run-level reuse.
- `redact(...)`: helper for safe rendered output.

## Resolution Algorithm

When an agent requires a context slot:

1. Use the value passed directly to the invocation if present.
2. Use the value already present in the parent frame if present.
3. Find datasources allowed by the agent contract that produce the required type.
4. For each candidate datasource, check whether its required context slots are already present or can be resolved.
5. If exactly one candidate is valid, execute it.
6. If zero candidates are valid, fail with a missing context error.
7. If multiple candidates are valid, fail with an ambiguous datasource error unless the contract chooses one.
8. Store the resolved value in the frame.
9. Record datasource start, success, failure, cache hit, and redaction events in the trace.

This algorithm should be deterministic. Silent best guesses are not acceptable.
V1 also detects datasource dependency cycles and fails with a structured
`DatasourceResolutionCycle`.

## Resolution Policy

The default policy should be:

- Resolve missing required slots automatically only from datasources explicitly allowed by the agent.
- Hosts should pass the compiled manifest's datasource names as the runtime allowlist when resolving context.
- Do not search arbitrary installed Python modules.
- Do not resolve sensitive context unless the agent is allowed to receive its rendered form.
- Allow sensitive context to remain hidden runtime state when tools or guards need it but the model should not see it.
- Fail if resolution would require an unapproved tool or datasource.
- Fail on ambiguity.

Disambiguation syntax can be added when needed:

```contract
resolve problem_summary using AccountRejectionStatus.from_customer_profile
```

## Rendering

Each context value has a rendered representation. Rendering must be explicit enough for safety and debugging.

Rendered context should include:

- Type name.
- Human-readable value.
- Provenance summary when useful.
- Redaction markers for omitted sensitive fields.

Rendered context should not include:

- Raw secrets.
- Provider tokens.
- Internal IDs that the agent contract forbids exposing.
- Large unbounded blobs without summarization.

## Trace Events

Datasource resolution should emit trace events such as:

```json
{
  "schema_version": "1",
  "run_id": "run-123",
  "event_id": "evt-004",
  "event_type": "datasource.resolved",
  "timestamp": 42.0,
  "datasource": "AccountRejectionStatus",
  "data": {
    "produces": "AccountRejectionStatus",
    "requires": ["CustomerProfile"],
    "duration_ms": 42,
    "cache": "miss"
  },
  "provider": {}
}
```

Trace data powers eval spies, monitor rules, debugging, and safety review.

## Error Types

Runtime primitives use structured errors:

- `MissingContextSlot`
- `AmbiguousDatasource`
- `DatasourcePermissionDenied`
- `DatasourceResolutionCycle`
- `DatasourceExecutionFailed`

Each error should include the agent name, missing or failing type, source location when available, and a suggested fix.

## Caching

Initial cache scopes:

- `none`: never cache.
- `run`: reuse within the current agent run.
- `thread`: reuse within a conversation or task thread.

## Security

Datasources are code execution boundaries. Runtime primitives should:

- Require explicit datasource registration.
- Avoid importing arbitrary strings from untrusted contracts.
- Keep secrets out of rendered context.
- Record provenance for all resolved values.
- Allow projects to mark datasource output as sensitive.
