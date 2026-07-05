# Contract4Agents VS Code Extension

Syntax highlighting for Contract4Agents `.contract` and `.eval` files.

This repo-first extension recognizes the Contract4Agents source files used by
the Python toolchain. It provides coloring only; diagnostics, completions,
hover help, and language-server behavior are future work.

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

## Local Development

```bash
npm ci
npm run package
code --install-extension dist/contract4agents-vscode-*.vsix
```
