"""Lark grammar for `.contract` and `.eval` modules."""

from __future__ import annotations

from lark import Lark
from lark.indenter import Indenter

MODULE_GRAMMAR = r"""
    start: _NEWLINE* (declaration _NEWLINE*)*

    ?declaration: type_def
        | enum_def
        | tool_def
        | datasource_def
        | external_context_def
        | isolation_def
        | composition_def
        | control_def
        | quality_def
        | operational_control_def
        | agent_def
        | run_spec_def
        | eval_def

    type_def: "type" NAME ":" field_block?
    field_block: _NEWLINE _INDENT field* _DEDENT
    field: NAME ":" SCALAR_VALUE _NEWLINE

    enum_def: "enum" NAME ":" _NEWLINE enum_block?
    enum_block: _INDENT enum_value* _DEDENT
    enum_value: ESCAPED_STRING _NEWLINE

    tool_def: "tool" DOTTED_NAME _LPAR params? _RPAR "->" type_ref ":" assignment_block?

    datasource_def: "datasource" DOTTED_NAME _LPAR params? _RPAR "->" type_ref ":" assignment_block?
    assignment_block: _NEWLINE _INDENT assignment* _DEDENT

    external_context_def: "external_context" DOTTED_NAME "->" type_ref ":" assignment_block?

    isolation_def: "isolation" NAME ":" assignment_block?

    composition_def: "composition" NAME "from" NAME "to" NAME ":" composition_block?
    composition_block: _NEWLINE _INDENT composition_stmt* _DEDENT
    ?composition_stmt: assignment | map_stmt
    map_stmt: "map" NAME "=" SCALAR_VALUE _NEWLINE

    control_def: "control" NAME "for" NAME ":" assignment_block?
    quality_def: "quality" NAME "for" NAME ":" assignment_block?
    operational_control_def: "operational_control" NAME "for" NAME ":" assignment_block?

    agent_def: "agent" NAME _LPAR params? _RPAR "->" NAME ":" agent_block?
    agent_block: _NEWLINE _INDENT agent_stmt* _DEDENT
    params: param (_COMMA _NEWLINE* param)* _COMMA?
    param: NAME ":" type_ref
    ?agent_stmt: grant_stmt | context_stmt | assignment
    grant_stmt: "use" DOTTED_NAME ":" assignment_block?
    context_stmt: "context" NAME ":" type_ref "from" CONTEXT_KIND DOTTED_NAME? (":" context_block | _NEWLINE)
    context_block: _NEWLINE _INDENT map_stmt+ _DEDENT

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

    run_spec_def: "run_spec" NAME ":" run_spec_block?
    run_spec_block: _NEWLINE _INDENT assignment* _DEDENT

    _LPAR: "("
    _RPAR: ")"
    _LSQB: "["
    _RSQB: "]"
    _COMMA: ","
    CONTEXT_KIND: "invocation" | "parent" | "handoff" | "stage" | "datasource" | "external"
    type_ref: type_primary constraint_block? nullable_suffix?
    ?type_primary: NAME                  -> named_type_ref
        | "list" _LSQB type_ref _RSQB  -> list_type_ref
        | "map" _LSQB "string" _COMMA type_ref _RSQB -> map_type_ref
    constraint_block: _LPAR type_constraint (_COMMA type_constraint)* _RPAR
    type_constraint: NAME "=" SIGNED_NUMBER
    nullable_suffix: "?"

    DOTTED_NAME.2: /[A-Za-z_][A-Za-z0-9_.]*/
    NAME: /[A-Za-z_][A-Za-z0-9_]*/
    SCALAR_VALUE: /[^ \t\n\[]+[^\n]*/
    INLINE_LIST_CONTENT: /[^\]\n]+/
    BLOCK_LIST_VALUE: /[^ \t\n\]][^\n]*/
    COMMENT: /#[^\n]*/
    _NEWLINE: /(\r?\n[ \t]*)+/

    %import common.ESCAPED_STRING
    %import common.SIGNED_NUMBER
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
