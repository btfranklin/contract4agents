# Contract4Agents VS Code Extension

Rich language support for Contract4Agents `.contract` and `.eval` files.

This repo-first extension recognizes the Contract4Agents source files used by
the Python toolchain. The grammar covers portable types and capabilities,
agents and grants, context, composition, isolation, controls, quality, evals,
run specs, and their expression vocabulary.

The extension provides syntax and semantic highlighting, hover documentation,
type shapes and callable signatures, contextual completions, diagnostics and
quick fixes, go to definition, references, rename, document and workspace
symbols, and optional inlay hints. The VS Code client contains no independent
language semantics: it starts the Python language server supplied by the
`contract4agents` package.

## Install From A GitHub Release

Download the VSIX from the latest Contract4Agents GitHub Release and install it
with VS Code:

```bash
tmpdir="$(mktemp -d)"
tag="$(gh release view --repo btfranklin/contract4agents --json tagName --jq .tagName)"
gh release download "$tag" --repo btfranklin/contract4agents --pattern "contract4agents-vscode-*.vsix" --dir "$tmpdir"
code --install-extension "$tmpdir"/contract4agents-vscode-*.vsix
```

You can also download the `.vsix` file from the release page and run **Install
from VSIX...** in VS Code.

## Python Discovery

Rich features require `contract4agents` in a Python environment. The extension
tries `contract4agents.pythonPath`, the workspace `.venv`, the Windows launcher,
then `python3` and `python`. `${workspaceFolder}` is supported in the configured
path. Syntax coloring does not depend on Python.

Use **Contract4Agents: Show Language Server Output** to see discovery failures
and server logs. The configured Python path restarts the server automatically;
use **Contract4Agents: Restart Language Server** after changing an environment
in place. The server overlays unsaved documents and retains the last valid
project model during incomplete edits.

## Local Development

```bash
npm ci
npm test
npm run package
code --install-extension dist/contract4agents-vscode-*.vsix
```

Grammar tests tokenize the full current `.contract` and `.eval` surface and
assert the most specific TextMate scopes exposed to VS Code themes. Client tests
cover interpreter resolution; the Python suite exercises an actual stdio
language-server session. `npm run package` validates the VSIX contents.

See the full [extension reference](https://github.com/btfranklin/contract4agents/blob/main/docs/reference/vscode-extension.md)
for architecture, troubleshooting, and isolated installation instructions.
