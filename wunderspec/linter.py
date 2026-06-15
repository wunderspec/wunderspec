from __future__ import annotations

import ast as py_ast
import inspect
import textwrap
import traceback
from copy import copy
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, get_args, get_origin

from wunderspec.api import build_expr_ast, find_state_classes, load_module
from wunderspec.ast.action_ast import (
    ActionAndNode,
    ActionCallNode,
    ActionChoiceNode,
    ActionLetNode,
    AssignNode,
    AssumeNode,
    NondetChoiceNode,
)
from wunderspec.ast.ast import (
    ARITH_OPS,
    BOOL_OPS,
    CMP_OPS,
    EQ_OPS,
    AlgebraNode,
    ExprCallNode,
    InNode,
    IteNode,
    LetNode,
    LitNode,
    Node,
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
    IntSort,
    ListSort,
    SetSort,
    StrSort,
    TemporalSort,
    sort_of,
)
from wunderspec.ast.temporal_ast import (
    AlwaysNode,
    EnabledNode,
    EventuallyNode,
    FairnessNode,
    ToTemporalNode,
)
from wunderspec.ast.tuple_ast import TupleCtorNode, TupleGetNode, TupleUpdateNode
from wunderspec.ast.union_ast import UnionCtorNode, UnionGetTagNode, UnionMatchNode
from wunderspec.expr import BoolExpr, Expr, IntExpr, StrExpr, VarExpr
from wunderspec.sym_context import ActionDefs, SymbolicContext


@dataclass
class LintErrorSource:
    """Source location of a lint error."""

    filename: str
    lineno: int
    line: str  # source text (stripped)
    func: str  # function name in the frame


@dataclass
class LintError:
    func_name: str
    message: str
    sources: list[LintErrorSource] = field(default_factory=list)


@dataclass
class LintWarning:
    func_name: str
    message: str
    sources: list[LintErrorSource] = field(default_factory=list)


@dataclass(frozen=True)
class ActionEffects:
    name: str
    kind: str
    reads_direct: frozenset[str]
    writes_direct: frozenset[str]
    reads_transitive: frozenset[str]
    writes_transitive: frozenset[str]
    calls: tuple[str, ...] = ()


@dataclass(frozen=True)
class LintAnalysis:
    errors: list[LintError]
    warnings: list[LintWarning]
    effects: dict[str, ActionEffects]


@dataclass(frozen=True)
class _ActionDefInfo:
    node: Node
    bound_vars: frozenset[str]
    kind: str


@dataclass
class _NodeSummary:
    reads_direct: set[str] = field(default_factory=set)
    writes_direct: set[str] = field(default_factory=set)
    reads_transitive: set[str] = field(default_factory=set)
    writes_transitive: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    errors: list[LintError] = field(default_factory=list)


@dataclass(frozen=True)
class _ActionSummary:
    effects: ActionEffects
    errors: tuple[LintError, ...] = ()


@dataclass(frozen=True)
class _CapturedStateAlias:
    roots: frozenset[str]
    generations: dict[str, int]


class _UninferrableActionParamSort(Exception):
    pass


def _extract_source_from_exc(
    exc: BaseException, spec_path: Path
) -> list[LintErrorSource]:
    """Extract source frames from *exc*'s traceback that belong to *spec_path*."""
    tb = exc.__traceback__
    if tb is None:
        return []
    resolved = str(spec_path.resolve())
    sources: list[LintErrorSource] = []
    for frame in traceback.extract_tb(tb):
        if frame.filename == resolved:
            sources.append(
                LintErrorSource(
                    filename=str(spec_path),
                    lineno=frame.lineno or 0,
                    line=frame.line.strip() if frame.line else "",
                    func=frame.name,
                )
            )
    return sources


def _lint_error(
    func_name: str,
    message: str,
    sources: list[LintErrorSource] | None = None,
) -> LintError:
    return LintError(func_name=func_name, message=message, sources=sources or [])


def _lint_warning(
    func_name: str,
    message: str,
    sources: list[LintErrorSource] | None = None,
) -> LintWarning:
    return LintWarning(func_name=func_name, message=message, sources=sources or [])


def _extract_annotation_sort(annotation: Any) -> Any:
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        if len(args) < 2:
            raise TypeError(f"Unsupported Annotated annotation: {annotation!r}")
        return sort_of(args[1])
    if annotation is IntExpr:
        return IntSort()
    if annotation is BoolExpr:
        return BoolSort()
    if annotation is StrExpr:
        return StrSort()
    if annotation is Expr:
        raise _UninferrableActionParamSort(
            "Uninferrable action parameter sort for annotation Expr"
        )
    return sort_of(annotation)


def _candidate_annotation_sorts(annotation: Any) -> tuple[Any, ...]:
    if annotation is Expr:
        return (StrSort(), IntSort(), BoolSort())
    return (_extract_annotation_sort(annotation),)


def _build_ast_for_action(
    state_cls: type[Any], func: Any
) -> tuple[Node, ActionDefs, frozenset[str]]:
    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    resolved_annotations = inspect.get_annotations(func, eval_str=True)

    if len(params) == 0:
        raise TypeError(f"Action '{func.__name__}' must accept a context argument")

    param_names: list[str] = []
    sort_candidates: list[tuple[Any, ...]] = []
    for param in params[1:]:
        annotation = resolved_annotations.get(param.name, param.annotation)
        if annotation is inspect.Parameter.empty:
            raise _UninferrableActionParamSort(
                f"Action '{func.__name__}' parameter '{param.name}' is missing a type annotation"
            )
        param_names.append(param.name)
        try:
            sort_candidates.append(_candidate_annotation_sorts(annotation))
        except _UninferrableActionParamSort as exc:
            raise _UninferrableActionParamSort(
                f"Action '{func.__name__}' parameter '{param.name}' has no inferrable sort: {exc}"
            ) from exc

    last_error: Exception | None = None
    candidate_products = product(*sort_candidates) if sort_candidates else [()]
    for candidate_sorts in candidate_products:
        extra_args = [
            VarExpr(name, sort) for name, sort in zip(param_names, candidate_sorts)
        ]
        sym_state = state_cls()
        ctx = SymbolicContext(copy(sym_state))
        try:
            func(ctx, *extra_args)
        except Exception as exc:
            last_error = exc
            continue
        return (
            ctx.build(),
            ctx.extracted_actions,
            frozenset(param_names),
        )

    if last_error is not None:
        raise _UninferrableActionParamSort(
            f"Action '{func.__name__}' parameter sorts could not be inferred from Expr annotations: {last_error}"
        ) from last_error

    raise _UninferrableActionParamSort(
        f"Action '{func.__name__}' parameter sorts could not be inferred"
    )


