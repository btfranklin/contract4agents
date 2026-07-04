# Using Contract4Agents With An Agent App

This guide is for an engineer who already has one Contract4Agents project
compiling and now wants to wire the artifacts beside an agent SDK such as the
OpenAI Agents SDK.

Contract4Agents does not replace your SDK. It gives you a typed, reviewable
source of truth for the agent team, then compiles that source into artifacts your
SDK integration can consume: instructions, manifests, JSON Schemas, eval packs,
monitor rules, run specs, and visualization files.

If you have not written a contract yet, start with
[First Contract Project](first-contract-project.md). This guide assumes you can
already run `contract4agents check agent_contracts` and
`contract4agents compile agent_contracts --out .contract/build`.

## The Mental Model

Your application still owns runtime execution:

- model selection and SDK runner setup;
- Python functions or remote tools;
- approval UI and approval decisions;
- database, search, document, and API connections;
- deployment, auth, and observability;
- final business workflow and stage sequencing.

Contract4Agents owns the contract layer:

- what agents exist;
- what inputs and outputs they accept;
- which host tools, hosted provider tools, subagents, and datasources each agent may use;
- what policies, guards, assertions, evals, monitors, and run specs should
  travel with the team;
- what generated artifacts should be reviewed or consumed by an adapter.

The source files are durable. The generated files are disposable build output.

## The Language Pieces

Contract4Agents uses two small source file types:

- `.contract` files define types, datasources, agents, guards, assertions,
  monitors, and run specs.
- `.eval` files define scenario checks against agents.

The definitive language docs are:

- [Contract4Agents Language](../language/contract-language.md): the main guide
  for `.contract` files.
- [Eval Language Reference](../reference/eval-language.md): supported
  deterministic eval, assertion, guard, and monitor expressions.
- [Evals, Assertions, And Monitors](../evaluation/evals-assertions-monitors.md):
  the conceptual difference between those behavioral checks.
- [Run Specs](../reference/run-specs.md): host-owned workflow sequence
  expectations and runtime evaluation.
- [Grammar Reference](../reference/grammar.md): the implemented V1 syntax
  surface.

Use the language docs when you want to know exactly what syntax is allowed. Use
this guide when you need to decide how compiled artifacts fit into application
startup, SDK construction, CI, traces, assertions, and monitors.

## Project Layout Recap

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

Relative `--out` paths are resolved from the current working directory. Keep
generated artifacts under an ignored path such as `.contract/...`; the CLI
refuses to write generated output into source-owned directories such as `docs`,
`src`, `tests`, `examples`, `agents`, `types`, `evals`, `monitors`, and
`datasources`.

If your contract imports Pydantic models with `type Name from python
"module:Model"`, add `--allow-python-imports` to CLI commands that compile
artifacts, including `check`, `compile`, `visualize`, `eval`, and `monitor`.

Keep `.contract/` ignored. It is generated output.

## What To Add After The First Contract

Once one agent compiles, expand the contract project deliberately:

1. Add one `.eval` for a controlled run you care about.
2. Add only the host tools, hosted provider tools, subagents, and datasources the agent really needs.
3. Add guards and assertions for output conformance, approval-sensitive actions, and behavior your host app can verify.
4. Add monitors when you have normalized traces from real or staged runs.
5. Add run specs only when a host-owned multi-stage workflow needs stage-output and trace expectations.

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
support_manifest = artifacts["manifests"]["SupportResponder"]
support_instructions = artifacts["instructions"]["SupportResponder"]
support_schema = artifacts["schemas"]["SupportReply"]
```

For Pydantic-backed contract types, call
`compile_project(Path("agent_contracts"), allow_python_imports=True)` from a
trusted build or startup path so the compiler can import your declared model
classes and derive canonical JSON Schema.

That API gives your application the same data the CLI writes to disk.

For CI checks against the host application, add
`agent_contracts/contract4agents.registry.json` and run:

```bash
contract4agents check agent_contracts --strict-drift
```

The registry names the local tool callables, external host-provided tools,
hosted-tool configuration, SDK agent names or factories, Pydantic output
classes, prompt assets, and host-provided context markers that should match the
compiled manifests. The checker imports only explicit registry refs and does not
execute tools or workflow code.

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
    model_registry={"SupportResponder": config.support_model},
    tool_registry={"crm.create_note": OpenAIToolRegistration(crm_create_note, raw_callable=True)},
    hosted_tool_registry={"openai.web_search": WebSearchTool},
)

factory_result = build_openai_agents_from_plan(plan)
agent = factory_result.agents["SupportResponder"]
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
trace.record("agent.started", event_id="evt-001", agent="SupportResponder")
trace.record("approval.requested", event_id="evt-002", agent="SupportResponder", tool="crm.create_note")
trace.record("approval.completed", event_id="evt-003", agent="SupportResponder", tool="crm.create_note", approved=True)
trace.record("tool.completed", event_id="evt-004", agent="SupportResponder", tool="crm.create_note", data={"note_id": "note-123"})
trace.record("hosted_tool.completed", event_id="evt-005", agent="SupportResponder", tool="openai.web_search")
trace.record("agent.completed", event_id="evt-006", agent="SupportResponder")
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
    outputs={"SupportResponder": output},
    run_id="run-support-001",
)
```

Assertion failures are reported separately from `.eval` failures and monitor
violations. A conditional assertion whose trace condition is false is skipped.
When a trace file contains more than one run, pass the intended `run_id` to
assertion, eval, and monitor evaluation.

Use compiled run specs when a host-owned multi-agent sequence should produce
specific stage outputs and trace behavior:

```python
from contract4agents.assertions import evaluate_run_spec

run_spec_result = evaluate_run_spec(
    contract=artifacts,
    run_spec="SupportEscalation",
    trace=trace,
    stage_outputs={"triage": triage_output, "reply": reply_output},
    run_id="run-support-001",
)
```

The host still decides which stages run and when. Contract4Agents validates the
declared stage output schemas, cardinality, and trace assertions after the run.

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
