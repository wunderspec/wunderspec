"""
AST nodes for temporal operators.

Igor Konnov, 2026
"""

from abc import ABC
from collections.abc import Sequence
from enum import Enum

from wunderspec.ast.ast import Node
from wunderspec.ast.sorts import TemporalSort


class TemporalNode(Node, ABC):
    """Abstract base class for all temporal AST nodes."""

    def __init__(self):
        super().__init__(TemporalSort())


class ToTemporalNode(TemporalNode):
    """
    AST node that lifts a Boolean expression to a temporal formula.

    In temporal logic, a Boolean expression p can be viewed as a temporal
    formula that evaluates p in the current state. This node wraps a
    BoolSort node to give it TemporalSort, enabling mixing of Boolean
    and temporal expressions in temporal operators.
    """

    def __init__(self, bool_formula: Node):
        super().__init__()
        self.bool_formula = bool_formula

    def __repr__(self):
        return f"ToTemporal({self.bool_formula!r})"

    def __eq__(self, other):
        if not isinstance(other, ToTemporalNode):
            return False

        return self.sort == other.sort and self.bool_formula == other.bool_formula

    def __hash__(self):
        return hash((self.sort, self.bool_formula))


class AlwaysNode(TemporalNode):
    """
    AST node for the 'always' operator, which is often
    denoted by □ or G in temporal logics.
    """

    def __init__(self, subformula: Node):
        super().__init__()
        self.subformula = subformula

    def __repr__(self):
        return f"Always({self.subformula!r})"

    def __eq__(self, other):
        if not isinstance(other, AlwaysNode):
            return False

        return self.sort == other.sort and self.subformula == other.subformula

    def __hash__(self):
        return hash((self.sort, self.subformula))


class EventuallyNode(TemporalNode):
    """
    AST node for the 'eventually' operator, which is often
    denoted by ◇ or F in temporal logics.
    """

    def __init__(self, subformula: Node):
        super().__init__()
        self.subformula = subformula

    def __repr__(self):
        return f"Eventually({self.subformula!r})"

    def __eq__(self, other):
        if not isinstance(other, EventuallyNode):
            return False

        return self.sort == other.sort and self.subformula == other.subformula

    def __hash__(self):
        return hash((self.sort, self.subformula))


class EnabledNode(TemporalNode):
    """
    AST node for the 'enabled' operator, which checks whether
    an action is enabled in the current state.

    In TLA+, this is denoted by `ENABLED A` for an action A.
    """

    def __init__(self, action: Node):
        super().__init__()
        self.action = action

    def __repr__(self):
        return f"Enabled({self.action!r})"

    def __eq__(self, other):
        if not isinstance(other, EnabledNode):
            return False

        return self.sort == other.sort and self.action == other.action

    def __hash__(self):
        return hash((self.sort, self.action))


class Fair(Enum):
    """
    Enum for the kinds of fairness: weak and strong.
    """

    WEAK = "weak"
    STRONG = "strong"

    def __repr__(self):
        return f"Fair.{self.name}"


class FairnessNode(TemporalNode):
    """
    AST node for the fairness operator, either weak or strong.
    This node needs an action as an operand, as well as, the names of the
    state variables that must or may stutter.

    **Weak fairness**: If an action is always enabled from some point on,
    it must eventually be taken.

    **Strong fairness**: If an action is enabled infinitely often, it must
    eventually be taken.

    In TLA+, the corresponding operators are `WF_<<x_1, ..., x_n>>(A)`
    and `SF_<<x_1, ..., x_n>>(A)`.
    """

    def __init__(
        self, fairness_kind: Fair, action: Node, stuttering_vars: Sequence[str]
    ):
        super().__init__()
        self.kind = fairness_kind
        self.action = action
        self.stuttering_vars = tuple(stuttering_vars)

    def __repr__(self):
        return f"Fairness({self.kind!r}, {self.action!r}, {self.stuttering_vars!r})"

    def __eq__(self, other):
        if not isinstance(other, FairnessNode):
            return False

        return (
            self.sort == other.sort
            and self.action == other.action
            and self.stuttering_vars == other.stuttering_vars
            and self.kind == other.kind
        )

    def __hash__(self):
        return hash((self.sort, self.action, tuple(self.stuttering_vars), self.kind))
