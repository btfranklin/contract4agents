# Using Contract4Agents With An Agent App

This tutorial is for an engineer who already has, or is about to build, an
agent team in an SDK such as the OpenAI Agents SDK.

Contract4Agents does not replace your SDK. It gives you a typed, reviewable
source of truth for the agent team, then compiles that source into artifacts your
SDK integration can consume: instructions, manifests, JSON Schemas, eval packs,
monitor rules, and visualization files.

You do not need to understand the whole language before trying it. The smallest
useful loop is:

1. describe one agent's input and output;
2. write that agent's contract;
3. add one eval that describes a run you care about;
4. compile and inspect the generated artifacts.

This tutorial walks through that loop first, then shows where the generated
artifacts fit beside an SDK implementation.

## The Mental Model

Your application still owns runtime execution:

- model selection and SDK runner setup;
- Python functions or remote tools;
- approval UI and approval decisions;
- database, search, document, and API connections;
- deployment, auth, and observability;
- final business workflow.

Contract4Agents owns the contract layer:

- what agents exist;
- what inputs and outputs they accept;
- which host tools, hosted provider tools, subagents, and datasources each agent may use;
- what policies, guards, assertions, evals, and monitors should travel with the
  team;
- what generated artifacts should be reviewed or consumed by an adapter.

The source files are durable. The generated files are disposable build output.

## The Language Pieces

Contract4Agents uses two small source file types:

- `.contract` files define types, datasources, agents, guards, assertions, and
  monitors.
- `.eval` files define scenario checks against agents.

The definitive language docs are:

- [Contract4Agents Language](../language/contract-language.md): the main guide
  for `.contract` files.
- [Eval Language Reference](../reference/eval-language.md): supported
  deterministic eval, assertion, guard, and monitor expressions.
- [Evals, Assertions, And Monitors](../evaluation/evals-assertions-monitors.md):
  the conceptual difference between those behavioral checks.
- [Grammar Reference](../reference/grammar.md): the implemented V1 syntax
  surface.

Read this tutorial first if you want the practical path. Use the language docs
when you want to know exactly what syntax is allowed.

## Where To Put The Files

Put a Contract4Agents project inside the same repo as your agent app, in a
stable directory that can be checked by CI.

One practical layout:

```text
your-agent-app/
  agent_contracts/
    types/
      support.contract
    agents/
      coordinator.contract
      billing_specialist.contract
      security_specialist.contract
    evals/
      support.eval
    monitors/
      support.monitors.contract
  src/
    your_app/
      agents/
      tools/
      runtime.py
  .contract/
    build/
```

The directory name is up to you. The CLI only needs a root path:

```bash
contract4agents check agent_contracts
contract4agents compile agent_contracts --out .contract/build
contract4agents visualize agent_contracts --out .contract/build/visualization
```

If your contract imports Pydantic models with `type Name from python
"module:Model"`, add `--allow-python-imports` to CLI commands that compile
artifacts, including `check`, `compile`, `visualize`, `eval`, and `monitor`.

Keep `.contract/` ignored. It is generated output.

## What To Write

Start with three small sets of files.

First, write types. Types are the vocabulary of the contract. They define the
structured values your SDK code will pass into an agent and the structured value
the agent should return.

```contract
type SupportRequest:
    account_id: str
    message: str

type SupportReply:
    route: str
    answer: str
    evidence: str[]
```

Then write one agent. An agent contract is not an implementation. It is the
callable boundary and behavior contract for something your SDK implementation
will run.

```contract
agent SupportCoordinator(
    request: SupportRequest
) -> SupportReply:

    use agent BillingSpecialist from ./billing_specialist
    use tool crm.create_note from tools.crm requires approval
    use hosted_tool openai.web_search context_size "medium"

    goal = "Route the support request and produce a source-backed reply."

    policy = [
        "delegate billing questions to BillingSpecialist",
        "do not write CRM notes without approval",
        "do not invent account facts",
    ]

    guards = [
        require(output conforms SupportReply),
        forbid(tool.crm.create_note unless approved_by_human),
    ]

    assertions = [
        expect(output conforms SupportReply),
        expect(output.answer excludes unsupported_customer_fact),
    ]
```

The contract says:

- this agent receives a `SupportRequest`;
- it must return a `SupportReply`;
- it may call `BillingSpecialist`;
- it has one approval-sensitive CRM tool;
- it should not invent unsupported account facts.

You can stop here and run `check` and `compile`. That is already useful: you get
schemas, instructions, manifests, and a review graph.

Next, add an eval. Evals are not unit tests for Python functions. They are
scenario expectations for an agent run: what the output should look like and
what should appear in the trace.

```contract
eval duplicate_charge for SupportCoordinator:
    given request = SupportRequest.fixture("duplicate_charge")

    expect output conforms SupportReply
    expect trace.agent_called(BillingSpecialist)
    expect trace.not_called(crm.create_note)
    expect semantic(output, "The reply is helpful and supported by evidence.")
```

