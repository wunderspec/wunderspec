"""
Translator to TLA+.

This module provides functionality to translate Wunderspec specifications
to TLA+ format for use with TLA+ tools like TLC and Apalache.

The translation uses a two-phase pipeline:
1. AST -> Doc: _node_to_doc() translates AST nodes to wadler_lindig Doc objects
2. Doc -> String: _render_doc() renders the Doc tree to a properly formatted string

This approach preserves structural information needed for correct column
alignment of conjunctions and disjunctions.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar, cast

from wadler_lindig import AbstractDoc, BreakDoc, ConcatDoc, GroupDoc, NestDoc, TextDoc

from wunderspec.ast.action_ast import (
    ActionAndNode,
    ActionCallNode,
    ActionChoiceNode,
    ActionLetNode,
    ActionNode,
    AssignNode,
    AssumeNode,
    NondetChoiceNode,
)
from wunderspec.ast.ast import (
    AlgebraNode,
    AlgebraOp,
    ExprCallNode,
    InNode,
    IteNode,
    LetNode,
    LitNode,
    Node,
    QuantOp,
    VarNode,
)
from wunderspec.ast.list_ast import (
    ListEnumNode,
    ListFilterNode,
    ListGetNode,
    ListKeysNode,
    ListRangeNode,
    ListReduceNode,
    ListSliceNode,
    ListUpdateNode,
)
from wunderspec.ast.map_ast import (
    MapEnumNode,
    MapGetNode,
    MapKeysNode,
    MapLambdaNode,
    MapSetNode,
)
from wunderspec.ast.record_ast import RecordCtorNode, RecordGetNode, RecordUpdateNode
from wunderspec.ast.set_ast import (
    AllMapsNode,
    AllRecordsNode,
    AllSubsetsNode,
    AllTuplesNode,
    ChooseNode,
    IntervalNode,
    SetEnumNode,
    SetFilterNode,
    SetIntOrNatNode,
    SetMapNode,
    SetQuantNode,
    SetReduceNode,
)
from wunderspec.ast.sorts import (
    BoolSort,
    EnumSort,
    IntSort,
    ListSort,
    MapSort,
    RecordSort,
    SetSort,
    Sort,
    StrSort,
    TupleSort,
    UnionSort,
)
from wunderspec.ast.temporal_ast import (
    AlwaysNode,
    EnabledNode,
    EventuallyNode,
    Fair,
    FairnessNode,
    ToTemporalNode,
)
from wunderspec.ast.tuple_ast import TupleCtorNode, TupleGetNode, TupleUpdateNode
from wunderspec.ast.union_ast import UnionCtorNode, UnionGetTagNode, UnionMatchNode
from wunderspec.doc_format import AlignDoc, HardLine, render_doc, with_text_indent
from wunderspec.machine import MachineState
from wunderspec.petnames import funny_name
from wunderspec.sym_context import ExtractedActionDef
from wunderspec.uniq_names import fresh_name

_APALACHE_TYPE_KEYWORDS = {
    "Bool",
    "Int",
    "Seq",
    "Set",
    "Str",
    "UNIT",
    "Variant",
}


@dataclass(frozen=True)
class ExtractedExprDef:
    """Definition of a non-inline @expr extracted as a TLA+ operator."""

    param_names: tuple[str, ...]
    param_sorts: tuple["Sort", ...]
    result_sort: "Sort"
    body: Node


def _render_tla_doc(doc: AbstractDoc, *, text_width: int, text_indent: int) -> str:
    return render_doc(with_text_indent(doc, text_indent), text_width)


# ---------------------------------------------------------------------------
# Utility helpers (unchanged from original)
# ---------------------------------------------------------------------------


def _span_suffix(node: "Node") -> str:
    """Return a human-readable source location suffix if the node has a span."""
    span = getattr(node, "source_span", None)
    if span is None:
        return ""
    filename = span.filename or "<unknown>"
    return f" (at {filename}:{span.lineno}:{span.col_offset})"


def _union_sort_key(sort: UnionSort) -> str:
    """Return a stable key for a concrete union sort."""
    petname = funny_name({"union_sort": sort.name}).replace("-", "_")
    return f"UnionCtor_{petname}"


def _union_ctor_name(sort: UnionSort, tag: str) -> str:
    """Return the helper operator name for a concrete union constructor."""
    return f"{_union_sort_key(sort)}_{tag}"


def _union_tag_names(sort: UnionSort) -> dict[str, str]:
    """Return Apalache-safe variant tag names for a union sort."""
    names: dict[str, str] = {}
    used: set[str] = set()
    for tag, _ in sort.variants:
        base = tag
        if not re.fullmatch(r"[A-Z][A-Za-z0-9_]*", base):
            parts = [part for part in re.split(r"[^A-Za-z0-9]+", tag) if part]
            base = "".join(part[:1].upper() + part[1:] for part in parts)
            if not base or not re.match(r"[A-Z]", base):
                base = f"V{base}"
        if base in _APALACHE_TYPE_KEYWORDS:
            base = f"{base}Tag"
        safe = base
        suffix = 2
        while safe in used:
            safe = f"{base}{suffix}"
            suffix += 1
        names[tag] = safe
        used.add(safe)
    return names


def _union_tag_name(sort: UnionSort, tag: str) -> str:
    """Return the generated TLA+ runtime tag for a union variant."""
    return _union_tag_names(sort)[tag]


def _union_tag_string(sort: UnionSort, tag: str) -> str:
    """Return a quoted generated TLA+ runtime tag string."""
    return f'"{_union_tag_name(sort, tag)}"'


def _union_original_tag_doc(union_doc: AbstractDoc, sort: UnionSort) -> AbstractDoc:
    """Render the user-facing tag for a variant value with escaped runtime tags."""
    tag_names = _union_tag_names(sort)
    if all(tag == safe for tag, safe in tag_names.items()):
        return TextDoc("VariantTag(") + union_doc + TextDoc(")")

    runtime_tag = TextDoc("VariantTag(") + union_doc + TextDoc(")")
    result: AbstractDoc = runtime_tag
    for tag, _ in reversed(sort.variants):
        safe = tag_names[tag]
        if safe == tag:
            continue
        result = (
            TextDoc("IF ")
            + runtime_tag
            + TextDoc(f' = "{safe}" THEN "{tag}" ELSE ')
            + result
        )
    return TextDoc("(") + result + TextDoc(")")


def _iter_child_nodes(value: object) -> list[Node]:
    """Yield child AST nodes nested in a node attribute value."""
    nodes: list[Node] = []
    if isinstance(value, Node):
        nodes.append(value)
    elif isinstance(value, dict):
        for key, child in value.items():
            nodes.extend(_iter_child_nodes(key))
            nodes.extend(_iter_child_nodes(child))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            nodes.extend(_iter_child_nodes(child))
    return nodes


def _collect_union_sorts(
    node: Node, seen: dict[tuple[tuple[str, Sort | None], ...], UnionSort]
) -> None:
    """Recursively collect concrete union sorts used by constructors."""
    if isinstance(node, UnionCtorNode):
        sort = node.sort
        assert isinstance(sort, UnionSort)
        seen.setdefault(sort.variants, sort)

    for value in vars(node).values():
        for child in _iter_child_nodes(value):
            _collect_union_sorts(child, seen)


def _union_ctor_defs(union_sorts: list[UnionSort]) -> list[str]:
    """Render typed helper operators for concrete union constructors."""
    lines: list[str] = []
    for union_sort in union_sorts:
        union_type = _sort_to_tla_type(union_sort)
        nested_union_type = _sort_to_tla_type(union_sort, nested=True)
        for tag, payload_sort in union_sort.variants:
            helper_name = _union_ctor_name(union_sort, tag)
            if payload_sort is None:
                lines.append(f"\\* @type: {union_type};")
                lines.append(
                    f"{helper_name} == Variant({_union_tag_string(union_sort, tag)}, "
                    f'"U_OF_UNIT")'
                )
            else:
                payload_type = _sort_to_tla_type(payload_sort, nested=True)
                lines.append(f"\\* @type: ({payload_type}) => {nested_union_type};")
                lines.append(
                    f"{helper_name}(payload) == "
                    f"Variant({_union_tag_string(union_sort, tag)}, payload)"
                )
            lines.append("")
    return lines


def _is_empty_set_literal(node: Node) -> bool:
    return isinstance(node, SetEnumNode) and len(node.elements) == 0


def _matches_nonempty_guard(condition: Node, base_set: Node) -> bool:
    match condition:
        case AlgebraNode(op=AlgebraOp.NE, args=(left, right)):
            return (left == base_set and _is_empty_set_literal(right)) or (
                right == base_set and _is_empty_set_literal(left)
            )
        case AlgebraNode(op=AlgebraOp.NOT, args=(inner,)):
            if not isinstance(inner, AlgebraNode) or inner.op != AlgebraOp.EQ:
                return False
            left, right = inner.args
            return (left == base_set and _is_empty_set_literal(right)) or (
                right == base_set and _is_empty_set_literal(left)
            )
        case _:
            return False


def _simplify_action_nonempty_guards(node: ActionNode) -> ActionNode:
    """Remove immediate non-empty guards implied by following action existentials."""
    match node:
        case ActionAndNode():
            simplified_actions: list[ActionNode] = [
                _simplify_action_nonempty_guards(action) for action in node.actions
            ]
            filtered: list[ActionNode] = []
            changed = False
            i = 0
            while i < len(simplified_actions):
                action = simplified_actions[i]
                next_action: ActionNode | None = None
                if i + 1 < len(simplified_actions):
                    next_action = simplified_actions[i + 1]
                if (
                    next_action is not None
                    and isinstance(action, AssumeNode)
                    and isinstance(next_action, NondetChoiceNode)
                    and _matches_nonempty_guard(action.condition, next_action.base_set)
                ):
                    changed = True
                    i += 1
                    continue
                filtered.append(action)
                i += 1
            if not changed and all(
                original is simplified
                for original, simplified in zip(node.actions, simplified_actions)
            ):
                return node
            return ActionAndNode(*filtered)
        case ActionChoiceNode():
            simplified_choice_actions: tuple[ActionNode, ...] = tuple(
                _simplify_action_nonempty_guards(action) for action in node.actions
            )
            if all(
                original is simplified
                for original, simplified in zip(node.actions, simplified_choice_actions)
            ):
                return node
            return ActionChoiceNode(*simplified_choice_actions, labels=node.labels)
        case NondetChoiceNode():
            simplified_body = _simplify_action_nonempty_guards(
                cast(ActionNode, node.body)
            )
            if simplified_body is node.body:
                return node
            return NondetChoiceNode(node.var, node.base_set, simplified_body)
        case ActionLetNode():
            simplified_body = _simplify_action_nonempty_guards(
                cast(ActionNode, node.body)
            )
            if simplified_body is node.body:
                return node
            return ActionLetNode(node.name, node.value, simplified_body)
        case ActionCallNode():
            simplified_body = _simplify_action_nonempty_guards(
                cast(ActionNode, node.body)
            )
            if simplified_body is node.body:
                return node
            return ActionCallNode(
                node.action_name,
                node.args,
                simplified_body,
                placeholder_body=node.placeholder_body,
            )
        case _:
            return node


def _needs_apalache_helpers(rendered_chunks: list[str]) -> bool:
    """Return whether rendered TLA uses Apalache helper operators."""
    return any("ApaFold" in chunk for chunk in rendered_chunks)


def _needs_tlc(rendered_chunks: list[str]) -> bool:
    """Return whether rendered TLA uses TLC operators (:>, @@, SetAsFun)."""
    return any(" :> " in chunk or "SetAsFun(" in chunk for chunk in rendered_chunks)


def _typed_local_op_let(
    name: str,
    params: list[VarNode],
    result_sort: Sort,
    body_doc: AbstractDoc,
    in_doc: AbstractDoc,
) -> AbstractDoc:
    """Return a typed LET-IN that defines a local operator and yields *in_doc*."""
    params_types = ", ".join(
        _sort_to_tla_type(param.sort, nested=True) for param in params
    )
    params_names = ", ".join(_tla_var_name(param) for param in params)
    result_type = _sort_to_tla_type(result_sort, nested=True)
    return GroupDoc(
        TextDoc("LET ")
        + TextDoc(f"(* @type: ({params_types}) => {result_type}; *)")
        + HardLine()
        + TextDoc(f"{name}({params_names}) ==")
        + NestDoc(ConcatDoc(HardLine(), body_doc), indent=4)
        + HardLine()
        + TextDoc("IN")
        + NestDoc(ConcatDoc(HardLine(), in_doc), indent=4)
    )


def _tuple_ctor_name(arity: int) -> str:
    return f"WSMkTuple{arity}"


def _seq_ctor_name(arity: int) -> str:
    return f"WSMkSeq{arity}"


def _tuple_ctor_defs(arities: list[int], *, text_indent: int) -> list[str]:
    lines: list[str] = []
    indent = " " * text_indent
    for arity in sorted(set(arities)):
        params = [f"x{i}" for i in range(arity)]
        type_vars = [chr(ord("a") + i) for i in range(arity)]
        type_sig = ", ".join(type_vars)
        tuple_type = ", ".join(type_vars)
        if params:
            lines.append(f"\\* @type: ({type_sig}) => <<{tuple_type}>>;")
            lines.append(f"{_tuple_ctor_name(arity)}({', '.join(params)}) ==")
            lines.append(f"{indent}<<{', '.join(params)}>>")
        else:
            lines.append("\\* @type: <<>>;")
            lines.append(f"{_tuple_ctor_name(arity)} ==")
            lines.append(f"{indent}<<>>")
        lines.append("")
    return lines


def _seq_ctor_defs(arities: list[int], *, text_indent: int) -> list[str]:
    lines: list[str] = []
    indent = " " * text_indent
    for arity in sorted(set(arities)):
        params = [f"x{i}" for i in range(arity)]
        if params:
            type_sig = ", ".join("a" for _ in range(arity))
            lines.append(f"\\* @type: ({type_sig}) => Seq(a);")
            lines.append(f"{_seq_ctor_name(arity)}({', '.join(params)}) ==")
            lines.append(f"{indent}<<{', '.join(params)}>>")
        else:
            lines.append("\\* @type: Seq(a);")
            lines.append(f"{_seq_ctor_name(arity)} ==")
            lines.append(f"{indent}<<>>")
        lines.append("")
    return lines


def _collect_literal_ctor_arities(
    node: Node,
    tuple_arities: set[int],
    seq_arities: set[int],
) -> None:
    """Recursively collect tuple and sequence literal arities used in a spec."""
    match node:
        case TupleCtorNode():
            tuple_arities.add(len(node.elements))
            for element in node.elements:
                _collect_literal_ctor_arities(element, tuple_arities, seq_arities)
        case ListEnumNode():
            seq_arities.add(len(node.elements))
            for element in node.elements:
                _collect_literal_ctor_arities(element, tuple_arities, seq_arities)
        case AlgebraNode(op=AlgebraOp.LIST_CONCAT) if (
            isinstance(node.args[1], ListEnumNode) and len(node.args[1].elements) == 1
        ):
            # Single-element concat renders as Append(left, elem): the one-element
            # list literal is inlined, so it needs no WSMkSeq1 helper.
            _collect_literal_ctor_arities(node.args[0], tuple_arities, seq_arities)
            _collect_literal_ctor_arities(
                node.args[1].elements[0], tuple_arities, seq_arities
            )
        case _:
            for child in node.__dict__.values():
                if isinstance(child, Node):
                    _collect_literal_ctor_arities(child, tuple_arities, seq_arities)
                elif isinstance(child, dict):
                    for key, value in child.items():
                        if isinstance(key, Node):
                            _collect_literal_ctor_arities(
                                key, tuple_arities, seq_arities
                            )
                        if isinstance(value, Node):
                            _collect_literal_ctor_arities(
                                value, tuple_arities, seq_arities
                            )
                elif isinstance(child, (tuple, list, set, frozenset)):
                    for item in child:
                        if isinstance(item, Node):
                            _collect_literal_ctor_arities(
                                item, tuple_arities, seq_arities
                            )


def _is_action_node(node: Node) -> bool:
    """Check whether a node is an action-level AST node."""
    return isinstance(
        node,
        (
            AssumeNode,
            AssignNode,
            ActionAndNode,
            ActionChoiceNode,
            NondetChoiceNode,
            ActionLetNode,
        ),
    )


def _action_assigns(node: Node) -> set[str]:
    """Compute assigns(N): state vars assigned directly or through action children."""
    match node:
        case AssignNode():
            if isinstance(node.var, VarNode):
                return {node.var.name}
            return set()
        case AssumeNode():
            return set()
        case ActionAndNode():
            assigned: set[str] = set()
            for action in node.actions:
                assigned.update(_action_assigns(action))
            return assigned
        case ActionChoiceNode():
            assigned = set()
            for action in node.actions:
                assigned.update(_action_assigns(action))
            return assigned
        case NondetChoiceNode():
            return _action_assigns(node.body)
        case ActionCallNode():
            return _action_assigns(node.body)
        case ActionLetNode():
            return _action_assigns(node.body)
        case _:
            return set()


def _ordered_var_names(
    var_names: set[str], state_vars: tuple[str, ...] | None
) -> list[str]:
    """Order variable names by state declaration order when known."""
    if state_vars:
        order = {name: i for i, name in enumerate(state_vars)}
        return sorted(var_names, key=lambda name: (order.get(name, len(order)), name))
    return sorted(var_names)


def _unchanged_clause(var_names: set[str], state_vars: tuple[str, ...] | None) -> str:
    """Render UNCHANGED for one or more variables."""
    ordered = _ordered_var_names(var_names, state_vars)
    if not ordered:
        raise ValueError("UNCHANGED clause requires at least one variable")
    if len(ordered) == 1:
        return f"UNCHANGED {ordered[0]}"
    return f"UNCHANGED <<{', '.join(ordered)}>>"


def _to_camel_case(name: str) -> str:
    """Convert snake_case to CamelCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def _sort_to_tla_type(sort: Sort, *, nested: bool = False) -> str:
    """Convert a wunderspec Sort to a TLA+ type annotation string."""
    match sort:
        case IntSort():
            return "Int"
        case BoolSort():
            return "Bool"
        case StrSort():
            return "Str"
        case EnumSort():
            return sort.enum_type.__name__.upper()
        case SetSort():
            elem_type = _sort_to_tla_type(sort.elem_sort, nested=True)
            return f"Set({elem_type})"
        case MapSort():
            key_type = _sort_to_tla_type(sort.key_sort, nested=True)
            value_type = _sort_to_tla_type(sort.value_sort, nested=True)
            type_str = f"{key_type} -> {value_type}"
            return f"({type_str})" if nested else type_str
        case ListSort():
            elem_type = _sort_to_tla_type(sort.elem_sort, nested=True)
            return f"Seq({elem_type})"
        case TupleSort():
            elem_types = ", ".join(
                _sort_to_tla_type(s, nested=True) for s in sort.elem_sorts
            )
            return f"<<{elem_types}>>"
        case RecordSort():
            field_strs = ", ".join(
                f"{name}: {_sort_to_tla_type(field_sort, nested=True)}"
                for name, field_sort in sort.fields
            )
            return "{" + field_strs + "}"
        case UnionSort():
            variant_strs = " | ".join(
                (
                    f"{_union_tag_name(sort, tag)}({_sort_to_tla_type(payload, nested=True)})"
                    if payload
                    else f"{_union_tag_name(sort, tag)}(UNIT)"
                )
                for tag, payload in sort.variants
            )
            return variant_strs
        case _:
            raise NotImplementedError(f"Sort {sort} not supported for TLA+ translation")


