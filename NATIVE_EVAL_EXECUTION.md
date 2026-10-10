# Native Eval Execution

Status: proposal. `run_project_evals(...)` and `eval run` below are proposed
interfaces. They are not available in the current package.

## User Problem and Product Promise

A user can define an agent and its eval cases in external files. Today,
`eval replay` assesses supplied outputs and traces. To test the generated native
agent, the application must write its own invocation and evidence collection
loop.

Add an optional operation that runs the declared eval against the generated
native agent and reports whether its expectations were met. This supports the
[project vision](VISION.md): clear external specifications, faithful generated
artifacts, and useful checks against those specifications.

## Reuse the System APIs

`PlannedSystem` contains the compiler artifacts and one materialization plan.
`MaterializedSystem` extends it with native agents and input, output, context,
and lifecycle helpers. Assessment APIs already accept either system type.

Use these APIs throughout the native eval path:

1. Use `plan_project(...)` to inspect the selected contract, bindings, and eval
   cases before invocation.
2. Resolve and validate the trial fixtures.
3. Call `materialize(...)` for each trial to create fresh native agents and
   context state. Confirm that its plan matches the reviewed plan.
4. Select the eval case's native entry agent once from the materialized system.
5. Validate and serialize input with `system.serialize_input_for_agent(agent,
   values)`. Resolve declared context through `system.resolve_context_for_agent`
   where the SDK integration requires it.
6. Open the adapter trace session with `router.open_session(system, ...)` and
   bind the trial attempt with `session.bind_attempt(attempt, agent=agent)`.
7. Invoke the native SDK and extract its terminal output.
8. Validate that output with `system.validate_output_for_agent(agent, output)`
   and record the trial's terminal attempt.
9. Close the supported trace capture to a snapshot, validate its identity and
   closure, and produce `FinalizedTrialEvidence`.
10. Call `assess_finalized_evidence(system, ...)` and reuse existing campaign
    aggregation. Release run and thread context state when the trial ends,
    including on failures.

`materialize(...)` already compiles and plans internally. The initial
`plan_project(...)` call is campaign preflight, not a requirement to write
compiler artifacts or construct a second planning pipeline.

Keep semantic IDs in eval declarations, plans, and evidence. Once execution
selects a native agent, pass that object to system helpers and trace binding.
The adapter should receive the selected object; it should not repeat the lookup
from an agent name or ID.

## Ownership

The contract owns agent definitions, types, grants, context declarations, and
eval expectations. Target bindings select implementations and model settings.
The native eval operation acts as the host for one direct invocation per trial.
It owns that trial's identity, attempt, evidence collection, and assessment.

The adapter supplies only SDK-specific invocation, input transport, output
extraction, provider correlation, and supported trace completion behavior.
Reuse system serialization and validation wherever they apply. Keep this
integration internal until a working example establishes the required shape.

Applications continue to own business rules, production approvals, deterministic
workflows, retries, persistence, and deployment. Native eval execution does not
execute `run_spec` stages. Existing assessment APIs remain available for
application-owned workflows and their supplied evidence.

## Bindings and Fixtures

Require an explicit, complete target-binding source for the eval configuration.
Use the existing `bindings=` argument and binding schema. Resolve every declared
dependency through that source, with no fallback to another binding file and no
replacement of tools or models after planning. A separate eval file is useful
when tests need different implementations; no new overlay format is required.

Planning currently imports Python modules to inspect bound callables and can
load an environment provider. Document trusted project imports as part of
preflight. Do not promise validation before all project code executes.

Use a small file fixture source for the first experiment. Resolve typed
`.fixture(...)` references against contract types, then validate the complete
entry input against the selected agent's signature. Reuse existing type rules.

Preserve the existing trial-data channels: invocation, host context, evaluator
truth, and redacted report view. Evaluator truth goes only to assessment.
Trial-scoped host data needs an explicit path to test dependencies. Establish
that path in the experiment without mutable process-wide fixture state.

## First Step: Internal Strands Experiment

Build one small example with a scripted Strands model, a typed fixture, and a
preapproved tool declared `side_effect = false`. Run one entry agent, one
attempt, and one trial through the actual SDK. Create fresh materialization and
trace state for every repeated trial.

Return an in-memory result. Use real execution events and the existing trace
session; do not supply prerecorded output or a hand-authored trace through
`FileEvalProvider`.

