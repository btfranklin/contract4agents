# Healthcare Workflows: A Safety Pattern, Not a Compliance Recipe

This is a docs-only design pattern for organizations evaluating Contract4Agents
for a healthcare workflow. It uses no patient data, credentials, or production
configuration. It is not legal advice and does not make a system HIPAA
compliant. A provider's privacy officer, security team, counsel, and covered
vendors must determine the obligations that apply to a real deployment.

The question this pattern answers is narrower:

> We have a complex healthcare workflow. Where can Contract4Agents help, and
> where must our existing healthcare systems enforce the safety boundary?

Contract4Agents can make an agent graph, its permitted capabilities, its
expected evidence, and its assurance results explicit. It does not replace
identity and access management, EHR controls, privacy operations, clinical
governance, vendor agreements, or incident response.

For background on the regulatory safeguards that remain outside the contract,
see HHS's [Security Rule summary](https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html),
[minimum-necessary guidance](https://www.hhs.gov/hipaa/for-professionals/privacy/guidance/minimum-necessary-requirement/index.html),
and [cloud-computing guidance](https://www.hhs.gov/hipaa/for-professionals/special-topics/health-information-technology/cloud-computing/index.html).

## When Contract4Agents Is a Good Fit

It can help when an organization already has trusted systems that determine:

- who is making a request and whether they are authorized;
- which patient, encounter, plan, or appointment the request concerns;
- the permitted purpose and minimum data available for that work;
- which changes an authorized workflow may make; and
- how access and decisions are durably audited.

Then Contract4Agents can describe the bounded work agents may do around those
systems: which host capabilities they may call, which context each may receive,
when approval is required, and which evidence reviewers expect after a run.

It is not a fit if the plan is to give an agent broad chart access and rely on
prompts, a semantic judge, or a later trace review to prevent misuse. Those are
not access controls.

## A Bounded Six-Agent Workflow

Imagine a provider handling insurance, record-quality, and scheduling work.
The agents should be narrow, and none of them should make clinical decisions or
have unrestricted patient-record access.

| Agent | Bounded responsibility | Safe output |
| --- | --- | --- |
| Patient Access Coordinator | Route an already authenticated request to the allowed workflow. | A route or escalation code, not a patient chart. |
| Coverage Verifier | Ask the payer or eligibility service for coverage and authorization status. | An eligibility disposition and reason code. |
| Record Discrepancy Reviewer | Compare named fields for one patient and encounter. | Discrepancy codes and evidence references. |
| Appointment Coordinator | Ask a scheduling service for eligible available slots. | Filtered slot choices, never another patient's details. |
| Prior-Authorization Assembler | Prepare a draft packet from an approved, narrow document set. | A draft identifier and missing-item list. |
| Clinical Escalation Coordinator | Send uncertain, high-risk, or clinically meaningful cases to an authorized human workflow. | An escalation record, not a diagnosis. |

The Patient Access Coordinator should not decide access rights. A deterministic,
host-owned Patient Access Gateway does that before the agent system begins.

## The Safety Boundary

```text
authenticated request
  -> host Patient Access Gateway
  -> short-lived, purpose-bound scope
  -> bounded agent and host-tool calls
  -> authorized healthcare systems make decisions or writes
  -> minimized trace and assurance evidence
```

The Patient Access Gateway can create a scope containing the authenticated
actor, the single patient or encounter, the allowed purpose, the permitted data
classes, a tenant or organization boundary, an expiry, and whether mutation is
allowed. It validates that scope at every EHR, payer, scheduling, or messaging
call. The model does not supply a patient ID and receive an arbitrary record.

For uses and disclosures to which the HIPAA minimum-necessary standard applies,
the provider should design the scope and service response around the least data
needed for that purpose. The exact applicability and exceptions are a privacy
and legal determination, not a model decision.

## What the Contract Describes

For example, an appointment agent can receive a narrow capability rather than a
calendar database:

```contract
tool scheduling.find_permitted_slots(
    appointment_request_id: string
) -> PermittedSlots:
    description = "Return slots the host has approved for this authenticated request."
    side_effect = false

agent AppointmentCoordinator(
    request: AppointmentRequest
) -> AppointmentRecommendation:
    use scheduling.find_permitted_slots:
        availability = enabled
        authorization = preapproved
        execution = host

    goal = "Recommend an eligible appointment slot without exposing other patients' information."
```

The target binding points `scheduling.find_permitted_slots` to a provider-owned
Python service. That service derives identity and purpose from trusted context,
applies scheduling policy, and returns only safe slot data. The agent never
receives the complete calendar or another patient's information.

When an action changes a record, sends a message, or commits an appointment,
declare a separate side-effecting capability with explicit authorization. The
host service re-checks the current access scope and business rules at the write.
An approval gate is an additional control; it must not become a policy bypass.

## Division of Responsibility

| Contract4Agents makes visible and assessable | The provider and its systems must enforce |
| --- | --- |
| Agent signatures, typed outputs, tool grants, and declared context origins | Authentication, role and relationship checks, consent/authorization, and purpose-of-use decisions |
| Which agent can call a named host capability | EHR, payer, scheduling, and messaging authorization at every request |
| Approval requirements and expected evidence | Actual approval decisions, UI, role restrictions, and final transactional re-checks |
| Isolation and audience requirements in the reviewed plan | The environment, tenant separation, network/storage controls, key management, retention, and backup policy |
| Normalized traces and assurance results | Audit-log durability, incident response, breach assessment, and review processes |
| Eval scenarios and quality rubrics | Clinical review, policy governance, and the decision whether a workflow may operate |

The second column is not optional. In particular, model-facing guidance such as
“do not disclose patient information” is helpful instruction, not enforced
access control.

## Safeguards to Design Deliberately

- **One patient and one purpose per run.** Do not use shared conversation
  history, caches, summaries, or retrieval results to bridge patient or tenant
  boundaries. Require explicit mappings whenever an agent receives context.
- **Minimum safe views.** Coverage work should not receive clinical notes.
  Scheduling should return allowed slots, not raw calendars. Record comparison
  should receive named fields, not a full chart.
- **Trusted writes only.** Agents can propose or prepare drafts. An authorized
  host service performs appointment changes, record updates, submissions, and
  communications after a final policy check.
- **Treat records as untrusted model input.** A clinical note, uploaded file, or
  payer message can contain prompt-injection text. It must not grant tool
  access, broaden the patient scope, or change host policy.
- **Keep ePHI out of unsafe observability.** Traces, eval fixtures, prompts,
  judge inputs, error reports, analytics, and support tickets need the same
  classification and redaction discipline as the primary workflow. Use
  synthetic data for ordinary development and evals.
- **Fail closed and escalate.** Missing identity, expired scope, ambiguous
  authorization, unavailable policy service, or incomplete evidence produces a
  refusal, an escalation, or an `unverified` result—not a best guess.
- **Review cloud and vendor boundaries.** A cloud service that creates,
  receives, maintains, or transmits ePHI for the provider can create business
  associate obligations. Contract4Agents does not establish those agreements or
  configure the vendor environment.

## Useful Evaluations

Before a team considers production use, it should test boundaries rather than
only happy-path answers. Useful synthetic or controlled cases include:

- a staff member requesting the wrong patient's record;
- a patient attempting to obtain another patient's appointment details;
- an expired or purpose-mismatched access scope;
- a prompt-injection instruction embedded in a clinical note;
- a model request for a capability the contract does not grant;
- a scheduling or EHR write attempted after approval but before the final host
  policy re-check; and
- an unavailable policy service, judge, or trace channel.

The intended safe outcome is often a refusal, escalation, or `unverified`
result. A system that makes a plausible answer while its evidence is incomplete
is not demonstrating a safe workflow.

## Deciding Whether to Proceed

Contract4Agents is worth considering when it adds clarity around an existing,
governed healthcare workflow: it can show reviewers what an agent can access,
how a host policy service is connected, and what evidence supports a result.

Do not proceed on the assumption that a contract, an eval score, or a provider
integration itself supplies HIPAA compliance. First establish the provider's
governance, authorized data boundaries, vendor posture, and human accountability.
Then use Contract4Agents as the contract-and-evidence layer around that trusted
foundation.

## Next Steps

- Read [Enforcing Business Policy with Host Tools](enforcing-business-policy.md)
  for a transactional policy-gateway example.
- Read [Using Contract4Agents in an Application](using-contract4agents-with-an-agent-app.md)
  for target bindings, approvals, and host responsibilities.
- Read [Evals, Controls, and Assurance](../evaluation/evals-controls-assurance.md)
  for campaign and assurance semantics.