def _state_to_tla_header(
    state_cls: type[MachineState],
    name: str,
    extends: list[str] | None = None,
    *,
    text_width: int,
    text_indent: int,
) -> str:
    """Generate TLA+ module header from state class."""
    lines = []
    indent = " " * text_indent

    # Module header (centered in text_width chars)
    module_text = f"MODULE {name}"
    total_dashes = text_width - len(module_text) - 2
    left_dashes = total_dashes // 2
    right_dashes = total_dashes - left_dashes
    lines.append("-" * left_dashes + " " + module_text + " " + "-" * right_dashes)

    # EXTENDS clause
    if extends:
        lines.append(f"EXTENDS {', '.join(extends)}")
        lines.append("")

    # CONSTANTS section (if any params)
    if state_cls._params:
        lines.append("CONSTANTS")
        for i, param_name in enumerate(state_cls._params):
            descriptor = getattr(state_cls, param_name)
            type_str = _sort_to_tla_type(descriptor.sort)
            lines.append(f"{indent}\\* @type: {type_str};")
            if i < len(state_cls._params) - 1:
                lines.append(f"{indent}{param_name},")
            else:
                lines.append(f"{indent}{param_name}")
        lines.append("")

    # VARIABLES section
    if state_cls._vars:
        lines.append("VARIABLES")
        for i, var_name in enumerate(state_cls._vars):
            descriptor = getattr(state_cls, var_name)
            type_str = _sort_to_tla_type(descriptor.sort)
            lines.append(f"{indent}\\* @type: {type_str};")
            if i < len(state_cls._vars) - 1:
                lines.append(f"{indent}{var_name},")
            else:
                lines.append(f"{indent}{var_name}")

    return "\n".join(lines)


