import assert from "node:assert/strict";
import {fileURLToPath} from "node:url";
import {createRequire} from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import * as esbuild from "esbuild";

const [, , schemaSourcePath, schemaName, corpusPath] = process.argv;
assert(schemaSourcePath && schemaName && corpusPath, "usage: execute-generated-zod.mjs schema.ts Type corpus.json");

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const outputDirectory = await fs.mkdtemp(path.join(scriptDirectory, ".contract4agents-zod-"));
const source = await fs.readFile(schemaSourcePath, "utf8");
const compiled = (await esbuild.transform(source, {format: "cjs", loader: "ts", target: "es2022"})).code;
const compiledPath = path.join(outputDirectory, "schemas.js");
await fs.writeFile(compiledPath, compiled);

try {
  const module = require(compiledPath);
  const schema = module[`${schemaName}Schema`];
  assert(schema, `Generated schema ${schemaName}Schema was not exported`);
  const corpus = JSON.parse(await fs.readFile(corpusPath, "utf8"));
  for (const entry of corpus) {
    const actual = schema.safeParse(entry.value).success;
    assert.equal(actual, entry.valid, `${entry.name}: expected ${entry.valid}, got ${actual}`);
  }
} finally {
  await fs.rm(outputDirectory, {recursive: true, force: true});
}
