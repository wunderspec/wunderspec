"""
Core expression classes for Wunderspec.

This module provides wrapper classes that combine pure AST nodes from ast/
with operator logic to provide a convenient user-facing API.

Each Expr class wraps a Node and provides operators that return new Expr
instances. The interpreter works with the underlying nodes.

Note that the expression classes in this module are usually not instantiated
directly. Instead, use the factory functions in `wunderspec.lang` to create
expressions of the desired type.

Igor Konnov, 2025-2026
"""

import inspect
import threading
from contextlib import contextmanager
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterator, Type, cast, overload

from wunderspec.ast.ast import (
    AlgebraNode,
    AlgebraOp,
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
    ListReduceNode,
    ListSliceNode,
    ListUpdateNode,
)
from wunderspec.ast.map_ast import MapGetNode, MapKeysNode, MapLambdaNode, MapSetNode
from wunderspec.ast.record_ast import RecordGetNode, RecordUpdateNode
from wunderspec.ast.set_ast import (
    ChooseNode,
    IntervalNode,
    SetEnumNode,
    SetFilterNode,
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
    TemporalSort,
    TupleSort,
    UnionSort,
)
from wunderspec.ast.temporal_ast import AlwaysNode, EventuallyNode, ToTemporalNode
from wunderspec.ast.tuple_ast import TupleCtorNode, TupleGetNode, TupleUpdateNode
from wunderspec.ast.union_ast import UnionGetTagNode, UnionMatchNode
from wunderspec.uniq_names import fresh_name

# =============================================================================
# Expression hierarchy
# =============================================================================


