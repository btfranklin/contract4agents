# CLI Reference

All project commands accept an optional `ROOT`, defaulting to the current
directory. Development examples in this repository add the `pdm run` prefix.

## `check [ROOT]`

Parse portable source and run semantic analysis. This command writes nothing,
loads no target bindings, and imports no application code.

```bash
contract4agents check agent_contracts
```

Success prints `Contract4Agents check passed`.

## `compile [ROOT]`

Compile provider-neutral canonical IR and artifacts.

```bash
contract4agents compile agent_contracts --out .contract/build
contract4agents compile agent_contracts --out .contract/build --check
```

Options:

- `--out PATH`: output root; default `.contract/build`.
- `--check`: write nothing and fail if managed artifacts are stale.

Managed output includes canonical IR and digest, JSON Schemas, audience-safe
instructions, Pydantic/TypeScript/Zod source, and generated reviewer docs.
`COMPILE001` reports stale files; `COMPILE002` reports an unsafe destination.

## `generate [ROOT]`

Write only disposable language artifacts derived from canonical IR.

```bash
contract4agents generate agent_contracts --out .contract/generated
contract4agents generate agent_contracts --out .contract/generated --check
```

Options:

- `--out PATH`: output root; default `.contract/generated`.
- `--check`: fail when generated source is missing, modified, extra, or stale.

## `plan [ROOT]`

Resolve a target/profile plan without constructing native agents or executing
business code.

```bash
contract4agents plan agent_contracts \
  --target openai \
  --profile production \
  --out .contract/build/production-plan.json
```

Options:

- `--target NAME`: required adapter target.
- `--profile NAME`: required complete profile.
- `--bindings PATH`: optional binding document override; default
  `ROOT/contract4agents.targets.toml`.
- `--out PATH`: write JSON; otherwise print to stdout.

Planning validates target-binding coverage and inspectable callable shape, then
resolves models, implementations, grants, composition, controls, isolation,
host obligations, caveats, and expected telemetry. Required degraded or
unsupported guarantees fail the command.

## `visualize [ROOT]`

Write a static declared, planned, observed, and assured review graph from the
evidence supplied.

```bash
contract4agents visualize agent_contracts \
  --target openai \
  --profile production \
  --trace run.trace.jsonl \
  --out .contract/build/visualization
```

Options:

- `--target NAME` and `--profile NAME`: optional together; add planned truth.
- `--bindings PATH`: optional binding override for planned truth.
- `--trace PATH`: optional normalized trace; add observed truth. With a plan,
  the command also assesses controls for the assured layer.
- `--out PATH`: default `.contract/build/visualization`.

The command writes deterministic graph data, Mermaid source, and standalone
HTML. Missing layers remain visibly unavailable rather than inferred.

## `eval [ROOT]`

Run a target/profile eval campaign using normalized contract-bound evidence.

```bash
contract4agents eval agent_contracts \
  --target openai \
  --profile test \
  --trials 5 \
  --min-pass-rate 0.8 \
  --max-violation-rate 0.1 \
  --out .contract/eval-results.json
```

Options:

- `--target NAME`: required.
- `--profile NAME`: required.
- `--bindings PATH`: optional target-binding override.
- `--data PATH`: file-backed eval data; default `ROOT/eval-data.json`.
- `--trials N`: trials per eval case; default `1`.
- `--min-pass-rate RATE`: optional campaign threshold from `0` to `1`.
- `--max-violation-rate RATE`: optional threshold from `0` to `1`.
- `--out PATH`: default `ROOT/.contract/eval-results.json`.

The command compiles, plans, derives the runtime inventory, runs every canonical
eval case through `FileEvalProvider`, assesses trace completeness, expectations,
controls, and quality, and writes a deterministic JSON report. Any violated or
unverified trial or failed threshold produces a nonzero exit.

## `assess [ROOT]`

Apply the same contract control assessor to an existing normalized trace.

```bash
contract4agents assess agent_contracts \
  --target openai \
  --profile production \
  --trace run.trace.jsonl
```

Options:

- `--target NAME`, `--profile NAME`: required.
- `--bindings PATH`: optional binding override.
- `--trace PATH`: required normalized trace V2 JSONL.
- `--run-id ID`: optional run selection.

Every control result is printed. Violated or unverified controls produce a
nonzero exit. Assessment derives behavioral requirements from contracts; there
is no separate behavioral rule file. A continuous monitoring service may run
this assessment whenever a complete trace arrives, but the command itself does
not watch a live system.

## `assure [ROOT]`

Assemble a deterministic assurance bundle from declared, planned, and available
observed evidence.

```bash
contract4agents assure agent_contracts \
  --target openai \
  --profile production \
  --trace run.trace.jsonl \
  --eval-results .contract/eval-results.json \
  --provenance provenance.json \
  --out .contract/assurance
```

Options:

- `--target NAME`, `--profile NAME`: required.
- `--bindings PATH`: optional binding override.
- `--trace PATH`: optional normalized trace.
- `--eval-results PATH`: optional eval report JSON.
- `--provenance PATH`: optional provenance JSON.
- `--out PATH`: default `.contract/assurance`.

Missing optional evidence is recorded as an explicit bundle diagnostic. It is
never synthesized into a passing result.

## `diff BEFORE AFTER`

Report assurance-relevant semantic changes between two contract projects.

```bash
contract4agents diff approved-contracts candidate-contracts \
  --out .contract/semantic-diff.json
```

Without `--out`, JSON is printed to stdout. The diff classifies changes in
capability access, authorization, schemas, context, isolation,
controls, audiences, quality, and eval coverage.

## Exit and Error Behavior

Commands emit structured diagnostics to stderr where available and use nonzero
exit status for parse/semantic errors, binding failures, unsupported required
guarantees, invalid traces, violated/unverified control gates, failed eval
campaigns, stale generated output, and unsafe output paths.

Repository documentation validation is available as `pdm run docs-check`; it is
not an installed Contract4Agents command.
