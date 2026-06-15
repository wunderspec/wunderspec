"""
Pure AST nodes for set expressions.

These are logical terms as data structures, no rich operator overloading and methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import cast

from .ast import Node, QuantOp, VarNode
from .sorts import (
    BoolSort,
    IntSort,
    MapSort,
    RecordSort,
    SetSort,
    Sort,
    TemporalSort,
    TupleSort,
)


class _BindingsMixin:
    """Mixin for AST nodes with (var, domain) bindings and a body expression.

    Provides the dual-constructor logic (legacy single-binding positional args
    vs. keyword-only ``bindings``/``body``), backward-compat properties
    ``base_set`` and ``var``, and helpers for ``__eq__``/``__hash__``.
    """

    bindings: list[tuple[VarNode, Node]]
    body: Node

    def _init_bindings(
        self,
        legacy_args: tuple[Node | None, VarNode | None, Node | None],
        bindings: list[tuple[VarNode, Node]] | None,
        body: Node | None,
    ) -> None:
        """Populate ``self.bindings`` and ``self.body`` from either form."""
        if bindings is not None:
            assert body is not None
            assert len(bindings) >= 1
            for _, domain in bindings:
                assert isinstance(domain.sort, SetSort)
            self.bindings = bindings
            self.body = body
        else:
            base_set, var, body_arg = legacy_args
            assert base_set is not None and var is not None and body_arg is not None
            assert isinstance(base_set.sort, SetSort)
            self.bindings = [(var, base_set)]
            self.body = body_arg

    @property
    def base_set(self) -> Node:
        """Backward-compat: first binding's domain."""
        return self.bindings[0][1]

    @property
    def var(self) -> VarNode:
        """Backward-compat: first binding's variable."""
        return self.bindings[0][0]

    def _bindings_eq(self, other: _BindingsMixin) -> bool:
        return self.bindings == other.bindings and self.body == other.body

    def _bindings_hash_components(self) -> tuple:
        """Return (bindings, body) as hashable tuple for use in ``__hash__``."""
        return (tuple(self.bindings), self.body)


class SetNode(Node, ABC):
    """Abstract base class for set AST nodes parameterized by element type."""

    def __init__(self, elem_sort: Sort):
        super().__init__(SetSort(elem_sort))
        self.elem_sort = elem_sort

    @abstractmethod
    def __repr__(self):
        """Subclasses must implement their own representation."""
        pass


class SetEnumNode(SetNode):
    """Set node with explicitly enumerated elements."""

    def __init__(self, elem_sort: Sort, *elements: Node):
        super().__init__(elem_sort)
        self.elements = elements

        # Validate that all elements have the correct sort
        for elem in elements:
            if elem.sort != elem_sort:
                raise TypeError(
                    f"Element {elem} has sort {elem.sort}, expected {elem_sort}"
                )

    def __repr__(self):
        if self.elements:
            return f"Set({', '.join(repr(e) for e in self.elements)})"
        else:
            return f"Set({repr(self.elem_sort)})"

    def __eq__(self, other):
        if not isinstance(other, SetEnumNode):
            return False

        return self.sort == other.sort and self.elements == other.elements

    def __hash__(self):
        return hash((self.sort, self.elements))


class SetIntOrNatNode(SetNode):
    """A set node that is either the set of all integers or all natural numbers."""

    def __init__(self, is_signed: bool = True):
        super().__init__(IntSort())
        self.is_signed = is_signed

    def __repr__(self):
        return "Ints" if self.is_signed else "UnsignedInts"

    def __eq__(self, other):
        if not isinstance(other, SetIntOrNatNode):
            return False

        return self.sort == other.sort and self.is_signed == other.is_signed

    def __hash__(self) -> int:
        return hash((self.sort, self.is_signed))


class SetFilterNode(_BindingsMixin, SetNode):
    """Set filter node: { x ∈ S: P(x) }.

    Supports multi-binding: { P(x, y) : x ∈ S1, y ∈ S2 }.
    """

    def __init__(
        self,
        base_set: Node | None = None,
        var: VarNode | None = None,
        predicate: Node | None = None,
        *,
        bindings: list[tuple[VarNode, Node]] | None = None,
        body: Node | None = None,
    ):
        self._init_bindings((base_set, var, predicate), bindings, body)
        # elem_sort comes from innermost domain
        inner_sort = cast(SetSort, self.bindings[-1][1].sort)
        super().__init__(inner_sort.elem_sort)

    @property
    def predicate(self) -> Node:
        """Backward-compat alias for body."""
        return self.body

    def __repr__(self):
        if len(self.bindings) == 1:
            return (
                f"SetFilter({repr(self.var)}, {repr(self.base_set)}, {repr(self.body)})"
            )
        binding_strs = ", ".join(f"({repr(v)}, {repr(d)})" for v, d in self.bindings)
        return f"SetFilter([{binding_strs}], {repr(self.body)})"

    def __eq__(self, other):
        if not isinstance(other, SetFilterNode):
            return False
        return self.sort == other.sort and self._bindings_eq(other)

    def __hash__(self):
        return hash((self.sort, *self._bindings_hash_components()))


