"""
An imperative-style builder for Wunderspec expressions. It is made
intentionally imperative, as it is what most users find natural.

Igor Konnov, 2026
"""

from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum, auto
from functools import wraps
from typing import Protocol

from wunderspec.ast import BoolSort
from wunderspec.ast.ast import IteNode, Node
from wunderspec.expr import Expr, coerce_expr, expr_from_node


class FlowError(Exception):
    """Exception raised when the flow builder is used incorrectly."""

    def __init__(self, msg: str):
        super().__init__(msg)


class _FlowReturn(Exception):
    """Internal exception used to signal a return from the flow builder."""

    pass


class FlowBuilderProtocol(Protocol):
    """Protocol defining the FlowBuilder interface for type checking."""

    def if_(self, condition: "Expr | bool") -> "FlowBuilderProtocol":
        """Enter a new conditional branch."""
        ...

    def else_(self) -> "FlowBuilderProtocol":
        """Enter the else branch of the current conditional."""
        ...

    def return_(self, expr: "Expr | int | str | bool | Enum") -> None:
        """Set the return expression for the current branch."""
        ...

    def end(self) -> Expr:
        """Build the final expression."""
        ...

    def __enter__(self) -> "FlowBuilderProtocol": ...

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool: ...


_current_builder: ContextVar["FlowBuilder"] = ContextVar("_flow_builder")


class _FlowBuilderProxy:
    def __getattr__(self, name):
        # Allow debugger and introspection tools to inspect the proxy without requiring context
        # This prevents debugger crashes when inspecting the 'flow' object
        if name.startswith("_"):
            # Use object's __getattribute__ to raise AttributeError naturally
            # This prevents debugger from breaking on our explicitly raised exceptions
            return object.__getattribute__(self, name)

        try:
            flow = _current_builder.get()
        except LookupError:
            raise FlowError(
                "Flow builder is not active. "
                "Make sure you're using 'flow' inside a @with_flow decorated function."
            )
        return getattr(flow, name)


# Type the global 'flow' as the protocol so IDEs/type checkers see the methods
flow: FlowBuilderProtocol
"""Global access point for the flow builder (thread-local)"""
flow = _FlowBuilderProxy()  # type: ignore[assignment]