Finally, add a monitor for behavior you never want to miss in a trace. Monitors
are useful when a final answer looks fine, but the agent did something risky
along the way.

```contract
monitor crm_note_requires_approval for SupportCoordinator:
    severity = "high"
    when trace.approval_requested("crm.create_note")
    expect trace.approval_granted("crm.create_note")
```

The eval and monitor expression vocabulary is intentionally small. When you need
the exact list of supported `output...` and `trace...` expressions, use the
[Eval Language Reference](../reference/eval-language.md).

## What To Run

During local development:

```bash
contract4agents check agent_contracts
contract4agents compile agent_contracts --out .contract/build
contract4agents visualize agent_contracts --out .contract/build/visualization
```

Use `check` like a typecheck. It should run before code review.

Use `compile` to generate artifacts:

- `schemas/*.json`: JSON Schemas for declared types.
- `manifests/*.json`: machine-readable agent contracts.
- `instructions/*.md`: generated instruction text.
- `evals/evals.json`: compiled eval expectations.
- `monitors/monitors.json`: compiled monitor rules.
- `guards/guard-plan.json`: guard enforcement metadata.
- `adapters/capability-matrix.json`: adapter support and caveats.
- `docs/summary.md` and `docs/agents/*.md`: generated review docs.

The compiler artifact reference is [Compiler Outputs](../compiler/compiler-outputs.md).

Use `visualize` when reviewing a team design with other engineers. Open
`.contract/build/visualization/index.html` to see the agent, tool, type, eval,
and monitor graph.

In CI, a minimal gate is:

```bash
contract4agents check agent_contracts
contract4agents compile agent_contracts --out .contract/build --check
```

Use `compile --check` only when generated artifacts are intentionally committed.
If generated artifacts are not committed, run normal `compile` in CI as a build
smoke check instead.

## Do You Have To Call An API?

For basic use, no. You can use Contract4Agents entirely through the CLI:

1. write `.contract` and `.eval` files;
2. run `check`;
3. run `compile`;
4. inspect or consume the generated artifacts.

If your app wants to integrate at runtime, use the Python API from your build or
startup code:

```python
from pathlib import Path

from contract4agents.compiler import compile_project

artifacts = compile_project(Path("agent_contracts"))
support_manifest = artifacts["manifests"]["SupportCoordinator"]
support_instructions = artifacts["instructions"]["SupportCoordinator"]
support_schema = artifacts["schemas"]["SupportReply"]
```

For Pydantic-backed contract types, call
`compile_project(Path("agent_contracts"), allow_python_imports=True)` from a
trusted build or startup path so the compiler can import your declared model
classes and derive canonical JSON Schema.

That API gives your application the same data the CLI writes to disk.

## Where It Fits With The OpenAI Agents SDK

In an OpenAI Agents SDK app, Contract4Agents usually sits just before SDK object
construction.

Your contract source describes the team. Your app still supplies the SDK-native
objects that execute it.

Typical flow:

1. Compile the contract project.
2. Read the manifest for each agent.
3. Read the generated instructions for each agent.
4. Create SDK function tools from your real Python callables.
5. Enable SDK hosted tools from declared `hosted_tools` when you intend to use them.
6. Create or generate SDK output models that match the generated JSON Schemas.
7. Inspect the typed OpenAI adapter plan.
8. Build OpenAI `Agent` objects from that plan.
9. Run the SDK agent through your normal runner or the contract-aware run helper.
10. Capture traces and run assertions, evals, or monitor checks against those traces.

The OpenAI adapter is plan-first. It can build OpenAI `Agent` objects from
Contract4Agents artifacts, but caller code still supplies real tools, handoffs
or agents-as-tools, approvals, models, hosted-tool enablement, and application
workflow control.

The detailed adapter notes are in
[OpenAI Adapter Reference](../reference/openai-adapter.md).

Sketch:

```python
from pathlib import Path

from agents import WebSearchTool

from contract4agents.adapters.openai import (
    OpenAIToolRegistration,
    build_openai_agents_from_plan,
    plan_openai_agents_from_contracts,
)
from contract4agents.compiler import compile_project

artifacts = compile_project(Path("agent_contracts"))

def crm_create_note(account_id: str, note: str) -> str:
    return create_note_in_your_app(account_id, note)

plan = plan_openai_agents_from_contracts(
    artifacts,
    output_type_registry={"SupportReply": SupportReplyModel},
    model_registry={"SupportCoordinator": config.support_model},
    tool_registry={"crm.create_note": OpenAIToolRegistration(crm_create_note, raw_callable=True)},
    hosted_tool_registry={"openai.web_search": WebSearchTool},
)

factory_result = build_openai_agents_from_plan(plan)
agent = factory_result.agents["SupportCoordinator"]
```

The manifest tells you which host tools and hosted provider tools are declared
and what permission state they have. It does not magically create your real CRM
function, approval UI, hosted-tool policy, or deployment workflow. Those remain
application code.

## How To Think About Guards And Approvals

A guard in a `.contract` file is a durable statement of required behavior. It is
not magic runtime enforcement by itself.

