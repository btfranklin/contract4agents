# Semantic Model

Status: accepted implementation specification for the contract-first product.
This document is the authority for the ownership model and the decisions
that must remain consistent across the language, IR, targets, runtime, traces,
evals, and assurance system.

## Product lifecycle

Contract4Agents follows one lifecycle:

```text
Declare -> Compile -> Plan -> Materialize -> Run -> Trace -> Assure
```

- **Declare:** `.contract` and `.eval` files define portable agent semantics.
- **Compile:** source becomes a versioned canonical intermediate
  representation, schemas, instructions, and provider-neutral checks.
- **Plan:** a target and profile resolve models, implementation bindings,
  provider capabilities, enforcement mechanisms, host obligations, and
  expected event types.
- **Materialize:** a target adapter constructs framework-native runtime objects
  and verifies them against the immutable plan.
- **Run:** the materialized system executes with host-owned tools, data,
  persistence, and deterministic workflow.
- **Trace:** normalized events retain contract and plan identity while
  correlating provider-native evidence.
- **Assure:** controls and evals compare declared, materialized, and observed
  truth and report `passed`, `violated`, or `unverified`.

The contract is the canonical semantic source. Target bindings are canonical
only for target-specific implementation choices. Plans, generated code,
instructions, assessment results, and assurance bundles are derived artifacts.

## Stable terminology

The following terms have one meaning throughout the language, IR, plan,
runtime, trace, and assurance layers:

- **Capability:** a named callable interface, such as a tool or datasource.
- **Grant:** the relationship allowing one agent to use one capability.
- **Composition edge:** a named model-selectable transition from one agent to
  another.
- **Handoff:** an edge that transfers control to the target agent.
- **Delegation:** an edge that runs the target agent and returns its typed result
  to the source agent.
- **Guidance:** model-visible behavioral instruction. Guidance is not an
  enforcement claim.
- **Control:** a named requirement with an audience, severity, requiredness,
  assessment mode, and expected evidence.
- **Quality rubric:** a named qualitative criterion assessed by a declared
  judge or reviewer.
- **Operational control:** a latency, cost, volume, retry, or cross-run rule that
  cannot be derived from a behavioral control.
- **Target:** an adapter and runtime family, such as `openai`.
- **Profile:** environment-specific model and provider options within a target,
  such as `test` or `production`.
- **Target binding:** a target-owned implementation locator or provider option.
- **Plan:** the immutable, fully resolved description of what will be
  materialized and how each guarantee will be met.
- **Evidence:** a normalized trace event, provider reference, host attestation,
  deterministic check, or semantic judgment supporting an assurance result.

Guidance is model-facing prose; controls and qualities state assessable
requirements. Target bindings connect portable semantics to implementations,
and isolation profiles state each required boundary explicitly.

## Source model

### Portable declarations

Contract source supports these top-level semantic declarations:

- `type`
- `tool`
- `datasource`
- `external_context`
- `isolation`
- `agent`
- `composition`
- `control`
- `quality`
- `operational_control`
- `eval`
- `run_spec`

Provider names, Python import paths, TypeScript module paths, credentials, and
model identifiers do not appear in portable declarations.

### Types

Types are structural and portable:

```contract
type IncidentRequest:
    incident_id: string
    question: string

type LogFinding:
    summary: string
    evidence_ids: list[string]
    confidence: float
```

The portable type subset includes:

- Named scalar types: `string`, `integer`, `float`, `boolean`, and `datetime`.
- Named closed string enums with quoted, nonempty, unique values.
- Named contract types.
- Nullable values using `?`.
- Homogeneous `list[T]` and `map[string, T]` collections.
- Field defaults representable in canonical JSON.

Arbitrary language-specific validators, methods, computed properties, and
runtime callbacks are outside the portable type system.

Portable types are declared structurally in contract source. Language-specific
implementations are generated from the canonical type graph.

### Shared tools

A tool is defined once:

```contract
tool incident.fetch_logs(
    request: IncidentRequest
) -> LogBatch:
    description = "Fetch logs relevant to an incident."
    side_effect = false
```