def _tla_var_name(name_or_var: str | VarNode) -> str:
    """Convert a Python variable name to a valid TLA+ identifier."""
    if isinstance(name_or_var, VarNode):
        name = name_or_var.unique_name or name_or_var.name
    else:
        name = name_or_var
    if name == "_":
        return fresh_name("_unused")
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not sanitized or not sanitized[0].isalpha():
        sanitized = f"v{sanitized}"
    return sanitized


# ---------------------------------------------------------------------------
# EXCEPT helpers (string-based, for inline expressions)
# ---------------------------------------------------------------------------


def _extract_get_info(node: Node) -> tuple[Node, object] | None:
    """Extract ``(base, key)`` from a GET node."""
    match node:
        case MapGetNode():
            return (node.map_node, node.key)
        case TupleGetNode():
            return (node.tuple_node, node.index)
        case ListGetNode():
            return (node.list_node, node.index)
        case RecordGetNode():
            return (node.record_node, node.field_name)
    return None


def _extract_set_info(node: Node) -> tuple[Node, object, Node] | None:
    """Extract ``(base, key, value)`` from a SET/UPDATE node."""
    match node:
        case MapSetNode():
            return (node.base_map, node.update_key, node.update_value)
        case TupleUpdateNode():
            return (node.base_tuple, node.index, node.new_value)
        case ListUpdateNode():
            return (node.base_list, node.index, node.new_value)
        case RecordUpdateNode():
            if len(node.updates) == 1:
                field_name, value = node.updates[0]
                return (node.base_record, field_name, value)
    return None


def _comma_separated_docs(docs: list[AbstractDoc]) -> AbstractDoc:
    """Interleave docs with ``TextDoc(", ")`` separators and concatenate."""
    parts: list[AbstractDoc] = []
    for i, d in enumerate(docs):
        if i > 0:
            parts.append(TextDoc(", "))
        parts.append(d)
    return ConcatDoc(*parts) if parts else TextDoc("")


def _breakable_comma_separated_docs(docs: list[AbstractDoc]) -> AbstractDoc:
    """Interleave docs with comma separators that may break across lines."""
    parts: list[AbstractDoc] = []
    for i, d in enumerate(docs):
        if i > 0:
            parts.append(TextDoc(",") + BreakDoc(" "))
        parts.append(d)
    return ConcatDoc(*parts) if parts else TextDoc("")


def _call_doc(
    name: str, args: list[AbstractDoc], *, break_first: bool = False
) -> AbstractDoc:
    """Render a function/operator call with breakable arguments."""
    if not args:
        return TextDoc(name)
    args_doc = _breakable_comma_separated_docs(args)
    if break_first:
        args_doc = ConcatDoc(BreakDoc(""), args_doc)
    return GroupDoc(TextDoc(f"{name}(") + NestDoc(args_doc, indent=4) + TextDoc(")"))


def _let_in_doc(
    name: str, value_doc: AbstractDoc, body_doc: AbstractDoc, *, parenthesized: bool
) -> AbstractDoc:
    """Render a LET-IN expression with breakable value and body clauses."""
    if parenthesized:
        return (
            TextDoc(f"(LET {name} ==")
            + NestDoc(ConcatDoc(HardLine(), value_doc), indent=4)
            + HardLine()
            + TextDoc("IN")
            + NestDoc(ConcatDoc(HardLine(), body_doc), indent=4)
            + TextDoc(")")
        )
    doc = GroupDoc(
        TextDoc(f"LET {name} ==")
        + NestDoc(ConcatDoc(BreakDoc(" "), value_doc), indent=4)
        + BreakDoc(" ")
        + TextDoc("IN")
        + NestDoc(ConcatDoc(BreakDoc(" "), body_doc), indent=4)
    )
    return doc


def _format_except_key(key: object, set_node: Node) -> AbstractDoc:
    """Format a single EXCEPT key fragment as a Doc."""
    match set_node:
        case MapSetNode():
            assert isinstance(key, Node)
            return TextDoc("[") + _node_to_doc(key) + TextDoc("]")
        case TupleUpdateNode():
            assert isinstance(key, int)
            return TextDoc(f"[{key + 1}]")  # 0-based -> 1-based
        case ListUpdateNode():
            assert isinstance(key, Node)
            return TextDoc("[(") + _node_to_doc(key) + TextDoc(") + 1]")
        case RecordUpdateNode():
            assert isinstance(key, str)
            return TextDoc(f".{key}")
        case _:
            raise NotImplementedError(
                f"Unsupported SET node type: {type(set_node)}{_span_suffix(set_node)}"
            )


def _needs_postfix_base_parens(node: Node) -> bool:
    """Whether ``node`` must be parenthesized before postfix/EXCEPT syntax.

    TLA+ parses postfix access/update syntax (``.field``, ``[k]``, ``EXCEPT``)
    tightly. Compound expressions such as ``IF``/``LET`` must therefore be
    wrapped before appending postfix syntax, or the parser/typechecker will
    associate the postfix operator with only the final branch/body.
    """

    return not isinstance(
        node,
        (
            LitNode,
            VarNode,
            RecordCtorNode,
            RecordGetNode,
            RecordUpdateNode,
            MapEnumNode,
            MapLambdaNode,
            MapGetNode,
            MapSetNode,
            TupleCtorNode,
            TupleGetNode,
            TupleUpdateNode,
            ListEnumNode,
            ListGetNode,
            ListUpdateNode,
        ),
    )


def _postfix_base_doc(node: Node) -> AbstractDoc:
    """Render a base expression that is safe to extend with postfix syntax."""

    base_doc = _node_to_doc(node)
    if _needs_postfix_base_parens(node):
        return TextDoc("(") + base_doc + TextDoc(")")
    return base_doc


def _needs_operand_parens(node: Node) -> bool:
    """Whether ``node`` must be wrapped when emitted as an operand.

    TLA+ has no explicit terminator for ``IF ... THEN ... ELSE ...``,
    ``LET ... IN ...``, quantifiers, and ``CHOOSE``.  When one of these forms is
    placed next to an infix or prefix operator, SANY extends the branch/body as
    far as possible unless parentheses mark the intended operand boundary.
    """

    return isinstance(
        node,
        (
            IteNode,
            LetNode,
            ChooseNode,
            SetQuantNode,
            UnionMatchNode,
            ActionAndNode,
            ActionChoiceNode,
            ActionLetNode,
            AssignNode,
            AssumeNode,
            NondetChoiceNode,
        ),
    )


def _operand_doc(node: Node) -> AbstractDoc:
    """Render an expression that is safe to place inside a larger expression."""

    doc = _node_to_doc(node)
    if _needs_operand_parens(node):
        return TextDoc("(") + doc + TextDoc(")")
    return doc


