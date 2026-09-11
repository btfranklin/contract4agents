# Vision

Contract4Agents is a toolkit for defining and generating structured agent
systems from typed external specifications.

The primary goal is to make agents easier to define, generate, and review.
The contract states the agent's structure, guardrails, and expectations so
implementors do not have to infer them from application code. Generated
schemas, instructions, documentation, and framework objects make that
specification useful in an application. Validation, evals, traces, and assurance
support checking the implementation against the specification.

An agent's model can choose tools and delegate work. A prompt alone does not
describe its full interface or the expectations for its results. A contract
puts these choices in a structured form that people and tools can read.

The toolkit supports this path:

```text
Declare -> Compile -> Plan -> Materialize -> Run -> Trace -> Assure
```

Use the stages your application needs. Generating types does not require an
agent runtime. Running generated agents does not require trace assessment.

## The Contract Is the Product

A `.contract` project should be the agent configuration kept in source control.
It should be compact enough for a model to understand, structured enough for
static analysis, and precise enough for a human to review without reading
generated provider code.

Contract4Agents is responsible for faithfully deriving artifacts from that
specification and explaining what each target can represent. It does not
supervise the host application or require implementors to adopt a prescribed
runtime architecture.

The contract owns portable semantics:

- native structural types and typed agent signatures;
- reusable tools and datasource interfaces;
- explicit per-agent capability grants;
- model-selectable delegation and handoff edges;
- named context origins and isolation requirements;
- model guidance, assessable controls, quality rubrics, and eval scenarios;
- expected trace and assurance evidence.

A separate target binding owns only implementation choices: Python or
TypeScript locators, provider-native tools, remote endpoints, environment
providers, models, and provider options.

Changing from one supported framework, model, or deployment profile to another
should be a target change when portable semantics stay the same. It must not
require recreating the team as a parallel tree of SDK agent configuration.

## Function-Like, Not a Workflow Language

The language should feel like typed declarations beside normal application
code:

```contract
type IncidentRequest:
    incident_id: string
    question: string

type LogFinding:
    summary: string
    evidence_ids: list[string]

tool logs.search(request: IncidentRequest) -> LogFinding:
    description = "Search approved incident logs."
    side_effect = false

agent LogInvestigator(request: IncidentRequest) -> LogFinding:
    use logs.search:
        availability = enabled
        authorization = preapproved
        execution = host

    goal = "Find the most likely cause supported by log evidence."
    guidance = ["Cite evidence IDs and distinguish facts from hypotheses."]
```

Named composition edges describe relationships available to the model:

```contract
composition investigate from IncidentCommander to LogInvestigator:
    mode = delegate
    description = "Investigate when log evidence is needed."
    history = none
    map request = input.request
```

Deterministic ordering, branching, loops, retries, checkpoints, and data
transformations remain ordinary host code. Contract4Agents should never become
a disguised general-purpose programming language.

## One Declaration, Many Consequences

If the system can derive something deterministically from the canonical
contract, users should not have to declare it again.

For example, an `approval_required` grant should produce:

- safe model guidance when appropriate;
- the target's approval configuration or integration hook;
- a clear description of the approval decision that the host supplies;
- expected approval trace events;
- a default live assessment rule;
- assurance evidence obligations.

Likewise, a typed return produces structured-output schema, generated runtime
types, output validation, telemetry expectations, and an assurance control.

This is how the language stays small while the system becomes powerful.

## Make Target Differences Explicit

Provider neutrality must not hide meaningful provider differences. Planning
resolves one target and profile before native objects exist. Every requested
mapping reports whether it is:

- exact;
- host-enforced;
- emulated without losing the guarantee;
- degraded with a documented semantic loss; or
- unsupported.

Planning rejects required degraded or unsupported mappings for the selected
target. This prevents generation from silently weakening the declared agent;
it does not give Contract4Agents control over application execution.

The same rule applies to isolation. Context separation, capability restriction,
state, filesystem, network, secrets, and return channels are independent
dimensions. Code generation can create fresh context and tool allowlists; it
cannot create an operating-system or network boundary by assertion. A runtime
provider must enforce stronger boundaries and produce evidence of the mechanism.

## Construct Normal Framework Objects

Contract4Agents materializes ordinary objects from the chosen framework. The
OpenAI target, for example, returns normal Agents SDK agents, generated output
types, tools, approval hooks, delegations, and handoffs.

The host still owns real tool implementations, credentials, approval decisions,
persistence, deterministic workflow, deployment, and external services.
Contract4Agents owns portable agent configuration and verifies the native graph
it constructs. Generated validators and approval hooks can implement declared
guardrails through the selected framework. The application retains business
behavior and operational authority. A documented host responsibility describes
an integration requirement; it does not require a new Contract4Agents runtime
enforcement mechanism.

## Evidence Is Part of the Contract

Evidence helps implementors check whether observed behavior matches declared
expectations. When an application uses trace and assurance support, it can
examine which tools ran, which approval was recorded, which agent received what
context, which isolation mechanism was reported, and whether the expected
telemetry was complete. These tools support the specification and generation
workflow; using generated agents does not require continuous runtime assessment.

Normalized traces bind observed events to:

- the exact contract and materialization plan digests;
- stable semantic IDs for agents, capabilities, grants, controls, and edges;
- causal parent/stage relationships;
- provider-native trace and span identifiers;
- provenance, evidence references, and audience-safe redaction.

Contract4Agents should integrate with existing provider traces and OpenTelemetry
rather than become a trace-storage or dashboard company merely because evidence
needs a portable schema.

## Keep Instructions and Evidence Distinct

Model-facing guidance is not automatically enforced policy. A prose wish is not
a verified success criterion. The language separates:

- `guidance`: behavioral instruction shown to the model;
- `control`: a named requirement with assessment class and evidence;
- `quality`: a named qualitative rubric for a judge or reviewer;
- `operational_control`: latency, cost, retry, volume, or cross-run behavior.

Assessment reports `passed`, `violated`, or `unverified`. Missing or incomplete
evidence must never become a pass. The same assessor should interpret controlled
eval traces and imported production traces.

Assurance bundles combine declared, planned, observed, and assessed truth for a
release review, compliance process, or incident investigation. They make
evidence portable and uncertainty explicit; they do not appoint
Contract4Agents as a legal certification authority.

## What Success Looks Like

Contract4Agents succeeds when:

- a team can keep contracts rather than provider SDK agent configuration as the
  durable source in its repository;
- a reviewer can understand agent access, composition, context, controls, and
  evidence obligations before execution;
- changing a supported model or framework is a target/profile change when
  semantics remain portable;
- planning exposes every target loss and blocks unsupported required guarantees;
- materialization constructs and validates the complete native graph;
- Python and TypeScript types derive from the same contract IR;
- traces connect observed behavior to the recorded contract and plan;
- eval and production assessment share controls and result semantics;
- release reviewers can see semantic changes, regressions, violations, and
  unverifiable claims without reconstructing the system by hand.

The end state is a clear external agent specification with faithful generated
artifacts. Implementors can define an agent once, understand its guardrails and
expectations before reading application code, and use validation and evidence
to check their implementation.
