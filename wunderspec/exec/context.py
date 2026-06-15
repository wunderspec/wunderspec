"""
Execution context for running state machine actions. This is an explicit
execution context. No translation to symbolic expressions is done, and the state
is mutated directly.

Igor Konnov, 2026
"""

from collections.abc import Callable, Iterable
from copy import copy
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from typing_extensions import Self

from wunderspec.ast.sorts import SetSort
from wunderspec.expr import Expr
from wunderspec.interpreter import BoolValue, value
from wunderspec.interpreter_sampling import EmptySetError
from wunderspec.interpreter_value import IValueNode
from wunderspec.lang import ExprLike
from wunderspec.machine import (
    Alternative,
    Context,
    ControlFlowError,
    FixedValueGenerator,
    MachineState,
    ValueGenerator,
)

from .scheduler import (
    RandomScheduler,
    Scheduler,
    SchedulerAlternative,
    SchedulerDecision,
    SchedulerRequestAlternative,
    SchedulerRequestOneOf,
    SchedulerRequestSplit,
    SchedulerSplit,
    SchedulerValue,
    ScriptedScheduler,
)

State = TypeVar("State", bound=MachineState)


class _OutsideChosenPath(Exception):
    """
    Internal exception raised when trying to get a scheduling decision while
    outside of the chosen path.
    """

    pass


class AssumptionViolated(Exception):
    """Exception raised when an assumption is violated during execution."""

    def __init__(self, msg: str = "assumption violated"):
        super().__init__(msg)

    def __repr__(self) -> str:
        return f"AssumptionViolated({super().__repr__()})"


# Type alias for assumption checker callbacks
AsssumptionChecker = Callable[[Expr], None]


def assumption_interpreter(condition: Expr) -> None:
    """
    Default assumption checker that evaluates the condition and raises
    AssumptionViolated if it evaluates to False.
    """
    if value(condition) == BoolValue(False):
        raise AssumptionViolated()


@dataclass
class BranchingTracker:
    """Tracks alternative branching and the chosen path."""

    _alternatives_stack: list[int] = field(default_factory=list)
    _chosen_stack: list[bool] = field(default_factory=list)

    @property
    def on_chosen_path(self) -> bool:
        """Whether we are currently on the chosen execution path."""
        return len(self._chosen_stack) == 0 or self._chosen_stack[-1]

    @property
    def current_level(self) -> int:
        """Current nesting level of alternatives."""
        return len(self._alternatives_stack)

    def open_alternatives(self, count: int) -> int:
        """Open a new set of alternatives, returning the level."""
        level = len(self._alternatives_stack)
        self._alternatives_stack.append(count)
        return level

    def close_alternative(self) -> bool:
        """Close one alternative at the current level.

        Returns True if all alternatives at this level have been closed.
        """
        self._alternatives_stack[-1] -= 1
        if self._alternatives_stack[-1] == 0:
            self._alternatives_stack.pop()
            return True
        return False

    def enter_alternative(self, level: int, is_chosen: bool) -> None:
        """Enter an alternative at the given level."""
        if len(self._alternatives_stack) - 1 != level:
            raise ControlFlowError(
                f"Expected `with ...` for level {len(self._alternatives_stack)}, "
                f"got level {level}"
            )
        self._chosen_stack.append(is_chosen)

    def exit_alternative(self) -> None:
        """Exit the current alternative."""
        self._chosen_stack.pop()

    def reset(self) -> None:
        """Reset the tracker to initial state."""
        self._alternatives_stack.clear()
        self._chosen_stack.clear()


@dataclass
class StateCheckpoint(Generic[State]):
    """Manages state checkpointing for save/restore during alternative execution."""

    current: State
    last_committed: State
    chosen_state: State | None = None

    @classmethod
    def create(cls, proto_state: State) -> Self:
        """Create a checkpoint from a prototype state."""
        return cls(
            current=copy(proto_state),
            last_committed=copy(proto_state),
            chosen_state=None,
        )

    def save_chosen(self) -> None:
        """Save the current state as the chosen state."""
        if self.chosen_state is None:
            self.chosen_state = copy(self.current)

    def restore_from(self, saved: State) -> None:
        """Restore the current state from a saved state."""
        self.current._copy_from(saved)

    def propagate_chosen(self) -> None:
        """Propagate the chosen state to current and clear it."""
        if self.chosen_state is not None:
            self.current._copy_from(self.chosen_state)
            self.chosen_state = None

    def commit(self) -> None:
        """Commit the current state, finalizing it."""
        self.current.finalize()
        self.last_committed = copy(self.current)

    def revert(self) -> None:
        """Revert to the last committed state."""
        self.current = copy(self.last_committed)
        self.chosen_state = None