def with_flow(fn):
    """Decorator to build a definition via imperative constructs."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        flow_builder = FlowBuilder()
        token = _current_builder.set(flow_builder)
        result = None
        try:
            result = fn(*args, **kwargs)
        except _FlowReturn:
            # this may be a top-level return_
            try:
                return flow_builder.end()
            except FlowError as e:
                raise FlowError("top-level return_ produced an error") from e
        finally:
            _current_builder.reset(token)

        if result is None:
            raise FlowError("Flow must end with: return flow.end()")
        else:
            return result

    return wrapper


class _NodeState(Enum):
    """The state of a node in the flow builder."""

    NEW = auto()
    IF_OPEN = auto()
    IF_CLOSED = auto()
    ELSE_OPEN = auto()
    ELSE_CLOSED = auto()


@dataclass
class _IteNode:
    """The If-Then-Else node, potentially with gaps."""

    condition: Expr | None
    then_expr: "_IteNode | Expr | None"
    else_expr: "_IteNode | Expr | None"
    state: _NodeState = _NodeState.NEW


class FlowBuilder:
    """Builder object for constructing AST nodes in an imperative style."""

    def __init__(self):
        # We collect the if-then-else branches here.
        self._root = _IteNode(None, None, None)
        # The stack of nodes we are currently in.
        self._node_stack = [self._root]

    def if_(self, condition: "Expr | bool") -> "FlowBuilder":
        """Enter a new conditional branch.

        The condition may be a boolean ``Expr`` or a raw Python ``bool``, which
        is auto-coerced.
        """

        condition = coerce_expr(condition, BoolSort())

        if len(self._node_stack) == 0:
            raise FlowError("if_ is called but all is done")

        last = self._node_stack[-1]
        if last.state == _NodeState.NEW:
            last.condition = condition
            last.state = _NodeState.IF_OPEN
        elif last.state == _NodeState.IF_OPEN:
            new_node = _IteNode(condition, None, None, _NodeState.IF_OPEN)
            last.then_expr = new_node
            self._node_stack.append(new_node)
        elif last.state == _NodeState.IF_CLOSED:
            # the else condition without explicit else_:
            # with flow.if_(P):
            #     ...
            #     flow.return_(E1)
            # with flow.if_(Q):
            #     ...
            new_node = _IteNode(condition, None, None, _NodeState.IF_OPEN)
            # this node is now complete: move it to ELSE_CLOSED, pop it from the stack
            last.else_expr = new_node
            last.state = _NodeState.ELSE_CLOSED
            self._node_stack.pop()
            self._node_stack.append(new_node)
        elif last.state == _NodeState.ELSE_OPEN:
            new_node = _IteNode(condition, None, None, _NodeState.IF_OPEN)
            last.else_expr = new_node
            self._node_stack.append(new_node)
        else:
            raise FlowError("if_ called, but current branch is already complete")

        return self

    def else_(self) -> "FlowBuilder":
        """Enter the else branch of the current conditional."""

        if len(self._node_stack) == 0:
            raise FlowError("else_ is called but the flow is done")

        last = self._node_stack[-1]
        if last.state == _NodeState.ELSE_CLOSED:
            raise FlowError("else_ is called twice")
        elif last.state == _NodeState.IF_CLOSED:
            last.state = _NodeState.ELSE_OPEN
            return self
        else:
            raise FlowError("else_ is called in unexpected flow state")

    def return_(self, expr: "Expr | int | str | bool | Enum") -> None:
        """Set the return expression for the current branch.

        The value may be an ``Expr`` or a raw Python literal (``int``, ``str``,
        ``bool``, ``Enum``), which is auto-coerced.
        """

        expr = coerce_expr(expr)

        if len(self._node_stack) == 0:
            raise FlowError("return_ is called but the flow is done")

        last = self._node_stack[-1]
        if last.state == _NodeState.IF_OPEN:
            last.then_expr = expr
            # raise to close the context
            raise _FlowReturn()
        elif last.state == _NodeState.IF_CLOSED:
            # the else condition without explicit else_:
            # with flow.if_(P):
            #     ...
            #     flow.return_(E1)
            # flow.return_(E2)
            last.else_expr = expr
            if len(self._node_stack) > 1:
                # raise to close the context
                raise _FlowReturn()
            else:
                # close the root node and pop it from the stack
                last.state = _NodeState.ELSE_CLOSED
                self._node_stack.pop()
                # Raise to avoid further code execution.
                # As a result, end() will be called by the decorator.
                raise _FlowReturn()
        elif last.state == _NodeState.ELSE_OPEN:
            last.else_expr = expr
            # raise to close the context
            raise _FlowReturn()
        else:
            raise FlowError("return_ is called in unexpected flow state")

    def end(self) -> Expr:
        """Build the final expression."""
        if len(self._node_stack) != 0:
            raise FlowError("end() is called in the middle of a flow")

        if self._root.state != _NodeState.ELSE_CLOSED:
            raise FlowError("The flow is incomplete")

        def build_node(node_or_expr: _IteNode | Expr) -> Node:
            if isinstance(node_or_expr, _IteNode):
                if node_or_expr.state != _NodeState.ELSE_CLOSED:
                    raise FlowError("Incomplete branch in the flow")
                assert node_or_expr.then_expr is not None
                assert node_or_expr.else_expr is not None
                return IteNode(
                    node_or_expr.condition.node,  # type: ignore
                    build_node(node_or_expr.then_expr),
                    build_node(node_or_expr.else_expr),
                )
            else:
                return node_or_expr.node

        return expr_from_node(build_node(self._root))

    def __enter__(self) -> "FlowBuilder":
        """Enter the context manager."""
        return self

    def __exit__(self, exc_type, _exc_value, _traceback) -> bool:
        """Exit the context manager."""

        last = self._node_stack[-1]
        if last.state == _NodeState.IF_OPEN:
            if last.then_expr is None:
                raise FlowError("Missing return_ in if_ block")
            last.state = _NodeState.IF_CLOSED
        elif last.state in [_NodeState.ELSE_OPEN, _NodeState.IF_CLOSED]:
            if last.else_expr is None:
                raise FlowError("Missing return_ in else_ block")
            last.state = _NodeState.ELSE_CLOSED
            self._node_stack.pop()

        if exc_type is not _FlowReturn:
            return False
        else:
            # 1. Suppress FlowError to allow building the expression.
            # 2. No `return_` is needed, if the branch contains a nested if-then-else.
            return True
