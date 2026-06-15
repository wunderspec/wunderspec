"""
The user-facing constructors for expressions.

Igor Konnov, 2025-2026
"""

from __future__ import annotations

import inspect
from collections.abc import Generator, Hashable
from copy import copy
from enum import Enum
from functools import wraps
from types import GeneratorType, GenericAlias
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Callable,
    Generic,
    ParamSpec,
    Type,
    TypeAlias,
    TypeVar,
    Union,
    cast,
    dataclass_transform,
    get_args,
    get_origin,
    overload,
)

from typing_extensions import TypeAliasType

# Import node classes
# Re-export enums and sorts
from wunderspec.ast import (
    AlgebraNode,
    AlgebraOp,
    BoolSort,
    ExprCallNode,
    IteNode,
    LetNode,
    LitNode,
    Node,
    Sort,
)
from wunderspec.ast.action_ast import ActionCallNode, ActionNode, AssumeNode
from wunderspec.ast.ast import QuantOp, VarNode
from wunderspec.ast.list_ast import ListEnumNode, ListRangeNode
from wunderspec.ast.map_ast import MapEnumNode, MapLambdaNode
from wunderspec.ast.record_ast import RecordCtorNode
from wunderspec.ast.set_ast import (
    AllMapsNode,
    AllRecordsNode,
    AllSubsetsNode,
    AllTuplesNode,
    IntervalNode,
    SetEnumNode,
    SetFilterNode,
    SetIntOrNatNode,
    SetMapNode,
    SetQuantNode,
)
from wunderspec.ast.sorts import RecordSort, SetSort, TemporalSort, UnionSort, sort_of
from wunderspec.ast.temporal_ast import (
    AlwaysNode,
    EnabledNode,
    EventuallyNode,
    Fair,
    FairnessNode,
    ToTemporalNode,
)
from wunderspec.ast.tuple_ast import TupleCtorNode
from wunderspec.ast.union_ast import UnionCtorNode
from wunderspec.expr import (
    BoolExpr,
    EnumExpr,
    Expr,
    IntExpr,
    ListExpr,
    MapExpr,
    RecordExpr,
    SetExpr,
    StrExpr,
    TemporalExpr,
    TupleExpr,
    UnionExpr,
    VarExpr,
    _pop_gen_ctx,
    _push_gen_ctx,
    coerce_bool_node,
    coerce_expr,
    coerce_int_node,
    expr_from_node,
)
from wunderspec.uniq_names import fresh_name

# Type alias for values that can be coerced to Expr (literals + Expr)
ExprLike: TypeAlias = Union[Expr, int, str, bool, Enum]
ExprFunc: TypeAlias = Callable[..., Any]

_P = ParamSpec("_P")
_AnnotationT = TypeVar("_AnnotationT")


class Unit:
    """Marker type for a no-payload union variant."""


if TYPE_CHECKING:
    Field = TypeAliasType(
        "Field", Annotated[ExprLike, _AnnotationT], type_params=(_AnnotationT,)
    )

    class Variant(Generic[_AnnotationT]):
        """Type-checker-visible annotation for symbolic union variants."""

        @overload
        def __call__(self: "Variant[Unit]") -> UnionExpr: ...

        @overload
        def __call__(self, payload: _AnnotationT | Expr) -> UnionExpr: ...

        def __call__(self, *args: Any, **kwargs: Any) -> UnionExpr:
            raise NotImplementedError

else:

    class Field:
        """Annotation shorthand for symbolic record and union fields.

        ``Field[T]`` is equivalent to ``Annotated[ExprLike, T]``.
        """

        def __class_getitem__(cls, type_hint: object) -> object:
            return Annotated[ExprLike, type_hint]

    class Variant:
        """Annotation shorthand for symbolic union variants.

        ``Variant[T]`` is equivalent to ``Annotated[ExprLike, T]``.
        Use ``Variant[Unit]`` for a variant with no payload.
        """

        def __class_getitem__(cls, type_hint: object) -> object:
            return Annotated[ExprLike, type_hint]


@overload
def expr(
    func: Callable[_P, Any],
    *,
    cache_args: bool = ...,
    coerce: bool = ...,
    pure: bool = ...,
    inline: bool = ...,
) -> Callable[_P, "Expr"]: ...


@overload
def expr(
    func: None = ...,
    *,
    cache_args: bool = ...,
    coerce: bool = ...,
    pure: bool = ...,
    inline: bool = ...,
) -> Callable[[Callable[_P, Any]], Callable[_P, "Expr"]]: ...