class Expr:
    """
    Unified expression builder. Expr implements a fat base class to play well
    with static type checkers and IDE autocompletion. However, the static
    type checker may miss certain sort errors, e.g., when dealing with nested maps
    and records. Therefore, all Expr methods perform sort checks at runtime.

    Examples:

        Create variables and expressions:

            >>> from wunderspec import Var
            >>> x = Var('x', int)
            >>> y = Var('y', int)

        Simple expressions display on one line:

            >>> expr = x + y
            >>> print(expr)
            ADD(Var(x), Var(y))

        Pretty print complex nested expressions:

            >>> expr = ((x + y) * (x - y)) / ((x * 2) + (y * 3))
            >>> print(expr.pretty())
            DIV(
              MUL(ADD(Var(x), Var(y)), SUB(Var(x), Var(y))),
              ADD(MUL(Var(x), (2)), MUL(Var(y), (3)))
            )

        Control line width:

            >>> print(expr.pretty(max_width=40))
            DIV(
              MUL(
                ADD(Var(x), Var(y)),
                SUB(Var(x), Var(y))
              ),
              ADD(
                MUL(Var(x), (2)),
                MUL(Var(y), (3))
              )
            )
    """

    _node: Node

    def __init__(self, node: Node) -> None:
        if not isinstance(node, Node):
            raise TypeError(f"Expected Node, got {type(node).__name__}")
        self._node = node

    # Core metadata and rendering

    def __getattribute__(self, name: str) -> "Expr":
        """Prefer record fields over expression attributes for public names."""
        if _RecordFields.is_field_attribute(name):
            try:
                node = object.__getattribute__(self, "_node")
            except AttributeError:
                pass
            else:
                if isinstance(node.sort, RecordSort) and name in node.sort:
                    return RecordExpr(node)._getitem(name)
        return cast("Expr", object.__getattribute__(self, name))

    @property
    def _(self) -> "_ExprOps":
        """Access expression operations and metadata hidden by record fields."""
        return _ExprOps(self)

    @property
    def node(self) -> Node:
        """Get the underlying AST node."""
        return self._node

    @property
    def sort(self) -> Sort:
        """Get the sort of this expression."""
        return self._node.sort

    @property
    def tag(self) -> "StrExpr":
        """Access the union tag as a string expression."""
        if not isinstance(self._node.sort, UnionSort):
            raise TypeError(f"Called .tag on non-union sort: {self._node.sort}")
        return StrExpr(UnionGetTagNode(self._node))

    def match(
        self,
        default: "Callable[[], Expr] | Expr | int | str | bool | None" = None,
        **cases: "Callable",
    ) -> "Expr":
        """Pattern match on this union. Delegates to :meth:`UnionExpr.match`.

        Exposed on the base class (like ``.tag``) so it is available on any
        union-sorted ``Expr``; the sort is validated at runtime.
        """
        if not isinstance(self._node.sort, UnionSort):
            raise TypeError(f"Called .match on non-union sort: {self._node.sort}")
        return UnionExpr(self._node).match(default, **cases)

    def __repr__(self) -> str:
        return repr(self._node)

    def __str__(self) -> str:
        return str(self._node)

    def __bool__(self) -> bool:
        raise TypeError(
            "Mixing Python Booleans and Wunderspec Booleans is not allowed."
        )

    def pretty(self, max_width: int = 80) -> str:
        """Pretty print this expression.

        Args:
            max_width: Maximum line width for formatting (default: 80).

        Returns:
            A nicely formatted string representation.
        """
        return self._node.pretty(max_width)

    def _repr_pretty_(self, p: Any, cycle: bool) -> None:
        """IPython pretty printing support."""
        self._node._repr_pretty_(p, cycle)

    def __rich__(self) -> Any:
        """rich rendering support (only invoked when rich is installed)."""
        from wunderspec.pretty import to_rich

        return to_rich(self.pretty())

    def _equality_comparison(self, other: object, operation: AlgebraOp) -> "BoolExpr":
        """Build an equality-family expression after checking operand sorts."""
        if isinstance(other, Expr):
            if self._node.sort != other._node.sort:
                raise TypeError(
                    f"Cannot compare expressions of different sorts: {self._node.sort} and {other._node.sort}"
                )
            return BoolExpr(AlgebraNode(BoolSort(), operation, self._node, other._node))

        coerced_other = coerce_expr(other, self._node.sort)
        return BoolExpr(
            AlgebraNode(BoolSort(), operation, self._node, coerced_other._node)
        )

    def __eq__(self, other: object) -> "BoolExpr":  # type: ignore[override]
        """Equality comparison: self == other."""
        return self._equality_comparison(other, AlgebraOp.EQ)

    def __ne__(self, other: object) -> "BoolExpr":  # type: ignore[override]
        """Inequality comparison: self != other."""
        return self._equality_comparison(other, AlgebraOp.NE)

    # Cross-sort convenience methods

    @property
    def is_empty(self) -> "BoolExpr":
        """Check if this collection is empty.

        Supported for sets, maps, and lists:
        - Set: compares against the empty set of the same element sort
        - Map: checks whether the set of keys (DOMAIN) is empty
        - List: compares against the empty list of the same element sort
        """
        sort = self._node.sort
        if isinstance(sort, SetSort):
            return BoolExpr(
                AlgebraNode(
                    BoolSort(), AlgebraOp.EQ, self._node, SetEnumNode(sort.elem_sort)
                )
            )
        if isinstance(sort, MapSort):
            return BoolExpr(
                AlgebraNode(
                    BoolSort(),
                    AlgebraOp.EQ,
                    MapKeysNode(self._node),
                    SetEnumNode(sort.key_sort),
                )
            )
        if isinstance(sort, ListSort):
            return BoolExpr(
                AlgebraNode(
                    BoolSort(), AlgebraOp.EQ, self._node, ListEnumNode(sort.elem_sort)
                )
            )
        raise TypeError(f"is_empty is not supported for sort: {sort}")

    # Set membership is available for all expressions, to keep the static type checker happy.
    def in_(self, set_expr: "Expr") -> "BoolExpr":
        """Set membership: self ∈ set_expr."""
        if not isinstance(set_expr.sort, SetSort):
            raise TypeError(f"Expected SetSort for set_expr, got {set_expr.sort}")
        if self.sort != set_expr.sort.elem_sort:
            raise TypeError(
                f"Element {self} has sort {self.sort}, expected {set_expr.sort.elem_sort}"
            )
        return BoolExpr(InNode(self._node, set_expr._node))

    def if_(self, condition: "Expr | bool") -> "_ConditionalBuilder":
        """Construct an if-then-else expression.

        The condition may be any boolean-sorted ``Expr`` or a raw Python
        ``bool``, which is auto-coerced (the sort is checked at runtime, as with
        ``And``/``Or``).
        """
        cond_expr = cast("BoolExpr", coerce_expr(condition, BoolSort()))
        return _ConditionalBuilder(cond_expr, self)

    # Since the static type checker quickly gets confused when dealing with
    # nested maps and records, we collect all methods (including the overloaded
    # operators) in `Expr`, in addition to more precise methods in subclasses.
    # This way, users can always rely on autocompletion to find the right methods.
    # This approach is still type-safe, as the expression classes check the sorts
    # when building new expressions. Basically, the static type checker serves
    # as the first line of defense and a completion engine, whereas our expression
    # builder is the second line of defense that ensures sort correctness.
    # This is still better than having no static type checking at all, as the IDEs
    # can figure out that they are dealing with expressions, not arbitrary objects.
    #
    # Note that operator overloading works at class-level, so it is not
    # sufficient to define operators only in subclasses.

    # Operator dispatch

    def upto(self, other: "Expr | int") -> "SetExpr":
        """Create an interval set: `{ x in Int : self <= x and x <= other }`."""
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"x.upto(y) requires x to be an integer, got {self.sort}")
        return IntExpr(self._node)._upto(other)

    def __iter__(self) -> "Iterator[Expr]":
        """Iterate over tuple elements, enabling unpacking like (x, y) = tuple_expr.

        For SetExpr/ListExpr inside a generator context (Forall/Exists/Set/SetIf/Map),
        yields a symbolic variable bound to the collection.
        """
        if isinstance(self.sort, (SetSort, ListSort)):
            ctx = _GeneratorContexts.current()
            if ctx is not None:
                if isinstance(self.sort, SetSort):
                    var = ctx.var("_v", self.sort.elem_sort)
                    ctx.bindings.append((var, self))
                    yield var
                    return
                if isinstance(self.sort, ListSort):
                    # Quantify over the index set; bind the element to self[idx].
                    idx = ctx.var("_idx", IntSort())
                    lst = ListExpr(self._node)
                    ctx.bindings.append((idx, lst.keys))
                    yield lst._getitem(idx)
                    return
                raise TypeError(f"Cannot iterate over {self.sort}")
            raise TypeError(
                f"Cannot iterate over symbolic {type(self).__name__} outside "
                f"Forall/Exists/Set/SetIf/Map"
            )

        if not isinstance(self.sort, TupleSort):
            raise TypeError(f"Iteration is only supported over tuples, got {self.sort}")

        for i in range(len(self)):
            yield TupleExpr(self._node)._getitem(i)

    def __add__(self, other: "Expr | int") -> "Expr":
        match self.sort:
            case IntSort():
                return IntExpr(self._node)._add(other)
            case ListSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x + y) requires y to be a list when x is a list, got {type(other).__name__}"
                    )
                return ListExpr(self._node)._concat(other)
            case _:
                raise TypeError(
                    f"(x + y) requires x to be an integer or list, got {self.sort}"
                )

    def __radd__(self, other: "Expr | int") -> "Expr":
        match self.sort:
            case IntSort():
                return IntExpr(coerce_int_node(other))._add(self)
            case ListSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x + y) requires x to be a list when y is a list, got {type(other).__name__}"
                    )
                return ListExpr(other._node)._concat(self)
            case _:
                raise TypeError(
                    f"(x + y) requires y to be an integer or list, got {self.sort}"
                )

    def __sub__(self, other: "Expr | int") -> "Expr":
        match self.sort:
            case IntSort():
                return IntExpr(self._node)._sub(other)
            case SetSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x - y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return SetExpr(self._node)._sub(other)
            case _:
                raise TypeError(
                    f"(x - y) requires x to be an integer or set, got {self.sort}"
                )

    def __rsub__(self, other: "Expr | int") -> "Expr":
        match self.sort:
            case IntSort():
                return IntExpr(coerce_int_node(other))._sub(self)
            case SetSort():
                if not isinstance(other, SetExpr):
                    raise TypeError(
                        f"(x - y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return other._sub(self)
            case _:
                raise TypeError(
                    f"(x - y) requires x to be an integer or set, got {self.sort}"
                )

    def __mul__(self, other: "Expr | int") -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(x * y) requires x to be an integer, got {self.sort}")
        return IntExpr(self._node)._mul(other)

    def __rmul__(self, other: "Expr | int") -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(x * y) requires y to be an integer, got {self.sort}")
        return IntExpr(coerce_int_node(other))._mul(self)

    def __truediv__(self, other: "Expr | int") -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(x / y) requires x to be an integer, got {self.sort}")
        return IntExpr(self._node)._div(other)

    def __rtruediv__(self, other: "Expr | int") -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(x / y) requires y to be an integer, got {self.sort}")
        return IntExpr(coerce_int_node(other))._div(self)

    def __mod__(self, other: "Expr | int") -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(x % y) requires x to be an integer, got {self.sort}")
        return IntExpr(self._node)._mod(other)

    def __rmod__(self, other: "Expr | int") -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(x % y) requires y to be an integer, got {self.sort}")
        return IntExpr(coerce_int_node(other))._mod(self)

    def __pow__(self, other: "Expr | int") -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(x ** y) requires x to be an integer, got {self.sort}")
        return IntExpr(self._node)._pow(other)

    def __rpow__(self, other: "Expr | int") -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(x ** y) requires y to be an integer, got {self.sort}")
        return IntExpr(coerce_int_node(other))._pow(self)

    def __neg__(self) -> "IntExpr":
        if not isinstance(self.sort, IntSort):
            raise TypeError(f"(-x) requires x to be an integer, got {self.sort}")
        return IntExpr(self._node)._neg()

    def __lt__(self, other: "Expr | int") -> "BoolExpr":
        match self.sort:
            case IntSort():
                return IntExpr(self._node)._lt(other)
            case SetSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x < y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return SetExpr(self._node)._lt(other)
            case _:
                raise TypeError(
                    f"(x < y) requires x to be an integer or set, got {self.sort}"
                )

    def __le__(self, other: "Expr | int") -> "BoolExpr":
        match self.sort:
            case IntSort():
                return IntExpr(self._node)._le(other)
            case SetSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x <= y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return SetExpr(self._node)._le(other)
            case _:
                raise TypeError(
                    f"(x <= y) requires x to be an integer or set, got {self.sort}"
                )

    def __gt__(self, other: "Expr | int") -> "BoolExpr":
        match self.sort:
            case IntSort():
                return IntExpr(self._node)._gt(other)
            case SetSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x > y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return SetExpr(self._node)._gt(other)
            case _:
                raise TypeError(
                    f"(x > y) requires x to be an integer or set, got {self.sort}"
                )

    def __ge__(self, other: "Expr | int") -> "BoolExpr":
        match self.sort:
            case IntSort():
                return IntExpr(self._node)._ge(other)
            case SetSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x >= y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return SetExpr(self._node)._ge(other)
            case _:
                raise TypeError(
                    f"(x >= y) requires x to be an integer or set, got {self.sort}"
                )

    def __and__(self, other: "Expr | bool") -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(self._node)._and(other)
            case TemporalSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x & y) requires y to be an expression of BoolSort or TemporalSort when x is temporal, got {type(other).__name__}"
                    )
                return TemporalExpr(self._node)._and(other)
            case SetSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x & y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return SetExpr(self._node)._and(other)
            case _:
                raise TypeError(
                    f"(x & y) requires x to be a boolean or set, got {self.sort}"
                )

    def __rand__(self, other: "Expr | bool") -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(coerce_bool_node(other))._and(BoolExpr(self._node))
            case TemporalSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x & y) requires y to be an expression of BoolSort or TemporalSort when x is temporal, got {type(other).__name__}"
                    )
                return TemporalExpr(other._node)._and(self)
            case SetSort():
                if not isinstance(other, SetExpr):
                    raise TypeError(
                        f"(x & y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return other._and(self)
            case _:
                raise TypeError(
                    f"(x & y) requires x to be a boolean or set, got {self.sort}"
                )

    def __or__(self, other: "Expr | bool") -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(self._node)._or(other)
            case TemporalSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x | y) requires y to be an expression of BoolSort or TemporalSort when x is temporal, got {type(other).__name__}"
                    )
                return TemporalExpr(self._node)._or(other)
            case SetSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x | y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return SetExpr(self._node)._or(other)
            case _:
                raise TypeError(
                    f"(x | y) requires x to be a boolean or set, got {self.sort}"
                )

    def __ror__(self, other: "Expr | bool") -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(coerce_bool_node(other))._or(BoolExpr(self._node))
            case TemporalSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"(x | y) requires y to be an expression of BoolSort or TemporalSort when x is temporal, got {type(other).__name__}"
                    )
                return TemporalExpr(other._node)._or(self)
            case SetSort():
                if not isinstance(other, SetExpr):
                    raise TypeError(
                        f"(x | y) requires y to be a set when x is a set, got {type(other).__name__}"
                    )
                return other._or(self)
            case _:
                raise TypeError(
                    f"(x | y) requires x to be a boolean or set, got {self.sort}"
                )

    def __invert__(self) -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(self._node)._invert()
            case TemporalSort():
                return TemporalExpr(self._node)._invert()
            case _:
                raise TypeError(
                    f"(~x) requires x to be a Boolean or Temporal expression, got {self.sort}"
                )

    def and_(self, other: "Expr | bool") -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(self._node)._and(other)
            case TemporalSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"p.and_(...) requires p to be an expression of BoolSort or TemporalSort when p is temporal, got {type(other).__name__}"
                    )
                return TemporalExpr(self._node)._and(other)
            case _:
                raise TypeError(
                    f"p.and_(...) requires p to be a Boolean or Temporal expression, got {self.sort}"
                )

    def or_(self, other: "Expr | bool") -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(self._node)._or(other)
            case TemporalSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"p.or_(...) requires p to be an expression of BoolSort or TemporalSort when p is temporal, got {type(other).__name__}"
                    )
                return TemporalExpr(self._node)._or(other)
            case _:
                raise TypeError(
                    f"p.or_(...) requires p to be a Boolean or Temporal expression, got {self.sort}"
                )

    def not_(self) -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(self._node).not_()
            case TemporalSort():
                return TemporalExpr(self._node).not_()
            case _:
                raise TypeError(
                    f"p.not_(...) requires p to be a Boolean or Temporal expression, got {self.sort}"
                )

    def implies(self, other: "Expr | bool") -> "Expr":
        match self.sort:
            case BoolSort():
                return BoolExpr(self._node).implies(other)
            case TemporalSort():
                if not isinstance(other, Expr):
                    raise TypeError(
                        f"p.implies(...) requires p to be an expression of BoolSort or TemporalSort when p is temporal, got {type(other).__name__}"
                    )
                return TemporalExpr(self._node).implies(other)
            case _:
                raise TypeError(
                    f"p.implies(...) requires p to be a Boolean or Temporal expression, got {self.sort}"
                )

    def issubset(self, other: "Expr") -> "BoolExpr":
        """Subset or equal: self ⊆ other."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.issubset(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).issubset(other)

    # Sort-specific method dispatch

    def filter(self, predicate: Callable[["Expr"], "Expr"]) -> "Expr":
        """Filter set or list elements by predicate."""
        if isinstance(self.sort, SetSort):
            return SetExpr(self._node).filter(predicate)
        elif isinstance(self.sort, ListSort):
            return ListExpr(self._node).filter(predicate)
        else:
            raise TypeError(
                f"s.filter(...) requires s to be a Set or List, got {self.sort}"
            )

    def map(self, mapper: Callable[["Expr"], "Expr"]) -> "SetExpr":
        """Map over set: { f(x) : x ∈ self }."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.map(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).map(mapper)

    def map_to(self, mapper: Callable[["Expr"], "Expr"]) -> "MapExpr":
        """Create a map: [ x ∈ self |-> mapper ]."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.map_to(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).map_to(mapper)

    @overload
    def forall(self, predicate: Callable[["Expr"], "BoolExpr"]) -> "BoolExpr": ...

    @overload
    def forall(
        self, predicate: Callable[["Expr"], "TemporalExpr"]
    ) -> "TemporalExpr": ...

    def forall(self, predicate: Callable) -> "Expr":
        """Universal quantification: ∀x ∈ self : P(x)."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.forall(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).forall(predicate)  # type: ignore[no-any-return]

    @overload
    def exists(self, predicate: Callable[["Expr"], "BoolExpr"]) -> "BoolExpr": ...

    @overload
    def exists(
        self, predicate: Callable[["Expr"], "TemporalExpr"]
    ) -> "TemporalExpr": ...

    def exists(self, predicate: Callable) -> "Expr":
        """Existential quantification: ∃x ∈ self : P(x)."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.exists(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).exists(predicate)  # type: ignore[no-any-return]

    def reduce(self, function: Callable[..., "Expr"], initial: "Expr") -> "Expr":
        if isinstance(self.sort, SetSort):
            return SetExpr(self._node).reduce(function, initial)
        elif isinstance(self.sort, ListSort):
            return ListExpr(self._node).reduce(function, initial)
        elif isinstance(self.sort, MapSort):
            return MapExpr(self._node).reduce(function, initial)
        else:
            raise TypeError(
                f"s.reduce(...) requires s to be a Set, List, or Map, got {self.sort}"
            )

    @property
    def size(self) -> "IntExpr":
        """Get the cardinality (size) of the finite set or list."""
        if isinstance(self.sort, SetSort):
            return SetExpr(self._node).size
        elif isinstance(self.sort, ListSort):
            return ListExpr(self._node).size
        else:
            raise TypeError(f"s.size requires s to be a Set or List, got {self.sort}")

    @property
    def flattened(self) -> "SetExpr":
        """Flatten a set of sets: ⋃ self."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.flattened requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).flattened

    def choose(self, predicate: Callable[["Expr"], "BoolExpr"]) -> "Expr":
        """Choose an element: CHOOSE x ∈ self : P(x)."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.choose(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).choose(predicate)

    @property
    def keys(self) -> "SetExpr":
        """Return the set of keys in this map or list indices.

        Example:
            account_ids = balances.keys
            indices = my_list.keys
        """
        if isinstance(self.sort, MapSort):
            return MapExpr(self._node).keys
        elif isinstance(self.sort, ListSort):
            return ListExpr(self._node).keys
        else:
            raise TypeError(f"m.keys requires m to be a Map or List, got {self.sort}")

    @property
    def values(self) -> "SetExpr":
        """Return the set of values in this map.

        Example:
            balances_seen = balances.values
        """
        if isinstance(self.sort, MapSort):
            return MapExpr(self._node).values
        else:
            raise TypeError(f"m.values requires m to be a Map, got {self.sort}")

    @property
    def items(self) -> "SetExpr":
        """Return the set of key-value pairs in this map, as 2-element tuples.

        Example:
            balances_items = balances.items
        """
        if isinstance(self.sort, MapSort):
            return MapExpr(self._node).items
        else:
            raise TypeError(f"m.items requires m to be a Map, got {self.sort}")

    # Indexing and functional updates

    def __getitem__(self, key: "Expr | int | bool | str | slice") -> "Expr":
        match self._node.sort:
            case MapSort():
                return MapExpr(self._node)._getitem(coerce_expr(key))
            case TupleSort():
                if isinstance(key, (str, bool, slice)):
                    raise TypeError(
                        f"x[y] requires y to be an integer when x is a Tuple, got {type(key).__name__}"
                    )
                return TupleExpr(self._node)._getitem(key)
            case RecordSort():
                if not isinstance(key, str):
                    raise TypeError(
                        f"x[y] requires y to be a string when x is a Record, got {type(key).__name__}"
                    )
                return RecordExpr(self._node)._getitem(key)
            case ListSort():
                if isinstance(key, slice):
                    return ListExpr(self._node)._slice(key)
                if isinstance(key, (str, bool)):
                    raise TypeError(
                        f"x[y] requires y to be an integer when x is a List, got {type(key).__name__}"
                    )
                return ListExpr(self._node)._getitem(key)
            case _:
                raise TypeError(
                    f"x[y] requires x to be a Map, Tuple, Record, or List, got {self._node.sort}"
                )

    def __setitem__(
        self, key: "Expr | int | bool | str | slice", value: object
    ) -> None:
        raise TypeError(
            "Assignment to x[y] is only supported on state variables. "
            "Use x.edit() for expression updates."
        )

    def __getattr__(self, field_name: str) -> "Expr":
        if isinstance(self._node.sort, RecordSort):
            return RecordExpr(self._node)._getitem(field_name)
        raise AttributeError(f"{type(self).__name__} has no attribute '{field_name}'")

    if TYPE_CHECKING:
        # Mirror the permissive read side (``__getattr__``) on the write side so
        # that idiomatic record-field assignments through an ``Expr`` (e.g.
        # ``s.replica_state[id].field = value``) type-check. As with reads, the
        # static checker cannot see record fields, so sorts are validated at
        # runtime. Type-checking only: at runtime ``Expr`` uses the default
        # ``object.__setattr__`` so internal attribute writes are unaffected.
        def __setattr__(self, name: str, value: object) -> None: ...

    def replace(
        self,
        key: "Expr | str | int | None" = None,
        value: "Expr | None" = None,
        **fields: "Expr",
    ) -> "Expr":
        """Functional replace: Depending on the sort, create a new map, record, tuple,
        or list with the value at the specified key/index updated.

        For maps: If there is no such key, no new entry is added.
        Tools may report an error if the key does not exist (if they can detect that).

        Example for maps:

            >>> from wunderspec import *
            >>> balances = Set('alice', 'bob').map_to(lambda x: Val(100))
            >>> new_map = balances.replace('bob', Val(1000))

        Example for tuples:

            >>> pair = Tuple(Val(1), Val(2))
            >>> new_pair = pair.replace(0, Val(99))
        """
        match self._node.sort:
            case MapSort():
                if fields:
                    raise TypeError(
                        "x.replace(...) with key-value pairs is not supported for Maps"
                    )
                if key is None or value is None:
                    raise TypeError(
                        "x.replace(key, value) requires both key and value for Maps"
                    )
                return MapExpr(self._node).replace(coerce_expr(key), value)
            case TupleSort():
                if fields:
                    raise TypeError(
                        "x.replace(...) with key-value pairs is not supported for Tuples"
                    )
                if key is None or value is None:
                    raise TypeError(
                        "x.replace(index, value) requires both index and value for Tuples"
                    )
                if not isinstance(key, int):
                    raise TypeError(
                        f"x.replace(...) requires index to be an int when x is a Tuple, got {type(key).__name__}"
                    )
                return TupleExpr(self._node).replace(key, value)
            case ListSort():
                if fields:
                    raise TypeError(
                        "x.replace(...) with key-value pairs is not supported for Lists"
                    )
                if key is None or value is None:
                    raise TypeError(
                        "x.replace(index, value) requires both index and value for Lists"
                    )
                if isinstance(key, str):
                    raise TypeError(
                        "x.replace(...) requires index to be an int or IntExpr"
                        " when x is a List, got str"
                    )
                return ListExpr(self._node).replace(key, value)
            case RecordSort():
                if key is not None and value is not None:
                    if not isinstance(key, str):
                        raise TypeError(
                            f"x.replace(...) requires key to be a string when x is a Record, got {type(key).__name__}"
                        )
                    fields = {key: value, **fields}
                elif key is not None or value is not None:
                    raise TypeError(
                        "x.replace(...) for Records requires either (key, value) or **fields, not a mix of one positional and keyword args"
                    )
                if not fields:
                    raise TypeError(
                        "x.replace(...) for Records requires at least one field to update"
                    )
                return RecordExpr(self._node).replace(**fields)
            case _:
                raise TypeError(
                    f"x.replace(...) requires x to be a Map, Tuple, List, or Record, got {self._node.sort}"
                )

    def edit(
        self, name_prefix: str = "_tmp", replace_only: bool = False
    ) -> "UpdatesBuilder":
        """Get a builder for in-place updates.

        Usage:
            upd = expr.edit()
            upd.field = new_value
            upd[key] = new_value
            result = upd.result
        """
        match self._node.sort:
            case MapSort():
                pass
            case TupleSort():
                pass
            case RecordSort():
                pass
            case ListSort():
                pass
            case _:
                raise TypeError(
                    f"x.edit(...) requires x to be a Map, Tuple, Record, or List, got {self._node.sort}"
                )

        ctx = UpdateContext(self, name_prefix, replace_only)
        return UpdatesBuilder(ctx, (), expr_from_node(ctx.updated_node))

    def contains(self, elem: "Expr | bool | int | str") -> "BoolExpr":
        """Check membership: elem ∈ self."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.contains(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).contains(elem)

    def union(self, other: "Expr") -> "SetExpr":
        """Set union: self ∪ other."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.union(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).union(other)

    def intersect(self, other: "Expr") -> "SetExpr":
        """Set intersection: self ∩ other."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(f"s.intersect(...) requires s to be a Set, got {self.sort}")
        return SetExpr(self._node).intersect(other)

    def difference(self, other: "Expr") -> "SetExpr":
        r"""Set difference: self \ other."""
        if not isinstance(self.sort, SetSort):
            raise TypeError(
                f"s.difference(...) requires s to be a Set, got {self.sort}"
            )
        return SetExpr(self._node).difference(other)

    def insert(self, key: "Expr", value: "Expr") -> "MapExpr":
        """Functional insert/update: create a new map with the value of a specified
        key inserted or updated. If there is no such key, new entry is added.

        Returns a new MapExpr with the updates on top of the old one.

        Example:
            new_map = balances.insert(account_id, Val(1000))
        """
        if not isinstance(self.sort, MapSort):
            raise TypeError(f"m.insert(...) requires m to be a Map, got {self.sort}")
        return MapExpr(self._node).insert(key, value)

    def __len__(self) -> int:
        """Get the number of elements in this tuple."""
        if not isinstance(self._node.sort, TupleSort):
            raise TypeError(f"len(t) requires t to be a Tuple, got {self._node.sort}")
        return len(self._node.sort.elem_sorts)


