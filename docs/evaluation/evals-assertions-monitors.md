# Evals, Assertions, And Monitors

Contract4Agents treats agent behavior as output plus trace. Evals and monitors must inspect both.

## Categories

- `guard`: safety intent and enforcement metadata available to adapters and host runtimes.
- `assertion`: invariant checked during or after one run.
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
- `trace.tool_called(tool_name)`
- `trace.agent_called(agent_name)`
- `trace.datasource_resolved(type_name)`
- `trace.approval_requested(name)`
- `trace.approval_granted(name)`
- `trace.approval_denied(name)`

Spies should work for tools, agents, datasources, approvals, output validation, and guardrail-style trace events.
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

## Guards

Guards describe safety constraints that adapters and host runtimes can enforce at the appropriate boundary.

```contract
guards = [
    forbid(tool.stripe.create_refund unless approved_by_human),
    require(output conforms RefundDecision),
]
```

## Monitors

Monitors apply to recorded traces.

```contract
monitor refund_without_evidence for BillingAgent:
    severity = "high"
    when trace.tool_called(stripe.create_refund)
    expect trace.contains("charge_evidence")
```

## Trace Event Schema

V1 emits normalized events such as:

- `datasource.started`
- `datasource.resolved`
- `datasource.failed`
- `tool.started`
- `tool.requested`
- `tool.allowed`
- `tool.denied`
- `tool.completed`
- `tool.failed`
- `llm.started`
- `llm.completed`
- `agent.started`
- `agent.handoff`
- `agent.completed`
- `approval.requested`
- `approval.completed`
- `guardrail.rejected`

Provider-specific trace data can be attached as metadata, but the normalized event names should be stable.

## Eval Runner Behavior

The eval runner should:

1. Compile contracts.
2. Load eval cases.
3. Build input and context fixtures.
4. Run the agent against a controlled runtime.
5. Capture normalized traces.
6. Check deterministic output expectations.
7. Check trace spies.
8. Run semantic judgments through a configured judge adapter, or report them as skipped.
9. Report failures with source locations and trace excerpts.

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
