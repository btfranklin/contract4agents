# Eval Language Reference

Eval files define fixture-based behavior checks outside agent definitions.

Eval expectations, assertion expressions, guard expressions, and monitor conditions share the same Lark-backed expression parser. Runtime evaluation and semantic reference extraction are separate implementation steps so unsupported syntax can fail closed consistently.

Supported deterministic expectations:

- `output conforms TypeName`
- `output.field == value`
- `output.field != value`
- `output.field contains value`
- `output.field excludes value`
- `trace.called(name)`
- `trace.not_called(name)`
- `trace.called_once(name)`
- `trace.called_times(name, n)`
- `trace.called_before(a, b)`
- `trace.called_after(a, b)`
- `trace.max_calls(name, n)`
- `trace.tool_called(name)`
- `trace.hosted_tool_called(name)`
- `trace.agent_called(Name)`
- `trace.datasource_resolved(TypeName)`
- `trace.approval_requested(name)`
- `trace.approval_granted(name)`
- `trace.approval_denied(name)`
- `trace.guardrail_rejected(name)`
- `trace.contains("text")`
- `output discovers hidden_truth.field_name`

Trace expressions evaluate over in-memory `TraceRecorder` events. When events
come from disk, load them from canonical schema-versioned trace JSONL using
`load_trace_jsonl(...)`; legacy top-level `type` JSONL is invalid.
Typed trace spies match normalized target fields, not arbitrary payload values:
tool and approval spies match `tool`, agent spies match `agent`, datasource
spies match `datasource` or `produces`, and guardrail spies match `guardrail`.
Use `trace.contains("text")` when a check intentionally searches payload text.
Single-run traces can be evaluated without an explicit run scope. Multi-run
traces require the host or CLI to pass a `run_id`, otherwise evaluation raises a
scope error instead of combining events from separate runs.

Agent assertions in `.contract` files use the same deterministic expression
surface, wrapped as contract assertions:

- `expect(output conforms TypeName)`
- `expect(output.field == value)`
- `expect(trace.tool_called(name))`
- `expect(trace.hosted_tool_called(openai.web_search))`
- `when(trace.tool_called(name), expect(output.field == value))`

Unsupported deterministic expectations fail closed. Semantic analysis reports
unsupported expressions in source files, and the eval runner reports an
`unsupported` failure defensively if an unchecked expression reaches runtime.
The host-callable assertion API follows the same fail-closed rule for unchecked
assertion text.

Hidden-truth values may be scalar strings, which use a loose keyword heuristic,
or explicit matchers such as:

```json
{"contains_all": ["invoice", "credit"]}
{"contains_any": ["rollback", "revert"]}
```

Semantic expectations are parsed separately from deterministic expectations. They
use:

```contract
expect semantic(output, "Criterion text")
```

When no semantic judge is configured, semantic expectations are reported as skipped.
The CLI marks those starts as `PARTIAL` while keeping the default eval exit code
successful; use `contract4agents eval --fail-on-skipped-semantic` when CI should
fail on skipped semantic checks.
Malformed semantic expectation syntax fails semantic analysis instead of being
reported as a skipped semantic check.
