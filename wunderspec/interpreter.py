"""
An interpreter of Wunderspec AST nodes for pure (non-action) expressions.  This
interpreter is a simple evaluator of specification expressions to concrete
values in Python. The goal is not to provide the most efficient implementation,
but to provide a clear and simple implementation that everyone can understand
the expression semantics.

Note that we only interpret AST nodes that deal with pure expressions.  Action
nodes call for a variety of different approaches such as randomize testing,
model checking, and symbolic execution.

The interpreter uses `@singledispatch` to dispatch on AST node types, making
it extensible. Users can register custom handlers for new node types without
modifying this module. A hot-path optimization using dict-based caching avoids
repeated dispatch lookups.

Igor Konnov, 2025-2026.
"""

from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from enum import Enum
from functools import singledispatch
from typing import Any, Callable, Iterator, Protocol, cast

from pyrsistent import pmap, pset

from wunderspec.ast import (
    ARITH_OPS,
    BOOL_OPS,
    CMP_OPS,
    EQ_OPS,
    LIST_OPS,
    SET_OPS,
    AlgebraNode,
    AlgebraOp,
    BoolSort,
    EnumSort,
    ExprCallNode,
    InNode,
    IntSort,
    IteNode,
    LetNode,
    LitNode,
    Node,
    QuantOp,
    RecordCtorNode,
    RecordGetNode,
    RecordUpdateNode,
    SetSort,
    StrSort,
    TupleCtorNode,
    TupleGetNode,
    TupleUpdateNode,
    UnionSort,
    VarNode,
)
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
from wunderspec.ast.temporal_ast import (
    AlwaysNode,
    EnabledNode,
    EventuallyNode,
    FairnessNode,
    ToTemporalNode,
)
from wunderspec.ast.union_ast import UnionCtorNode, UnionGetTagNode, UnionMatchNode
from wunderspec.errors import EvaluationError, _is_control_flow, is_locating_errors
from wunderspec.interpreter_value import (
    AbstractSetValue,
    AllMapsValue,
    AllRecordsValue,
    AllSubsetsValue,
    AllTuplesValue,
    BoolValue,
    EnumeratedSetValue,
    EnumValue,
    InfIntSetValue,
    IntervalSetValue,
    IntValue,
    IValue,
    IValueNode,
    ListValue,
    MapValue,
    RecordValue,
    SetFilterValue,
    SetMapValue,
    StrValue,
    TupleValue,
    UnionValue,
    to_python,
)
from wunderspec.lang import Expr
from wunderspec.machine import MachineState, MachineStateBase
from wunderspec.sym_context import SymbolicContext


class NativeActionContext:
    """Action definitions available while evaluating native ``Enabled`` nodes."""

    __slots__ = ("proto_state", "actions", "_body_cache")

    def __init__(
        self,
        proto_state: MachineState,
        actions: Mapping[str, Callable[..., Any]],
    ) -> None:
        self.proto_state = proto_state
        self.actions = actions
        self._body_cache: dict[tuple[str, tuple[Node, ...]], ActionNode] = {}

    def resolve(self, node: ActionCallNode) -> ActionNode:
        key = (node.action_name, node.args)
        cached = self._body_cache.get(key)
        if cached is not None:
            return cached
        try:
            action_func = self.actions[node.action_name]
        except KeyError:
            raise ValueError(
                f"Cannot evaluate Enabled({node.action_name}): no native action "
                f"definition is registered for '{node.action_name}'"
            ) from None
        ctx = SymbolicContext(copy(self.proto_state), inline_all=True)
        action_func(ctx, *(Expr(arg) for arg in node.args))
        body = ctx.build()
        self._body_cache[key] = body
        return body


_native_action_context: ContextVar[NativeActionContext | None] = ContextVar(
    "native_action_context", default=None
)


@contextmanager
def native_action_context(
    proto_state: MachineState | None,
    actions: Mapping[str, Callable[..., Any]] | None,
) -> Iterator[None]:
    """Provide named action definitions for native ``Enabled`` evaluation."""
    if proto_state is None or actions is None:
        yield
        return
    token = _native_action_context.set(NativeActionContext(proto_state, actions))
    try:
        yield
    finally:
        _native_action_context.reset(token)


@singledispatch
def _value_impl(e: Expr | Node | MachineStateBase, env: "Env" = pmap()) -> IValue:
    """Internal singledispatch implementation for value().

    Do not call directly - use value() instead.
    """
    raise NotImplementedError(f"Evaluation not implemented for {type(e).__name__}")


class Env(Protocol):
    """Internal protocol for interpreter environments."""

    def __contains__(self, key: object) -> bool: ...

    def __getitem__(self, key: str) -> IValue: ...

    def set(self, key: str, value: IValue) -> "Env": ...


# Type alias for value handler functions
_ValueHandler = Callable[[Expr | Node | MachineStateBase, Env], IValue]


# Dict-based cache for dispatch lookup (avoids lru_cache key generation overhead)
_value_dispatch_cache: dict[type, _ValueHandler] = {}

_CONST_SET_ENUM_CACHE_SIZE = 4096
_const_set_enum_cache: OrderedDict[int, tuple[SetEnumNode, EnumeratedSetValue]] = (
    OrderedDict()
)

# Type-specific caches for LitNode evaluation.  Keyed by the raw Python value
# (int/bool/str/Enum) so lookups use native hash/eq and never pay LitNode's
# frozen-dataclass overhead.  The caches stay small (one entry per distinct
# literal value in the spec).
_lit_bool_cache: dict[bool, BoolValue] = {}
_lit_int_cache: dict[int, IntValue] = {}
_lit_str_cache: dict[str, StrValue] = {}
_lit_enum_cache: dict["Enum", EnumValue] = {}

# Cache for IntervalSetValue by (lower, upper) integer pair.  For a fixed
# parameter like N the same bounds are queried millions of times.
_interval_cache: dict[tuple[int, int], "IntervalSetValue"] = {}


class _BindingEnv:
    """Small mapping overlay used in quantifier/set-binding evaluation.

    Keeps base env unchanged and stores binding variables in a compact dict,
    which is cheaper than repeatedly calling ``pmap.set`` in tight loops.
    """

    __slots__ = ("_base", "_bindings")

    def __init__(self, base: Env, bindings: dict[str, IValue]) -> None:
        self._base = base
        self._bindings = bindings

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key in self._bindings or key in self._base

    def __getitem__(self, key: str) -> IValue:
        try:
            return self._bindings[key]
        except KeyError:
            return self._base[key]

    def set(self, key: str, value: IValue) -> "_BindingEnv":
        next_bindings = dict(self._bindings)
        next_bindings[key] = value
        return _BindingEnv(self._base, next_bindings)


class _ActionEnv:
    """Environment overlay used while evaluating action enabledness paths."""

    __slots__ = ("_base", "_bindings")

    def __init__(self, base: Env, bindings: dict[str, IValue] | None = None) -> None:
        self._base = base
        self._bindings = bindings if bindings is not None else {}

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key in self._bindings or key in self._base

    def __getitem__(self, key: str) -> IValue:
        try:
            return self._bindings[key]
        except KeyError:
            return self._base[key]

    def set(self, key: str, value: IValue) -> "_ActionEnv":
        next_bindings = dict(self._bindings)
        next_bindings[key] = value
        return _ActionEnv(self._base, next_bindings)

    def remove_local(self, key: str) -> "_ActionEnv":
        next_bindings = dict(self._bindings)
        next_bindings.pop(key, None)
        return _ActionEnv(self._base, next_bindings)