def expr(  # type: ignore[misc]
    func: ExprFunc | None = None,
    *,
    cache_args: bool = True,
    coerce: bool = True,
    pure: bool = False,
    inline: bool = True,
) -> Any:
    """Decorate a helper function that returns symbolic expressions.

    The decorator always coerces inputs and the return value to Expr.
    When ``cache_args=True`` (default), each argument is additionally bound via
    nested LetNode wrappers to avoid eager inlining.
    When ``pure=True``, the function must not accept a state as its first
    argument.  Pure helpers operate on plain ``Expr`` values and are not
    treated as top-level TLA+ definitions during conversion.
    When ``inline=False``, the expression body is extracted as a separate named
    TLA+ operator instead of being inlined with LET…IN at every call site.
    """

    def decorator(fn: ExprFunc) -> ExprFunc:
        sig = inspect.signature(fn)

        def _alias_name(param_name: str, *, index: int | None = None) -> str:
            if index is None:
                return fresh_name(f"{param_name}_")
            return fresh_name(f"{param_name}{index}_")

        def _is_state_like(value: Any) -> bool:
            # @state-decorated classes expose _params/_vars/_asdict.
            return (
                hasattr(value, "_params")
                and isinstance(getattr(value, "_params"), tuple)
                and hasattr(value, "_vars")
                and isinstance(getattr(value, "_vars"), tuple)
                and callable(getattr(value, "_asdict", None))
            )

        def _is_state_view_like(value: Any) -> bool:
            # StateView carries variable fields in _mapping and optional
            # parameters in _params, returning Expr on field access.
            return (
                hasattr(value, "_mapping")
                and hasattr(value, "_params")
                and callable(getattr(value, "__getitem__", None))
            )

        def _coerce_or_expand(
            param_name: str, arg_value: Any, *, cache: bool
        ) -> tuple[Any, list[tuple[str, Node]]]:
            if pure and (_is_state_like(arg_value) or _is_state_view_like(arg_value)):
                raise TypeError(
                    f"@expr(pure=True) does not accept state arguments; "
                    f"got {type(arg_value).__name__} for parameter {param_name!r}"
                )

            if _is_state_view_like(arg_value):
                return arg_value, []

            if not _is_state_like(arg_value):
                if not coerce and not isinstance(arg_value, Expr):
                    raise TypeError(
                        f"@expr(coerce=False) requires Expr arguments; "
                        f"got {type(arg_value).__name__} for parameter {param_name!r}"
                    )

                arg_expr = coerce_expr(arg_value) if coerce else arg_value
                if not cache:
                    return arg_expr, []
                # VarNode is a single identifier — aliasing it is a no-op.
                if isinstance(arg_expr.node, VarNode):
                    return arg_expr, []
                alias_name = _alias_name(param_name)
                return VarExpr(alias_name, arg_expr.sort), [(alias_name, arg_expr.node)]

            if not cache:
                return arg_value, []

            state_aliases: list[tuple[str, Node]] = []
            field_values = arg_value._asdict()
            state_copy = copy(arg_value)
            for field_name in arg_value._params + arg_value._vars:
                if field_name not in field_values:
                    continue
                field_value = field_values[field_name]
                if coerce:
                    field_expr = coerce_expr(field_value)
                else:
                    if not isinstance(field_value, Expr):
                        raise TypeError(
                            f"@expr(cache_args=True, coerce=False) requires Expr state fields; "
                            f"got {type(field_value).__name__} for field {field_name!r}"
                        )
                    field_expr = field_value
                alias_name = _alias_name(f"{param_name}_{field_name}")
                # VarNode is already a single identifier — aliasing it is a no-op.
                if isinstance(field_expr.node, VarNode):
                    continue
                state_aliases.append((alias_name, field_expr.node))
                state_copy.__dict__[field_name] = VarExpr(alias_name, field_expr.sort)

            return state_copy, state_aliases

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Expr:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()

            if not inline:
                return _wrapper_non_inline(bound)
            return _wrapper_inline(bound)

        def _wrapper_inline(bound: inspect.BoundArguments) -> Expr:
            aliases: list[tuple[str, Node]] = []
            converted: dict[str, Any] = {}

            for param_name, param in sig.parameters.items():
                if param_name not in bound.arguments:
                    continue
                arg_value = bound.arguments[param_name]

                if param.kind is inspect.Parameter.VAR_POSITIONAL:
                    varargs: list[Any] = []
                    for i, item in enumerate(arg_value):
                        converted_item, new_aliases = _coerce_or_expand(
                            f"{param_name}{i}", item, cache=cache_args
                        )
                        aliases.extend(new_aliases)
                        varargs.append(converted_item)
                    converted[param_name] = tuple(varargs)
                elif param.kind is inspect.Parameter.VAR_KEYWORD:
                    varkw: dict[str, Any] = {}
                    for key, item in arg_value.items():
                        converted_item, new_aliases = _coerce_or_expand(
                            f"{param_name}_{key}", item, cache=cache_args
                        )
                        aliases.extend(new_aliases)
                        varkw[key] = converted_item
                    converted[param_name] = varkw
                else:
                    converted_item, new_aliases = _coerce_or_expand(
                        param_name, arg_value, cache=cache_args
                    )
                    aliases.extend(new_aliases)
                    converted[param_name] = converted_item

            call_args: list[Any] = []
            call_kwargs: dict[str, Any] = {}
            for param_name, param in sig.parameters.items():
                if param_name not in converted:
                    continue
                arg_expr = converted[param_name]
                if param.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ):
                    call_args.append(arg_expr)
                elif param.kind is inspect.Parameter.VAR_POSITIONAL:
                    call_args.extend(arg_expr)
                elif param.kind is inspect.Parameter.KEYWORD_ONLY:
                    call_kwargs[param_name] = arg_expr
                elif param.kind is inspect.Parameter.VAR_KEYWORD:
                    call_kwargs.update(arg_expr)

            raw_result = fn(*call_args, **call_kwargs)
            if coerce:
                result = coerce_expr(raw_result)
            else:
                if not isinstance(raw_result, Expr):
                    raise TypeError(
                        f"@expr(coerce=False) expects function {fn.__name__} to return Expr, "
                        f"got {type(raw_result).__name__}"
                    )
                result = raw_result
            if not cache_args:
                return result

            node = result.node
            for alias_name, alias_node in reversed(aliases):
                node = LetNode(alias_name, alias_node, node)
            return expr_from_node(node)

        def _wrapper_non_inline(bound: inspect.BoundArguments) -> Expr:
            # For inline=False: replace non-state Expr params with VarExpr
            # placeholders, call fn to get the body, then wrap in ExprCallNode.
            actual_arg_nodes: list[Node] = []
            param_names: list[str] = []
            call_args_ni: list[Any] = []
            call_kwargs_ni: dict[str, Any] = {}

            for param_name, param in sig.parameters.items():
                if param_name not in bound.arguments:
                    continue
                arg_value = bound.arguments[param_name]

                if _is_state_like(arg_value) or _is_state_view_like(arg_value):
                    # State params pass through as-is; no formal parameter needed.
                    call_arg = arg_value
                else:
                    # Coerce to get the sort, then replace with a formal VarExpr.
                    arg_expr = coerce_expr(arg_value) if coerce else arg_value
                    if not isinstance(arg_expr, Expr):
                        raise TypeError(
                            f"@expr(inline=False) requires Expr arguments; "
                            f"got {type(arg_value).__name__} for parameter {param_name!r}"
                        )
                    actual_arg_nodes.append(arg_expr.node)
                    param_names.append(param_name)
                    call_arg = VarExpr(param_name, arg_expr.sort)

                if param.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ):
                    call_args_ni.append(call_arg)
                elif param.kind is inspect.Parameter.VAR_POSITIONAL:
                    call_args_ni.extend(call_arg)
                elif param.kind is inspect.Parameter.KEYWORD_ONLY:
                    call_kwargs_ni[param_name] = call_arg
                elif param.kind is inspect.Parameter.VAR_KEYWORD:
                    call_kwargs_ni.update(call_arg)

            raw_result = fn(*call_args_ni, **call_kwargs_ni)
            if coerce:
                body = coerce_expr(raw_result)
            else:
                if not isinstance(raw_result, Expr):
                    raise TypeError(
                        f"@expr(coerce=False) expects function {fn.__name__} to return Expr, "
                        f"got {type(raw_result).__name__}"
                    )
                body = raw_result

            return expr_from_node(
                ExprCallNode(
                    fn.__name__,
                    tuple(actual_arg_nodes),
                    body.node,
                    tuple(param_names),
                )
            )

        wrapper._is_expr = True  # type: ignore[attr-defined]
        wrapper._inline = inline  # type: ignore[attr-defined]
        if pure:
            wrapper._is_expr_pure = True  # type: ignore[attr-defined]
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