# =============================================================================
# Integer expressions
# =============================================================================


class IntExpr(Expr):
    """Integer expression with arithmetic operators."""

    def __dir__(self) -> list[str]:
        """Provide explicit list of attributes for better tab completion in IPython."""
        return _Completion.names(
            "if_",
            "in_",
            "upto",
            operators=(
                "__add__",
                "__radd__",
                "__sub__",
                "__rsub__",
                "__mul__",
                "__rmul__",
                "__truediv__",
                "__rtruediv__",
                "__mod__",
                "__rmod__",
                "__pow__",
                "__rpow__",
                "__neg__",
            ),
        )

    @property
    def value(self) -> int:
        """Get the value of an integer literal.

        Raises AttributeError if this expression is not a literal.
        """
        if isinstance(self.node, LitNode):
            val = self.node.value
            assert isinstance(val, int) and not isinstance(val, bool)
            return val
        raise AttributeError(f"{type(self.node).__name__} has no 'value' attribute")

    def _upto(self, other: "Expr | int") -> "SetExpr":
        """Create an interval set: `{ x in Int : self <= x and x <= other }`."""
        return SetExpr(IntervalNode(self._node, coerce_int_node(other)))

    def _binary_operation(self, operation: AlgebraOp, other: "Expr | int") -> "IntExpr":
        other_node = coerce_int_node(other)
        return IntExpr(AlgebraNode(IntSort(), operation, self._node, other_node))

    def _comparison_operation(
        self, operation: AlgebraOp, other: "Expr | int"
    ) -> "BoolExpr":
        other_node = coerce_int_node(other)
        return BoolExpr(AlgebraNode(BoolSort(), operation, self._node, other_node))

    def _add(self, other: "Expr | int") -> "IntExpr":
        return self._binary_operation(AlgebraOp.ADD, other)

    def _sub(self, other: "Expr | int") -> "IntExpr":
        return self._binary_operation(AlgebraOp.SUB, other)

    def _mul(self, other: "Expr | int") -> "IntExpr":
        return self._binary_operation(AlgebraOp.MUL, other)

    def _div(self, other: "Expr | int") -> "IntExpr":
        return self._binary_operation(AlgebraOp.DIV, other)

    def _mod(self, other: "Expr | int") -> "IntExpr":
        return self._binary_operation(AlgebraOp.MOD, other)

    def _pow(self, other: "Expr | int") -> "IntExpr":
        return self._binary_operation(AlgebraOp.POW, other)

    def _neg(self) -> "IntExpr":
        return IntExpr(AlgebraNode(IntSort(), AlgebraOp.NEG, self._node))

    # Comparison operators
    def _lt(self, other: "Expr | int") -> "BoolExpr":
        return self._comparison_operation(AlgebraOp.LT, other)

    def _le(self, other: "Expr | int") -> "BoolExpr":
        return self._comparison_operation(AlgebraOp.LE, other)

    def _gt(self, other: "Expr | int") -> "BoolExpr":
        return self._comparison_operation(AlgebraOp.GT, other)

    def _ge(self, other: "Expr | int") -> "BoolExpr":
        return self._comparison_operation(AlgebraOp.GE, other)