The declaration owns the capability name, typed interface, description, and
portable behavioral metadata. Whether an SDK implements the capability as a
host function, provider-hosted tool, remote service, or MCP call is a target
binding decision.

### Datasource interfaces

A datasource is a typed context resolver, not an implementation locator:

```contract
datasource incident.timeline(
    incident: IncidentRecord
) -> IncidentTimeline:
    description = "Resolve the current incident timeline."
    render = markdown
    cache = run
```

`render` and `cache` describe portable runtime expectations. A target binding
selects the Python, TypeScript, remote, or provider implementation.

### External context interfaces

Host-owned values that are not invocation parameters or datasource results are
declared explicitly:

```contract
external_context incident_record -> IncidentRecord:
    description = "The incident record selected by the invoking application."
    sensitivity = confidential
    render = markdown
```

An agent references a typed origin rather than a vague host promise:

```contract
context incident: IncidentRecord from external incident_record
context timeline: IncidentTimeline from datasource incident.timeline:
    map incident = context.incident
```

The complete origin vocabulary is:

- `invocation`: an entry agent signature parameter.
- `parent`: a value mapped from a delegating parent.
- `handoff`: a value carried by a handoff edge.
- `stage`: a previous run-spec stage output.
- `datasource`: a declared datasource result.
- `external`: a named external-context binding.

Entry-agent signature parameters are invocation values by default. A
composition edge explicitly maps values for child signatures. Run specs
explicitly map previous-stage values. Agent-local `context` declarations are
therefore required only for datasource and external values.

### Per-agent grants

An agent receives a grant without redefining the capability:

```contract
agent LogInvestigator(
    request: IncidentRequest
) -> LogFinding:
    use incident.fetch_logs:
        availability = enabled
        authorization = preapproved
        execution = host

    context incident: IncidentRecord from external incident_record

    goal = "Identify the most likely cause supported by log evidence."
    guidance = [
        "Distinguish observations from hypotheses.",
        "Cite the evidence IDs supporting each conclusion.",
    ]
```

Grant dimensions are independent:

- `availability`: `enabled` or `denied`.
- `authorization`: `preapproved` or `approval_required`.
- `execution`: `host`, `provider_hosted`, `remote`, or a named environment
  boundary.

Defaults are deliberately conservative:

- Omitted `availability` is `enabled` only because the explicit `use` creates
  the grant.
- An enabled tool grant must declare `authorization` explicitly.
- Omitted `execution` is unresolved and must be supplied by the target binding;
  a plan cannot guess it.
- A denied grant cannot also declare authorization or execution.

Datasource and external-context access use the same availability and execution
dimensions but do not use tool-call approval unless a future datasource
explicitly declares a side effect.

### Guidance, controls, and quality

Guidance is model-visible prose:

```contract
guidance = [
    "Use only evidence returned by declared capabilities.",
]
```

A control is a stable, assessable requirement:

```contract
control publish_after_evidence for IncidentCommander:
    severity = high
    required = true
    audience = [adapter, host, evaluator, reviewer]
    assessment = post_run
    when = trace.tool_called(status.publish)
    require = trace.tool_called(incident.fetch_logs)
```

Controls use these assessment classes:

- `static`
- `adapter`
- `runtime`
- `host_attested`
- `post_run`
- `semantic`
- `advisory`

The selected target plan reports how the requested assessment is implemented.
It may report `unsupported`, but a required unsupported control blocks
materialization.

Some controls are derived rather than written. An `approval_required` grant
creates a stable derived control requiring the runtime approval chain. Output
types create output-conformance controls. Each derived requirement appears once
in the control inventory and feeds planning, tracing, and assessment.

Quality rubrics are named and evaluator-facing by default:

```contract
quality concise_incident_summary for IncidentCommander:
    rubric = "The summary is concise, operationally useful, and non-speculative."
    audience = [evaluator, reviewer]
```

Stable names permit comparisons across runs and versions. Semantic judge
evidence records the judge model, rubric version, prompt digest, and sampling
configuration.