class SetMapNode(_BindingsMixin, SetNode):
    """Set map node: { r(x): x ∈ S }.

    Supports multi-binding: { f(x, y) : x ∈ S1, y ∈ S2 }.
    """

    def __init__(
        self,
        base_set: Node | None = None,
        var: VarNode | None = None,
        mapper: Node | None = None,
        *,
        bindings: list[tuple[VarNode, Node]] | None = None,
        body: Node | None = None,
    ):
        self._init_bindings((base_set, var, mapper), bindings, body)
        # elem_sort is the body's sort (the mapped output)
        super().__init__(self.body.sort)

    @property
    def mapper(self) -> Node:
        """Backward-compat alias for body."""
        return self.body

    def __repr__(self):
        if len(self.bindings) == 1:
            return f"SetMap({repr(self.var)}, {repr(self.base_set)}, {repr(self.body)})"
        binding_strs = ", ".join(f"({repr(v)}, {repr(d)})" for v, d in self.bindings)
        return f"SetMap([{binding_strs}], {repr(self.body)})"

    def __eq__(self, other):
        if not isinstance(other, SetMapNode):
            return False
        return self.sort == other.sort and self._bindings_eq(other)

    def __hash__(self):
        return hash((self.sort, *self._bindings_hash_components()))


class SetQuantNode(_BindingsMixin, Node):
    """Quantifier node over sets: ∀x ∈ S: P, ∃ x ∈ S: P.

    Supports multi-binding: ∀x ∈ S1, y ∈ S2: P(x, y).
    """

    def __init__(
        self,
        quant: QuantOp,
        base_set: Node | None = None,
        var: VarNode | None = None,
        predicate: Node | None = None,
        sort: Sort = BoolSort(),
        *,
        bindings: list[tuple[VarNode, Node]] | None = None,
        body: Node | None = None,
    ):
        if not isinstance(sort, BoolSort) and not isinstance(sort, TemporalSort):
            raise TypeError(
                f"Set quantifier must have Bool or Temporal sort, got {sort}"
            )
        super().__init__(sort)
        self.quant = quant
        self._init_bindings((base_set, var, predicate), bindings, body)

    @property
    def predicate(self) -> Node:
        """Backward-compat alias for body."""
        return self.body

    def __repr__(self):
        if len(self.bindings) == 1:
            return (
                f"SetQuant({repr(self.quant.value)}, {repr(self.var)},"
                f" {repr(self.base_set)}, {repr(self.body)})"
            )
        binding_strs = ", ".join(f"({repr(v)}, {repr(d)})" for v, d in self.bindings)
        return (
            f"SetQuant({repr(self.quant.value)}, [{binding_strs}],"
            f" {repr(self.body)})"
        )

    def __eq__(self, other):
        if not isinstance(other, SetQuantNode):
            return False
        return (
            self.sort == other.sort
            and self.quant == other.quant
            and self._bindings_eq(other)
        )

    def __hash__(self):
        return hash((self.sort, self.quant, *self._bindings_hash_components()))


class SetReduceNode(Node):
    """Set reduce node: Reduce(op, set, initial)."""

    def __init__(
        self,
        base_set: Node,
        acc_var: VarNode,
        elem_var: VarNode,
        fun: Node,
        initial: Node,
    ):
        super().__init__(fun.sort)
        self.base_set = base_set
        self.acc_var = acc_var
        self.elem_var = elem_var
        self.fun = fun
        self.initial = initial

    def __repr__(self):
        return (
            f"SetReduce(({repr(self.acc_var)}, {repr(self.elem_var)}),"
            f" {repr(self.base_set)}, {repr(self.fun)}, {repr(self.initial)})"
        )

    def __eq__(self, other):
        if not isinstance(other, SetReduceNode):
            return False

        return (
            self.sort == other.sort
            and self.base_set == other.base_set
            and self.acc_var == other.acc_var
            and self.elem_var == other.elem_var
            and self.fun == other.fun
            and self.initial == other.initial
        )

    def __hash__(self):
        return hash(
            (
                self.sort,
                self.base_set,
                self.acc_var,
                self.elem_var,
                self.fun,
                self.initial,
            )
        )