# =============================================================================
# Constructor functions
# =============================================================================


@overload
def Var(name: str, typ: type[bool]) -> BoolExpr: ...  # type: ignore[overload-overlap]


@overload
def Var(name: str, typ: type[int]) -> IntExpr: ...  # type: ignore[overload-overlap]


@overload
def Var(name: str, typ: type[str]) -> StrExpr: ...  # type: ignore[overload-overlap]


@overload
def Var(name: str, typ: "type[dict]") -> MapExpr: ...  # type: ignore[overload-overlap]


@overload
def Var(name: str, typ: "type[set]") -> SetExpr: ...  # type: ignore[overload-overlap]


@overload
def Var(name: str, typ: "type[list]") -> ListExpr: ...  # type: ignore[overload-overlap]


@overload
def Var(name: str, typ: "type[tuple]") -> TupleExpr: ...  # type: ignore[overload-overlap]


@overload
def Var(name: str, typ: type) -> RecordExpr: ...  # type: ignore[overload-overlap]


def Var(name: str, typ: Type) -> Expr:
    """
    Create a variable of the sort corresponding to the given Python type.

    Args:
        name: The variable name
        typ: The Python type annotation for the variable

    Returns:
        An Expr representing the variable with the appropriate sort.
        The expression type depends on the sort derived from the Python type.
    """
    return VarExpr(name, sort_of(typ))  # type: ignore[arg-type]


@overload
def Val(value: bool) -> BoolExpr: ...  # type: ignore[overload-overlap]


@overload
def Val(value: int) -> IntExpr: ...


@overload
def Val(value: str) -> StrExpr: ...


@overload
def Val(value: Enum) -> EnumExpr: ...


def Val(
    value: ExprLike,
) -> BoolExpr | IntExpr | StrExpr | EnumExpr | Expr:
    """Create a literal from a Python value."""
    match value:
        case Expr():
            return value
        case bool():
            return BoolExpr(LitNode(value))
        case int():
            return IntExpr(LitNode(value))
        case str():
            return StrExpr(LitNode(value))
        case Enum():
            return EnumExpr(LitNode(value))
        case _:
            raise TypeError(f"Cannot create literal from type {type(value).__name__}")


def Record(**fields: ExprLike) -> RecordExpr:
    """Create a record with the specified fields.

    Field values may be Exprs or raw Python literals (int, str, bool, Enum),
    which are auto-coerced.

    Example:
        >>> Record(name="Alice", age=30, active=True)
        Record(active=Lit(True), age=Lit(30), name=Lit('Alice'))
    """
    field_nodes = {name: coerce_expr(v)._node for name, v in fields.items()}
    return RecordExpr(RecordCtorNode(**field_nodes))


def Tuple(*elements: ExprLike) -> TupleExpr:
    """Create a tuple with the specified elements.

    Elements may be Exprs or raw Python literals (int, str, bool, Enum),
    which are auto-coerced.

    Example:
        >>> Tuple(42, True)
        Tuple(Lit(42), Lit(True))
        >>> Tuple(1, 2, 3)
        Tuple(Lit(1), Lit(2), Lit(3))
    """
    if not elements:
        raise ValueError("Tuple must have at least one element")
    elem_nodes = tuple(coerce_expr(e)._node for e in elements)
    return TupleExpr(TupleCtorNode(*elem_nodes))


