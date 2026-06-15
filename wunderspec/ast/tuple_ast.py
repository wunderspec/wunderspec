"""AST nodes for tuple types.

Tuples are immutable sequences with positional elements of potentially
different sorts. Unlike records, tuples use integer indices rather than
field names.

Igor Konnov, 2025
"""

from wunderspec.ast.ast import Node
from wunderspec.ast.sorts import TupleSort


class TupleNode(Node):
    """Abstract base class for tuple AST nodes."""

    def __init__(self, tuple_sort: TupleSort):
        """Initialize a tuple node with a tuple sort."""
        super().__init__(tuple_sort)


class TupleCtorNode(TupleNode):
    """Tuple constructor node: creates a tuple from element values.

    Example:
        pair = TupleCtorNode(IntLitNode(42), BoolLitNode(True))
        # Represents the tuple (42, True)
    """

    def __init__(self, *elements: Node):
        """Initialize a tuple constructor node.

        Args:
            *elements: Variable number of element nodes.

        Raises:
            ValueError: If no elements provided.
        """
        if not elements:
            raise ValueError("Tuple must have at least one element")

        # Extract sorts from the element nodes
        elem_sorts = tuple(node.sort for node in elements)
        tuple_sort = TupleSort(*elem_sorts)

        super().__init__(tuple_sort)

        # Store elements as a tuple
        self.elements = elements

    def __getitem__(self, index: int) -> Node:
        """Get the value node of an element by index."""
        return self.elements[index]

    def __len__(self) -> int:
        """Get the number of elements in this tuple."""
        return len(self.elements)

    def __contains__(self, index: int) -> bool:
        """Check if an index is valid in this tuple."""
        return 0 <= index < len(self.elements)

    def __repr__(self):
        if self.elements:
            items = ", ".join(repr(node) for node in self.elements)
            return f"Tuple({items})"
        else:
            return "Tuple()"

    def __eq__(self, other):
        if not isinstance(other, TupleCtorNode):
            return False

        return self.sort == other.sort and self.elements == other.elements

    def __hash__(self):
        return hash((self.sort, self.elements))


class TupleUpdateNode(TupleNode):
    """Tuple update node: tuple with specified element updated to a new value.

    This node represents a tuple with one element updated. The base tuple
    can be any TupleNode (constructor, variable, or another update).

    Example:
        updated = TupleUpdateNode(base_tuple, 0, new_value_node)
        # Represents base_tuple with element 0 replaced by new_value
    """

    def __init__(self, base_tuple: Node, index: int, new_value: Node):
        """Initialize a tuple update node.

        Args:
            base_tuple: The base tuple node to update (must have TupleSort).
            index: The index of the element to update (must be valid).
            new_value: The new value for the element (must have matching sort).

        Raises:
            TypeError: If index is invalid or sort doesn't match.
        """
        if not isinstance(base_tuple.sort, TupleSort):
            raise TypeError(f"Base tuple must have TupleSort, got {base_tuple.sort}")

        if index not in base_tuple.sort:
            raise TypeError(
                f"Index {index} is out of bounds for tuple of length {len(base_tuple.sort)}"
            )

        expected_sort = base_tuple.sort[index]
        if new_value.sort != expected_sort:
            raise TypeError(
                f"Element at index {index} has sort {expected_sort}, "
                f"but got value with sort {new_value.sort}"
            )

        super().__init__(base_tuple.sort)
        self.base_tuple = base_tuple
        self.index = index
        self.new_value = new_value

    def __repr__(self):
        return f"TupleUpdate({repr(self.base_tuple)}, {self.index}, {repr(self.new_value)})"

    def __eq__(self, other):
        if not isinstance(other, TupleUpdateNode):
            return False

        return (
            self.sort == other.sort
            and self.base_tuple == other.base_tuple
            and self.index == other.index
            and self.new_value == other.new_value
        )

    def __hash__(self):
        return hash((self.sort, self.base_tuple, self.index, self.new_value))


class TupleGetNode(Node):
    """Tuple element access node: tuple[index].

    This node represents accessing a single element from a tuple by index.
    The result sort is the sort of the accessed element.

    Example:
        first_elem = TupleGetNode(pair_tuple, 0)
        # Accesses element 0 of pair_tuple
    """

    def __init__(self, tuple_node: Node, index: int):
        """Initialize a tuple element access node.

        Args:
            tuple_node: The tuple node to access (must have TupleSort).
            index: The index of the element to access (must be valid).

        Raises:
            TypeError: If the index is out of bounds.
        """
        if not isinstance(tuple_node.sort, TupleSort):
            raise TypeError(f"Tuple node must have TupleSort, got {tuple_node.sort}")

        if index not in tuple_node.sort:
            raise TypeError(
                f"Index {index} is out of bounds for tuple of length {len(tuple_node.sort)}"
            )

        # The result type is the sort of the accessed element
        elem_sort = tuple_node.sort[index]
        super().__init__(elem_sort)

        self.tuple_node = tuple_node
        self.index = index

    def __repr__(self):
        return f"TupleGet({repr(self.tuple_node)}, {self.index})"

    def __eq__(self, other):
        if not isinstance(other, TupleGetNode):
            return False

        return (
            self.sort == other.sort
            and self.tuple_node == other.tuple_node
            and self.index == other.index
        )

    def __hash__(self):
        return hash((self.sort, self.tuple_node, self.index))
