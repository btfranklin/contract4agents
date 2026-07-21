# Using Contract4Agents in an Application

Contract4Agents makes the portable contract the maintained agent configuration.
Application code supplies implementations and deterministic business workflow;
it does not re-declare agents, prompts, permissions, output types, or ordinary
handoffs.

## Ownership Boundary

| Contract-owned | Target-binding-owned | Host-owned |
| --- | --- | --- |
| Types and agent signatures | Tool/datasource/context locators | Credentials and secrets |
| Shared capability meaning | Provider-native capability selection | Approval UI and decisions |
| Per-agent grants | Models and provider options | Persistence and external services |
| Goals and guidance | Runtime environment provider | Deterministic workflow |
| Composition edges | Remote endpoints | Deployment and trace storage |
| Controls, quality, evals | Target-specific caveats | Application-specific validation |

Generated IR, instructions, code, plans, runtime graphs, traces, and assurance
bundles are derived artifacts.

## Build-Time Workflow

Run source and generated-artifact checks in CI:

```bash
contract4agents check agent_contracts
contract4agents compile agent_contracts --out .contract/build
contract4agents compile agent_contracts --out .contract/build --check
contract4agents plan agent_contracts --target openai --profile production \
  --out .contract/build/production-plan.json
```

If application code imports generated source, run `generate` with each target
it consumes and protect that machine-owned source directory with the same
target selection in `generate --check`. For example:

```bash
contract4agents generate agent_contracts --target python --out src/generated
contract4agents generate agent_contracts --target python --out src/generated --check
```

Approve the contract digest and plan digest that correspond to the release.
Model changes are target-profile changes; portable behavior changes are contract
changes. Both are visible in semantic diffs.

## Materialization at Startup

```python
from contract4agents import materialize

system = materialize(
    "agent_contracts",
    target="openai",
    profile="production",
)

triage_agent = system.agents["TriageAgent"]
plan = system.plan
```

The materializer:

1. compiles and validates the portable project;
2. loads the selected target bindings;
3. validates binding coverage and inspectable callable shapes;
4. produces an immutable provider-neutral plan;
5. generates native structured-output types;
6. builds all agent shells;
7. resolves tools, approvals, delegations, and handoffs across the graph;
8. validates the native objects against the plan.

Required unsupported or degraded mappings stop this process. There is no silent
fallback to a weaker interpretation.

## Composition and Workflow

Named `delegate` and `handoff` edges are model-selectable graph relationships:

```contract
composition investigate from TriageAgent to Investigator:
    mode = delegate
    description = "Gather focused evidence when the request needs investigation."
    history = none
    map request = input.request
```

The OpenAI materializer turns delegation into an agent-as-tool and handoff into
a native SDK handoff. Host code does not maintain parallel agent-tool or handoff
registries.

Deterministic branches, loops, retries, checkpoints, stage ordering, and data
transforms remain ordinary application code. A `run_spec` can verify that
workflow's stages, cardinalities, typed outputs, derived values, and trace
relations without becoming a switching language. The host supplies explicit
workflow-completeness evidence to `assess_run_spec(...)`; control assessment
remains a separate operation.

## Context and Datasources

Invocation parameters, edge mappings, previous-stage values, datasource
resolutions, and named external context each retain distinct provenance.

```contract
external_context authenticated_account -> AccountProfile:
    description = "The account selected by authenticated host context."
    sensitivity = confidential
    render = markdown

datasource account.history(account: AccountProfile) -> AccountHistory:
    description = "Resolve recent account activity."
    render = markdown
    cache = run

agent SupportAgent(request: SupportRequest) -> SupportReply:
    context account: AccountProfile from external authenticated_account
    context history: AccountHistory from datasource account.history:
        map account = context.account
```

Only the target binding contains the Python, TypeScript, remote, or provider
locator. Sensitive values remain structured until an audience-safe renderer
creates model-visible content. Trace events retain provenance and redaction
metadata rather than copying secrets into generic payloads.