def Set(arg0: object, *other_args: object) -> SetExpr:
    """Create a symbolic set from the given elements, element sort, or an interval.

    Examples:

        Create a set by enumerating its elements (integers and Booleans are auto-coerced):

            >>> from wunderspec import Set, Val, Var
            >>> Set(Val(2), Val(3), Val(4))
            Set(Lit(2), Lit(3), Lit(4))
            >>> Set(2, 3, 4)
            Set(Lit(2), Lit(3), Lit(4))
            >>> Set('a', 'b', 'c')
            Set(Lit('a'), Lit('b'), Lit('c'))
            >>> Set(True, False, True)
            Set(Lit(True), Lit(False), Lit(True))
            >>> Set(Var("x", int), Var("y", int), Var("z", int))
            Set(Var('x', IntSort()), Var('y', IntSort()), Var('z', IntSort()))

        Create an empty set by specifying the element sort:

            >>> from wunderspec import IntSort, Set, SetSort, sort_of
            >>> Set(SetSort(IntSort()))
            Set(SetSort(IntSort()))
            >>> Set(sort_of(set[int]))
            Set(SetSort(IntSort()))

        Create an empty set by specifying the element sort using a Python type:

            >>> Set(int)
            Set(IntSort())
            >>> Set(bool)
            Set(BoolSort())
            >>> Set(set[str])
            Set(SetSort(StrSort()))

        Create an interval set by specifying its lower and upper bounds:

            >>> from wunderspec import Set
            >>> Set(4, ..., 10)
            Interval(Lit(4), Lit(10))
            >>> Set(Var("a", int), ..., Var("b", int))
            Interval(Var('a', IntSort()), Var('b', IntSort()))

        Create a set by comprehension over another set (set-map):

            >>> Set(x * Val(2) for x in S)  # doctest: +SKIP

    """
    match (arg0, other_args):
        case (gen, ()) if isinstance(gen, GeneratorType):
            return _gen_set(gen)

        case (
            Expr() | int() as lower,
            (middle, Expr() | int() as upper),
        ) if middle is Ellipsis:
            return Interval(lower, upper)

        case (Sort() as sort_arg, ()):
            return SetExpr(SetEnumNode(sort_arg))

        case (type() as typ, ()):
            return SetExpr(SetEnumNode(sort_of(typ)))

        case (GenericAlias() as typ, ()):
            return SetExpr(SetEnumNode(sort_of(typ)))

        case _:
            # treat all args as elements
            all_args = (arg0,) + other_args
            coerced = [coerce_expr(elem, None) for elem in all_args]
            nodes = [e._node for e in coerced]

            elem_sort = nodes[0].sort
            return SetExpr(SetEnumNode(elem_sort, *nodes))


def Interval(
    lower: Union[IntExpr, int, Expr],
    upper: Union[IntExpr, int, Expr],
) -> SetExpr:
    """Create an integer interval [lower..upper]."""
    lower_node = coerce_int_node(lower)
    upper_node = coerce_int_node(upper)
    return SetExpr(IntervalNode(lower_node, upper_node))


def List(arg0: object, *other_args: object) -> ListExpr:
    """Create a symbolic list from the given elements.

    Examples:

        Create a list by enumerating its elements (integers and Booleans are auto-coerced):

            >>> from wunderspec import List, Val, Var
            >>> List(Val(2), Val(3), Val(4))
            List(Lit(2), Lit(3), Lit(4))
            >>> List(2, 3, 4)
            List(Lit(2), Lit(3), Lit(4))

    """
    match (arg0, other_args):
        case ():
            raise ValueError("The empty list must specify the element sort")

        case (Sort() as sort_arg, ()):
            return ListExpr(ListEnumNode(sort_arg))

        case (type() as typ, ()):
            return ListExpr(ListEnumNode(sort_of(typ)))

        case (GenericAlias() as typ, ()):
            return ListExpr(ListEnumNode(sort_of(typ)))

        case _:
            args = (arg0,) + other_args

    coerced = [coerce_expr(elem, None) for elem in args]
    nodes = [e._node for e in coerced]
    elem_sort = nodes[0].sort
    return ListExpr(ListEnumNode(elem_sort, *nodes))


def Range(lower: Union[IntExpr, int], upper: Union[IntExpr, int]) -> ListExpr:
    """Create a list representing the integer range [lower, upper) (lower included, upper excluded).

    Examples:

        >>> from wunderspec import Range, Val
        >>> Range(0, 5)
        Range(Lit(0), Lit(5))

    """
    lower_node = coerce_int_node(lower)
    upper_node = coerce_int_node(upper)
    return ListExpr(ListRangeNode(lower_node, upper_node))


Ints = SetExpr(SetIntOrNatNode(is_signed=True))
"""
The set of all integers. This set is infinite, so not much can be done with
it directly.
"""


UnsignedInts = SetExpr(SetIntOrNatNode(is_signed=False))
"""
The set of all unsigned integers (natural numbers). This set is infinite, so not
much can be done with it directly.
"""


def AllSubsets(s: Expr) -> SetExpr:
    """Create the set of all subsets (power set) of the given set."""
    if not isinstance(s.sort, SetSort):
        raise TypeError(f"AllSubsets requires a Set, got {s.sort}")
    return SetExpr(AllSubsetsNode(s._node))


def AllMaps(key_set: Expr, value_set: Expr) -> SetExpr:
    """Create the set of all maps from key_set to value_set."""
    if not isinstance(key_set.sort, SetSort):
        raise TypeError(f"AllMaps key_set must be a Set, got {key_set.sort}")
    if not isinstance(value_set.sort, SetSort):
        raise TypeError(f"AllMaps value_set must be a Set, got {value_set.sort}")
    return SetExpr(AllMapsNode(key_set._node, value_set._node))


def AllTuples(*sets: Expr) -> SetExpr:
    """Create the set of all tuples (Cartesian product) from the given sets."""
    if len(sets) < 1:
        raise ValueError("AllTuples requires at least one set")
    for s in sets:
        if not isinstance(s.sort, SetSort):
            raise TypeError(f"AllTuples requires Sets, got {s.sort}")
    return SetExpr(AllTuplesNode(tuple(s._node for s in sets)))


def AllRecords(**sets: Expr) -> SetExpr:
    """Create the set of all records with fields drawn from the given sets."""
    if len(sets) < 1:
        raise ValueError("AllRecords requires at least one field")
    for name, s in sets.items():
        if not isinstance(s.sort, SetSort):
            raise TypeError(f"AllRecords field {name} must be a Set, got {s.sort}")
    return SetExpr(AllRecordsNode({name: s._node for name, s in sets.items()}))