The experiment must show that a deliberate output or tool-use error fails the
declared expectation. It must also establish when the adapter has finished
emitting trace events, how context reaches the invocation, and which SDK details
the execution helper must handle.

This scripted path tests construction and execution integration. It does not
measure the behavior of a provider model. Review the experiment before fixing a
public adapter protocol.

## Minimum Public Interface

Prefer one operation that returns a result:

```python
from contract4agents.eval_campaigns import CampaignConfig, run_project_evals

result = await run_project_evals(
    "agent_contracts",
    target="strands",
    profile="test",
    bindings="contract4agents.eval.targets.toml",
    fixtures="eval-fixtures.json",
    config=CampaignConfig("support-evals", trial_count=1),
)
```

The operation creates fresh systems internally. A caller does not need to
create a runner object or supply separate IR, plan, schema, or agent registries.
Use `run_campaign(...)` and its assessment and aggregation code where useful;
factor shared internals only where the native path requires it.

The corresponding command is:

```bash
contract4agents eval run agent_contracts \
  --target strands \
  --profile test \
  --bindings contract4agents.eval.targets.toml \
  --fixtures eval-fixtures.json \
  --trials 1 \
  --out .contract/eval-run.json
```

The Python operation should return `CampaignResult` if its existing model can
represent the required result accurately. The CLI can write that result using
the existing serialization. Resolve any missing execution-status fields before
public release. A new directory format, manifest protocol, and storage API are
not prerequisites.

The public path should support at least one explicitly selected provider model
so users can evaluate generated agent behavior. Keep the scripted path as the
offline integration check. Adapter and model support must be stated clearly;
the first release does not need all three adapters.

## Initial Support Limits and Result Rules

Start with sequential direct invocations, one attempt per trial, fresh mutable
state, trusted local dependencies, and preapproved capabilities declared
side-effect-free. Report unsupported configurations before native invocation.
These are limits of the built-in eval operation. They do not impose a runtime
architecture on applications that consume generated agents.

Trusted Python implementations can access the network, files, and environment.
A declaration that a tool has no side effects is not an operating-system
boundary. Use the ordinary planner to report unsupported isolation requirements.
Provider calls use explicitly selected models and the application's credentials.
Document their data use and cost in the runnable example.

Keep execution outcome separate from expectation results. A completed invocation
can violate an expectation; an invocation error does not establish that every
expectation failed. Preserve available failure evidence without claiming missing
events were captured. The current shared assessor returns `unverified` for
failed execution; any richer failure assessment needs a deliberate shared change.

Assess positive claims from observed evidence. Assess absence-dependent claims
only when the relevant trace channel has valid closure. Incomplete capture
remains `unverified`. Missing quality-judge evidence also remains `unverified`.
Malformed identity or contradictory evidence must produce a clear failure.

Do not offer timeout completion semantics until the adapter can establish task
termination and completion of trace capture. Process interruption need not
produce a finalized result in the first release.

## Acceptance Checks

- The declared eval invokes a real native agent built from the selected binding
  source, with no prerecorded output or trace.
- Each trial uses fresh agent, context, attempt, and trace state.
- One plan identity connects preflight, materialization, trace, and assessment.
- The selected native agent is reused for input, output, context, and tracing.
- Invalid fixtures and unsupported configurations fail before invocation.
- A deliberate behavioral error is reported as a violated expectation.
- Exceptions, invalid output, and incomplete capture have accurate result states.
- Evaluator truth stays outside execution inputs and ordinary reports.
- Existing safe report serialization is preserved; terminal output and
  diagnostics are checked for disclosure before public examples use real data.
- A scripted test runs offline, and an opt-in provider test proves the documented
  model path before it is advertised as supported.

## Deferred Decisions

The [decision log](docs/decisions/open-questions.md) records the public-release
choices that need evidence from the experiment. They do not block that experiment.

Approval execution, configurable judges, operational metrics, validated assurance
ingestion, baseline comparison, and binding overlays can each follow a concrete
user need. Reuse existing assessment components when those features are added.

Concurrency, resume, cancellation guarantees, persistent job state, graph reuse,
retention services, and sandbox providers require separate proposals supported
by observed use. Design persisted artifact versions only when a concrete format
and consumer need them.
