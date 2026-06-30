# Vision

Contract4Agents is a typed, declarative project language and local toolchain for specifying AI agents as callable contracts.

The core idea is simple:

> An agent is a callable whose implementation is supplied by an AI runtime, so its durable source of truth must describe inputs, outputs, allowed capabilities, policies, success criteria, and observable behavior.

Contract4Agents should feel close to writing a function signature and a behavior contract, not close to writing YAML. A `.contract` file should be compact enough for an LLM to read accurately, structured enough for static analysis, and clear enough for a human to review before the agent is wired into a host application.

## The Mental Model

A traditional function has stable code between its input and output. An agent does not. Its middle is a probabilistic planning and execution process. That makes the function analogy useful but incomplete.

Contract4Agents treats an agent as:

- A callable interface with typed parameters and a typed return value.
- A policy-bound process that may call tools, agents, and datasources.
- A manifest-backed component with explicit capabilities, permissions, and context requirements.
- A behavior surface whose output and execution trace can be tested.

The source artifact is not primarily a prompt. It is an agent contract that compiles into the artifacts needed to review, integrate, evaluate, and monitor the agent.

## The Shape

The language should look more like this than like configuration:

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
        forbid(tool.stripe.create_refund unless approved_by_human),
    ]

    assertions = [
        expect(output conforms GreetingResult),
        expect(output.message excludes unsupported_customer_fact),
        when(trace.called(BillingAgent), expect(output.routed_to == BillingAgent)),
    ]
```

This syntax is intentionally function-like at the boundary and declarative inside. Contract files describe intended behavior, required context, capabilities, safety constraints, and reviewable expectations without turning the contract language into general-purpose application code.

## What Compiles From It

A Contract4Agents project produces multiple artifacts from one source of truth:

- Agent instructions optimized for LLM comprehension.
- Provider-neutral manifests with tool, agent, datasource, permission, context, and output-schema metadata.
- JSON Schemas for declared output and context types.
- Adapter mappings, starting with explicit OpenAI support.
- Eval packs that check output, trace behavior, hidden-truth discovery, and qualitative expectations.
- Monitor packs that inspect normalized traces for contract violations.
- Static visualization artifacts for review and onboarding.
- Human-readable generated docs.

The compiler is the leverage point. The syntax is valuable because it lets the system produce safer and more inspectable agent integrations from a durable contract.

## Parameters Are Typed Context Slots

Agent parameters are not divided into rigid categories like "input" and "context." Every parameter is a typed context slot:

```contract
agent SupportAgent(
    user_message: UserMessage,
    customer_profile: CustomerProfile,
    problem_summary: AccountRejectionStatus
) -> SupportResult:
```

When one agent depends on another agent or a host application invokes an agent, the available context values should be explicit and typed. If a required value is missing, an allowed datasource can resolve it.

Datasources are deliberately real Python code, not a second DSL. They follow a small interface, declare the type they produce, declare the context types they require, and return a value plus a renderable representation for the model.

This creates dependency injection for agents:

- The caller has `UserMessage` and `CustomerProfile`.
- `SupportAgent` also requires `AccountRejectionStatus`.
- A Python datasource can produce `AccountRejectionStatus` from `CustomerProfile`.
- Runtime context primitives resolve it, record provenance, render it for the model, and include it in the trace.

## Tests, Assertions, Guards, And Monitors

Contract4Agents separates behavior checks by when and how they are used.

- `guard`: safety intent and enforcement metadata available to adapters and host runtimes.
- `assertion`: invariant checked during or after a single run.
- `.eval`: offline examples and adversarial cases, kept outside the agent file.
- `monitor`: trace rule that can be run against recorded execution.

Eval files should live beside the project, not inside the agent implementation:

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

The trace is part of the agent's behavioral surface. A correct-looking final answer is not enough if the agent called an unapproved tool, skipped required evidence, wasted tool calls, or failed to route work to the right subagent.

## Why Not YAML

YAML is tolerable for configuration, but this project needs a language with:

- Agent signatures.
- Type references.
- Tool, agent, and datasource capability declarations.
- Static checks.
- Separate eval files.
- Syntax that feels natural beside Python code.

Contract4Agents should sit comfortably next to `.py` files and behave like a project language, not a pile of nested configuration.

## What Success Looks Like

Contract4Agents succeeds when it makes agent systems more reliable and easier to inspect:

- A human can review an agent's behavior contract without reading generated prompts.
- A model can read the same source and understand the job quickly.
- A compiler can reject missing tools, unsatisfied context dependencies, ambiguous datasources, invalid output schemas, and impossible eval references.
- Host applications and SDK adapters can consume the same manifest, schema, instruction, eval, monitor, and visualization artifacts.
- Eval runners can inspect traces as first-class behavior.
- Monitor rules can reuse the same source of truth as development evals.

The end state is not a prettier prompt format. It is a source-controlled contract layer for building, reviewing, testing, and observing agent integrations.
