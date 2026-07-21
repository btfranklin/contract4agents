# Vendor and Payment Changes: A Safety Pattern, Not a Payments System

This is a docs-only design pattern for organizations evaluating Contract4Agents
for vendor onboarding, invoice review, or payment-change work. It uses no
financial data, credentials, or production configuration. It is not financial,
legal, security, or compliance advice.

The question this pattern answers is practical:

> Can we use agents to reduce vendor and accounts-payable work without giving a
> model authority to change bank details or release money?

Yes, when the agent system is a bounded analyst and router around the company's
existing vendor-master, approval, fraud, and payment controls. Contract4Agents
can make those boundaries and their evidence explicit. It is not a payment
engine, identity-verification service, or fraud-control system.

Business-email-compromise fraud often relies on spoofed or compromised accounts
to redirect payments. The FBI recommends independently verifying changes using
known contact channels rather than information supplied in a suspicious message.
See the [FBI IC3 business-email-compromise guidance](https://www.ic3.gov/CrimeInfo/BEC).

## When Contract4Agents Is a Good Fit

It can help when the company already has trusted systems that determine:

- the canonical vendor identity and approved payment destination;
- who may request, review, approve, or release a payment;
- segregation-of-duties and approval thresholds;
- how invoice, contract, purchase-order, and receipt data are reconciled; and
- how payment changes and approvals are durably audited.

Then contracts can define the limited work that agents do around those systems:
extracting facts, finding mismatches, preparing a review case, and routing it
to the right authorized people.

It is not a fit if the intended design lets an agent treat an email as proof of
identity, modify a vendor master record, set banking instructions, or release a
payment. Those must remain host-enforced operations.

## A Bounded Workflow

```text
invoice or vendor-change request
  -> host identifies the vendor and validates request origin
  -> bounded agents extract and reconcile facts
  -> host fraud and policy services decide whether a review is required
  -> authorized humans complete independent verification and approvals
  -> transactional payment service releases funds, or a case is refused
```

The agents may help a reviewer see the important facts faster. They do not
convert an untrusted request into a trusted payment instruction.

## Five Agents, One Non-Agent Payment Boundary

| Agent | Bounded responsibility | Safe output |
| --- | --- | --- |
| Vendor Intake Analyst | Extract structured facts from an onboarding packet or change request. | Candidate fields and missing-information codes. |
| Vendor Identity Reviewer | Compare supplied identifiers with the existing vendor master and trusted registries. | Match, mismatch, or human-review disposition. |
| Contract and PO Matcher | Reconcile invoice lines against approved purchase orders, contracts, and receipts. | Variance codes and evidence references. |
| Invoice Risk Triage Agent | Identify duplicate, unusual, or policy-sensitive invoices for review. | Risk indicators and a review recommendation. |
| Approval Coordinator | Create a structured review case and route it to the required approvers. | Case ID and required approval roles. |

The payment-release service is deliberately not an agent. No agent receives a
`payments.release` capability. The only system that can release funds is the
company's existing transactional payment service after it verifies policy,
segregation of duties, and the current vendor-master record.

## What the Contract Describes

The contract can express the safe analysis and routing capabilities. For
example:

```contract
tool vendor_records.lookup(vendor_request_id: string) -> VendorMatch:
    description = "Compare a submitted vendor request with the host-approved vendor record."
    side_effect = false

tool payment_controls.open_review_case(
    vendor_request_id: string,
    reason_code: string
) -> PaymentReviewCase:
    description = "Open an approval workflow case without changing vendor payment instructions."
    side_effect = true

agent VendorIdentityReviewer(
    request: VendorChangeRequest
) -> VendorReview:
    use vendor_records.lookup:
        availability = enabled
        authorization = preapproved
        execution = host

    use payment_controls.open_review_case:
        availability = enabled
        authorization = approval_required
        execution = host

    goal = "Identify mismatches and route uncertain vendor changes for independent review."
```

There is intentionally no `payments.release` tool in this agent's contract.
The target binding connects the permitted capabilities to normal company code:

```toml
[targets.openai.tools."vendor_records.lookup"]
python = "acme.procurement.vendor_records:lookup"

[targets.openai.tools."payment_controls.open_review_case"]
python = "acme.payments.review_cases:open_case"
```

## The Host-Enforced Payment Boundary

The host payment workflow, not the agent, must make the final decision. At a
minimum, it should independently:

1. Resolve the vendor through the canonical vendor master, rather than from
   values extracted from an email or attachment.
2. Require out-of-band confirmation through a previously known contact channel
   for changes to payment instructions.
3. Enforce approval roles, thresholds, and segregation of duties.
4. Re-check the active vendor record and all policy conditions immediately
   before a payment-changing write.
5. Record an idempotent, durable decision so retries cannot duplicate a change
   or payment.

The independent verification step is particularly important. A realistic
fraudulent message can contain an authentic-looking signature, invoice, tax
form, and urgency. Those are signals to investigate, not authorization to pay.

## Evidence and Review

Contract4Agents can attach stable identities to the agent, capability, grant,
approval, and result. The host should add the evidence that matters for the
business decision without copying bank-account details into broad traces:

- vendor-master record reference and version;
- independent-verification record reference;
- policy and approval-rule version;
- approving roles and timestamps;
- review-case ID; and
- final status such as `approved`, `denied`, `held_for_review`, or
  `unverified`.

An `approval_required` grant provides a derived control that checks whether
approval was recorded before the named tool started. That does not prove the
vendor change was legitimate; the host verification record and payment service
remain the enforcement evidence.

## Useful Evaluations

Use synthetic data and test the boundaries, not only invoice extraction:

- a request from a lookalike vendor email domain;
- a bank-account change that conflicts with the vendor master;
- an urgent executive request to bypass normal approval;
- duplicate invoices with small formatting differences;
- a vendor change where the only confirmation number came from the message;
- an approver attempting to approve their own exception;
- a retry after an ambiguous payment-service response; and
- an unavailable vendor-master, fraud, or approval service.

The safe expected result is often a held case, refusal, escalation, or
`unverified` result. A polished model explanation is not evidence that the
payment instruction is safe.

## Division of Responsibility

| Contract4Agents makes visible and assessable | The company must enforce |
| --- | --- |
| Narrow agent roles, typed outputs, and declared capabilities | Vendor identity, source validation, and master-data integrity |
| Which agent can open a review case | Who may approve, change payment instructions, or release funds |
| Approval requirements and expected evidence | Independent verification, segregation of duties, thresholds, and payment policy |
| Trace identities and assurance results | Durable audit records, fraud response, and payment reconciliation |
| Eval scenarios and quality rubrics | The decision to release, hold, reverse, or investigate a transaction |

## Deciding Whether to Proceed

Use Contract4Agents when it makes an existing, governed finance workflow easier
to understand and review. It can show that agents are confined to extraction,
reconciliation, and routing while trusted services retain authority over money.

Do not proceed if the proposed value depends on an agent autonomously accepting
vendor banking changes or releasing funds. First establish the verified
vendor-master, independent confirmation, authorization, and transactional
payment controls. Then use Contract4Agents as the contract-and-evidence layer
around them.

## Next Steps

- Read [Enforcing Business Policy with Host Tools](enforcing-business-policy.md)
  for the general host-policy-gateway pattern.
- Read [Healthcare Workflows: A Safety Pattern](healthcare-safety-pattern.md)
  for the same boundary in a regulated-data setting.
- Read [Using Contract4Agents in an Application](using-contract4agents-with-an-agent-app.md)
  for target bindings, approvals, and host responsibilities.
