# Contract4Agents Language

The Contract4Agents language is a typed declarative language for agent interfaces, behavior contracts, capability declarations, and run-level expectations.

It is intentionally Python-adjacent without being Python. The language should be easy to read beside `.py` files, but the compiler controls its semantics.

## Files

- `.contract`: agents, types, datasources, guards, assertions, and monitors.
- `.eval`: offline eval cases against agents.

Eval files are separate because evals are tests, not agent implementation.

## Design Principles

- Function-like boundaries: agents have typed parameters and typed return values.
- Declarative bodies: agent bodies declare capabilities, policies, success criteria, guards, and assertions.
- Explicit capability kinds: tools, agents, datasources, and types are not interchangeable.
- No fake imperative control flow: contract bodies describe behavior and constraints rather than executable application logic.
- Parseable prose: free text is allowed only inside structured fields.

## Type Declarations

Types define semantic shapes used by agent parameters, outputs, and datasources.

```contract
type CustomerProfile:
    id: str
    plan: str
    status: "active" | "past_due" | "cancelled"

type GreetingResult:
    message: str
    routed_to: AgentRef? = null
    confidence: float between 0 and 1
```

JSON Schema is the canonical interchange format for Contract4Agents types. Python/Pydantic and TypeScript/Zod bindings are adapter conveniences, not the source of truth.

When a host application already owns stable Pydantic v2 models, a contract type
can bind to an explicit import path:

```contract
type ResearchPlan from python "my_app.models:ResearchPlan"
```

Python-backed types are imported only when a check or compile explicitly allows
host-code imports. The compiler derives the same canonical JSON Schema artifacts
from the model and preserves the import path as metadata for adapters and later
drift checks. Imported type declarations cannot include native fields.

## Agent Declarations

An agent declaration has a callable signature and a declarative body.

```contract
agent CustomerGreeter(
    user_message: UserMessage,
    customer_profile: CustomerProfile
) -> GreetingResult:

    use tool calculate from mathlib
    use agent BillingAgent from ./billing
    use agent SupportAgent from ./support
    use datasource AccountRejectionStatus from datasources.account_rejection_status

    goal = "Greet the customer and route them to the right specialist when needed."

    description = "Routes simple greetings, billing requests, and account-support requests."

    policy = [
        "answer directly only when the request is simple",
        "delegate billing questions to BillingAgent",
        "delegate account-support questions to SupportAgent",
        "do not invent account details",
    ]

    success = [
        "message is friendly and specific",
        "routed_to is set when delegation happens",
        "no unsupported customer facts are asserted",
    ]

    guards = [
        require(output conforms GreetingResult),
    ]

    assertions = [
        expect(output conforms GreetingResult),
        expect(output.message excludes unsupported_customer_fact),
    ]
```

Agent bodies accept capability declarations plus these assignment attributes:

- `goal`: string.
- `description`: string.
- `policy`: list of text or contract expressions.
- `success`: list of text or contract expressions.
- `routes`: list of routing declarations.
- `composition`: list of composition declarations.
- `guards`: list of guard expressions.
- `assertions`: list of assertion expressions.

Unknown agent attributes are semantic errors. Known attributes must use the
declared value shape; for example, `guards` and `assertions` must be lists, and
`goal` and `description` must be strings. Misspellings such as `guard = [...]`
fail instead of compiling to an empty guard plan.

## Agent Parameters

Every agent parameter is a required typed context slot unless it is nullable or has a default.

```contract
agent SupportAgent(
    user_message: UserMessage,
    customer_profile: CustomerProfile,
    problem_summary: AccountRejectionStatus
) -> SupportResult:
```

Host integrations and runtime primitives supply parameters from:

- The direct invocation.
- The parent agent context.
- Datasources allowed by the contract.

If a required context slot cannot be supplied or resolved at runtime,
invocation fails with a deterministic error. Full compiler proof that every
child-agent context slot is satisfiable from parent context or datasource chains
is roadmap work; current static checks validate declared types, known
datasources, and expression references.

## Capability Declarations

Capabilities are declared inside the agent that may use them.

```contract
use tool list_charges from tools.stripe
use tool create_refund from tools.stripe requires approval
use tool read_logs from tools.log_search preapproved
use tool run_shell from tools.local denied
use agent BillingAgent from ./billing
use datasource AccountRejectionStatus from datasources.account_rejection_status
use hosted_tool openai.web_search context_size "medium"
```

Capability kinds matter:

- `tool`: a deterministic or external callable capability with a schema.
- `hosted_tool`: a provider-native capability such as OpenAI web search,
  configured as metadata and enabled by an adapter registry.
