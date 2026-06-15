"""
Pure AST nodes for list expressions.

These are logical terms as data structures, no rich operator overloading and methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .ast import Node, VarNode
from .sorts import BoolSort, IntSort, ListSort, SetSort, Sort


class ListNode(Node, ABC):
    """Abstract base class for list AST nodes parameterized by element type."""

    def __init__(self, elem_sort: Sort):
        super().__init__(ListSort(elem_sort))
        self.elem_sort = elem_sort

    @abstractmethod
    def __repr__(self):
        """Subclasses must implement their own representation."""
        pass


class ListEnumNode(ListNode):
    """List node with explicitly enumerated elements."""

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
            return f"List({', '.join(repr(e) for e in self.elements)})"
        else:
            return f"List({repr(self.elem_sort)})"

    def __eq__(self, other):
        if not isinstance(other, ListEnumNode):
            return False

        return self.sort == other.sort and self.elements == other.elements

    def __hash__(self):
        return hash((self.sort, self.elements))


class ListRangeNode(ListNode):
    """List range node: list of integers [lower, upper) (lower included, upper excluded)."""

    def __init__(self, lower: Node, upper: Node):
        if lower.sort != IntSort():
            raise TypeError(f"Lower bound must have IntSort, got {lower.sort}")
        if upper.sort != IntSort():
            raise TypeError(f"Upper bound must have IntSort, got {upper.sort}")
        super().__init__(IntSort())
        self.lower = lower
        self.upper = upper

    def __repr__(self):
        return f"Range({repr(self.lower)}, {repr(self.upper)})"

    def __eq__(self, other):
        if not isinstance(other, ListRangeNode):
            return False

        return (
            self.sort == other.sort
            and self.lower == other.lower
            and self.upper == other.upper
        )

    def __hash__(self):
        return hash((self.sort, self.lower, self.upper))


class ListGetNode(Node):
    """List element access node: list[index].

    The result sort is the element sort of the list.
    """

    def __init__(self, list_node: Node, index: Node):
        if not isinstance(list_node.sort, ListSort):
            raise TypeError(f"List node must have ListSort, got {list_node.sort}")
        if index.sort != IntSort():
            raise TypeError(f"Index must have IntSort, got {index.sort}")

        super().__init__(list_node.sort.elem_sort)
        self.list_node = list_node
        self.index = index

    def __repr__(self):
        return f"ListGet({repr(self.list_node)}, {repr(self.index)})"

    def __eq__(self, other):
        if not isinstance(other, ListGetNode):
            return False

        return (
            self.sort == other.sort
            and self.list_node == other.list_node
            and self.index == other.index
        )

    def __hash__(self):
        return hash((self.sort, self.list_node, self.index))


class ListUpdateNode(ListNode):
    """List update node: list with element at index replaced by a new value."""

    def __init__(self, base_list: Node, index: Node, new_value: Node):
        if not isinstance(base_list.sort, ListSort):
            raise TypeError(f"Base list must have ListSort, got {base_list.sort}")
        if index.sort != IntSort():
            raise TypeError(f"Index must have IntSort, got {index.sort}")

        elem_sort = base_list.sort.elem_sort
        if new_value.sort != elem_sort:
            raise TypeError(
                f"New value has sort {new_value.sort}, expected {elem_sort}"
            )

        super().__init__(elem_sort)
        self.base_list = base_list
        self.index = index
        self.new_value = new_value

    def __repr__(self):
        return f"ListUpdate({repr(self.base_list)}, {repr(self.index)}, {repr(self.new_value)})"

    def __eq__(self, other):
        if not isinstance(other, ListUpdateNode):
            return False

        return (
            self.sort == other.sort
            and self.base_list == other.base_list
            and self.index == other.index
            and self.new_value == other.new_value
        )

    def __hash__(self):
        return hash((self.sort, self.base_list, self.index, self.new_value))


class ListSliceNode(ListNode):
    """List slice node: list[start:end] (start inclusive, end exclusive)."""

    def __init__(self, base_list: Node, start: Node, end: Node):
        if not isinstance(base_list.sort, ListSort):
            raise TypeError(f"Base list must have ListSort, got {base_list.sort}")
        if start.sort != IntSort():
            raise TypeError(f"Start must have IntSort, got {start.sort}")
        if end.sort != IntSort():
            raise TypeError(f"End must have IntSort, got {end.sort}")

        super().__init__(base_list.sort.elem_sort)
        self.base_list = base_list
        self.start = start
        self.end = end

    def __repr__(self):
        return (
            f"ListSlice({repr(self.base_list)}, {repr(self.start)}, {repr(self.end)})"
        )

    def __eq__(self, other):
        if not isinstance(other, ListSliceNode):
            return False

        return (
            self.sort == other.sort
            and self.base_list == other.base_list
            and self.start == other.start
            and self.end == other.end
        )

    def __hash__(self):
        return hash((self.sort, self.base_list, self.start, self.end))


class ListFilterNode(ListNode):
    """List filter node: elements of list for which predicate holds."""

    def __init__(self, base_list: Node, var: VarNode, predicate: Node):
        if not isinstance(base_list.sort, ListSort):
            raise TypeError(f"Base list must have ListSort, got {base_list.sort}")
        if not isinstance(predicate.sort, BoolSort):
            raise TypeError(f"Predicate must have BoolSort, got {predicate.sort}")

        super().__init__(base_list.sort.elem_sort)
        self.base_list = base_list
        self.var = var
        self.predicate = predicate

    def __repr__(self):
        return f"ListFilter({repr(self.var)}, {repr(self.base_list)}, {repr(self.predicate)})"

    def __eq__(self, other):
        if not isinstance(other, ListFilterNode):
            return False

        return (
            self.sort == other.sort
            and self.base_list == other.base_list
            and self.var == other.var
            and self.predicate == other.predicate
        )

    def __hash__(self):
        return hash((self.sort, self.base_list, self.var, self.predicate))


class ListReduceNode(Node):
    """List reduce node: reduce list using a binary function and initial value.

    Unlike set reduce, list reduce processes elements in order.
    """

    def __init__(
        self,
        base_list: Node,
        acc_var: VarNode,
        elem_var: VarNode,
        fun: Node,
        initial: Node,
    ):
        if not isinstance(base_list.sort, ListSort):
            raise TypeError(f"Base list must have ListSort, got {base_list.sort}")

        super().__init__(fun.sort)
        self.base_list = base_list
        self.acc_var = acc_var
        self.elem_var = elem_var
        self.fun = fun
        self.initial = initial

    def __repr__(self):
        return (
            f"ListReduce(({repr(self.acc_var)}, {repr(self.elem_var)}),"
            f" {repr(self.base_list)}, {repr(self.fun)}, {repr(self.initial)})"
        )

    def __eq__(self, other):
        if not isinstance(other, ListReduceNode):
            return False

        return (
            self.sort == other.sort
            and self.base_list == other.base_list
            and self.acc_var == other.acc_var
            and self.elem_var == other.elem_var
            and self.fun == other.fun
            and self.initial == other.initial
        )

    def __hash__(self):
        return hash(
            (
                self.sort,
                self.base_list,
                self.acc_var,
                self.elem_var,
                self.fun,
                self.initial,
            )
        )


class ListKeysNode(Node):
    """List keys node: the set of all valid indices of a list (starting from 0)."""

    def __init__(self, list_node: Node):
        if not isinstance(list_node.sort, ListSort):
            raise TypeError(f"List node must have ListSort, got {list_node.sort}")

        super().__init__(SetSort(IntSort()))
        self.list_node = list_node

    def __repr__(self):
        return f"ListKeys({repr(self.list_node)})"

    def __eq__(self, other):
        if not isinstance(other, ListKeysNode):
            return False

        return self.sort == other.sort and self.list_node == other.list_node

    def __hash__(self) -> int:
        return hash((self.sort, self.list_node))
