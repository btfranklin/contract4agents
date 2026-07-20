# Validation and Quality Gates

Contract4Agents validation is offline by default and staged by responsibility.

## Full Local Gate

```bash
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
visualization, and eval campaign. It must not depend on a second hand-authored
runtime inventory.

## Generated Artifact Freshness

For projects that commit or package generated artifacts:

```bash
contract4agents compile agent_contracts --out .contract/build
contract4agents compile agent_contracts --out .contract/build --check
contract4agents generate agent_contracts --out .contract/generated
contract4agents generate agent_contracts --out .contract/generated --check
```

Freshness checks compare deterministic content and detect missing, modified,
extra, or digest-stale managed files. Generated files are disposable and must
not be edited by hand.

## Planner and Materializer Gates

Provider-neutral planner tests should prove:

- every required binding is present exactly once;
- target bindings cannot override contract-owned semantics;
- callable shape checks never invoke business code;
- required degraded or unsupported mappings fail closed;
- models, grants, controls, context, isolation, and telemetry are represented in
  the plan;
- plan serialization and digest are deterministic.

Adapter tests that claim SDK compatibility must construct the installed SDK's
real native objects. Materialization tests should validate the complete graph
against the plan and include a negative case for every required guarantee that
can be unsupported.

## Trace and Assurance Gates

Tests should cover:

- duplicate, broken, cyclic, mixed-digest, and malformed trace rejection;
- stable semantic references and provider correlation;
- audience redaction before serialization and export;
- trace-evidence assessment against plan event types;
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
```

Versioning comes from semantic Git tags through the PDM backend. Do not edit a
static package version. The source distribution must exclude repository-local
examples, generated build output, and stale metadata directories.

## VS Code Extension

When changing `editors/vscode` or its release workflow:

```bash
npm --prefix editors/vscode ci
npm --prefix editors/vscode test
npm --prefix editors/vscode run package
```

The VSIX is a release asset, not a Python package file.

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

## Documentation

```bash
pdm run docs-check
```

This is a repository-maintenance command, not an installed product command. It
checks required docs, local Markdown links, and paths listed in the docs index.
