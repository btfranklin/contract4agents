# Parser Internals

The source pipeline is intentionally simple:

```text
.contract/.eval -> Lark parse tree -> AST -> semantic analysis -> canonical IR
```

Lark trees are private parser detail. AST dataclasses are syntax-oriented and
preserve source spans; canonical IR is the immutable semantic boundary consumed
by compilation, planning, materialization, visualization, tracing, evals, and
assurance.

## Source Parser Modules

- `contract4agents.parser` is the public `parse_project` and `parse_file`
  facade and converts syntax failures to structured `ContractError` diagnostics.
- `contract4agents.parser._grammar` owns the indentation-aware module grammar.
- `contract4agents.parser._transformer` converts Lark trees to AST declarations.
- `contract4agents.parser._values` owns scalar and collection value helpers.

The transformer validates only what is necessary to construct a coherent AST.
Name resolution, ownership rules, type compatibility, grant conflicts, edge
mappings, and assurance semantics belong in semantic analysis.

## Expression Modules

Eval expectations, control expressions, operational-control expressions, and
run-spec relations share one fail-closed expression subsystem:

- `contract4agents.expressions` is the public facade.
- `_grammar` parses supported expressions.
- `_model` owns parsed-expression values and errors.
- `_refs` extracts semantic references for static checks.
- `_eval` evaluates supported output, trace, hidden-truth, and derived-value
  relations against normalized inputs.

Unsupported syntax must fail semantic analysis or become an explicit
unverified runtime result. It must never be silently treated as truthy.

## Maintenance Rules

- Do not add a second AST or hand-authored IR.
- Do not import target implementations during parsing or portable semantic
  analysis.
- Keep stable kind-qualified IDs in the IR, not in source parsing heuristics.
- Extend one grammar, transformer, semantic rule set, IR mapping, and golden
  surface together.
- Use representative public examples plus small syntax-pressure fixtures.
- When syntax is removed, delete its parser path and fixtures instead of adding
  aliases.