def _try_collapse_except(node: LetNode) -> AbstractDoc | None:
    """Try to collapse a LetNode chain into a TLA+ multi-level EXCEPT.

    Returns ``None`` when the pattern does not match.

    Handles both the original two-binding form::

        LET _t0 == root IN LET _t1 == _t0[k1] IN [_t0 EXCEPT ![k1] = [_t1 ...]]

    and the compact form (where the root variable is used directly without
    a trivial alias binding)::

        LET _t1 == root[k1] IN [root EXCEPT ![k1] = [_t1 EXCEPT ![k2] = v]]
    """
    bindings: list[tuple[str, Node]] = []
    current: Node = node
    while isinstance(current, LetNode):
        bindings.append((current.name, current.value))
        current = current.body

    if len(bindings) < 1:
        return None

    # Determine root_expr and build keys_from_gets.
    #
    # Standard form: bindings[0] value is the root expression (a VarNode),
    # and bindings[1..] are GETs chaining off previous binding names.
    #
    # Inlined form: bindings[0] value is itself a GET (the trivial
    # ``LET alias == var`` was removed).  We extract the root from the GET
    # base and prepend its key to keys_from_gets.  The outer SET's base
    # will reference the root directly (not bindings[0] name).
    first_get = _extract_get_info(bindings[0][1])
    inlined_root: VarNode | None = None
    keys_from_gets: list[object] = []

    if first_get is not None and isinstance(first_get[0], VarNode):
        # Inlined form: root was folded into the first GET.
        inlined_root = first_get[0]
        root_expr: Node = inlined_root
        keys_from_gets.append(first_get[1])
        # Remaining bindings chain off previous binding names as usual.
        for i in range(1, len(bindings)):
            _name, value = bindings[i]
            prev_name = bindings[i - 1][0]
            get_info = _extract_get_info(value)
            if get_info is None:
                return None
            base, key = get_info
            if not isinstance(base, VarNode) or base.name != prev_name:
                return None
            keys_from_gets.append(key)
    else:
        # Standard form: first binding value is the root expression.
        root_expr = bindings[0][1]
        for i in range(1, len(bindings)):
            _name, value = bindings[i]
            prev_name = bindings[i - 1][0]
            get_info = _extract_get_info(value)
            if get_info is None:
                return None
            base, key = get_info
            if not isinstance(base, VarNode) or base.name != prev_name:
                return None
            keys_from_gets.append(key)

    # Walk the nested SET nodes.  In standard form, num_sets == len(bindings)
    # and the i-th SET base must match bindings[i] name.  In inlined form,
    # there is one extra SET level (the outermost references the root
    # directly).
    num_sets = len(bindings) + (1 if inlined_root is not None else 0)
    key_doc_parts: list[AbstractDoc] = []
    keys_from_sets: list[object] = []
    set_node: Node = current
    final_value: Node | None = None

    for i in range(num_sets):
        set_info = _extract_set_info(set_node)
        if set_info is None:
            return None
        base, key, value = set_info
        if not isinstance(base, VarNode):
            return None
        # In inlined form, the first SET base references the root variable.
        if inlined_root is not None:
            expected_name = inlined_root.name if i == 0 else bindings[i - 1][0]
        else:
            expected_name = bindings[i][0]
        if base.name != expected_name:
            return None
        keys_from_sets.append(key)
        key_doc_parts.append(_format_except_key(key, set_node))
        if i < num_sets - 1:
            set_node = value
        else:
            final_value = value

    for get_key, set_key in zip(keys_from_gets, keys_from_sets):
        if get_key != set_key:
            return None

    assert final_value is not None
    root_doc = _postfix_base_doc(root_expr)
    keys_doc = ConcatDoc(*key_doc_parts) if key_doc_parts else TextDoc("")
    value_doc = _node_to_doc(final_value)
    return GroupDoc(
        TextDoc("[")
        + root_doc
        + TextDoc(" EXCEPT !")
        + keys_doc
        + TextDoc(" =")
        + NestDoc(ConcatDoc(BreakDoc(" "), value_doc), indent=4)
        + TextDoc("]")
    )


# ---------------------------------------------------------------------------
# Doc building helpers
# ---------------------------------------------------------------------------


def _junct_doc(op: str, items: list[AbstractDoc]) -> AbstractDoc:
    """Build a conjunction or disjunction list (always multi-line).

    Each element is prefixed with ``op`` (e.g. "/\\\\" or "\\\\/") and
    continuation lines within each element are indented past the operator.

    The whole list is wrapped in ``AlignDoc`` so that every ``/\\`` or ``\\/``
    prefix is rendered at the same column as the first one, regardless of how
    deeply the list is nested inside quantifiers or set-filter expressions.
    """
    parts: list[AbstractDoc] = []
    for i, item in enumerate(items):
        if i > 0:
            parts.append(HardLine())
        parts.append(TextDoc(f"{op} "))
        parts.append(NestDoc(item, indent=3))
    return AlignDoc(ConcatDoc(*parts))


def _conjoin_action_doc_with_clause(
    action_doc: AbstractDoc, clause_str: str
) -> AbstractDoc:
    """Append an UNCHANGED clause to an action doc.

    Uses ``GroupDoc`` so that simple actions produce inline conjunctions
    (e.g. ``x' = 1 /\\ UNCHANGED y``) while multi-line actions get the
    clause on a new line.
    """
    return GroupDoc(ConcatDoc(action_doc, BreakDoc(" "), TextDoc(f"/\\ {clause_str}")))


def _if_then_else_doc(
    condition: AbstractDoc, then_doc: AbstractDoc, else_doc: AbstractDoc
) -> AbstractDoc:
    """Render an IF expression with branch points that can wrap cleanly."""
    return GroupDoc(
        TextDoc("IF ")
        + condition
        + BreakDoc(" ")
        + TextDoc("THEN")
        + NestDoc(ConcatDoc(BreakDoc(" "), then_doc), indent=4)
        + BreakDoc(" ")
        + TextDoc("ELSE")
        + NestDoc(ConcatDoc(BreakDoc(" "), else_doc), indent=4)
    )


# ---------------------------------------------------------------------------
# AST -> Doc translation
# ---------------------------------------------------------------------------


