# Contract Language

Contract4Agents is a portable declarative language. Contracts describe
desired agent semantics; target bindings describe how one runtime implements
them. Python, TypeScript, provider, SDK, and deployment details are never
portable source authority.

## Portable Types

```contract
type ResearchQuestion:
    topic: string
    as_of: datetime

type Evidence:
    source_id: string(min_length=1,max_length=200)
    confidence: float(minimum=0,maximum=1)
    tags: list[string](min_items=1,max_items=20)
    metadata: map[string,string]
    note: string(max_length=2000)?

enum VerificationStatus:
    "accepted"
    "follow_up"
    "failed"

type Verification:
    status: VerificationStatus
```

The scalar types are `string`, `integer`, `float`, `boolean`, and `datetime`.
Collections use `list[T]` and `map[string,T]`. Add `?` for nullability. Defaults
follow the field type after `=`. A `float` must be finite. `NaN` and positive or
negative infinity are not portable JSON values and fail validation.

The portable constrained subset is:

- `string(min_length=N,max_length=N)`;
- `integer(minimum=N,maximum=N)`;
- `float(minimum=N,maximum=N)`;
- `list[T](min_items=N,max_items=N)`.

Each bound is optional, but a constrained type must declare at least one bound.
String length bounds are non-negative integers. Integer bounds must be integers.
List item bounds are non-negative integers. Each constrained type must declare
at least one bound, and its minimum cannot be greater than its maximum. String
length counts Unicode code points. Constraints can be used in type fields, tool
and datasource parameters, context, lists, and nullable types. Lists keep their
item type inside square brackets and put the constraint block before a nullable
suffix, for example `list[Source](min_items=1,max_items=20)?`. Bounds are
formatted in canonical `min_items` then `max_items` order. Map types do not
support constraints. Defaults must satisfy enum membership and declared
constraints.

The portable `datetime` lexical profile is the RFC 3339 subset accepted by all
selected runtimes: `YYYY-MM-DDTHH:MM:SS`, with optional fractional seconds and
a required `Z` or numeric `+HH:MM`/`-HH:MM` offset. The separator is a literal
`T`; a space separator, missing timezone, malformed offset, impossible date,
and leap-second value are invalid. Python runtime values remain aware
`datetime` objects.

`str`, `int`, `bool`, `T[]`, and `type ... from python` are not aliases. Code is
generated outward from native contract types.

String enums declare a closed set of one or more quoted, nonempty, unique
values. Enums and structural types share one name namespace and may be used
anywhere a named type is accepted, including nullable, list, and map fields.
Defaults must conform to enum membership at compile time.

## Shared Capabilities

Tools are canonical provider-neutral interfaces:

```contract
tool sources.search(
    query: string(min_length=1,max_length=4000),
    as_of: datetime
) -> Evidence:
    description = "Search dated evidence."
    side_effect = false
```

The contract owns the name, signature, description, and side-effect semantics.
Python callables, provider-hosted tool names, remote endpoints, and MCP locators
belong in `contract4agents.targets.toml`.

A datasource is a typed context resolver rather than an agent-invoked tool:

```contract
datasource account.profile(account_id: string) -> AccountProfile:
    description = "Resolve the current account profile."
    render = markdown
    cache = run
```

Datasource implementations also belong in target bindings.

## External Context

Host-owned values must have a named portable interface:

```contract
external_context current_account -> AccountProfile:
    description = "The authenticated account for this invocation."
    sensitivity = confidential
    render = markdown
```

Sensitivity is `public`, `internal`, `confidential`, or `restricted`. Render
mode is `markdown`, `json`, or `text`.

## Agents and Grants

```contract
agent SupportAgent(request: SupportRequest) -> SupportReply:
    use crm.create_note:
        availability = enabled
        authorization = approval_required
        execution = host
    use web.search:
        availability = enabled
        authorization = preapproved
        execution = provider_hosted

    context account: AccountProfile from external current_account
    context history: AccountHistory from datasource account.history:
        map account_id = context.account.id

    goal = "Resolve the request safely and accurately."
    description = "Handles authenticated support requests."
    guidance = [
        "Distinguish verified facts from hypotheses.",
        "Cite account evidence when making a recommendation.",
    ]
```

Agent signatures are typed invocation inputs and one typed output. A grant is a
relationship between an agent and a shared tool declaration; it does not
redeclare the tool.

Grant dimensions are orthogonal:

- `availability`: `enabled` or `denied`
- `authorization`: `preapproved` or `approval_required`
- `execution`: `host`, `provider_hosted`, `remote`, or a named target environment
- optional `isolation`: a declared isolation profile

