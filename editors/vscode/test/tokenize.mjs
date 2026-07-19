import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const textmate = require("vscode-textmate");
const oniguruma = require("vscode-oniguruma");
const extensionRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const wasmPath = require.resolve("vscode-oniguruma/release/onig.wasm");
const wasm = await fs.readFile(wasmPath);
await oniguruma.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));

const registry = new textmate.Registry({
  onigLib: Promise.resolve({
    createOnigScanner: (patterns) => new oniguruma.OnigScanner(patterns),
    createOnigString: (value) => new oniguruma.OnigString(value),
  }),
  loadGrammar: async (scopeName) => {
    const name = scopeName === "source.contract4agents" ? "contract4agents" : "contract4agents-eval";
    const file = path.join(extensionRoot, `syntaxes/${name}.tmLanguage.json`);
    return textmate.parseRawGrammar(await fs.readFile(file, "utf8"), file);
  },
});

const contractGrammar = await registry.loadGrammar("source.contract4agents");
const evalGrammar = await registry.loadGrammar("source.contract4agents.eval");
assert(contractGrammar);
assert(evalGrammar);

const contractTokens = tokenize(contractGrammar, await fixtureLines("full.contract"));
const evalTokens = tokenize(evalGrammar, await fixtureLines("full.eval"));

const contractExpectations = [
  ["# Complete", "# Complete", "comment.line.number-sign.contract4agents"],
  ["enum ResearchStatus", "enum", "keyword.declaration.enum.contract4agents"],
  ["enum ResearchStatus", "ResearchStatus", "entity.name.type.enum.contract4agents"],
  ["    \"draft\"", "\"draft\"", "constant.other.enum-member.contract4agents"],
  ["type ResearchRequest", "type", "keyword.declaration.type.contract4agents"],
  ["type ResearchRequest", "ResearchRequest", "entity.name.type.contract4agents"],
  ["topic: string", "topic", "variable.other.member.contract4agents"],
  ["topic: string", "string", "support.type.primitive.contract4agents"],
  ["as_of: datetime?", "?", "storage.modifier.nullable.contract4agents"],
  ["tags: list[string]", "list", "storage.type.collection.contract4agents"],
  ["metadata: map[string,string]", "map", "storage.type.collection.contract4agents"],
  ["limit: integer = 5", "=", "keyword.operator.assignment.contract4agents"],
  ["tool sources.search", "tool", "keyword.declaration.capability.contract4agents"],
  ["tool sources.search", "sources.search", "entity.name.function.capability.contract4agents"],
  ["query: string", "query", "variable.parameter.contract4agents"],
  ["datasource accounts.profile", "datasource", "keyword.declaration.capability.contract4agents"],
  ["account_id: string", "account_id", "variable.parameter.contract4agents"],
  [") -> ResearchResult", "->", "keyword.operator.arrow.contract4agents"],
  [") -> ResearchResult", "ResearchResult", "entity.name.type.contract4agents"],
  ["external_context current_account", "external_context", "keyword.declaration.external-context.contract4agents"],
  ["external_context current_account", "current_account", "entity.name.function.context.contract4agents"],
  ["isolation EvidenceWorker", "isolation", "keyword.declaration.isolation.contract4agents"],
  ["isolation EvidenceWorker", "EvidenceWorker", "entity.name.type.isolation.contract4agents"],
  ["agent EvidenceAnalyst", "agent", "keyword.declaration.agent.contract4agents"],
  ["agent EvidenceAnalyst", "EvidenceAnalyst", "entity.name.type.agent.contract4agents"],
  ["use sources.search", "use", "keyword.control.use.contract4agents"],
  ["use sources.search", "sources.search", "support.function.capability.contract4agents"],
  ["context account", "context", "keyword.control.context.contract4agents"],
  ["context account", "external", "constant.language.origin.contract4agents"],
  ["context profile", "datasource", "constant.language.origin.contract4agents"],
  ["map account_id", "map", "keyword.control.map.contract4agents"],
  ["map account_id", "input", "support.variable.mapping.contract4agents"],
  ["Treat control", "control runtime", "string.quoted.double.contract4agents"],
  ["composition investigate", "composition", "keyword.declaration.composition.contract4agents"],
  ["composition investigate", "investigate", "entity.name.function.composition.contract4agents"],
  ["composition investigate", "from", "keyword.control.relationship.contract4agents"],
  ["composition investigate", "to", "keyword.control.relationship.contract4agents"],
  ["control evidence_required", "control", "keyword.declaration.assurance.contract4agents"],
  ["quality evidence_backed", "quality", "keyword.declaration.assurance.contract4agents"],
  ["operational_control latency", "operational_control", "keyword.declaration.assurance.contract4agents"],
  ["eval current_evidence", "eval", "keyword.declaration.eval.contract4agents"],
  ["given request", "given", "keyword.control.given.contract4agents"],
  ["ResearchRequest.fixture", "ResearchRequest", "entity.name.type.contract4agents"],
  ["ResearchRequest.fixture", "fixture", "support.function.fixture.contract4agents"],
  ["run_spec ResearchRun", "run_spec", "keyword.declaration.run-spec.contract4agents"],
  ["evidence: EvidenceAnalyst", "evidence", "entity.name.function.stage.contract4agents"],
  ["optional_review?", "?", "storage.modifier.cardinality.contract4agents"],
  ["repeated_checks+", "+", "storage.modifier.cardinality.contract4agents"],
  ["EvidenceAnalyst -> ResearchResult", "->", "keyword.operator.arrow.contract4agents"],
  ["value.evidence_ids subset_of", "value", "support.variable.expression.contract4agents"],
  ["value.evidence_ids subset_of", "evidence_ids", "variable.other.member.contract4agents"],
  ["forbid(tool.sources", "forbid", "keyword.control.expression.contract4agents"],
  ["forbid(tool.sources", "tool", "support.variable.expression.contract4agents"],
  ["approved_by_human", "approved_by_human", "constant.language.approval.contract4agents"],
  ["when(trace.tool_called", "when", "keyword.control.expression.contract4agents"],
  ["trace.duration", "duration", "variable.other.member.contract4agents"],
  ["p95(trace.duration)", "p95", "support.function.expression.contract4agents"],
  ["window = 15m", "15m", "constant.other.duration.contract4agents"],
  ["< 10s", "<", "keyword.operator.expression.contract4agents"],
];

