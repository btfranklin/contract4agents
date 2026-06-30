# CLI Reference

The CLI command is `contract4agents`. `ROOT` defaults to the current directory for every command that accepts it.

All contract parse, semantic, and compile failures are printed as Contract4Agents diagnostics with a stable code, message, source location when available, and optional hint. The command exits non-zero after printing diagnostics.

## `check [ROOT]`

Parses `.contract` and `.eval` files under `ROOT` and runs semantic validation.

- Default root: `.`
- Writes: nothing
- Success message: `Contract4Agents check passed`
- Failure message: diagnostics followed by `Contract4Agents check failed`
- Side effects: none

## `compile [ROOT]`

Generates provider-neutral artifacts from a valid project.

- Default root: `.`
- Default output: `.contract/build`
- Options: `--out PATH`, `--check`
- Writes without `--check`: schemas, manifests, instructions, eval packs, monitor packs, adapter capability matrix, and generated docs under `PATH`
- Writes with `--check`: nothing
- Success message: `Contract4Agents compile passed`
- Failure shape: diagnostics; stale generated files use `COMPILE001`
- Side effects: creates parent directories for generated files unless `--check` is used

When `--check` fails with `COMPILE001`, rerun the same compile command without `--check` to refresh generated artifacts.

## `visualize [ROOT]`

Builds a static review graph from parsed contracts and compiler artifacts.

- Default root: `.`
- Default output: `.contract/build/visualization`
- Options: `--out PATH`
- Writes: `graph.json`, `graph.mmd`, and `index.html`
- Success message: `Contract4Agents visualization written to PATH`
- Failure shape: diagnostics from parsing or semantic analysis
- Side effects: creates the output directory

## `eval [ROOT]`

Runs the local deterministic fixture project declared by `ROOT/fixture.json`.

- Default root: `.`
- Writes: `ROOT/.contract/runs/last`
- Success message: fixture summary with per-start `PASS` lines
- Failure shape: Click error `Contract4Agents fixture eval failed: ...` for runner failures, or `Contract4Agents eval failed` after a completed failing report
- Side effects: compiles the fixture, seeds fixture data, writes traces and reports, and cleans transient build/data/trace artifacts only after successful runs unless `CONTRACT4AGENTS_KEEP_FIXTURE_ARTIFACTS=1`

Failed eval runs keep generated artifacts and traces in `ROOT/.contract/runs/last` for debugging.

## `monitor [ROOT] --trace TRACE_JSONL`

Runs project monitors against a recorded trace JSONL file.

- Default root: `.`
- Required options: `--trace TRACE_JSONL`
- Writes: nothing
- Success message: `Contract4Agents monitor passed`
- Failure shape: monitor violations printed as `SEVERITY rule: message`, followed by `Contract4Agents monitor failed`; invalid trace files fail with a Click error
- Side effects: none

## `docs-check [ROOT]`

Checks required repository documentation files and local markdown links.

- Default root: `.`
- Writes: nothing
- Success message: `Docs check passed`
- Failure shape: diagnostics followed by `Docs check failed`
- Side effects: none

## Development Invocation

Use PDM during development:

```bash
pdm run contract4agents --help
```