# =============================================================================
# Boolean expressions
# =============================================================================


class BoolExpr(Expr):
    """Boolean expression with logical operators."""

    def __dir__(self) -> list[str]:
        """Provide explicit list of attributes for better tab completion in IPython."""
        return _Completion.names(
            "and_",
            "if_",
            "implies",
            "in_",
            "not_",
            "or_",
            operators=(
                "__and__",
                "__rand__",
                "__or__",
                "__ror__",
                "__invert__",
            ),
        )

    @property
    def value(self) -> bool:
        """Get the value of a boolean literal.

        Raises AttributeError if this expression is not a literal.
        """
        if isinstance(self._node, LitNode):
            val = self._node.value
            assert isinstance(val, bool)
            return val
        raise AttributeError(f"{type(self._node).__name__} has no 'value' attribute")

    def _and(self, other: "Expr | bool") -> "BoolExpr":
        other_node = coerce_bool_node(other)
        return BoolExpr(AlgebraNode(BoolSort(), AlgebraOp.AND, self._node, other_node))

    def _or(self, other: "Expr | bool") -> "BoolExpr":
        other_node = coerce_bool_node(other)
        return BoolExpr(AlgebraNode(BoolSort(), AlgebraOp.OR, self._node, other_node))

    def _invert(self) -> "BoolExpr":
        return BoolExpr(AlgebraNode(BoolSort(), AlgebraOp.NOT, self._node))

    def __and__(self, other: "Expr | bool") -> "BoolExpr":
        return self._and(other)

    def __rand__(self, other: "Expr | bool") -> "BoolExpr":
        return BoolExpr(coerce_bool_node(other))._and(self)

    def __or__(self, other: "Expr | bool") -> "BoolExpr":
        return self._or(other)

    def __ror__(self, other: "Expr | bool") -> "BoolExpr":
        return BoolExpr(coerce_bool_node(other))._or(self)

    def __invert__(self) -> "BoolExpr":
        return self._invert()

    def _flattened_operation(
        self, operation: AlgebraOp, others: tuple["Expr | bool", ...]
    ) -> "BoolExpr":
        """Build a variadic Boolean operation, extending an existing one."""
        new_nodes = [coerce_bool_node(other) for other in others]
        if isinstance(self._node, AlgebraNode) and self._node.op == operation:
            nodes = [*self._node.args, *new_nodes]
        else:
            nodes = [self._node, *new_nodes]
        return BoolExpr(AlgebraNode(BoolSort(), operation, *nodes))

    def and_(self, *others: "Expr | bool") -> "BoolExpr":
        """Logical AND: self ∧ others. Flattens nested ANDs."""
        return self._flattened_operation(AlgebraOp.AND, others)

    def or_(self, *others: "Expr | bool") -> "BoolExpr":
        """Logical OR: self ∨ others. Flattens nested ORs."""
        return self._flattened_operation(AlgebraOp.OR, others)

    def not_(self) -> "BoolExpr":
        """Logical NOT: ¬self."""
        if not isinstance(self.sort, BoolSort):
            raise TypeError(f"not_() requires a Boolean expression, got {self.sort}")
        return BoolExpr(AlgebraNode(BoolSort(), AlgebraOp.NOT, self._node))

    def implies(self, other: "Expr | bool") -> "BoolExpr":
        """Logical implication: self → other."""
        other_node = coerce_bool_node(other)
        return BoolExpr(
            AlgebraNode(BoolSort(), AlgebraOp.IMPLIES, self._node, other_node)
        )


# =============================================================================
# Temporal formulas
# =============================================================================


class TemporalExpr(Expr):
    """
    Temporal formulas. They share many operators with `BoolExpr`.
    Nevertheless, they are a separate class to avoid confusion between Boolean
    expressions and temporal formulas. TemporalExpr consumes expressions of
    `BoolSort` or `TemporalSort` in its operators and produces expressions of
    `TemporalSort`.

    In contrast to `BoolExpr`, we do not allow mixing `bool` literals with
    `TemporalExpr` in the operators, as this could lead to confusion.

    NOTE: we do not expose `eventually`, `always`, `weak_fair`, and `strong_fair`
    for tab completion, as their postfix form is very non-standard. Use
    `Eventually(...)`, `Always(...)`, etc. of `lang` module instead.
    You can still call the postfix methods directly if you want to.
    """

    def __dir__(self) -> list[str]:
        """Provide explicit list of attributes for better tab completion in IPython."""
        return _Completion.names(
            "and_",
            "if_",
            "implies",
            "in_",
            "not_",
            "or_",
            operators=(
                "__and__",
                "__rand__",
                "__or__",
                "__ror__",
                "__invert__",
            ),
        )

    @property
    def value(self) -> bool:
        """Get the value of a boolean literal.

        Raises AttributeError if this expression is not a literal.
        """
        if isinstance(self._node, LitNode):
            val = self._node.value
            assert isinstance(val, bool)
            return val
        raise AttributeError(f"{type(self._node).__name__} has no 'value' attribute")

    def _and(self, other: Expr) -> "TemporalExpr":
        return TemporalExpr(
            AlgebraNode(TemporalSort(), AlgebraOp.AND, self._node, other._node)
        )

    def _or(self, other: Expr) -> "TemporalExpr":
        return TemporalExpr(
            AlgebraNode(TemporalSort(), AlgebraOp.OR, self._node, other._node)
        )

    @staticmethod
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

    def _invert(self) -> "TemporalExpr":
        return TemporalExpr(AlgebraNode(TemporalSort(), AlgebraOp.NOT, self._node))

    def __and__(self, other: "Expr | bool") -> "TemporalExpr":
        return self._and(
            other if isinstance(other, Expr) else BoolExpr(coerce_bool_node(other))
        )

    def __or__(self, other: "Expr | bool") -> "TemporalExpr":
        return self._or(
            other if isinstance(other, Expr) else BoolExpr(coerce_bool_node(other))
        )

    def __invert__(self) -> "TemporalExpr":
        return self._invert()

    def _lifted_operation(
        self, operation: AlgebraOp, other: Expr | bool
    ) -> "TemporalExpr":
        """Build a temporal operation after lifting Boolean operands."""
        other_node: Node
        if isinstance(other, bool):
            other_node = ToTemporalNode(LitNode(other))
        else:
            other_node = self._lift_to_temporal(other._node)
        self_node = self._lift_to_temporal(self._node)
        return TemporalExpr(
            AlgebraNode(TemporalSort(), operation, self_node, other_node)
        )

    def and_(self, other: Expr | bool) -> "TemporalExpr":
        """Logical AND: self ∧ other."""
        return self._lifted_operation(AlgebraOp.AND, other)

    def or_(self, other: Expr | bool) -> "TemporalExpr":
        """Logical OR: self ∨ other."""
        return self._lifted_operation(AlgebraOp.OR, other)

    def not_(self) -> "TemporalExpr":
        """Logical NOT: ¬self."""
        self_node = self._lift_to_temporal(self._node)
        return TemporalExpr(AlgebraNode(TemporalSort(), AlgebraOp.NOT, self_node))

    def implies(self, other: Expr | bool) -> "TemporalExpr":
        """Logical implication: self → other."""
        return self._lifted_operation(AlgebraOp.IMPLIES, other)

    def eventually(self) -> "TemporalExpr":
        """Eventually operator: ◇self."""
        return TemporalExpr(EventuallyNode(self._node))

    def always(self) -> "TemporalExpr":
        """Always operator: □self."""
        return TemporalExpr(AlwaysNode(self._node))

    # weak_fair, strong_fair, and enabled do not belong here,
    # as they operate on actions, not on temporal formulas.
    # Look for them in `wunderspec.lang`.


# =============================================================================
# String expressions
# =============================================================================


class StrExpr(Expr):
    """String expression."""


# =============================================================================
# Enum expressions
# =============================================================================


class EnumExpr(Expr):
    """Enum expression for user-defined enum types."""

    @property
    def enum_type(self) -> Type:
        """Get the enum type of this expression."""
        return self._node.sort.enum_type  # type: ignore[return-value,attr-defined,no-any-return]

    @property
    def value(self) -> Any:
        """Get the value of an enum literal.

        Raises AttributeError if this expression is not a literal.
        """
        if isinstance(self.node, LitNode):
            return self.node.value  # type: ignore[return-value]
        raise AttributeError(f"{type(self.node).__name__} has no 'value' attribute")


# =============================================================================
# Tuple expressions
# =============================================================================


class TupleExpr(Expr):
    """Tuple expression with element access and update operations."""

    def __init__(self, node: Node):
        assert isinstance(
            node.sort, TupleSort
        ), f"Expected TupleSort, got {type(node.sort).__name__}"
        super().__init__(node)

    def __len__(self) -> int:
        """Get the number of elements in this tuple."""
        return len(self.node.sort.elem_sorts)  # type: ignore[attr-defined]

    def __iter__(self) -> "Iterator[Expr]":
        """Iterate over tuple elements, enabling unpacking like (x, y) = tuple_expr."""
        for i in range(len(self)):
            yield self._getitem(i)

    def _getitem(self, index: Expr | int) -> "Expr":
        """Element access: self[index]."""
        if isinstance(index, IntExpr):
            node = TupleGetNode(self.node, index.value)
        elif isinstance(index, int):
            node = TupleGetNode(self.node, index)
        else:
            raise TypeError(f"Expected IntExpr or int, got {type(index).__name__}")
        return expr_from_node(node)

    def replace(self, index: int, value) -> "TupleExpr":  # type: ignore[override]
        """Functional update: create a new tuple with element at index updated.

        Returns a new TupleExpr with the update applied.

        Example:
            new_pair = pair.replace(0, Val(99))
        """
        if not isinstance(value, Expr):
            assert isinstance(self.node.sort, TupleSort)
            value = coerce_expr(value, self.node.sort[index])
        updated_node = TupleUpdateNode(self.node, index, value.node)
        return TupleExpr(updated_node)


# =============================================================================
# Record expressions
# =============================================================================


class RecordExpr(Expr):
    """Record expression with field access and update operations."""

    def _getitem(self, field_name: str) -> "Expr":
        """Field access: self[field_name] or self.field_name."""
        node = RecordGetNode(self._node, field_name)
        return expr_from_node(node)

    def __getattr__(self, field_name: str) -> Expr:
        """Field access via dot notation: self.field_name."""
        # Avoid infinite recursion for special attributes
        if field_name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{field_name}'"
            )
        try:
            return self[field_name]
        except (TypeError, KeyError) as e:
            raise AttributeError(str(e)) from e

    def replace(self, **fields) -> "RecordExpr":  # type: ignore[override]
        """Functional update: create a new record with specified fields updated.

        Returns a new RecordExpr with the updates applied.

        Example:
            new_person = person.replace(age=Val(31), active=Val(False))
        """
        record_sort = self._node.sort
        assert isinstance(record_sort, RecordSort)
        field_nodes = {}
        for name, expr in fields.items():
            if not isinstance(expr, Expr):
                expr = coerce_expr(expr, record_sort[name])
            field_nodes[name] = expr._node
        updated_node = RecordUpdateNode(self._node, **field_nodes)
        return RecordExpr(updated_node)