def Map(*args: object) -> MapExpr:
    """Create a map.

    Three usage modes:

    1. Empty map:  ``Map(key_sort, value_sort)``
    2. Generator:  ``Map(v for x in S)`` builds ``[x ∈ S ↦ v]`` (exactly one
       ``for`` clause)
    3. Explicit:   ``Map((Val("a"), Val(1)), (Val("b"), Val(2)))``

    In mode 3 each argument is a 2-tuple of ``Expr`` objects (or raw Python
    literals that can be lifted with ``Val``).  Key and value sorts are
    inferred from the first pair; all subsequent pairs must match.

    For set comprehensions use ``Set(e for x in S)``.

    Examples:
        >>> Map(int, str)
        Map(IntSort(), StrSort())
        >>> Map((Val("a"), Val(1)), (Val("b"), Val(2)))  # doctest: +SKIP
        >>> Map(Val(10_000) for a in ADDR)  # doctest: +SKIP
    """
    if len(args) == 1 and isinstance(args[0], GeneratorType):
        return _gen_map(args[0])

    if args and all(isinstance(a, tuple) and len(a) == 2 for a in args):
        # Explicit pairs: Map((k1, v1), (k2, v2), ...)
        pairs: list[tuple[Expr, Expr]] = []
        for arg in args:
            k, v = cast(tuple[object, object], arg)
            key_expr = k if isinstance(k, Expr) else coerce_expr(k)
            value_expr = v if isinstance(v, Expr) else coerce_expr(v)
            pairs.append((key_expr, value_expr))
        key_sort = pairs[0][0].node.sort
        value_sort = pairs[0][1].node.sort
        mappings = {k.node: v.node for k, v in pairs}
        return MapExpr(MapEnumNode(key_sort, value_sort, mappings))

    # Empty map: Map(int, str) or Map(IntSort(), StrSort())
    if len(args) == 2:
        key_sort_arg, value_sort_arg = args[0], args[1]
        key_sort = (
            key_sort_arg
            if isinstance(key_sort_arg, Sort)
            else sort_of(cast(Hashable, key_sort_arg))
        )
        value_sort = (
            value_sort_arg
            if isinstance(value_sort_arg, Sort)
            else sort_of(cast(Hashable, value_sort_arg))
        )
        return MapExpr(MapEnumNode(key_sort, value_sort))

    raise TypeError(
        "Map() requires a generator, two sort arguments for an empty map, "
        "or one or more (key, value) tuple pairs for an explicit map."
    )


def _lift_to_temporal(node: Node) -> Node:
    """Lift a node to TemporalSort if needed.

    BoolSort nodes are wrapped in ToTemporalNode to give them TemporalSort.
    TemporalSort nodes are returned as-is.
    """
    if isinstance(node.sort, TemporalSort):
        return node
    elif isinstance(node.sort, BoolSort):
        return ToTemporalNode(node)
    else:
        raise TypeError(f"Expected BoolSort or TemporalSort, got {node.sort}")


# Logical operators
def And(*args: Expr | bool) -> BoolExpr:
    """Create a Boolean logical AND expression."""
    bool_nodes = [coerce_bool_node(arg) for arg in args]
    return BoolExpr(AlgebraNode(BoolSort(), AlgebraOp.AND, *bool_nodes))


def Or(*args: Expr | bool) -> BoolExpr:
    """Create a Boolean logical OR expression."""
    bool_nodes = [coerce_bool_node(arg) for arg in args]
    return BoolExpr(AlgebraNode(BoolSort(), AlgebraOp.OR, *bool_nodes))


def Not(arg: Expr | bool) -> BoolExpr:
    """Create a Boolean logical NOT expression."""
    return BoolExpr(AlgebraNode(BoolSort(), AlgebraOp.NOT, coerce_bool_node(arg)))


def Implies(a: Expr | bool, b: Expr | bool) -> BoolExpr:
    """Create a Boolean logical implication expression."""
    return BoolExpr(
        AlgebraNode(
            BoolSort(), AlgebraOp.IMPLIES, coerce_bool_node(a), coerce_bool_node(b)
        )
    )


def _coerce_temporal_nodes(*args: Expr | bool) -> list[Node]:
    """Lift Boolean inputs and keep temporal inputs as temporal nodes."""
    temporal_nodes: list[Node] = []
    for arg in args:
        if isinstance(arg, bool):
            temporal_nodes.append(ToTemporalNode(LitNode(arg)))
        else:
            temporal_nodes.append(_lift_to_temporal(arg._node))
    return temporal_nodes


def AndT(*args: Expr | bool) -> TemporalExpr:
    """Create a temporal logical AND expression."""
    if not args:
        raise ValueError("AndT requires at least one argument")
    return TemporalExpr(
        AlgebraNode(TemporalSort(), AlgebraOp.AND, *_coerce_temporal_nodes(*args))
    )


def OrT(*args: Expr | bool) -> TemporalExpr:
    """Create a temporal logical OR expression."""
    if not args:
        raise ValueError("OrT requires at least one argument")
    return TemporalExpr(
        AlgebraNode(TemporalSort(), AlgebraOp.OR, *_coerce_temporal_nodes(*args))
    )


def NotT(arg: Expr | bool) -> TemporalExpr:
    """Create a temporal logical NOT expression."""
    return TemporalExpr(
        AlgebraNode(TemporalSort(), AlgebraOp.NOT, *_coerce_temporal_nodes(arg))
    )


def ImpliesT(a: Expr | bool, b: Expr | bool) -> TemporalExpr:
    """Create a temporal logical implication expression."""
    return TemporalExpr(
        AlgebraNode(TemporalSort(), AlgebraOp.IMPLIES, *_coerce_temporal_nodes(a, b))
    )


