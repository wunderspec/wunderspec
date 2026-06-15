"""
A symbolic implementation of the Context protocol.  It simply goes over the
alternatives and builds action AST nodes. These nodes constitute the symbolic
representation of the specification.

Igor Konnov, 2026
"""

import inspect
from copy import copy
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from wunderspec.ast.action_ast import (
    ActionAndNode,
    ActionCallNode,
    ActionChoiceNode,
    ActionLetNode,
    ActionNode,
    AssignNode,
    AssumeNode,
    NondetChoiceNode,
)
from wunderspec.ast.ast import AlgebraNode, AlgebraOp, Node, SourceSpan, VarNode
from wunderspec.ast.set_ast import SetEnumNode
from wunderspec.ast.sorts import BoolSort, SetSort, Sort
from wunderspec.expr import Expr, VarExpr, coerce_expr
from wunderspec.lang import Val
from wunderspec.machine import (
    Alternative,
    Context,
    ControlFlowError,
    MachineState,
    ValueGenerator,
)
from wunderspec.uniq_names import fresh_name

State = TypeVar("State", bound=MachineState)


@dataclass(frozen=True)
class ExtractedActionDef:
    """Definition of a non-inline action extracted as a TLA+ operator."""

    param_names: tuple[str, ...]
    param_sorts: tuple[Sort, ...]
    body: ActionNode


# Type alias for extracted action definitions: action_name -> definition
ActionDefs = dict[str, ExtractedActionDef]