# =============================================================================
# Set expressions
# =============================================================================


class SetExpr(Expr):
    """Set expression with set operators."""

    def __init__(self, node: Node):
        assert isinstance(
            node.sort, SetSort
        ), f"Expected SetSort, got {type(node.sort).__name__}"
        super().__init__(node)

    def __dir__(self) -> list[str]:
        """Provide explicit list of attributes for better tab completion in IPython."""
        return _Completion.names(
            "flattened",
            "is_empty",
            "size",
            "choose",
            "contains",
            "difference",
            "exists",
            "filter",
            "forall",
            "if_",
            "in_",
            "intersect",
            "map",
            "reduce",
            "union",
            operators=("__and__", "__or__", "__sub__"),
        )

    @property
    def elem_sort(self) -> Sort:
        """Get the element sort of this set."""
        return self._node.sort.elem_sort  # type: ignore[return-value,attr-defined,no-any-return]

    def contains(self, elem: Expr | bool | int | str) -> BoolExpr:
        """Check membership: elem ∈ self."""
        elem_expr = coerce_expr(elem, self.elem_sort)
        return BoolExpr(InNode(elem_expr.node, self.node))

    def _set_algebra(self, operation: AlgebraOp, other: "Expr") -> "SetExpr":
        """Build a binary set operation after validating element sorts."""
        self._check_elem_sort(other)
        return SetExpr(AlgebraNode(self.node.sort, operation, self.node, other.node))

    def union(self, other: "Expr") -> "SetExpr":
        """Set union: self ∪ other."""
        return self._set_algebra(AlgebraOp.UNION, other)

    def intersect(self, other: "Expr") -> "SetExpr":
        """Set intersection: self ∩ other."""
        return self._set_algebra(AlgebraOp.INTERSECT, other)

    def difference(self, other: "Expr") -> "SetExpr":
        r"""Set difference: self \ other."""
        return self._set_algebra(AlgebraOp.DIFFERENCE, other)

    def issubset(self, other: "Expr") -> "BoolExpr":
        """Subset or equal: self ⊆ other."""
        self._check_elem_sort(other)
        return BoolExpr(
            AlgebraNode(BoolSort(), AlgebraOp.SUBSETEQ, self.node, other.node)
        )

    def _or(self, other: "Expr") -> "SetExpr":
        return self.union(other)

    def _and(self, other: "Expr") -> "SetExpr":
        return self.intersect(other)

    def _sub(self, other: "Expr") -> "SetExpr":
        return self.difference(other)

    def __and__(self, other: "Expr | bool") -> "SetExpr":  # type: ignore[override]
        return self._and(other)  # type: ignore[arg-type]

    def __or__(self, other: "Expr | bool") -> "SetExpr":  # type: ignore[override]
        return self._or(other)  # type: ignore[arg-type]

    def __sub__(self, other: "Expr | int") -> "SetExpr":  # type: ignore[override]
        return self._sub(other)  # type: ignore[arg-type]

    def _le(self, other: "Expr") -> "BoolExpr":
        return self.issubset(other)

    def _lt(self, other: "Expr") -> "BoolExpr":
        return self.issubset(other).and_(~(self == other))

    def _ge(self, other: "Expr") -> "BoolExpr":
        return other.issubset(self)

    def _gt(self, other: "Expr") -> "BoolExpr":
        return other.issubset(self).and_(~(self == other))

    def filter(self, predicate: Callable[["Expr"], "Expr"]) -> "SetExpr":
        """Filter set: { x ∈ self : P(x) }."""
        var, var_node = _Binders.fresh_unary(predicate, self.elem_sort)
        pred_expr = predicate(var)
        return SetExpr(
            SetFilterNode(
                self.node,
                var_node,
                pred_expr.node,
            )
        )

    def map(self, mapper: Callable[["Expr"], "Expr"]) -> "SetExpr":
        """Map over set: { f(x) : x ∈ self }."""
        var, var_node = _Binders.fresh_unary(mapper, self.elem_sort)
        mapped_expr = mapper(var)
        return SetExpr(
            SetMapNode(
                self._node,
                var_node,
                mapped_expr._node,
            )
        )

    def map_to(self, mapper: Callable[["Expr"], "Expr"]) -> "MapExpr":
        """Create a map: [ x ∈ self |-> mapper ]."""
        var, var_node = _Binders.fresh_unary(mapper, self.elem_sort)
        mapped_expr = mapper(var)
        if not isinstance(mapped_expr, Expr):
            mapped_expr = coerce_expr(mapped_expr)
        return MapExpr(
            MapLambdaNode(
                self.node,
                var_node,
                mapped_expr.node,
            )
        )

    @overload
    def forall(self, predicate: Callable[["Expr"], "BoolExpr"]) -> "BoolExpr": ...

    @overload
    def forall(
        self, predicate: Callable[["Expr"], "TemporalExpr"]
    ) -> "TemporalExpr": ...

    def forall(self, predicate: Callable) -> "Expr":
        """Universal quantification: ∀x ∈ self : P(x)."""
        return self._quantify(QuantOp.FORALL, predicate)

    @overload
    def exists(self, predicate: Callable[["Expr"], "BoolExpr"]) -> "BoolExpr": ...

    @overload
    def exists(
        self, predicate: Callable[["Expr"], "TemporalExpr"]
    ) -> "TemporalExpr": ...

    def exists(self, predicate: Callable) -> "Expr":
        """Existential quantification: ∃x ∈ self : P(x)."""
        return self._quantify(QuantOp.EXISTS, predicate)

    def _quantify(self, operation: QuantOp, predicate: Callable[..., Any]) -> "Expr":
        """Build a set quantifier and wrap it according to the predicate sort."""
        var, var_node = _Binders.fresh_unary(predicate, self.elem_sort)
        pred_expr = predicate(var)
        quant_node = SetQuantNode(
            operation,
            self.node,
            var_node,
            pred_expr.node,
        )
        match pred_expr.sort:
            case BoolSort():
                return BoolExpr(quant_node)
            case TemporalSort():
                return TemporalExpr(quant_node)
            case _:
                raise TypeError(
                    f"Predicate must return BoolExpr or TemporalExpr, got {pred_expr.sort}"
                )

    def reduce(
        self,
        function: Callable[[Expr, Expr], Expr],
        initial: "Expr | int | str | bool | Enum",
    ) -> Expr:
        """Reduce set using a binary operation and an initial value.

        The initial value may be an ``Expr`` or a raw Python literal (``int``,
        ``str``, ``bool``, ``Enum``), which is auto-coerced.
        """

        initial = coerce_expr(initial)
        acc_name, elem_name = _Binders.parameter_names(
            function,
            2,
            "Callable must take exactly two arguments (accumulator, element)",
        )
        acc_var, acc_var_node = _Binders.fresh_named_variable(acc_name, initial.sort)
        elem_var, elem_var_node = _Binders.fresh_named_variable(
            elem_name, self.elem_sort
        )
        fun_node = function(acc_var, elem_var).node

        return expr_from_node(
            SetReduceNode(
                self.node, acc_var_node, elem_var_node, fun_node, initial.node
            )
        )

    @property
    def size(self) -> "IntExpr":
        """Get the cardinality (size) of the finite set."""
        return IntExpr(AlgebraNode(IntSort(), AlgebraOp.CARDINALITY, self.node))

    @property
    def flattened(self) -> "SetExpr":
        """Flatten a set of sets: ⋃ self."""
        if not isinstance(self.elem_sort, SetSort):
            raise TypeError(
                f"flattened requires a set of sets, got Set({self.elem_sort})"
            )
        inner_elem_sort = self.elem_sort.elem_sort
        return SetExpr(
            AlgebraNode(SetSort(inner_elem_sort), AlgebraOp.FLATTEN, self.node)
        )

    def choose(self, predicate: Callable[["Expr"], "BoolExpr"]) -> "Expr":
        """Choose an element: CHOOSE x ∈ self : P(x)."""
        var, var_node = _Binders.fresh_unary(predicate, self.elem_sort)
        pred_expr = predicate(var)
        node = ChooseNode(
            self.node,
            var_node,
            pred_expr.node,
        )
        return expr_from_node(node)

    def _check_elem_sort(self, other: "Expr") -> None:
        """Check that both sets have the same element sort."""
        if not isinstance(other.sort, SetSort):
            raise TypeError(f"Expected SetSort for other, got {other.sort}")
        other_elem_sort = other.sort.elem_sort
        if self.elem_sort != other_elem_sort:
            raise TypeError(
                f"Set operands must have the same element sort: {self.elem_sort} vs {other_elem_sort}"
            )


# =============================================================================
# List expressions
# =============================================================================