### Audiences

The audience vocabulary is:

- `model`
- `adapter`
- `host`
- `evaluator`
- `reviewer`

Defaults are safe and deterministic:

- `goal` and `guidance`: `[model, reviewer]`.
- Tool and composition descriptions: `[model, adapter, host, reviewer]`.
- Quality: `[evaluator, reviewer]`.
- Controls: `[adapter, host, evaluator, reviewer]`.
- Operational controls: `[evaluator, reviewer]`.

A control must opt into `model` visibility explicitly. The compiler produces a
separate view for every consumer and never inserts hidden control expressions,
rubrics, or thresholds into model instructions.

### Composition

Composition edges are named top-level declarations. They replace `routes` and
the string-valued `composition` list:

```contract
composition investigate_logs from IncidentCommander to LogInvestigator:
    mode = delegate
    description = "Investigate logs when the incident needs technical evidence."
    history = none
    map request = input.request
    isolation = EvidenceWorker

composition transfer_to_writer from IncidentCommander to CustomerImpactWriter:
    mode = handoff
    description = "Transfer when a customer-facing update is ready to draft."
    history = summary
    map brief = context.incident_brief
```

`mode` is either:

- `delegate`: the target returns its declared output to the source agent.
- `handoff`: control transfers to the target agent.

Every edge declares a model-visible description, typed child-input mappings,
history transfer, and optional isolation profile. Edge names are stable IDs.
The materializer constructs ordinary provider-native handoff or agent-as-tool
objects. A provider-specific callback is an explicit nonportable target binding,
not portable composition source.

Deterministic ordering, branches, loops, retries, checkpoints, and data
transformations remain host code. `run_spec` verifies supplied evidence from
that host-owned workflow through a distinct post-run assessment API; it does
not execute the workflow. The host must explicitly attest whether its workflow
evidence is complete. Missing stages are violations only for complete workflow
evidence and are otherwise unverified.

### Isolation

Isolation profiles declare independent requirements:

```contract
isolation EvidenceWorker:
    context = explicit_only
    capabilities = declared_only
    state = fresh
    filesystem = inherited_read_only
    network = denied
    secrets = declared_only
    return = final_output_only
```

Dimension vocabularies are intentionally closed:

- `context`: `explicit_only` or `inherited`.
- `capabilities`: `declared_only` or `inherited`.
- `state`: `fresh` or `shared`.
- `filesystem`: `none`, `ephemeral`, `inherited_read_only`, or `inherited`.
- `network`: `denied`, `allowlisted`, or `inherited`.
- `secrets`: `none`, `declared_only`, or `inherited`.
- `return`: `final_output_only` or `full_trace`.

Every declared dimension is required. A target may omit a dimension only when
the profile omits it. In-process code can enforce context and capability
scoping; it cannot claim filesystem or network isolation without an environment
provider that enforces the boundary.

### Operational controls

Operational controls are explicit because they cannot be derived from a
single-run behavioral requirement:

```contract
operational_control commander_latency for IncidentCommander:
    severity = medium
    window = 15m
    require = p95(trace.duration) < 10s
```

The initial implementation supports single-run budgets before cross-run window
aggregation. A plan must report a windowed rule as unsupported until a bound
telemetry provider supplies that capability.

## Complete proposed source

The following small project exercises every core concept:

```contract
type IncidentRequest:
    incident_id: string
    question: string

type IncidentRecord:
    incident_id: string
    service: string
    severity: string

type LogBatch:
    entries: list[string]

type LogFinding:
    summary: string
    evidence_ids: list[string]

type IncidentDecision:
    summary: string
    publish_update: boolean

tool incident.fetch_logs(request: IncidentRequest) -> LogBatch:
    description = "Fetch logs relevant to an incident."
    side_effect = false

tool status.publish(decision: IncidentDecision) -> IncidentDecision:
    description = "Publish an approved incident update."
    side_effect = true

external_context incident_record -> IncidentRecord:
    description = "The current host-owned incident record."
    sensitivity = confidential
    render = markdown

isolation EvidenceWorker:
    context = explicit_only
    capabilities = declared_only
    state = fresh
    filesystem = none
    network = denied
    secrets = none
    return = final_output_only

agent LogInvestigator(request: IncidentRequest) -> LogFinding:
    use incident.fetch_logs:
        availability = enabled
        authorization = preapproved
        execution = host
    context incident: IncidentRecord from external incident_record
    goal = "Find the most likely cause supported by log evidence."
    guidance = ["Cite evidence IDs and distinguish facts from hypotheses."]

agent IncidentCommander(request: IncidentRequest) -> IncidentDecision:
    use status.publish:
        availability = enabled
        authorization = approval_required
        execution = host
    goal = "Form an evidence-backed incident decision."
    guidance = ["Delegate technical evidence collection when needed."]

composition investigate_logs from IncidentCommander to LogInvestigator:
    mode = delegate
    description = "Investigate when log evidence is needed."
    history = none
    map request = input.request
    isolation = EvidenceWorker

control evidence_before_publish for IncidentCommander:
    severity = high
    required = true
    audience = [host, evaluator, reviewer]
    assessment = post_run
    when = trace.tool_called(status.publish)
    require = trace.agent_called(LogInvestigator)

quality evidence_backed_summary for IncidentCommander:
    rubric = "Every causal conclusion is supported by observed evidence."
    audience = [evaluator, reviewer]

operational_control commander_latency for IncidentCommander:
    severity = medium
    require = trace.duration < 10s

eval clear_incident for IncidentCommander:
    given request = IncidentRequest.fixture("clear_incident")
    expect output conforms IncidentDecision
    expect trace.agent_called(LogInvestigator)
    expect quality(evidence_backed_summary)
```

## Canonical IR

### Pre-1.0 format-version policy

Every Contract4Agents-owned serialized format keeps its version field at `"1"`
through all `0.x` product releases. During this pre-stable period, the package
version—not incrementing schema, IR, plan, trace, manifest, bundle, or generator
versions—is the compatibility signal. Format-version increments begin only
after the product has moved beyond v0.

### Identity

The canonical IR is deterministic JSON with `ir_version = "1"`. It is generated
only from parsed source and never hand-authored.

Semantic IDs use readable, kind-qualified names:

```text
type:IncidentRequest
tool:incident.fetch_logs
agent:IncidentCommander
grant:IncidentCommander:status.publish
edge:investigate_logs
control:IncidentCommander:evidence_before_publish
control:IncidentCommander:approval:status.publish
quality:IncidentCommander:evidence_backed_summary
isolation:EvidenceWorker
eval:IncidentCommander:clear_incident
```

Source spans are preserved for diagnostics but excluded from semantic digests.
The `contract_digest` is SHA-256 over canonical UTF-8 JSON with sorted object
keys, no insignificant whitespace, no generation timestamp, normalized
repository-relative source paths, and all source spans removed.

### Representative IR

```json
{
  "ir_version": "1",
  "agents": {
    "agent:IncidentCommander": {
      "name": "IncidentCommander",
      "input_type": "type:IncidentRequest",
      "output_type": "type:IncidentDecision",
      "goal": "Form an evidence-backed incident decision.",
      "guidance": [
        {
          "text": "Delegate technical evidence collection when needed.",
          "audience": ["model", "reviewer"]
        }
      ],
      "grants": ["grant:IncidentCommander:status.publish"]
    }
  },
  "capabilities": {
    "tool:status.publish": {
      "kind": "tool",
      "input_type": "type:IncidentDecision",
      "output_type": "type:IncidentDecision",
      "side_effect": true
    }
  },
  "grants": {
    "grant:IncidentCommander:status.publish": {
      "agent_id": "agent:IncidentCommander",
      "capability_id": "tool:status.publish",
      "availability": "enabled",
      "authorization": "approval_required",
      "execution": "host"
    }
  },
  "composition": {
    "edge:investigate_logs": {
      "source_agent_id": "agent:IncidentCommander",
      "target_agent_id": "agent:LogInvestigator",
      "mode": "delegate",
      "history": "none",
      "isolation_id": "isolation:EvidenceWorker",
      "input_mappings": {"request": "input.request"}
    }
  },
  "controls": {
    "control:IncidentCommander:approval:status.publish": {
      "derived_from": "grant:IncidentCommander:status.publish",
      "required": true,
      "assessment": "runtime",
      "audience": ["adapter", "host", "evaluator", "reviewer"]
    }
  }
}
```

