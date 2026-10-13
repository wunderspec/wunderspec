# Moved from wunderspec-lean commit 8cc1dcec2f7eb5f86345161a9a9bd677b2d72e66.
"""
Gather a Wunderspec spec module into a structured, backend-neutral form.

Both the Lean-source emitter (`ws_lean.emit`) and the canonical-JSON serializer
(`ws_lean.ast_json`) consume a `GatheredSpec`, so the discovery/build logic lives
in one place. This mirrors the convert orchestration in `wunderspec.api`.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from wunderspec.api import build_action_ast, build_expr_ast
from wunderspec.ast.action_ast import ActionCallNode
from wunderspec.ast.ast import ExprCallNode, Node
from wunderspec.machine import find_instance_factories, get_wunderspec_exports
from wunderspec.source_tracking import enable_source_tracking
from wunderspec.uniq_names import reset_name_counter


@dataclass
class GDef:
    name: str
    kind: str  # action | init | invariant | example | temporal | coverage | expr
    body: Any  # wunderspec Node
    params: list[tuple[str, Any]] = field(default_factory=list)


@dataclass
class GExtracted:
    name: str
    param_names: tuple[str, ...]
    param_sorts: tuple[Any, ...]
    body: Any


@dataclass
class GInstance:
    name: str
    # (param_name, node): a LitNode, or from format 1.1 a closed expression (spec 006)
    params: list[tuple[str, Node]]


@dataclass
class GatheredSpec:
    name: str
    src_file: str | None
    params: list[tuple[str, Any]]  # (name, Sort)
    state_vars: list[tuple[str, Any]]  # (name, Sort)
    defs: list[GDef]
    extracted: list[GExtracted]  # sorted by name
    instances: list[GInstance]
    # Names of non-inlined `@expr(pure=True)` helpers.  `ExprCallNode` does not
    # carry purity itself, so Step B uses this registry to decide whether the
    # generated Lean definition/call needs a trailing `State` argument.
    pure_exprs: list[str]
    # Python docstrings attached to Wunderspec operators, keyed by the raw
    # operator name used in the emitted Lean.
    operator_comments: dict[str, str]
    # Nominal names for structural record/union sorts: the structural `Sort.name`
    # string (e.g. "RecordSort(balances=…)") → the `@record`/`@union` class name
    # (e.g. "EvmState"). Lets records/unions lower to *named* Lean
    # structures/inductives instead of synthesized field-/tag-derived names.
    record_names: dict[str, str]
    union_names: dict[str, str]
    files: list[str] = field(default_factory=list)
    producer: dict[str, str] | None = None


def find_state_class(module: Any) -> Any:
    exported_names = get_wunderspec_exports(module)
    if exported_names is not None:
        candidates = (
            getattr(module, name)
            for name in exported_names
            if inspect.isclass(getattr(module, name))
        )
    else:
        candidates = (
            obj
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__
        )

    for obj in candidates:
        if hasattr(obj, "_params") and hasattr(obj, "_vars"):
            return obj
    raise ValueError("no @state class found in module")


def discover_defs(module: Any, state_cls: Any) -> list[tuple[str, Any, str]]:
    """Mirror of api.py::_discover_defs — find translatable top-level defs."""
    out: list[tuple[str, Any, str]] = []
    exported_names = get_wunderspec_exports(module)
    if exported_names is not None:
        candidates = (
            (name, getattr(module, name))
            for name in exported_names
            if inspect.isfunction(getattr(module, name))
        )
    else:
        candidates = (
            (name, func)
            for name, func in inspect.getmembers(module, inspect.isfunction)
            if func.__module__ == module.__name__
        )

    seen: set[int] = set()
    for name, func in candidates:
        if id(func) in seen:
            continue
        seen.add(id(func))
        if hasattr(func, "_action_name"):
            if len(inspect.signature(func).parameters) == 1:
                kind = "init" if getattr(func, "_is_init", False) else "action"
                out.append((name, func, kind))
        elif getattr(func, "_is_invariant", False):
            out.append((name, func, "invariant"))
        elif getattr(func, "_is_example", False):
            out.append((name, func, "example"))
        elif getattr(func, "_is_temporal", False):
            out.append((name, func, "temporal"))
        elif getattr(func, "_is_coverage", False):
            out.append((name, func, "coverage"))
        elif getattr(func, "_is_expr", False) and not getattr(
            func, "_is_expr_pure", False
        ):
            if len(inspect.signature(func).parameters) == 1:
                out.append((name, func, "expr"))
    return out


def _scan_modules(module: Any) -> list[Any]:
    """The spec module plus imported sibling modules.

    Wunderspec examples commonly keep shared definitions in a sibling module.
    Keep type-name and expression-metadata discovery on the same scope.
    """
    spec_dir = None
    spec_file = getattr(module, "__file__", None)
    if spec_file is not None:
        spec_dir = os.path.dirname(os.path.abspath(spec_file))
    scan_modules = [module]
    for _, obj in inspect.getmembers(module, inspect.ismodule):
        mf = getattr(obj, "__file__", None)
        if (
            mf is not None
            and spec_dir is not None
            and os.path.dirname(os.path.abspath(mf)) == spec_dir
        ):
            scan_modules.append(obj)
    return scan_modules


def _is_operator_function(func: Any) -> bool:
    return (
        hasattr(func, "_action_name")
        or getattr(func, "_is_invariant", False)
        or getattr(func, "_is_example", False)
        or getattr(func, "_is_temporal", False)
        or getattr(func, "_is_coverage", False)
        or getattr(func, "_is_expr", False)
    )


def _iter_child_nodes(value: Any) -> list[Node]:
    """Return AST nodes nested in a node attribute value."""
    if isinstance(value, Node):
        return [value]
    if isinstance(value, dict):
        return [
            node
            for key, child in value.items()
            for item in (key, child)
            for node in _iter_child_nodes(item)
        ]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [node for item in value for node in _iter_child_nodes(item)]
    return []


def _collect_node_comments(node: Node, out: dict[str, str]) -> None:
    """Collect fallback docstrings retained on non-inline call nodes."""
    if isinstance(node, ExprCallNode) and node.comment:
        out.setdefault(node.op_name, node.comment)
    elif isinstance(node, ActionCallNode) and node.comment:
        out.setdefault(node.action_name, node.comment)
    for value in vars(node).values():
        for child in _iter_child_nodes(value):
            _collect_node_comments(child, out)


def _build_type_names(module: Any, sort_attr: str) -> dict[str, str]:
    """Map the structural `Sort.name` of each `@record`/`@union` class reachable
    from `module` to the class's Python name.

    `@record`/`@union` store their (structural) sort on the class as
    `_record_sort` / `_union_sort`; the class `__name__` is the nominal name we
    want in the generated Lean. Classes imported with `from … import *` are bound
    as module attributes, so they are reached too. Raises on a genuine collision
    (two differently-shaped classes sharing a name).
    """
    out: dict[str, str] = {}
    seen_names: dict[str, str] = {}  # class __name__ -> structural sort name

    # Scan the spec module plus any *sibling* module it imported (same directory,
    # e.g. fifo's `import channel`), so records/unions defined in a helper module
    # and referenced as `channel.Chan` are named too.
    for mod in _scan_modules(module):
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            sort = getattr(obj, sort_attr, None)
            if sort is None:
                continue
            cls_name = obj.__name__
            struct = sort.name
            prev = seen_names.get(cls_name)
            if prev is not None and prev != struct:
                raise ValueError(
                    f"wunderspec.transfer: two distinct {sort_attr} shapes both named "
                    f"'{cls_name}'; cannot pick a Lean type name"
                )
            seen_names[cls_name] = struct
            out[struct] = cls_name
    return out


def gather(module: Any, name: str | None = None) -> GatheredSpec:
    # Wunderspec names comprehension variables (_v0, _v1, …) from a process-global
    # counter; reset it so repeated gathers of the same spec are deterministic.
    reset_name_counter()
    state_cls = find_state_class(module)
    spec_name = str(name or getattr(module, "__name__", "spec"))
    src_file = getattr(module, "__file__", None)
    if src_file is not None:
        src_file = os.path.abspath(src_file)

    params = [(p, getattr(state_cls, p).sort) for p in state_cls._params]
    state_vars = [(v, getattr(state_cls, v).sort) for v in state_cls._vars]

    def_items = discover_defs(module, state_cls)
    # `@invariant` and `@example` are expression helpers too.  Wunderspec only
    # preserves calls to them when AST construction runs under its predicate
    # extraction context.  Suppress the definition currently being built so its
    # root remains a body rather than a recursive call to itself.
    predicate_ops: dict[Callable[..., Any], str] = {
        func: bound_name
        for bound_name, func in inspect.getmembers(module, inspect.isfunction)
        if (
            (
                getattr(func, "_is_invariant", False)
                or getattr(func, "_is_example", False)
            )
            and not getattr(func, "_inline", False)
        )
    }
    # Explicit exports may rename a function; the exported operator name wins.
    body: Node
    for def_name, func, kind in def_items:
        if kind in ("invariant", "example") and not getattr(func, "_inline", False):
            predicate_ops[func] = def_name

    # Match Wunderspec's TLA+ comment extraction: decorated-function docstrings
    # are operator comments, including imported functions bound on the spec
    # module. Explicit export aliases are recorded under their emitted names.
    operator_comments: dict[str, str] = {}
    for bound_name, func in inspect.getmembers(module, inspect.isfunction):
        if _is_operator_function(func) and (doc := inspect.getdoc(func)):
            operator_comments[bound_name] = doc
    for def_name, func, _ in def_items:
        if doc := inspect.getdoc(func):
            operator_comments[def_name] = doc

    defs: list[GDef] = []
    extracted_map: dict[str, Any] = {}
    with enable_source_tracking():
        for def_name, func, kind in def_items:
            if kind in ("action", "init"):
                body, ex = build_action_ast(state_cls, func)
                extracted_map.update(ex)
            else:
                body = build_expr_ast(
                    state_cls,
                    func,
                    predicate_ops=predicate_ops,
                    suppress_predicate=func,
                )
            defs.append(GDef(def_name, kind, body))
            _collect_node_comments(body, operator_comments)

    extracted = [
        GExtracted(act_name, d.param_names, d.param_sorts, d.body)
        for act_name, d in sorted(extracted_map.items())
    ]
    for act_name, d in extracted_map.items():
        if d.comment:
            operator_comments.setdefault(act_name, d.comment)
        _collect_node_comments(d.body, operator_comments)

    # Instances fix the machine parameters. A literal value is carried as such (format
    # 1.0); any other value is carried as the expression the factory built (format 1.1,
    # wunderspec-saas spec 006). ``export_spec`` drops an instance whose expression the
    # validator rejects (rule 3a), with a warning.
    instances = [
        GInstance(
            inst_name,
            [(p, getattr(obj, p).node) for p in state_cls._params],
        )
        for inst_name, obj in (
            (inst_name, factory())
            for inst_name, factory in find_instance_factories(module)
        )
    ]

    return GatheredSpec(
        name=spec_name,
        src_file=src_file,
        params=params,
        state_vars=state_vars,
        defs=defs,
        extracted=extracted,
        instances=instances,
        pure_exprs=sorted(
            {
                obj.__name__
                for mod in _scan_modules(module)
                for _, obj in inspect.getmembers(mod, inspect.isfunction)
                if (
                    getattr(obj, "_is_expr", False)
                    and getattr(obj, "_is_expr_pure", False)
                    and not getattr(obj, "_inline", False)
                )
            }
        ),
        operator_comments=operator_comments,
        record_names=_build_type_names(module, "_record_sort"),
        union_names=_build_type_names(module, "_union_sort"),
    )
