# Implementation Roadmap

This roadmap is the active backlog for work that remains between the current implementation and `VISION.md`. It is not a changelog and it should not preserve finished work.

## Maintenance Rule

Keep this file limited to unimplemented or materially incomplete work. When an item is implemented, documented, and covered by validation, delete its section from this file in the same change. Do not move implemented work into a finished, completed, historical, archived, or done-items section.

If an item no longer belongs in the product, remove it from `VISION.md` first and then delete it from this roadmap.

## Assertion Execution

Vision gap: assertions are part of the agent contract and should be checked during or after a run. Today they are parsed, statically checked, compiled into manifests and instructions, and documented, but they are not executed as a general invariant layer for every run path.

Implementation work:

- Add a small assertion execution module that accepts an agent name, that agent's manifest assertions, final output, and normalized trace events.
- Reuse the existing expression parser and evaluator instead of adding a second assertion language.
- Support both unconditional `expect(...)` assertions and conditional `when(..., expect(...))` assertions.
- Treat unsupported assertion syntax as a failed assertion, not as a skipped check.
- Return structured assertion results with assertion text, pass/fail state, failure kind, and enough trace or output context to debug the violation.
- Integrate assertion execution into the fixture runner so `contract4agents eval` reports assertion failures separately from output, trace-spy, and semantic-eval failures.
- Expose a host-callable assertion API so SDK adapters can run the same checks after a model run without depending on the fixture runner.
- Add tests for passing assertions, output failures, trace failures, conditional assertions whose condition is false, and unsupported expressions.

Validation:

```bash
pdm run test:unit
pdm run contract4agents eval tests/fixtures/contract_projects/ops-desk-lab
```

Definition of done:

- Any run path that returns output and a Contract4Agents trace can execute compiled assertions for the target agent.
- Assertion failures are visible in reports with a distinct failure type.
- Existing eval behavior stays deterministic when assertions pass.

## Guard Mapping For Adapters And Hosts

Vision gap: guards describe safety intent and enforcement metadata for adapters and host runtimes. Today guards are preserved as contract text and statically checked, but the OpenAI adapter does not translate them into concrete guardrail objects, approval hooks, output validation, or host enforcement metadata.

Implementation work:

- Introduce a typed guard plan built from manifest guards.
- Classify supported guard patterns, starting with output-conformance guards, approval-required tool guards, denied-tool guards, and input or prompt-rejection guardrails where the expression can be represented safely.
- Emit explicit unsupported-guard diagnostics or adapter caveats when a guard cannot be represented by the selected adapter or host boundary.
- Map `require(output conforms TypeName)` to output schema validation metadata that host code and adapters can apply after the model returns.
- Map `forbid(tool.name unless approved_by_human)` to approval-required tool metadata and host approval hooks.
- Map denied tools to adapter or host preflight rejection where a tool registry is available.
- Extend the OpenAI adapter so it can consume the guard plan when building agents and tools, rather than requiring example code to hand-wire all guard behavior.
- Keep guard execution honest: do not claim hard enforcement for guards that are only preserved as instructions or warnings.
- Add unit tests for guard-plan classification, unsupported guard reporting, and OpenAI adapter mapping.

Validation:

```bash
pdm run test:unit
pdm run test:integration
```

Definition of done:

- Compiled artifacts expose a typed guard plan or equivalent metadata.
- The OpenAI adapter consumes the guard plan for every supported guard category.
- Unsupported guard semantics are reported clearly instead of being silently treated as enforced.

## Stronger Context-Dependency Analysis

Vision gap: the compiler should reject unsatisfied context dependencies. Today datasource definitions and type references are checked, but the analyzer does not fully prove that agent-to-agent calls have all required typed context slots satisfiable from caller inputs, declared datasources, or host-supplied context.

Implementation work:

- Build a per-agent context graph from typed parameters, declared datasources, and declared agent dependencies.
- For each `use agent` dependency and each composition declaration that references another agent, compare the child agent's required parameters against the parent agent's available context.
- Recursively account for datasources that can produce missing context slots when their own requirements are satisfiable from the parent context.
- Detect missing context, ambiguous datasource choices, and datasource requirement cycles across the full dependency path.
- Add diagnostics that name the parent agent, child agent, missing type, and failed resolution path.
- Keep host-owned context explicit: if a type must be supplied by the host, represent that as a known requirement rather than pretending it is resolved.
- Add focused parser fixture cases for satisfied parent-to-child context, missing child context, datasource-satisfied child context, ambiguous datasource paths, and cyclic datasource requirements.

Validation:

```bash
pdm run test:unit
pdm run contract4agents check examples/incident-command
```

Definition of done:

- `contract4agents check` can reject an agent dependency whose required typed inputs cannot be supplied or resolved.
- Diagnostics point to the contract declaration that created the unsatisfied dependency.
- Valid existing fixtures still pass without host-specific wiring.

## Missing Capability Checks

Vision gap: contracts should make missing tools visible before an agent is wired into a host application. Today trace and eval expressions can be checked against known tools, but declared tool sources are not validated against a registry or importable implementation surface.

Implementation work:

