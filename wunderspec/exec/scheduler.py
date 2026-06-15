"""
Scheduler types and implementations for execution contexts.

Igor Konnov, 2026
"""

import itertools
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from wunderspec.ast.sorts import SetSort
from wunderspec.expr import Expr
from wunderspec.interpreter import value
from wunderspec.interpreter_sampling import (
    SamplingHint,
    SamplingStrategy,
    UniformSamplingStrategy,
)
from wunderspec.interpreter_value import (
    AbstractSetValue,
    IValueNode,
    from_python_with_sort,
)
from wunderspec.lang import ExprLike, Val
from wunderspec.permutation import mix64, permute

# Request types - what the context sends to the scheduler


@dataclass(frozen=True, slots=True)
class SchedulerRequestOneOf:
    """The scheduler receives this request on `one_of`."""

    base_set: Expr


@dataclass(frozen=True, slots=True)
class SchedulerRequestAlternative:
    """The scheduler receives this request on `alternatives`."""

    alternatives: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SchedulerRequestSplit:
    """The scheduler receives this request on `split`."""

    condition: Expr


SchedulerRequest = (
    SchedulerRequestOneOf | SchedulerRequestAlternative | SchedulerRequestSplit
)


# Decision types - what the scheduler returns to the context


@dataclass(frozen=True, slots=True)
class SchedulerValue:
    """Request for a value from a set (used by `one_of`).

    Accepts ExprLike values (Expr, int, str, bool, Enum) and auto-coerces to Expr.
    """

    _value: ExprLike

    def __post_init__(self) -> None:
        """Auto-coerce literals to Expr."""
        if not isinstance(self._value, Expr):
            object.__setattr__(self, "_value", Val(self._value))

    @property
    def value(self) -> Expr:
        """Return the value as an Expr (guaranteed after construction)."""
        return self._value  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class SchedulerAlternative:
    """Request for choosing an alternative (used by `alternatives`)."""

    chosen: str


@dataclass(frozen=True, slots=True)
class SchedulerSplit:
    """Request for a split decision (used by `split`)."""

    split_arm: bool


@dataclass(frozen=True, slots=True)
class SchedulerChoiceIndex:
    """Index-based choice used by schedule replay (e.g., Rust `choice` entries)."""

    index: int


@dataclass(frozen=True, slots=True)
class SchedulerRawValue:
    """Raw decoded schedule sample, converted once the one_of sort is known."""

    value: Any


SchedulerDecision = (
    SchedulerValue
    | SchedulerAlternative
    | SchedulerSplit
    | SchedulerChoiceIndex
    | SchedulerRawValue
)


# Protocol for schedulers


class Scheduler(Protocol):
    """Protocol for schedulers that make decisions during execution."""

    def decide(self, request: SchedulerRequest) -> SchedulerDecision:
        """Make a decision based on the request."""
        ...


@dataclass
class RecordingScheduler:
    """Wrap a scheduler and record every decision it returns."""

    scheduler: Scheduler
    decisions: list[SchedulerDecision] = field(default_factory=list)

    def decide(self, request: SchedulerRequest) -> SchedulerDecision:
        decision = self.scheduler.decide(request)
        self.decisions.append(decision)
        return decision