const evalExpectations = [
  ["# Complete", "# Complete", "comment.line.number-sign.contract4agents-eval"],
  ["eval evaluates_research", "eval", "keyword.declaration.eval.contract4agents-eval"],
  ["eval evaluates_research", "evaluates_research", "entity.name.function.eval.contract4agents-eval"],
  ["eval evaluates_research", "for", "keyword.control.relationship.contract4agents-eval"],
  ["eval evaluates_research", "ResearchLead", "entity.name.type.agent.contract4agents-eval"],
  ["given request", "given", "keyword.control.given.contract4agents-eval"],
  ["ResearchRequest.fixture", "ResearchRequest", "entity.name.type.contract4agents-eval"],
  ["ResearchRequest.fixture", "fixture", "support.function.fixture.contract4agents-eval"],
  ["given expected_status", "ready", "string.unquoted.scalar.contract4agents-eval"],
  ["given expected_summary", "given", "keyword.control.given.contract4agents-eval"],
  ["given expected_summary", "expected_summary", "variable.other.assignment.contract4agents-eval"],
  ["given expected_summary", "=", "keyword.operator.assignment.contract4agents-eval"],
  ["given expected_summary", "\"ready\"", "string.quoted.double.contract4agents-eval"],
  ["expect output conforms", "expect", "keyword.control.expect.contract4agents-eval"],
  ["expect output conforms", "output", "support.variable.expression.contract4agents-eval"],
  ["expect output conforms", "conforms", "keyword.operator.expression.contract4agents-eval"],
  ["output.summary ==", "summary", "variable.other.member.contract4agents-eval"],
  ["output discovers hidden_truth", "discovers", "keyword.operator.expression.contract4agents-eval"],
  ["hidden_truth.answer", "hidden_truth", "support.variable.expression.contract4agents-eval"],
  ["trace.tool_called", "tool_called", "support.function.trace.contract4agents-eval"],
  ["trace.guardrail_rejected", "guardrail_rejected", "support.function.trace.contract4agents-eval"],
  ["quality(evidence_backed)", "quality", "support.function.expression.contract4agents-eval"],
  ["semantic(output", "semantic", "support.function.expression.contract4agents-eval"],
  ["Evidence is current", "\"Evidence is current", "string.quoted.double.contract4agents-eval"],
];

expectScopes(contractTokens, contractExpectations);
expectScopes(evalTokens, evalExpectations);

const enumValues = [
  "enabled", "denied", "preapproved", "approval_required", "host", "provider_hosted", "remote",
  "delegate", "handoff", "none", "summary", "full", "markdown", "json", "text", "run", "thread",
  "public", "internal", "confidential", "restricted", "model", "adapter", "evaluator", "reviewer",
  "static", "runtime", "host_attested", "post_run", "semantic", "advisory", "low", "medium", "high",
  "critical", "explicit_only", "inherited", "declared_only", "fresh", "shared", "ephemeral",
  "inherited_read_only", "allowlisted", "final_output_only", "full_trace",
];
const enumTokens = tokenize(contractGrammar, enumValues.map((value) => `setting = ${value}`));
for (const value of enumValues) {
  expectDeepestScope(enumTokens, `setting = ${value}`, value, "constant.language.enum.contract4agents");
}