def _check_node(
    node: Node,
    func_name: str,
    state_scope: set[str],
    bound_vars: frozenset[str],
    _seen: set[tuple[int, frozenset[str]]] | None = None,
) -> list[LintError]:
    if _seen is None:
        _seen = set()
    seen_key = (id(node), bound_vars)
    if seen_key in _seen:
        return []
    _seen.add(seen_key)

    errors: list[LintError] = []

    def check(child: Node, child_bound: frozenset[str] = bound_vars) -> None:
        errors.extend(_check_node(child, func_name, state_scope, child_bound, _seen))

    def sort_mismatch(msg: str) -> None:
        errors.append(_lint_error(func_name, msg))

    if isinstance(node, VarNode):
        if node.name not in state_scope and node.name not in bound_vars:
            errors.append(
                _lint_error(
                    func_name,
                    f"Var({node.name!r}) is not in scope; expected one of state vars/params "
                    f"{sorted(state_scope)} or bound vars {sorted(bound_vars)}",
                )
            )
        return errors

    if isinstance(node, (LitNode, SetIntOrNatNode)):
        return errors

    if isinstance(node, AlgebraNode):
        for arg in node.args:
            check(arg)

        if node.op in ARITH_OPS:
            if node.sort != IntSort():
                sort_mismatch(
                    f"Arithmetic node {node.op.value} must have Int sort result, got {node.sort}"
                )
            if any(arg.sort != IntSort() for arg in node.args):
                sort_mismatch(
                    f"Arithmetic node {node.op.value} expects Int arguments, "
                    f"got {[arg.sort for arg in node.args]}"
                )
        elif node.op in CMP_OPS:
            if node.sort != BoolSort():
                sort_mismatch(
                    f"Comparison node {node.op.value} must have Bool sort result, got {node.sort}"
                )
        elif node.op in EQ_OPS:
            if node.sort != BoolSort():
                sort_mismatch(
                    f"Equality node {node.op.value} must have Bool sort result, got {node.sort}"
                )
        elif node.op in BOOL_OPS:
            if any(
                not isinstance(arg.sort, (BoolSort, TemporalSort)) for arg in node.args
            ):
                sort_mismatch(
                    f"Boolean node {node.op.value} expects Bool/Temporal args, "
                    f"got {[arg.sort for arg in node.args]}"
                )
            if any(arg.sort != node.sort for arg in node.args):
                sort_mismatch(
                    f"Boolean node {node.op.value} result sort {node.sort} "
                    f"does not match argument sorts {[arg.sort for arg in node.args]}"
                )
        return errors

    if isinstance(node, IteNode):
        check(node.condition)
        check(node.then_node)
        check(node.else_node)
        return errors

    if isinstance(node, LetNode):
        check(node.value)
        check(node.body, bound_vars | {node.name})
        return errors

    if isinstance(node, ExprCallNode):
        # Check the actual call-site args in the current scope.
        # The body uses formal VarNode params, so don't traverse it here.
        for arg in node.args:
            check(arg)
        return errors

    if isinstance(node, InNode):
        check(node.elem)
        check(node.set_node)
        if not isinstance(node.set_node.sort, SetSort):
            sort_mismatch(f"In node expects set operand, got {node.set_node.sort}")
        elif node.elem.sort != node.set_node.sort.elem_sort:
            sort_mismatch(
                f"In node sort mismatch: element has {node.elem.sort}, "
                f"set element sort is {node.set_node.sort.elem_sort}"
            )
        return errors

    if isinstance(node, AssumeNode):
        check(node.condition)
        if node.condition.sort != BoolSort():
            sort_mismatch(
                f"Assume condition must have Bool sort, got {node.condition.sort}"
            )
        return errors

    if isinstance(node, AssignNode):
        check(node.var)
        check(node.expr)
        if node.var.sort != node.expr.sort:
            sort_mismatch(
                f"Assign sort mismatch: var has {node.var.sort}, expr has {node.expr.sort}"
            )
        return errors

    if isinstance(node, (ActionAndNode, ActionChoiceNode)):
        for action in node.actions:
            check(action)
        return errors

    if isinstance(node, ActionCallNode):
        for arg in node.args:
            check(arg)
        # Action bodies are linted independently via extracted action definitions.
        # Re-checking node.body here can report false positives when argument
        # expressions are not plain VarNode (e.g., tuple projections).
        return errors

    if isinstance(node, SetEnumNode):
        for e in node.elements:
            check(e)
        return errors

    if isinstance(node, SetFilterNode):
        all_bound = bound_vars
        for var, domain in node.bindings:
            check(domain)
            if isinstance(domain.sort, SetSort) and (var.sort != domain.sort.elem_sort):
                sort_mismatch(
                    f"SetFilter binder sort mismatch: var has {var.sort}, "
                    f"set elements are {domain.sort.elem_sort}"
                )
            all_bound = all_bound | {var.name}
        check(node.body, all_bound)
        return errors

    if isinstance(node, SetMapNode):
        all_bound = bound_vars
        for var, domain in node.bindings:
            check(domain)
            if isinstance(domain.sort, SetSort) and (var.sort != domain.sort.elem_sort):
                sort_mismatch(
                    f"SetMap binder sort mismatch: var has {var.sort}, "
                    f"set elements are {domain.sort.elem_sort}"
                )
            all_bound = all_bound | {var.name}
        check(node.body, all_bound)
        return errors

    if isinstance(node, SetQuantNode):
        all_bound = bound_vars
        for var, domain in node.bindings:
            check(domain)
            if isinstance(domain.sort, SetSort) and (var.sort != domain.sort.elem_sort):
                sort_mismatch(
                    f"SetQuant binder sort mismatch: var has {var.sort}, "
                    f"set elements are {domain.sort.elem_sort}"
                )
            all_bound = all_bound | {var.name}
        check(node.body, all_bound)
        return errors

    if isinstance(node, SetReduceNode):
        check(node.base_set)
        check(node.initial)
        check(node.fun, bound_vars | {node.acc_var.name, node.elem_var.name})
        if isinstance(node.base_set.sort, SetSort) and (
            node.elem_var.sort != node.base_set.sort.elem_sort
        ):
            sort_mismatch(
                f"SetReduce element binder sort mismatch: elem var has {node.elem_var.sort}, "
                f"set elements are {node.base_set.sort.elem_sort}"
            )
        return errors

    if isinstance(node, ChooseNode):
        check(node.base_set)
        check(node.predicate, bound_vars | {node.var.name})
        if isinstance(node.base_set.sort, SetSort) and (
            node.var.sort != node.base_set.sort.elem_sort
        ):
            sort_mismatch(
                f"Choose binder sort mismatch: var has {node.var.sort}, "
                f"set elements are {node.base_set.sort.elem_sort}"
            )
        return errors

    if isinstance(node, NondetChoiceNode):
        check(node.base_set)
        if isinstance(node.var, VarNode):
            body_bound_vars = bound_vars | {node.var.name}
        else:
            body_bound_vars = bound_vars
            sort_mismatch(
                f"NondetChoice binder must be VarNode, got {type(node.var).__name__}"
            )
        check(node.body, body_bound_vars)
        if isinstance(node.base_set.sort, SetSort) and (
            node.var.sort != node.base_set.sort.elem_sort
        ):
            sort_mismatch(
                f"NondetChoice binder sort mismatch: var has {node.var.sort}, "
                f"set elements are {node.base_set.sort.elem_sort}"
            )
        return errors

    if isinstance(node, ActionLetNode):
        check(node.value)
        check(node.body, bound_vars | {node.name})
        return errors

    if isinstance(node, IntervalNode):
        check(node.lower)
        check(node.upper)
        return errors

    if isinstance(node, ListEnumNode):
        for e in node.elements:
            check(e)
        return errors

    if isinstance(node, ListRangeNode):
        check(node.lower)
        check(node.upper)
        return errors

    if isinstance(node, ListGetNode):
        check(node.list_node)
        check(node.index)
        return errors

    if isinstance(node, ListUpdateNode):
        check(node.base_list)
        check(node.index)
        check(node.new_value)
        return errors

    if isinstance(node, ListSliceNode):
        check(node.base_list)
        check(node.start)
        check(node.end)
        return errors

    if isinstance(node, ListFilterNode):
        check(node.base_list)
        check(node.predicate, bound_vars | {node.var.name})
        if isinstance(node.base_list.sort, ListSort) and (
            node.var.sort != node.base_list.sort.elem_sort
        ):
            sort_mismatch(
                f"ListFilter binder sort mismatch: var has {node.var.sort}, "
                f"list elements are {node.base_list.sort.elem_sort}"
            )
        return errors

    if isinstance(node, ListReduceNode):
        check(node.base_list)
        check(node.initial)
        check(node.fun, bound_vars | {node.acc_var.name, node.elem_var.name})
        if isinstance(node.base_list.sort, ListSort) and (
            node.elem_var.sort != node.base_list.sort.elem_sort
        ):
            sort_mismatch(
                f"ListReduce element binder sort mismatch: elem var has {node.elem_var.sort}, "
                f"list elements are {node.base_list.sort.elem_sort}"
            )
        return errors

    if isinstance(node, ListKeysNode):
        check(node.list_node)
        return errors

    if isinstance(node, MapEnumNode):
        for map_key, map_value in node.mappings.items():
            check(map_key)
            check(map_value)
        return errors

    if isinstance(node, MapLambdaNode):
        check(node.base_set)
        check(node.mapper, bound_vars | {node.var.name})
        if isinstance(node.base_set.sort, SetSort):
            if node.var.sort != node.base_set.sort.elem_sort:
                sort_mismatch(
                    f"MapLambda binder sort mismatch: var has {node.var.sort}, "
                    f"set elements are {node.base_set.sort.elem_sort}"
                )
        else:
            sort_mismatch(
                f"MapLambda base_set must have SetSort, got {node.base_set.sort}"
            )
        return errors

    if isinstance(node, MapGetNode):
        check(node.map_node)
        check(node.key)
        return errors

    if isinstance(node, MapSetNode):
        check(node.base_map)
        check(node.update_key)
        check(node.update_value)
        return errors

    if isinstance(node, MapKeysNode):
        check(node.map_node)
        return errors

    if isinstance(node, RecordCtorNode):
        for _, field_value in node.fields:
            check(field_value)
        return errors

    if isinstance(node, RecordGetNode):
        check(node.record_node)
        return errors

    if isinstance(node, RecordUpdateNode):
        check(node.base_record)
        for _, update_value in node.updates:
            check(update_value)
        return errors

    if isinstance(node, TupleCtorNode):
        for element in node.elements:
            check(element)
        return errors

    if isinstance(node, TupleGetNode):
        check(node.tuple_node)
        return errors

    if isinstance(node, TupleUpdateNode):
        check(node.base_tuple)
        check(node.new_value)
        return errors

    if isinstance(node, UnionCtorNode):
        if node.payload is not None:
            check(node.payload)
        return errors

    if isinstance(node, UnionGetTagNode):
        check(node.union_node)
        return errors

    if isinstance(node, UnionMatchNode):
        check(node.union_node)
        for _, (var_node, body_node) in node.cases.items():
            case_bound = bound_vars
            if var_node is not None:
                case_bound = case_bound | {var_node.name}
            check(body_node, case_bound)
        return errors

    if isinstance(node, ToTemporalNode):
        check(node.bool_formula)
        return errors

    if isinstance(node, (AlwaysNode, EventuallyNode)):
        check(node.subformula)
        return errors

    if isinstance(node, EnabledNode):
        check(node.action)
        return errors

    if isinstance(node, FairnessNode):
        check(node.action)
        return errors

    # Fallback for any future node types: recursively inspect node fields.
    for value in node.__dict__.values():
        if isinstance(value, Node):
            check(value)
        elif isinstance(value, tuple):
            for item in value:
                if isinstance(item, Node):
                    check(item)
                elif (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[1], Node)
                ):
                    check(item[1])
        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, Node):
                    check(item)
                elif (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[1], Node)
                ):
                    check(item[1])
    return errors


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _merge_node_summaries(target: _NodeSummary, source: _NodeSummary) -> None:
    target.reads_direct.update(source.reads_direct)
    target.writes_direct.update(source.writes_direct)
    target.reads_transitive.update(source.reads_transitive)
    target.writes_transitive.update(source.writes_transitive)
    for call in source.calls:
        _append_unique(target.calls, call)
    target.errors.extend(source.errors)