# enable tracing for debugging when stuck
# @print_trace(enabled=False, private=True)
class SymbolicContext(Context[State]):
    """
    A symbolic context that builds action AST nodes from scheduler alternatives.
    The resulting AST nodes may be used for various purposes:

      - Translating to other languages such as TLA+, Lean, or SMT-LIB.
      - Performing randomized simulation by sampling alternatives.
      - Symbolic model checking via Apalache.

    When an action is called with this context, it builds up the action nodes
    corresponding to the control flow constructs used inside the action.
    Hence, calling `build()` on the context after the action execution
    will produce the complete action AST node representing the action's behavior.
    Note that the context state may keep some intermediate symbolic values as well,
    but they will not have meaningful semantics outside the action AST.
    """

    def __init__(self, pre_init_state: State, *, inline_all: bool = False):
        """
        Initialize the symbolic context with the state to start from.
        Typically, this is the state that has all fields set to symbolic variables,
        e.g., the field `x: int` is simply set to `Var(x, int)`. However, this state
        may also be partially evaluated, e.g., the parameters may be set to concrete values.

        Args:
            pre_init_state: The initial state with symbolic variables.
            inline_all: When True, ignore ``inline=False`` on actions and
                always inline their bodies. Useful for random walks where
                extraction is unnecessary.
        """
        self._inline_all = inline_all
        # the stack of action nodes being built at each nesting level
        self._level_actions: list[list[ActionNode]] = [[]]
        # the current nesting level
        self._level: int = 0
        # The stack of action levels for nested actions.
        # Invariant: initialized with [0] for the top-level action.
        # len(_action_levels) > 1 means we are inside a nested action call.
        # This is relied upon in begin_action's has_parent_action check.
        self._action_levels: list[int] = [0]
        # the current machine state, proxied to track field assignments
        self._state: State = _with_setattr_callback(
            pre_init_state, self._on_field_assignment
        )
        # Each time, alternatives are opened, we record the number of them here.
        # When a single alternative is closed, we decrease the counter.
        # Once the last counter hits zero, we pop it.
        self._alternatives_stack: list[int] = []
        # Parallel stack of alternative names for label generation.
        # Each entry is a list of names collected as alternatives close.
        self._alternative_names_stack: list[list[str]] = []

        # Stack of pending let-bindings per scope level.
        # Mirrors _level_actions: each entry holds (split_index, name, value_node)
        # triples for cache() calls at that scope level. ``split_index`` is the
        # number of actions already collected at the level when cache() was
        # called, so the binding can be positioned at its source location (after
        # the preceding assumes/assigns) instead of being hoisted above them.
        self._pending_lets: list[list[tuple[int, str, Node]]] = [[]]

        # Action extraction tracking
        # Extracted action definitions: action_name -> definition
        self._extracted_actions: ActionDefs = {}
        # Stack of non-inline actions being extracted.
        # Each entry is (action_func, action_level, param_names) where
        # action_level is the value of len(self._action_levels) when the
        # action was started, and param_names are the action's parameter
        # names (excluding the context parameter). This allows handling
        # chains of non-inline action calls without recomputing signatures.
        self._extracting_actions: list[tuple[object, int, tuple[str, ...]]] = []
        # Saved symbolic states for action entry/exit. The boolean says whether
        # end_action should restore that snapshot after the action body finishes.
        self._action_saved_states: list[tuple[State, bool]] = []

    @property
    def state(self) -> State:
        return self._state

    def cache(self, expr: Expr, name: str | None = None) -> Expr:
        """Cache an expression by binding it to a name.

        Records the pending let-binding at the current scope level, tagged with
        the current number of collected actions so it can be positioned at its
        source location. When the scope closes, the binding wraps the actions
        that follow it (so preceding assumes/assigns run first and gate it).
        Returns a VarExpr referencing the cached value.
        """
        if name is None:
            name = fresh_name("_cache")
        else:
            if name in self._state._params:
                raise ValueError(
                    f"Cannot use '{name}' in cache: already used as a state parameter"
                )
            if name in self._state._vars:
                raise ValueError(
                    f"Cannot use '{name}' in cache: already used as a state field"
                )
        split_index = len(self._level_actions[-1])
        self._pending_lets[-1].append((split_index, name, expr.node))
        return VarExpr(name, expr.sort)

    def one_of(self, base_set: Expr, name: str | None = None) -> ValueGenerator[Expr]:
        # introduce a new level for collecting assumptions and delegate to
        # the symbolic value generator
        self._level_actions.append([])
        self._pending_lets.append([])
        set_sort = base_set.sort
        if isinstance(set_sort, SetSort):
            var: Expr
            if name:
                if name in self._state._params:
                    raise ValueError(
                        f"Cannot use '{name}' in one_of: already used as a state parameter"
                    )
                if name in self._state._vars:
                    raise ValueError(
                        f"Cannot use '{name}' in one_of: already used as a state field"
                    )
                var = VarExpr(name, sort=set_sort.elem_sort)
            else:
                var = self._fresh_var(sort=set_sort.elem_sort)
            return _SymbolicValueGenerator(self, base_set, var)
        else:
            raise TypeError(f"Base set must have SetSort, got {set_sort}")

    def assume(self, condition: Expr) -> None:
        """Record an assumption (a Boolean condition) in the current context."""
        if condition.sort != BoolSort():
            raise TypeError(
                f"Assume condition must have Bool sort, got {condition.sort}"
            )
        self._level_actions[-1].append(AssumeNode(condition.node))

    def _on_field_assignment(self, _state_obj: Any, name: str, value: Expr):
        """Callback invoked when a field of the state is assigned a new value."""
        # Record an assignment action node
        if name not in self._state._vars:
            raise AttributeError(f"State has no machine variable {name}")
        field_value = getattr(self._state, name)
        if not isinstance(field_value, Expr):
            raise TypeError(f"Field {name} must be Expr, got {type(field_value)}")
        # Coerce Python literals to the field sort so `s.x = 5` records the same
        # assignment as `s.x = Val(5)`.
        if not isinstance(value, Expr):
            value = coerce_expr(value, field_value.sort)
        if field_value.sort != value.sort:
            raise TypeError(
                f"Field {name} has sort {field_value.sort}, value has sort {value.sort}"
            )
        var = VarNode(name, value.sort)
        new_node = AssignNode(var, value.node)
        # Direct nested assignment updates a field in place and re-reads it, so
        # several statements touching the same field (e.g. `s.chan.val = ...`
        # then `s.chan.rdy = ...`) each carry the *full* updated expression.
        # Coalesce them into a single next-state assignment for this variable
        # (one `x'` in TLA+), keeping the latest value.
        actions = self._level_actions[-1]
        for i, act in enumerate(actions):
            if isinstance(act, AssignNode) and act.var == var:
                # Normally coalesce in place, keeping the earlier position. But if
                # the new value references a c.cache(...) introduced *after* this
                # position, coalescing in place would move that reference above
                # its let-binding (unbound at eval). In that case, drop the old
                # assignment and append the latest at the end (after the cache),
                # shifting pending let positions to account for the removal.
                later_cache_names = {
                    n for (s, n, _v) in self._pending_lets[-1] if s > i
                }
                if later_cache_names and _node_uses_any_name(
                    value.node, later_cache_names
                ):
                    del actions[i]
                    self._pending_lets[-1] = [
                        (s - 1 if s > i else s, n, v)
                        for (s, n, v) in self._pending_lets[-1]
                    ]
                    actions.append(new_node)
                    return
                actions[i] = new_node
                return
        actions.append(new_node)

    def alternatives(self, *names: str) -> tuple[Alternative, ...]:
        level = self._intro_alternatives(len(names))
        if level != self._level:
            raise ControlFlowError(
                f"Expected `with ...` for level {self._level}, " f"got `alternatives`"
            )
        return tuple(_SymbolicAlternative(self, level, name) for name in names)

    def split(self, condition: Expr) -> tuple[Alternative, Alternative]:
        level = self._intro_alternatives(2)
        if level != self._level:
            raise ControlFlowError(
                f"Expected `with ...` for level {self._level}, " f"got `alternatives`"
            )
        then_alt = _SymbolicAlternative(self, level, "then", precond=condition)
        else_alt = _SymbolicAlternative(self, level, "else", precond=~condition)
        return then_alt, else_alt

    def begin_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> tuple[object, ...]:
        """Begin a new action.

        Args:
            action_func: The decorated action function (optional). When provided,
                and the function has _inline=False, the action body will be
                extracted as a separate definition.
            action_args: Arguments passed to the action (beyond the context).

        Returns:
            Potentially modified action_args. For non-inline nested actions,
            returns VarExprs with parameter names instead of the original args.
        """
        # Check if this is a non-inline action nested inside another action
        # We need to track the nesting level to know if there's a parent action
        # Note: _action_levels starts with [0], so len > 1 means we're nested
        has_parent_action = len(self._action_levels) > 1
        is_non_inline = (
            action_func is not None
            and hasattr(action_func, "_inline")
            and not getattr(action_func, "_inline")
            and not self._inline_all
        )

        should_restore = (not has_parent_action) or (
            is_non_inline and has_parent_action
        )
        self._action_saved_states.append((copy(self._state), should_restore))
        self._action_levels.append(self._level)

        # Only extract if non-inline AND nested inside another action
        if is_non_inline and has_parent_action:
            # Start collecting action nodes for this non-inline action
            self._level_actions.append([])
            self._pending_lets.append([])

            # Get parameter names from the action function (computed once,
            # stored on the stack so end_action doesn't need to recompute)
            sig = inspect.signature(getattr(action_func, "__wrapped__"))
            params = list(sig.parameters.keys())
            # Skip the first parameter (context)
            param_names = tuple(params[1:]) if len(params) > 1 else ()

            # Record the action level and param_names for matching in end_action
            action_level = len(self._action_levels)
            self._extracting_actions.append((action_func, action_level, param_names))

            # Create VarExprs for each parameter
            modified_args: list[object] = []
            for i, arg in enumerate(action_args):
                if i < len(param_names) and isinstance(arg, Expr):
                    # Create a VarExpr with the parameter name and the arg's sort
                    param_var = VarExpr(param_names[i], arg.sort)
                    modified_args.append(param_var)
                else:
                    modified_args.append(arg)

            return tuple(modified_args)
        else:
            return action_args

    def end_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> None:
        """End the current action.

        Args:
            action_func: The decorated action function (same as passed to begin_action).
            action_args: The original (unmodified) arguments passed to the action.
        """
        if len(self._action_levels) == 0:
            raise ControlFlowError("Level mismatch: no action to end")
        saved_state, should_restore = self._action_saved_states.pop()
        action_level = self._action_levels.pop()
        if self._level != action_level:
            raise ControlFlowError(
                f"Expected to end action at level {action_level}, got level {self._level}"
            )
        if action_level != len(self._alternatives_stack):
            raise ControlFlowError(f"Unclosed alternatives at level {action_level}")

        # Handle non-inline action extraction
        # Check if the top of the extracting stack matches this action
        # The action_level we compare against is len(_action_levels) + 1 because
        # we already popped from _action_levels above
        current_action_level = len(self._action_levels) + 1
        if not self._extracting_actions:
            if should_restore:
                self._state._copy_from(saved_state)
            return  # No non-inline actions being extracted

        extracting_func, extracting_level, param_names = self._extracting_actions[-1]
        if extracting_level != current_action_level:
            # This is an inline action called from within a non-inline action,
            # or we're at a different nesting level. Don't extract.
            if should_restore:
                self._state._copy_from(saved_state)
            return

        if action_func is not extracting_func:
            # Safety check: the function should match
            if should_restore:
                self._state._copy_from(saved_state)
            return

        # Pop from the extracting stack
        self._extracting_actions.pop()

        # Extract the action body
        action_name = getattr(action_func, "_action_name")

        # Collect the action body from the dedicated level
        action_body_nodes = self._level_actions.pop()
        # Wrap with pending let-bindings from the extracted action scope,
        # positioned at their source locations.
        lets = self._pending_lets.pop()
        action_body = _wrap_actions_with_positioned_lets(action_body_nodes, lets)

        # Store the extracted action definition (only if not already stored).
        # The body already uses parameter names since begin_action returned VarExprs.
        if action_name not in self._extracted_actions:
            param_sorts = tuple(
                arg.sort for arg in action_args if isinstance(arg, Expr)
            )
            self._extracted_actions[action_name] = ExtractedActionDef(
                param_names=param_names,
                param_sorts=param_sorts,
                body=action_body,
            )

        # Create argument nodes from the original Expr arguments passed
        arg_nodes: tuple[Node, ...] = tuple(
            arg.node for arg in action_args if isinstance(arg, Expr)
        )

        # Wrap the body with let-bindings that map each formal parameter name to its
        # actual argument value, but only when they differ.  This is necessary for
        # execution (replay/run): the body was compiled with formal param names as
        # VarNodes, but the caller's env only has the argument variable names bound.
        # Example: `proposer_step(c, prop_id, ...)` compiles the body with VarNode("id"),
        # but the caller env has "prop_id".  We add ActionLetNode("id", VarNode("prop_id"))
        # so that "id" is bound when the body runs.
        body_for_call = action_body
        for param_name, arg_node in reversed(list(zip(param_names, arg_nodes))):
            arg_var_name = arg_node.name if isinstance(arg_node, VarNode) else None
            if param_name != arg_var_name:
                body_for_call = ActionLetNode(param_name, arg_node, body_for_call)

        # Emit ActionCallNode to the parent level
        call_node = ActionCallNode(action_name, arg_nodes, body_for_call)

        # Set source_span from the action function's definition location
        wrapped = getattr(action_func, "__wrapped__", action_func)
        try:
            source_file = inspect.getfile(wrapped)  # type: ignore[arg-type]
            source_lines = inspect.getsourcelines(wrapped)  # type: ignore[arg-type]
            lineno = source_lines[1]
            call_node.source_span = SourceSpan(
                filename=source_file,
                lineno=lineno,
                col_offset=0,
                end_lineno=lineno,
                end_col_offset=0,
            )
        except (TypeError, OSError):
            pass

        self._level_actions[-1].append(call_node)
        if should_restore:
            self._state._copy_from(saved_state)

    def build(self) -> ActionNode:
        """Produce the action AST node built so far."""
        if len(self._level_actions) != 1:
            raise ControlFlowError(
                "Cannot finalize the symbolic context: there are unclosed alternatives"
            )
        if len(self._alternatives_stack):
            raise ControlFlowError(
                f"Alternatives at levels {self._alternatives_stack} are not closed"
            )
        if self._level > 0:
            raise ControlFlowError("Some alternatives are not closed")
        action_nodes = self._level_actions.pop()
        # Wrap with top-level pending let-bindings, positioned at their source
        # locations so preceding assumes/assigns run (and gate) before them.
        lets = self._pending_lets.pop()
        return _wrap_actions_with_positioned_lets(action_nodes, lets)

    @property
    def extracted_actions(self) -> ActionDefs:
        """Get the extracted action definitions.

        Returns:
            A dictionary mapping action names to extracted action definitions.
            This is populated when non-inline actions (with inline=False) are called.
        """
        return self._extracted_actions

    def _fresh_var(self, sort: Sort) -> Expr:
        """Create a fresh temporary variable of the given sort."""
        var_name = fresh_name("_tmp")
        # Create a new variable of the given sort
        return VarExpr(var_name, sort)

    def _combine_assumptions(
        self, combinator: Callable[[list[ActionNode]], ActionNode]
    ) -> None:
        """Pop the current assumptions, combine them using the given combinator, and append the result."""
        action_nodes = self._level_actions.pop()
        # Wrap the collected actions with pending let-bindings, positioned at
        # their source locations, before handing them to the combinator, so the
        # lets are inside the combinator's node (e.g., inside NondetChoiceNode or
        # ActionChoiceNode) and after any preceding assumes that gate them.
        lets = self._pending_lets.pop()
        if lets:
            inner = _wrap_actions_with_positioned_lets(action_nodes, lets)
            one_action = combinator([inner])
        else:
            one_action = combinator(action_nodes)
        self._level_actions[-1].append(one_action)

    def _intro_alternatives(self, count: int) -> int:
        """Record that the context introduced `count` alternatives at the current level."""
        level = len(self._alternatives_stack)
        self._alternatives_stack.append(count)
        self._alternative_names_stack.append([])
        self._level_actions.append(
            []
        )  # this is where we collect actions of this alternative
        self._pending_lets.append([])
        return level

    def _close_one_alternative(self, name: str | None = None) -> bool:
        """Record that one alternative at the current level has exited."""
        self._alternatives_stack[-1] -= 1
        if name is not None:
            self._alternative_names_stack[-1].append(name)
        # combine the collected actions at this level
        self._combine_assumptions(lambda actions: _action_and(actions))
        if self._alternatives_stack[-1] == 0:
            # All alternatives at this level have exited.
            self._alternatives_stack.pop()
            names = self._alternative_names_stack.pop()
            labels = tuple(names) if names else None
            # combine the alternatives using ActionChoiceNode
            self._combine_assumptions(
                lambda actions: _action_or(actions, labels=labels)
            )
            return True
        return False

    def _alternative_entered(self, alt_level: int):
        """Notify the context that an alternative has been entered."""
        if self._level != alt_level:
            raise ControlFlowError(
                f"Expected `with ...` for level {self._level}, "
                f"got level {alt_level}"
            )
        # introduce a new level for collecting actions of this alternative
        self._level_actions.append([])
        self._pending_lets.append([])
        self._level += 1

    def _alternative_exited(
        self, alt_level: int, saved_state: State | None, name: str | None = None
    ):
        """Notify the context that an alternative has exited."""
        if saved_state is None:
            raise ValueError("saved_state must not be None")
        self._level -= 1
        if self._level != alt_level:
            raise ControlFlowError(
                f"Expected the alternative level {self._level}, "
                f"got level {alt_level}"
            )

        # Restore the saved state, as other alternatives have to work on it.
        # We always copy back to the state, as the user may have saved a reference to it.
        self._state._copy_from(saved_state)
        self._close_one_alternative(name=name)


