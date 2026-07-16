# VS Code Extension

Contract4Agents ships a repo-owned VS Code extension for syntax highlighting.
It colors `.contract` and `.eval` files only. Diagnostics, completions, hover
help, and language-server behavior are future work.

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

## Local Development

The extension source lives in `editors/vscode` from the repository root.

```bash
npm --prefix editors/vscode ci
npm --prefix editors/vscode test
npm --prefix editors/vscode run package
code --install-extension editors/vscode/dist/contract4agents-vscode-*.vsix
```

Generated `.vsix` files are local build output. Do not commit them.
The tokenizer suite uses full-surface `.contract` and `.eval` fixtures and
asserts the most specific scope visible to editor themes.

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