def _collect_state_reads(
    node: Node,
    state_scope: set[str],
    bound_vars: frozenset[str],
    _memo: dict[tuple[int, frozenset[str]], frozenset[str]] | None = None,
) -> set[str]:
    if _memo is None:
        _memo = {}
    memo_key = (id(node), bound_vars)
    cached = _memo.get(memo_key)
    if cached is not None:
        return set(cached)

    reads: set[str] = set()

    def collect(child: Node, child_bound: frozenset[str] = bound_vars) -> None:
        reads.update(_collect_state_reads(child, state_scope, child_bound, _memo))

    if isinstance(node, VarNode):
        if node.name in state_scope and node.name not in bound_vars:
            reads.add(node.name)
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, (LitNode, SetIntOrNatNode)):
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, LetNode):
        collect(node.value)
        collect(node.body, bound_vars | {node.name})
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, ExprCallNode):
        for arg in node.args:
            collect(arg)
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, AssumeNode):
        collect(node.condition)
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, AssignNode):
        collect(node.expr)
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, ActionCallNode):
        for arg in node.args:
            collect(arg)
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, ActionLetNode):
        collect(node.value)
        collect(node.body, bound_vars | {node.name})
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, NondetChoiceNode):
        collect(node.base_set)
        child_bound = (
            bound_vars | {node.var.name}
            if isinstance(node.var, VarNode)
            else bound_vars
        )
        collect(node.body, child_bound)
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, (ActionAndNode, ActionChoiceNode)):
        for action in node.actions:
            collect(action)
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, (SetFilterNode, SetMapNode, SetQuantNode)):
        active_bound = bound_vars
        for var, domain in node.bindings:
            collect(domain, active_bound)
            active_bound = active_bound | {var.name}
        collect(node.body, active_bound)
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, SetReduceNode):
        collect(node.base_set)
        collect(node.initial)
        collect(node.fun, bound_vars | {node.acc_var.name, node.elem_var.name})
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, ChooseNode):
        collect(node.base_set)
        collect(node.predicate, bound_vars | {node.var.name})
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, MapLambdaNode):
        collect(node.base_set)
        collect(node.mapper, bound_vars | {node.var.name})
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, ListFilterNode):
        collect(node.base_list)
        collect(node.predicate, bound_vars | {node.var.name})
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, ListReduceNode):
        collect(node.base_list)
        collect(node.initial)
        collect(node.fun, bound_vars | {node.acc_var.name, node.elem_var.name})
        _memo[memo_key] = frozenset(reads)
        return reads

    if isinstance(node, UnionMatchNode):
        collect(node.union_node)
        for _, (var_node, body_node) in node.cases.items():
            case_bound = bound_vars
            if var_node is not None:
                case_bound = case_bound | {var_node.name}
            collect(body_node, case_bound)
        _memo[memo_key] = frozenset(reads)
        return reads

    for value in node.__dict__.values():
        if isinstance(value, Node):
            collect(value)
        elif isinstance(value, tuple):
            for item in value:
                if isinstance(item, Node):
                    collect(item)
                elif isinstance(item, tuple):
                    for nested in item:
                        if isinstance(nested, Node):
                            collect(nested)
        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, Node):
                    collect(item)
                elif isinstance(item, tuple):
                    for nested in item:
                        if isinstance(nested, Node):
                            collect(nested)
    _memo[memo_key] = frozenset(reads)
    return reads