def Enabled(action: object, /, *args: object) -> TemporalExpr:
    """Create an 'enabled' expression for the given action.

    Can be called in two ways:

    1. With an ``@action(inline=False)`` function and its arguments::

        Enabled(my_action, actor)

    2. With an action ``Expr``::

        Enabled(action_expr)
    """
    if callable(action) and not isinstance(action, Expr):
        if not hasattr(action, "_inline"):
            raise TypeError(
                f"Enabled(...) requires an @action(inline=False) decorated function, "
                f"got: {action!r}"
            )
        if getattr(action, "_inline"):
            raise TypeError(
                f"Enabled(...) requires the action to be marked with "
                f"@action(inline=False), but "
                f"'{getattr(action, '__name__', action)}' uses inline=True. "
                f"Add @action(inline=False) to the action definition."
            )
        action_name = getattr(action, "_action_name")
        arg_nodes = tuple(coerce_expr(a)._node for a in args)
        dummy = AssumeNode(LitNode(True))
        action_node: ActionNode = ActionCallNode(
            action_name,
            arg_nodes,
            dummy,
            placeholder_body=True,
        )
    else:
        if not isinstance(action, Expr):
            raise TypeError(
                "Enabled(...) requires an action expression or "
                "an @action(inline=False) decorated function"
            )
        if not isinstance(action._node, ActionNode):
            raise TypeError(
                f"Enabled(...) requires an action expression, found: {action.sort}"
            )
        action_node = action._node
    return TemporalExpr(EnabledNode(action_node))


def Always(subformula: Expr) -> TemporalExpr:
    """Create an 'always' temporal expression."""
    if not isinstance(subformula._node.sort, (BoolSort, TemporalSort)):
        raise TypeError("Always(...) argument must be of BoolSort or TemporalSort")
    return TemporalExpr(AlwaysNode(subformula._node))


def Eventually(subformula: Expr) -> TemporalExpr:
    """Create an 'eventually' temporal expression."""
    if not isinstance(subformula._node.sort, (BoolSort, TemporalSort)):
        raise TypeError("Eventually(...) argument must be of BoolSort or TemporalSort")
    return TemporalExpr(EventuallyNode(subformula._node))


def _make_fairness(
    kind: Fair, action: object, /, *args: object, vars: tuple[str, ...] = ()
) -> TemporalExpr:
    prefix = "WeakFair" if kind == Fair.WEAK else "StrongFair"
    if callable(action) and not isinstance(action, Expr):
        # Action function path: action is a @action(inline=False) decorated function
        if not hasattr(action, "_inline"):
            raise TypeError(
                f"{prefix}(...) requires an @action(inline=False) decorated function, "
                f"got: {action!r}"
            )
        if getattr(action, "_inline"):
            raise TypeError(
                f"{prefix}(...) requires the action to be marked with "
                f"@action(inline=False), but '{getattr(action, '__name__', action)}' uses inline=True. "
                f"Add @action(inline=False) to the action definition."
            )
        action_name = getattr(action, "_action_name")
        arg_nodes = tuple(coerce_expr(a)._node for a in args)
        dummy = AssumeNode(LitNode(True))
        node = ActionCallNode(
            action_name,
            arg_nodes,
            dummy,
            placeholder_body=True,
        )
        return TemporalExpr(FairnessNode(kind, node, vars))
    else:
        # Expr path (backward compatible): args are var names
        if not isinstance(action, Expr):
            raise TypeError(
                f"{prefix}(...) requires an action expression or "
                f"an @action(inline=False) decorated function"
            )
        if not isinstance(action._node, ActionNode):
            raise TypeError(
                f"{prefix}(...) requires an action expression, found: {action.sort}"
            )
        names = tuple(str(a) for a in args)
        return TemporalExpr(FairnessNode(kind, action._node, names))


def WeakFair(
    action: object, /, *args: object, vars: tuple[str, ...] = ()
) -> TemporalExpr:
    """Create a weak fairness expression.

    Can be called in two ways:

    1. With an ``@action(inline=False)`` function and its arguments::

        WeakFair(try_read, actor, vars=("x", "y"))

    2. With an action ``Expr`` and stuttering variable names::

        WeakFair(action_expr, "x", "y")
    """
    return _make_fairness(Fair.WEAK, action, *args, vars=vars)


def StrongFair(
    action: object, /, *args: object, vars: tuple[str, ...] = ()
) -> TemporalExpr:
    """Create a strong fairness expression.

    Can be called in two ways:

    1. With an ``@action(inline=False)`` function and its arguments::

        StrongFair(try_read, actor, vars=("x", "y"))

    2. With an action ``Expr`` and stuttering variable names::

        StrongFair(action_expr, "x", "y")
    """
    return _make_fairness(Fair.STRONG, action, *args, vars=vars)


def Ite(cond: Union[BoolExpr, bool], then_expr: ExprLike, else_expr: ExprLike) -> Expr:
    """Create an if-then-else expression.

    ``cond``, ``then_expr``, and ``else_expr`` may be raw Python literals
    (``bool``, ``int``, ``str``, ``Enum``), which are auto-coerced. The then and
    else branches must have the same sort.
    """
    cond_node = coerce_bool_node(cond)
    # Derive the target sort from whichever branch is already an Expr, so a
    # literal branch is coerced to match. If both are literals, the sort is
    # inferred independently from each value.
    target_sort: Sort | None = None
    if isinstance(then_expr, Expr):
        target_sort = then_expr._node.sort
    elif isinstance(else_expr, Expr):
        target_sort = else_expr._node.sort
    then_e = coerce_expr(then_expr, target_sort)
    else_e = coerce_expr(else_expr, target_sort)
    if then_e.sort != else_e.sort:
        raise TypeError(
            f"then_expr and else_expr are of different sorts: {then_e.sort} and {else_e.sort}"
        )
    node = IteNode(cond_node, then_e._node, else_e._node)
    return expr_from_node(node)


# =============================================================================
# Record decorator for symbolic record types
# =============================================================================

_T = TypeVar("_T")