```contract
forbid(tool.crm.create_note unless approved_by_human)
```

The compiled manifest and instructions preserve that requirement. Your SDK
integration should then use the compiled guard plan and enforce it at the right
boundary:

- check `artifacts["guard_plan"]` for output, approval, and denied-tool requirements;
- configure the SDK tool as approval-required if the SDK supports that;
- pause and ask your approval UI before continuing;
- record `approval.requested` and `approval.completed` trace events;
- run monitors against recorded traces.

`plan_openai_agents_from_contracts(...)` consumes the guard plan before
construction. It omits denied tools, relies on registered or generated output
types for output-conformance guards, wraps approval-required raw callables with
SDK approval metadata, and returns caveats for unsupported or unverifiable
semantics.

For one already-chosen SDK agent, `run_openai_agent_with_contract(...)` can
render non-sensitive `RuntimeContext` values into the prompt, resolve SDK
approval interruptions through your callback, record approval trace events, and
evaluate compiled assertions after the run. It still does not choose routes or
own the larger workflow.

Contract4Agents makes the intended behavior explicit and testable. The host app
still performs the runtime action.

## How To Capture Trace JSONL

Use `TraceRecorder` when you want Contract4Agents to write canonical trace JSONL
for a real or staged run:

```python
from pathlib import Path

from contract4agents.runtime import TraceRecorder

trace = TraceRecorder(Path("runs/support-001.trace.jsonl"), run_id="run-support-001")
trace.record("agent.started", event_id="evt-001", agent="SupportCoordinator")
trace.record("approval.requested", event_id="evt-002", agent="SupportCoordinator", tool="crm.create_note")
trace.record("approval.completed", event_id="evt-003", agent="SupportCoordinator", tool="crm.create_note", approved=True)
trace.record("tool.completed", event_id="evt-004", agent="SupportCoordinator", tool="crm.create_note", data={"note_id": "note-123"})
trace.record("hosted_tool.completed", event_id="evt-005", agent="SupportCoordinator", tool="openai.web_search")
trace.record("agent.completed", event_id="evt-006", agent="SupportCoordinator")
```

Each JSONL line uses `schema_version`, `event_id`, `event_type`, `timestamp`,
optional index fields such as `agent` and `tool`, event-specific `data`, and
provider metadata. If your host app writes trace files directly, follow
[Trace Schema Reference](../reference/trace-schema.md). The `contract4agents monitor --trace`
command rejects legacy top-level `type` JSONL.

## How To Use Evals And Monitors

Use `.eval` files for scenario tests. They say what output and trace behavior a
controlled run should produce. If a normal unit test asks "did this function
return the expected value?", a Contract4Agents eval asks "did this agent produce
the expected structured result, and did it take the expected path?"

Use monitor files for rules you want to apply to recorded traces. They catch
runtime behavior that final output alone might hide, such as an approval-gated
tool call without approval.

Use compiled assertions for invariants that should hold after every run of an
agent, including runs from your real SDK integration:

```python
from contract4agents.assertions import evaluate_run_assertions

assertion_result = evaluate_run_assertions(
    contract=artifacts,
    trace=trace,
    outputs={"SupportCoordinator": output},
    run_id="run-support-001",
)
```

Assertion failures are reported separately from `.eval` failures and monitor
violations. A conditional assertion whose trace condition is false is skipped.
When a trace file contains more than one run, pass the intended `run_id` to
assertion, eval, and monitor evaluation.

For public examples in this repo, deterministic harnesses run fake tools and use
the eval runner directly. In a production app, you can use the same idea against
your real or staged agent runs:

1. compile contracts;
2. run your agent with a controlled input;
3. capture normalized trace events;
4. evaluate compiled assertions, output expectations, and trace expectations;
5. run monitors against the trace.

## What To Commit

Commit:

- `.contract` files;
- `.eval` files;
- monitor files;
- any fixture data or harness code you intentionally use for tests;
- docs explaining the contract project.

Usually do not commit:

- `.contract/build`;
- generated visualization files;
- local run traces;
- generated reports;
- local SQLite files unless they are deliberate fixtures.

If your team wants generated artifacts reviewed in pull requests, commit them
and use `contract4agents compile --check` in CI. For Pydantic-backed types, use
`contract4agents compile --check --allow-python-imports` so schema artifacts and
`types/type-bindings.json` stay aligned with the current model classes.
Otherwise, treat generated artifacts as build output.

## A Practical First Adoption Path

1. Pick one existing agent team.
2. Write its output type first.
3. Write the coordinator contract.
4. Add one specialist at a time.
5. Declare only the tools each agent actually needs.
6. Add guards for output conformance and approval-sensitive tools.
7. Add one eval for the most important happy path.
8. Add one monitor for the riskiest trace behavior.
9. Run `check`, `compile`, and `visualize`.
10. Wire the manifest and instructions into your SDK construction code.

The goal is not to move all implementation into `.contract` files. The goal is
to make the agent team's shape, permissions, policies, and review artifacts
explicit enough that humans, CI, and SDK adapters can reason about them.