def _binding_key(var: VarNode) -> str:
    """Resolve binding identity key for a variable node."""
    unique_name = getattr(var, "unique_name", None)
    return unique_name if unique_name is not None else var.name


def _value_dispatch(tp: type) -> _ValueHandler:
    """Cached dispatch lookup for value()."""
    try:
        return _value_dispatch_cache[tp]
    except KeyError:
        handler = _value_impl.dispatch(tp)
        _value_dispatch_cache[tp] = handler  # type: ignore[assignment]
        return handler  # type: ignore[return-value]


def _value_dispatch_clear_cache() -> None:
    """Clear the dispatch cache (needed after registering new handlers)."""
    _value_dispatch_cache.clear()
    _const_set_enum_cache.clear()
    _lit_bool_cache.clear()
    _lit_int_cache.clear()
    _lit_str_cache.clear()
    _lit_enum_cache.clear()
    _interval_cache.clear()


def _const_set_enum_values(e: SetEnumNode, env: Env) -> tuple[IValue, ...] | None:
    """Return interpreted values for cacheable constant SetEnumNode, else None."""
    vals: list[IValue] = []
    for elem in e.elements:
        if isinstance(elem, IValueNode):
            vals.append(elem._value)
            continue
        if isinstance(elem, LitNode):
            vals.append(_value_lit(elem, env))
            continue
        return None
    return tuple(vals)


def _get_cached_const_set_enum(e: SetEnumNode) -> EnumeratedSetValue | None:
    cached = _const_set_enum_cache.get(id(e))
    if cached is None:
        return None
    cached_node, cached_value = cached
    if cached_node is not e:
        # Defensive against id reuse.
        _const_set_enum_cache.pop(id(e), None)
        return None
    _const_set_enum_cache.move_to_end(id(e))
    return cached_value


def _put_cached_const_set_enum(e: SetEnumNode, val: EnumeratedSetValue) -> None:
    node_id = id(e)
    _const_set_enum_cache[node_id] = (e, val)
    _const_set_enum_cache.move_to_end(node_id)
    if len(_const_set_enum_cache) > _CONST_SET_ENUM_CACHE_SIZE:
        _const_set_enum_cache.popitem(last=False)


# since to_python is also singledispatch, we compose to_python and value
# for Expr and Node
@to_python.register(Expr)  # type: ignore[attr-defined]
def _expr_to_python(e: Expr) -> Any:
    """Evaluate an expression directly to a Python value"""
    return to_python(value(e))


@to_python.register(Node)  # type: ignore[attr-defined]
def _node_to_python(e: Node) -> Any:
    """Evaluate an AST node directly to a Python value"""
    return to_python(value(e))


def value(e: Expr | Node | MachineStateBase, env: Env = pmap()) -> IValue:
    """Interpret an expression.

    This function uses singledispatch to dispatch on AST node types, making
    it extensible. Users can register custom handlers for new node types:

        ```python
        @value.register(MyCustomNode)
        def _(e: MyCustomNode, env: Env = pmap()) -> IValue:
            ...
        ```

    Note: After registering a new handler at runtime, call value.clear_cache()
    to ensure the new handler is used.

    Args:
        e: The expression to evaluate (can be Expr wrapper or raw Node)
        env: Optional environment mapping variable names to their values (IValue)
             Defaults to empty environment if not provided

    Returns:
        The interpreted value
    """
    # NOTE: env=pmap() is safe here because pyrsistent pmap is immutable.
    # If you think that introducing a value cache here would be beneficial,
    # read the comments in PR #134. It slows things down significantly.
    if not is_locating_errors():
        # Fast/default path: preserve the interpreter's native exceptions.
        return _value_dispatch(type(e))(e, env)  # type: ignore[arg-type]
    try:
        return _value_dispatch(type(e))(e, env)  # type: ignore[arg-type]
    except EvaluationError as ev:
        # Already located by an inner frame; climb until a span is found so that
        # the reported location is the innermost subexpression that has one.
        if ev.span is None:
            ev.span = getattr(e, "source_span", None)
        raise
    except BaseException as exc:
        # Control-flow exceptions (e.g. AssumptionViolated, which drives the
        # exploration retry loops) and interrupts must pass through untouched.
        if _is_control_flow(exc):
            raise
        raise EvaluationError(exc, getattr(e, "source_span", None)) from exc


# Expose registration interface for extensibility
value.register = _value_impl.register  # type: ignore[attr-defined]
value.dispatch = _value_impl.dispatch  # type: ignore[attr-defined]
value.registry = _value_impl.registry  # type: ignore[attr-defined]
value.clear_cache = _value_dispatch_clear_cache  # type: ignore[attr-defined]


@_value_impl.register(Expr)
def _value_expr(e: Expr, env: Env = pmap()) -> IValue:
    """Unwrap Expr and dispatch to the appropriate node handler."""
    return value(e._node, env)


@_value_impl.register(LitNode)
def _value_lit(e: LitNode, env: Env = pmap()) -> IValue:
    """Evaluate literal nodes.

    Uses per-type caches keyed by the raw Python value so that lookup uses
    native int/str/bool/Enum hash rather than the heavier LitNode hash.
    """
    match e.sort:
        case BoolSort():
            v = e.value
            assert isinstance(v, bool)
            cached = _lit_bool_cache.get(v)
            if cached is not None:
                return cached
            bv = BoolValue(v)
            _lit_bool_cache[v] = bv
            return bv
        case IntSort():
            iv = e.value
            assert isinstance(iv, int) and not isinstance(iv, bool)
            ic = _lit_int_cache.get(iv)
            if ic is not None:
                return ic
            iv_obj = IntValue(iv)
            _lit_int_cache[iv] = iv_obj
            return iv_obj
        case StrSort():
            sv = e.value
            assert isinstance(sv, str)
            sc = _lit_str_cache.get(sv)
            if sc is not None:
                return sc
            sv_obj = StrValue(sv)
            _lit_str_cache[sv] = sv_obj
            return sv_obj
        case EnumSort():
            ev = e.value
            assert isinstance(ev, Enum)
            ec = _lit_enum_cache.get(ev)  # type: ignore[arg-type]
            if ec is not None:
                return ec
            ev_obj = EnumValue(ev)
            _lit_enum_cache[ev] = ev_obj  # type: ignore[index]
            return ev_obj
        case _:
            raise ValueError(f"Unknown literal sort: {e.sort}")


@_value_impl.register(IValueNode)
def _value_ivalue_node(e: IValueNode, env: Env = pmap()) -> IValue:
    """Evaluate IValueNode - return the stored value directly."""
    return e._value


@_value_impl.register(LetNode)
def _value_let(e: LetNode, env: Env = pmap()) -> IValue:
    """Evaluate let binding nodes."""
    # Evaluate the value expression
    val = value(e.value, env)
    if e.name in env:
        raise ValueError(f"Attempting to replace {e.name} with let-binding")
    # Extend the environment with the binding
    new_env = _BindingEnv(env, {e.name: val})
    # Evaluate the body in the extended environment
    return value(e.body, new_env)