class _SymbolicValueGenerator(ValueGenerator[Expr]):
    """
    A symbolic value generator that simply produces a symbolic variable
    and collects the action nodes underneath.
    """

    def __init__(self, context: SymbolicContext, base_set: Expr, var: Expr):
        self._context = context
        self._base_set = base_set
        self._var = var

    def __enter__(self) -> Expr:
        return self._var

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_value is not None:
            return  # An exception occurred, do not record an action node
        if self._var is None:
            raise RuntimeError(
                "SymbolicValueGenerator.__exit__ called before __enter__"
            )

        def combinator(action_nodes: list[ActionNode]) -> ActionNode:
            combined_action: ActionNode = _action_and(action_nodes)
            nondet_choice = NondetChoiceNode(self._var.node, self._base_set.node, combined_action)  # type: ignore
            # \E x \in S: P semantics: add explicit assumption that S is non-empty.
            # self._base_set.sort is SetSort (validated in one_of before creating this generator).
            base_set_sort = self._base_set.sort
            assert isinstance(base_set_sort, SetSort)
            elem_sort = base_set_sort.elem_sort
            empty_set = SetEnumNode(elem_sort)  # {} — empty set of the right sort
            nonempty_cond = AlgebraNode(
                BoolSort(), AlgebraOp.NE, self._base_set.node, empty_set
            )
            return ActionAndNode(AssumeNode(nonempty_cond), nondet_choice)

        self._context._combine_assumptions(combinator)


