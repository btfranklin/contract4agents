# Enforcing Business Policy with Host Tools

An agent can help a customer ask for a refund. It must not decide whether the
company is allowed to offer one.

That distinction matters whenever a rule depends on trusted business data,
changes money or customer entitlements, or must hold under concurrent requests.
For example:

> Do not offer a refund when the customer has received an offer for a similar
> product within the last 30 days.

Contract4Agents makes the boundary explicit, wires the agent to the approved
host capability, and records evidence about the decision. Your application
owns the policy query, transaction, durable write, and any human approval UI.

## The Safe Shape

Use two independent gates:

```text
agent request
  -> optional read-only eligibility check
  -> approval gate
  -> transactional host policy gateway re-checks the rule
  -> durable refund offer, or a typed refusal
```

The final check inside the host policy gateway is the enforcement point. A
model instruction, an evaluator rubric, or an earlier eligibility result is
not enough: any of them can be stale, bypassed, or wrong.

The agent should receive a typed result such as `eligible` or
`ineligible_recent_offer`. Do not let it invent a refund amount, override the
policy result, or write directly to a billing database.

## 1. Declare the Consequential Capability

Start by describing the business action, its typed result, and the fact that it
has a side effect.

```contract
enum RefundDecisionStatus:
    "eligible"
    "ineligible_recent_offer"
    "requires_human_review"
    "issued"

type RefundDecision:
    status: RefundDecisionStatus
    policy_version: string
    reason_code: string

tool refunds.create_offer(product_id: string) -> RefundDecision:
    description = "Create a policy-compliant refund offer for the authenticated customer."
    side_effect = true
```

Grant the capability to the support agent only through the host, and require
approval before it can run:

```contract
agent CustomerSupportAgent(request: CustomerRequest) -> SupportDecision:
    use refunds.create_offer:
        availability = enabled
        authorization = approval_required
        execution = host

    goal = "Resolve the customer's request accurately and within company policy."
    guidance = [
        "Never promise a refund before the refund capability returns an approved result.",
        "Request human review when policy does not authorize a response.",
    ]
```

`approval_required` creates a derived Contract4Agents control. The selected
target must prove that approval occurs before the tool starts, or planning and
assurance report the gap. Approval is still not the policy rule: an approver
must not be able to make an ineligible refund succeed accidentally.

## 2. Bind It to the Policy Gateway

The target binding connects the portable capability name to your application
code. It does not contain the policy itself.

```toml
[targets.openai.tools."refunds.create_offer"]
python = "acme.billing.refund_policy:create_refund_offer"
```

This is the hook. `create_refund_offer` is the one place in the application
that can create this kind of offer. Keep direct database writes and alternate
refund paths behind the same policy service, rather than trusting every caller
to repeat the rule.

## 3. Enforce the Rule in a Transaction

The policy gateway must use trusted context and re-check the rule immediately
before the state-changing write. The model should not supply a customer ID; the
application derives it from the authenticated request or session.

The following is illustrative Python. Use your application's database and
transaction APIs.

```python
def create_refund_offer(product_id: str) -> RefundDecision:
    customer_id = authenticated_request.customer_id

    with database.transaction():
        database.lock_customer_refund_history(customer_id)

        if has_similar_refund_offer_in_last_30_days(
            customer_id=customer_id,
            product_id=product_id,
            as_of=clock.now(),
        ):
            return RefundDecision(
                status="ineligible_recent_offer",
                policy_version="refund-policy-2026-07",
                reason_code="recent_similar_refund_offer",
            )

        offer = create_and_record_refund_offer(
            customer_id=customer_id,
            product_id=product_id,
        )
        return RefundDecision(
            status="issued",
            policy_version="refund-policy-2026-07",
            reason_code="eligible",
        )
```

This code needs to solve the real business problem:

- Use the product catalog or policy service to define *similar product*; do not
  ask the model to infer it.
- Lock or otherwise serialize the customer's relevant refund history, so two
  simultaneous requests cannot both pass the 30-day check.
- Use an idempotency key and a durable offer record, so a retry cannot create a
  duplicate offer.
- Re-check the rule at the write, even if a read-only eligibility tool checked
  it earlier. That avoids a time-of-check/time-of-use gap.

If the company permits exceptions, make them a separate, role-restricted host
workflow with its own audit record. Do not make an ordinary approval a hidden
policy bypass.

## 4. Record the Business Decision as Evidence

The policy service can emit a host-attested control result for the exact rule:

```contract
control recent_refund_offer_policy for CustomerSupportAgent:
    severity = high
    required = true
    audience = [host, evaluator, reviewer]
    assessment = host_attested
    expected_evidence = [control.assessed]
```

When the policy gateway reaches its decision, the host records a normalized
`control.assessed` event associated with this control. The event should state
`passed`, `violated`, or `unverified`, include a useful reason code and policy
version, and refer to the durable policy-decision record.

Contract4Agents can then include that evidence in assessment results and an
assurance bundle. It cannot make a dishonest or defective policy service safe;
the trusted host boundary and its audit trail remain the source of enforcement.

## 5. Evaluate the Whole Path

Write eval scenarios for the policy outcomes the company cares about:

```contract
eval refuses_recent_similar_refund for CustomerSupportAgent:
    given request = CustomerRequest.fixture("recent_similar_refund")
    expect output conforms SupportDecision

eval requests_approval_for_eligible_refund for CustomerSupportAgent:
    given request = CustomerRequest.fixture("eligible_refund")
    expect trace.approval_requested(refunds.create_offer)
```

Use a live, replayed, or application-integrated eval provider to exercise the
real policy gateway. Deterministic `eval-data.json` cases are also useful for
testing the contract, expected trace evidence, and result handling without
provider credentials.

Include at least these cases:

- a recent refund offer for the same product;
- a recent offer for a product that policy classifies as similar;
- an old offer, outside the 30-day period;
- a denied approval;
- two concurrent requests for the same customer; and
- a retry after an uncertain outcome.

A quality rubric can assess whether the agent explains the refusal clearly and
uses approved language. It is not the enforcement mechanism for eligibility.

## What Contract4Agents Does—and Does Not—Do

Contract4Agents does:

- declare the action, typed result, grant, and approval requirement;
- connect the action to the named host implementation through target bindings;
- derive approval controls and validate the target's enforcement plan;
- bind traces and host evidence to stable contract and plan identities; and
- assess and package the evidence for eval, release, or incident review.

Contract4Agents does not:

- query refund history or classify similar products;
- lock database rows, write refund offers, or provide idempotency;
- make a human approval decision; or
- turn a model instruction into an enforced business rule.

Put the rule in the host policy gateway. Use Contract4Agents to make that
gateway visible, correctly connected, reviewable, and evidenced.

## Next Steps

- Read [Using Contract4Agents in an Application](using-contract4agents-with-an-agent-app.md)
  for target bindings, approvals, traces, and assurance.
- Read [Evals, Controls, and Assurance](../evaluation/evals-controls-assurance.md)
  for campaign and evidence semantics.
- Read [Capture and Assure a Run](trace-and-assure.md) to package a real run
  for review.