@dataclass
class ExecAlternative(Generic[State]):
    """
    A branching alternative that computes towards the final state, only
    if it's the chosen one. This class works together with ExecContext.
    """

    tracker: BranchingTracker
    checkpoint: "StateCheckpoint[State]"
    level: int
    name: str
    is_chosen: bool
    _saved_state: State | None = field(default=None, init=False)
    _num_entered: int = field(default=0, init=False)

    def __enter__(self) -> Self:
        if self._num_entered > 0:
            raise ControlFlowError(f"Alternative {self.name} is re-entered")
        self._saved_state = copy(self.checkpoint.current)
        self.tracker.enter_alternative(self.level, self.is_chosen)
        self._num_entered += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        # If this is the chosen path, save the state
        if self.is_chosen and self.checkpoint.chosen_state is None:
            self.checkpoint.save_chosen()

        # Restore the saved state for other alternatives
        if self._saved_state is not None:
            self.checkpoint.restore_from(self._saved_state)

        self.tracker.exit_alternative()

        if self.tracker.close_alternative():
            # All alternatives at this level have exited.
            # Propagate the chosen state upwards.
            self.checkpoint.propagate_chosen()

        # Suppress exceptions only if we are not on the chosen path
        return exc_value is not None and isinstance(exc_value, _OutsideChosenPath)