class _SymbolicAlternative(Generic[State]):
    """
    A branching alternative that collects actions under the alternative's
    context. This class is tightly coupled with `SymbolicContext`. Hence, you
    have to read both to understand how they work together.
    """

    def __init__(
        self,
        context: "SymbolicContext[State]",
        level: int,
        name: str,
        precond: Expr | None = None,
    ):
        self._level = level
        self._name = name
        self._precond = precond
        self._context = context
        self._saved_state: State | None = None
        self._num_entered = 0

    def __enter__(self):
        if self._num_entered > 0:
            raise ControlFlowError(f"Alternative {self._name} is re-entered")
        self._saved_state = copy(self._context.state)
        # tell the context that we have entered this alternative
        self._context._alternative_entered(self._level)
        self._num_entered += 1
        if self._precond is not None:
            self._context.assume(self._precond)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # tell the context that we are exiting this alternative
        self._context._alternative_exited(
            self._level, self._saved_state, name=self._name
        )
        # else the user should inspect the context manually and call revert()

    def __repr__(self) -> str:
        return f"SymbolicAlternative(name={self._name})"


def _node_uses_any_name(node: Node, names: set[str]) -> bool:
    """Whether *node* references a ``VarNode`` whose name is in *names*.

    Generic structural walk over child nodes (mirrors ``_has_free_vars`` in
    ``api.py``). Conservative w.r.t. shadowing — a false positive only causes a
    redundant reorder, never an incorrect result.
    """
    if isinstance(node, VarNode) and node.name in names:
        return True
    for child in node.__dict__.values():
        if isinstance(child, Node):
            if _node_uses_any_name(child, names):
                return True
        elif isinstance(child, dict):
            for k, v in child.items():
                if isinstance(k, Node) and _node_uses_any_name(k, names):
                    return True
                if isinstance(v, Node) and _node_uses_any_name(v, names):
                    return True
        elif isinstance(child, (tuple, list, set, frozenset)):
            for item in child:
                if isinstance(item, Node) and _node_uses_any_name(item, names):
                    return True
    return False