@dataclass_transform(kw_only_default=True)
def record(cls: type[_T]) -> type[_T]:
    """Decorator that transforms a class with annotated fields into a symbolic record type.

    The decorated class becomes a subclass of RecordExpr, so instances are Expr.
    Fields should be annotated with `Field[type_hint]`, where `type_hint` is the
    Python type that determines the sort (e.g., `int`, `str`, `dict[str, int]`).
    The underlying `Annotated[ExprLike, type_hint]` form remains supported.

    Example:
        ```python
        @record
        class Person:
            name: Field[str]
            age: Field[int]

        alice = Person(name="Alice", age=30)  # auto-coerces to Expr, returns RecordExpr
        ```

    Args:
        cls: A class with Field[type] or Annotated[ExprLike, type] field annotations.

    Returns:
        The transformed class (now a RecordExpr subclass) with record functionality.
    """
    # Get annotations using inspect.get_annotations (preferred over get_type_hints)
    annotations = inspect.get_annotations(cls, eval_str=True)

    field_sorts: dict[str, Sort] = {}

    for field_name, hint in annotations.items():
        # Check if it's an Annotated type
        if get_origin(hint) is Annotated:
            args = get_args(hint)
            # args[0] is Expr, args[1] is the type hint for sort_of
            if len(args) < 2:
                raise TypeError(
                    f"Field {field_name} must have Annotated[Expr, type_hint]"
                )
            type_hint = args[1]
            field_sort = sort_of(type_hint)
            field_sorts[field_name] = field_sort

    # Create the __new__ method that returns RecordExpr
    def __new__(cls_arg: type, **fields_expr: ExprLike) -> RecordExpr:
        """Create a validated RecordExpr with the specified fields.

        Args:
            **fields_expr: Named fields where each value is an ExprLike
                          (Expr, int, str, bool, or Enum - auto-coerced).

        Returns:
            A RecordExpr with the record's sort.

        Raises:
            TypeError: If field types don't match or required fields are missing.
            ValueError: If extra fields are provided that aren't in the RecordSort.
        """
        # Check for missing fields
        expected_fields = set(field_sorts.keys())
        provided_fields = set(fields_expr.keys())

        missing_fields = expected_fields - provided_fields
        if missing_fields:
            raise TypeError(
                f"Missing required fields: {', '.join(sorted(missing_fields))}"
            )

        # Check for extra fields
        extra_fields = provided_fields - expected_fields
        if extra_fields:
            raise ValueError(
                f"Extra fields not in record: {', '.join(sorted(extra_fields))}"
            )

        coerced_fields: dict[str, Expr] = {}
        for field_name in expected_fields:
            expected_sort = field_sorts[field_name]
            coerced_fields[field_name] = coerce_expr(
                fields_expr[field_name], expected_sort
            )

        # Create the record node
        field_nodes = {name: expr._node for name, expr in coerced_fields.items()}
        node = RecordCtorNode(**field_nodes)
        return RecordExpr(node)

    # Build bases: RecordExpr + any non-object bases from original class
    original_bases = tuple(b for b in cls.__bases__ if b is not object)
    new_bases = (RecordExpr,) + original_bases

    # Create a new class that inherits from RecordExpr
    new_cls = type(
        cls.__name__,
        new_bases,
        {
            "__new__": __new__,
            "__module__": cls.__module__,
            "__qualname__": cls.__qualname__,
            "__annotations__": cls.__annotations__,
            "__doc__": cls.__doc__,
            # sort_of inspects _record_sort
            "_record_sort": RecordSort(**field_sorts),
        },
    )

    return new_cls  # type: ignore[return-value]


# =============================================================================
# Union decorator for symbolic union (sum) types
# =============================================================================


def union(cls: type[_T]) -> type[_T]:
    """Decorator that transforms a class with annotated fields into a symbolic union type.

    The decorated class becomes a factory for UnionExpr instances. Each annotated field
    defines a variant: the field name is the tag, and the Field type hint determines
    the payload sort (use `()` for no-payload variants). The underlying
    `Variant[type_hint]`, `Field[type_hint]`, and `Annotated[ExprLike, type_hint]`
    forms are supported. Use `Variant[Unit]` or `()` for no-payload variants.

    Example:
        ```python
        @union
        class Option:
            Some: Variant[int]    # variant with int payload
            None_: Variant[Unit]  # variant with no payload

        x = Option.Some(42)       # UnionExpr with tag "Some", payload IntExpr(42)
        y = Option.None_()        # UnionExpr with tag "None_", no payload
        ```

    Args:
        cls: A class with Variant[type], Field[type], or Annotated[ExprLike, type]
            field annotations.

    Returns:
        The transformed class with variant constructor classmethods.
    """
    annotations = inspect.get_annotations(cls, eval_str=True)

    variant_sorts: dict[str, Sort | None] = {}

    for variant_name, hint in annotations.items():
        if get_origin(hint) is Annotated:
            args = get_args(hint)
            if len(args) < 2:
                raise TypeError(
                    f"Variant {variant_name} must have Annotated[ExprLike, type_hint]"
                )
            type_hint = args[1]
            # Unit, (), and None mean no payload.
            if type_hint is Unit or type_hint == () or type_hint is type(None):
                variant_sorts[variant_name] = None
            else:
                variant_sorts[variant_name] = sort_of(type_hint)

    union_sort = UnionSort(**variant_sorts)

    # Build variant constructor classmethods
    class_dict: dict[str, object] = {
        "__module__": cls.__module__,
        "__qualname__": cls.__qualname__,
        "__annotations__": cls.__annotations__,
        "__doc__": cls.__doc__,
        "_union_sort": union_sort,
    }

    for variant_name, payload_sort in variant_sorts.items():
        if payload_sort is not None:
            # Variant with payload

            def _make_ctor_with_payload(
                tag: str, p_sort: Sort, u_sort: UnionSort
            ) -> classmethod:
                def ctor(cls_arg, payload_value: ExprLike) -> UnionExpr:
                    payload_expr = coerce_expr(payload_value, p_sort)
                    node = UnionCtorNode(u_sort, tag, payload_expr._node)
                    return UnionExpr(node)

                ctor.__name__ = tag
                ctor.__qualname__ = f"{cls.__qualname__}.{tag}"
                return classmethod(ctor)

            class_dict[variant_name] = _make_ctor_with_payload(
                variant_name, payload_sort, union_sort
            )
        else:
            # Variant without payload

            def _make_ctor_no_payload(tag: str, u_sort: UnionSort) -> classmethod:
                def ctor(cls_arg) -> UnionExpr:
                    node = UnionCtorNode(u_sort, tag)
                    return UnionExpr(node)

                ctor.__name__ = tag
                ctor.__qualname__ = f"{cls.__qualname__}.{tag}"
                return classmethod(ctor)

            class_dict[variant_name] = _make_ctor_no_payload(variant_name, union_sort)

    new_cls = type(cls.__name__, (object,), class_dict)
    return new_cls  # type: ignore[return-value]


