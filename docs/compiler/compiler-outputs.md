# Compiler Outputs

`contract4agents compile` has one canonical pipeline:

```text
portable source -> parsed AST -> semantic analysis -> canonical IR -> artifacts
```

Every downstream artifact is derived from the immutable canonical IR. The
compiler never imports target application code and never accepts target-specific
permissions, prompts, schemas, or implementation locators as source authority.

## Managed Artifacts

The default output root is `.contract/build`:

```text
ir/
  contract.json
  contract-digest.txt
schemas/
  TypeName.json
instructions/
  AgentName.md
generated/
  python/models.py
  typescript/types.ts
  typescript/schemas.ts
docs/
  summary.md
  agents/AgentName.md
```

- `ir/contract.json` is the deterministic serialized semantic model.
- `ir/contract-digest.txt` identifies the exact contract revision used by
  plans, traces, and assurance results.
- `schemas/` contains standalone JSON Schema derived from structural types and
  string enums.
- `instructions/` contains only model-visible goals, guidance, composition
  descriptions, and controls whose audience explicitly includes `model`.
- `generated/` contains disposable Pydantic, TypeScript, and Zod artifacts.
  String enums become Python `Literal` aliases, TypeScript string unions, and
  Zod `z.enum` schemas.
- `docs/` contains reviewer-facing summaries generated from the IR.

Permissions and output-conformance controls already exist in canonical IR;
target support belongs in the materialization plan; implementations belong in
target bindings. The compiler does not emit a second agent manifest, behavioral
rule pack, adapter capability matrix, or language-specific schema authority.

## Determinism and Freshness

Generation is deterministic for a given canonical IR. Generated code includes
the contract digest and should not be edited manually.

```bash
pdm run contract4agents compile agent_contracts --out .contract/build
pdm run contract4agents compile agent_contracts --out .contract/build --check
```

`--check` reports `COMPILE001` when any managed file is missing, changed, or
stale. A normal compile replaces only managed artifact directories and preserves
adjacent outputs such as visualization or target plans.

Unsafe destinations report `COMPILE002`. The compiler refuses the project root,
the current working directory, and obvious source-owned directories.

## Target Separation

`contract4agents.targets.toml` is not compiler input. `plan` and `materialize`
join target bindings to the canonical IR after compilation and report target
support without changing portable semantics.