def _assigned_state_var(
    node: AssignNode,
    state_scope: set[str],
    bound_vars: frozenset[str],
) -> str | None:
    if isinstance(node.var, VarNode):
        if node.var.name in state_scope and node.var.name not in bound_vars:
            return node.var.name
    return None


def _duplicate_write_error(action_name: str, var_name: str) -> LintError:
    return _lint_error(
        action_name,
        f"State variable '{var_name}' may be assigned more than once in one action branch",
    )


def _summarize_action_node(
    node: Node,
    *,
    owner_name: str,
    state_scope: set[str],
    bound_vars: frozenset[str],
    action_defs: dict[str, _ActionDefInfo],
    memo: dict[str, _ActionSummary],
    active: set[str],
    read_memo: dict[tuple[int, frozenset[str]], frozenset[str]],
) -> _NodeSummary:
    summary = _NodeSummary()

    if isinstance(node, AssumeNode):
        reads = _collect_state_reads(node.condition, state_scope, bound_vars, read_memo)
        summary.reads_direct.update(reads)
        summary.reads_transitive.update(reads)
        return summary

    if isinstance(node, AssignNode):
        reads = _collect_state_reads(node.expr, state_scope, bound_vars, read_memo)
        summary.reads_direct.update(reads)
        summary.reads_transitive.update(reads)
        assigned = _assigned_state_var(node, state_scope, bound_vars)
        if assigned is not None:
            summary.writes_direct.add(assigned)
            summary.writes_transitive.add(assigned)
        return summary

    if isinstance(node, ActionAndNode):
        seen_writes: set[str] = set()
        for child in node.actions:
            child_summary = _summarize_action_node(
                child,
                owner_name=owner_name,
                state_scope=state_scope,
                bound_vars=bound_vars,
                action_defs=action_defs,
                memo=memo,
                active=active,
                read_memo=read_memo,
            )
            for var_name in sorted(seen_writes & child_summary.writes_transitive):
                summary.errors.append(_duplicate_write_error(owner_name, var_name))
            seen_writes.update(child_summary.writes_transitive)
            _merge_node_summaries(summary, child_summary)
        return summary

    if isinstance(node, ActionChoiceNode):
        for child in node.actions:
            child_summary = _summarize_action_node(
                child,
                owner_name=owner_name,
                state_scope=state_scope,
                bound_vars=bound_vars,
                action_defs=action_defs,
                memo=memo,
                active=active,
                read_memo=read_memo,
            )
            _merge_node_summaries(summary, child_summary)
        return summary

    if isinstance(node, ActionCallNode):
        for arg in node.args:
            arg_reads = _collect_state_reads(arg, state_scope, bound_vars, read_memo)
            summary.reads_direct.update(arg_reads)
            summary.reads_transitive.update(arg_reads)
        _append_unique(summary.calls, node.action_name)
        if node.action_name in action_defs:
            callee = _summarize_action(
                node.action_name,
                state_scope=state_scope,
                action_defs=action_defs,
                memo=memo,
                active=active,
            )
            summary.reads_transitive.update(callee.effects.reads_transitive)
            summary.writes_transitive.update(callee.effects.writes_transitive)
        return summary

    if isinstance(node, ActionLetNode):
        value_reads = _collect_state_reads(
            node.value, state_scope, bound_vars, read_memo
        )
        summary.reads_direct.update(value_reads)
        summary.reads_transitive.update(value_reads)
        body_summary = _summarize_action_node(
            node.body,
            owner_name=owner_name,
            state_scope=state_scope,
            bound_vars=bound_vars | {node.name},
            action_defs=action_defs,
            memo=memo,
            active=active,
            read_memo=read_memo,
        )
        _merge_node_summaries(summary, body_summary)
        return summary

    if isinstance(node, NondetChoiceNode):
        base_reads = _collect_state_reads(
            node.base_set, state_scope, bound_vars, read_memo
        )
        summary.reads_direct.update(base_reads)
        summary.reads_transitive.update(base_reads)
        child_bound = (
            bound_vars | {node.var.name}
            if isinstance(node.var, VarNode)
            else bound_vars
        )
        body_summary = _summarize_action_node(
            node.body,
            owner_name=owner_name,
            state_scope=state_scope,
            bound_vars=child_bound,
            action_defs=action_defs,
            memo=memo,
            active=active,
            read_memo=read_memo,
        )
        _merge_node_summaries(summary, body_summary)
        return summary

    reads = _collect_state_reads(node, state_scope, bound_vars, read_memo)
    summary.reads_direct.update(reads)
    summary.reads_transitive.update(reads)
    return summary