class ScheduleEnumerator:
    """
    A systematic enumerator of choices in a schedule tree. This is different
    from random choice. This is what model checkers usually do.

    The tricky part is that different paths may have different lengths.
    """

    _path: list[int]
    # the current path in the schedule tree, where each int is the index
    # of the choice at that level
    _has_next: list[bool]
    # whether there are more choices to explore at each level of the path
    _current_level: int
    # the current depth in the schedule tree (1-based)

    _shuffle_seed: int | None

    def __init__(self, shuffle_seed: int | None = None):
        # We introduce 0 as a dummy choice index for the root.
        # As soon as we backtrack to level 0, enumeration is complete.
        self._path = [0]
        # The backtracking guard at level 0.
        self._has_next = [False]
        self._current_level = 1
        self._shuffle_seed = shuffle_seed

    def draw(self, lower: int, upper: int) -> int:
        """
        Draw a value from the range, according to the current path.
        If there is no node at the current level, we extend the path with the lower bound.
        """

        assert self._current_level > 0, "Enumeration is over"
        if self._current_level < len(self._path):
            saved_choice = self._path[self._current_level]
            assert (
                lower <= saved_choice <= upper
            ), f"Saved choice {saved_choice} out of bounds [{lower}, {upper})"
            next_choice = saved_choice + 1
            self._has_next[self._current_level] = next_choice <= upper
            self._current_level += 1
            return self._apply_shuffle(saved_choice, lower, upper)
        elif self._current_level == len(self._path):
            # We are at a leaf, so we need to extend the path.
            saved_choice = lower
            self._path.append(saved_choice)
            next_choice = saved_choice + 1
            self._has_next.append(next_choice <= upper)
            self._current_level += 1
            return self._apply_shuffle(saved_choice, lower, upper)
        else:
            raise ValueError(
                f"Current level {self._current_level} cannot exceed path length {len(self._path)}"
            )

    def _apply_shuffle(self, raw_choice: int, lower: int, upper: int) -> int:
        """Apply keyed permutation to the raw choice index if shuffling is enabled."""
        if self._shuffle_seed is None:
            return raw_choice
        seed = self._shuffle_seed
        # Compute tweak from path prefix (identifies tree node).
        tweak = mix64(seed)
        for j in range(1, self._current_level - 1):
            tweak = mix64(tweak ^ self._path[j])
        d = upper - lower + 1
        if d <= 1:
            return raw_choice
        return lower + permute(seed, tweak, d, raw_choice - lower)

    def next_schedule(self) -> bool:
        """
        Move to the next schedule. Returns False if there are no more schedules.
        We backtrack until we find a level with more choices, or we exhaust the root.
        """
        # find the last level that has a sibling (can be incremented)
        last_level = self._current_level - 1
        while last_level > 0 and not self._has_next[last_level]:
            last_level -= 1

        # increment the path index at last_value and reset the levels to iterate again
        if last_level > 0:
            self._path = self._path[: last_level + 1]
            self._has_next = self._has_next[: last_level + 1]
            self._path[last_level] += 1
            # reset the level, so we can draw a sequence again
            self._current_level = 1
            return True
        else:
            # finish enumeration, the object is useless after this point
            self._path = []
            self._has_next = []
            self._current_level = 0
            return False


# Implementations


