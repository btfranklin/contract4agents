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
- `trace.agent_called(Name)`
- `trace.datasource_resolved(TypeName)`
- `trace.approval_requested(name)`
- `trace.approval_granted(name)`
- `trace.approval_denied(name)`
- `trace.guardrail_rejected(name)`
- `trace.contains("text")`
- `output discovers hidden_truth.field_name`

Agent assertions in `.contract` files use the same deterministic expression
surface, wrapped as contract assertions:

- `expect(output conforms TypeName)`
- `expect(output.field == value)`
- `expect(trace.tool_called(name))`
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

Semantic expectations use:

```contract
expect semantic(output, "Criterion text")
```

When no semantic judge is configured, semantic expectations are reported as skipped.