- Define a lightweight local capability registry contract for tools, including tool name, source, permission, and Python callable or host-provided marker.
- Teach project checks to load that registry when present, starting with fixture projects and local examples.
- For importable Python tool references, verify that the referenced module and callable exist without executing business logic.
- For explicitly host-provided tools, require a registry entry that marks the tool as external so the compiler can distinguish intentional host ownership from a typo.
- Check that manifest permissions match the registry permission for the same tool, or report a targeted diagnostic.
- Keep the default developer path practical: projects without a registry may still compile, but strict fixture and example validation should fail on missing local tools.
- Add tests for missing tool source, misspelled callable, permission mismatch, and intentionally external host-provided capability.

Validation:

```bash
pdm run test:unit
pdm run contract4agents check examples/incident-command
```

Definition of done:

- Local fixtures and public examples can prove that every declared local tool has an implementation surface.
- Typos in declared tool sources fail during validation rather than surfacing only during a run.
- Host-owned tools remain possible, but they must be explicit.

## Public Example As A First-Class Fixture

Vision gap: the public `examples/incident-command` project works for check, compile, and visualization, and it has eval and monitor source files. It is not yet a reusable `contract4agents eval` fixture because it does not include a fixture-runner `fixture.json` project.

Implementation work:

- Add `examples/incident-command/fixture.json` with the same shape documented in `docs/reference/test-fixtures.md`.
- Wire the existing incident command seed data, local harness, hidden truth, starts, and output type into fixture-runner-compatible Python references.
- Ensure the fixture runner compiles the example, verifies generated artifacts, executes starts, writes report artifacts, and evaluates the existing `.eval` cases.
- Decide whether the public example should include a live OpenAI runner; if it does, keep it opt-in and behind the same environment gate as other live tests.
- Update README commands so the public example is the default eval smoke path once it can run through `contract4agents eval`.
- Keep `tests/fixtures/contract_projects/ops-desk-lab` as an internal edge-case fixture, not the primary public walkthrough.
- Add integration coverage that runs `contract4agents eval examples/incident-command`.

Validation:

```bash
pdm run contract4agents eval examples/incident-command
pdm run test:integration
```

Definition of done:

- A fresh clone can run check, compile, visualize, and eval against the same public example.
- The README no longer needs to explain that eval uses an internal fixture instead of the public example.
- The internal ops-desk fixture remains available for broader edge coverage.

## Richer Generated Documentation

Vision gap: compiler-generated human docs should support review and onboarding from the contract source. Today generated docs are a compact project summary.

Implementation work:

- Expand `generated_docs()` into a deterministic documentation generator with at least a project overview and one agent-focused page.
- Include each agent's signature, goal, description, inputs, output type, schema link, tool permissions, datasource requirements, subagent dependencies, policies, success criteria, guards, assertions, evals, monitors, and adapter caveats.
- Include a project-level index that lists agents, types, datasources, evals, monitors, and generated artifact paths.
- Render capability tables rather than prose-only lists so reviewers can scan permissions and context dependencies quickly.
- Cross-link generated docs to generated schemas, manifests, instructions, eval packs, and monitor packs using stable relative paths.
- Keep generated docs stable across runs by sorting sections and avoiding timestamps.
- Add snapshot or structural tests that catch accidental doc regressions without making minor prose maintenance painful.

Validation:

```bash
pdm run test:unit
pdm run contract4agents compile examples/incident-command --out .contract/build
```

Definition of done:

- Generated docs contain enough information to review an agent contract without opening the raw manifest JSON.
- Generated docs are deterministic and covered by tests.
- Compile check mode catches stale generated docs.

## Fuller OpenAI Adapter Integration

Vision gap: the OpenAI adapter can build Agents SDK objects and normalize trace hooks, but it still relies heavily on caller-supplied wiring. More of the manifest surface should be consumed directly: permissions, guards, context rendering, approvals, output schemas, handoffs, composition metadata, and assertion metadata.

Implementation work:

- Add an adapter planning layer that consumes compiler artifacts and returns a typed OpenAI adapter plan before constructing SDK objects.
- Generate or accept output types from Contract4Agents schemas so callers do not need to hand-supply every `output_type`.
- Build OpenAI tool wrappers from a registered tool surface and manifest permission metadata.
- Carry approval-required tools into SDK or host approval handling consistently and record approval trace events.
- Map composition metadata to OpenAI handoffs or agents-as-tools when the caller provides the corresponding child agent objects.
- Render typed context into model input using `RuntimeContext.rendered_context()` or an adapter-specific equivalent, while preserving hidden state outside the model prompt.
- Feed guard plans and assertion metadata into the adapter run path so OpenAI runs produce the same normalized trace and post-run checks as fixture runs.
- Return adapter caveats when a manifest feature cannot be represented directly by the OpenAI Agents SDK.
- Add live-test coverage only behind existing opt-in environment flags; normal validation must remain offline.

Validation:

```bash
pdm run test:unit
pdm run test:integration
CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE=1 pdm run test:openai-agent-live
```

Definition of done:

- A host can construct and run the OpenAI adapter from Contract4Agents artifacts with minimal manual wiring.
- Permissions, guards, context rendering, traces, and assertions follow the same semantics as local fixture runs where the SDK surface allows it.
- Unsupported adapter semantics are explicit caveats, not hidden behavior.
