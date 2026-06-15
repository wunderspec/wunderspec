"""
Dekker's mutual exclusion algorithm using direct Wunderspec actions.

Translated from the PlusCal specification at:
https://github.com/duerrfk/skp/blob/master/criticalsection5dekker/criticalsection5dekker.tla

The source has two concrete processes, P and Q, and a shared NCS procedure.
This version models them as one explicit state-machine step over process ids
{1, 2}, without using the process builder.
"""

from enum import Enum, auto

from wunderspec import (
    Always,
    AndT,
    Eventually,
    Expr,
    ImpliesT,
    MachineStateBase,
    Set,
    StateVar,
    Tuple,
    Val,
    coverage,
    instance,
    invariant,
    state,
    temporal,
)
from wunderspec.expr import SetExpr
from wunderspec.machine import Context, action


class PC(Enum):
    NCS = auto()
    NCS_DECIDE = auto()
    NCS_LOOP = auto()
    NCS_SKIP = auto()
    SET_WANT = auto()
    WAIT = auto()
    CHECK_TURN = auto()
    LOWER_WANT = auto()
    AWAIT_TURN = auto()
    RAISE_WANT = auto()
    CS = auto()
    SET_TURN = auto()
    CLEAR_WANT = auto()


@state
class DekkerState(MachineStateBase):
    turn: StateVar[int]
    want: StateVar[dict[int, bool]]
    is_endless: StateVar[dict[int, int]]
    pc: StateVar[dict[int, PC]]


def procs(_: DekkerState) -> SetExpr:
    return Set(Val(1), Val(2))


def peer(pid: Expr) -> Expr:
    return Val(3) - pid


@action(init=True)
def init(c: Context[DekkerState]):
    s, ps = c.state, procs(c.state)
    s.turn = Val(1)
    s.want = ps.map_to(lambda _: Val(False))
    s.is_endless = ps.map_to(lambda _: Val(0))
    s.pc = ps.map_to(lambda _: Val(PC.NCS))


@action(inline=True)
def dekker_step(c: Context[DekkerState], q: Expr):
    s = c.state
    other = peer(q)
    alts = iter(c.alternatives(*(label.name for label in PC)))

    with next(alts):
        c.assume(s.pc[q] == Val(PC.NCS))
        with c.one_of(Set(0, 1), "x") as x:
            s.is_endless[q] = x
            s.pc[q] = PC.NCS_DECIDE
    with next(alts):
        c.assume(s.pc[q] == Val(PC.NCS_DECIDE))
        endless, finite = c.split(s.is_endless[q] == 1)
        with endless:
            s.pc[q] = PC.NCS_LOOP
        with finite:
            s.pc[q] = PC.SET_WANT
    with next(alts):
        c.assume(s.pc[q] == Val(PC.NCS_LOOP))
        s.pc[q] = PC.NCS_SKIP
    with next(alts):
        c.assume(s.pc[q] == Val(PC.NCS_SKIP))
        s.pc[q] = PC.NCS_LOOP
    with next(alts):
        c.assume(s.pc[q] == Val(PC.SET_WANT))
        s.want[q] = True
        s.pc[q] = PC.WAIT
    with next(alts):
        c.assume(s.pc[q] == Val(PC.WAIT))
        wants_in, can_enter = c.split(s.want[other])
        with wants_in:
            s.pc[q] = PC.CHECK_TURN
        with can_enter:
            s.pc[q] = PC.CS
    with next(alts):
        c.assume(s.pc[q] == Val(PC.CHECK_TURN))
        their_turn, my_turn = c.split(s.turn == other)
        with their_turn:
            s.pc[q] = PC.LOWER_WANT
        with my_turn:
            s.pc[q] = PC.WAIT
    with next(alts):
        c.assume(s.pc[q] == Val(PC.LOWER_WANT))
        s.want[q] = False
        s.pc[q] = PC.AWAIT_TURN
    with next(alts):
        c.assume(s.pc[q] == Val(PC.AWAIT_TURN))
        c.assume(s.turn == q)
        s.pc[q] = PC.RAISE_WANT
    with next(alts):
        c.assume(s.pc[q] == Val(PC.RAISE_WANT))
        s.want[q] = True
        s.pc[q] = PC.WAIT
    with next(alts):
        c.assume(s.pc[q] == Val(PC.CS))
        s.pc[q] = PC.SET_TURN
    with next(alts):
        c.assume(s.pc[q] == Val(PC.SET_TURN))
        s.turn = other
        s.pc[q] = PC.CLEAR_WANT
    with next(alts):
        c.assume(s.pc[q] == Val(PC.CLEAR_WANT))
        s.want[q] = False
        s.pc[q] = PC.NCS


@action
def step(c: Context[DekkerState]):
    with c.one_of(procs(c.state), "self") as q:
        dekker_step(c, q)


@invariant
def mutual_exclusion(s: DekkerState):
    return (s.pc[Val(1)] != Val(PC.CS)).or_(s.pc[Val(2)] != Val(PC.CS))


@temporal
def no_deadlock(s: DekkerState):
    both_want = (s.pc[Val(1)] == Val(PC.SET_WANT)).and_(
        s.pc[Val(2)] == Val(PC.SET_WANT)
    )
    someone_enters = (s.pc[Val(1)] == Val(PC.CS)).or_(s.pc[Val(2)] == Val(PC.CS))
    return Always(ImpliesT(both_want, Eventually(someone_enters)))


@temporal
def no_starvation(s: DekkerState):
    return AndT(
        Always(
            ImpliesT(
                s.pc[Val(1)] == Val(PC.SET_WANT),
                Eventually(s.pc[Val(1)] == Val(PC.CS)),
            )
        ),
        Always(
            ImpliesT(
                s.pc[Val(2)] == Val(PC.SET_WANT),
                Eventually(s.pc[Val(2)] == Val(PC.CS)),
            )
        ),
    )


@coverage
def state_cov(s: DekkerState) -> Expr:
    return Tuple(s.turn, s.want, s.is_endless, s.pc)


proto = DekkerState()


@instance
def default() -> DekkerState:
    return DekkerState()