@dataclass
class ScriptedScheduler:
    """
    A scheduler that replays the provided sequence of decisions (schedule).
    Once the schedule is exhausted, it raises `StopIteration`. The decisions
    queue can be extended via `extend`, once it is exhausted.

    This scheduler does not look into validity of the assumptions.
    Hence, it is the job of the user of `ExecContext` to make sure
    that the schedule does not progress under invalid assumptions.
    """

    _schedule_iter: Iterator[SchedulerDecision] | None = None

    def __init__(self, schedule: Iterable[SchedulerDecision] | None = None):
        """Create a scheduler that replays a scripted schedule.

        Args:
            schedule: An iterable of decisions to replay. If None, the scheduler
                      starts empty and `extend` must be called before use.
        """
        self._schedule_iter = iter(schedule) if schedule is not None else None

    def decide(self, request: SchedulerRequest) -> SchedulerDecision:
        """Make a decision based on the request."""
        replayed = (
            next(self._schedule_iter, None) if self._schedule_iter is not None else None
        )

        if replayed is not None:
            if isinstance(replayed, SchedulerRawValue):
                match request:
                    case SchedulerRequestOneOf():
                        base_set_sort = request.base_set.sort
                        if not isinstance(base_set_sort, SetSort):
                            raise ValueError(
                                f"one_of base expression must have SetSort, "
                                f"got {base_set_sort!r}"
                            )
                        sample_ivalue = from_python_with_sort(
                            replayed.value, base_set_sort.elem_sort
                        )
                        return SchedulerValue(Expr(IValueNode(sample_ivalue)))
                    case _:
                        raise ValueError(
                            "sample_value can only be used for one_of schedule entries"
                        )

            if isinstance(replayed, SchedulerChoiceIndex):
                idx = replayed.index
                if idx < 0:
                    raise ValueError(f"Negative choice index is invalid: {idx}")

                match request:
                    case SchedulerRequestAlternative():
                        if idx >= len(request.alternatives):
                            raise ValueError(
                                f"Choice index {idx} out of bounds for alternatives "
                                f"of length {len(request.alternatives)}"
                            )
                        return SchedulerAlternative(request.alternatives[idx])
                    case SchedulerRequestSplit():
                        if idx not in (0, 1):
                            raise ValueError(
                                f"Split choice index must be 0 or 1, got: {idx}"
                            )
                        return SchedulerSplit(bool(idx))
                    case SchedulerRequestOneOf():
                        raise ValueError(
                            "Choice index cannot be used for one_of; "
                            "expected sample_value in schedule"
                        )

            return replayed

        # set the iterator None, so the user can call `extend`
        self._schedule_iter = None
        raise StopIteration

    def extend(self, schedule: Iterable[SchedulerDecision]) -> None:
        """Add decisions to the script. Raises if the previous script is not exhausted."""
        if self._schedule_iter is not None:
            raise ValueError(
                "ScriptedScheduler.extend is called but the previous schedule "
                "is not exhausted"
            )
        self._schedule_iter = iter(schedule)


@dataclass
class RandomScheduler:
    """
    A scheduler that generates decisions at random.

    This scheduler does not look into validity of the assumptions.
    Hence, it is the job of the user of `ExecContext` to make sure
    that the schedule does not progress under invalid assumptions.
    """

    _rng: random.Random = field(default_factory=random.Random)
    _strategy: SamplingStrategy = field(default_factory=UniformSamplingStrategy)
    _bound: int = field(default=2**31 - 1)

    def __init__(
        self,
        rng: random.Random | None = None,
        strategy: SamplingStrategy | None = None,
        bound: int = 2**31 - 1,
    ):
        """Create a scheduler that generates random decisions.

        Args:
            rng: Random number generator. If None, a new Random() is created.
            strategy: Sampling strategy for sets. If None, UniformSamplingStrategy is used.
            bound: Upper bound for integer sampling. Defaults to max int32.
        """
        self._rng = rng if rng is not None else random.Random()
        self._strategy = strategy if strategy is not None else UniformSamplingStrategy()
        self._bound = bound

    def decide(self, request: SchedulerRequest) -> SchedulerDecision:
        """Make a decision based on the request."""
        match request:
            case SchedulerRequestAlternative():
                return SchedulerAlternative(self._rng.choice(request.alternatives))
            case SchedulerRequestSplit():
                return SchedulerSplit(self._rng.choice((False, True)))
            case SchedulerRequestOneOf():
                # evaluate the base set to see the values
                set_value = value(request.base_set)
                sampling_hint = SamplingHint(rng=self._rng, size=self._bound)
                sample = self._strategy.draw(set_value, sampling_hint)
                # At this point, sample is the interpreted value but we need Expr.
                # We simply wrap this value with IValueNode.
                return SchedulerValue(Expr(IValueNode(sample)))


class MockRNG:
    """A mock RNG that returns a fixed value on `randint`. Used for SystematicScheduler."""

    fixed_value: Optional[int]

    def __init__(self):
        self.fixed_value = None

    def randint(self, a: int, b: int) -> int:
        if self.fixed_value is None:
            raise ValueError("MockRNG fixed_value is not set")
        if not (0 <= self.fixed_value <= b - a):
            raise ValueError(
                f"MockRNG fixed_value {self.fixed_value} out of bounds [0, {b} - {a}]"
            )
        return a + self.fixed_value

    def __getattr__(self, name):
        raise RuntimeError(
            f"RNG is not used in SystematicScheduler (attempted to access {name})"
        )