def _summarize_action(
    action_name: str,
    *,
    state_scope: set[str],
    action_defs: dict[str, _ActionDefInfo],
    memo: dict[str, _ActionSummary],
    active: set[str],
) -> _ActionSummary:
    if action_name in memo:
        return memo[action_name]

    info = action_defs[action_name]
    if action_name in active:
        empty = _ActionSummary(
            effects=ActionEffects(
                name=action_name,
                kind=info.kind,
                reads_direct=frozenset(),
                writes_direct=frozenset(),
                reads_transitive=frozenset(),
                writes_transitive=frozenset(),
                calls=(),
            ),
            errors=(),
        )
        memo[action_name] = empty
        return empty

    active.add(action_name)
    read_memo: dict[tuple[int, frozenset[str]], frozenset[str]] = {}
    node_summary = _summarize_action_node(
        info.node,
        owner_name=action_name,
        state_scope=state_scope,
        bound_vars=info.bound_vars,
        action_defs=action_defs,
        memo=memo,
        active=active,
        read_memo=read_memo,
    )
    active.remove(action_name)

    effect = ActionEffects(
        name=action_name,
        kind=info.kind,
        reads_direct=frozenset(node_summary.reads_direct),
        writes_direct=frozenset(node_summary.writes_direct),
        reads_transitive=frozenset(node_summary.reads_transitive),
        writes_transitive=frozenset(node_summary.writes_transitive),
        calls=tuple(node_summary.calls),
    )
    summary = _ActionSummary(effect, tuple(node_summary.errors))
    memo[action_name] = summary
    return summary


def _format_effect_values(values: frozenset[str] | set[str] | tuple[str, ...]) -> str:
    if not values:
        return "-"
    return (
        ", ".join(sorted(values))
        if not isinstance(values, tuple)
        else ", ".join(values)
    )


def _qualify_effect(effect: ActionEffects, prefix: str) -> ActionEffects:
    qualified = f"{prefix}.{effect.name}"
    return ActionEffects(
        name=qualified,
        kind=effect.kind,
        reads_direct=effect.reads_direct,
        writes_direct=effect.writes_direct,
        reads_transitive=effect.reads_transitive,
        writes_transitive=effect.writes_transitive,
        calls=tuple(
            call if "." in call else f"{prefix}.{call}" for call in effect.calls
        ),
    )


def _iter_related_spec_modules(module: Any, path: Path) -> list[tuple[str, Path]]:
    related: list[tuple[str, Path]] = []
    base_dir = path.resolve().parent
    for name, value in module.__dict__.items():
        if not isinstance(value, ModuleType):
            continue
        module_file = getattr(value, "__file__", None)
        if module_file is None:
            continue
        module_path = Path(module_file).resolve()
        if module_path == path.resolve():
            continue
        if module_path.suffix != ".py":
            continue
        if module_path.parent != base_dir:
            continue
        related.append((name, module_path))
    related.sort(key=lambda item: item[0])
    return related


