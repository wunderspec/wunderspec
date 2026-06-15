"""
AST nodes for action-level statements.

While TLA+ does not syntactically distinguish between expressions,
actions, predicates, and temporal formulas, we do so in the AST for clarity.

Igor Konnov, 2026
"""

from abc import ABC

from wunderspec.ast.ast import Node
from wunderspec.ast.sorts import ActionSort, BoolSort, SetSort


class ActionNode(Node, ABC):
    """Abstract base class for all action-related AST nodes."""

    def __init__(self):
        super().__init__(ActionSort())


class AssumeNode(ActionNode):
    """
    An assumption node, simply a Boolean condition wrapped as an action
    that checks whether the condition holds.

    In TLA+, this is simply a Boolean expression written inside an action,
    e.g., as `∧ P`.
    """

    def __init__(self, condition: Node):
        if condition.sort != BoolSort():
            raise TypeError(
                f"Assume condition must have Bool sort, got {condition.sort}"
            )
        super().__init__()
        self.condition = condition

    def __repr__(self):
        return f"Assume({self.condition!r})"

    def __eq__(self, other):
        if not isinstance(other, AssumeNode):
            return False

        return self.sort == other.sort and self.condition == other.condition

    def __hash__(self):
        return hash((self.sort, self.condition))


class AssignNode(ActionNode):
    """
    An assignment action node: var = expr.

    This is similar to TLA+'s primed variable assignment: `x' = e`.
    In WunderSpec, we only have `x = e` in action context, which
    represents the next-state assignment. This assignment can be done
    at most once per variable in a branch of an action. This must be
    enforced by the implementations of the `Context` protocol, as long
    as they construct action nodes.
    """

    def __init__(self, var: Node, expr: Node):
        if var.sort != expr.sort:
            raise TypeError(
                f"Assignment sort mismatch: var {var} has sort {var.sort}, "
                f"rhs {expr} has sort {expr.sort}"
            )
        super().__init__()
        self.var = var
        self.expr = expr

    def __repr__(self):
        return f"Assign({self.var!r}, {self.expr!r})"

    def __eq__(self, other):
        if not isinstance(other, AssignNode):
            return False

        return (
            self.sort == other.sort
            and self.var == other.var
            and self.expr == other.expr
        )

    def __hash__(self):
        return hash((self.sort, self.var, self.expr))


class NondetChoiceNode(ActionNode):
    """
    A nondeterministic choice of an element from a set.

    This is similar to action-level `∃x ∈ S: P`.
    """

    def __init__(self, var: Node, base_set: Node, body: Node):
        if not isinstance(base_set.sort, SetSort):
            raise TypeError(f"Base set must have Set sort, got {base_set.sort}")
        if not var.sort == base_set.sort.elem_sort:
            raise TypeError(
                f"Variable sort {var.sort} does not match "
                f"base set element sort {base_set.sort.elem_sort}"
            )
        if not body.sort == ActionSort():
            raise TypeError(f"Body must have Action sort, got {body.sort}")
        super().__init__()
        self.var = var
        self.base_set = base_set
        self.body = body

    def __repr__(self):
        return f"NondetData({self.var!r}, {self.base_set!r}, {self.body!r})"

    def __eq__(self, other):
        if not isinstance(other, NondetChoiceNode):
            return False

        return (
            self.sort == other.sort
            and self.var == other.var
            and self.base_set == other.base_set
            and self.body == other.body
        )

    def __hash__(self):
        return hash((self.sort, self.var, self.base_set, self.body))


