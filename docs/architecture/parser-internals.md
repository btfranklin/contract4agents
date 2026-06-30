# Parser Internals

Contract4Agents parsing is Lark-driven, but Lark parse trees are not the stable internal representation. The parser lifecycle is:

```text
.contract/.eval source -> Lark parse tree -> AST dataclasses -> semantic analysis -> compiler/runtime/evals
```

The AST dataclasses in `contract4agents.ast` remain the compiler-facing boundary. Semantic analysis, schema generation, compiler artifacts, eval execution, monitor execution, and docs generation should consume AST objects rather than Lark trees.

## Source Parser Modules

The source parser is intentionally split by responsibility:

- `contract4agents.parser` owns the public API: `parse_project`, `parse_file`, source discovery, and conversion of Lark syntax failures into `ContractError`.
- `contract4agents.parser._grammar` owns the module grammar, indentation handling, and Lark parser instance for `.contract` and `.eval` files.
- `contract4agents.parser._transformer` owns conversion from Lark trees into `TypeDef`, `DatasourceDef`, `AgentDef`, `EvalCase`, `MonitorDef`, and `ContractModule` objects.
- `contract4agents.parser._values` owns scalar, default, quoted-string, and list-value helpers used by the transformer.

The source transformer may emit parse-family diagnostics when the syntax parses but cannot produce a valid AST node, such as a datasource declaration without a Python reference.

## Expression Parser Modules

Eval expectations, monitor rules, guards, and assertions share one expression parser surface:

- `contract4agents.expressions` is the canonical public facade for expression parsing, evaluation, and reference helpers.
- `contract4agents.expressions._model` owns `ParsedExpression`, `ExpressionError`, and expression type aliases used by expression internals.
- `contract4agents.expressions._grammar` owns expression grammar, Lark parser construction, and parse entrypoints.
- `contract4agents.expressions._eval` owns runtime checks against outputs, hidden truth, and traces.
- `contract4agents.expressions._refs` owns semantic-analysis reference extraction.

Unsupported expression syntax must continue to fail closed. Parser entrypoints raise `ExpressionError`; semantic analysis reports diagnostics; eval execution records unsupported failures; monitor execution records invalid-rule violations.

## Boundaries

Do not add parser base classes, plugin registries, or a second AST model unless a concrete new syntax family requires it. The current parser architecture is meant to keep grammar, transformation, and semantic/runtime behavior separate without hiding the simple V1 flow.

Parser golden tests cover both real-world fixture projects and synthetic parser-pressure projects. Real-world goldens protect representative package behavior; synthetic goldens deliberately stress source syntax, expression parsing, datasource declarations, duplicate declaration visibility, and large multi-agent module layout.
