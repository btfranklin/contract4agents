# Implementation Roadmap

This roadmap is the active backlog for work that remains between the current implementation and `VISION.md`. It is not a changelog and it should not preserve finished work.

## Maintenance Rule

Keep this file limited to unimplemented or materially incomplete work. When an item is implemented, documented, and covered by validation, delete its section from this file in the same change. Do not move implemented work into a finished, completed, historical, archived, or done-items section.

If an item no longer belongs in the product, remove it from `VISION.md` first and then delete it from this roadmap.

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
- Reserve `evaluate_run_contract(...)` for evaluating this first-class artifact against normalized trace events and stage outputs emitted by a host application. The current host-callable assertion API remains `evaluate_run_assertions(...)`.
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