class ActionChoiceNode(ActionNode):
    """
    Alternative choice between multiple actions.

    This is what action-level `A_1 ∨ A_2 ∨ ... ∨ A_n` represents in TLA+.

    When ``labels`` is provided, each disjunct gets a TLA+ label
    (e.g. ``lab_start ::``).  Apalache propagates these labels back
    as transition metadata.
    """

    def __init__(self, *actions: ActionNode, labels: tuple[str, ...] | None = None):
        if any(not isinstance(a.sort, ActionSort) for a in actions):
            raise TypeError("All actions must have Action sort")
        if labels is not None and len(labels) != len(actions):
            raise ValueError(
                f"labels length ({len(labels)}) != actions length ({len(actions)})"
            )
        super().__init__()
        self.actions = actions
        self.labels = labels

    def __repr__(self):
        items = ", ".join(repr(a) for a in self.actions)
        return f"ActionChoice({items})"

    def __eq__(self, other):
        if not isinstance(other, ActionChoiceNode):
            return False

        return self.sort == other.sort and self.actions == other.actions

    def __hash__(self):
        return hash((self.sort, self.actions))


class ActionAndNode(ActionNode):
    """
    Conjunction of multiple actions.

    This is what action-level `A_1 ∧ A_2 ∧ ... ∧ A_n` represents in TLA+.
    """

    def __init__(self, *actions: ActionNode):
        if any(not isinstance(a.sort, ActionSort) for a in actions):
            raise TypeError("All actions must have Action sort")
        super().__init__()
        self.actions = actions

    def __repr__(self):
        items = ", ".join(repr(a) for a in self.actions)
        return f"ActionAnd({items})"

    def __eq__(self, other):
        if not isinstance(other, ActionAndNode):
            return False

        return self.sort == other.sort and self.actions == other.actions

    def __hash__(self):
        return hash((self.sort, self.actions))


class ActionCallNode(ActionNode):
    """
    A call to a named action (non-inline action).

    This represents a reference to an action that has been extracted as a
    separate operator definition. In TLA+, this translates to `ActionName`
    (for parameterless actions) or `ActionName(arg1, arg2, ...)` (for
    parameterized actions).
    """

    def __init__(
        self,
        action_name: str,
        args: tuple[Node, ...],
        body: "ActionNode",
        *,
        placeholder_body: bool = False,
    ):
        """
        Create an action call node.

        Args:
            action_name: The name of the action being called.
            args: The argument nodes passed to the action.
            body: The body of the action (used for extraction).
        """
        super().__init__()
        self.action_name = action_name
        self.args = args
        self.body = body
        # True when ``body`` is only a rendering placeholder, as in
        # ``Enabled(named_action, ...)`` before native evaluation resolves the
        # named action body from a spec-level action registry.
        self.placeholder_body = placeholder_body

    def __repr__(self):
        args_str = ", ".join(repr(a) for a in self.args)
        return f"ActionCall({self.action_name!r}, ({args_str}))"

    def __eq__(self, other):
        if not isinstance(other, ActionCallNode):
            return False

        return (
            self.sort == other.sort
            and self.action_name == other.action_name
            and self.args == other.args
            and self.body == other.body
            and self.placeholder_body == other.placeholder_body
        )

    def __hash__(self):
        return hash((self.sort, self.action_name, self.args, self.placeholder_body))


class ActionLetNode(ActionNode):
    """
    A let-binding within an action scope.

    Binds a name to a value expression and makes it available within the body
    action. This is the action-level counterpart of ``LetNode`` (which works
    within pure expressions).

    In TLA+, this translates to ``LET name == value IN body``.
    """

    def __init__(self, name: str, value: Node, body: ActionNode):
        """
        Create an action let-binding node.

        Args:
            name: The variable name to bind.
            value: The expression to evaluate and bind.
            body: The action in which the binding is visible.
        """
        if not isinstance(body.sort, ActionSort):
            raise TypeError(f"Body must have Action sort, got {body.sort}")
        super().__init__()
        self.name = name
        self.value = value
        self.body = body

    def __repr__(self):
        return f"ActionLet({self.name!r}, {self.value!r}, {self.body!r})"

    def __eq__(self, other):
        if not isinstance(other, ActionLetNode):
            return False

        return (
            self.sort == other.sort
            and self.name == other.name
            and self.value == other.value
            and self.body == other.body
        )

    def __hash__(self):
        return hash((self.sort, self.name, self.value, self.body))