The complete IR contains all types, context interfaces and requirements,
isolation profiles, controls, quality rubrics, operational controls, evals, and
run specs. Maps are keyed by semantic ID so target plans, traces, diffs, and
assurance results join without display-name heuristics.

## Target bindings

The default target-binding filename is `contract4agents.targets.toml`. Schema
version `1` requires every declared target to contain at least one named profile.
Profiles are complete and do not inherit. Target-level bindings are shared by
that target; a profile supplies model selections and provider options. A profile
must resolve a model for every canonical agent through `default_model` or an
explicit per-agent model, and per-agent overrides may name only canonical
agents. Profiles cannot change portable grants, authorization, controls, schemas,
audiences, composition, or isolation requirements.

Contract4Agents profiles own model identifiers and provider options. Environment
variables own credentials and may select a target and profile, but binding files
do not interpolate environment variables and profiles do not inherit. Tests and
control planes may still supply bindings programmatically; the host must persist
the resulting named materialization plan as the auditable runtime configuration.

```toml
schema_version = "1"

[targets.openai]
adapter = "openai"

[targets.openai.tools."incident.fetch_logs"]
python = "incident_app.tools:fetch_logs"

[targets.openai.tools."status.publish"]
python = "incident_app.tools:publish_update"

[targets.openai.external_context.incident_record]
python = "incident_app.context:current_incident"

[targets.openai.environments.in_process]
provider = "contract4agents.runtime:InProcessEnvironment"

[targets.openai.profiles.test]
default_model = "test-model"

[targets.openai.profiles.test.agents.LogInvestigator]
model = "test-model"

[targets.openai.profiles.production]
default_model = "gpt-5.2"

[targets.openai.profiles.production.agents.LogInvestigator]
model = "gpt-5.6-luna"
```

`contract4agents check` discovers the default target-binding file when it is
present and validates every target's binding coverage, callable shapes, and
profile completeness. A project without the file remains a valid
provider-neutral contract project. The target-binding validator rejects keys that duplicate contract authority,
including `availability`, `authorization`, `execution`, `goal`, `guidance`,
`control`, `quality`, `audience`, and `isolation`.

Python and TypeScript implementation locators are target-specific string values
resolved by their adapters. Loading bindings may import configured callables to
validate signatures, but it never calls application code during `check` or
`plan`.

## Provider-neutral plan

Every requested mapping has one status:

- `exact`: the target natively implements the declared semantics.
- `host_enforced`: a named host or environment provider enforces it.
- `emulated`: Contract4Agents can preserve the guarantee through generated
  runtime behavior.
- `degraded`: execution is possible only with a documented semantic loss.
- `unsupported`: no honest implementation is available.

`degraded` and `unsupported` are fatal for required guarantees. Advisory
guidance or quality criteria may proceed with a visible caveat when their
assessment is unavailable.

The `plan_digest` uses the same canonical JSON rules as the contract digest. It
includes the contract digest, target and profile, adapter and runtime versions,
resolved model identifiers, implementation binding identities, control
mappings, isolation mechanisms, generated artifact digests, expected event types,
and host obligations. It excludes timestamps and loaded callable memory
addresses.