@_value_impl.register(ExprCallNode)
def _value_expr_call(e: ExprCallNode, env: Env = pmap()) -> IValue:
    """Evaluate a non-inline @expr call by substituting actual args into the body."""
    new_env: Env = env
    for param_name, arg_node in zip(e.param_names, e.args):
        arg_val = value(arg_node, env)
        new_env = _BindingEnv(new_env, {param_name: arg_val})
    return value(e.body, new_env)


@_value_impl.register(VarNode)
def _value_var(e: VarNode, env: Env = pmap()) -> IValue:
    """Evaluate variable nodes - look up in environment."""
    name = _binding_key(e)
    try:
        return env[name]
    except KeyError:
        if name != e.name:
            try:
                return env[e.name]
            except KeyError:
                pass
    raise ValueError(f"Cannot evaluate unbound variable: {e.name}")


def _as_bool(val: IValue, context: str) -> bool:
    if type(val) is not BoolValue:
        raise ValueError(
            f"Expected BoolValue while evaluating {context}, got {type(val)}"
        )
    return val.value


def _var_name(node: Node, context: str) -> str:
    if not isinstance(node, VarNode):
        raise ValueError(f"{context} requires a VarNode, got {type(node).__name__}")
    return _binding_key(node)


def _iter_enabled_envs(node: ActionNode, env: _ActionEnv) -> Iterator[_ActionEnv]:
    match node:
        case AssumeNode():
            if _as_bool(value(node.condition, env), "Enabled assumption"):
                yield env
            return

        case AssignNode():
            name = _var_name(node.var, "Enabled assignment")
            yield env.set(name, value(node.expr, env))
            return

        case ActionAndNode():
            current_envs: list[_ActionEnv] = [env]
            for action in node.actions:
                next_envs: list[_ActionEnv] = []
                for current in current_envs:
                    next_envs.extend(_iter_enabled_envs(action, current))
                if not next_envs:
                    return
                current_envs = next_envs
            yield from current_envs
            return

        case ActionChoiceNode():
            for action in node.actions:
                yield from _iter_enabled_envs(action, env)
            return

        case NondetChoiceNode():
            name = _var_name(node.var, "Enabled nondeterministic choice")
            if name in env:
                raise RuntimeError(f"Name collision: {name} is declared twice")
            base_value = value(node.base_set, env)
            if not isinstance(base_value, AbstractSetValue):
                raise ValueError(
                    f"Expected AbstractSetValue in Enabled one_of, got {type(base_value)}"
                )
            try:
                iterator = iter(base_value)
                for elem in iterator:
                    bound_env = env.set(name, elem)
                    for body_env in _iter_enabled_envs(
                        cast(ActionNode, node.body), bound_env
                    ):
                        yield body_env.remove_local(name)
            except RuntimeError as exc:
                raise ValueError(
                    "Cannot evaluate Enabled exactly over a non-enumerable "
                    f"one_of domain: {exc}"
                ) from exc
            return

        case ActionLetNode():
            name = node.name
            if name in env:
                raise RuntimeError(f"Name collision: {name} is declared twice")
            bound_env = env.set(name, value(node.value, env))
            for body_env in _iter_enabled_envs(node.body, bound_env):
                yield body_env.remove_local(name)
            return

        case ActionCallNode():
            if node.placeholder_body:
                native_ctx = _native_action_context.get()
                if native_ctx is None:
                    raise ValueError(
                        f"Cannot evaluate Enabled({node.action_name}): no native "
                        "action context is active"
                    )
                yield from _iter_enabled_envs(native_ctx.resolve(node), env)
            else:
                yield from _iter_enabled_envs(node.body, env)
            return

        case _:
            raise ValueError(
                f"Unsupported action node in Enabled: {type(node).__name__}"
            )


def _is_enabled(node: ActionNode, env: Env) -> bool:
    action_env = env if isinstance(env, _ActionEnv) else _ActionEnv(env)
    return next(_iter_enabled_envs(node, action_env), None) is not None


@_value_impl.register(ToTemporalNode)
def _value_to_temporal(e: ToTemporalNode, env: Env = pmap()) -> IValue:
    """Evaluate a lifted state predicate as a Boolean value."""
    return value(e.bool_formula, env)


@_value_impl.register(EnabledNode)
def _value_enabled(e: EnabledNode, env: Env = pmap()) -> IValue:
    """Evaluate ``Enabled(action)`` as exact current-state action feasibility."""
    if not isinstance(e.action, ActionNode):
        raise ValueError(
            f"Enabled requires an action node, got {type(e.action).__name__}"
        )
    return BoolValue(_is_enabled(e.action, env))


@_value_impl.register(AlwaysNode)
def _value_always(e: AlwaysNode, env: Env = pmap()) -> IValue:
    raise NotImplementedError("Native evaluation of Always(...) is not implemented")


@_value_impl.register(EventuallyNode)
def _value_eventually(e: EventuallyNode, env: Env = pmap()) -> IValue:
    raise NotImplementedError("Native evaluation of Eventually(...) is not implemented")


@_value_impl.register(FairnessNode)
def _value_fairness(e: FairnessNode, env: Env = pmap()) -> IValue:
    raise NotImplementedError("Native evaluation of fairness is not implemented")


@_value_impl.register(RecordCtorNode)
def _value_record_ctor(e: RecordCtorNode, env: Env = pmap()) -> IValue:
    """Evaluate record constructor nodes."""
    field_values = {name: value(node, env) for name, node in e.fields}
    return RecordValue(**field_values)


@_value_impl.register(RecordUpdateNode)
def _value_record_update(e: RecordUpdateNode, env: Env = pmap()) -> IValue:
    """Evaluate record update nodes."""
    base_val = value(e.base_record, env)
    if not isinstance(base_val, RecordValue):
        raise ValueError(f"Expected RecordValue, got {type(base_val)}")

    update_values = {name: value(node, env) for name, node in e.updates}

    # Create new record with updated fields
    new_fields = dict(base_val._field_dict)
    new_fields.update(update_values)
    return RecordValue(**new_fields)


@_value_impl.register(RecordGetNode)
def _value_record_get(e: RecordGetNode, env: Env = pmap()) -> IValue:
    """Evaluate record field access nodes."""
    record_val = value(e.record_node, env)
    if not isinstance(record_val, RecordValue):
        raise ValueError(f"Expected RecordValue, got {type(record_val)}")

    if e.field_name not in record_val:
        raise ValueError(f"Field '{e.field_name}' not found in record")
    return record_val[e.field_name]


@_value_impl.register(MachineStateBase)
def _value_machine_state(e: MachineStateBase, env: Env = pmap()) -> IValue:
    """Evaluate a machine state to a RecordValue.

    Converts each field (params and vars) from Expr to IValue.
    """
    uninitialized = e.__dict__.get("_uninitialized", frozenset())
    if uninitialized:
        cls_name = type(e).__name__
        fields = ", ".join(sorted(uninitialized))
        raise ValueError(
            f"Cannot evaluate partially initialized {cls_name}: "
            f"fields {{{fields}}} have not been assigned"
        )
    field_values = {name: value(expr, env) for name, expr in e._asdict().items()}
    return RecordValue(**field_values)


@_value_impl.register(TupleCtorNode)
def _value_tuple_ctor(e: TupleCtorNode, env: Env = pmap()) -> IValue:
    """Evaluate tuple constructor nodes."""
    elem_values = tuple(value(node, env) for node in e.elements)
    return TupleValue(*elem_values)


