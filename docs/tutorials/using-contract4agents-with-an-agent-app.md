# Using Contract4Agents in an Application

Contract4Agents makes the portable contract the maintained agent configuration.
Application code supplies implementations and deterministic business workflow;
it does not need to re-declare agents, prompts, permissions, output types, or ordinary
handoffs.

Start with the generated artifacts your application needs. Add trace capture,
evals, and assurance when you want to check behavior against the declared
expectations. Contract4Agents checks what it generates and provides integration
hooks; your application owns execution and operational decisions.

For a complete runnable example, start with
[First Contract Project](first-contract-project.md). This guide explains the
integration choices as your application grows. Code fragments use illustrative
agent and type names; adapt them to your contract.

The commands below use the first tutorial's package layout, with contracts in
`your_app/agent_contracts`. Run them from the directory that contains
`your_app/`. Keep the contracts within the application package when bindings
refer to that package's Python tools.

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
pdm run contract4agents check your_app/agent_contracts
pdm run contract4agents compile your_app/agent_contracts --out .contract/build
pdm run contract4agents compile your_app/agent_contracts --out .contract/build --check
pdm run contract4agents plan your_app/agent_contracts --target openai --profile production \
  --out .contract/build/production-plan.json
```

If application code imports generated source, run `generate` with each target
it consumes and protect that machine-owned source directory with the same
target selection in `generate --check`. For example:

```bash
pdm run contract4agents generate your_app/agent_contracts --target python --out src/generated
pdm run contract4agents generate your_app/agent_contracts --target python --out src/generated --check
```

The generated Python package exposes machine-readable ownership metadata. The
generated TypeScript schema module provides equivalent runtime exports.

For release reviews, you can record the contract and plan digests.
Model changes are target-profile changes; portable behavior changes are contract
changes. Both are visible in semantic diffs.

## Materialization at Startup

```python
from contract4agents import materialize

system = materialize(
    "your_app/agent_contracts",
    target="openai",
    profile="production",
)

triage_agent = system.agents["TriageAgent"]
input_type = system.input_type_for_agent(triage_agent)
artifacts = system.artifacts
plan = system.plan
input_types = system.agent_input_types
structural_types = system.structural_output_types
materialization_evidence = system.graph.validation.to_json()
```

If the application imports generated Python models, compare their digest with
the exact compiler artifacts returned above:

```python
import generated_contracts

if (
    generated_contracts.__contract4agents_contract_digest__
    != system.artifacts.contract_digest
):
    raise RuntimeError("Generated contract models are stale")
```

Use the import name that the application assigns to its generated package. The
generated TypeScript schema module provides the equivalent
`contract4agentsContractDigest` and `contract4agentsCodegenVersion` exports.

`materialize()` compiles the contract, checks the selected bindings, and builds
the SDK agents with their tools, types, and declared relationships. It checks
the resulting configuration against the plan and returns both the agents and
the artifacts used to construct them.

Use `system.artifacts` when a run, eval, trace, or assurance workflow needs the
compiled IR or compiler artifact digests. This keeps those consumers joined to
the exact compilation used to construct the native graph; do not compile the
same project a second time at startup.

Required unsupported or degraded mappings stop this process. There is no silent
fallback to a weaker interpretation.

## Root-Agent Inputs

An agent signature is the runtime input contract for that agent. Validate and
serialize the input before the host calls its SDK runner:

```python
triage_agent = system.agents["TriageAgent"]
run_input = system.serialize_input_for_agent(
    triage_agent,
    {"request": request_data},
)
```

`system.validate_input_for_agent(...)` returns the validated Pydantic value when
the host needs it before serialization. `system.input_type_for_agent(...)`
returns the strict type for the selected agent. `system.agent_input_types`
keeps the name-based map for enumeration and inspection. Invalid scalar
coercions, missing fields, extra fields, and input for an agent with no
parameters fail before a provider request starts.

## Structural Output and Domain Validation

The generated output types enforce the portable contract structure. They do not
import application Pydantic models and do not run application validators inside
a provider SDK output parser. The host runs business rules after the SDK returns
the structural output.

The generated JSON Schema describes the declared constraints; generated
Pydantic and Zod types validate values against them. See the
[language reference](../language/contract-language.md) for string, list, and
datetime rules.

Use `system.structural_output_types` when host code needs the generated types.
Keep prose limits, graph completeness, database checks, and other application
rules in a separate host validation step. This order keeps provider usage and
structural failures visible even when an application rule rejects the result.

If you capture traces, record application validation separately from structural
validation. A result can have the correct fields and still fail a business
rule. See [Evals, Controls, and Assurance](../evaluation/evals-controls-assurance.md)
for these separate assessment phases.

## Composition and Workflow

Named `delegate` and `handoff` edges are model-selectable graph relationships:

```contract
composition investigate from TriageAgent to Investigator:
    mode = delegate
    description = "Gather focused evidence when the request needs investigation."
    history = none
    map request = input.request
```

The selected adapter maps composition only when its semantics match. OpenAI
supports native handoffs and agent tools; Strands and Google ADK expose typed
`history = none` delegation as tools but report contract handoffs unsupported.
Host code does not maintain parallel agent-tool or handoff registries.

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
assurance evidence requirements. Each built-in materializer configures its
supported native tool-approval mechanism. The host supplies the actual approval
decision, UI, pause/resume handling, and recovery policy.

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

## Optional: Traces and Assessment

Normalized traces connect observed events to the contract, plan, and SDK run.
They let you assess declared expectations without maintaining a separate
description of the agents and tools.

Keep provider-native traces in their existing platform. Correlate or import
them into the normalized schema for portable assessment, and export normalized
events through the OpenTelemetry integration when useful. Contract4Agents does
not require its own trace backend.

Use `.eval` scenarios and a test profile for controlled campaigns. A campaign
provider may use deterministic files, replayed traces, or a live application.
The plan supplies the expected runtime inventory, so there is no separate
hand-maintained description of agents and permissions.

Assessment needs both the events and evidence of capture coverage. Incomplete
evidence produces `unverified`, not a pass. Follow
[Capture and Assure a Run](trace-and-assure.md) for the complete recording,
assessment, and recovery examples.

## Optional: Release Review

For a release or incident review, assemble an assurance bundle containing the
approved canonical IR and plan, `system.graph.validation` evidence, normalized
traces, trace closure, trace-evidence and control results, eval campaign
summaries, and semantic diffs. Verify the bundle's digest references before
review.

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
