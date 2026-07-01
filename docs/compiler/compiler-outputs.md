# Compiler Outputs

The Contract4Agents compiler turns source files into artifacts used by humans, host integrations, SDK adapters, eval runners, and monitor runners.

## Compiler Phases

1. Discover source files.
2. Parse `.contract` and `.eval` files into ASTs.
3. Resolve modules and names.
4. Resolve types and schemas.
5. Build the capability and context graph.
6. Classify guards and preserve assertions, policies, and monitors in generated artifacts.
7. Validate eval references.
8. Generate target artifacts.
9. Optionally check generated artifacts for freshness.

Each phase should produce structured diagnostics with source spans.

## Generated Artifacts

### Agent Instructions

Agent instructions are model-facing prose generated from structured fields:

- Agent identity.
- Goal.
- Parameters and rendered context descriptions.
- Allowed tools and agents.
- Policies.
- Success criteria.
- Output contract.
- Failure behavior.

Generated instructions should be stable, compact, and reviewable.

### Provider-Neutral Manifest

The provider-neutral manifest is the machine-facing contract.

Example shape:

```json
{
  "agent": "CustomerGreeter",
  "inputs": [
    {"name": "user_message", "type": "UserMessage", "required": true},
    {"name": "customer_profile", "type": "CustomerProfile", "required": true}
  ],
  "output": {"type": "GreetingResult", "schema_ref": "schemas/GreetingResult.json"},
  "tools": [
    {"name": "calculate", "module": "mathlib", "permission": "preapproved"}
  ],
  "agents": [
    {"name": "BillingAgent", "module": "./billing"},
    {"name": "SupportAgent", "module": "./support"}
  ],
  "datasources": [
    {
      "name": "AccountRejectionStatus",
      "python": "contract_app.datasources.account_rejection_status:resolve",
      "produces": "AccountRejectionStatus",
      "requires": ["CustomerProfile"]
    }
  ],
  "guards": [],
  "assertions": []
}
```

The manifest is provider-neutral. SDK adapters consume it.

### Guard Plan

The compiler emits `guards/guard-plan.json`, a provider-neutral host and adapter
enforcement plan derived from manifest guards.

Supported V1 guard mappings are:

- `require(output conforms TypeName)` -> `output_conformance` with `output_schema` enforcement.
- `forbid(tool.name unless approved_by_human)` -> `approval_required_tool` with host-owned approval enforcement.
- `forbid(tool.name)` -> `denied_tool` with adapter tool omission.

Unsupported parseable guard expressions are emitted as `unsupported` guard-plan
items instead of being silently treated as enforced.

### Adapter Capability Matrix

The compiler produces a capability matrix that says how a manifest maps to the available adapter target.

Example dimensions:

- Instructions or system prompt.
- Tool declarations.
- Permission states: available, preapproved, approval-required, denied, sandboxed.
- Output schema support.
- Tool-plus-output-schema compatibility.
- Runtime context or hidden state.
- Agent-as-tool composition.
- Agent composition.
- Hooks or callbacks.
- Trace capture.

The matrix reports entries with `status` and `caveats`. Common statuses are:

- `supported`: the adapter can represent the Contract4Agents feature directly.
- `partial`: the adapter contributes part of the behavior, but host code must supply provider objects or control flow.
- `emulated`: the adapter can approximate the feature with generated code or instructions.
- `unsupported`: the adapter cannot represent the feature safely.
Unsupported features should produce warnings or compile errors depending on whether they are required by the contract.

### SDK Adapter Config

SDK adapter config maps Contract4Agents concepts to a specific provider SDK while keeping the adapter boundary explicit.

Adapter responsibilities:

- Convert tool manifests into provider tool declarations.
- Convert output types into provider schema formats.
- Convert instructions into provider system/developer/user message structure.
- Capture provider trace events into the Contract4Agents trace schema.
- Carry approval-gate metadata to host code before provider tool calls.
- Emit adapter caveats when target SDK semantics differ from Contract4Agents semantics.

### Eval Pack

Eval packs are generated from `.eval` files and agent manifests. They include:

- Agent under test.
- Input fixture values.
- Context fixtures.
- Expected output checks.
- Expected trace checks.
- Optional semantic rubrics.
- Required mock tools, fake datasources, or fixture providers.

### Monitor Pack

Monitor packs are generated from monitor declarations.

They include:

- Trace queries.
- Violation conditions.
- Severity.
- Suggested remediation.

### Generated Docs

Generated docs should help humans review what will run:

- Agent summary pages.
- Capability tables.
- Context dependency graphs.
- Eval coverage summaries.
- Guard and assertion matrices.

Generated docs must be treated as build artifacts unless the project explicitly chooses to commit them.

### Visualization Artifacts

Visualization artifacts help humans inspect the configured project graph without changing the source of truth.

The V1 visualizer emits a static HTML page plus portable graph files:

- `visualization/graph.json`: stable machine-readable graph model.
- `visualization/graph.mmd`: Mermaid flowchart source.
- `visualization/index.html`: static review page with agent drill-in.

V1 visualization is conservative. It renders declared relationships such as agent, tool, datasource, type, eval, and monitor links. Route and composition metadata is shown in agent detail views, but it is not inferred into graph edges.

## Static Checks

The compiler should fail on:

- Parse errors.
- Unknown references.
- Invalid type definitions.
- Invalid agent signatures.
- Missing return type schemas.
- Missing datasource implementations.
- Ambiguous datasource resolution.
- Tool access that violates declared permissions.
- Eval references to missing trace events or output fields.
- Monitor references to unavailable trace fields.

The compiler can warn on:

- Policies too vague to evaluate.
- Success criteria without corresponding evals or assertions.
- Agents with no eval coverage.
- Datasources with no provenance metadata.
- Tools with side effects but no approval policy.
- Monitors with no severity.

## Diagnostics

Diagnostics should be useful to coding agents. A diagnostic should include:

- Error code.
- Human-readable message.
- Source file and span.
- Why the rule exists.
- Suggested fix.

Example:

```text
CONTEXT001 Missing context resolver
SupportAgent requires AccountRejectionStatus, but CustomerGreeter does not pass it
and no allowed datasource can produce it from available context.

Fix: add `use datasource AccountRejectionStatus from ...` to CustomerGreeter or
pass `problem_summary` explicitly when calling SupportAgent.
```

## Freshness

Once generated artifacts exist, the CLI should support:

```bash
pdm run contract4agents check
pdm run contract4agents compile
pdm run contract4agents compile --check
```

`pdm run contract4agents compile --check` should fail when generated artifacts are stale.