```json
{
  "plan_version": "1",
  "contract_digest": "sha256:contract...",
  "plan_digest": "sha256:plan...",
  "target": "openai",
  "profile": "production",
  "adapter": {"name": "openai", "version": "1"},
  "agents": {
    "agent:IncidentCommander": {
      "name": "IncidentCommander",
      "model": "gpt-5.2",
      "model_options": {},
      "output_type": "type:IncidentDecision"
    }
  },
  "bindings": {
    "tool:status.publish": {
      "kind": "tool",
      "locator": {"python": "incident_app.tools:publish_update"},
      "outcome": "exact",
      "mechanism": "host.implementation_binding",
      "execution": "host"
    }
  },
  "controls": {
    "control:IncidentCommander:approval:status.publish": {
      "required": true,
      "assessment": "runtime",
      "outcome": "exact",
      "mechanism": "openai.tool_approval_interrupt",
      "expected_evidence": [
        "approval.requested",
        "approval.completed",
        "tool.started"
      ]
    }
  },
  "isolation": {
    "isolation:EvidenceWorker": {
      "environment": "in_process",
      "provider": "contract4agents.runtime:InProcessEnvironment",
      "dimensions": {
        "context": {
          "requested": "explicit_only",
          "outcome": "emulated",
          "mechanism": "in_process.fresh_context"
        },
        "network": {
          "requested": "denied",
          "outcome": "unsupported",
          "mechanism": null
        }
      }
    }
  },
  "expected_event_types": [
    "agent.started",
    "agent.completed",
    "output.accepted"
  ],
  "host_obligations": []
}
```

The representative plan is intentionally blocked because its selected
in-process environment cannot enforce `network = denied`. Selecting an
environment provider that enforces network denial changes the plan without
changing the portable contract.

## Materialization API

The primary Python API is:

```python
from contract4agents import materialize

result = materialize(
    "agent_contracts",
    target="openai",
    profile="production",
)

commander = result.agents["IncidentCommander"]
plan = result.plan
```

Bindings are discovered from `contract4agents.targets.toml` by default and may
be supplied programmatically for embedded or in-memory applications. The result
retains the immutable plan used to construct the native objects.

Materialization uses two passes:

1. Construct agent shells, generated output types, bound tools, instructions,
   and hooks.
2. Resolve the complete composition graph and attach native handoff or
   agent-as-tool objects.

After construction, the adapter validates the native graph against the plan.
The materializer owns agent construction, generated output types, capability
attachment, and composition wiring; the host supplies only declared bindings
and application workflow.

## Trace identity and evidence

Trace schema version `1` uses an immutable run context plus event-specific
data. Required run identity is repeated in JSONL events so files remain
independently inspectable:

```json
{
  "schema_version": "1",
  "run_id": "run-123",
  "thread_id": "thread-1",
  "event_id": "evt-000004",
  "parent_event_id": "evt-000003",
  "event_type": "approval.completed",
  "timestamp": 1784098974.25,
  "contract_digest": "sha256:contract...",
  "plan_digest": "sha256:plan...",
  "semantic": {
    "agent_id": "agent:IncidentCommander",
    "capability_id": "tool:status.publish",
    "grant_id": "grant:IncidentCommander:status.publish",
    "control_ids": ["control:IncidentCommander:approval:status.publish"]
  },
  "data": {"approved": true},
  "provider": {
    "name": "openai",
    "run_id": "provider-run-456",
    "span_id": "provider-span-789"
  },
  "evidence_refs": ["provider:openai:provider-span-789"],
  "provenance": {"source": "approval_callback"},
  "redaction": {"state": "safe", "applied": []}
}
```

The loader rejects duplicate event IDs, broken parent references within a
complete trace, mixed contract or plan digests in one run, malformed semantic
references, and absent required identity.

Negative claims require identity-bound trace-closure evidence. Event-family
occurrence is diagnostic only: the absence of a tool event cannot prove that a
tool was not called unless closure covers every attempt and the relevant tool
and provider-response paths for that run. A closure frontier records the exact
ordered event count and canonical digest it covers; stale or mismatched
frontiers fail validation. Resumed adapter sessions may extend a validated
frontier with new retry attempts but cannot rebind a sealed attempt to another
provider execution.

## Assurance results

All control assessment uses one result model:

```json
{
  "control_id": "control:IncidentCommander:approval:status.publish",
  "status": "passed",
  "applicability": "applicable",
  "reason": "Approval was granted before the capability started.",
  "evidence_event_ids": ["evt-000003", "evt-000004", "evt-000005"],
  "assessment": "runtime",
  "assessor": {"name": "contract4agents", "version": "1"}
}
```

