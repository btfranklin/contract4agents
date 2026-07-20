import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

const [vsixPath] = process.argv.slice(2);
assert(vsixPath, "usage: node test/verify-package.mjs <extension.vsix>");

const files = new Set(
  execFileSync("unzip", ["-Z1", vsixPath], { encoding: "utf8" })
    .split("\n")
    .filter(Boolean),
);

for (const required of [
  "extension/package.json",
  "extension/dist/extension.js",
  "extension/language-configuration.json",
  "extension/syntaxes/contract4agents.tmLanguage.json",
  "extension/syntaxes/contract4agents-eval.tmLanguage.json",
]) {
  assert(files.has(required), `packaged extension is missing ${required}`);
}

for (const file of files) {
  assert(!file.startsWith("extension/node_modules/"), `packaged dependency leaked into VSIX: ${file}`);
  assert(!file.startsWith("extension/src/"), `TypeScript source leaked into VSIX: ${file}`);
  assert(!file.startsWith("extension/test/"), `test source leaked into VSIX: ${file}`);
}

console.log(`Verified ${vsixPath} (${files.size} archive entries).`);