@_value_impl.register(TupleUpdateNode)
def _value_tuple_update(e: TupleUpdateNode, env: Env = pmap()) -> IValue:
    """Evaluate tuple update nodes."""
    base_val = value(e.base_tuple, env)
    if not isinstance(base_val, TupleValue):
        raise ValueError(f"Expected TupleValue, got {type(base_val)}")

    new_val = value(e.new_value, env)

    # Create new tuple with updated element
    new_elements = list(base_val._elements)
    new_elements[e.index] = new_val
    return TupleValue(*new_elements)


@_value_impl.register(TupleGetNode)
def _value_tuple_get(e: TupleGetNode, env: Env = pmap()) -> IValue:
    """Evaluate tuple element access nodes."""
    tuple_val = value(e.tuple_node, env)
    if not isinstance(tuple_val, TupleValue):
        raise ValueError(f"Expected TupleValue, got {type(tuple_val)}")

    if e.index not in tuple_val:
        raise ValueError(
            f"Index {e.index} out of bounds for tuple of length {len(tuple_val)}"
        )
    return tuple_val[e.index]


@_value_impl.register(MapSetNode)
def _value_map_set(e: MapSetNode, env: Env = pmap()) -> IValue:
    """Evaluate map update nodes (insert or replace)."""
    base_val = value(e.base_map, env)
    if type(base_val) is not MapValue:
        raise ValueError(f"Expected MapValue, got {type(base_val)}")

    key_val = value(e.update_key, env)
    val_val = value(e.update_value, env)

    # If replace_only is True, only update if key exists
    if e.replace_only and key_val not in base_val:
        return base_val

    # Use pmap.set() directly — avoids pmap→dict→pmap double conversion
    return MapValue._from_pmap(
        base_val._mappings.set(key_val, val_val),
        key_sort=e.key_sort,
        value_sort=e.value_sort,
    )


@_value_impl.register(MapEnumNode)
def _value_map_enum(e: MapEnumNode, env: Env = pmap()) -> IValue:
    """Evaluate map enumeration constructor nodes."""
    evaluated_mappings = {}
    for key_node, value_node in e.mappings.items():
        key_val = value(key_node, env)
        val_val = value(value_node, env)
        evaluated_mappings[key_val] = val_val
    return MapValue(evaluated_mappings, key_sort=e.key_sort, value_sort=e.value_sort)


@_value_impl.register(MapLambdaNode)
def _value_map_lambda(e: MapLambdaNode, env: Env = pmap()) -> IValue:
    """Evaluate map lambda constructor nodes."""
    base_val = value(e.base_set, env)
    if not isinstance(base_val, AbstractSetValue):
        raise ValueError(f"Expected AbstractSetValue, got {type(base_val)}")

    evaluated_mappings = {}
    bindings: dict[str, IValue] = {}
    bind_key = _binding_key(e.var)
    for elem in base_val:
        bindings[bind_key] = elem
        mapped_val = value(e.mapper, _BindingEnv(env, bindings))
        evaluated_mappings[elem] = mapped_val

    return MapValue(evaluated_mappings, key_sort=e.key_sort, value_sort=e.value_sort)


@_value_impl.register(MapGetNode)
def _value_map_get(e: MapGetNode, env: Env = pmap()) -> IValue:
    """Evaluate map element access nodes."""
    map_val = value(e.map_node, env)
    if type(map_val) is not MapValue:
        raise ValueError(f"Expected MapValue, got {type(map_val)}")

    key_val = value(e.key, env)

    # Single pmap lookup via try/except — avoids the redundant __contains__ walk
    try:
        return map_val._mappings[key_val]
    except KeyError:
        raise ValueError(f"Key {key_val} not found in map")


@_value_impl.register(MapKeysNode)
def _value_map_keys(e: MapKeysNode, env: Env = pmap()) -> IValue:
    """Evaluate map keys nodes."""
    map_val = value(e.map_node, env)
    if not isinstance(map_val, MapValue):
        raise ValueError(f"Expected MapValue, got {type(map_val)}")

    keys_pset = pset(map_val.mappings.keys())
    return EnumeratedSetValue._from_material_set(keys_pset)


@_value_impl.register(AlgebraNode)
def _value_algebra(e: AlgebraNode, env: Env = pmap()) -> IValue:
    """Evaluate algebra nodes - dispatch based on operator type."""
    handler = _OP_DISPATCH.get(e.op)
    if handler is None:
        raise NotImplementedError(f"Unknown operator type: {type(e.op)}")
    return handler(e, env)


@_value_impl.register(InNode)
def _value_in(e: InNode, env: Env = pmap()) -> IValue:
    """Evaluate set membership nodes."""
    elem_val = value(e.elem, env)
    set_val = value(e.set_node, env)
    if type(set_val) is not EnumeratedSetValue and not isinstance(
        set_val, AbstractSetValue
    ):
        raise ValueError(
            f"Expected AbstractSetValue for IN operation, got {type(set_val)}"
        )
    elem_materialized = elem_val.materialize()
    return BoolValue(elem_materialized in set_val)


@_value_impl.register(IteNode)
def _value_ite(e: IteNode, env: Env = pmap()) -> IValue:
    """Evaluate if-then-else nodes."""
    cond_val = value(e.condition, env)
    if not isinstance(cond_val, BoolValue):
        raise ValueError(f"Expected BoolValue for condition, got {type(cond_val)}")
    if cond_val.value:
        return value(e.then_node, env)
    else:
        return value(e.else_node, env)


@_value_impl.register(SetEnumNode)
def _value_set_enum(e: SetEnumNode, env: Env = pmap()) -> IValue:
    """Evaluate set enumeration nodes.

    Fast path: avoid building an intermediate Python list and varargs tuple
    for large set literals.
    """
    cached = _get_cached_const_set_enum(e)
    if cached is not None:
        return cached

    const_vals = _const_set_enum_values(e, env)
    if const_vals is not None:
        const_set = EnumeratedSetValue._from_value_iterable(
            const_vals,
            elem_sort=e.elem_sort,
        )
        _put_cached_const_set_enum(e, const_set)
        return const_set

    return EnumeratedSetValue._from_value_iterable(
        (value(elem, env) for elem in e.elements),
        elem_sort=e.elem_sort,
    )


@_value_impl.register(SetIntOrNatNode)
def _value_set_int_or_nat(e: SetIntOrNatNode, env: Env = pmap()) -> IValue:
    """Evaluate infinite integer set nodes (Ints, UnsignedInts)."""
    return InfIntSetValue(e.is_signed)


def _extract_int_bound(node: Node, env: Env) -> int:
    """Fast extraction of an integer bound from a node.

    Short-circuits value() dispatch for the two common cases:
      - LitNode  → read .value directly (no dispatch, no IValue allocation)
      - IValueNode → read ._value.value directly
    Falls back to full value() evaluation for other node types.
    """
    if type(node) is LitNode:
        v = node.value
        if type(v) is int and not type(v) is bool:
            return v
        raise ValueError(f"Expected int LitNode for interval bound, got {type(v)}")
    if type(node) is IValueNode:
        iv = node._value
        if type(iv) is IntValue:
            return iv.value
        raise ValueError(f"Expected IntValue in IValueNode bound, got {type(iv)}")
    result = value(node, env)
    if type(result) is not IntValue:
        raise ValueError(f"Expected IntValue for interval bound, got {type(result)}")
    return result.value


