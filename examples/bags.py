"""
Bags (multisets) as a user-defined high-level primitive.

This example shows how to introduce a brand new high-level data type --- a
*bag* (a.k.a. multiset) --- **entirely in user space**, without touching the
Wunderspec source code. The ``Bag`` class below uses Python operator
overloading so that bags read like an ordinary Python ADT, while every
operation lowers to the *standard* Wunderspec AST, exactly the way the built-in
``SetExpr`` and ``MapExpr`` do.

A bag is represented as a map from an element to its positive integer count,
following Apalache's ``Bags`` module
(https://github.com/apalache-mc/apalache/blob/main/src/tla/__rewire_bags_in_apalache.tla):

    EmptyBag        == [x \\in {} |-> 0]
    BagToSet(B)     == DOMAIN B
    SetToBag(S)     == [e \\in S |-> 1]
    BagIn(e, B)     == e \\in DOMAIN B
    CopiesIn(e, B)  == IF e \\in DOMAIN B THEN B[e] ELSE 0
    B1 (+) B2       == [e \\in DOMAIN B1 \\union DOMAIN B2 |->
                          CopiesIn(e, B1) + CopiesIn(e, B2)]
    B1 (-) B2       == drop e whose count drops to <= 0, keep the rest
    BagCardinality(B) == sum of all counts
    BagOfAll(F, B)  == apply F to every element, preserving multiplicity
    BagUnion(S)     == fold (+) over a set of bags

Because a bag is just a ``dict[T, int]`` under the hood, it can be stored in a
state variable and checked by all the usual Wunderspec tools. See
``producer_consumer.py`` for a runnable spec that uses this primitive.

Perhaps, we will make it a package of its own in the future.

Igor Konnov, 2026 (done with Claude Opus 4.8)
"""

from typing import Callable

from wunderspec import Expr, Ite, Map, MapExpr, Set, Val
from wunderspec.ast.sorts import IntSort, MapSort, Sort

# =============================================================================
# The Bag ADT --- a multiset built on top of MapExpr
# =============================================================================


