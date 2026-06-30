# Grammar Reference

Contract4Agents source parsing is Lark-driven. The parser transforms Lark parse trees into the stable AST dataclasses used by semantic analysis, schema generation, compiler output, evals, and docs.

The concrete source grammar lives in the parser implementation, and parser internals are documented in `../architecture/parser-internals.md`. Lark trees are not consumed outside the parser boundary.

Contract4Agents V1 supports:

- `type Name:` declarations with indented fields.
- `datasource Name:` declarations with `python`, `requires`, `produces`, `render`, and `cache`.
- `agent Name(params) -> ReturnType:` declarations.
- Inline capability declarations: `use tool|agent|datasource Name from source [permission]`.
- Structured assignments for `goal`, `description`, `policy`, `success`, `routes`, `composition`, `guards`, and `assertions`.
- `.eval` files with `given` and `expect` statements.
- `monitor Name for Agent:` declarations.

General-purpose loops, executable expression blocks, and inline eval suites inside agent files are intentionally out of scope for V1.