@_value_impl.register(IntervalNode)
def _value_interval(e: IntervalNode, env: Env = pmap()) -> IValue:
    """Evaluate interval nodes.

    Avoids full value() dispatch for LitNode/IValueNode bounds (the common
    case) and caches the resulting IntervalSetValue by (lower, upper) pair.
    """
    lv = _extract_int_bound(e.lower, env)
    uv = _extract_int_bound(e.upper, env)
    key = (lv, uv)
    cached = _interval_cache.get(key)
    if cached is not None:
        return cached
    result = IntervalSetValue(lv, uv)
    _interval_cache[key] = result
    return result


def _eval_bindings_product(bindings: list, env: Env) -> Iterator[Env]:
    """Evaluate all binding domains and yield an extended env for each combination.

    Each binding is ``(VarNode, domain_node)``. Domains are evaluated and their
    Cartesian product is iterated, extending *env* with each variable bound to
    the corresponding element.
    """
    domain_vals: list[tuple[VarNode, tuple[IValue, ...]]] = []
    for var, domain in bindings:
        dval = value(domain, env)
        if not isinstance(dval, AbstractSetValue):
            raise ValueError(f"Expected AbstractSetValue, got {type(dval)}")
        domain_vals.append((var, tuple(dval)))

    if not domain_vals:
        yield env
        return

    active_bindings: dict[str, IValue] = {}

    def _recurse(i: int) -> Iterator[Env]:
        if i >= len(domain_vals):
            yield _BindingEnv(env, dict(active_bindings))
            return
        var, elems = domain_vals[i]
        key = _binding_key(var)
        for elem in elems:
            active_bindings[key] = elem
            yield from _recurse(i + 1)
            del active_bindings[key]

    yield from _recurse(0)


@_value_impl.register(SetFilterNode)
def _value_set_filter(e: SetFilterNode, env: Env = pmap()) -> IValue:
    """Evaluate set filter nodes lazily, supporting multi-binding."""
    if len(e.bindings) == 1:
        var, domain = e.bindings[0]
        base_val = value(domain, env)
        if not isinstance(base_val, AbstractSetValue):
            raise ValueError(f"Expected AbstractSetValue, got {type(base_val)}")

        def predicate_fn(elem: IValue) -> bool:
            elem_env = _BindingEnv(env, {_binding_key(var): elem})
            pred_val = value(e.body, elem_env)
            if type(pred_val) is not BoolValue:
                raise ValueError(
                    f"Expected BoolValue from predicate, got {type(pred_val)}"
                )
            return pred_val.value

        return SetFilterValue(base_val, predicate_fn, e.elem_sort)

    # Multi-binding: materialize by filtering all combinations
    result_elems = []
    for combo_env in _eval_bindings_product(e.bindings, env):
        pred_val = value(e.body, combo_env)
        if not isinstance(pred_val, BoolValue):
            raise ValueError(f"Expected BoolValue from predicate, got {type(pred_val)}")
        if pred_val.value:
            # Result is the innermost binding's element
            result_elems.append(combo_env[_binding_key(e.bindings[-1][0])])

    return EnumeratedSetValue(*result_elems, elem_sort=e.elem_sort)


@_value_impl.register(SetMapNode)
def _value_set_map(e: SetMapNode, env: Env = pmap()) -> IValue:
    """Evaluate set map nodes lazily, supporting multi-binding."""
    if len(e.bindings) == 1:
        var, domain = e.bindings[0]
        base_val = value(domain, env)
        if not isinstance(base_val, AbstractSetValue):
            raise ValueError(f"Expected AbstractSetValue, got {type(base_val)}")

        def mapper_fn(elem: IValue) -> IValue:
            elem_env = _BindingEnv(env, {_binding_key(var): elem})
            return value(e.body, elem_env)

        return SetMapValue(base_val, mapper_fn, e.elem_sort)

    # Multi-binding: iterate over product of all domains, produce flat set
    result_elems = [
        value(e.body, combo_env)
        for combo_env in _eval_bindings_product(e.bindings, env)
    ]
    return EnumeratedSetValue(*result_elems, elem_sort=e.elem_sort)


@_value_impl.register(SetQuantNode)
def _value_set_quant(e: SetQuantNode, env: Env = pmap()) -> IValue:
    """Evaluate set quantification nodes (forall, exists), supporting multi-binding."""
    if len(e.bindings) == 1:
        var, domain = e.bindings[0]
        base_val = value(domain, env)
        if type(base_val) is not EnumeratedSetValue and not isinstance(
            base_val, AbstractSetValue
        ):
            raise ValueError(f"Expected AbstractSetValue, got {type(base_val)}")

        match e.quant:
            case QuantOp.FORALL:
                for elem in base_val:
                    elem_env = _BindingEnv(env, {_binding_key(var): elem})
                    pred_val = value(e.body, elem_env)
                    if type(pred_val) is not BoolValue:
                        raise ValueError(
                            f"Expected BoolValue from predicate, got {type(pred_val)}"
                        )
                    if not pred_val.value:
                        return BoolValue(False)
                return BoolValue(True)

            case QuantOp.EXISTS:
                for elem in base_val:
                    elem_env = _BindingEnv(env, {_binding_key(var): elem})
                    pred_val = value(e.body, elem_env)
                    if type(pred_val) is not BoolValue:
                        raise ValueError(
                            f"Expected BoolValue from predicate, got {type(pred_val)}"
                        )
                    if pred_val.value:
                        return BoolValue(True)
                return BoolValue(False)

            case _:
                raise ValueError(f"Unknown quantifier: {e.quant}")

    # Multi-binding: iterate over product of all domains
    match e.quant:
        case QuantOp.FORALL:
            for combo_env in _eval_bindings_product(e.bindings, env):
                pred_val = value(e.body, combo_env)
                if type(pred_val) is not BoolValue:
                    raise ValueError(
                        f"Expected BoolValue from predicate, got {type(pred_val)}"
                    )
                if not pred_val.value:
                    return BoolValue(False)
            return BoolValue(True)

        case QuantOp.EXISTS:
            for combo_env in _eval_bindings_product(e.bindings, env):
                pred_val = value(e.body, combo_env)
                if type(pred_val) is not BoolValue:
                    raise ValueError(
                        f"Expected BoolValue from predicate, got {type(pred_val)}"
                    )
                if pred_val.value:
                    return BoolValue(True)
            return BoolValue(False)

        case _:
            raise ValueError(f"Unknown quantifier: {e.quant}")


@_value_impl.register(SetReduceNode)
def _value_set_reduce(e: SetReduceNode, env: Env = pmap()) -> IValue:
    """Evaluate set reduce nodes."""
    base_val = value(e.base_set, env)
    if not isinstance(base_val, AbstractSetValue):
        raise ValueError(f"Expected AbstractSetValue, got {type(base_val)}")

    acc_val = value(e.initial, env)
    acc_key = _binding_key(e.acc_var)
    elem_key = _binding_key(e.elem_var)

    for elem in base_val:
        reduce_env = env.set(acc_key, acc_val).set(elem_key, elem)
        acc_val = value(e.fun, reduce_env)

    return acc_val