const origins = ["invocation", "parent", "handoff", "stage", "datasource", "external"];
const originTokens = tokenize(
  contractGrammar,
  origins.map((origin) => `context item: ResearchResult from ${origin} source.value`),
);
for (const origin of origins) {
  expectDeepestScope(
    originTokens,
    `from ${origin}`,
    origin,
    "constant.language.origin.contract4agents",
  );
}

const traceOperations = [
  "called", "not_called", "called_once", "called_times", "called_before", "called_after", "max_calls",
  "not_tool_called_by", "tool_called", "agent_called", "datasource_resolved", "approval_requested",
  "approval_granted", "approval_denied", "guardrail_rejected", "contains",
];
for (const [grammar, suffix] of [
  [contractGrammar, "contract4agents"],
  [evalGrammar, "contract4agents-eval"],
]) {
  const tokens = tokenize(grammar, traceOperations.map((operation) => `expect trace.${operation}(Target)`));
  for (const operation of traceOperations) {
    expectDeepestScope(tokens, `trace.${operation}`, operation, `support.function.trace.${suffix}`);
  }
}

const expressionOperators = [
  "conforms", "contains", "excludes", "discovers", "subset_of", "contains_all", "equals_set",
  "intersects", "disjoint_from",
];
const contractOperatorTokens = tokenize(
  contractGrammar,
  expressionOperators.map((operator) => `expect output.value ${operator} Target`),
);
for (const operator of expressionOperators) {
  expectDeepestScope(
    contractOperatorTokens,
    ` ${operator} `,
    operator,
    "keyword.operator.expression.contract4agents",
  );
}

const evalOperators = ["conforms", "contains", "excludes", "discovers"];
const evalOperatorTokens = tokenize(
  evalGrammar,
  evalOperators.map((operator) => `expect output.value ${operator} Target`),
);
for (const operator of evalOperators) {
  expectDeepestScope(
    evalOperatorTokens,
    ` ${operator} `,
    operator,
    "keyword.operator.expression.contract4agents-eval",
  );
}

const compositeTypeTokens = tokenize(contractGrammar, [
  "external_context records -> list[ResearchResult]?:",
  "context records: map[string,ResearchResult]? from external current_account",
]);
expectDeepestScope(
  compositeTypeTokens,
  "external_context records",
  "list",
  "storage.type.collection.contract4agents",
);
expectDeepestScope(
  compositeTypeTokens,
  "context records",
  "map",
  "storage.type.collection.contract4agents",
);
expectDeepestScope(
  compositeTypeTokens,
  "context records",
  "ResearchResult",
  "entity.name.type.contract4agents",
);
expectDeepestScope(
  compositeTypeTokens,
  "context records",
  "?",
  "storage.modifier.nullable.contract4agents",
);

const dottedIdentifierTokens = tokenize(contractGrammar, ["expect trace.tool_called(host.search)"]);
expectDeepestScope(
  dottedIdentifierTokens,
  "host.search",
  "host.search",
  "support.function.dotted.contract4agents",
);

const primitiveTokens = tokenize(
  contractGrammar,
  ["string", "integer", "float", "boolean", "datetime"].map((type) => `field: ${type}`),
);
for (const type of ["string", "integer", "float", "boolean", "datetime"]) {
  expectDeepestScope(primitiveTokens, `field: ${type}`, type, "support.type.primitive.contract4agents");
}

const booleanTokens = tokenize(contractGrammar, ["value = true", "value = false", "value = null"]);
for (const value of ["true", "false", "null"]) {
  expectDeepestScope(booleanTokens, `value = ${value}`, value, "constant.language.boolean.contract4agents");
}

async function fixtureLines(name) {
  const value = await fs.readFile(path.join(extensionRoot, "test/fixtures", name), "utf8");
  return value.trimEnd().split(/\r?\n/);
}

function tokenize(grammar, lines) {
  let ruleStack = textmate.INITIAL;
  return lines.flatMap((line) => {
    const result = grammar.tokenizeLine(line, ruleStack);
    ruleStack = result.ruleStack;
    return result.tokens.map((token) => ({
      line,
      text: line.slice(token.startIndex, token.endIndex),
      scopes: token.scopes,
    }));
  });
}

function expectScopes(tokens, expectations) {
  for (const [lineNeedle, textNeedle, scope] of expectations) {
    expectDeepestScope(tokens, lineNeedle, textNeedle, scope);
  }
}

function expectDeepestScope(tokens, lineNeedle, textNeedle, expectedScope) {
  const token = tokens.find((item) => item.line.includes(lineNeedle) && item.text.includes(textNeedle));
  assert(token, `Missing token ${JSON.stringify(textNeedle)} on line containing ${JSON.stringify(lineNeedle)}`);
  assert.equal(
    token.scopes.at(-1),
    expectedScope,
    `${JSON.stringify(token.text)} on ${JSON.stringify(token.line)}: ${token.scopes.join(" ")}`,
  );
}