def render_effects_report(effects: dict[str, ActionEffects]) -> str:
    lines: list[str] = []
    for action_name in sorted(effects):
        effect = effects[action_name]
        lines.append(f"Action: {effect.name} [{effect.kind}]")
        lines.append(f"  reads_direct: {_format_effect_values(effect.reads_direct)}")
        lines.append(f"  writes_direct: {_format_effect_values(effect.writes_direct)}")
        lines.append(
            f"  reads_transitive: {_format_effect_values(effect.reads_transitive)}"
        )
        lines.append(
            f"  writes_transitive: {_format_effect_values(effect.writes_transitive)}"
        )
        lines.append(f"  calls: {_format_effect_values(effect.calls)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n" if lines else ""


def _is_state_class(annotation: Any) -> bool:
    """Return True if *annotation* looks like a ``@state``-decorated class."""
    return (
        isinstance(annotation, type)
        and hasattr(annotation, "_params")
        and isinstance(getattr(annotation, "_params"), tuple)
        and hasattr(annotation, "_vars")
        and isinstance(getattr(annotation, "_vars"), tuple)
    )


def _is_name(node: py_ast.AST, name: str) -> bool:
    return isinstance(node, py_ast.Name) and node.id == name


def _extract_function_ast(
    func: Any,
) -> tuple[py_ast.FunctionDef, list[str], int] | None:
    wrapped = inspect.unwrap(func)
    try:
        source_lines, start_lineno = inspect.getsourcelines(wrapped)
    except (OSError, TypeError):
        return None

    source = textwrap.dedent("".join(source_lines))
    try:
        parsed = py_ast.parse(source)
    except SyntaxError:
        return None

    for node in parsed.body:
        if isinstance(node, py_ast.FunctionDef):
            return node, source.splitlines(), start_lineno
    return None


class _LazyAliasWarningAnalyzer:
    def __init__(
        self,
        *,
        func_name: str,
        filename: str,
        source_lines: list[str],
        start_lineno: int,
        context_name: str,
        state_vars: set[str],
        action_effects: dict[str, ActionEffects],
    ) -> None:
        self.func_name = func_name
        self.filename = filename
        self.source_lines = source_lines
        self.start_lineno = start_lineno
        self.context_name = context_name
        self.state_vars = state_vars
        self.action_effects = action_effects
        self.state_aliases: set[str] = set()
        self.aliases: dict[str, _CapturedStateAlias] = {}
        self.generations: dict[str, int] = {name: 0 for name in state_vars}
        self.warnings: list[LintWarning] = []
        self._emitted: set[tuple[str, str, int]] = set()

    def copy(self) -> "_LazyAliasWarningAnalyzer":
        other = _LazyAliasWarningAnalyzer(
            func_name=self.func_name,
            filename=self.filename,
            source_lines=self.source_lines,
            start_lineno=self.start_lineno,
            context_name=self.context_name,
            state_vars=self.state_vars,
            action_effects=self.action_effects,
        )
        other.state_aliases = set(self.state_aliases)
        other.aliases = dict(self.aliases)
        other.generations = dict(self.generations)
        other.warnings = self.warnings
        other._emitted = self._emitted
        return other

    def analyze(self, body: list[py_ast.stmt]) -> list[LintWarning]:
        self._analyze_block(body)
        return self.warnings

    def _source_for(self, node: py_ast.AST) -> list[LintErrorSource]:
        lineno = getattr(node, "lineno", 0)
        absolute = self.start_lineno + lineno - 1 if lineno else self.start_lineno
        source_line = ""
        if lineno and 0 < lineno <= len(self.source_lines):
            source_line = self.source_lines[lineno - 1].strip()
        return [
            LintErrorSource(
                filename=self.filename,
                lineno=absolute,
                line=source_line,
                func=self.func_name,
            )
        ]

    def _warn_if_stale_alias(self, alias_name: str, node: py_ast.AST) -> None:
        captured = self.aliases.get(alias_name)
        if captured is None:
            return
        for root in sorted(captured.roots):
            if self.generations.get(root, 0) <= captured.generations.get(root, 0):
                continue
            line = getattr(node, "lineno", 0)
            key = (alias_name, root, line)
            if key in self._emitted:
                continue
            self._emitted.add(key)
            self.warnings.append(
                _lint_warning(
                    self.func_name,
                    f"Local '{alias_name}' captures state variable '{root}' before "
                    f"a later write to '{root}' and is used after that write; "
                    "use c.cache(...) if you intended a snapshot, or reorder "
                    "assignments so this read happens after the write.",
                    self._source_for(node),
                )
            )

    def _is_context_state(self, node: py_ast.AST) -> bool:
        return (
            isinstance(node, py_ast.Attribute)
            and node.attr == "state"
            and _is_name(node.value, self.context_name)
        )

    def _direct_state_root(self, node: py_ast.AST) -> str | None:
        if not isinstance(node, py_ast.Attribute):
            return None
        if node.attr not in self.state_vars:
            return None
        if isinstance(node.value, py_ast.Name) and node.value.id in self.state_aliases:
            return node.attr
        if self._is_context_state(node.value):
            return node.attr
        return None

    def _alias_chain_roots(self, node: py_ast.AST) -> set[str]:
        direct = self._direct_state_root(node)
        if direct is not None:
            return {direct}

        if isinstance(node, py_ast.Name):
            captured = self.aliases.get(node.id)
            return set(captured.roots) if captured is not None else set()

        if isinstance(node, py_ast.Attribute):
            return self._alias_chain_roots(node.value)

        if isinstance(node, py_ast.Subscript):
            return self._alias_chain_roots(node.value)

        return set()

    def _is_cache_call(self, node: py_ast.AST) -> bool:
        return (
            isinstance(node, py_ast.Call)
            and isinstance(node.func, py_ast.Attribute)
            and node.func.attr == "cache"
            and _is_name(node.func.value, self.context_name)
        )

    def _action_call_writes(self, node: py_ast.AST) -> set[str]:
        if not isinstance(node, py_ast.Call):
            return set()
        name: str | None = None
        if isinstance(node.func, py_ast.Name):
            name = node.func.id
        elif isinstance(node.func, py_ast.Attribute):
            name = node.func.attr
        if name is None:
            return set()
        effect = self.action_effects.get(name)
        if effect is None:
            return set()
        return set(effect.writes_transitive)

    def _visit_expr(self, node: py_ast.AST) -> None:
        for child in py_ast.walk(node):
            if isinstance(child, py_ast.Name):
                self._warn_if_stale_alias(child.id, child)

    def _write_roots_for_target(self, node: py_ast.AST) -> set[str]:
        roots = self._alias_chain_roots(node)
        if roots:
            return roots
        if isinstance(node, (py_ast.Attribute, py_ast.Subscript)):
            return self._write_roots_for_target(node.value)
        return set()

    def _record_writes(self, roots: set[str]) -> None:
        for root in roots:
            if root in self.state_vars:
                self.generations[root] = self.generations.get(root, 0) + 1

    def _bind_target_from_value(self, target: py_ast.AST, value: py_ast.AST) -> None:
        if not isinstance(target, py_ast.Name):
            return
        name = target.id
        if self._is_context_state(value):
            self.state_aliases.add(name)
            self.aliases.pop(name, None)
            return
        self.state_aliases.discard(name)
        if self._is_cache_call(value):
            self.aliases.pop(name, None)
            return
        roots = self._alias_chain_roots(value)
        if roots:
            self.aliases[name] = _CapturedStateAlias(
                roots=frozenset(roots),
                generations={root: self.generations.get(root, 0) for root in roots},
            )
        else:
            self.aliases.pop(name, None)

    def _merge_after_branches(
        self,
        base: "_LazyAliasWarningAnalyzer",
        branches: list["_LazyAliasWarningAnalyzer"],
    ) -> None:
        for root in self.state_vars:
            self.generations[root] = max(
                branch.generations.get(root, 0) for branch in branches
            )
        # Keep outer aliases and state aliases stable. Branch-local bindings may be
        # ambiguous outside the branch, so only preserve aliases that all branches
        # agree on exactly.
        self.state_aliases = set(base.state_aliases)
        for name in set.intersection(*(set(b.state_aliases) for b in branches)):
            self.state_aliases.add(name)
        self.aliases = dict(base.aliases)
        common_aliases = set.intersection(*(set(b.aliases) for b in branches))
        for name in common_aliases:
            captures = [b.aliases[name] for b in branches]
            first = captures[0]
            if all(capture == first for capture in captures[1:]):
                self.aliases[name] = first

    def _alternative_alias_names(self, node: py_ast.With) -> set[str]:
        if not node.items:
            return set()
        expr = node.items[0].context_expr
        if not (
            isinstance(expr, py_ast.Call)
            and isinstance(expr.func, py_ast.Attribute)
            and expr.func.attr in {"alternatives", "split"}
            and _is_name(expr.func.value, self.context_name)
        ):
            return set()
        optional_vars = node.items[0].optional_vars
        if isinstance(optional_vars, py_ast.Tuple):
            return {
                item.id for item in optional_vars.elts if isinstance(item, py_ast.Name)
            }
        if isinstance(optional_vars, py_ast.Name):
            return {optional_vars.id}
        return set()

    def _analyze_block(self, body: list[py_ast.stmt]) -> None:
        for stmt in body:
            self._analyze_stmt(stmt)

    def _analyze_stmt(self, stmt: py_ast.stmt) -> None:
        if isinstance(stmt, py_ast.Assign):
            self._visit_expr(stmt.value)
            for target in stmt.targets:
                if not isinstance(target, py_ast.Name):
                    self._visit_expr(target)
            for target in stmt.targets:
                if not isinstance(target, py_ast.Name):
                    self._record_writes(self._write_roots_for_target(target))
            for target in stmt.targets:
                self._bind_target_from_value(target, stmt.value)
            return

        if isinstance(stmt, py_ast.AnnAssign):
            if stmt.value is not None:
                self._visit_expr(stmt.value)
            if not isinstance(stmt.target, py_ast.Name):
                self._visit_expr(stmt.target)
                self._record_writes(self._write_roots_for_target(stmt.target))
            if stmt.value is not None:
                self._bind_target_from_value(stmt.target, stmt.value)
            return

        if isinstance(stmt, py_ast.AugAssign):
            if not isinstance(stmt.target, py_ast.Name):
                self._visit_expr(stmt.target)
            self._visit_expr(stmt.value)
            if not isinstance(stmt.target, py_ast.Name):
                self._record_writes(self._write_roots_for_target(stmt.target))
            if isinstance(stmt.target, py_ast.Name):
                self.aliases.pop(stmt.target.id, None)
                self.state_aliases.discard(stmt.target.id)
            return

        if isinstance(stmt, py_ast.Expr):
            self._visit_expr(stmt.value)
            if isinstance(stmt.value, py_ast.Call):
                self._record_writes(self._action_call_writes(stmt.value))
            return

        if isinstance(stmt, py_ast.If):
            self._visit_expr(stmt.test)
            base = self.copy()
            body_analyzer = base.copy()
            body_analyzer._analyze_block(stmt.body)
            else_analyzer = base.copy()
            else_analyzer._analyze_block(stmt.orelse)
            self._merge_after_branches(base, [body_analyzer, else_analyzer])
            return

        if isinstance(stmt, py_ast.With):
            for item in stmt.items:
                self._visit_expr(item.context_expr)
            alternatives = self._alternative_alias_names(stmt)
            if alternatives:
                branch_bodies: list[list[py_ast.stmt]] = []
                for child in stmt.body:
                    if (
                        isinstance(child, py_ast.With)
                        and child.items
                        and isinstance(child.items[0].context_expr, py_ast.Name)
                        and child.items[0].context_expr.id in alternatives
                    ):
                        branch_bodies.append(child.body)
                if branch_bodies:
                    base = self.copy()
                    branches = []
                    for branch_body in branch_bodies:
                        branch = base.copy()
                        branch._analyze_block(branch_body)
                        branches.append(branch)
                    self._merge_after_branches(base, branches)
                    return
            self._analyze_block(stmt.body)
            return

        if isinstance(stmt, (py_ast.For, py_ast.AsyncFor, py_ast.While)):
            if isinstance(stmt, (py_ast.For, py_ast.AsyncFor)):
                self._visit_expr(stmt.iter)
                self._visit_expr(stmt.target)
            else:
                self._visit_expr(stmt.test)
            base = self.copy()
            loop = base.copy()
            loop._analyze_block(stmt.body)
            orelse = loop.copy()
            orelse._analyze_block(stmt.orelse)
            self._merge_after_branches(base, [base, loop, orelse])
            return

        if isinstance(stmt, py_ast.Return):
            if stmt.value is not None:
                self._visit_expr(stmt.value)
            return

        for child_node in py_ast.iter_child_nodes(stmt):
            if isinstance(child_node, py_ast.expr):
                self._visit_expr(child_node)


def _lazy_alias_warnings_for_action(
    func: Any,
    *,
    state_cls: type[Any],
    action_effects: dict[str, ActionEffects],
) -> list[LintWarning]:
    extracted = _extract_function_ast(func)
    if extracted is None:
        return []
    function_node, source_lines, start_lineno = extracted
    wrapped = inspect.unwrap(func)
    params = list(inspect.signature(wrapped).parameters)
    if not params:
        return []
    filename = inspect.getsourcefile(wrapped) or inspect.getfile(wrapped)
    analyzer = _LazyAliasWarningAnalyzer(
        func_name=getattr(func, "_action_name", wrapped.__name__),
        filename=filename,
        source_lines=source_lines,
        start_lineno=start_lineno,
        context_name=params[0],
        state_vars=set(state_cls._vars),
        action_effects=action_effects,
    )
    return analyzer.analyze(function_node.body)


def analyze(
    path: Path,
    *,
    _visited: set[Path] | None = None,
    _include_related: bool = True,
    _include_errors: bool = True,
) -> LintAnalysis:
    resolved_path = path.resolve()
    if _visited is None:
        _visited = set()
    if resolved_path in _visited:
        return LintAnalysis(errors=[], warnings=[], effects={})
    _visited.add(resolved_path)

    errors: list[LintError] = []
    warnings: list[LintWarning] = []
    action_defs: dict[str, _ActionDefInfo] = {}
    action_funcs: dict[str, Any] = {}

    try:
        module = load_module(path)
    except BaseException as exc:  # includes SystemExit from cli.fatal
        return LintAnalysis(
            errors=[_lint_error("<module>", str(exc))],
            warnings=[],
            effects={},
        )

    state_classes = find_state_classes(module)
    if len(state_classes) == 0:
        return LintAnalysis(
            errors=[_lint_error("<module>", "No @state-decorated class found")],
            warnings=[],
            effects={},
        )
    if len(state_classes) > 1:
        return LintAnalysis(
            errors=[
                _lint_error("<module>", "Multiple @state classes found, expected 1")
            ],
            warnings=[],
            effects={},
        )

    state_cls = state_classes[0]
    state_scope = set(state_cls._params + state_cls._vars)

    for _, func in inspect.getmembers(module, inspect.isfunction):
        func_name = func.__name__
        built_nodes: list[tuple[Node, frozenset[str], str]] = []

        try:
            if hasattr(func, "_action_name"):
                action_node, extracted_actions, action_params = _build_ast_for_action(
                    state_cls, func
                )
                built_nodes.append((action_node, action_params, func_name))
                action_defs[func_name] = _ActionDefInfo(
                    node=action_node,
                    bound_vars=action_params,
                    kind="top-level",
                )
                action_funcs[func_name] = func
                for extracted_name, extracted_action in extracted_actions.items():
                    extracted_bound = frozenset(extracted_action.param_names)
                    built_nodes.append(
                        (extracted_action.body, extracted_bound, extracted_name)
                    )
                    action_defs[extracted_name] = _ActionDefInfo(
                        node=extracted_action.body,
                        bound_vars=extracted_bound,
                        kind="extracted",
                    )
            elif getattr(func, "_is_invariant", False) or getattr(
                func, "_is_temporal", False
            ):
                built_nodes.append(
                    (build_expr_ast(state_cls, func), frozenset(), func_name)
                )
            elif getattr(func, "_is_expr", False):
                is_pure = getattr(func, "_is_expr_pure", False)
                sig = inspect.signature(func)
                params = list(sig.parameters.values())
                if params:
                    first_ann = params[0].annotation
                    state_like = (
                        first_ann is not inspect.Parameter.empty
                        and _is_state_class(first_ann)
                    )
                    if is_pure and state_like:
                        errors.append(
                            _lint_error(
                                func_name,
                                f"@expr(pure=True) must not have a state as its first "
                                f"parameter, but '{params[0].name}' is annotated with "
                                f"{first_ann.__name__!r}",
                            )
                        )
                    elif not is_pure and not state_like:
                        errors.append(
                            _lint_error(
                                func_name,
                                f"@expr must have a state as its first parameter, "
                                f"but '{params[0].name}' is not annotated with a "
                                f"@state class",
                            )
                        )
                if not is_pure and len(params) == 1:
                    built_nodes.append(
                        (build_expr_ast(state_cls, func), frozenset(), func_name)
                    )
                else:
                    continue
            else:
                continue
        except BaseException as exc:  # keep linting other defs
            if isinstance(exc, _UninferrableActionParamSort):
                continue
            sources = _extract_source_from_exc(exc, path)
            errors.append(_lint_error(func_name, str(exc), sources))
            continue

        for node, initial_bound_vars, node_name in built_nodes:
            errors.extend(
                _check_node(
                    node,
                    func_name=node_name,
                    state_scope=state_scope,
                    bound_vars=initial_bound_vars,
                )
            )

    memo: dict[str, _ActionSummary] = {}
    active: set[str] = set()
    effects: dict[str, ActionEffects] = {}
    for action_name in sorted(action_defs):
        summary = _summarize_action(
            action_name,
            state_scope=state_scope,
            action_defs=action_defs,
            memo=memo,
            active=active,
        )
        effects[action_name] = summary.effects
        errors.extend(summary.errors)

    if _include_errors:
        for action_name in sorted(action_funcs):
            warnings.extend(
                _lazy_alias_warnings_for_action(
                    action_funcs[action_name],
                    state_cls=state_cls,
                    action_effects=effects,
                )
            )

    if _include_related:
        for module_name, related_path in _iter_related_spec_modules(module, path):
            related = analyze(
                related_path,
                _visited=_visited,
                _include_related=True,
                _include_errors=False,
            )
            for effect_name, effect in related.effects.items():
                qualified = _qualify_effect(effect, module_name)
                effects[qualified.name] = qualified

    return LintAnalysis(
        errors=errors if _include_errors else [],
        warnings=warnings if _include_errors else [],
        effects=effects,
    )


def lint(path: Path) -> list[LintError]:
    return analyze(path).errors