class ListExpr(Expr):
    """List expression with list operators."""

    def __init__(self, node: Node):
        assert isinstance(
            node.sort, ListSort
        ), f"Expected ListSort, got {type(node.sort).__name__}"
        super().__init__(node)

    def __dir__(self) -> list[str]:
        """Provide explicit list of attributes for better tab completion in IPython."""
        return _Completion.names(
            "is_empty",
            "keys",
            "size",
            "exists",
            "filter",
            "forall",
            "if_",
            "in_",
            "reduce",
            "replace",
            operators=("__add__", "__getitem__"),
        )

    @property
    def elem_sort(self) -> Sort:
        """Get the element sort of this list."""
        return self._node.sort.elem_sort  # type: ignore[return-value,attr-defined,no-any-return]

    def _getitem(self, index: "Expr | int") -> "Expr":
        """Element access: self[index]."""
        index_node = coerce_int_node(index)
        node = ListGetNode(self._node, index_node)
        return expr_from_node(node)

    def _slice(self, s: slice) -> "ListExpr":
        """Slice: self[start:end]. Start defaults to 0, end defaults to len."""
        if s.step is not None:
            raise TypeError("List slicing does not support step")
        start_node = (
            coerce_int_node(s.start) if s.start is not None else coerce_int_node(0)
        )
        end_node = coerce_int_node(s.stop) if s.stop is not None else self.size.node
        return ListExpr(ListSliceNode(self._node, start_node, end_node))

    def _concat(self, other: "Expr") -> "ListExpr":
        """List concatenation: self + other."""
        if not isinstance(other.sort, ListSort):
            raise TypeError(f"Expected ListSort for other, got {other.sort}")
        if self.elem_sort != other.sort.elem_sort:
            raise TypeError(
                f"List operands must have the same element sort: "
                f"{self.elem_sort} vs {other.sort.elem_sort}"
            )
        return ListExpr(
            AlgebraNode(self.node.sort, AlgebraOp.LIST_CONCAT, self.node, other.node)
        )

    def replace(self, index: "Expr | int", value: "Expr") -> "ListExpr":  # type: ignore[override]
        """Replace element at index with a new value."""
        index_node = coerce_int_node(index)
        value_expr = coerce_expr(value, self.elem_sort)
        return ListExpr(ListUpdateNode(self._node, index_node, value_expr._node))

    def filter(self, predicate: Callable[["Expr"], "Expr"]) -> "ListExpr":
        """Filter list: elements of self for which P(x) holds."""
        var, var_node = _Binders.fresh_unary(predicate, self.elem_sort)
        pred_expr = predicate(var)
        return ListExpr(
            ListFilterNode(
                self.node,
                var_node,
                pred_expr.node,
            )
        )

    def reduce(
        self,
        function: Callable[["Expr", "Expr"], "Expr"],
        initial: "Expr | int | str | bool | Enum",
    ) -> "Expr":
        """Reduce list using a binary operation and an initial value.

        Unlike set reduce, list reduce processes elements in order. The initial
        value may be an ``Expr`` or a raw Python literal (``int``, ``str``,
        ``bool``, ``Enum``), which is auto-coerced.
        """
        initial = coerce_expr(initial)
        acc_name, elem_name = _Binders.parameter_names(
            function,
            2,
            "Callable must take exactly two arguments (accumulator, element)",
        )
        acc_var, acc_var_node = _Binders.fresh_named_variable(acc_name, initial.sort)
        elem_var, elem_var_node = _Binders.fresh_named_variable(
            elem_name, self.elem_sort
        )
        fun_node = function(acc_var, elem_var).node

        return expr_from_node(
            ListReduceNode(
                self.node, acc_var_node, elem_var_node, fun_node, initial.node
            )
        )

    @property
    def size(self) -> "IntExpr":
        """Get the size (length) of the list."""
        return IntExpr(AlgebraNode(IntSort(), AlgebraOp.LIST_SIZE, self.node))

    @property
    def keys(self) -> "SetExpr":
        """Return the set of all valid indices of this list."""
        return SetExpr(ListKeysNode(self.node))

    @overload
    def forall(self, predicate: Callable[["Expr"], "BoolExpr"]) -> "BoolExpr": ...

    @overload
    def forall(
        self, predicate: Callable[["Expr"], "TemporalExpr"]
    ) -> "TemporalExpr": ...

    def forall(self, predicate: Callable) -> "Expr":
        """Universal quantification over elements: ∀x ∈ self : P(x).

        Desugars to ``self.keys.forall(lambda idx: P(self[idx]))``.
        """
        _Binders.parameter_names(
            predicate, 1, "Callable must take exactly one argument"
        )
        return cast("Expr", self.keys.forall(lambda idx: predicate(self._getitem(idx))))

    @overload
    def exists(self, predicate: Callable[["Expr"], "BoolExpr"]) -> "BoolExpr": ...

    @overload
    def exists(
        self, predicate: Callable[["Expr"], "TemporalExpr"]
    ) -> "TemporalExpr": ...

    def exists(self, predicate: Callable) -> "Expr":
        """Existential quantification over elements: ∃x ∈ self : P(x).

        Desugars to ``self.keys.exists(lambda idx: P(self[idx]))``.
        """
        _Binders.parameter_names(
            predicate, 1, "Callable must take exactly one argument"
        )
        return cast("Expr", self.keys.exists(lambda idx: predicate(self._getitem(idx))))


# =============================================================================
# Map expressions
# =============================================================================


class MapExpr(Expr):
    """Map expression with map operators."""

    def __init__(self, node: Node):
        if not isinstance(node.sort, MapSort):
            raise TypeError(f"Expected MapSort, got {type(node.sort).__name__}")
        super().__init__(node)

    @property
    def key_sort(self) -> Sort:
        assert isinstance(self.node.sort, MapSort)
        return self.node.sort.key_sort

    @property
    def value_sort(self) -> Sort:
        assert isinstance(self.node.sort, MapSort)
        return self.node.sort.value_sort

    def _getitem(self, key: "Expr") -> "Expr":
        """Map lookup: self[key]."""
        node = MapGetNode(self.node, key.node)
        return expr_from_node(node)

    def insert(
        self, key: "Expr | int | bool | str", value: "Expr | int | bool | str"
    ) -> "MapExpr":
        """Functional insert/update: create a new map with the value of a specified
        key inserted or updated. If there is no such key, new entry is added.

        Returns a new MapExpr with the updates on top of the old one.

        Example:
            new_map = balances.insert(account_id, Val(1000))
        """
        if not isinstance(key, Expr):
            key = coerce_expr(key, self.key_sort)
        if not isinstance(value, Expr):
            value = coerce_expr(value, self.value_sort)
        return MapExpr(MapSetNode(self.node, key.node, value.node, replace_only=False))

    def replace(  # type: ignore[override]
        self, key: "Expr | int | bool | str", value: "Expr | int | bool | str"
    ) -> "MapExpr":
        """Functional replace: create a new map with the value of a specified
        key updated. If there is no such key, no new entry is added.
        Tools may report an error if the key does not exist (if they can detect that).

        Returns a new MapExpr with the updates on top of the old one.

        Example:
            new_map = balances.replace(account_id, Val(1000))
        """
        if not isinstance(key, Expr):
            key = coerce_expr(key, self.key_sort)
        if not isinstance(value, Expr):
            value = coerce_expr(value, self.value_sort)
        return MapExpr(MapSetNode(self.node, key.node, value.node, replace_only=True))

    @property
    def size(self) -> "IntExpr":
        """Get the number of key-value pairs in the map."""
        return SetExpr(MapKeysNode(self.node)).size

    @property
    def keys(self) -> "SetExpr":
        """Return the set of keys in this map.

        Example:
            account_ids = balances.keys
        """
        return SetExpr(MapKeysNode(self.node))

    @property
    def values(self) -> "SetExpr":
        """Return the set of values in this map.

        Desugars to ``self.keys.map(lambda k: self[k])``. Because the result is
        a set, duplicate values collapse.

        Example:
            balances_seen = balances.values
        """
        return self.keys.map(lambda k: self._getitem(k))

    @property
    def items(self) -> "SetExpr":
        """Return the set of key-value pairs in this map, as 2-element tuples.

        Desugars to ``self.keys.map(lambda k: Tuple(k, self[k]))``.

        Example:
            balances_items = balances.items
        """
        return self.keys.map(
            lambda k: TupleExpr(TupleCtorNode(k.node, self._getitem(k).node))
        )

    def reduce(  # type: ignore[override]
        self,
        function: "Callable[[Expr, Expr, Expr], Expr]",
        initial: "Expr | int | str | bool | Enum",
    ) -> "Expr":
        """Reduce the map over its key/value pairs.

        ``function`` takes ``(accumulator, key, value)``. Desugars to
        ``self.keys.reduce(lambda acc, k: function(acc, k, self[k]), initial)``.

        The initial value may be an ``Expr`` or a raw Python literal (``int``,
        ``str``, ``bool``, ``Enum``), which is auto-coerced.

        Example:
            total = balances.reduce(lambda acc, k, v: acc + v, 0)
        """
        _Binders.parameter_names(
            function,
            3,
            "Callable must take exactly three arguments (accumulator, key, value)",
        )
        return self.keys.reduce(
            lambda acc, k: function(acc, k, self._getitem(k)), initial
        )


# =============================================================================
# Union expressions
# =============================================================================


class UnionExpr(Expr):
    """Union expression with tag access and pattern matching."""

    def __init__(self, node: Node):
        if not isinstance(node.sort, UnionSort):
            raise TypeError(f"Expected UnionSort, got {type(node.sort).__name__}")
        super().__init__(node)

    @property
    def tag(self) -> "StrExpr":
        """Access the tag as a string expression."""
        return StrExpr(UnionGetTagNode(self._node))

    @staticmethod
    def _missing_match_tags(
        union_sort: UnionSort,
        case_tags: set[str],
        has_default: bool,
    ) -> set[str]:
        """Validate supplied tags and return the variants still needing cases."""
        variant_tags = {tag for tag, _ in union_sort.variants}
        extra_tags = case_tags - variant_tags
        if extra_tags:
            raise ValueError(
                f"Unknown variants in match: {', '.join(sorted(extra_tags))}"
            )

        missing_tags = variant_tags - case_tags
        if missing_tags and not has_default:
            raise ValueError(
                "Non-exhaustive match: missing cases for "
                f"{', '.join(sorted(missing_tags))}"
            )
        return missing_tags

    @staticmethod
    def _default_match_body(default: Any) -> Node | None:
        """Coerce a default value or invoke its zero-argument callback."""
        if default is None:
            return None
        if callable(default):
            _Binders.parameter_names(
                default, 0, "default callback must take 0 arguments"
            )
            default = default()
        return coerce_expr(default)._node

    @staticmethod
    def _build_match_case(
        tag: str,
        function: Callable[..., Any],
        payload_sort: Sort | None,
    ) -> tuple[VarNode | None, Node]:
        """Invoke and normalize one match callback."""
        if payload_sort is not None:
            (parameter_name,) = _Binders.parameter_names(
                function,
                1,
                f"Case '{tag}' has a payload of sort {payload_sort.name}, "
                "callback must take exactly 1 argument",
            )
            variable = VarExpr(parameter_name, payload_sort)
            body = function(variable)
            body_expr = body if isinstance(body, Expr) else coerce_expr(body)
            return VarNode(parameter_name, payload_sort), body_expr._node

        _Binders.parameter_names(
            function,
            0,
            f"Case '{tag}' has no payload, callback must take 0 arguments",
        )
        body = function()
        body_expr = body if isinstance(body, Expr) else coerce_expr(body)
        return None, body_expr._node

    @staticmethod
    def _add_default_match_cases(
        built_cases: dict[str, tuple[VarNode | None, Node]],
        missing_tags: set[str],
        union_sort: UnionSort,
        default_body: Node | None,
    ) -> None:
        """Expand a default body into explicit cases required by the AST."""
        if default_body is None:
            return
        for tag in missing_tags:
            payload_sort = union_sort[tag]
            variable = VarNode("_", payload_sort) if payload_sort is not None else None
            built_cases[tag] = variable, default_body

    def match(
        self,
        default: "Callable[[], Expr] | Expr | int | str | bool | None" = None,
        **cases: "Callable",
    ) -> "Expr":
        """Pattern match on this union.

        Each keyword argument is a variant tag mapped to a callable:
        - For variants with payload: lambda takes one argument (the payload expr)
        - For variants without payload: lambda takes no arguments

        If `default` is provided, it handles all unspecified variants:
        - Can be a zero-argument callable returning an Expr
        - Can be a literal value (int, str, bool) or Expr

        All cases (including default) must return expressions of the same sort.

        Examples:

            Exhaustive match (all variants specified):

            ```python
            result = option.match(
                Some=lambda v: v + 1,
                None_=lambda: Val(0),
            )
            ```

            Match with default (non-exhaustive):

            ```python
            result = result.match(
                Ok=lambda v: v + 1,
                default=Val(-1),  # handles Err variant
            )
            # Or with a callable:
            result = result.match(
                Ok=lambda v: v + 1,
                default=lambda: Val(-1),
            )
            ```
        """
        union_sort: UnionSort = self._node.sort  # type: ignore[assignment]
        missing_tags = self._missing_match_tags(
            union_sort, set(cases), default is not None
        )
        default_body = self._default_match_body(default)

        built_cases: dict[str, tuple[VarNode | None, Node]] = {}
        for tag, function in cases.items():
            built_cases[tag] = self._build_match_case(tag, function, union_sort[tag])
        self._add_default_match_cases(
            built_cases, missing_tags, union_sort, default_body
        )

        match_node = UnionMatchNode(self._node, built_cases)
        return expr_from_node(match_node)


