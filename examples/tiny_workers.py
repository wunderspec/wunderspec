"""
Tiny add/remove workers example in Wunderspec.

Inspired by Hillel Wayne's examples:

https://learntla.com/topics/tips.html
"""

from wunderspec import Expr, Param, Set, StateVar, Tuple, Val, invariant
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    coverage,
    instance,
    state,
)


@state
class TinyWorkersState(MachineStateBase):
    # all possible workers
    Worker: Param[set[int]]
    # active workers
    active: StateVar[set[int]]
    # the count of active workers
    count: StateVar[int]


@action(init=True)
def init(c: Context[TinyWorkersState]):
    s = c.state
    s.active = Set(int)
    s.count = Val(0)


@action(inline=False)
def add(c: Context[TinyWorkersState], w: Expr):
    s = c.state
    c.assume(~s.active.contains(w))
    s.active |= Set(w)
    s.count += 1


@action(inline=False)
def remove(c: Context[TinyWorkersState], w: Expr):
    s = c.state
    c.assume(s.active.contains(w))
    s.active -= Set(w)
    s.count -= 1


@action
def step(c: Context[TinyWorkersState]):
    with c.one_of(c.state.Worker, "w") as w:
        alts = iter(c.alternatives("Add", "Remove"))
        with next(alts):
            add(c, w)
        with next(alts):
            remove(c, w)


@invariant
def inv(s: TinyWorkersState) -> Expr:
    return s.count == s.active.size


@instance
def workers_2() -> TinyWorkersState:
    return TinyWorkersState(Worker=Set(1, 2))


@instance
def workers_3() -> TinyWorkersState:
    return TinyWorkersState(Worker=Set(1, 2, 3))


@coverage
def state_cov(s: TinyWorkersState) -> Expr:
    return Tuple(s.active, s.count)
