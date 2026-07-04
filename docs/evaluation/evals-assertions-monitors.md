# Evals, Assertions, And Monitors

Contract4Agents treats agent behavior as output plus trace. Evals and monitors must inspect both.

## Categories

- `guard`: safety intent and enforcement metadata available to adapters and host runtimes.
- `assertion`: invariant checked during or after one run.
- `run_spec`: stage-output and trace expectations for a host-owned multi-agent run.
- `.eval`: offline fixture-based test case.
- `monitor`: trace rule that can be run against recorded execution.

These categories should not be collapsed into one vague "test" concept. They operate at different times and have different enforcement power.

## Eval Files

Eval files live outside agent files.

```contract
eval simple_greeting for CustomerGreeter:
    given user_message = UserMessage("Hi, can you help me?")
    given customer_profile = null

    expect output.routed_to == null
    expect output.message contains greeting
    expect trace.not_called(BillingAgent)
    expect trace.not_called(SupportAgent)

eval billing_request for CustomerGreeter:
    given user_message = UserMessage("I was charged twice")
    given customer_profile = CustomerProfile.fixture("active_customer")

    expect trace.called(BillingAgent)
    expect output.routed_to == BillingAgent
```

`.eval` files are allowed to contain one-off inputs, fixtures, and adversarial cases. `.contract` files should not.

## Trace Spies

Trace spies are assertions over trace events.

Initial spy vocabulary:

- `trace.called(Target)`
- `trace.not_called(Target)`
- `trace.called_once(Target)`
- `trace.called_times(Target, n)`
- `trace.called_before(A, B)`
- `trace.called_after(A, B)`
- `trace.max_calls(Target, n)`
- `trace.not_tool_called_by(agent_name, tool_name)`
- `trace.tool_called(tool_name)`
- `trace.hosted_tool_called(openai.web_search)`
- `trace.agent_called(agent_name)`
- `trace.datasource_resolved(type_name)`
- `trace.approval_requested(name)`
- `trace.approval_granted(name)`
- `trace.approval_denied(name)`

`trace.tool_called(...)` checks host-supplied `tool.completed` events by their
normalized `tool` index field. `trace.hosted_tool_called(...)` checks
provider-native hosted-tool events such as `hosted_tool.completed` by the same
field. `trace.not_tool_called_by(...)` checks both host tools and hosted tools
using the normalized `agent` and `tool` index fields. Other typed spies follow
the same pattern: agent spies match `agent`, datasource spies match `datasource`
or produced type, approval spies match the approved tool, and guardrail spies
match `guardrail`. Generic spies such as `trace.called(...)` can still match
these normalized target fields across categories by target name. Use
`trace.contains(...)` for free-text payload searches.

Spies should work for tools, hosted tools, agents, datasources, approvals,
output validation, and guardrail-style trace events.
The Contract4Agents language term is `guard`; `guardrail` is adapter and trace
vocabulary, preserved in trace event names such as `guardrail.rejected` and
spy names such as `trace.guardrail_rejected(...)`.

## Output Expectations

Output expectations inspect structured fields:

```contract
expect output.action == "recommend_refund"
expect output.evidence contains ChargeEvidence
expect output.customer_reply excludes internal_provider_id
expect output conforms RefundDecision
```

The expression language is intentionally small and declarative.

## Semantic Expectations

Some expectations require model judgment:

```contract
expect semantic(customer_reply, "concise and non-technical")
expect semantic(evidence, "supports the recommended action")
```

Semantic expectations should be explicit and separately reported from deterministic checks. A passing deterministic eval with failing semantic judgment should not be hidden.

Semantic evals are in V1 scope. A local run may skip them only when no judge adapter is configured, and that skip must be reported as skipped semantic checks rather than silently passing.

## Assertions

Assertions belong in `.contract` files because they are part of the agent contract.

```contract
assertions = [
    expect(output conforms RefundDecision),
    expect(output.customer_reply excludes internal_provider_id),
    when(trace.called(stripe.create_refund), expect(trace.approval_granted("refund"))),
]
```

Assertions should be invariant-like. They should not define one test fixture.

Host applications can evaluate compiled assertions after a real run by calling
`evaluate_run_assertions(...)` with compiled artifacts, normalized trace events,
and outputs keyed by agent name:

```python
from contract4agents.assertions import evaluate_run_assertions

result = evaluate_run_assertions(
    contract=artifacts,
    trace=trace,
    outputs={"CustomerGreeter": output},
    run_id="run-123",
)
```

