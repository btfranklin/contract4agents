# Implementation Roadmap

This roadmap is the active backlog for work that remains between the current implementation and `VISION.md`. It is not a changelog and it should not preserve finished work.

## Maintenance Rule

Keep this file limited to unimplemented or materially incomplete work. When an item is implemented, documented, and covered by validation, delete its section from this file in the same change. Do not move implemented work into a finished, completed, historical, archived, or done-items section.

If an item no longer belongs in the product, remove it from `VISION.md` first and then delete it from this roadmap.

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

## Capability Registry And Host-Code Drift Checks

Vision gap: contracts should make missing tools and implementation drift visible before an agent is wired into a host application. Today trace and eval expressions can be checked against known tools, but declared tool sources are not validated against a registry or importable implementation surface, and there is no opt-in check that compares contract declarations with actual host application code.

Design boundary: drift checks are CI-oriented verification. They should import only explicitly configured host surfaces, avoid executing business workflows, and report mismatches without claiming to prove all runtime behavior.

Implementation work:

- Define a lightweight local capability registry contract for tools, hosted tools, agents, output types, prompts, and host-provided markers.
- Teach project checks to load that registry when present, starting with fixture projects and local examples.
- For importable Python tool references, verify that the referenced module and callable exist without executing business logic.
- For explicitly host-provided tools, require a registry entry that marks the tool as external so the compiler can distinguish intentional host ownership from a typo.
- Check that manifest permissions match the registry permission for the same tool, or report a targeted diagnostic.
- Add opt-in host-code drift checks that can verify:
  - contract agent names match actual OpenAI Agents SDK agent names or configured factory names;
  - contract output types match actual Pydantic output classes;
  - contract hosted tool declarations match actual agent factory configuration;
  - declared host tool permissions match registered tool surfaces;
  - configured prompt or instruction assets have not obviously drifted from contract expectations.
- Keep prompt drift checks conservative. They should catch obvious mismatches such as missing configured prompt assets or wrong agent-to-prompt mappings, not attempt to semantically prove that prompt prose fully matches the contract.
- Support a small project configuration file or contract declaration for drift-check import paths, registry paths, and strictness settings.
- Add a CI-usable command or command option for drift checks, with deterministic exit codes.
- Ensure failures name the Python import path, registry entry, contract declaration, and expected versus actual value when possible.
- Keep the default developer path practical: projects without a registry may still compile, but strict fixture and example validation should fail on missing local tools.
- Add tests for missing tool source, misspelled callable, permission mismatch, intentionally external host-provided capability, mismatched agent name, mismatched Pydantic output type, hosted tool drift, and prompt asset drift.

Validation:

```bash
pdm run test:unit
pdm run contract4agents check examples/incident-command
pdm run contract4agents check tests/fixtures/contract_projects/host-drift --strict-drift
```

Definition of done:

- Local fixtures and public examples can prove that every declared local tool has an implementation surface.
- Typos in declared tool sources fail during validation rather than surfacing only during a run.
- Host-owned tools remain possible, but they must be explicit.
- Opt-in drift checks can run in CI and produce actionable diagnostics that name the host code surface involved.

## Run And Trace Contract Source Syntax

Vision gap: current contract files describe agents, evals, assertions, guards, monitors, and composition metadata, but there is no first-class source declaration for the expected behavior of a host-owned multi-agent workflow. Existing agent assertions can express parts of this, but real workflows need a machine-readable declaration of stage outputs, ordering constraints, tool constraints, and run-level invariants.

Design boundary: run contracts describe and verify workflow behavior. They must not introduce branching, loops, retries, checkpointing, recovery, or executable orchestration semantics. If a declaration decides what happens next, it belongs in Python, not in Contract4Agents.

Implementation work:

- Choose a source declaration name, likely `run_contract` or `trace_contract`, and document why that name does not imply executable workflow ownership.
- Add syntax for declaring expected stage or agent outputs and run-level assertions, for example:

  ```contract
  run_contract CompendiumResearch:

      agents = [
          PlannerAgent -> ResearchPlan,
          ResearchManagerAgent -> ResearchAgenda,
          SectionResearchAgent -> SectionResearchBrief,
          VerifierAgent -> VerificationReport,
          SynthesisAgent -> CompendiumPayload,
      ]

      assertions = [
          expect(trace.called_before(PlannerAgent, ResearchManagerAgent)),
          expect(trace.called_before(VerifierAgent, SynthesisAgent)),
          expect(trace.max_calls(VerifierAgent, 2)),
          expect(trace.not_tool_called_by(SynthesisAgent, openai.web_search)),
      ]
  ```

- Reuse the existing expression parser and evaluator for run-level assertions.
- Compile run contracts into a machine-readable artifact that references agent manifests, output schemas, trace assertions, and stage-output expectations.
- Allow `evaluate_run_contract(...)` to evaluate this artifact against normalized trace events and stage outputs emitted by a host application.
- Add static checks for unknown agents, unknown output types, duplicate stage names, trace assertions that reference undeclared agents or tools, and unsupported workflow semantics.
- Make ordering and cardinality checks operate on normalized trace events rather than on source declaration order alone.
- Add parser, compiler, artifact, and runtime evaluation tests for simple linear workflows, repeated per-section agents, optional follow-up passes, forbidden tool use, and missing stage outputs.

Validation:

```bash
pdm run test:unit
pdm run contract4agents check tests/fixtures/contract_projects/run-contracts
```

Definition of done:

- A `.contract` file can declare run-level expectations for a host-owned workflow without defining executable control flow.
- Compiled run-contract artifacts can be evaluated against host-emitted traces and stage outputs.
- Unsupported workflow-like semantics are rejected with diagnostics that point back to the design boundary.

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