class IntervalNode(SetNode):
    """Integer interval node: [a..b]."""

    def __init__(self, lower: Node, upper: Node):
        super().__init__(IntSort())
        self.lower = lower
        self.upper = upper

    def __repr__(self):
        return f"Interval({repr(self.lower)}, {repr(self.upper)})"

    def __eq__(self, other):
        if not isinstance(other, IntervalNode):
            return False

        return (
            self.sort == other.sort
            and self.lower == other.lower
            and self.upper == other.upper
        )

    def __hash__(self):
        return hash((self.sort, self.lower, self.upper))


class ChooseNode(Node):
    """Choose node: CHOOSE x ∈ S: P(x).

    Returns a single element from base_set satisfying the predicate.
    Not a SetNode since the result is a single element, not a set.
    """

    def __init__(
        self,
        base_set: Node,
        var: VarNode,
        predicate: Node,
    ):
        assert isinstance(base_set.sort, SetSort)
        super().__init__(base_set.sort.elem_sort)
        self.base_set = base_set
        self.var = var
        self.predicate = predicate

    def __repr__(self):
        return (
            f"Choose({repr(self.var)}, {repr(self.base_set)}, {repr(self.predicate)})"
        )

    def __eq__(self, other):
        if not isinstance(other, ChooseNode):
            return False

        return (
            self.sort == other.sort
            and self.base_set == other.base_set
            and self.var == other.var
            and self.predicate == other.predicate
        )

    def __hash__(self):
        return hash((self.sort, self.base_set, self.var, self.predicate))


class AllSubsetsNode(SetNode):
    """Set of all subsets (power set): SUBSET S."""

    def __init__(self, base_set: Node):
        assert isinstance(base_set.sort, SetSort)
        super().__init__(SetSort(base_set.sort.elem_sort))
        self.base_set = base_set

    def __repr__(self):
        return f"AllSubsets({repr(self.base_set)})"

    def __eq__(self, other):
        if not isinstance(other, AllSubsetsNode):
            return False

        return self.sort == other.sort and self.base_set == other.base_set

    def __hash__(self):
        return hash((self.sort, self.base_set))


class AllMapsNode(SetNode):
    """Set of all maps [key_set -> value_set]."""

    def __init__(self, key_set: Node, value_set: Node):
        assert isinstance(key_set.sort, SetSort)
        assert isinstance(value_set.sort, SetSort)
        super().__init__(MapSort(key_set.sort.elem_sort, value_set.sort.elem_sort))
        self.key_set = key_set
        self.value_set = value_set

    def __repr__(self):
        return f"AllMaps({repr(self.key_set)}, {repr(self.value_set)})"

    def __eq__(self, other):
        if not isinstance(other, AllMapsNode):
            return False

        return (
            self.sort == other.sort
            and self.key_set == other.key_set
            and self.value_set == other.value_set
        )

    def __hash__(self):
        return hash((self.sort, self.key_set, self.value_set))


class AllTuplesNode(SetNode):
    """Set of all tuples (Cartesian product): S1 × S2 × ... × Sn."""

    def __init__(self, sets: tuple[Node, ...]):
        assert len(sets) >= 1
        for s in sets:
            assert isinstance(s.sort, SetSort)
        elem_sorts = tuple(cast(SetSort, s.sort).elem_sort for s in sets)
        super().__init__(TupleSort(*elem_sorts))
        self.sets = sets

    def __repr__(self):
        return f"AllTuples({', '.join(repr(s) for s in self.sets)})"

    def __eq__(self, other):
        if not isinstance(other, AllTuplesNode):
            return False

        return self.sort == other.sort and self.sets == other.sets

    def __hash__(self):
        return hash((self.sort, self.sets))


class AllRecordsNode(SetNode):
    """Set of all records with fields drawn from given sets."""

    def __init__(self, field_sets: dict[str, Node]):
        assert len(field_sets) >= 1
        for name, s in field_sets.items():
            assert isinstance(s.sort, SetSort), f"Field {name} must be a set"
        field_sorts = {
            name: cast(SetSort, s.sort).elem_sort for name, s in field_sets.items()
        }
        super().__init__(RecordSort(**field_sorts))
        self.field_sets = field_sets

    def __repr__(self):
        fields = ", ".join(
            f"{name}={repr(s)}" for name, s in sorted(self.field_sets.items())
        )
        return f"AllRecords({fields})"

    def __eq__(self, other):
        if not isinstance(other, AllRecordsNode):
            return False

        return self.sort == other.sort and self.field_sets == other.field_sets

    def __hash__(self):
        return hash((self.sort, frozenset(self.field_sets.items())))
