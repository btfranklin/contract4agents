import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const textmate = require("vscode-textmate");
const oniguruma = require("vscode-oniguruma");
const extensionRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const wasmPath = require.resolve("vscode-oniguruma/release/onig.wasm");
const wasm = await fs.readFile(wasmPath);
await oniguruma.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));

const registry = new textmate.Registry({
  onigLib: Promise.resolve({
    createOnigScanner: oniguruma.createOnigScanner,
    createOnigString: oniguruma.createOnigString,
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

const contractTokens = tokenize(contractGrammar, [
  "tool crm.create_note(request: NoteRequest) -> NoteResult:",
  "agent SupportAgent(request: SupportRequest) -> SupportReply:",
  "    use crm.create_note:",
  "        availability = enabled",
  "        authorization = approval_required",
  "    context account: Account from datasource crm.account:",
  "        map account_id = input.request.account_id",
  '    guidance = ["prefer verified evidence"]',
  "composition investigate from SupportAgent to ResearchAgent:",
  "    mode = delegate",
  "control approval_evidence for SupportAgent:",
  "    assessment = post_run",
  "    require = trace.approval_granted(crm.create_note)",
  "quality useful_reply for SupportAgent:",
  '    rubric = "The reply is useful."',
  "run_spec SupportRun:",
  "    assertions = [expect(trace.called(SupportAgent))]",
]);

expectScope(contractTokens, "tool crm", "tool", "keyword.declaration.contract4agents");
expectScope(contractTokens, "use crm", "crm.create_note", "support.function.capability.contract4agents");
expectScope(contractTokens, "availability", "enabled", "constant.language.enum.contract4agents");
expectScope(contractTokens, "context account", "datasource", "constant.language.origin.contract4agents");
expectScope(contractTokens, "map account_id", "map", "keyword.control.map.contract4agents");
expectScope(contractTokens, "mode = delegate", "delegate", "constant.language.enum.contract4agents");
expectScope(contractTokens, "trace.approval", "approval_granted", "support.function.trace.contract4agents");

const evalTokens = tokenize(evalGrammar, [
  "eval useful_reply for SupportAgent:",
  '    given request = SupportRequest.fixture("default")',
  "    expect output conforms SupportReply",
  "    expect trace.tool_called(crm.create_note)",
  "    expect quality(useful_reply)",
]);

expectScope(evalTokens, "given request", "given", "keyword.control.given.contract4agents-eval");
expectScope(evalTokens, "SupportRequest.fixture", "SupportRequest", "entity.name.type.contract4agents-eval");
expectScope(evalTokens, "output conforms", "conforms", "keyword.operator.expression.contract4agents-eval");
expectScope(evalTokens, "trace.tool_called", "tool_called", "support.function.trace.contract4agents-eval");

function tokenize(grammar, lines) {
  let ruleStack = textmate.INITIAL;
  return lines.flatMap((line) => {
    const result = grammar.tokenizeLine(line, ruleStack);
    ruleStack = result.ruleStack;
    return result.tokens.map((token) => ({line, text: line.slice(token.startIndex, token.endIndex), scopes: token.scopes}));
  });
}

function expectScope(tokens, lineNeedle, textNeedle, expectedScope) {
  const token = tokens.find((item) => item.line.includes(lineNeedle) && item.text.includes(textNeedle));
  assert(token, `Missing token ${textNeedle}`);
  assert(token.scopes.includes(expectedScope), `${token.text}: ${token.scopes.join(" ")}`);
}
