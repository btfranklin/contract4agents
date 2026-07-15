"""Lark grammar and transformers for Contract4Agents expressions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast

from lark import Lark, Transformer, UnexpectedInput
from lark.exceptions import VisitError

from contract4agents.expressions._model import (
    ConditionalExpression,
    ContractExpression,
    ExpressionError,
    ParsedExpression,
)
from contract4agents.expressions._trace_ops import TRACE_OPS, is_trace_op

EXPRESSION_GRAMMAR = r"""
    ?expectation: output_conforms
        | output_compare
        | output_text
        | hidden_truth
        | trace_expr
        | data_relation

    ?contract_expr: expect_wrapper
        | require_wrapper
        | forbid_wrapper
        | when_wrapper
        | expectation

    output_conforms: "output" "conforms" NAME
    output_compare: "output" "." NAME COMPARE_OP COMPARE_VALUE
    output_text: "output" "." NAME TEXT_OP TEXT_VALUE
    hidden_truth: "output" "discovers" "hidden_truth" "." NAME
    semantic_expr: "semantic" "(" NAME "," ESCAPED_STRING ")"

    trace_expr: "trace" "." TRACE_OP "(" [trace_args] ")"
    trace_args: trace_arg ("," trace_arg)*
    trace_arg: ESCAPED_STRING | ARG_VALUE
    data_relation: value_ref SET_OP value_ref
    value_ref: "value" "." NAME

    expect_wrapper: "expect" "(" expectation ")"
    require_wrapper: "require" "(" expectation ")"
    forbid_wrapper: "forbid" "(" "tool" "." DOTTED_NAME forbid_approval? ")"
    forbid_approval: "unless" "approved_by_human"
    when_wrapper: "when" "(" trace_expr "," expect_wrapper ")"

    COMPARE_OP: "==" | "!="
    TEXT_OP: "contains" | "excludes"
    SET_OP: "subset_of" | "contains_all" | "equals_set" | "intersects" | "disjoint_from"
    TRACE_OP: /[A-Za-z_][A-Za-z0-9_]*/
    DOTTED_NAME: /[A-Za-z_][A-Za-z0-9_.]*/
    NAME: /[A-Za-z_][A-Za-z0-9_]*/
    ARG_VALUE: /[^,()\n]+/
    COMPARE_VALUE: /[^)\n]+/
    TEXT_VALUE: /[^)\n]+/

    %import common.ESCAPED_STRING
    %import common.WS_INLINE
    %ignore WS_INLINE
