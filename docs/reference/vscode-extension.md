# VS Code Extension

Contract4Agents ships a repo-owned VS Code extension for `.contract` and
`.eval` files. Its TextMate grammar provides immediate syntax coloring. A thin
VS Code client starts the Python language server, which reuses the canonical
parser, semantic checks, and project model instead of maintaining a second
interpretation of the language.

The language server provides:

- hover help for declarations, references, properties, and accepted values;
- type shapes and callable signatures in hover content;
- go to definition, find references, document highlights, and project-wide
  rename;
- context-aware completions for properties, closed vocabularies, and visible
  symbols;
- syntax and semantic diagnostics, with conservative quick fixes for missing
  capability-grant properties;
- document and workspace symbols, semantic highlighting, and optional inlay
  hints.

The extension recognizes the complete portable language surface: types,
capabilities, external context, agents and grants, composition, isolation,
controls, quality, operational controls, evals, run specs, and expressions.
Target-binding TOML and generated JSON artifacts use VS Code's native language
support.

## Install From A Release

Install the latest VSIX from GitHub Releases:

```bash
tmpdir="$(mktemp -d)"
tag="$(gh release view --repo btfranklin/contract4agents --json tagName --jq .tagName)"
gh release download "$tag" --repo btfranklin/contract4agents --pattern "contract4agents-vscode-*.vsix" --dir "$tmpdir"
code --install-extension "$tmpdir"/contract4agents-vscode-*.vsix
```

You can also download the `contract4agents-vscode-*.vsix` release asset and
choose **Install from VSIX...** in VS Code.

## Python Environment

Rich language features require a Python interpreter containing the matching
`contract4agents` package. Syntax coloring remains available if the server
cannot start.

By default, the extension tries these interpreters in order:

1. `contract4agents.pythonPath`, when configured;
2. the workspace's `.venv` interpreter;
3. the Windows Python launcher;
4. `python3`, then `python` from the extension host environment.

For a project managed with PDM, install the package in the project environment
and open the project root as the VS Code workspace:

```bash
pdm add contract4agents
```

While developing this repository, `pdm install` creates the required
environment. If discovery is ambiguous, set `contract4agents.pythonPath` in
workspace settings; the setting accepts `${workspaceFolder}`:

```json
{
  "contract4agents.pythonPath": "${workspaceFolder}/.venv/bin/python"
}
```

Use **Contract4Agents: Show Language Server Output** to inspect every discovery
attempt and the server log. Changing `contract4agents.pythonPath` restarts the
server automatically; use **Contract4Agents: Restart Language Server** after
changing an environment in place.

The server keeps the most recent valid project model while a document contains
an incomplete edit. It reports the current syntax error without discarding
navigation and hover information from the last valid source. Unsaved document
contents always take precedence over files on disk.

Each VS Code workspace folder is a project by default. Nested directories that
contain `contract4agents.targets.toml` are recognized as independent contract
projects, so a repository can contain multiple examples or applications
without mixing their symbol tables or diagnostics.

## Local Development

The extension source lives in `editors/vscode` from the repository root.

```bash
npm --prefix editors/vscode ci
npm --prefix editors/vscode test
npm --prefix editors/vscode run package
code --install-extension editors/vscode/dist/contract4agents-vscode-*.vsix
```

Generated `.vsix` files are local build output. Do not commit them.
The test suite compiles and bundles the client, tokenizes full-surface fixtures,
tests interpreter discovery, and exercises the Python server through a real
stdio protocol session. Packaging also verifies that the VSIX contains the
client bundle and grammars without source, tests, or `node_modules`.

To verify a local package without replacing your installed extension, use
temporary VS Code state:

```bash
tmpdir="$(mktemp -d)"
code --user-data-dir "$tmpdir/user-data" \
  --extensions-dir "$tmpdir/extensions" \
  --install-extension editors/vscode/dist/contract4agents-vscode-*.vsix \
  --force
code --user-data-dir "$tmpdir/user-data" \
  --extensions-dir "$tmpdir/extensions" \
  --list-extensions --show-versions
```

## Release Asset

The extension version follows the repository tag. A tag such as `vX.Y.Z`
produces `contract4agents-vscode-X.Y.Z.vsix`.

The tag-triggered `Draft Release Notes` workflow builds the VSIX and attaches it
to the draft GitHub Release. The Python package publishing workflow remains
focused on PyPI; the VSIX is not included in the wheel or source distribution.