class ExecContext(Context[State]):
    """
    An execution context for running state machine actions.

    This context requests values from a scheduler and tracks assumptions.
    It supports scripted schedules for testing and random schedules for exploration.

    Usage:
        ```python
        def my_scenario():
            yield SchedulerValue(Val("alice"))       # for one_of
            yield SchedulerAlternative("increment")  # for alternatives
            yield SchedulerSplit(True)               # for split (take the 'then' branch)

        s = MyState(...)
        c = ExecContext(s, my_scenario())
        my_action(c)
        c.finalize()

        # alternatively, to also check the assumptions
        c = ExecContext(s, my_scenario())
        c.step(my_action)

        # for random exploration, pass a RandomScheduler
        c = ExecContext(s, RandomScheduler())
        c.step(my_action)
        ```
    """

    def __init__(
        self,
        proto_state: State,
        scheduler: Scheduler | Iterable[SchedulerDecision] | None = None,
        assumption_checker: AsssumptionChecker = assumption_interpreter,
    ):
        """
        Initialize the execution context with a prototype state and a scheduler.

        Args:
            proto_state: The initial state (should contain values for all parameters).
            scheduler: A Scheduler instance, an iterable of SchedulerDecision objects,
                       or None for a default random scheduler.
            assumption_checker: Callback to check assumptions. Called for each assumption
                               on the chosen path. Defaults to assumption_interpreter,
                               which evaluates the condition and raises AssumptionViolated
                               if it is False.
        """
        self._checkpoint = StateCheckpoint.create(proto_state)
        self._tracker = BranchingTracker()
        self._assumption_checker = assumption_checker

        # Initialize the scheduler with reasonable defaults
        match scheduler:
            case ScriptedScheduler() | RandomScheduler():
                self._scheduler = scheduler
            case Iterable():
                self._scheduler = ScriptedScheduler(scheduler)
            case _:
                self._scheduler = RandomScheduler()

    @property
    def state(self) -> State:
        """The current state being built by the transition."""
        return self._checkpoint.current

    @state.setter
    def state(self, new_state: State) -> None:
        self._checkpoint.current = new_state

    def step(self, action: Callable, /, *args: ExprLike, **kwargs: ExprLike) -> None:
        """
        Execute an action and finalize.

        This method automates the standard steps:
         1. Execute `action` (assumptions are checked via assumption_checker).
         2. Call `finalize()` to commit the state.
        """
        try:
            action(self, *args, **kwargs)
            self.finalize()
        except AssumptionViolated as e:
            self.revert()
            raise e

    def try_step(self, action: Callable, /, *args: Expr, **kwargs: Expr) -> bool:
        """
        Try to execute an action and finalize.

        Similar to `step`, but returns `False` instead of raising `AssumptionViolated`.
        If no assumption is violated, return `True`.
        """
        try:
            self.step(action, *args, **kwargs)
            return True
        except AssumptionViolated:
            return False

    def one_of(self, base_set: Expr, _name: str | None = None) -> ValueGenerator[Expr]:
        """Pick a value from a set."""
        if not self._tracker.on_chosen_path:
            raise _OutsideChosenPath()

        try:
            decision = self._scheduler.decide(SchedulerRequestOneOf(base_set))
        except EmptySetError:
            raise AssumptionViolated("one_of: set is empty") from None
        if not isinstance(decision, SchedulerValue):
            raise ControlFlowError(
                f"Expected SchedulerValue for one_of, got {decision}"
            )
        next_value = decision.value
        set_sort = base_set.sort
        if not isinstance(set_sort, SetSort):
            raise TypeError(f"Expected `base_set` to have a set sort, got {set_sort}")
        elem_sort = set_sort.elem_sort
        if next_value.sort != elem_sort:
            raise TypeError(
                f"Expected `next_value` to have sort {elem_sort}, got {next_value.sort}"
            )
        return FixedValueGenerator(next_value)

    def alternatives(self, *names: str) -> tuple[Alternative, ...]:
        """Create a set of mutually exclusive alternatives."""
        if not self._tracker.on_chosen_path:
            # immediately skip this alternative
            raise _OutsideChosenPath()

        decision = self._scheduler.decide(SchedulerRequestAlternative(names))
        if not isinstance(decision, SchedulerAlternative):
            raise ControlFlowError(
                f"Expected SchedulerAlternative(...), got {decision}"
            )
        next_alternative = decision.chosen
        if next_alternative not in names:
            raise ControlFlowError(
                f"Expected one of the alternatives in {names}, got '{next_alternative}'"
            )
        level = self._tracker.open_alternatives(len(names))
        return tuple(
            ExecAlternative(
                tracker=self._tracker,
                checkpoint=self._checkpoint,
                level=level,
                name=name,
                is_chosen=name == next_alternative,
            )
            for name in names
        )

    def split(self, condition: Expr) -> tuple[Alternative, Alternative]:
        """Do a case split on a boolean condition."""
        if not self._tracker.on_chosen_path:
            # immediately skip this alternative
            raise _OutsideChosenPath()

        decision = self._scheduler.decide(SchedulerRequestSplit(condition))
        if not isinstance(decision, SchedulerSplit):
            raise ControlFlowError(f"Expected SchedulerSplit(...), got {decision}")
        next_split_arm = decision.split_arm
        level = self._tracker.open_alternatives(2)
        true_alt = ExecAlternative(
            tracker=self._tracker,
            checkpoint=self._checkpoint,
            level=level,
            name="then",
            is_chosen=next_split_arm,
        )
        false_alt = ExecAlternative(
            tracker=self._tracker,
            checkpoint=self._checkpoint,
            level=level,
            name="else",
            is_chosen=not next_split_arm,
        )
        # since we already know which arm to take, we can push the assumption right here
        if next_split_arm:
            self.assume(condition)
        else:
            self.assume(~condition)

        return (true_alt, false_alt)

    def assume(self, condition: Expr) -> None:
        """Check an assumption about the current state via the assumption_checker."""
        if self._tracker.on_chosen_path:
            self._assumption_checker(condition)

    def cache(self, expr: Expr, name: str | None = None) -> Expr:
        """Cache an expression by evaluating it immediately.

        Returns a concrete-value expression wrapping the evaluated result.
        The *name* argument is accepted for API compatibility with
        ``SymbolicContext`` but is not used during execution.
        """
        if not self._tracker.on_chosen_path:
            raise _OutsideChosenPath()
        val = value(expr)
        return Expr(IValueNode(val))

    def begin_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> tuple[object, ...]:
        """Begin a new action (called by @action decorator).

        Args:
            action_func: The decorated action function (optional, used for extraction).
            action_args: Arguments passed to the action (optional).

        Returns:
            The action_args unchanged (ExecContext doesn't modify them).
        """
        return action_args

    def end_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> None:
        """End the current action (called by @action decorator).

        Args:
            action_func: The decorated action function (optional).
            action_args: The original arguments passed to the action (optional).
        """
        pass

    def revert(self) -> None:
        """Revert to the last committed state."""
        self._checkpoint.revert()
        self._tracker.reset()

    def finalize(self) -> None:
        """Finalize the changes made on the chosen path to the state."""
        if self._tracker.current_level > 0:
            raise ControlFlowError(
                f"Alternatives at {self._tracker.current_level} levels are not closed"
            )
        if (
            self._checkpoint.chosen_state is not None
            or len(self._tracker._chosen_stack) > 0
        ):
            raise ControlFlowError("Inconsistent state, cannot finalize")
        # self._checkpoint.current contains the final state on the chosen path
        self._checkpoint.commit()

    # Attributes that are allowed to be set directly on ExecContext
    _allowed_attrs = frozenset(
        {
            "_checkpoint",
            "_tracker",
            "_scheduler",
            "_assumption_checker",
        }
    )

    def __setattr__(self, name: str, value: object) -> None:
        if name not in self._allowed_attrs:
            raise AttributeError(
                f"Cannot set attribute '{name}' on ExecContext. "
                f"Did you mean to use 'c.state.{name} = ...'?"
            )
        object.__setattr__(self, name, value)