@_value_impl.register(ChooseNode)
def _value_choose(e: ChooseNode, env: Env = pmap()) -> IValue:
    """Evaluate choose node: pick the minimum element (by fingerprint ordering)
    satisfying the predicate."""
    base_val = value(e.base_set, env)
    if not isinstance(base_val, AbstractSetValue):
        raise ValueError(f"Expected AbstractSetValue, got {type(base_val)}")

    min_elem: IValue | None = None
    min_fp: int | None = None

    for elem in base_val:
        elem_env = env.set(_binding_key(e.var), elem)
        pred_val = value(e.predicate, elem_env)
        if not isinstance(pred_val, BoolValue):
            raise ValueError(f"Expected BoolValue from predicate, got {type(pred_val)}")
        if pred_val.value:
            elem_fp = elem.fingerprint()

            if min_fp is None or elem_fp < min_fp:
                min_fp = elem_fp
                min_elem = elem

    if min_elem is None:
        raise ValueError("CHOOSE: no element satisfies the predicate")

    return min_elem


@_value_impl.register(AllSubsetsNode)
def _value_all_subsets(e: AllSubsetsNode, env: Env = pmap()) -> IValue:
    """Evaluate AllSubsets node: return AllSubsetsValue."""
    base_val = value(e.base_set, env)
    if not isinstance(base_val, AbstractSetValue):
        raise ValueError(f"Expected AbstractSetValue, got {type(base_val)}")
    elements = tuple(base_val)
    # Extract elem_sort from the AST node so empty sets retain sort info
    elem_sort = (
        e.base_set.sort.elem_sort if isinstance(e.base_set.sort, SetSort) else None
    )
    return AllSubsetsValue(elements, elem_sort=elem_sort)


@_value_impl.register(AllMapsNode)
def _value_all_maps(e: AllMapsNode, env: Env = pmap()) -> IValue:
    """Evaluate AllMaps node: return AllMapsValue."""
    key_val = value(e.key_set, env)
    value_val = value(e.value_set, env)
    if not isinstance(key_val, AbstractSetValue):
        raise ValueError(f"Expected AbstractSetValue for key_set, got {type(key_val)}")
    if not isinstance(value_val, AbstractSetValue):
        raise ValueError(
            f"Expected AbstractSetValue for value_set, got {type(value_val)}"
        )
    keys = tuple(key_val)
    values = tuple(value_val)
    return AllMapsValue(keys, values)


@_value_impl.register(AllTuplesNode)
def _value_all_tuples(e: AllTuplesNode, env: Env = pmap()) -> IValue:
    """Evaluate AllTuples node: return AllTuplesValue."""
    dimension_elements = []
    for s in e.sets:
        s_val = value(s, env)
        if not isinstance(s_val, AbstractSetValue):
            raise ValueError(f"Expected AbstractSetValue, got {type(s_val)}")
        dimension_elements.append(tuple(s_val))
    return AllTuplesValue(tuple(dimension_elements))


@_value_impl.register(AllRecordsNode)
def _value_all_records(e: AllRecordsNode, env: Env = pmap()) -> IValue:
    """Evaluate AllRecords node: return AllRecordsValue."""
    field_names = tuple(sorted(e.field_sets.keys()))
    field_elements = []
    for name in field_names:
        s_val = value(e.field_sets[name], env)
        if not isinstance(s_val, AbstractSetValue):
            raise ValueError(
                f"Expected AbstractSetValue for field {name}, got {type(s_val)}"
            )
        field_elements.append(tuple(s_val))
    return AllRecordsValue(field_names, tuple(field_elements))


@_value_impl.register(ListEnumNode)
def _value_list_enum(e: ListEnumNode, env: Env = pmap()) -> IValue:
    """Evaluate list enumeration nodes."""
    return ListValue([value(elem, env) for elem in e.elements], elem_sort=e.elem_sort)


@_value_impl.register(ListRangeNode)
def _value_list_range(e: ListRangeNode, env: Env = pmap()) -> IValue:
    """Evaluate list range nodes."""
    lower_val = value(e.lower, env)
    upper_val = value(e.upper, env)
    if type(lower_val) is not IntValue:
        raise ValueError(f"Expected IntValue for lower bound, got {type(lower_val)}")
    if type(upper_val) is not IntValue:
        raise ValueError(f"Expected IntValue for upper bound, got {type(upper_val)}")
    elements = [IntValue(i) for i in range(lower_val.value, upper_val.value)]
    return ListValue(elements, elem_sort=e.elem_sort)


@_value_impl.register(ListGetNode)
def _value_list_get(e: ListGetNode, env: Env = pmap()) -> IValue:
    """Evaluate list element access nodes."""
    list_val = value(e.list_node, env)
    if not isinstance(list_val, ListValue):
        raise ValueError(f"Expected ListValue, got {type(list_val)}")
    index_val = value(e.index, env)
    if not isinstance(index_val, IntValue):
        raise ValueError(f"Expected IntValue for index, got {type(index_val)}")
    idx = index_val.value
    if idx < 0 or idx >= len(list_val):
        raise ValueError(
            f"Index {idx} out of bounds for list of length {len(list_val)}"
        )
    return list_val[idx]


@_value_impl.register(ListUpdateNode)
def _value_list_update(e: ListUpdateNode, env: Env = pmap()) -> IValue:
    """Evaluate list update nodes."""
    base_val = value(e.base_list, env)
    if not isinstance(base_val, ListValue):
        raise ValueError(f"Expected ListValue, got {type(base_val)}")
    index_val = value(e.index, env)
    if not isinstance(index_val, IntValue):
        raise ValueError(f"Expected IntValue for index, got {type(index_val)}")
    new_val = value(e.new_value, env)
    idx = index_val.value
    if idx < 0 or idx >= len(base_val):
        raise ValueError(
            f"Index {idx} out of bounds for list of length {len(base_val)}"
        )
    new_vec = base_val.elements.set(idx, new_val)
    return ListValue._from_pvector(new_vec, elem_sort=e.elem_sort)


@_value_impl.register(ListSliceNode)
def _value_list_slice(e: ListSliceNode, env: Env = pmap()) -> IValue:
    """Evaluate list slice nodes."""
    base_val = value(e.base_list, env)
    if not isinstance(base_val, ListValue):
        raise ValueError(f"Expected ListValue, got {type(base_val)}")
    start_val = value(e.start, env)
    end_val = value(e.end, env)
    if not isinstance(start_val, IntValue):
        raise ValueError(f"Expected IntValue for start, got {type(start_val)}")
    if not isinstance(end_val, IntValue):
        raise ValueError(f"Expected IntValue for end, got {type(end_val)}")
    sliced = list(base_val.elements)[start_val.value : end_val.value]
    return ListValue(sliced, elem_sort=e.elem_sort)


@_value_impl.register(ListFilterNode)
def _value_list_filter(e: ListFilterNode, env: Env = pmap()) -> IValue:
    """Evaluate list filter nodes."""
    base_val = value(e.base_list, env)
    if not isinstance(base_val, ListValue):
        raise ValueError(f"Expected ListValue, got {type(base_val)}")
    filtered = []
    bindings: dict[str, IValue] = {}
    bind_key = _binding_key(e.var)
    for elem in base_val.elements:
        bindings[bind_key] = elem
        pred_val = value(e.predicate, _BindingEnv(env, bindings))
        if type(pred_val) is not BoolValue:
            raise ValueError(f"Expected BoolValue from predicate, got {type(pred_val)}")
        if pred_val.value:
            filtered.append(elem)
    return ListValue(filtered, elem_sort=e.elem_sort)


