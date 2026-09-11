# Documentation Index

Start with a working agent, then use the references for the features you need.
Contracts define the agents. Target bindings select implementations and models.
Your application supplies the tools and runs the generated SDK agents.

## Start Here

- [First Contract Project](tutorials/first-contract-project.md) builds the
  smallest contract-first agent from scratch.
- [Using Contract4Agents in an Application](tutorials/using-contract4agents-with-an-agent-app.md)
  explains integration choices as your application grows.
- [Incident Command](../examples/incident-command/README.md) is the complete
  beginner-facing multi-agent example.
- [Vision](../VISION.md) explains the product purpose.

## Check Behavior and Business Rules

These guides cover optional assessment and application-specific design:

- [Enforcing Business Policy with Host Tools](tutorials/enforcing-business-policy.md)
  shows where transactional rules such as refund eligibility belong, and how
  contracts, approvals, and evidence connect to that host enforcement.
- [Healthcare Workflow Design Example](tutorials/healthcare-safety-pattern.md)
  helps regulated healthcare teams decide whether Contract4Agents fits around
  their existing access, policy, and clinical-governance controls.
- [Vendor and Payment Workflow Design Example](tutorials/vendor-payment-safety-pattern.md)
  shows how agents can analyze and route finance work without receiving payment
  authority or replacing vendor-verification controls.
- [Capture and Assure a Run](tutorials/trace-and-assure.md) records an OpenAI run,
  checks its evidence, and creates a review bundle.

## Define and Compile

- [Contract Language](language/contract-language.md): types, shared capabilities,
  grants, context provenance, composition, controls, quality, isolation, evals,
  and run specs.
- [Compiler Outputs](compiler/compiler-outputs.md): canonical IR, schemas,
  audience-specific instructions, generated code, and freshness checks.
- [Context and Datasources](runtime/context-and-datasources.md): explicit value
  origins, target bindings, provenance, rendering, caching, and evidence.
- [Grammar](reference/grammar.md): compact implemented syntax map.

## Plan, Materialize, and Run

- [OpenAI Target](reference/openai-adapter.md): planning and native OpenAI
  Agents SDK materialization.
- [Strands Target](reference/strands-adapter.md): native Strands agents,
  interventions, typed tools, and delegation.
- [Google ADK Target](reference/google-adk-adapter.md): native ADK agents,
  confirmation, typed tools, delegation, and Google Search.
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

- [System Design](architecture/system-design.md): components and ownership.
- [Parser Internals](architecture/parser-internals.md): parser maintenance map.
- [Provider Contributor Map](architecture/provider-contributor-map.md): where to
  change provider planning, construction, validation, and tracing.
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
- Tutorials explain one task, its prerequisites, and a usable example.
- References retain detailed rules; design proposals are marked as proposals.
- Portable semantics belong in the language and architecture references.
- Target-specific behavior belongs in target references.
- Unresolved decisions belong in `decisions/open-questions.md`.
- Documentation describes the current syntax and one canonical runtime inventory.