class Bag:
    """A multiset of elements, backed by a map ``element -> positive count``.

    ``Bag`` is a thin, standalone wrapper around a :class:`MapExpr`. It is *not*
    a subclass of ``Expr``/``MapExpr`` on purpose: subclassing would inherit the
    map operators (``+``, ``-``, ``[]``, ...) whose meaning differs from bag
    semantics. Keeping ``Bag`` standalone gives a clean, closed surface where
    each operation lowers to verified ``MapExpr``/``SetExpr``/``Ite`` calls.

    The single adapter back to the standard AST is :attr:`as_map`: state
    variables hold a plain ``dict[T, int]``, so you *read* a bag with
    ``Bag(state_var)`` and *write* it with ``state_var = somebag.as_map``.

    Every bag operation returns a standard Wunderspec expression (or another
    ``Bag``), so the symbolic tools work on bags out of the box:

        >>> from wunderspec import Set, Val
        >>> b = Bag.from_set(Set(1, 2, 3))   # each element with count 1
        >>> b.contains(Val(2)).sort          # BagIn -> a Boolean expression
        BoolSort()
        >>> b[Val(2)].sort                   # CopiesIn -> an integer count
        IntSort()
        >>> b.cardinality.sort               # BagCardinality -> an integer
        IntSort()
        >>> (b <= b).sort                    # sub-bag -> a Boolean expression
        BoolSort()
        >>> type(b.add_one(Val(2))).__name__ # combinators return a Bag
        'Bag'
    """

    def __init__(self, m: Expr):
        if not isinstance(m.sort, MapSort):
            raise TypeError(f"Bag expects a map element -> count, got {m.sort}")
        if not isinstance(m.sort.value_sort, IntSort):
            raise TypeError(f"Bag counts must be Int, got {m.sort.value_sort}")
        self._map: MapExpr = m if isinstance(m, MapExpr) else MapExpr(m.node)

    # --- bridge back to the standard AST -------------------------------------

    @property
    def as_map(self) -> MapExpr:
        """The underlying ``[element |-> count]`` map.

        Assign *this* into a ``StateVar[dict[T, int]]``; reconstruct a bag with
        ``Bag(state_var)``. Named ``as_map`` (not ``map``) to avoid clashing
        with :meth:`map` (``BagOfAll``), which has different semantics.
        """
        return self._map

    @property
    def elem_sort(self) -> Sort:
        """The sort of the bag's elements."""
        return self._map.key_sort

    # --- constructors --------------------------------------------------------

    @staticmethod
    def empty(elem_sort: "type | Sort") -> "Bag":
        """The empty bag over ``elem_sort``: ``[x \\in {} |-> 0]``.

        Examples:

            >>> print(Bag.empty(int).as_map)
            Map(IntSort(), IntSort())
        """
        return Bag(Map(elem_sort, int))

    @staticmethod
    def from_set(s: Expr) -> "Bag":
        """``SetToBag(S) == [e \\in S |-> 1]`` --- every element with count 1."""
        return Bag(s.map_to(lambda e: Val(1)))

    # --- queries -------------------------------------------------------------

    def to_set(self) -> Expr:
        """``BagToSet(B) == DOMAIN B`` --- the set of elements present."""
        return self._map.keys

    def contains(self, e: Expr) -> Expr:
        """``BagIn(e, B) == e \\in DOMAIN B`` --- whether any copy is present.

        Membership is a method (not Python ``in``): ``__contains__`` is forced
        to return a ``bool``, which a symbolic expression cannot, mirroring
        ``SetExpr.contains``.
        """
        return self._map.keys.contains(e)

    def __getitem__(self, e: Expr) -> Expr:
        """``CopiesIn(e, B) == IF e \\in DOMAIN B THEN B[e] ELSE 0``."""
        return Ite(self._map.keys.contains(e), self._map[e], 0)

    @property
    def cardinality(self) -> Expr:
        """``BagCardinality(B)`` --- the total number of copies (sum of counts)."""
        return self._map.reduce(lambda acc, k, v: acc + v, 0)

    @property
    def is_empty(self) -> Expr:
        """Whether the bag has no elements at all."""
        return self._map.is_empty

    # --- combinators ---------------------------------------------------------

    def __add__(self, other: "Bag") -> "Bag":
        """``B1 (+) B2`` --- bag union: counts are added over the union of domains."""
        keys = self._map.keys | other._map.keys
        return Bag(keys.map_to(lambda e: self[e] + other[e]))

    def __sub__(self, other: "Bag") -> "Bag":
        """``B1 (-) B2`` --- bag difference: subtract counts, drop non-positive ones."""
        keys = self._map.keys.filter(lambda e: self[e] - other[e] > 0)
        return Bag(keys.map_to(lambda e: self[e] - other[e]))

    def __le__(self, other: "Bag") -> Expr:
        """Sub-bag relation: ``\\A e \\in DOMAIN B1 : B1[e] <= CopiesIn(e, B2)``."""
        return self._map.keys.forall(lambda e: self[e] <= other[e])

    def __ge__(self, other: "Bag") -> Expr:
        """Super-bag relation: ``other <= self``."""
        return other <= self

    def __eq__(self, other: object) -> Expr:  # type: ignore[override]
        """Bag equality, i.e. equality of the underlying maps."""
        if not isinstance(other, Bag):
            return NotImplemented
        return self._map == other._map

    def add_one(self, e: Expr) -> "Bag":
        """Add a single copy of ``e`` (handles both new and existing elements)."""
        return self + Bag.from_set(Set(e))

    def remove_one(self, e: Expr) -> "Bag":
        """Remove a single copy of ``e`` (drops the element when its count hits 0)."""
        return self - Bag.from_set(Set(e))

    def map(self, f: Callable[[Expr], Expr]) -> "Bag":
        """``BagOfAll(F, B)`` --- apply ``f`` to every element, preserving multiplicity.

        EXPERIMENTAL. The count of a result element ``y`` is the sum of the
        counts of every source element ``k`` with ``f(k) == y``. This lowers to
        a nested set-map / reduce and is therefore quadratic; prefer the cheaper
        operations in hot paths.
        """
        new_keys = self._map.keys.map(f)
        return Bag(
            new_keys.map_to(
                lambda y: self._map.reduce(
                    lambda acc, k, v: acc + Ite(f(k) == y, v, 0), 0
                )
            )
        )


def BagUnion(bags: "list[Bag]") -> Bag:
    """``BagUnion(S)`` --- fold ``(+)`` over a Python list of bags.

    EXPERIMENTAL. This folds in Python over a *static* list of bags, which is
    the common case. A fully symbolic union over a ``SetExpr`` of bag-maps is
    also expressible as a single ``.reduce`` over that set, but it is verbose
    and rarely needed, so it is intentionally left out here.
    """
    if not bags:
        raise ValueError("BagUnion requires at least one bag (to fix the element sort)")
    result = bags[0]
    for b in bags[1:]:
        result = result + b
    return result
