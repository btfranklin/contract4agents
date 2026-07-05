# VS Code Extension

Contract4Agents ships a repo-owned VS Code extension for syntax highlighting.
It colors `.contract` and `.eval` files only. Diagnostics, completions, hover
help, and language-server behavior are future work.

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

## Local Development

The extension source lives in `editors/vscode` from the repository root.

```bash
npm --prefix editors/vscode ci
npm --prefix editors/vscode test
npm --prefix editors/vscode run package
code --install-extension editors/vscode/dist/contract4agents-vscode-*.vsix
```

Generated `.vsix` files are local build output. Do not commit them.

## Release Asset

The extension version follows the repository tag. A tag such as `v0.4.0`
produces `contract4agents-vscode-0.4.0.vsix`.

The tag-triggered `Draft Release Notes` workflow builds the VSIX and attaches it
to the draft GitHub Release. The Python package publishing workflow remains
focused on PyPI; the VSIX is not included in the wheel or source distribution.