def _wrap_actions_with_positioned_lets(
    action_nodes: list[ActionNode],
    lets: list[tuple[int, str, Node]],
) -> ActionNode:
    """Combine *action_nodes* into an ``And``, nesting each cached let-binding at
    its source position.

    ``lets`` are ``(split_index, name, value)`` triples in call order, where
    ``split_index`` is the number of actions that preceded the ``cache()`` call.
    Each let wraps only the actions that follow it, so actions before a let stay
    outside it (and run/gate first), and for lets sharing a position the
    first-called one is the outermost. A cache variable is only referenced after
    its ``cache()`` call, so the binding is always in scope at every use.
    """
    if not lets:
        return _action_and(action_nodes)
    split_index, name, value = lets[0]
    head = action_nodes[:split_index]
    tail = action_nodes[split_index:]
    rest = [(s - split_index, n, v) for (s, n, v) in lets[1:]]
    body = ActionLetNode(name, value, _wrap_actions_with_positioned_lets(tail, rest))
    return _action_and(head + [body])


def _action_and(action_nodes: list[ActionNode]) -> ActionNode:
    """Combine action nodes using ActionAndNode."""
    if len(action_nodes) == 0:
        return AssumeNode(Val(True).node)  # No-op action
    elif len(action_nodes) == 1:
        return action_nodes[0]
    else:
        return ActionAndNode(*action_nodes)


def _action_or(
    action_nodes: list[ActionNode],
    labels: tuple[str, ...] | None = None,
) -> ActionNode:
    """Combine action nodes using ActionOrNode."""
    if len(action_nodes) == 0:
        return AssumeNode(Val(True).node)  # No-op action
    elif len(action_nodes) == 1:
        return action_nodes[0]
    else:
        return ActionChoiceNode(*action_nodes, labels=labels)


def _with_setattr_callback(obj, callback):  # type: ignore[no-untyped-def]
    """
    Patch the given object's __setattr__ to call the callback.
    This interceptor is used to track field assignments in machine states.
    """

    # inherit from the original class, so we preserve its behavior
    obj_cls: type = obj.__class__

    class _SetAttrProxy(obj_cls):  # type: ignore[valid-type, misc]
        def __setattr__(self, name, value):  # type: ignore[no-untyped-def]
            if not name.startswith("_"):
                callback(self, name, value)
            return super().__setattr__(name, value)

    obj.__class__ = _SetAttrProxy
    obj.__dict__["__edit_callback"] = callback
    return obj
