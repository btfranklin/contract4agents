import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {fileURLToPath} from "node:url";
import fs from "node:fs/promises";
import path from "node:path";
import * as esbuild from "esbuild";

const [, , schemaSourcePath] = process.argv;
assert(schemaSourcePath, "usage: read-generated-provenance.mjs schemas.ts");

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const outputDirectory = await fs.mkdtemp(
  path.join(scriptDirectory, ".contract4agents-provenance-"),
);
const source = await fs.readFile(schemaSourcePath, "utf8");
const compiled = (
  await esbuild.transform(source, {
    format: "cjs",
    loader: "ts",
    target: "es2022",
  })
).code;
const compiledPath = path.join(outputDirectory, "schemas.js");
await fs.writeFile(compiledPath, compiled);

try {
  const module = require(compiledPath);
  process.stdout.write(
    JSON.stringify({
      codegenVersion: module.contract4agentsCodegenVersion,
      contractDigest: module.contract4agentsContractDigest,
    }),
  );
} finally {
  await fs.rm(outputDirectory, {recursive: true, force: true});
}