"""

EXPRESSION_PARSER = Lark(
    EXPRESSION_GRAMMAR,
    parser="lalr",
    start=["expectation", "trace_expr", "contract_expr", "semantic_expr"],
)


def parse_expectation(expression: str) -> ParsedExpression:
    """Parse an eval/assertion expectation expression."""
    value = expression.strip()
    return _parse_lark_expression(value, "expectation")


def parse_semantic_expectation(expression: str) -> ParsedExpression:
    """Parse a semantic eval expectation and preserve its rubric text."""
    value = expression.strip()
    return _parse_lark_expression(value, "semantic_expr")


def parse_contract_expression(expression: str) -> list[ContractExpression]:
    """Parse guard/assertion forms enough for static validation."""
    value = expression.strip()
    parsed = _parse_lark_contract_expression(value)
    return parsed if isinstance(parsed, list) else [parsed]


class _ExpressionTransformer(Transformer[Any, Any]):
    def __init__(self, expression: str) -> None:
        super().__init__()
        self.expression = expression

    def output_conforms(self, items: list[Any]) -> ParsedExpression:
        return ParsedExpression("output_conforms", self.expression, type_name=str(items[0]))

    def output_compare(self, items: list[Any]) -> ParsedExpression:
        field, op, raw_expected = items
        return ParsedExpression(
            "output_compare",
            self.expression,
            field=str(field),
            operator=str(op),
            value=literal(str(raw_expected)),
        )

    def output_text(self, items: list[Any]) -> ParsedExpression:
        field, op, needle = items
        return ParsedExpression(
            "output_text",
            self.expression,
            field=str(field),
            operator=str(op),
            value=unquote(str(needle)),
        )

    def hidden_truth(self, items: list[Any]) -> ParsedExpression:
        return ParsedExpression("hidden_truth", self.expression, field=str(items[0]))

    def semantic_expr(self, items: list[Any]) -> ParsedExpression:
        target, criterion = items
        if str(target) != "output":
            raise ExpressionError("Semantic expectations currently support `output` only")
        return ParsedExpression("semantic", self.expression, field=str(target), value=unquote(str(criterion)))

    def trace_expr(self, items: list[Any]) -> ParsedExpression:
        raw_op = str(items[0])
        if not is_trace_op(raw_op):
            raise ExpressionError(f"Unsupported trace spy `{raw_op}`")
        op = raw_op
        args = cast(tuple[str, ...], items[1]) if len(items) > 1 else ()
        spec = TRACE_OPS[op]
        if len(args) != spec.arity:
            raise ExpressionError(f"`trace.{raw_op}` expects {spec.arity} argument(s)")
        if spec.count_arg_index is not None:
            try:
                int(args[spec.count_arg_index])
            except ValueError as exc:
                raise ExpressionError(f"`trace.{raw_op}` count must be an integer") from exc
        return ParsedExpression("trace", self.expression, trace_op=op, args=args)

    def trace_args(self, items: list[Any]) -> tuple[str, ...]:
        return tuple(str(item).strip() for item in items)

    def trace_arg(self, items: list[Any]) -> str:
        return unquote(str(items[0]).strip())

    def data_relation(self, items: list[Any]) -> ParsedExpression:
        left_ref, operator, right_ref = items
        return ParsedExpression(
            "data_relation",
            self.expression,
            operator=str(operator),
            left_ref=str(left_ref),
            right_ref=str(right_ref),
        )

    def value_ref(self, items: list[Any]) -> str:
        return str(items[0])

    def expect_wrapper(self, items: list[Any]) -> ParsedExpression:
        return replace(cast(ParsedExpression, items[0]), wrapper="expect")

    def require_wrapper(self, items: list[Any]) -> ParsedExpression:
        return replace(cast(ParsedExpression, items[0]), wrapper="require")

    def forbid_wrapper(self, items: list[Any]) -> ParsedExpression:
        return ParsedExpression(
            "trace",
            self.expression,
            trace_op="tool_called",
            args=(str(items[0]),),
            wrapper="forbid",
            approval_required=len(items) > 1,
        )

    def forbid_approval(self, _items: list[Any]) -> str:
        return "approved_by_human"

    def when_wrapper(self, items: list[Any]) -> ConditionalExpression:
        return ConditionalExpression(
            self.expression,
            cast(ParsedExpression, items[0]),
            cast(ParsedExpression, items[1]),
        )


def _parse_lark_expression(
    expression: str,
    start: Literal["expectation", "trace_expr", "semantic_expr"],
) -> ParsedExpression:
    parsed = _parse_with_lark(expression, start)
    if not isinstance(parsed, ParsedExpression):
        raise ExpressionError(f"Unsupported expression: {expression}")
    return parsed


def _parse_lark_contract_expression(expression: str) -> ContractExpression:
    parsed = _parse_with_lark(expression, "contract_expr")
    if isinstance(parsed, ParsedExpression | ConditionalExpression):
        return parsed
    raise ExpressionError(f"Unsupported expression: {expression}")


def _parse_with_lark(
    expression: str,
    start: Literal["expectation", "trace_expr", "contract_expr", "semantic_expr"],
) -> Any:
    try:
        tree = EXPRESSION_PARSER.parse(expression, start=start)
        return _ExpressionTransformer(expression).transform(tree)
    except UnexpectedInput as exc:
        raise ExpressionError(f"Unsupported expression: {expression}") from exc
    except VisitError as exc:
        if isinstance(exc.orig_exc, ExpressionError):
            raise exc.orig_exc from exc
        raise


def literal(raw: str) -> Any:
    value = raw.strip()
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