@dataclass
class EnumerativeScheduler:
    """
    A scheduler that draws the decisions from the schedules that are
    produced by `ScheduleEnumerator`.

    This scheduler does not look into validity of the assumptions.
    Hence, it is the job of the user of `ExecContext` to make sure
    that the schedule does not progress under invalid assumptions.
    """

    enumerator: ScheduleEnumerator = field(default_factory=ScheduleEnumerator)
    _strategy: SamplingStrategy = field(default_factory=UniformSamplingStrategy)
    _bound: int = field(default=2**31 - 1)
    _rng: MockRNG = field(default_factory=MockRNG)
    # we use this dummy RNG to satisfy the SamplingStrategy interface,
    # to avoid constructing multiple RNGs down the call stack.

    def __init__(
        self,
        enumerator: Optional[ScheduleEnumerator] = None,
        strategy: Optional[SamplingStrategy] = None,
        bound: int = 2**31 - 1,
        shuffle_seed: int | None = None,
    ):
        """Create a scheduler that generates systematic decisions.

        Args:
            enumerator: Schedule enumerator for systematic decisions. If None, a new ScheduleEnumerator is used.
            strategy: Sampling strategy for sets. If None, UniformSamplingStrategy is used.
            bound: Upper bound for integer sampling. Defaults to max int32.
            shuffle_seed: If set, apply keyed permutation to diversify DFS order.
        """
        self.enumerator = (
            enumerator
            if enumerator is not None
            else ScheduleEnumerator(shuffle_seed=shuffle_seed)
        )
        self._strategy = strategy if strategy is not None else UniformSamplingStrategy()
        self._bound = bound
        self._rng = MockRNG()

    def decide(self, request: SchedulerRequest) -> SchedulerDecision:
        """Make a decision based on the request."""
        match request:
            case SchedulerRequestAlternative():
                index = self.enumerator.draw(0, len(request.alternatives) - 1)
                return SchedulerAlternative(request.alternatives[index])
            case SchedulerRequestSplit():
                index = self.enumerator.draw(0, 1)
                return SchedulerSplit(bool(index))
            case SchedulerRequestOneOf():
                # evaluate the base set to see the values
                set_value = value(request.base_set)
                if not isinstance(set_value, AbstractSetValue):
                    raise ValueError(
                        f"Expected AbstractSetValue for one_of base set, got {type(set_value)}"
                    )
                bound = self._one_of_upper_bound(set_value)
                fixed_value = self.enumerator.draw(0, bound - 1)
                self._rng.fixed_value = fixed_value
                sampling_hint = SamplingHint(rng=self._rng, size=bound)  # type: ignore
                sample = self._strategy.draw(set_value, sampling_hint)
                # At this point, sample is the interpreted value but we need Expr.
                # We simply wrap this value with IValueNode.
                return SchedulerValue(Expr(IValueNode(sample)))

    def _one_of_upper_bound(self, set_value: AbstractSetValue) -> int:
        """Compute a finite one_of upper bound for this scheduler."""
        card = set_value._cardinality()
        if isinstance(card, int):
            upper = min(self._bound, card)
            if upper <= 0:
                raise ValueError(f"Cannot draw from empty set: {type(set_value)}")
            return upper
        if isinstance(card, float):
            if self._bound <= 0:
                raise ValueError(f"Cannot draw with bound={self._bound}")
            return self._bound

        # Unknown cardinality (lazy sets): count up to configured bound.
        count = sum(1 for _ in itertools.islice(set_value, self._bound))
        if count <= 0:
            raise ValueError(f"Cannot draw from empty set: {type(set_value)}")
        return count
