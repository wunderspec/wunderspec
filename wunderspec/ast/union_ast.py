"""
Union AST nodes for tagged union (sum type) expressions.

Provides nodes for constructing union values, accessing the tag,
and pattern matching on union expressions.
"""

from __future__ import annotations

from .ast import Node, VarNode
from .sorts import Sort, StrSort, UnionSort


class UnionCtorNode(Node):
    """Union constructor: creates a variant with a tag and optional payload.

    Example:
        UnionCtorNode(option_sort, "Some", payload_node)
        UnionCtorNode(option_sort, "None_", None)
    """

    def __init__(self, union_sort: UnionSort, tag: str, payload: Node | None = None):
        if tag not in union_sort:
            raise ValueError(f"Tag '{tag}' is not a variant of {union_sort.name}")
        expected_payload_sort = union_sort[tag]
        if expected_payload_sort is None and payload is not None:
            raise TypeError(f"Variant '{tag}' takes no payload, but one was provided")
        if expected_payload_sort is not None and payload is None:
            raise TypeError(
                f"Variant '{tag}' requires a payload of sort {expected_payload_sort.name}"
            )
        if expected_payload_sort is not None and payload is not None:
            if payload.sort != expected_payload_sort:
                raise TypeError(
                    f"Variant '{tag}' expects payload sort {expected_payload_sort.name}, "
                    f"got {payload.sort.name}"
                )
        super().__init__(union_sort)
        self.tag = tag
        self.payload = payload

    def __repr__(self):
        if self.payload is not None:
            return f"UnionCtor({repr(self.sort)}, {self.tag!r}, {repr(self.payload)})"
        return f"UnionCtor({repr(self.sort)}, {self.tag!r})"

    def __eq__(self, other):
        if not isinstance(other, UnionCtorNode):
            return False

        return (
            self.sort == other.sort
            and self.tag == other.tag
            and self.payload == other.payload
        )

    def __hash__(self):
        return hash((self.sort, self.tag, self.payload))


class UnionGetTagNode(Node):
    """Access the tag of a union expression as a string."""

    def __init__(self, union_node: Node):
        if not isinstance(union_node.sort, UnionSort):
            raise TypeError(f"Expected UnionSort, got {type(union_node.sort).__name__}")
        super().__init__(StrSort())
        self.union_node = union_node

    def __repr__(self):
        return f"UnionGetTag({repr(self.union_node)})"

    def __eq__(self, other):
        if not isinstance(other, UnionGetTagNode):
            return False

        return self.sort == other.sort and self.union_node == other.union_node

    def __hash__(self):
        return hash((self.sort, self.union_node))


class UnionMatchNode(Node):
    """Pattern matching on a union expression.

    cases: dict mapping tag -> (var_node | None, body_node)
    - var_node is bound to the payload (None for no-payload variants)
    - body_node is the result expression for that case
    - All cases must have the same result sort
    - Must be exhaustive (all variants covered)
    """

    def __init__(
        self,
        union_node: Node,
        cases: dict[str, tuple[VarNode | None, Node]],
    ):
        if not isinstance(union_node.sort, UnionSort):
            raise TypeError(
                f"Expected UnionSort for union_node, got {type(union_node.sort).__name__}"
            )
        union_sort: UnionSort = union_node.sort  # type: ignore[assignment]

        # Check exhaustiveness
        variant_tags = set(tag for tag, _ in union_sort.variants)
        case_tags = set(cases.keys())
        missing = variant_tags - case_tags
        if missing:
            raise ValueError(
                f"Non-exhaustive match: missing cases for {', '.join(sorted(missing))}"
            )
        extra = case_tags - variant_tags
        if extra:
            raise ValueError(f"Unknown variants in match: {', '.join(sorted(extra))}")

        # Validate each case
        result_sort: Sort | None = None
        for tag, (var_node, body_node) in cases.items():
            payload_sort = union_sort[tag]
            if payload_sort is None and var_node is not None:
                raise TypeError(
                    f"Variant '{tag}' has no payload, but a variable was provided"
                )
            if payload_sort is not None:
                if var_node is None:
                    raise TypeError(
                        f"Variant '{tag}' has payload sort {payload_sort.name}, "
                        f"but no variable was provided"
                    )
                if var_node.sort != payload_sort:
                    raise TypeError(
                        f"Variable sort {var_node.sort.name} does not match "
                        f"payload sort {payload_sort.name} for variant '{tag}'"
                    )

            if result_sort is None:
                result_sort = body_node.sort
            elif body_node.sort != result_sort:
                raise TypeError(
                    f"All match cases must have the same result sort. "
                    f"Expected {result_sort.name}, got {body_node.sort.name} "
                    f"in case '{tag}'"
                )

        assert result_sort is not None
        super().__init__(result_sort)
        self.union_node = union_node
        self.cases = cases

    def __repr__(self):
        # Output format: UnionMatch(union_node, tag1=body1, tag2=body2)
        # Note: This simplified format omits the variable binding information
        # but allows for round-trip via the UnionMatch constructor
        case_strs = []
        for tag in sorted(self.cases.keys()):
            var, body = self.cases[tag]
            case_strs.append(f"{tag}={repr(body)}")
        return f"UnionMatch({repr(self.union_node)}, {', '.join(case_strs)})"

    def __eq__(self, other):
        if not isinstance(other, UnionMatchNode):
            return False

        return (
            self.sort == other.sort
            and self.union_node == other.union_node
            and self.cases == other.cases
        )

    def __hash__(self):
        return hash((self.sort, self.union_node, frozenset(self.cases.items())))