# =============================================================================
# Variable expressions
# =============================================================================


class VarExpr(Expr):
    """
    A factory for creating variable expressions of appropriate expression types
    based on sort.

    Args:
        name: The name of the variable.
        sort: The sort of the variable.

    Returns:
        An instance of the appropriate variable expression class.

    Example:

        Create a variable of integer sort, inheriting from `IntExpr`:

            >>> from wunderspec import sort_of
            >>> x = VarExpr("x", sort_of(int))
            >>> type(x).__name__
            'IntVar'
            >>> x.name
            'x'
            >>> x.sort
            IntSort()
            >>> isinstance(x, IntExpr)
            True

        Create a variable of the sort "set of integers", inheriting from `SetExpr`:

            >>> s = VarExpr("S", sort_of(set[int]))
            >>> type(s).__name__
            'SetVar'
            >>> s.name
            'S'
            >>> s.sort
            SetSort(IntSort())
            >>> isinstance(s, SetExpr)
            True

        The other combinations also work similarly:

            >>> isinstance(VarExpr("b", sort_of(bool)), BoolExpr)
            True
            >>> isinstance(VarExpr("s", sort_of(str)), StrExpr)
            True
            >>> isinstance(VarExpr("m", sort_of(dict[int, str])), MapExpr)
            True
            >>> isinstance(VarExpr("t", sort_of(tuple[int, bool])), TupleExpr)
            True
            >>> from dataclasses import dataclass
            >>> @dataclass(frozen=True)
            ... class MyRecord:
            ...     a: int
            ...
            >>> r = VarExpr("r", sort_of(MyRecord))
            >>> isinstance(r, RecordExpr)
            True
            >>> r.sort
            RecordSort(a=IntSort())
            >>> from enum import Enum
            >>> class MyEnum(Enum):
            ...     A = 1
            ...     B = 2
            ...
            >>> e = VarExpr("e", sort_of(MyEnum))
            >>> isinstance(e, EnumExpr)
            True
            >>> e.sort
            EnumSort(MyEnum)
    """

    SORT_TO_EXPR_BASE: dict[type, type[Expr]] = {
        IntSort: IntExpr,
        BoolSort: BoolExpr,
        StrSort: StrExpr,
        SetSort: SetExpr,
        ListSort: ListExpr,
        MapSort: MapExpr,
        RecordSort: RecordExpr,
        TupleSort: TupleExpr,
        EnumSort: EnumExpr,
        UnionSort: UnionExpr,
    }

    # cache for dynamically created variable classes
    _CLASS_CACHE: dict[tuple[type[Expr], type], type[Expr]] = {}

    _node: Any
    _name: str

    def __new__(  # type: ignore[misc]
        cls,
        name: str,
        sort: Sort,
        unique_name: str | None = None,
        tla_name: str | None = None,
        **extra: Any,
    ) -> Expr:
        """
        Create a new variable expression of the appropriate type based on sort.
        NOTE: the return type is `Expr`, but the actual instance type will be a subclass
        of `Expr` corresponding to the sort. The type: ignore is needed because of the
        factory pattern - we return instances of dynamically created subclasses.
        """
        sort_name = type(sort).__name__
        base = VarExpr.SORT_TO_EXPR_BASE.get(type(sort), Expr)
        key = (base, type(sort))

        var_cls = VarExpr._CLASS_CACHE.get(key)
        if var_cls is None:
            cls_name = (
                sort_name[: -len("Sort")] if sort_name.endswith("Sort") else sort_name
            )
            cls_name = f"{cls_name}Var"
            var_cls = type(
                cls_name,
                (base,),
                {
                    "name": property(_VariableMetadata.name),
                    "unique_name": property(_VariableMetadata.unique_name),
                    "tla_name": property(_VariableMetadata.tla_name),
                },
            )
            VarExpr._CLASS_CACHE[key] = var_cls

        obj = super().__new__(var_cls)  # type: ignore
        obj._node = VarNode(name, sort, unique_name=unique_name, tla_name=tla_name)
        obj._name = name
        for extra_name, extra_value in extra.items():
            setattr(obj, f"_{extra_name}", extra_value)
        return obj  # type: ignore


# =============================================================================
# Expression builders
# =============================================================================


class _ConditionalBuilder:
    """Build a conditional expression after its condition and true branch."""

    def __init__(self, condition: "BoolExpr", then_expr: "Expr"):
        self.condition = condition
        self.then_expr = then_expr

    def else_(self, else_expr: "Expr | int | str | bool | Enum") -> "Expr":
        """Complete the if-then-else expression.

        The else branch may be an ``Expr`` or a raw Python literal (``int``,
        ``str``, ``bool``, ``Enum``), which is auto-coerced to the sort of the
        then branch. A mismatched sort raises ``TypeError``.
        """
        else_coerced = coerce_expr(else_expr, self.then_expr._node.sort)
        node = IteNode(self.condition._node, self.then_expr._node, else_coerced._node)
        return expr_from_node(node)


_UpdateAlias = tuple[VarNode, Node]
_UpdateParent = tuple[Node, Expr]


class UpdateContext:
    """Internal context for assignment-like updates using UpdatesBuilder."""

    def __init__(
        self,
        source_expr: Expr,
        name_prefix: str,
        replace_only: bool = False,
        on_update: Callable[[], None] | None = None,
    ) -> None:
        self.updated_node = source_expr._node
        self.name_prefix = name_prefix
        self.replace_only = replace_only
        self.is_done = False
        self._on_update = on_update

    def _mk_get_node(self, base: Node, key: Node) -> Node:
        match base.sort:
            case MapSort():
                return MapGetNode(base, key)
            case RecordSort():
                if isinstance(key, LitNode) and isinstance(key.value, str):
                    return RecordGetNode(base, key.value)  # type: ignore
                raise TypeError("Record field key must be a string literal")
            case TupleSort():
                if isinstance(key, LitNode) and isinstance(key.value, int):
                    return TupleGetNode(base, key.value)  # type: ignore
                raise TypeError("Tuple index key must be an integer literal")
            case ListSort():
                return ListGetNode(base, key)
            case _:
                raise TypeError(f"Unsupported sort for get operation: {base.sort}")

    def _mk_set_node(self, base: Node, key: Node, value: Node) -> Node:
        match base.sort:
            case MapSort():
                return MapSetNode(base, key, value, replace_only=self.replace_only)
            case RecordSort():
                if isinstance(key, LitNode) and isinstance(key.value, str):
                    return RecordUpdateNode(base, **{key.value: value})  # type: ignore
                raise TypeError("Record field key must be a string literal")
            case TupleSort():
                if isinstance(key, LitNode) and isinstance(key.value, int):
                    return TupleUpdateNode(base, key.value, value)  # type: ignore
                raise TypeError("Tuple index key must be an integer literal")
            case ListSort():
                return ListUpdateNode(base, key, value)
            case _:
                raise TypeError(f"Unsupported sort for set operation: {base.sort}")

    def _walk_to_update_parent(
        self, path_prefix: tuple[Expr, ...]
    ) -> tuple[list[_UpdateAlias], list[_UpdateParent], Node]:
        """Alias the root and path prefix, returning the final update parent."""
        if isinstance(self.updated_node, VarNode):
            aliases: list[tuple[VarNode, Node]] = []
            current_node: Node = self.updated_node
        else:
            root_alias = VarNode(self._fresh_var_name(), self.updated_node.sort)
            aliases = [(root_alias, self.updated_node)]
            current_node = root_alias

        parents: list[_UpdateParent] = []
        for key in path_prefix:
            child_node = self._mk_get_node(current_node, key.node)
            child_alias = VarNode(self._fresh_var_name(), child_node.sort)
            aliases.append((child_alias, child_node))
            parents.append((current_node, key))
            current_node = child_alias

        return aliases, parents, current_node

    def _rebuild_update_parents(
        self, updated_child: Node, parents: list[_UpdateParent]
    ) -> Node:
        """Reinsert an updated child through all enclosing collection nodes."""
        updated_node = updated_child
        for parent_node, key in reversed(parents):
            updated_node = self._mk_set_node(parent_node, key.node, updated_node)
        return updated_node

    @staticmethod
    def _wrap_update_aliases(updated_node: Node, aliases: list[_UpdateAlias]) -> Node:
        """Bind path aliases around a rebuilt update expression."""
        for variable, aliased_node in reversed(aliases):
            updated_node = LetNode(variable.name, aliased_node, updated_node)
        return updated_node

    def update(self, key_path: tuple[Expr, ...], new_value: Expr) -> None:
        """Apply an update at the specified key path with the new value."""
        aliases, parents, update_parent = self._walk_to_update_parent(key_path[:-1])
        final_key = key_path[-1]
        updated_node = self._mk_set_node(update_parent, final_key.node, new_value._node)
        updated_node = self._rebuild_update_parents(updated_node, parents)
        self.updated_node = self._wrap_update_aliases(updated_node, aliases)

        if self._on_update:
            self._on_update()

    def _fresh_var_name(self) -> str:
        return fresh_name(self.name_prefix)