An enabled grant requires explicit authorization and execution. A denied grant
cannot declare authorization, execution, or isolation. Named execution
boundaries must be declared by the selected target.

Context origins are `invocation`, `parent`, `handoff`, `stage`, `datasource`,
and `external`. Agent-local context declarations use datasource or external
origins; invocation, parent, handoff, and stage provenance is represented by
typed signatures, composition mappings, and run specs. Datasource declarations
map every resolver input from `input.<path>` or an earlier `context.<path>`.

`goal` and `guidance` are model-visible. `description` is review and adapter
metadata. Output conformance is derived automatically from the return type;
behavioral invariants belong in controls or quality rubrics. Run-spec assertions
remain available for host-owned workflow verification.

## Composition

Composition is a named top-level edge:

```contract
composition investigate from SupportAgent to EvidenceAnalyst:
    mode = delegate
    description = "Delegate focused evidence analysis when needed."
    history = none
    map request = input.request
    isolation = EvidenceWorker
```

Mode is `delegate` or `handoff`. History is `none`, `summary`, or `full`.
Every required target input needs one explicit `map`. The edge defines an
available relationship; the model chooses it using the edge and target
descriptions. Deterministic routing remains application workflow code.

There is no list-valued `composition`, `routes`, or `use agent ... from` syntax.

## Isolation

Isolation requirements are multidimensional:

```contract
isolation EvidenceWorker:
    context = explicit_only
    capabilities = declared_only
    state = fresh
    filesystem = none
    network = denied
    secrets = none
    return = final_output_only
```

Planning reports support for each dimension and fails when a required boundary
cannot be enforced. A compiler can construct fresh context and capability
allowlists, but an environment provider must enforce filesystem, network,
process, and secret boundaries.

## Guidance, Controls, and Quality

Guidance is prose shown to the model. Controls are stable assessable
requirements:

```contract
control current_claims_need_current_facts for ResearchLead:
    severity = high
    required = true
    audience = [host, evaluator, reviewer]
    assessment = post_run
    when = trace.tool_called(documents.fetch)
    require = trace.tool_called(current_facts.fetch)
    expected_evidence = [tool.completed]
```

Assessment is `static`, `adapter`, `runtime`, `host_attested`, `post_run`,
`semantic`, or `advisory`. Required unsupported controls block planning.

The compiler derives output-conformance controls from agent return types and
approval controls from `approval_required` grants. Each derived requirement
appears once in the control inventory and feeds every assessment surface.

Quality declarations are named evaluator rubrics:

```contract
quality evidence_backed for ResearchLead:
    rubric = "The recommendation is supported by current cited evidence."
    audience = [evaluator, reviewer]
```

Operational controls cover behavior that cannot be derived from one run's
contract semantics:

```contract
operational_control latency for ResearchLead:
    severity = medium
    require = trace.duration < 30s
```

## Audiences

Audience values are `model`, `adapter`, `host`, `evaluator`, and `reviewer`.
The compiler creates separate views and never pastes hidden control expressions,
rubrics, thresholds, or reviewer-only content into model instructions.

Defaults are:

- goal and guidance: model, reviewer
- tool/composition descriptions: model, adapter, host, reviewer
- control: adapter, host, evaluator, reviewer
- quality and operational control: evaluator, reviewer

## Evals

`.eval` files declare cases against an agent:

```contract
eval cites_current_evidence for ResearchLead:
    given question = ResearchQuestion.fixture("current_market")
    expect output conforms ResearchBrief
    expect trace.tool_called(current_facts.fetch)
    expect quality(evidence_backed)
```

Stable eval and quality IDs allow comparison across contract versions, targets,
models, and production traces.

## Run Specs

Run specs verify host-owned workflow results without implementing workflow
control flow:

```contract
run_spec ResearchRun:
    stages = [
        evidence: EvidenceAgent -> Evidence,
        synthesis: ResearchLead -> ResearchBrief,
    ]
    assertions = [
        expect(trace.called_before(EvidenceAgent, ResearchLead)),
    ]
```

Branching, loops, retries, checkpoints, and deterministic routing remain in
application code.

## Static Checks

Semantic analysis rejects:

- invalid or unknown portable types;
- duplicate declarations and fields;
- unknown capabilities, agents, datasources, external contexts, or isolation
  profiles;
- incomplete or contradictory grants;
- context requirements with unresolved named origins;
- composition edges with missing target-input mappings;
- invalid isolation dimensions;
- controls without a valid assessment or requirement;
- expressions that reference unavailable output fields or trace targets;
- evals and run specs with unknown agents, stages, types, or capabilities.

Target implementation coverage and callable signature conformance are checked by
`plan` and `materialize`, not by portable source parsing.
