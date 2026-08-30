# Validation and Quality Gates

Contract4Agents validation is offline by default and staged by responsibility.

## Full Local Gate

```bash
pdm install
npm --prefix tests/typescript ci
npm --prefix editors/vscode ci
pdm run validate
```

The composite runs:

- `pdm run lint`: Ruff over source, tests, and examples.
- `pdm run typecheck`: strict mypy over `src`.
- `pdm run docs-check`: repository documentation and link consistency.
- offline unit and integration tests.

Run this before handing off implementation changes.

## Product Vertical Slice

When changing the language, IR, planner, materializer, tracing, assurance, CLI,
or public examples, also run:

```bash
pdm run smoke:cli
```

The smoke suite exercises every public example through the supported
contract-first path: source check, target/profile plan, compilation,
visualization, and eval replay campaign. Replay must consume supplied evidence,
must not invoke a native graph, and must not depend on a second hand-authored
runtime inventory. `scripts/smoke_cli.py` keeps the example and target matrix as
structured data while the PDM command remains the stable entry point.

## Generated Artifact Freshness

For projects that commit or package generated artifacts:

```bash
contract4agents compile agent_contracts --out .contract/build
contract4agents compile agent_contracts --out .contract/build --check
contract4agents generate agent_contracts --target python --out .contract/generated
contract4agents generate agent_contracts --target python --out .contract/generated --check
```

Freshness checks compare deterministic content and detect missing, modified,
extra, or digest-stale compiler files. Generated-source checks cover only the
explicitly selected targets. Generated files are disposable and must not be
edited by hand.

Portable constraint tests run a shared corpus through JSON Schema with format
checking, materialized and generated Pydantic models, and an executed generated
Zod module. The corpus covers Unicode code-point lengths, nested list bounds,
aware RFC 3339 datetimes, naive and space-separated datetimes, impossible dates,
malformed offsets, strict scalar types, and non-finite Python float rejection.
The generated TypeScript and Zod execution harnesses and their Node dependencies
live in the root-owned `tests/typescript` package. They do not depend on the VS
Code extension workspace.

## Planner and Materializer Gates

Provider-neutral planner tests should prove:

- every required binding is present exactly once;
- target bindings cannot override contract-owned semantics;
- callable shape checks never invoke business code;
- adapter binding validators reject ambiguous locator families and statically
  unsupported binding shapes;
- required degraded or unsupported mappings fail closed;
- binding, approval, execution, isolation, and composition combinations use
  contextual adapter support rather than independent global claims;
- models, grants, controls, context, isolation, and telemetry are represented in
  the plan;
- plan serialization and digest are deterministic.

Adapter tests that claim SDK compatibility must construct the installed SDK's
real native objects. Materialization tests should validate the complete graph
configuration and schema evidence against the plan and include a negative case
for every required guarantee that can be unsupported. Native readback must be
kept separate from normalized runtime traces. Known incompatible combinations
must fail during conformance or planning; late materializer checks remain
defense in depth. Configuration evidence must not claim host deadline,
cancellation, token-budget, persistence, retry, or fallback enforcement.

Normal materialization must not call a bound host tool to probe its result.
Static graph and schema conformance cannot prove that application code returns
a valid value. Each concrete adapter must therefore have an offline regression
that invokes its final native tool wrapper with deterministic host code and
passes the result through the provider SDK's tool-result path. Consumer tests
remain responsible for representative application data and business rules.

## Trace and Assurance Gates

Tests should cover:

- duplicate, broken, cyclic, mixed-digest, and malformed trace rejection;
- stable semantic references and provider correlation;
- audience redaction before serialization and export;
- replay audience separation among invocation, host context, evaluator truth,
  and redacted report data;
- absence of evaluator truth from execution requests, judge requests, traces,
  and ordinary replay reports;
- replay reports exporting only the invocation digest and explicit redacted
  report projection, never raw generic inputs;
- trace-evidence assessment against plan event types;
- provider outcome and usage evidence states, safe extraction, aggregation
  identity, and terminal-failure closure;
- operational-control planning, deterministic serialization, single-run
  assessment, unsupported windows, upper-bound asymmetry, and bundle replay;
- missing evidence becoming `unverified`;
- identical control results in eval and production-trace assessment;
- deterministic assurance bundle assembly and internal digest verification;
- semantic diffs for access, authorization, context, isolation, audience,
  control, model, and enforcement changes.

## Packaging

Run a build after changes to package metadata, `README.md`, `LICENSE`, build
configuration, or public package files:

```bash
pdm build
pdm run package-check
```

`pdm run package-check` verifies that the built wheel and source distribution
both contain the `contract4agents/py.typed` marker.

Versioning comes from semantic Git tags through the PDM backend. Do not edit a
static package version. The source distribution must exclude repository-local
examples, generated build output, and stale metadata directories.

## VS Code Extension

When changing `editors/vscode` or its release workflow:

```bash
npm --prefix editors/vscode ci
npm --prefix editors/vscode test
npm --prefix editors/vscode run package
pdm run pytest tests/unit/test_language_server.py
```

The Node suite verifies the grammar, client compilation, interpreter discovery,
and bundled VSIX contents. The Python smoke test starts the packaged language
server entry point over stdio and checks initialization, hover, and definition
navigation. The VSIX is a release asset, not a Python package file.

## Live OpenAI Checks

Normal validation does not call external APIs. The offline adapter suite uses
real SDK classes with deterministic local model behavior. Live provider checks
are opt-in and require `OPENAI_API_KEY`:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
```

The live test materializes the public Incident Command example from contracts,
resolves its declared context, executes the commander and three delegated
specialists through the native Agents SDK, validates structured output, and
correlates SDK spans into normalized contract-bound events. Use it for real
authentication, request compatibility, native agent-as-tool execution, and
model behavior. A skipped live test is not evidence that a live provider path
was exercised.

## Live Strands Checks

The Strands live test is opt-in and uses the normal AWS credential and region
provider chain:

```bash
CONTRACT4AGENTS_RUN_STRANDS_LIVE=1 pdm run test:strands-live
```

It materializes a native Strands agent from the Incident Command target, calls
Bedrock, validates `AgentResult.structured_output`, and closes a normalized
trace attempt. Offline tests use a deterministic fake `Model`; they do not
prove AWS authentication or Bedrock model access. A skipped live test is not
live evidence.

## Live Google ADK Checks

The Google ADK live test is opt-in and requires `GOOGLE_API_KEY`:

```bash
CONTRACT4AGENTS_RUN_GOOGLE_ADK_LIVE=1 pdm run test:google-adk-live
```

It lets host-owned ADK `App`, `Runner`, and session service execute the Market
Research Google Search agent, then asserts grounding, Search suggestion, and
`renderedContent` evidence. A skip is not live evidence.

## Documentation

```bash
pdm run docs-check
```

This is a repository-maintenance command, not an installed product command. It
checks required docs, local Markdown links, and paths listed in the docs index.
