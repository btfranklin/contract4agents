"""Lark grammar for `.contract` and `.eval` modules."""

from __future__ import annotations

from lark import Lark
from lark.indenter import Indenter

MODULE_GRAMMAR = r"""
    start: _NEWLINE* (declaration _NEWLINE*)*

    ?declaration: python_type_def | type_def | datasource_def | agent_def | run_contract_def | eval_def | monitor_def

    type_def: "type" NAME ":" field_block?
    python_type_def: "type" NAME "from" "python" ESCAPED_STRING
    field_block: _NEWLINE _INDENT field* _DEDENT
    field: NAME ":" SCALAR_VALUE _NEWLINE

    datasource_def: "datasource" NAME ":" assignment_block?
    assignment_block: _NEWLINE _INDENT assignment* _DEDENT

    agent_def: "agent" NAME _LPAR params? _RPAR "->" NAME ":" agent_block?
    agent_block: _NEWLINE _INDENT agent_stmt* _DEDENT
    params: param (_COMMA _NEWLINE* param)* _COMMA?
    param: NAME ":" PARAM_TYPE
    ?agent_stmt: hosted_use_stmt _NEWLINE | use_stmt _NEWLINE | assignment
    use_stmt: "use" USE_KIND DOTTED_NAME "from" SOURCE permission?
    hosted_use_stmt: "use" "hosted_tool" DOTTED_NAME hosted_option? permission?
    hosted_option: NAME ESCAPED_STRING

    permission: "preapproved" -> preapproved
        | "denied" -> denied
        | "available" -> available
        | "sandboxed" -> sandboxed
        | "requires" "approval" -> requires_approval

    assignment: NAME "=" assignment_value _NEWLINE
    ?assignment_value: list_value | scalar_value
    scalar_value: SCALAR_VALUE
    list_value: inline_list | block_list
    inline_list: _LSQB INLINE_LIST_CONTENT? _RSQB
    block_list: _LSQB _NEWLINE _INDENT block_list_item* _DEDENT _RSQB
    block_list_item: BLOCK_LIST_VALUE _NEWLINE

    eval_def: "eval" NAME "for" NAME ":" eval_block?
    eval_block: _NEWLINE _INDENT eval_stmt* _DEDENT
    ?eval_stmt: given_stmt | expect_stmt
    given_stmt: "given" NAME "=" SCALAR_VALUE _NEWLINE
    expect_stmt: "expect" SCALAR_VALUE _NEWLINE

    monitor_def: "monitor" NAME "for" NAME ":" monitor_block?
    monitor_block: _NEWLINE _INDENT monitor_stmt* _DEDENT
    ?monitor_stmt: severity_stmt | when_stmt | monitor_expect_stmt
    severity_stmt: "severity" "=" SCALAR_VALUE _NEWLINE
    when_stmt: "when" SCALAR_VALUE _NEWLINE
    monitor_expect_stmt: "expect" SCALAR_VALUE _NEWLINE

    run_contract_def: "run_contract" NAME ":" run_contract_block?
    run_contract_block: _NEWLINE _INDENT assignment* _DEDENT

    _LPAR: "("
    _RPAR: ")"
    _LSQB: "["
    _RSQB: "]"
    _COMMA: ","
    USE_KIND: "tool" | "agent" | "datasource"
    PARAM_TYPE: /[A-Za-z_][A-Za-z0-9_?]*/
    DOTTED_NAME: /[A-Za-z_][A-Za-z0-9_.]*/
    NAME: /[A-Za-z_][A-Za-z0-9_]*/
    SOURCE: /[^ \t\n]+/
    SCALAR_VALUE: /[^ \t\n\[]+[^\n]*/
    INLINE_LIST_CONTENT: /[^\]\n]+/
    BLOCK_LIST_VALUE: /[^ \t\n\]][^\n]*/
    COMMENT: /#[^\n]*/
    _NEWLINE: /(\r?\n[ \t]*)+/

    %import common.ESCAPED_STRING
    %declare _INDENT _DEDENT
    %ignore /[ \t\f]+/
    %ignore COMMENT
"""


class _ContractIndenter(Indenter):
    NL_type = "_NEWLINE"
    OPEN_PAREN_types = ["_LPAR"]
    CLOSE_PAREN_types = ["_RPAR"]
    INDENT_type = "_INDENT"
    DEDENT_type = "_DEDENT"
    tab_len = 8


MODULE_PARSER = Lark(
    MODULE_GRAMMAR,
    parser="lalr",
    postlex=_ContractIndenter(),
    propagate_positions=True,
    maybe_placeholders=False,
)