@_value_impl.register(ListReduceNode)
def _value_list_reduce(e: ListReduceNode, env: Env = pmap()) -> IValue:
    """Evaluate list reduce nodes."""
    base_val = value(e.base_list, env)
    if not isinstance(base_val, ListValue):
        raise ValueError(f"Expected ListValue, got {type(base_val)}")
    acc_val = value(e.initial, env)
    bindings: dict[str, IValue] = {}
    acc_key = _binding_key(e.acc_var)
    elem_key = _binding_key(e.elem_var)
    for elem in base_val.elements:
        bindings[acc_key] = acc_val
        bindings[elem_key] = elem
        acc_val = value(e.fun, _BindingEnv(env, bindings))
    return acc_val


@_value_impl.register(ListKeysNode)
def _value_list_keys(e: ListKeysNode, env: Env = pmap()) -> IValue:
    """Evaluate list keys nodes."""
    list_val = value(e.list_node, env)
    if not isinstance(list_val, ListValue):
        raise ValueError(f"Expected ListValue, got {type(list_val)}")
    indices = [IntValue(i) for i in range(len(list_val))]
    return EnumeratedSetValue(*indices)


@_value_impl.register(UnionCtorNode)
def _value_union_ctor(e: UnionCtorNode, env: Env = pmap()) -> IValue:
    """Evaluate union constructor nodes."""
    payload_val = value(e.payload, env) if e.payload is not None else None
    return UnionValue(e.tag, payload_val, cast(UnionSort, e.sort))


@_value_impl.register(UnionGetTagNode)
def _value_union_get_tag(e: UnionGetTagNode, env: Env = pmap()) -> IValue:
    """Evaluate union tag access nodes."""
    union_val = value(e.union_node, env)
    if not isinstance(union_val, UnionValue):
        raise ValueError(f"Expected UnionValue, got {type(union_val)}")
    return StrValue(union_val.tag)


@_value_impl.register(UnionMatchNode)
def _value_union_match(e: UnionMatchNode, env: Env = pmap()) -> IValue:
    """Evaluate union match nodes."""
    union_val = value(e.union_node, env)
    if not isinstance(union_val, UnionValue):
        raise ValueError(f"Expected UnionValue, got {type(union_val)}")

    tag = union_val.tag
    if tag not in e.cases:
        raise ValueError(f"No case for tag '{tag}' in match expression")

    var_node, body_node = e.cases[tag]
    if var_node is not None:
        if union_val.payload is None:
            raise ValueError(
                f"Variant '{tag}' expected payload but UnionValue has none"
            )
        match_env = env.set(_binding_key(var_node), union_val.payload)
    else:
        match_env = env

    return value(body_node, match_env)


def eval_set_algebra_node(e: AlgebraNode, env: Env) -> IValue:
    """Evaluate a set algebra node (union, intersect, difference, cardinality, subseteq)"""
    # CARDINALITY is a unary operation
    if e.op == AlgebraOp.CARDINALITY:
        if len(e.args) != 1:
            raise ValueError(f"Cardinality expects 1 argument, got {len(e.args)}")
        set_val = value(e.args[0], env)
        if not isinstance(set_val, AbstractSetValue):
            raise ValueError(f"Expected AbstractSetValue, got {type(set_val)}")

        # Use __len__ if available (e.g., SetFilterValue), otherwise materialize
        if hasattr(set_val, "__len__"):
            return IntValue(len(set_val))
        else:
            materialized = set_val.materialize()
            assert isinstance(materialized, EnumeratedSetValue)
            return IntValue(len(materialized.material_set))

    # FLATTEN is a unary operation on a set of sets
    if e.op == AlgebraOp.FLATTEN:
        if len(e.args) != 1:
            raise ValueError(f"Flatten expects 1 argument, got {len(e.args)}")
        outer_val = value(e.args[0], env)
        if not isinstance(outer_val, AbstractSetValue):
            raise ValueError(f"Expected AbstractSetValue, got {type(outer_val)}")

        result_elements: list[IValue] = []
        for inner in outer_val:
            if not isinstance(inner, AbstractSetValue):
                raise ValueError(
                    f"Flatten requires a set of sets, got element {type(inner)}"
                )
            for elem in inner:
                result_elements.append(elem)
        return EnumeratedSetValue(*result_elements)

    # Binary operations (union, intersect, difference, subseteq)
    if len(e.args) != 2:
        raise ValueError(
            f"Set algebra operation expects 2 arguments, got {len(e.args)}"
        )

    # Evaluate both operands
    left_val = value(e.args[0], env)
    right_val = value(e.args[1], env)

    if type(left_val) is not EnumeratedSetValue and not isinstance(
        left_val, AbstractSetValue
    ):
        raise ValueError(f"Expected AbstractSetValue, got {type(left_val)}")
    if type(right_val) is not EnumeratedSetValue and not isinstance(
        right_val, AbstractSetValue
    ):
        raise ValueError(f"Expected AbstractSetValue, got {type(right_val)}")

    # SUBSETEQ can be checked without materialization
    if e.op == AlgebraOp.SUBSETEQ:
        # Subset or equal: check if every element of left is in right
        # This avoids materializing the right set and can short-circuit
        for elem in left_val:
            if elem not in right_val:
                return BoolValue(False)
        return BoolValue(True)

    # For set algebra operations (union, intersect, difference), materialize both sets
    left_materialized = left_val.materialize()
    right_materialized = right_val.materialize()

    # Both materialized values are guaranteed to be EnumeratedSetValue
    assert isinstance(left_materialized, EnumeratedSetValue)
    assert isinstance(right_materialized, EnumeratedSetValue)

    # Perform the set operation
    match e.op:
        case AlgebraOp.UNION:
            result_set = (
                left_materialized.material_set | right_materialized.material_set
            )
        case AlgebraOp.INTERSECT:
            result_set = (
                left_materialized.material_set & right_materialized.material_set
            )
        case AlgebraOp.DIFFERENCE:
            result_set = (
                left_materialized.material_set - right_materialized.material_set
            )
        case _:
            raise ValueError(f"Unknown set operation: {e.op}")

    # Return as enumerated set (using internal constructor to avoid unpacking)
    # Extract elem_sort so empty results retain sort information
    elem_sort = e.sort.elem_sort if isinstance(e.sort, SetSort) else None
    return EnumeratedSetValue._from_material_set(result_set, elem_sort=elem_sort)


def eval_bool_node(e: AlgebraNode, env: Env) -> BoolValue:
    """Evaluate a boolean algebra node"""
    match e.op:
        case AlgebraOp.AND:
            result = True
            for arg in e.args:
                val = value(arg, env)
                if type(val) is not BoolValue:
                    raise ValueError(f"Expected BoolValue, got {type(val)}")
                result = result and val.value
                if not result:  # Short-circuit
                    break
            return BoolValue(result)

        case AlgebraOp.OR:
            result = False
            for arg in e.args:
                val = value(arg, env)
                if type(val) is not BoolValue:
                    raise ValueError(f"Expected BoolValue, got {type(val)}")
                result = result or val.value
                if result:  # Short-circuit
                    break
            return BoolValue(result)

        case AlgebraOp.NOT:
            if len(e.args) != 1:
                raise ValueError(f"Not expects 1 argument, got {len(e.args)}")
            val = value(e.args[0], env)
            if type(val) is not BoolValue:
                raise ValueError(f"Expected BoolValue, got {type(val)}")
            return BoolValue(not val.value)

        case AlgebraOp.IMPLIES:
            if len(e.args) != 2:
                raise ValueError(f"Implies expects 2 arguments, got {len(e.args)}")
            left = value(e.args[0], env)
            right = value(e.args[1], env)
            if type(left) is not BoolValue or type(right) is not BoolValue:
                raise ValueError("Expected BoolValue arguments")
            # A => B is equivalent to (not A) or B
            return BoolValue((not left.value) or right.value)

        case AlgebraOp.IFF:
            if len(e.args) != 2:
                raise ValueError(f"Iff expects 2 arguments, got {len(e.args)}")
            left = value(e.args[0], env)
            right = value(e.args[1], env)
            if type(left) is not BoolValue or type(right) is not BoolValue:
                raise ValueError("Expected BoolValue arguments")
            # A <=> B is true when both have the same value
            return BoolValue(left.value == right.value)

        case _:
            raise NotImplementedError(f"Boolean operation {e.op} not implemented")