Statuses are:

- `passed`: sufficient evidence proves the requirement for the assessed scope.
- `violated`: sufficient evidence proves the requirement was broken.
- `unverified`: evidence is missing, incomplete, contradictory, unsupported, or
  unavailable.

`skipped` remains an eval-execution state, not a control assurance result. A
skipped semantic judge produces an `unverified` quality result with a reason.

`applicability` is orthogonal to status. It is `applicable` when the requirement
was assessed, `not_applicable` when a conditional control's `when` expression
was proven false, and `unverified` when the condition could not be established.
A false condition passes vacuously; an unknown condition never does.

## Eval campaigns

The public eval workflow consumes the contract project and a test profile:

```bash
contract4agents eval agent_contracts --target openai --profile test
```

The test profile binds deterministic tool, datasource, external-context,
approval, and judge providers. `.eval` cases supply scenario inputs and
expectations. The contract and plan supply agent, capability grant,
authorization, control, and event-type inventory; no test-data provider repeats
them.

An eval campaign records:

- Case and trial IDs.
- Contract and plan digests.
- Deterministic expectation results.
- Control results.
- Quality results with judge provenance.
- Latency, cost, and token metrics when available.
- Trial counts, pass and violation rates, thresholds, and confidence intervals.
- Baseline digest and regression results when configured.

The same control assessor is used for offline eval traces and production trace
assessment. Continuous monitoring is an external operational pattern that
repeats this assessment as complete traces arrive.

## Assurance bundle and semantic diff

The deterministic assurance bundle contains:

```text
summary.html
attestation.json
contract.snapshot.json
materialization-plan.json
normalized-trace.jsonl
control-results.json
eval-results.json
provenance.json
```

`attestation.json` is the digest manifest for the other artifacts. Run-specific
timestamps remain explicit; bundle assembly itself does not add an unstable
generation timestamp. Missing expected inputs produce diagnostics and
`unverified` results rather than disappearing from the bundle.

Semantic diff compares canonical IR and plans rather than raw source text. It
classifies capability access, authorization, approvals, isolation, schema
compatibility, context exposure, enforcement status, audience, quality, and eval
coverage changes.

## CLI decisions

The installed CLI contains:

```text
check
compile
generate
plan
eval
assess
assure
diff
visualize
```

Repository documentation validation remains `pdm run docs-check`; it is not an
installed `contract4agents` command. Materialization is primarily a library API,
not a command that serializes in-memory SDK objects.

## System invariants

Every implementation phase must preserve these invariants:

1. Portable source never contains a Python or TypeScript implementation path.
2. Target bindings never repeat contract semantics.
3. A required unsupported or degraded guarantee blocks materialization.
4. Materialized native objects are validated against the reviewed plan.
5. Model-visible instructions never receive evaluator-only or hidden controls.
6. Generated types share one canonical IR and contract digest.
7. Every trace run identifies its exact contract and plan.
8. Missing event or instrumentation-closure evidence never becomes a passing assurance claim.
9. Deterministic host workflow stays outside the DSL.

## Current design choices

The product uses these design choices:

- Target bindings use `contract4agents.targets.toml`.
- Profiles do not inherit from other profiles.
- Shared tools are provider-neutral; “hosted tool” is a target-binding detail.
- Composition uses named `delegate` and `handoff` edges.
- Agent grants use orthogonal availability, authorization, and execution fields.
- `guidance`, `control`, `quality`, and `operational_control` replace ambiguous
  prose and duplicate assessment surfaces.
- Stable semantic IDs are deterministic kind-qualified names.
- SHA-256 over canonical JSON defines contract and plan digests.
- `materialize(...)` is the primary runtime-construction API.
- Trace schema version `1` requires contract and plan identity.
- Control results use `passed`, `violated`, and `unverified`.
- Environment isolation requires a bound enforcing provider.

Open product questions live in the decisions document. Surface details are
corrected by updating this specification before implementation diverges.