def _node_to_doc(
    node: Node,
    *,
    state_vars: tuple[str, ...] | None = None,
    is_init: bool = False,
    bound_vars: tuple[str, ...] = (),
) -> AbstractDoc:
    """Convert an AST node to a wadler_lindig Doc.

    This is the core translation function. The resulting Doc tree captures the
    structural information needed for proper column alignment of conjunctions,
    disjunctions, and other multi-line TLA+ constructs.
    """
    if _is_action_node(node):
        simplified = _simplify_action_nonempty_guards(cast(ActionNode, node))
        if simplified is not node:
            return _node_to_doc(
                simplified,
                state_vars=state_vars,
                is_init=is_init,
                bound_vars=bound_vars,
            )

    match node:
        # --- Literals ---
        case LitNode():
            if isinstance(node.value, bool):
                return TextDoc("TRUE" if node.value else "FALSE")
            elif isinstance(node.value, Enum):
                enum_cls_name = node.value.__class__.__name__.upper()
                return TextDoc(f'"{node.value.name}_OF_{enum_cls_name}"')
            elif isinstance(node.value, str):
                return TextDoc(f'"{node.value}"')
            else:
                return TextDoc(str(node.value))

        # --- Variables ---
        case VarNode():
            return TextDoc(_tla_var_name(node))

        # --- Arithmetic operators ---
        case AlgebraNode(op=AlgebraOp.ADD):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" + ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.SUB):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" - ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.MUL):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" * ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.DIV):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" \\div ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.MOD):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" % ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.POW):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" ^ ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.NEG):
            return TextDoc("(-") + _operand_doc(node.args[0]) + TextDoc(")")

        # --- Comparison operators ---
        case AlgebraNode(op=AlgebraOp.LT):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" < ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.LE):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" <= ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.GT):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" > ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.GE):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" >= ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.EQ):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" = ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.NE):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" /= ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )

        # --- Boolean operators ---
        case AlgebraNode(op=AlgebraOp.AND):
            if len(node.args) > 1:
                items = [_operand_doc(a) for a in node.args]
                return _junct_doc("/\\", items)
            else:
                return _node_to_doc(node.args[0])
        case AlgebraNode(op=AlgebraOp.OR):
            if len(node.args) > 1:
                items = [_operand_doc(a) for a in node.args]
                return _junct_doc("\\/", items)
            else:
                return _node_to_doc(node.args[0])
        case AlgebraNode(op=AlgebraOp.NOT):
            return TextDoc("~(") + _node_to_doc(node.args[0]) + TextDoc(")")
        case AlgebraNode(op=AlgebraOp.IMPLIES):
            return (
                TextDoc("(")
                + _node_to_doc(node.args[0])
                + TextDoc(") => (")
                + _node_to_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.IFF):
            return (
                TextDoc("(")
                + _node_to_doc(node.args[0])
                + TextDoc(") <=> (")
                + _node_to_doc(node.args[1])
                + TextDoc(")")
            )

        # --- Set operations ---
        case SetEnumNode():
            if not node.elements:
                return TextDoc("{}")
            elem_docs = [_operand_doc(e) for e in node.elements]
            return GroupDoc(
                TextDoc("{")
                + NestDoc(_breakable_comma_separated_docs(elem_docs), indent=4)
                + TextDoc("}")
            )

        case IntervalNode():
            return (
                TextDoc("(")
                + _node_to_doc(node.lower)
                + TextDoc(")..(")
                + _node_to_doc(node.upper)
                + TextDoc(")")
            )

        case SetIntOrNatNode():
            return TextDoc("Int" if node.is_signed else "Nat")

        case AlgebraNode(op=AlgebraOp.UNION):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" \\union ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.INTERSECT):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" \\intersect ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.DIFFERENCE):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" \\ ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.CARDINALITY):
            return TextDoc("Cardinality(") + _operand_doc(node.args[0]) + TextDoc(")")
        case AlgebraNode(op=AlgebraOp.SUBSETEQ):
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" \\subseteq ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )
        case AlgebraNode(op=AlgebraOp.FLATTEN):
            return TextDoc("UNION ") + _operand_doc(node.args[0])

        # --- Set membership ---
        case InNode():
            return (
                TextDoc("(")
                + _operand_doc(node.elem)
                + TextDoc(" \\in ")
                + _operand_doc(node.set_node)
                + TextDoc(")")
            )

        # --- Set quantifiers ---
        case SetQuantNode(quant=QuantOp.FORALL):
            binding_docs: list[AbstractDoc] = []
            for i, (v, d) in enumerate(node.bindings):
                if i > 0:
                    binding_docs.append(TextDoc(", "))
                binding_docs.append(TextDoc(f"{_tla_var_name(v)} \\in "))
                binding_docs.append(_operand_doc(d))
            prefix_doc = TextDoc("\\A ") + ConcatDoc(*binding_docs) + TextDoc(": ")
            return prefix_doc + NestDoc(_node_to_doc(node.body), indent=3)
        case SetQuantNode(quant=QuantOp.EXISTS):
            binding_docs_e: list[AbstractDoc] = []
            for i, (v, d) in enumerate(node.bindings):
                if i > 0:
                    binding_docs_e.append(TextDoc(", "))
                binding_docs_e.append(TextDoc(f"{_tla_var_name(v)} \\in "))
                binding_docs_e.append(_operand_doc(d))
            prefix_doc_e = TextDoc("\\E ") + ConcatDoc(*binding_docs_e) + TextDoc(": ")
            return prefix_doc_e + NestDoc(_node_to_doc(node.body), indent=3)

        # --- Set filter ---
        case SetFilterNode():
            if len(node.bindings) == 1:
                prefix_doc = (
                    TextDoc(f"{{{_tla_var_name(node.var)} \\in ")
                    + _operand_doc(node.base_set)
                    + TextDoc(": ")
                )
                return (
                    prefix_doc
                    + NestDoc(_operand_doc(node.body), indent=3)
                    + TextDoc("}")
                )
            result_doc = _operand_doc(node.body)
            for v, d in reversed(node.bindings):
                inner_prefix = (
                    TextDoc(f"{{{_tla_var_name(v)} \\in ")
                    + _operand_doc(d)
                    + TextDoc(": ")
                )
                result_doc = inner_prefix + NestDoc(result_doc, indent=3) + TextDoc("}")
            return result_doc

        # --- Set map ---
        case SetMapNode():
            binding_docs_m: list[AbstractDoc] = []
            for i, (v, d) in enumerate(node.bindings):
                if i > 0:
                    binding_docs_m.append(TextDoc(", "))
                binding_docs_m.append(TextDoc(f"{_tla_var_name(v)} \\in "))
                binding_docs_m.append(_operand_doc(d))
            return (
                TextDoc("{")
                + _operand_doc(node.body)
                + TextDoc(": ")
                + ConcatDoc(*binding_docs_m)
                + TextDoc("}")
            )

        # --- Set reduce ---
        case SetReduceNode():
            op_name = fresh_name("set_reduce_")
            return _typed_local_op_let(
                op_name,
                [node.acc_var, node.elem_var],
                node.fun.sort,
                _node_to_doc(node.fun),
                _call_doc(
                    "ApaFoldSet",
                    [
                        TextDoc(op_name),
                        _operand_doc(node.initial),
                        _operand_doc(node.base_set),
                    ],
                ),
            )

        # --- CHOOSE ---
        case ChooseNode():
            prefix_doc_c = (
                TextDoc(f"CHOOSE {_tla_var_name(node.var)} \\in ")
                + _operand_doc(node.base_set)
                + TextDoc(": ")
            )
            return prefix_doc_c + NestDoc(_node_to_doc(node.predicate), indent=3)

        # --- Power set ---
        case AllSubsetsNode():
            base_doc = _node_to_doc(node.base_set)
            # SANY has a precedence conflict on `SUBSET A \X B`, so parenthesize
            # the base when it is a composite set expression.
            if isinstance(node.base_set, (AllTuplesNode, IntervalNode)):
                return TextDoc("SUBSET (") + base_doc + TextDoc(")")
            return TextDoc("SUBSET ") + _operand_doc(node.base_set)

        # --- All maps ---
        case AllMapsNode():
            return (
                TextDoc("[")
                + _operand_doc(node.key_set)
                + TextDoc(" -> ")
                + _operand_doc(node.value_set)
                + TextDoc("]")
            )

        # --- Cartesian product ---
        case AllTuplesNode():
            parts_at: list[AbstractDoc] = [
                TextDoc("(") + _node_to_doc(node.sets[0]) + TextDoc(")")
            ]
            for s in node.sets[1:]:
                parts_at.append(TextDoc(" \\X ("))
                parts_at.append(_node_to_doc(s))
                parts_at.append(TextDoc(")"))
            return ConcatDoc(*parts_at)

        # --- All records ---
        case AllRecordsNode():
            field_docs_ar: list[AbstractDoc] = []
            for name, s in sorted(node.field_sets.items()):
                field_docs_ar.append(TextDoc(f"{name}: ") + _operand_doc(s))
            return GroupDoc(
                TextDoc("[")
                + NestDoc(_breakable_comma_separated_docs(field_docs_ar), indent=4)
                + TextDoc("]")
            )

        # --- Record constructor ---
        case RecordCtorNode():
            field_docs_rc: list[AbstractDoc] = []
            for name, val in node.fields:
                field_docs_rc.append(TextDoc(f"{name} |-> ") + _operand_doc(val))
            return GroupDoc(
                TextDoc("[")
                + NestDoc(_breakable_comma_separated_docs(field_docs_rc), indent=4)
                + TextDoc("]")
            )

        # --- Record field access ---
        case RecordGetNode():
            return _postfix_base_doc(node.record_node) + TextDoc(f".{node.field_name}")

        # --- Record update ---
        case RecordUpdateNode():
            update_docs_ru: list[AbstractDoc] = []
            for name, val in node.updates:
                update_docs_ru.append(
                    TextDoc(f"!.{name} =")
                    + NestDoc(ConcatDoc(BreakDoc(" "), _operand_doc(val)), indent=4)
                )
            return GroupDoc(
                TextDoc("[")
                + _postfix_base_doc(node.base_record)
                + TextDoc(" EXCEPT ")
                + _breakable_comma_separated_docs(update_docs_ru)
                + TextDoc("]")
            )

        # --- Map enumeration ---
        case MapEnumNode():
            if not node.mappings:
                return TextDoc("SetAsFun({})")
            pair_docs: list[AbstractDoc] = []
            for mk, mv in node.mappings.items():
                pair_docs.append(
                    TextDoc("(")
                    + _operand_doc(mk)
                    + TextDoc(" :> ")
                    + _operand_doc(mv)
                    + TextDoc(")")
                )
            result_me: AbstractDoc = pair_docs[0]
            for pd in pair_docs[1:]:
                result_me = result_me + TextDoc(" @@ ") + pd
            return result_me

        # --- Map lambda ---
        case MapLambdaNode():
            prefix_doc_ml = (
                TextDoc(f"[{_tla_var_name(node.var)} \\in ")
                + _operand_doc(node.base_set)
                + TextDoc(" |-> ")
            )
            return (
                prefix_doc_ml
                + NestDoc(_operand_doc(node.mapper), indent=3)
                + TextDoc("]")
            )

        # --- Map lookup ---
        case MapGetNode():
            return (
                _postfix_base_doc(node.map_node)
                + TextDoc("[")
                + _node_to_doc(node.key)
                + TextDoc("]")
            )

        # --- Map update ---
        case MapSetNode():
            return GroupDoc(
                TextDoc("[")
                + _postfix_base_doc(node.base_map)
                + TextDoc(" EXCEPT ![")
                + _node_to_doc(node.update_key)
                + TextDoc("] =")
                + NestDoc(
                    ConcatDoc(BreakDoc(" "), _operand_doc(node.update_value)), indent=4
                )
                + TextDoc("]")
            )

        # --- Map keys ---
        case MapKeysNode():
            return TextDoc("DOMAIN ") + _operand_doc(node.map_node)

        # --- Tuple constructor ---
        case TupleCtorNode():
            elem_docs_tc = [_operand_doc(e) for e in node.elements]
            ctor_name = _tuple_ctor_name(len(node.elements))
            if elem_docs_tc:
                return _call_doc(ctor_name, elem_docs_tc)
            return TextDoc(ctor_name)

        # --- Tuple element access (0-based -> 1-based) ---
        case TupleGetNode():
            tla_index = node.index + 1
            return _postfix_base_doc(node.tuple_node) + TextDoc(f"[{tla_index}]")

        # --- Tuple update ---
        case TupleUpdateNode():
            tla_index = node.index + 1
            return GroupDoc(
                TextDoc("[")
                + _postfix_base_doc(node.base_tuple)
                + TextDoc(f" EXCEPT ![{tla_index}] =")
                + NestDoc(
                    ConcatDoc(BreakDoc(" "), _operand_doc(node.new_value)), indent=4
                )
                + TextDoc("]")
            )

        # --- List/Sequence operations (0-based -> 1-based) ---
        case ListEnumNode():
            elem_docs_le = [_operand_doc(e) for e in node.elements]
            ctor_name = _seq_ctor_name(len(node.elements))
            if elem_docs_le:
                return _call_doc(ctor_name, elem_docs_le)
            return TextDoc(ctor_name)

        case ListRangeNode():
            return (
                TextDoc("[i \\in (")
                + _node_to_doc(node.lower)
                + TextDoc(")..(")
                + _operand_doc(node.upper)
                + TextDoc(" - 1) |-> i]")
            )

        case ListGetNode():
            return (
                _postfix_base_doc(node.list_node)
                + TextDoc("[(")
                + _node_to_doc(node.index)
                + TextDoc(") + 1]")
            )

        case ListUpdateNode():
            return GroupDoc(
                TextDoc("[")
                + _postfix_base_doc(node.base_list)
                + TextDoc(" EXCEPT ![(")
                + _node_to_doc(node.index)
                + TextDoc(") + 1] =")
                + NestDoc(
                    ConcatDoc(BreakDoc(" "), _operand_doc(node.new_value)), indent=4
                )
                + TextDoc("]")
            )

        case ListSliceNode():
            return _call_doc(
                "SubSeq",
                [
                    _node_to_doc(node.base_list),
                    TextDoc("(") + _node_to_doc(node.start) + TextDoc(") + 1"),
                    _node_to_doc(node.end),
                ],
            )

        case ListFilterNode():
            op_name = fresh_name("list_filter_")
            return _typed_local_op_let(
                op_name,
                [node.var],
                BoolSort(),
                _node_to_doc(node.predicate),
                _call_doc(
                    "SelectSeq", [_node_to_doc(node.base_list), TextDoc(op_name)]
                ),
            )

        case ListReduceNode():
            op_name = fresh_name("list_reduce_")
            return _typed_local_op_let(
                op_name,
                [node.acc_var, node.elem_var],
                node.fun.sort,
                _node_to_doc(node.fun),
                _call_doc(
                    "ApaFoldSeqLeft",
                    [
                        TextDoc(op_name),
                        _operand_doc(node.initial),
                        _operand_doc(node.base_list),
                    ],
                ),
            )

        case ListKeysNode():
            return (
                TextDoc("({0} \\union (DOMAIN ")
                + _operand_doc(node.list_node)
                + TextDoc(")) \\ {Len(")
                + _operand_doc(node.list_node)
                + TextDoc(")}")
            )

        case AlgebraNode(op=AlgebraOp.LIST_SIZE):
            return TextDoc("Len(") + _operand_doc(node.args[0]) + TextDoc(")")

        case AlgebraNode(op=AlgebraOp.LIST_CONCAT):
            # Render a single-element concatenation (l + List(e)) as the more
            # idiomatic Append(l, e); fall back to \o for general concatenation.
            right = node.args[1]
            if isinstance(right, ListEnumNode) and len(right.elements) == 1:
                return _call_doc(
                    "Append",
                    [_operand_doc(node.args[0]), _operand_doc(right.elements[0])],
                )
            return (
                TextDoc("(")
                + _operand_doc(node.args[0])
                + TextDoc(" \\o ")
                + _operand_doc(node.args[1])
                + TextDoc(")")
            )

        # --- If-then-else ---
        case IteNode():
            return _if_then_else_doc(
                _operand_doc(node.condition),
                _operand_doc(node.then_node),
                _operand_doc(node.else_node),
            )

        # --- Non-inline @expr call ---
        case ExprCallNode():
            tla_name = _to_camel_case(node.op_name)
            if node.args:
                arg_docs = [_operand_doc(arg) for arg in node.args]
                return _call_doc(tla_name, arg_docs)
            return TextDoc(tla_name)

        # --- Let binding ---
        case LetNode():
            collapsed = _try_collapse_except(node)
            if collapsed is not None:
                return collapsed

            return _let_in_doc(
                _tla_var_name(node.name),
                _operand_doc(node.value),
                _node_to_doc(node.body),
                parenthesized=False,
            )

        # --- Action nodes ---
        case AssumeNode():
            return _node_to_doc(node.condition)

        case AssignNode():
            prime = "" if is_init else "'"
            if isinstance(node.var, VarNode):
                return GroupDoc(
                    TextDoc(f"{_tla_var_name(node.var)}{prime} =")
                    + NestDoc(
                        ConcatDoc(BreakDoc(" "), _operand_doc(node.expr)), indent=4
                    )
                )
            return GroupDoc(
                _node_to_doc(node.var)
                + TextDoc(f"{prime} =")
                + NestDoc(ConcatDoc(BreakDoc(" "), _operand_doc(node.expr)), indent=4)
            )

        case ActionAndNode():
            items = [
                _node_to_doc(
                    a, state_vars=state_vars, is_init=is_init, bound_vars=bound_vars
                )
                for a in node.actions
            ]
            return _junct_doc("/\\", items)

        case ActionChoiceNode():
            has_labels = node.labels is not None
            choice_assigns = _action_assigns(node)
            choice_items: list[AbstractDoc] = []
            for i, a in enumerate(node.actions):
                # Labels capture bound_vars; body starts fresh
                child_bound = () if has_labels else bound_vars
                action_doc = _node_to_doc(
                    a, state_vars=state_vars, is_init=is_init, bound_vars=child_bound
                )
                missing = choice_assigns - _action_assigns(a)
                if missing:
                    clause = _unchanged_clause(missing, state_vars)
                    action_doc = _conjoin_action_doc_with_clause(action_doc, clause)
                # Prepend a TLA+ label if available
                if node.labels is not None and i < len(node.labels):
                    label_name = f"lab_{node.labels[i]}"
                    if bound_vars:
                        params_str = ", ".join(bound_vars)
                        label_text = f"{label_name}({params_str}) ::"
                    else:
                        label_text = f"{label_name} ::"
                    action_doc = ConcatDoc(TextDoc(label_text), HardLine(), action_doc)
                choice_items.append(action_doc)
            return _junct_doc("\\/", choice_items)

        case NondetChoiceNode():
            if isinstance(node.var, VarNode):
                var_name = _tla_var_name(node.var)
                header = (
                    TextDoc(f"\\E {var_name} \\in ")
                    + _operand_doc(node.base_set)
                    + TextDoc(":")
                )
                new_bound = bound_vars + (var_name,)
            else:
                header = (
                    TextDoc("\\E ")
                    + _node_to_doc(node.var)
                    + TextDoc(" \\in ")
                    + _operand_doc(node.base_set)
                    + TextDoc(":")
                )
                new_bound = bound_vars + tuple(
                    _tla_var_name(e)
                    for e in getattr(node.var, "elements", ())
                    if isinstance(e, VarNode)
                )
            body_doc = _node_to_doc(
                node.body, state_vars=state_vars, is_init=is_init, bound_vars=new_bound
            )
            return header + NestDoc(ConcatDoc(HardLine(), body_doc), indent=4)

        case ActionCallNode():
            action_name = _to_camel_case(node.action_name)
            if node.args:
                arg_docs = [_operand_doc(arg) for arg in node.args]
                return _call_doc(action_name, arg_docs)
            else:
                return TextDoc(action_name)

        case ActionLetNode():
            value_doc = _operand_doc(node.value)
            body_doc = _node_to_doc(
                node.body, state_vars=state_vars, is_init=is_init, bound_vars=bound_vars
            )
            return _let_in_doc(
                _tla_var_name(node.name),
                value_doc,
                body_doc,
                parenthesized=False,
            )

        # --- Union (variant) operations ---
        case UnionCtorNode():
            assert isinstance(node.sort, UnionSort)
            helper_name = _union_ctor_name(node.sort, node.tag)
            if node.payload is not None:
                return _call_doc(
                    helper_name, [_operand_doc(node.payload)], break_first=True
                )
            else:
                return TextDoc(helper_name)

        case UnionGetTagNode():
            assert isinstance(node.union_node.sort, UnionSort)
            if isinstance(node.union_node, UnionCtorNode):
                helper_name = _union_ctor_name(
                    node.union_node.sort, node.union_node.tag
                )
                if node.union_node.payload is None:
                    union_doc = TextDoc(helper_name)
                else:
                    union_doc = (
                        TextDoc(f"{helper_name}(")
                        + _operand_doc(node.union_node.payload)
                        + TextDoc(")")
                    )
            else:
                union_doc = _operand_doc(node.union_node)
            return _union_original_tag_doc(union_doc, node.union_node.sort)

        case UnionMatchNode():
            assert isinstance(node.union_node.sort, UnionSort)
            union_sort = node.union_node.sort
            union_doc = _operand_doc(node.union_node)
            tags = sorted(node.cases.keys())
            result_um: AbstractDoc | None = None
            for i in range(len(tags) - 1, -1, -1):
                tag = tags[i]
                var_node_um, body = node.cases[tag]
                if var_node_um is not None:
                    case_doc = _let_in_doc(
                        _tla_var_name(var_node_um),
                        _call_doc(
                            "VariantGetUnsafe",
                            [
                                TextDoc(_union_tag_string(union_sort, tag)),
                                union_doc,
                            ],
                        ),
                        _node_to_doc(body),
                        parenthesized=True,
                    )
                else:
                    case_doc = _operand_doc(body)
                if result_um is None:
                    result_um = case_doc
                else:
                    result_um = _if_then_else_doc(
                        _call_doc("VariantTag", [union_doc])
                        + TextDoc(f" = {_union_tag_string(union_sort, tag)}"),
                        case_doc,
                        result_um,
                    )
            assert result_um is not None
            return result_um

        # --- Temporal operators ---
        case ToTemporalNode():
            return _node_to_doc(node.bool_formula)

        case AlwaysNode():
            return TextDoc("[](") + _node_to_doc(node.subformula) + TextDoc(")")

        case EventuallyNode():
            return TextDoc("<>(") + _node_to_doc(node.subformula) + TextDoc(")")

        case EnabledNode():
            return TextDoc("ENABLED ") + _operand_doc(node.action)

        case FairnessNode():
            prefix_str = "WF" if node.kind == Fair.WEAK else "SF"
            vars_tla = ", ".join(node.stuttering_vars)
            return (
                TextDoc(f"{prefix_str}_<<{vars_tla}>>(")
                + _node_to_doc(node.action)
                + TextDoc(")")
            )

        case _:
            raise NotImplementedError(
                f"Node type {type(node).__name__} not supported for TLA+ translation"
                f"{_span_suffix(node)}"
            )


