import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {pythonCandidates, resolvePython} from "../out/python.js";

test("configured Python is authoritative and expands the workspace folder", async () => {
  const workspace = path.resolve("example-workspace");
  const probes = [];
  const resolution = await resolvePython(
    [workspace],
    "${workspaceFolder}/tools/python",
    workspace,
    async (candidate) => {
      probes.push(candidate);
      return {ok: true, detail: "ready"};
    },
  );

  assert.equal(resolution.candidate.command, path.join(workspace, "tools/python"));
  assert.equal(probes.length, 1);
});

test("workspace virtual environments precede system interpreters", () => {
  const workspace = path.resolve("example-workspace");
  const candidates = pythonCandidates([workspace], "");

  assert.equal(candidates[0].command, path.join(workspace, ".venv", "bin", "python"));
  assert.equal(candidates[1].command, path.join(workspace, ".venv", "Scripts", "python.exe"));
  assert(candidates.some((candidate) => candidate.command === "python3"));
});

test("discovery reports every failed attempt", async () => {
  const workspace = path.resolve("example-workspace");

  await assert.rejects(
    resolvePython([workspace], "", workspace, async () => ({ok: false, detail: "not importable"})),
    (error) => {
      assert.match(error.message, /No Python interpreter/);
      assert.match(error.message, /workspace \.venv/);
      assert.match(error.message, /not importable/);
      return true;
    },
  );
});