The result separates assertion failures from eval failures and monitor
violations. Conditional assertions whose `when(...)` trace condition is false
are reported as skipped. Unsupported assertion syntax fails closed.
Single-run traces can omit `run_id`; multi-run traces must pass it so events
from separate runs cannot satisfy one assertion set.

OpenAI adapter runs that use `run_openai_agent_with_contract(...)` call the same
assertion API after the SDK run completes and record one `assertion.evaluated`
trace event for each assertion check.

## Run Specs

Run specs are project-level expectations for a host-owned sequence of agent
stages. They are evaluated after the host application has collected stage
outputs and emitted a normalized trace:

```python
from contract4agents.assertions import evaluate_run_spec

result = evaluate_run_spec(
    contract=artifacts,
    run_spec="CompendiumResearch",
    trace=trace,
    stage_outputs={"plan": plan, "section_research": sections, "synthesis": synthesis},
    run_id="run-123",
)
```

Stage outputs are validated against declared output schemas and cardinality.
Run spec assertions use trace expressions such as `trace.called_before(...)`,
`trace.max_calls(...)`, and `trace.not_tool_called_by(...)`, plus derived-value
data relations such as `value.synthesis_citation_ids subset_of
value.ledger_cited_ids`. They do not define execution order, branching, retries,
or data transformation; they verify what the host run observed and supplied.

## Guards

Guards describe safety constraints that adapters and host runtimes can enforce at the appropriate boundary.

```contract
guards = [
    forbid(tool.stripe.create_refund unless approved_by_human),
    require(output conforms RefundDecision),
]
```

Compiled artifacts include a guard plan that classifies supported guards into
output-schema enforcement, host-owned approval requirements, and adapter tool
omission. Unsupported parseable guards remain visible as unsupported guard-plan
items; integrations must not claim they are enforced.

## Monitors

Monitors apply to recorded traces.

```contract
monitor refund_without_evidence for BillingAgent:
    severity = "high"
    when trace.tool_called(stripe.create_refund)
    expect trace.contains("charge_evidence")
```

The `for Agent` scope is applied before evaluating both `when` and `expect`.
Events with a different `agent` index field do not satisfy that monitor,
including approval events. Legacy or local fixture events that omit `agent` are
treated as unscoped events, so trace producers should include `agent` on
tool and approval events when agent-specific monitoring matters.

## Trace Event Schema

V1 trace files use the canonical JSONL envelope documented in
[Trace Schema Reference](../reference/trace-schema.md). Each event line has
`schema_version`, `event_id`, `event_type`, `timestamp`, optional index fields
such as `agent` and `tool`, event-specific `data`, and provider metadata.

Known V1 event names are:

- `agent.started`, `agent.completed`, `agent.handoff`
- `tool.requested`, `tool.started`, `tool.allowed`, `tool.denied`, `tool.completed`, `tool.failed`
- `host_tool.requested`, `host_tool.started`, `host_tool.completed`, `host_tool.failed`
- `hosted_tool.requested`, `hosted_tool.started`, `hosted_tool.completed`, `hosted_tool.failed`
- `datasource.started`, `datasource.resolved`, `datasource.failed`
- `approval.requested`, `approval.completed`
- `stage.completed`
- `output.accepted`, `output.rejected`, `output.schema_failed`
- `assertion.evaluated`
- `guardrail.rejected`
- `llm.started`, `llm.completed`

Unknown event names are diagnostic warnings, not fatal load errors. Malformed
envelopes, unsupported schema versions, bad timestamps, non-object `data` or
`provider`, and legacy top-level `type` fields are fatal.

## Eval Runner Behavior

The eval runner should:

1. Compile contracts.
2. Load eval cases.
3. Build input and context fixtures.
4. Run the agent against a controlled runtime.
5. Capture normalized traces.
6. Check deterministic output expectations.
7. Check trace spies.
8. Check compiled agent assertions for the entry agent.
9. Run semantic judgments through a configured judge adapter, or report them as skipped.
10. Report failures with source locations and trace excerpts.

## Monitor Reports

Monitor failures should produce actionable records:

- Agent name.
- Run ID.
- Monitor name.
- Severity.
- Failing condition.
- Trace event excerpt.
- Suggested remediation.

The system should say "contract violation found" rather than implying certainty about all possible behavior.