# ---------------------------------------------------------------------------
# Backward-compatible string-based API
# ---------------------------------------------------------------------------


def _node_to_tla(
    node: Node,
    indent: int = 0,
    *,
    state_vars: tuple[str, ...] | None = None,
    text_width: int = 79,
    text_indent: int = 4,
) -> str:
    """Convert an AST node to TLA+ syntax.

    Args:
        node: The AST node to convert.
        indent: Current indentation level (each level = ``text_indent`` spaces).
        state_vars: Tuple of state variable names for UNCHANGED handling.
        text_width: Preferred output width in columns.
        text_indent: Preferred block indentation in columns.

    Returns:
        TLA+ syntax string for the node.
    """
    doc = _node_to_doc(node, state_vars=state_vars)
    rendered = _render_tla_doc(doc, text_width=text_width, text_indent=text_indent)
    if indent > 0:
        prefix = " " * text_indent * indent
        lines = rendered.split("\n")
        return "\n".join(prefix + line for line in lines)
    return rendered


# ---------------------------------------------------------------------------
# Module generators
# ---------------------------------------------------------------------------


def _build_op_dep_graph(
    rendered_ops: dict[str, str],
) -> dict[str, set[str]]:
    """Return a dependency graph for *rendered_ops*.

    For each operator name, the corresponding set contains the names of the
    other operators (from the same dict) whose TLA+ text is referenced by
    that operator's rendered body — i.e. the operators that must be defined
    *before* this one.
    """
    op_names = list(rendered_ops.keys())
    deps: dict[str, set[str]] = {op: set() for op in op_names}
    for op_name, rendered in rendered_ops.items():
        for other in op_names:
            if other != op_name and re.search(
                r"\b" + re.escape(other) + r"\b", rendered
            ):
                deps[op_name].add(other)
    return deps


