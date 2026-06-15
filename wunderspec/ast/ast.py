"""
Base classes for AST nodes.

These are the fundamental building blocks that don't depend on any
specific node type. All classes here are pure data structures.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .sorts import BoolSort, EnumSort, IntSort, Sort, StrSort


class AlgebraOp(Enum):
    """All operators for algebra expressions."""

    # Arithmetic operators
    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"
    MOD = "%"
    POW = "**"
    NEG = "neg"

    # Relational (comparison) operators
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    EQ = "=="
    NE = "!="

    # Boolean (logical) operators, they are also used in temporal formulas
    AND = "And"
    OR = "Or"
    NOT = "Not"
    IMPLIES = "Implies"
    IFF = "Iff"

    # Set algebra operators
    UNION = "Union"
    INTERSECT = "Intersect"
    DIFFERENCE = "Difference"
    CARDINALITY = "Cardinality"
    SUBSETEQ = "SubsetEq"
    FLATTEN = "Flatten"

    # List operators
    LIST_CONCAT = "ListConcat"
    LIST_SIZE = "ListSize"


# Integer arithmetic of IntSort
ARITH_OPS = {
    AlgebraOp.ADD,
    AlgebraOp.SUB,
    AlgebraOp.MUL,
    AlgebraOp.DIV,
    AlgebraOp.MOD,
    AlgebraOp.POW,
    AlgebraOp.NEG,
}
# Ordering comparisons (over integers only), of BoolSort
CMP_OPS = {AlgebraOp.LT, AlgebraOp.LE, AlgebraOp.GT, AlgebraOp.GE}
# Equality (any sort)
EQ_OPS = {AlgebraOp.EQ, AlgebraOp.NE}
# All relational operators of BoolSort
REL_OPS = CMP_OPS | EQ_OPS
# Boolean operators that can produce nodes of BoolSort and TemporalSort
BOOL_OPS = {
    AlgebraOp.AND,
    AlgebraOp.OR,
    AlgebraOp.NOT,
    AlgebraOp.IMPLIES,
    AlgebraOp.IFF,
}
# Set operators of SetSort or BoolSort
SET_OPS = {
    AlgebraOp.UNION,
    AlgebraOp.INTERSECT,
    AlgebraOp.DIFFERENCE,
    AlgebraOp.CARDINALITY,
    AlgebraOp.SUBSETEQ,
    AlgebraOp.FLATTEN,
}
# List operators of ListSort or IntSort
LIST_OPS = {AlgebraOp.LIST_CONCAT, AlgebraOp.LIST_SIZE}


class QuantOp(Enum):
    """Kinds of quantifiers."""

    FORALL = "Forall"
    EXISTS = "Exists"


@dataclass(frozen=True)
class SourceSpan:
    """
    Source code span for error reporting and debugging.  While this may seem
    expensive to do source tracking for every node, these nodes are normally
    computed once and then used many times, so the cost is amortized.
    """

    filename: str | None
    lineno: int
    col_offset: int
    end_lineno: int
    end_col_offset: int


# Set by wunderspec.source_tracking on import to enable automatic span capture.
_source_span_hook: Callable[[], SourceSpan | None] | None = None


class Node:
    """Base class for all AST nodes.

    This is a pure data structure with no operator logic.
    """

    def __init__(self, sort: Sort):
        self.sort = sort
        hook = _source_span_hook
        self.source_span: SourceSpan | None = hook() if hook is not None else None
        self._hash: int | None = None

    def __repr__(self):
        return f"{self.__class__.__name__}(...)"

    def pretty(self, max_width: int = 80) -> str:
        """Pretty print this AST node.

        Args:
            max_width: Maximum line width for formatting (default: 80).

        Returns:
            A nicely formatted string representation.
        """
        # This import is here to avoid circular imports
        from wunderspec.pretty import pretty

        return pretty(self, max_width)

    def _repr_pretty_(self, p, cycle):
        """IPython pretty printing support."""
        if cycle:
            p.text(f"{self.__class__.__name__}(...)")
        else:
            # Use the simple pretty printer and feed it to IPython's printer.
            # This import is here to avoid circular imports.
            from wunderspec.pretty import _simple_pretty

            p.text(_simple_pretty(self, indent=0, max_width=p.max_width))

    def __rich__(self) -> Any:
        """rich rendering support (only invoked when rich is installed)."""
        from wunderspec.pretty import to_rich

        return to_rich(self.pretty())


class VarNode(Node):
    """Variable node for any sort.

    This is the unified variable node class. The type of variable
    can be determined by checking its sort.
    """

    def __init__(self, name: str, sort: Sort, unique_name: str | None = None) -> None:
        super().__init__(sort)
        self.name = name
        # Internal binder identity used by interpreters to avoid variable capture.
        # Keep this out of string/repr/equality to preserve user-facing behavior.
        self.unique_name = unique_name

    def __str__(self):
        """Short format for display."""
        return f"Var({self.name})"

    def __repr__(self):
        """Full format for round-trip (can be eval'd back)."""
        return f"Var({self.name!r}, {repr(self.sort)})"

    def __eq__(self, other):
        if not isinstance(other, VarNode):
            return False

        return self.sort == other.sort and self.name == other.name

    def __hash__(self):
        if self._hash is None:
            self._hash = hash((self.sort, self.name))
        return self._hash


class LetNode(Node):
    """Let-binding node.

    Binds a variable to a value within a scope. No parameters are supported.
    """

    def __init__(self, name: str, value: Node, body: Node):
        super().__init__(body.sort)
        self.name = name
        self.value = value
        self.body = body

    def __str__(self):
        """Short format for display."""
        return f"Let({repr(self.name)}, {str(self.value)}, {str(self.body)})"

    def __repr__(self):
        """Full format for round-trip."""
        return f"Let({repr(self.name)}, {repr(self.value)}, {repr(self.body)})"

    def __eq__(self, other):
        if not isinstance(other, LetNode):
            return False

        return (
            self.sort == other.sort
            and self.name == other.name
            and self.value == other.value
            and self.body == other.body
        )

    def __hash__(self):
        if self._hash is None:
            self._hash = hash((self.sort, self.name, self.value, self.body))
        return self._hash


class ExprCallNode(Node):
    """Call to a non-inline @expr operator.

    Represents a call to an expression extracted as a separate TLA+ operator.
    TLA+ call sites render as ``OpName`` or ``OpName(arg1, arg2, ...)``.
    """

    def __init__(
        self,
        op_name: str,
        args: tuple[Node, ...],
        body: Node,
        param_names: tuple[str, ...],
    ):
        super().__init__(body.sort)
        self.op_name = op_name
        self.args = args  # actual arg nodes at call site
        self.body = body  # definition body using VarNodes for params
        self.param_names = param_names

    def __str__(self):
        args_str = ", ".join(str(a) for a in self.args)
        return f"ExprCall({self.op_name!r}, ({args_str}), {str(self.body)})"

    def __repr__(self):
        args_repr = ", ".join(repr(a) for a in self.args)
        params_repr = ", ".join(repr(p) for p in self.param_names)
        return f"ExprCall({self.op_name!r}, ({args_repr}), {repr(self.body)}, ({params_repr}))"

    def __eq__(self, other):
        if not isinstance(other, ExprCallNode):
            return False
        return (
            self.sort == other.sort
            and self.op_name == other.op_name
            and self.args == other.args
            and self.body == other.body
            and self.param_names == other.param_names
        )

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(
                (self.sort, self.op_name, self.args, self.body, self.param_names)
            )
        return self._hash


class AlgebraNode(Node):
    """
    Unified algebra node for a large subset of operators. The operators that
    have special forms or require special treatment have their own node classes.

    This handles a large subset of arithmetic, relational, boolean, set, list
    and temporal operations. We aggregate these operations in a single class to
    avoid the class bloat. The specific operation type can be determined by
    checking the op type. All arguments must have the same sort.
    """

    def __init__(self, result_sort: Sort, op: AlgebraOp, *args: Node):
        """
        Construct an algebra node. This constructor does not validate
        that the operation is valid for the given sorts. This validation
        is done at the expression level.
        """
        super().__init__(result_sort)
        self.op = op
        self.args = args
        # Validate that all args have the same sort
        if len(args) > 1:
            first_sort = args[0].sort
            for i, arg in enumerate(args[1:], start=1):
                if arg.sort != first_sort:
                    raise TypeError(
                        f"All arguments to {op} must have the same sort, "
                        f"but argument 0 has sort {first_sort} and argument {i} has sort {arg.sort}"
                    )

    def __str__(self):
        """Short format for display."""
        return f"{self.op.name}({', '.join(str(arg) for arg in self.args)})"

    def __repr__(self):
        """Full format for round-trip."""
        return f"{self.op.name}({', '.join(repr(arg) for arg in self.args)})"

    def __eq__(self, other):
        if not isinstance(other, AlgebraNode):
            return False

        return (
            self.sort == other.sort and self.op == other.op and self.args == other.args
        )

    def __hash__(self):
        if self._hash is None:
            self._hash = hash((self.sort, self.op, self.args))
        return self._hash


class LitNode(Node):
    """Literal node for any sort (int, bool, str, enum).

    The sort is inferred from the value type:
    - bool -> BoolSort
    - str -> StrSort
    - Enum member -> EnumSort(type(value))
    - int -> IntSort
    """

    def __init__(self, value: int | bool | str | Enum):
        # Infer sort from value type (check bool first since bool is subclass of int)
        sort: BoolSort | StrSort | EnumSort | IntSort
        match value:
            case bool():
                sort = BoolSort()
            case str():
                sort = StrSort()
            case int():
                sort = IntSort()
            case Enum():
                sort = EnumSort(type(value))
            case _:
                raise TypeError(f"Cannot create literal from {type(value).__name__}")

        super().__init__(sort)  # type: ignore[arg-type]
        self.value = value

    def __str__(self):
        """Short format for display (backward compatible)."""
        if isinstance(self.value, Enum):
            return f"{type(self.value).__name__}.{self.value.name}"
        elif isinstance(self.value, str):
            return repr(self.value)
        return f"({self.value})"

    def __repr__(self):
        """Full format for round-trip (can be eval'd back)."""
        if isinstance(self.value, Enum):
            return f"Lit({type(self.value).__name__}.{self.value.name})"
        return f"Lit({self.value!r})"

    def __eq__(self, other):
        if not isinstance(other, LitNode):
            return False

        return self.sort == other.sort and self.value == other.value

    def __hash__(self):
        if self._hash is None:
            self._hash = hash((self.sort, self.value))
        return self._hash


class InNode(Node):
    """Set membership node: elem ∈ set.

    This is separate from AlgebraNode because the element and set
    have different sorts.
    """

    def __init__(self, elem: Node, set_node: Node):
        super().__init__(BoolSort())
        self.elem = elem
        self.set_node = set_node

    def __str__(self):
        """Short format for display."""
        return f"In({str(self.elem)}, {str(self.set_node)})"

    def __repr__(self):
        """Full format for round-trip."""
        return f"In({repr(self.elem)}, {repr(self.set_node)})"

    def __eq__(self, other):
        if not isinstance(other, InNode):
            return False

        return (
            self.sort == other.sort
            and self.elem == other.elem
            and self.set_node == other.set_node
        )

    def __hash__(self):
        if self._hash is None:
            self._hash = hash((self.sort, self.elem, self.set_node))
        return self._hash


class IteNode(Node):
    """If-then-else node."""

    def __init__(self, condition: Node, then_node: Node, else_node: Node):
        if not isinstance(condition.sort, BoolSort):
            raise TypeError(f"Condition must be boolean, got {condition.sort}")
        if then_node.sort != else_node.sort:
            raise TypeError(
                f"then_node and else_node are of different sorts: {then_node.sort} and {else_node.sort}"
            )
        super().__init__(then_node.sort)
        self.condition = condition
        self.then_node = then_node
        self.else_node = else_node

    def __str__(self):
        """Short format for display."""
        return (
            f"Ite({str(self.condition)}, {str(self.then_node)}, {str(self.else_node)})"
        )

    def __repr__(self):
        """Full format for round-trip."""
        return f"Ite({repr(self.condition)}, {repr(self.then_node)}, {repr(self.else_node)})"

    def __eq__(self, other):
        if not isinstance(other, IteNode):
            return False

        return (
            self.sort == other.sort
            and self.condition == other.condition
            and self.then_node == other.then_node
            and self.else_node == other.else_node
        )

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(
                (self.sort, self.condition, self.then_node, self.else_node)
            )
        return self._hash
