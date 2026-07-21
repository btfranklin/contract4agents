# Documentation Index

Contract4Agents has one supported product path:

```text
Declare -> Compile -> Plan -> Materialize -> Run -> Trace -> Assure
```

Portable contracts own agent semantics. Target bindings own target-specific
implementation choices. Generated plans, runtime objects, traces, eval reports,
and assurance bundles are derived from those two authorities.

## Start Here

- [First Contract Project](tutorials/first-contract-project.md) builds the
  smallest contract-first agent from scratch.
- [Using Contract4Agents in an Application](tutorials/using-contract4agents-with-an-agent-app.md)
  covers materialization, host responsibilities, traces, and assurance.
- [Enforcing Business Policy with Host Tools](tutorials/enforcing-business-policy.md)
  shows where transactional rules such as refund eligibility belong, and how
  contracts, approvals, and evidence connect to that host enforcement.
- [Healthcare Workflows: A Safety Pattern](tutorials/healthcare-safety-pattern.md)
  helps regulated healthcare teams decide whether Contract4Agents fits around
  their existing access, policy, and clinical-governance controls.
- [Vendor and Payment Changes: A Safety Pattern](tutorials/vendor-payment-safety-pattern.md)
  shows how agents can analyze and route finance work without receiving payment
  authority or replacing vendor-verification controls.
- [Capture and Assure a Run](tutorials/trace-and-assure.md) completes the
  runtime path with attempt-bound OpenAI capture, closure, assessment, and a
  portable assurance bundle.
- [Incident Command](../examples/incident-command/README.md) is the complete
  beginner-facing example.
- [Vision](../VISION.md) explains the product thesis.

## Define and Compile

- [Contract Language](language/contract-language.md): types, shared capabilities,
  grants, context provenance, composition, controls, quality, isolation, evals,
  and run specs.
- [Compiler Outputs](compiler/compiler-outputs.md): canonical IR, schemas,
  audience-specific instructions, generated code, and freshness checks.
- [Context and Datasources](runtime/context-and-datasources.md): explicit value
  origins, target bindings, provenance, rendering, caching, and evidence.
- [Grammar](reference/grammar.md): compact implemented syntax map.
- [Parser Internals](architecture/parser-internals.md): parser maintenance map.

## Plan, Materialize, and Run

- [System Design](architecture/system-design.md): lifecycle components and
  ownership boundaries.
- [OpenAI Target](reference/openai-adapter.md): planning and native OpenAI
  Agents SDK materialization.
- [CLI](reference/cli.md): public commands and their side effects.
- [Run Specs](reference/run-specs.md): verification of host-owned deterministic
  workflow.

## Trace, Evaluate, and Assure

- [Trace Schema](reference/trace-schema.md): normalized trace identity,
  evidence, validation, redaction, and OpenTelemetry export.
- [Eval Language](reference/eval-language.md): scenario and expectation syntax.
- [Evals, Controls, and Assurance](evaluation/evals-controls-assurance.md):
  repeated campaigns, shared assessment, bundles, and semantic diffs.
- [Semantic Judges](reference/semantic-judge.md): judge evidence requirements.
- [Visualization](reference/visualization.md): declared, planned, observed, and
  assured review views.
- [Deterministic Eval Data](examples/fake-tools-and-data.md): file-backed inputs,
  traces, approval decisions, judge decisions, and metrics.

## Examples

- [Examples Overview](../examples/README.md)
- [Incident Command](../examples/incident-command/README.md)
- [Multi-Lens Research](../examples/multi-lens-research/README.md)
- [Market Research Brief](../examples/market-research-brief/README.md)
- [Demo Team Design Notes](examples/demo-agent-teams.md)

## Project and Contributor References

- [Semantic Model](architecture/semantic-model.md): accepted detailed
  implementation specification.
- [SDK Pattern Survey](research/agent-sdk-pattern-survey.md): provider
  differences the target layer must preserve.
- [Validation and Quality Gates](quality/validation.md)
- [VS Code Extension](reference/vscode-extension.md)
- [Releasing](releasing.md)
- [Open Questions](decisions/open-questions.md)

## Documentation Rules

- `README.md` is the public front door.
- `AGENTS.md` is the coding-agent operating map.
- This index is the documentation map.
- Portable semantics belong in the language and architecture references.
- Target-specific behavior belongs in target references.
- Unresolved decisions belong in `decisions/open-questions.md`.
- Documentation describes the current syntax and one canonical runtime inventory.