## Approvals and Controls

Declare an approval once on the grant:

```contract
use billing.issue_credit:
    availability = enabled
    authorization = approval_required
    execution = host
```

This creates a derived runtime control, expected approval trace events, and
assurance evidence requirements. The OpenAI materializer configures the native
tool approval mechanism. The host supplies the actual approval decision and UI.

### Approval Is Not Policy Enforcement

An approval gate confirms that an authorized person or system allowed a tool
call. It does not implement the business rule governing that call. Put rules
such as refund eligibility, pricing limits, or entitlement changes in the
host-owned, transactional tool implementation, then record its decision as
evidence. [Enforcing Business Policy with Host Tools](enforcing-business-policy.md)
walks through a 30-day refund-offer rule end to end.

Explicit controls are appropriate when the rule is not already derivable:

```contract
control evidence_before_credit for SupportAgent:
    severity = high
    required = true
    audience = [host, evaluator, reviewer]
    assessment = post_run
    when = trace.tool_called(billing.issue_credit)
    require = trace.tool_called(account.lookup)
```

If the selected target cannot implement a required assessment, planning fails.

## Isolation

Do not use “isolated” as a blanket claim. Profiles declare independent context,
capability, state, filesystem, network, secret, and return-channel requirements.

In-process materialization can enforce fresh context, declared-capability
allowlisting, fresh state, and final-output-only return. Filesystem or network
isolation requires a target environment provider that enforces those boundaries.
A required unsupported dimension blocks materialization and is recorded in the
plan rather than being treated as best effort.

## Contract-Bound Traces

Every normalized event carries:

- trace schema version, run ID, thread ID, event ID, and parent relationship;
- contract and plan digests;
- stable agent, capability, grant, and control IDs;
- provider-native run/span/request correlation;
- evidence references and provenance;
- redaction state and audience rules.

Keep provider-native traces in their existing platform. Correlate or import
them into the normalized schema for portable assessment, and export normalized
events through the OpenTelemetry integration when useful. Contract4Agents does
not require its own trace backend.

## Evals and Production Assessment

Use `.eval` scenarios and a test profile for controlled campaigns. A campaign
provider may use deterministic files, replayed traces, or a live application.
The plan supplies the expected runtime inventory, so there is no separate
hand-maintained description of agents and permissions.

For production assessment:

1. load and validate a normalized trace plus its versioned, exact-frontier
   closure manifest;
2. validate event-family coverage and identity-bound closure against the reviewed plan;
3. call the shared control assessor with the matching `TraceClosureEvidence`;
4. store or export the results with their contract and plan digests;
5. treat incomplete evidence as `unverified`.

For crash recovery, persist the trace and closure returned by one
`session.snapshot()` call, then resume a session with that exact pair. New SDK
work uses new attempt identity linked by `retry_of`; prior attempts remain
immutable audit evidence. Contract4Agents validates and conservatively combines
the evidence, while the application still owns transactional persistence,
workflow state, and the decision to retry or fail recovery.

A continuous monitoring service can repeat this process whenever a complete
trace arrives. Contract4Agents performs the assessment; the surrounding
application or observability platform owns the continuous watch.

Operational controls cover cost, latency, retry, volume, and cross-run rules.
They do not duplicate behavioral controls.

## Release Assurance

For a release or incident review, assemble an assurance bundle containing the
approved canonical IR and plan, normalized traces, trace closure, trace-evidence
and control results, eval campaign summaries, and semantic diffs. Verify the
bundle's digest references before review.

A useful release gate asks:

- Did portable semantics change?
- Did any agent gain a capability or weaker authorization?
- Did context exposure or audience visibility expand?
- Did model selection or a target mechanism change?
- Did any required guarantee degrade or become unsupported?
- Are all required control results passed, or are some violated/unverified?
- Did eval coverage or statistical performance regress?

That evidence supports compliance and high-reliability review. It does not
replace the organization's risk decision or certify compliance by itself.