def _toposort_ops(ops: list[str], dep_graph: dict[str, set[str]]) -> list[str]:
    """Return *ops* in topological order using Kahn's algorithm.

    *dep_graph* maps each op to the set of ops it depends on (i.e.
    the ops that must appear *before* it).  When multiple ops become
    ready at the same time the original order in *ops* is preserved,
    so the sort is stable with respect to the input ordering.

    If a cycle is detected (which should not occur in valid TLA+ specs)
    the remaining ops are appended in their original order without
    raising an exception.
    """
    original_index = {op: i for i, op in enumerate(ops)}
    in_degree = {op: len(dep_graph[op]) for op in ops}

    # Build reverse map: dep -> list of ops that depend on dep.
    dependents: dict[str, list[str]] = {op: [] for op in ops}
    for op, dep_set in dep_graph.items():
        for d in dep_set:
            dependents[d].append(op)

    ready = sorted(
        (op for op in ops if in_degree[op] == 0), key=original_index.__getitem__
    )
    sorted_ops: list[str] = []
    while ready:
        op = ready.pop(0)
        sorted_ops.append(op)
        for dependent in dependents[op]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)
        ready.sort(key=original_index.__getitem__)

    # Cycle safety: append any remaining ops in original order.
    if len(sorted_ops) < len(ops):
        sorted_ops.extend(op for op in ops if op not in set(sorted_ops))

    return sorted_ops


State = TypeVar("State", bound=MachineState)


def to_tla(
    state: type[State],
    name: str = "untitled",
    extends: list[str] | None = None,
    /,
    extracted_actions: Mapping[str, ExtractedActionDef] | None = None,
    init_ops: set[str] | None = None,
    text_width: int = 79,
    text_indent: int = 4,
    **nodes: Node,
) -> str:
    """Convert a wunderspec specification to TLA+ format.

    Args:
        state: The state class decorated with @state.
        name: Module name for the TLA+ output (default: "untitled").
        extends: List of modules to extend.
            Defaults to ["Integers", "FiniteSets", "Sequences"].
        extracted_actions: Dictionary of extracted action definitions.
        text_width: Preferred output width in columns.
        text_indent: Preferred block indentation in columns.
        **nodes: Named operator definitions (e.g., Init=init_node, Next=next_node).

    Returns:
        TLA+ specification as a string.

    Example:
        >>> from wunderspec import *
        >>> from wunderspec.machine import MachineStateBase, state
        >>> from typing import Annotated
        >>> @state
        ... class CounterState(MachineStateBase):
        ...     count: Annotated[Expr, int]
        >>> output = to_tla(CounterState, "counter")
        >>> "MODULE counter" in output
        True
    """
    if extends is None:
        extends = ["Integers", "FiniteSets", "Sequences"]
    if text_width <= 0:
        raise ValueError(f"text_width must be positive, got: {text_width}")
    if text_indent <= 0:
        raise ValueError(f"text_indent must be positive, got: {text_indent}")

    # Generate body lines first (to detect Variants usage)
    body_lines: list[str] = []

    # Collect extracted actions from nodes
    all_extracted: dict[str, ExtractedActionDef] = {}
    if extracted_actions:
        all_extracted.update(extracted_actions)
    for node in nodes.values():
        _collect_extracted_actions(node, all_extracted)

    # Collect extracted exprs (non-inline @expr operators) from nodes and action bodies
    all_extracted_exprs: dict[str, ExtractedExprDef] = {}
    for node in nodes.values():
        _collect_extracted_exprs(node, all_extracted_exprs)
    for extracted_action in all_extracted.values():
        _collect_extracted_exprs(extracted_action.body, all_extracted_exprs)

    union_sorts: dict[tuple[tuple[str, Sort | None], ...], UnionSort] = {}
    tuple_ctor_arities: set[int] = set()
    seq_ctor_arities: set[int] = set()
    for node in nodes.values():
        _collect_union_sorts(node, union_sorts)
        _collect_literal_ctor_arities(node, tuple_ctor_arities, seq_ctor_arities)
    for extracted in all_extracted.values():
        _collect_union_sorts(extracted.body, union_sorts)
        _collect_literal_ctor_arities(
            extracted.body, tuple_ctor_arities, seq_ctor_arities
        )
    for extracted_expr in all_extracted_exprs.values():
        _collect_union_sorts(extracted_expr.body, union_sorts)
        _collect_literal_ctor_arities(
            extracted_expr.body, tuple_ctor_arities, seq_ctor_arities
        )

    state_vars = tuple(state._vars)

    body_lines.extend(_union_ctor_defs(list(union_sorts.values())))
    body_lines.extend(
        _tuple_ctor_defs(list(tuple_ctor_arities), text_indent=text_indent)
    )
    body_lines.extend(_seq_ctor_defs(list(seq_ctor_arities), text_indent=text_indent))

    # Track top-level operators emitted early (as extracted exprs), so we can
    # skip them later in the main operator rendering loop.
    early_emitted_ops: set[str] = set()

    # Output extracted expr definitions before extracted actions so that
    # actions can reference @expr(inline=False) operators without forward refs.
    # We also emit top-level @expr(inline=False) ops here (not just anonymous
    # ones), because extracted actions may reference them.
    for op_name_e, extracted_expr in all_extracted_exprs.items():
        tla_name = _to_camel_case(op_name_e)
        is_toplevel_expr = (
            tla_name in nodes
            and isinstance(nodes[tla_name], ExprCallNode)
            and _to_camel_case(cast("ExprCallNode", nodes[tla_name]).op_name)
            == tla_name
        )
        if tla_name in nodes and not is_toplevel_expr:
            # A regular top-level operator that happens to contain an
            # ExprCallNode: let the normal rendering loop handle it.
            continue
        param_names_e = extracted_expr.param_names
        param_sorts_e = extracted_expr.param_sorts
        result_type_e = _sort_to_tla_type(extracted_expr.result_sort, nested=True)
        if param_names_e:
            params_types_e = ", ".join(
                _sort_to_tla_type(s, nested=True) for s in param_sorts_e
            )
            body_lines.append(f"\\* @type: ({params_types_e}) => {result_type_e};")
            header_e = f"{tla_name}({', '.join(param_names_e)}) =="
        else:
            result_type_standalone_e = _sort_to_tla_type(extracted_expr.result_sort)
            body_lines.append(f"\\* @type: {result_type_standalone_e};")
            header_e = f"{tla_name} =="
        body_doc_e = _node_to_doc(extracted_expr.body, state_vars=state_vars)
        def_doc_e = ConcatDoc(
            TextDoc(header_e), NestDoc(ConcatDoc(HardLine(), body_doc_e), indent=4)
        )
        body_lines.append(
            _render_tla_doc(def_doc_e, text_width=text_width, text_indent=text_indent)
        )
        body_lines.append("")
        if is_toplevel_expr:
            early_emitted_ops.add(tla_name)

    # Output extracted action definitions
    for action_name, extracted in all_extracted.items():
        param_names = extracted.param_names
        param_sorts = extracted.param_sorts
        body = extracted.body
        tla_name = _to_camel_case(action_name)
        # Skip if this action is already defined as a top-level operator in nodes
        # (e.g. both a standalone @action def and called inline from another action).
        if tla_name in nodes:
            continue
        if param_names:
            params_types = ", ".join(
                _sort_to_tla_type(sort, nested=True) for sort in param_sorts
            )
            body_lines.append(f"\\* @type: ({params_types}) => Bool;")
        if param_names:
            params_str = ", ".join(param_names)
            header = f"{tla_name}({params_str}) =="
        else:
            header = f"{tla_name} =="

        body_doc = _node_to_doc(body, state_vars=state_vars)
        label_doc = ConcatDoc(TextDoc(f"{tla_name} ::"), HardLine(), body_doc)
        def_doc = ConcatDoc(
            TextDoc(header), NestDoc(ConcatDoc(HardLine(), label_doc), indent=4)
        )
        body_lines.append(
            _render_tla_doc(def_doc, text_width=text_width, text_indent=text_indent)
        )
        body_lines.append("")

    # Add operator definitions in topological order (dependencies before dependents)
    # so that TLA+/SANY never sees a forward reference.
    op_names = [n for n in nodes.keys() if n not in early_emitted_ops]

    # Pre-render every body once (used for both dependency scanning and output).
    rendered_ops: dict[str, str] = {}
    for op_name, node in nodes.items():
        # Skip operators already emitted early (top-level @expr(inline=False)
        # ops that had to precede extracted actions).
        if op_name in early_emitted_ops:
            continue

        is_init_op = init_ops is not None and op_name in init_ops

        # If this is an @expr(inline=False) top-level operator, the AST node
        # is an ExprCallNode referencing itself (the call-site representation).
        # Unwrap it so we render the body directly instead of a self-referential
        # call.  Any Expr params become formal parameters in the header.
        expr_param_names: tuple[str, ...] = ()
        if isinstance(node, ExprCallNode) and _to_camel_case(node.op_name) == op_name:
            expr_param_names = node.param_names
            node = node.body

        body_doc = _node_to_doc(node, state_vars=state_vars, is_init=is_init_op)

        # Top-level transition relations must explicitly preserve untouched
        # state variables with UNCHANGED.
        if op_name.lower() in {"next", "step"} and _is_action_node(node):
            missing = set(state._vars) - _action_assigns(node)
            if missing:
                clause = _unchanged_clause(missing, state_vars)
                body_doc = _conjoin_action_doc_with_clause(body_doc, clause)

        if expr_param_names:
            header = f"{op_name}({', '.join(expr_param_names)}) =="
        else:
            header = f"{op_name} =="
        def_doc = ConcatDoc(
            TextDoc(header),
            NestDoc(ConcatDoc(HardLine(), body_doc), indent=4),
        )
        rendered_ops[op_name] = _render_tla_doc(
            def_doc, text_width=text_width, text_indent=text_indent
        )

    deps = _build_op_dep_graph(rendered_ops)
    for op_name in _toposort_ops(op_names, deps):
        body_lines.append(rendered_ops[op_name])
        body_lines.append("")

    if "Variants" not in extends and union_sorts:
        extends = [*extends, "Variants"]
    if "TLC" not in extends and _needs_tlc(body_lines):
        extends = [*extends, "TLC"]
    if "Apalache" not in extends and _needs_apalache_helpers(body_lines):
        extends = [*extends, "Apalache"]

    # Assemble: header + body + footer
    lines = [
        _state_to_tla_header(
            state, name, extends, text_width=text_width, text_indent=text_indent
        ),
        "",
    ]
    lines.extend(body_lines)
    lines.append("=" * text_width)

    return "\n".join(lines)