class UpdatesBuilder(Expr):
    """
    Helper class to build path updates for maps, records, tuples, and lists.

    UpdatesBuilder extends `Expr` for coercion to work correctly.
    """

    _INTERNAL_NAMES = frozenset({"_ctx", "_key_path", "_proxied_expr", "_node"})

    def __init__(
        self, ctx: UpdateContext, key_path: tuple[Expr, ...], proxied_expr: Expr
    ) -> None:
        Expr.__init__(self, proxied_expr._node)
        self._ctx = ctx
        self._key_path = key_path
        self._proxied_expr = proxied_expr

    def _child_builder(self, key_expr: Expr, proxied_expr: Expr) -> "UpdatesBuilder":
        """Create a builder for one more segment of the current update path."""
        return UpdatesBuilder(
            self._ctx,
            self._key_path + (key_expr,),
            proxied_expr,
        )

    def __getattribute__(self, name: str) -> "Expr":
        if _RecordFields.is_field_attribute(name):
            try:
                proxied = object.__getattribute__(self, "_proxied_expr")
            except AttributeError:
                pass
            else:
                node = object.__getattribute__(proxied, "_node")
                if isinstance(node.sort, RecordSort) and name in node.sort:
                    make_child = cast(
                        Callable[[Expr, Expr], UpdatesBuilder],
                        object.__getattribute__(self, "_child_builder"),
                    )
                    return make_child(
                        coerce_expr(name),
                        RecordExpr(node)._getitem(name),
                    )
        return cast("Expr", object.__getattribute__(self, name))

    @property
    def result(self) -> Expr:
        """Get the final updated expression after all updates have been applied."""
        if self._key_path:
            raise RuntimeError("result must be accessed on the root update object")
        if self._ctx.is_done:
            raise RuntimeError("result has already been accessed")
        self._ctx.is_done = True
        return expr_from_node(self._ctx.updated_node)

    def __getitem__(self, key: "Expr | int | str | bool | slice") -> "UpdatesBuilder":  # type: ignore[override]
        """Extend the update path with the next key."""
        # convert literals to expressions, unless in the expression form already
        key_expr = coerce_expr(key)  # type: ignore[call-arg]
        item = self._proxied_expr.__getitem__(key_expr)  # type: ignore[call-arg,index]
        return self._child_builder(key_expr, item)

    def __setitem__(self, key: Expr | int | bool | str | slice, value: object) -> None:
        """Set the value at the specified path."""
        # convert literals to expressions, unless in the expression form already
        if isinstance(key, slice):
            raise TypeError("Assignment to x[a:b] is not supported")
        key_expr = coerce_expr(key)  # type: ignore[call-arg]
        item = self._proxied_expr.__getitem__(key_expr)  # type: ignore[call-arg,index]
        self._ctx.update(self._key_path + (key_expr,), coerce_expr(value, item.sort))

    def __getattr__(self, name: str) -> Any:
        """Extend the update path with a field name, or delegate to proxied expression."""
        # Get the proxied expression using object.__getattribute__ to avoid recursion
        try:
            proxied = object.__getattribute__(self, "_proxied_expr")
        except AttributeError:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '_proxied_expr'"
            )

        proxied_attr = getattr(proxied, name)

        # If it's a method or non-field attribute, delegate directly
        if callable(proxied_attr) or not isinstance(
            proxied, (MapExpr, RecordExpr, TupleExpr)
        ):
            return proxied_attr

        return self._child_builder(coerce_expr(name), proxied_attr)

    def __setattr__(self, name: str, value: object) -> None:
        """Set the value at the specified path using attribute syntax (for records)."""
        if name in UpdatesBuilder._INTERNAL_NAMES:
            object.__setattr__(self, name, value)
        else:
            # Coerce Python literals to the record field's sort (so
            # ``upd.rec.field = 5`` works). Expr values pass through unchanged so
            # the update node still validates field existence and sort.
            if not isinstance(value, Expr):
                proxied = object.__getattribute__(self, "_proxied_expr")
                value = coerce_expr(value, getattr(proxied, name).sort)
            self._ctx.update(self._key_path + (coerce_expr(name),), value)


# =============================================================================
# Internal helper namespaces
# =============================================================================


class _Completion:
    """Build the intentionally restricted completion lists for expressions."""

    _STANDARD_NAMES = (
        "__class__",
        "__delattr__",
        "__dict__",
        "__dir__",
        "__doc__",
        "__eq__",
        "__format__",
        "__ge__",
        "__getattribute__",
        "__gt__",
        "__hash__",
        "__init__",
        "__init_subclass__",
        "__le__",
        "__lt__",
        "__ne__",
        "__new__",
        "__reduce__",
        "__reduce_ex__",
        "__repr__",
        "__setattr__",
        "__sizeof__",
        "__str__",
        "__subclasshook__",
    )

    @classmethod
    def names(cls, *public_names: str, operators: tuple[str, ...] = ()) -> list[str]:
        return [*public_names, *cls._STANDARD_NAMES, *operators]


class _Binders:
    """Create and validate variables used by expression callbacks."""

    @staticmethod
    def parameter_names(
        function: Callable[..., Any],
        expected_arity: int,
        error_message: str,
    ) -> tuple[str, ...]:
        parameter_names = tuple(inspect.signature(function).parameters)
        if len(parameter_names) != expected_arity:
            raise ValueError(error_message)
        return parameter_names

    @staticmethod
    def fresh_named_variable(name: str, sort: Sort) -> tuple[Expr, VarNode]:
        variable = VarExpr(name, sort, unique_name=fresh_name(name))
        variable_node = variable.node
        if not isinstance(variable_node, VarNode):
            raise TypeError(f"Expected VarNode, got {type(variable_node).__name__}")
        return variable, variable_node

    @staticmethod
    def fresh_unary(function: Callable[..., Any], sort: Sort) -> tuple[Expr, VarNode]:
        (name,) = _Binders.parameter_names(
            function, 1, "Callable must take exactly one argument"
        )
        return _Binders.fresh_named_variable(name, sort)


class _GeneratorContext:
    """Track variable bindings while consuming a symbolic generator."""

    def __init__(self, names: tuple[str, ...] | None = None) -> None:
        self.bindings: list[tuple[Expr, Expr]] = []
        self.names = names

    def var(self, default_prefix: str, sort: Sort) -> VarExpr:
        index = len(self.bindings)
        if self.names is None:
            return VarExpr(fresh_name(default_prefix), sort)
        if index >= len(self.names):
            raise ValueError(
                f"name has {len(self.names)} entries, but the generator has more bindings"
            )
        name = self.names[index]
        return VarExpr(name, sort, unique_name=fresh_name(name), tla_name=name)


class _GeneratorLocal(threading.local):
    """Hold each thread's stack of active symbolic generator contexts."""

    def __init__(self) -> None:
        self.stack: list[_GeneratorContext] = []


class _GeneratorContexts:
    """Manage nested, thread-local symbolic generator contexts."""

    _local = _GeneratorLocal()

    @classmethod
    def current(cls) -> _GeneratorContext | None:
        stack = cls._local.stack
        return stack[-1] if stack else None

    @classmethod
    @contextmanager
    def activate(
        cls, names: tuple[str, ...] | None = None
    ) -> Iterator[_GeneratorContext]:
        if names is not None:
            active_names = {
                name
                for context in cls._local.stack
                for name in (() if context.names is None else context.names)
            }
            duplicate = active_names.intersection(names)
            if duplicate:
                names_str = ", ".join(sorted(duplicate))
                raise ValueError(f"duplicate active generator name(s): {names_str}")

        context = _GeneratorContext(names)
        cls._local.stack.append(context)
        try:
            yield context
        finally:
            cls._local.stack.pop()


class _RecordFields:
    """Recognize attributes that may refer to symbolic record fields."""

    @staticmethod
    def is_field_attribute(name: str) -> bool:
        return not name.startswith("_") and not (
            name.startswith("__") and name.endswith("__")
        )


class _ExprOps:
    """Provide expression operations shadowed by record fields."""

    def __init__(self, expr: Expr):
        object.__setattr__(self, "_expr", expr)

    @property
    def node(self) -> Node:
        expr = cast(Expr, object.__getattribute__(self, "_expr"))
        return cast(Node, object.__getattribute__(expr, "_node"))

    @property
    def sort(self) -> Sort:
        return self.node.sort

    @property
    def name(self) -> str:
        node = self.node
        if isinstance(node, VarNode):
            return node.name
        raise AttributeError(f"{type(node).__name__} has no variable name")

    @property
    def unique_name(self) -> str | None:
        node = self.node
        if isinstance(node, VarNode):
            return node.unique_name
        raise AttributeError(f"{type(node).__name__} has no unique variable name")

    def __getattr__(self, name: str) -> Any:
        expr = object.__getattribute__(self, "_expr")
        return object.__getattribute__(expr, name)


class _VariableMetadata:
    """Read metadata properties installed on dynamic variable classes."""

    @staticmethod
    def name(expr: Expr) -> str:
        return cast(str, object.__getattribute__(expr, "_name"))

    @staticmethod
    def unique_name(expr: Expr) -> str | None:
        node = cast(Node, object.__getattribute__(expr, "_node"))
        return getattr(node, "unique_name", None)

    @staticmethod
    def tla_name(expr: Expr) -> str | None:
        node = cast(Node, object.__getattribute__(expr, "_node"))
        return getattr(node, "tla_name", None)


# =============================================================================
# Expression construction and coercion
# =============================================================================


def expr_from_node(node: Node) -> Expr:
    """Wrap a Node in the appropriate Expr type."""
    for sort_type, expr_type in VarExpr.SORT_TO_EXPR_BASE.items():
        if isinstance(node.sort, sort_type):
            return expr_type(node)
    return Expr(node)


def coerce_int_node(value: Expr | int) -> Node:
    """Coerce a value to an integer Node."""
    if isinstance(value, Expr) and isinstance(value._node.sort, IntSort):
        return value._node
    if isinstance(value, int):
        return LitNode(value)
    raise TypeError(f"Cannot coerce {type(value).__name__} to integer sort")


def coerce_bool_node(value: Expr | bool) -> Node:
    """Coerce a value to a boolean Node."""
    if isinstance(value, Expr) and isinstance(value._node.sort, BoolSort):
        return value._node
    if isinstance(value, bool):
        return LitNode(value)
    raise TypeError(f"Cannot coerce {type(value).__name__} to Boolean sort")


def coerce_expr(value: Any, sort: Sort | None = None) -> Expr:
    """Coerce a Python value to an Expr."""
    if isinstance(value, Expr):
        if sort is not None and value._node.sort != sort:
            raise TypeError(f"Cannot coerce Expr[{value._node.sort}] to Expr[{sort}]")
        return value

    # If an expected sort is provided, enforce it.
    if sort is not None:
        if isinstance(sort, BoolSort):
            if isinstance(value, bool):
                return BoolExpr(LitNode(value))
            raise TypeError(f"Cannot coerce {type(value).__name__} to Expr[{sort}]")
        elif isinstance(sort, IntSort):
            # bool is a subclass of int
            if isinstance(value, int) and not isinstance(value, bool):
                return IntExpr(LitNode(value))
            raise TypeError(f"Cannot coerce {type(value).__name__} to Expr[{sort}]")
        elif isinstance(sort, StrSort):
            if isinstance(value, str):
                return StrExpr(LitNode(value))
            raise TypeError(f"Cannot coerce {type(value).__name__} to Expr[{sort}]")
        elif isinstance(sort, EnumSort):
            if isinstance(value, Enum):
                return EnumExpr(LitNode(value))
            raise TypeError(f"Cannot coerce {type(value).__name__} to Expr[{sort}]")
        else:
            raise TypeError(f"Cannot coerce {type(value).__name__} to Expr[{sort}]")

    # Otherwise, infer sort from the Python value.
    if isinstance(value, bool):  # Check bool before int!
        return BoolExpr(LitNode(value))
    if isinstance(value, Enum):
        return EnumExpr(LitNode(value))
    if isinstance(value, int):
        return IntExpr(LitNode(value))
    if isinstance(value, str):
        return StrExpr(LitNode(value))
    raise TypeError(f"Cannot coerce {type(value).__name__} to Expr")
