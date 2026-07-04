# Compiler Outputs

The Contract4Agents compiler turns source files into artifacts used by humans, host integrations, SDK adapters, eval runners, and monitor runners.

## Compiler Phases

1. Discover source files.
2. Parse `.contract` and `.eval` files into ASTs.
3. Resolve modules and names.
4. Resolve types and schemas.
5. Build declared capability metadata and runtime context metadata.
6. Classify guards and preserve assertions, policies, monitors, and run contracts in generated artifacts.
7. Validate eval, monitor, and run-contract references against declared agent reachability.
8. Generate target artifacts.
9. Optionally check generated artifacts for freshness.

Each phase should produce structured diagnostics with source spans.

## Generated Artifacts

### Agent Instructions

Agent instructions are model-facing prose generated from structured fields:

- Agent identity.
- Goal.
- Parameters and rendered context descriptions.
- Allowed host tools, hosted provider tools, and agents.
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
  "source_path": "agents/customer_greeter.contract",
  "inputs": [
    {"name": "user_message", "type": "UserMessage", "required": true, "python_ref": null},
    {"name": "customer_profile", "type": "CustomerProfile", "required": true, "python_ref": null}
  ],
  "output": {"type": "GreetingResult", "schema_ref": "schemas/GreetingResult.json", "python_ref": null},
  "host_context": [
    {"type": "AccountRejectionStatus", "python_ref": null}
  ],
  "tools": [
    {"name": "calculate", "module": "mathlib", "permission": "preapproved"}
  ],
  "hosted_tools": [
    {
      "name": "openai.web_search",
      "provider": "openai",
      "tool": "web_search",
      "config": {"context_size": "medium"},
      "permission": "available"
    }
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

The manifest is provider-neutral, but it can carry provider-native capability
metadata explicitly. Host Python tools stay in `tools`; provider-hosted tools
stay in `hosted_tools` so adapters and capability reports can distinguish what
the host must wire from what a provider SDK may supply.

When a type is imported from a Pydantic model, the manifest preserves the import
path on matching input, output, and host-context references as `python_ref`.

### Capability Registry Checks

`contract4agents.registry.json` is a source-owned validation file, not a
generated artifact. When present, `contract4agents check` validates its shape.
`contract4agents check --strict-drift` requires it and compares compiled
manifest declarations against explicit host surfaces:

- Python tool refs import and are callable, unless marked `external: true`.
- Tool and hosted-tool permissions match the manifest.
- Hosted-tool provider, tool, and config match the manifest.
- Registered agent names and factory imports match contract agent declarations.
- Registered Pydantic output classes match contract output schemas.
- Registered prompt assets exist and point at known agents.
- Manifest `host_context` entries are marked as host-provided.

Strict drift checks import only configured registry refs and never call tools,
factories, hosted-tool providers, or business workflows.

### Type Bindings

The compiler emits `types/type-bindings.json` beside `schemas/*.json`. Each entry
records the contract type name, whether the type came from native contract
fields or a Python model import, the Python import path when applicable, the
schema artifact path, and a deterministic schema hash.

Native schemas remain one file per declared type. When a native type references
another native type, the schema keeps `#/$defs/TypeName` field references and
embeds the reachable native type definitions in that artifact's `$defs` block so
the schema validates as a standalone JSON Schema document.

Python-backed types require `--allow-python-imports` during compile so host code
is not imported accidentally. `compile --check --allow-python-imports` catches
stale schemas and stale type bindings when a Pydantic model changes.

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
- Hosted provider tool declarations.
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
- Provide a typed adapter plan before constructing provider SDK objects, including
  source paths, instruction refs, schema refs, hosted tools, assertions,
  composition metadata, and caveats.

The OpenAI adapter also exposes a generated-output-model helper for the native
Contract4Agents JSON Schema subset and a single-agent run helper that renders
non-sensitive `RuntimeContext` values, resolves SDK approval interruptions
through host callbacks, and records post-run assertion checks.

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

### Run Contracts

Run contracts are generated from `run_contract` declarations. They describe
expected host-owned workflow behavior without executable orchestration.

The compiler emits `run-contracts/run-contracts.json` with:

- Run-contract name and source path.
- Stage name, agent, output type, cardinality, manifest ref, and schema ref.
- Trace assertions over the normalized run trace.

Host applications evaluate the artifact with `evaluate_run_contract(...)` after
they have emitted normalized trace events and collected stage outputs. Required
and optional single stages validate one output object; repeated `+` stages
validate a non-empty sequence of output objects.

### Generated Docs

Generated docs should help humans review what will run:

- `docs/summary.md`: project-level index of agents, types, evals, monitors, run contracts, and hosted tools.
- `docs/agents/*.md`: per-agent pages with signature, intent, inputs, output,
  host context, capabilities, checks, evals, monitors, and artifact links.
- Capability tables.
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

The compiler currently fails on:

- Parse errors.
- Unknown references.
- Invalid type definitions.
- Invalid agent signatures.
- Missing return type schemas.
- Duplicate top-level declarations.
- Malformed agent attributes.
- Invalid hosted-tool provider metadata for bundled descriptors.
- Ambiguous datasource declarations for the same agent and produced type.
- Child-agent parameters that cannot be satisfied from parent required inputs,
  declared host context, or deterministic parent datasource chains.
- Datasource requirement cycles while proving child-agent context.
- Guards and assertions that reference unavailable local tools, hosted tools, output fields, or types.
- Eval and monitor references to unavailable output fields, types, or trace targets outside the scoped agent's declared `use agent` dependency closure.

With `contract4agents check --strict-drift`, project checks also fail on missing
capability registry entries, unresolved registry Python refs, permission drift,
hosted-tool config drift, agent-name drift, registered Pydantic output-type
drift, prompt asset drift, and unmarked host-provided context.

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
SEM072 Missing child context
CustomerGreeter cannot supply required context AccountRejectionStatus for child
agent SupportAgent.

Fix: add `AccountRejectionStatus` as a required parent parameter, declare
`host_context = [AccountRejectionStatus]`, or add a datasource chain on
`CustomerGreeter` that can produce it.
```

Strict drift diagnostics use `CAP###` codes; see
[Capability Registry Reference](../reference/capability-registry.md).

## Freshness

Once generated artifacts exist, the CLI should support:

```bash
pdm run contract4agents check
pdm run contract4agents check --strict-drift
pdm run contract4agents compile
pdm run contract4agents compile --check
```

`pdm run contract4agents compile --check` should fail when generated artifacts are stale.
Compiler output paths are guarded before any managed artifact directory is
removed. Writing directly to the project root or to obvious source-owned
top-level directories such as `docs` fails with `COMPILE002`; use a generated
artifact directory such as `.contract/build`.