- `agent`: another Contract4Agents agent with its own contract and trace.
- `datasource`: Python resolver for a typed context slot.
- `type`: compile-time shape used for validation and schemas.

Hosted provider tools are distinct from host Python tools. Core language
semantics accept any `provider.tool` declaration. Providers with built-in
descriptors get richer validation; the built-in OpenAI descriptor supports
`openai.web_search` with `context_size` set to `"low"`, `"medium"`, or
`"high"`. Unknown providers are accepted with a warning so adapters or hosts can
own their validation.

```contract
use hosted_tool openai.web_search context_size "high"
```

Adapters may project hosted tools to SDK-native objects when host code enables
them explicitly. The provider-specific name remains metadata; core language
semantics do not assume every runtime has OpenAI web search.

Tool permission state should be explicit. The surveyed SDKs do not all use the same meaning for "allowed." Contract4Agents should distinguish:

- `available`: visible to the model or runtime but not automatically approved.
- `preapproved`: executable without an approval callback.
- `requires approval`: must pause for approval before execution.
- `denied`: cannot execute even if an adapter has a broader permission mode.
- `sandboxed`: can execute only inside a declared sandbox.

## Datasource Declarations

Datasources can be declared in `.contract` files and implemented in Python.

```contract
datasource AccountRejectionStatus:
    python = "contract_app.datasources.account_rejection_status:resolve"
    requires = [CustomerProfile]
    produces = AccountRejectionStatus
    render = "markdown"
    cache = "run"
```

The datasource declaration tells the compiler what the resolver can produce and what context it needs. The Python function handles real data loading.

## Guards

Guards describe safety constraints for adapters and host runtimes.

```contract
guards = [
    require(output conforms RefundDecision),
    forbid(tool.stripe.create_refund unless approved_by_human),
    forbid(tool.github.merge_pull_request),
]
```

The compiler preserves guard intent in generated instructions and manifests so integrations can apply it at the relevant boundary.
It also emits a structured guard plan for currently supported mappings:
output conformance, approval-required tools, and denied tools. Unsupported
guard expressions remain explicit in the plan rather than becoming hidden
instructions-only behavior.

## Assertions

Assertions are run-level invariants checked during or after execution.

```contract
assertions = [
    expect(output conforms GreetingResult),
    expect(output.message excludes unsupported_customer_fact),
    when(trace.called(BillingAgent), expect(output.routed_to == BillingAgent)),
]
```

Assertions should not contain one-off fixtures. A condition like `when user_message == "Hi"` belongs in a `.eval` file.

## Policies And Success Criteria

Policies and success criteria are model-facing text, but they remain structured fields.

```contract
policy = [
    "prefer evidence over speculation",
    "ask a human only when account ownership is ambiguous",
    "do not expose internal provider IDs to customers",
]

success = [
    "decision is supported by evidence",
    "customer reply is concise and non-technical",
    "no irreversible action occurs without approval",
]
```

The compiler uses these fields to generate instructions and semantic eval rubrics.

## Routing

Routing declares expected delegation behavior. The compiler preserves routes in the manifest, generated instructions, eval material, and adapter metadata where supported.

Preferred declarative form:

```contract
routes = [
    when(intent == "billing", call(BillingAgent)),
    when(intent == "account_support", call(SupportAgent)),
]
```

Adapter support may vary: some targets can map routes to handoffs or agent tools; others may carry them as instructions plus trace expectations.

## Composition Declarations

Contract4Agents can preserve declared composition preferences while leaving execution mechanics to adapters and host code.

```contract
composition = [
    agent_as_tool(ResearchAgent),
    handoff(SupportAgent),
    isolated_subagent(LogInvestigator),
]
```

Common semantic modes:

- `agent_as_tool`: the current agent stays in control and calls another agent like a tool.
- `handoff`: control transfers to a specialist agent.
- `isolated_subagent`: the child agent has an isolated context and returns only a final result to the parent.

Composition declarations must use one of these function forms and target a known
agent declared with `use agent` on the current agent.

## Static Checks

The compiler currently rejects:

- Unknown types.
- Unknown agents, tools, or datasources.
- Duplicate names in the same module scope.
- Ambiguous datasource resolution.
- Guards that reference unavailable tools.
- Assertions that reference unavailable trace events.
- Eval cases and monitors that reference missing fields or capabilities outside
  the scoped agent's declared dependency closure.
- Return types that cannot produce an output schema.

Roadmap checks will add full child-context satisfiability analysis and
host-code or capability-registry drift validation.