def to_tla_instance(
    state: type[State],
    name: str,
    target_module: str,
    fixed_params: Mapping[str, Node],
    include_variables: bool = True,
    include_behavior_spec: bool = False,
    init_op: str = "Init",
    step_op: str = "Step",
    text_width: int = 79,
    text_indent: int = 4,
) -> str:
    """Generate a model-checking wrapper module with fixed constants and INSTANCE.

    Args:
        state: The state class decorated with @state.
        name: Wrapper module name (e.g., "MC_MySpec").
        target_module: Name of the base module to instantiate.
        fixed_params: Mapping from parameter name to fixed AST node value.
        include_variables: Whether to emit VARIABLES with type comments.
        include_behavior_spec: Whether to emit ``Vars`` and ``Spec`` for TLC.
        init_op: Name of the init operator used in ``Spec``.
        step_op: Name of the next-state operator used in ``Spec``.
        text_width: Preferred output width in columns.
        text_indent: Preferred block indentation in columns.

    Returns:
        TLA+ module text.
    """
    if text_width <= 0:
        raise ValueError(f"text_width must be positive, got: {text_width}")
    if text_indent <= 0:
        raise ValueError(f"text_indent must be positive, got: {text_indent}")

    extends = ["Integers", "FiniteSets", "Sequences"]
    union_sorts: dict[tuple[tuple[str, Sort | None], ...], UnionSort] = {}
    tuple_ctor_arities: set[int] = set()
    seq_ctor_arities: set[int] = set()
    for param in state._params:
        _collect_union_sorts(fixed_params[param], union_sorts)
        _collect_literal_ctor_arities(
            fixed_params[param], tuple_ctor_arities, seq_ctor_arities
        )
    if union_sorts:
        extends.append("Variants")

    lines: list[str] = []

    # Module header (centered in text_width chars)
    module_text = f"MODULE {name}"
    total_dashes = text_width - len(module_text) - 2
    left_dashes = total_dashes // 2
    right_dashes = total_dashes - left_dashes
    lines.append("-" * left_dashes + " " + module_text + " " + "-" * right_dashes)
    lines.append("\\* an instance to run the model checker")
    lines.append(f"EXTENDS {', '.join(extends)}")
    lines.append("")

    lines.extend(_union_ctor_defs(list(union_sorts.values())))
    lines.extend(_tuple_ctor_defs(list(tuple_ctor_arities), text_indent=text_indent))
    lines.extend(_seq_ctor_defs(list(seq_ctor_arities), text_indent=text_indent))

    for param_name in state._params:
        node = fixed_params[param_name]
        lines.append(
            f"{param_name} == "
            f"{_node_to_tla(node, text_width=text_width, text_indent=text_indent)}"
        )

    if state._params:
        lines.append("")

    if include_variables and state._vars:
        indent = " " * text_indent
        lines.append("VARIABLES")
        for i, var_name in enumerate(state._vars):
            descriptor = getattr(state, var_name)
            type_str = _sort_to_tla_type(descriptor.sort)
            lines.append(f"{indent}\\* @type: {type_str};")
            if i < len(state._vars) - 1:
                lines.append(f"{indent}{var_name},")
            else:
                lines.append(f"{indent}{var_name}")
        lines.append("")

    if "TLC" not in extends and _needs_tlc(lines):
        extends.append("TLC")
        lines[2] = f"EXTENDS {', '.join(extends)}"
    if "Apalache" not in extends and _needs_apalache_helpers(lines):
        extends.append("Apalache")
        lines[2] = f"EXTENDS {', '.join(extends)}"

    lines.append(f"INSTANCE {target_module}")
    if include_behavior_spec:
        lines.append("")
        vars_tuple = f"<<{', '.join(state._vars)}>>"
        lines.append(f"Vars == {vars_tuple}")
        lines.append(f"Spec == {init_op} /\\ [][{step_op}]_Vars")
    lines.append("=" * text_width)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Extracted action collection (unchanged)
# ---------------------------------------------------------------------------


def _collect_extracted_actions(
    node: Node, extracted: dict[str, ExtractedActionDef]
) -> None:
    """Recursively collect extracted actions from ActionCallNodes."""
    match node:
        case ActionCallNode():
            if node.action_name not in extracted:
                param_names: tuple[str, ...] = ()
                if node.args:
                    param_names = tuple(
                        arg.name if isinstance(arg, VarNode) else f"arg{i}"
                        for i, arg in enumerate(node.args)
                    )
                param_sorts = tuple(arg.sort for arg in node.args)
                extracted[node.action_name] = ExtractedActionDef(
                    param_names=param_names,
                    param_sorts=param_sorts,
                    body=node.body,
                )
            _collect_extracted_actions(node.body, extracted)

        case ActionAndNode():
            for action in node.actions:
                _collect_extracted_actions(action, extracted)

        case ActionChoiceNode():
            for action in node.actions:
                _collect_extracted_actions(action, extracted)

        case NondetChoiceNode():
            _collect_extracted_actions(node.body, extracted)

        case ActionLetNode():
            _collect_extracted_actions(node.body, extracted)

        case _:
            pass


def _collect_extracted_exprs(
    node: Node,
    extracted: dict[str, ExtractedExprDef],
    *,
    _visited: set[str] | None = None,
) -> None:
    """Recursively collect ExprCallNodes from an AST.

    Uses post-order DFS (via a separate ``_visited`` set) so that
    dependencies are inserted into ``extracted`` before the operators that
    depend on them, giving a forward-reference-free emission order.
    """
    if _visited is None:
        _visited = set()
    match node:
        case ExprCallNode():
            if node.op_name in _visited:
                return
            _visited.add(node.op_name)
            # Recurse into children first (post-order).
            _collect_extracted_exprs(node.body, extracted, _visited=_visited)
            for arg in node.args:
                _collect_extracted_exprs(arg, extracted, _visited=_visited)
            # Insert this node after its dependencies.
            if node.op_name not in extracted:
                param_sorts = tuple(arg.sort for arg in node.args)
                extracted[node.op_name] = ExtractedExprDef(
                    param_names=node.param_names,
                    param_sorts=param_sorts,
                    result_sort=node.sort,
                    body=node.body,
                )
        case _:
            for child_val in vars(node).values():
                for child in _iter_child_nodes(child_val):
                    _collect_extracted_exprs(child, extracted, _visited=_visited)