def eval_arith_node(e: AlgebraNode, env: Env) -> IntValue:
    """Evaluate an arithmetic node"""
    match e.op:
        case AlgebraOp.ADD:
            if len(e.args) != 2:
                raise ValueError(f"Add expects 2 arguments, got {len(e.args)}")
            left = value(e.args[0], env)
            right = value(e.args[1], env)
            if type(left) is not IntValue or type(right) is not IntValue:
                raise ValueError("Expected IntValue arguments")
            return IntValue(left.value + right.value)

        case AlgebraOp.SUB:
            if len(e.args) != 2:
                raise ValueError(f"Sub expects 2 arguments, got {len(e.args)}")
            left = value(e.args[0], env)
            right = value(e.args[1], env)
            if type(left) is not IntValue or type(right) is not IntValue:
                raise ValueError("Expected IntValue arguments")
            return IntValue(left.value - right.value)

        case AlgebraOp.MUL:
            if len(e.args) != 2:
                raise ValueError(f"Mul expects 2 arguments, got {len(e.args)}")
            left = value(e.args[0], env)
            right = value(e.args[1], env)
            if type(left) is not IntValue or type(right) is not IntValue:
                raise ValueError("Expected IntValue arguments")
            return IntValue(left.value * right.value)

        case AlgebraOp.DIV:
            if len(e.args) != 2:
                raise ValueError(f"Div expects 2 arguments, got {len(e.args)}")
            left = value(e.args[0], env)
            right = value(e.args[1], env)
            if type(left) is not IntValue or type(right) is not IntValue:
                raise ValueError("Expected IntValue arguments")
            if right.value == 0:
                raise ZeroDivisionError("Division by zero")
            return IntValue(left.value // right.value)  # Integer division

        case AlgebraOp.MOD:
            if len(e.args) != 2:
                raise ValueError(f"Mod expects 2 arguments, got {len(e.args)}")
            left = value(e.args[0], env)
            right = value(e.args[1], env)
            if type(left) is not IntValue or type(right) is not IntValue:
                raise ValueError("Expected IntValue arguments")
            if right.value == 0:
                raise ZeroDivisionError("Modulo by zero")
            return IntValue(left.value % right.value)

        case AlgebraOp.POW:
            if len(e.args) != 2:
                raise ValueError(f"Pow expects 2 arguments, got {len(e.args)}")
            left = value(e.args[0], env)
            right = value(e.args[1], env)
            if type(left) is not IntValue or type(right) is not IntValue:
                raise ValueError("Expected IntValue arguments")
            if left.value == 0 and right.value == 0:
                raise ValueError("0**0 is undefined")
            if right.value < 0:
                raise ValueError("Negative exponents are not supported")
            return IntValue(left.value**right.value)

        case AlgebraOp.NEG:
            if len(e.args) != 1:
                raise ValueError(f"Neg expects 1 argument, got {len(e.args)}")
            val = value(e.args[0], env)
            if type(val) is not IntValue:
                raise ValueError(f"Expected IntValue, got {type(val)}")
            return IntValue(-val.value)

        case _:
            raise NotImplementedError(f"Arithmetic operation {e.op} not implemented")


def eval_cmp_node(e: AlgebraNode, env: Env) -> BoolValue:
    """Evaluate an ordering comparison node (integers only)"""
    if len(e.args) != 2:
        raise ValueError(f"Comparison operation expects 2 arguments, got {len(e.args)}")

    left = value(e.args[0], env)
    right = value(e.args[1], env)

    if type(left) is not IntValue or type(right) is not IntValue:
        raise ValueError("Expected IntValue arguments for ordering comparison")

    match e.op:
        case AlgebraOp.LT:
            return BoolValue(left.value < right.value)

        case AlgebraOp.LE:
            return BoolValue(left.value <= right.value)

        case AlgebraOp.GT:
            return BoolValue(left.value > right.value)

        case AlgebraOp.GE:
            return BoolValue(left.value >= right.value)

        case _:
            raise NotImplementedError(f"Comparison operation {e.op} not implemented")


def eval_eq_node(e: AlgebraNode, env: Env) -> BoolValue:
    """Evaluate an equality/inequality node (any sort)"""
    if len(e.args) != 2:
        raise ValueError(f"Equality operation expects 2 arguments, got {len(e.args)}")

    left = value(e.args[0], env)
    right = value(e.args[1], env)

    match e.op:
        case AlgebraOp.EQ:
            return BoolValue(left == right)

        case AlgebraOp.NE:
            return BoolValue(left != right)

        case _:
            raise NotImplementedError(f"Equality operation {e.op} not implemented")


def eval_list_algebra_node(e: AlgebraNode, env: Env) -> IValue:
    """Evaluate a list algebra node (concat, size)"""
    match e.op:
        case AlgebraOp.LIST_SIZE:
            if len(e.args) != 1:
                raise ValueError(f"ListSize expects 1 argument, got {len(e.args)}")
            list_val = value(e.args[0], env)
            if type(list_val) is not ListValue:
                raise ValueError(f"Expected ListValue, got {type(list_val)}")
            return IntValue(len(list_val))

        case AlgebraOp.LIST_CONCAT:
            if len(e.args) != 2:
                raise ValueError(f"ListConcat expects 2 arguments, got {len(e.args)}")
            left_val = value(e.args[0], env)
            right_val = value(e.args[1], env)
            if type(left_val) is not ListValue:
                raise ValueError(f"Expected ListValue, got {type(left_val)}")
            if type(right_val) is not ListValue:
                raise ValueError(f"Expected ListValue, got {type(right_val)}")
            combined = list(left_val.elements) + list(right_val.elements)
            # Get elem_sort from the left list's sort
            elem_sort = (
                left_val._elem_sort if left_val._elem_sort else right_val._elem_sort
            )
            return ListValue(combined, elem_sort=elem_sort)

        case _:
            raise NotImplementedError(f"List operation {e.op} not implemented")


# Built once at module load time after all eval_* functions are defined.
# Maps each AlgebraOp directly to its handler, eliminating the sequential
# `e.op in SET` membership checks (and their enum __hash__ calls) in
# _value_algebra.
_OP_DISPATCH: dict[AlgebraOp, Callable[[AlgebraNode, "Env"], IValue]] = {
    **{op: eval_bool_node for op in BOOL_OPS},
    **{op: eval_arith_node for op in ARITH_OPS},
    **{op: eval_cmp_node for op in CMP_OPS},
    **{op: eval_eq_node for op in EQ_OPS},
    **{op: eval_set_algebra_node for op in SET_OPS},
    **{op: eval_list_algebra_node for op in LIST_OPS},
}