# =============================================================================
# Generator expression consuming functions
# =============================================================================


def _consume_generator(gen):
    """Push context, consume one value from generator, return (bindings, body)."""
    ctx = _push_gen_ctx()
    try:
        body = next(gen)
        return ctx.bindings, body
    except StopIteration:
        return ctx.bindings, None
    finally:
        _pop_gen_ctx()


def _build_bindings_nodes(
    bindings: list[tuple[Expr, Expr]],
) -> list[tuple[VarNode, Node]]:
    """Convert (VarExpr, SetExpr/ListExpr) pairs to (VarNode, Node) pairs."""
    var_nodes: list[tuple[VarNode, Node]] = []
    for v, d in bindings:
        if not isinstance(v._node, VarNode):
            raise TypeError(f"Expected VarNode, got {type(v._node).__name__}")
        var_nodes.append((VarNode(v._node.name, v._node.sort), d._node))
    return var_nodes


def _quant(gen, quant_op: QuantOp, empty_val: bool) -> BoolExpr | TemporalExpr:
    """Build a quantifier expression from a generator.

    Shared implementation for ``Forall`` and ``Exists``.
    """
    bindings, body = _consume_generator(gen)
    if body is None:
        return BoolExpr(LitNode(empty_val))
    if not bindings:
        raise ValueError(f"{quant_op.value} requires at least one 'for' clause")
    var_nodes = _build_bindings_nodes(bindings)
    quant_node = SetQuantNode(
        quant_op, bindings=var_nodes, body=body._node, sort=body.sort
    )
    match body.sort:
        case BoolSort():
            return BoolExpr(quant_node)
        case TemporalSort():
            return TemporalExpr(quant_node)
        case _:
            raise TypeError(
                f"{quant_op.value} body must be BoolExpr or TemporalExpr, "
                f"got {body.sort}"
            )


@overload
def Forall(gen: Generator[BoolExpr, None, None]) -> BoolExpr: ...


@overload
def Forall(  # type: ignore[overload-overlap]
    gen: Generator[TemporalExpr, None, None],
) -> TemporalExpr: ...


@overload
def Forall(gen: Generator[Expr, None, None]) -> BoolExpr: ...


def Forall(gen: Generator[Expr, None, None]) -> BoolExpr | TemporalExpr:
    """Universal quantification via generator expression.

    Examples::

        Forall(x > Val(0) for x in S)           # ∀x ∈ S : x > 0
        Forall(x + y > Val(0) for x in S1 for y in S2)  # ∀x ∈ S1, y ∈ S2 : x+y > 0
    """
    return _quant(gen, QuantOp.FORALL, True)


@overload
def Exists(gen: Generator[BoolExpr, None, None]) -> BoolExpr: ...


@overload
def Exists(  # type: ignore[overload-overlap]
    gen: Generator[TemporalExpr, None, None],
) -> TemporalExpr: ...


@overload
def Exists(gen: Generator[Expr, None, None]) -> BoolExpr: ...


def Exists(gen: Generator[Expr, None, None]) -> BoolExpr | TemporalExpr:
    """Existential quantification via generator expression.

    Examples::

        Exists(x > Val(0) for x in S)           # ∃x ∈ S : x > 0
        Exists(x + y > Val(0) for x in S1 for y in S2)  # ∃x ∈ S1, y ∈ S2 : x+y > 0
    """
    return _quant(gen, QuantOp.EXISTS, False)


def _gen_set(gen: Generator[Expr, None, None]) -> SetExpr:
    """Set comprehension via generator expression (internal, called by Set)."""
    bindings, body = _consume_generator(gen)
    if body is None:
        raise ValueError("Set comprehension over empty domain")
    if not bindings:
        raise ValueError("Set comprehension requires at least one 'for' clause")
    var_nodes = _build_bindings_nodes(bindings)
    body = coerce_expr(body)
    return SetExpr(SetMapNode(bindings=var_nodes, body=body._node))


def SetIf(gen: Generator[Expr, None, None]) -> SetExpr:
    """Filtered set comprehension via generator expression.

    Examples::

        SetIf(x > Val(3) for x in S)            # { x ∈ S : x > 3 }
    """
    bindings, body = _consume_generator(gen)
    if body is None:
        raise ValueError("SetIf over empty domain")
    if not bindings:
        raise ValueError("SetIf requires at least one 'for' clause")
    var_nodes = _build_bindings_nodes(bindings)
    body = coerce_expr(body)
    return SetExpr(SetFilterNode(bindings=var_nodes, body=body._node))


def _gen_map(gen: Generator[Expr, None, None]) -> MapExpr:
    """Map comprehension via generator (internal, called by Map).

    Builds ``[x ∈ S ↦ f(x)]``; requires exactly one 'for' clause.
    """
    bindings, body = _consume_generator(gen)
    if body is None:
        raise ValueError("Map comprehension over empty domain")
    if len(bindings) != 1:
        raise ValueError("Map comprehension requires exactly one 'for' clause")
    var, domain = bindings[0]
    if not isinstance(var._node, VarNode):
        raise TypeError(f"Expected VarNode, got {type(var._node).__name__}")
    body = coerce_expr(body)
    return MapExpr(
        MapLambdaNode(domain._node, VarNode(var._node.name, var._node.sort), body._node)
    )
