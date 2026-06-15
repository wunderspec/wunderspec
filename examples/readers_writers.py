# Readers-writers example using Wunderspec.
#
# A manual translation of the example from:
# https://github.com/tlaplus/Examples/blob/master/specifications/ReadersWriters/ReadersWriters.tla
#
# Igor Konnov, 2026

from enum import Enum
from typing import Annotated

from wunderspec import *
from wunderspec import Param, StateVar, expr
from wunderspec.machine import invariant, temporal


class RW(Enum):
    READ = 1
    WRITE = 2


RW_actor = tuple[RW, int]


@state
class ReadersWritersState(MachineStateBase):
    NumActors: Param[int]
    # set of processes currently reading
    readers: StateVar[set[int]]
    # set of processes currently writing
    writers: StateVar[set[int]]
    # queue of processes waiting to access the resource
    waiting: StateVar[list[RW_actor]]


@expr(inline=False)
def actors(s: ReadersWritersState) -> Annotated[Expr, set[int]]:
    return Set(1, ..., s.NumActors)


@expr(pure=True, inline=False)
def list_to_set(xs: Expr) -> Annotated[Expr, set[int]]:
    return xs.keys.map(lambda i: xs[i])


@expr(inline=False)
def waiting_to_read(s: ReadersWritersState):
    return list_to_set(s.waiting.filter(lambda w: w[0] == RW.READ)).map(lambda x: x[1])


@expr(inline=False)
def waiting_to_write(s: ReadersWritersState):
    return list_to_set(s.waiting.filter(lambda w: w[0] == RW.WRITE)).map(lambda x: x[1])


# actions


@action(inline=False)
def try_read(c: Context[ReadersWritersState], actor: Annotated[Expr, int]):
    s = c.state
    c.assume(~waiting_to_read(s).contains(actor))
    s.waiting = s.waiting + List(Tuple(Val(RW.READ), actor))


@action(inline=False)
def try_write(c: Context[ReadersWritersState], actor: Annotated[Expr, int]):
    s = c.state
    c.assume(~waiting_to_write(s).contains(actor))
    s.waiting = s.waiting + List(Tuple(Val(RW.WRITE), actor))


@action(inline=False)
def read(c: Context[ReadersWritersState], actor: Annotated[Expr, int]):
    s = c.state
    s.readers = s.readers | Set(actor)
    s.waiting = s.waiting[1:]


@action(inline=False)
def write(c: Context[ReadersWritersState], actor: Annotated[Expr, int]):
    s = c.state
    c.assume(s.readers.is_empty)
    s.writers = s.writers | Set(actor)
    s.waiting = s.waiting[1:]


@action(inline=False)
def read_or_write(c: Context[ReadersWritersState]):
    s = c.state
    c.assume(~s.waiting.is_empty)
    c.assume(s.writers.is_empty)
    (op, actor) = s.waiting[0]
    read_, write_ = c.split(op == Val(RW.READ))
    with read_:
        read(c, actor)
    with write_:
        write(c, actor)


@action(inline=False)
def stop_activity(c: Context[ReadersWritersState], actor: Annotated[Expr, int]):
    s = c.state
    then_, else_ = c.split(s.readers.contains(actor))
    with then_:
        s.readers = s.readers - Set(actor)
    with else_:
        s.writers = s.writers - Set(actor)


@action(inline=False)
def stop(c: Context[ReadersWritersState]):
    s = c.state
    with c.one_of(s.readers | s.writers, "actor") as actor:
        stop_activity(c, actor)


# specification
@action(init=True)
def init(c: Context[ReadersWritersState]):
    s = c.state
    s.readers = Set(sort_of(int))
    s.writers = Set(sort_of(int))
    s.waiting = List(RW_actor)


@action
def step(c: Context[ReadersWritersState]):
    alts = iter(c.alternatives("try_read", "try_write", "read_or_write", "stop"))
    with next(alts):
        with c.one_of(actors(c.state), "actor") as actor:
            try_read(c, actor)
    with next(alts):
        with c.one_of(actors(c.state), "actor") as actor:
            try_write(c, actor)
    with next(alts):
        read_or_write(c)
    with next(alts):
        stop(c)


# fairness
@temporal
def fairness(s: ReadersWritersState):
    v = ("readers", "writers", "waiting")
    return AndT(
        actors(s).forall(lambda actor: WeakFair(try_read, actor, vars=v)),
        actors(s).forall(lambda actor: WeakFair(try_write, actor, vars=v)),
        WeakFair(read_or_write, vars=v),
        WeakFair(stop, vars=v),
    )


# Spec == Init /\ [][Next]_vars /\ Fairness


# invariants
@invariant
def safety(s: ReadersWritersState) -> Annotated[Expr, bool]:
    return And(
        ~(~s.readers.is_empty & ~s.writers.is_empty),
        s.writers.size <= Val(1),
    )


# liveness
@temporal
def liveness(s: ReadersWritersState):
    return AndT(
        actors(s).forall(lambda actor: Always(Eventually(s.readers.contains(actor)))),
        actors(s).forall(lambda actor: Always(Eventually(s.writers.contains(actor)))),
        actors(s).forall(lambda actor: Always(Eventually(~s.readers.contains(actor)))),
        actors(s).forall(lambda actor: Always(Eventually(~s.writers.contains(actor)))),
    )


# a few instances for testing and coverage


@instance
def inst2() -> ReadersWritersState:
    return ReadersWritersState(NumActors=2)


@instance
def inst3() -> ReadersWritersState:
    return ReadersWritersState(NumActors=3)


@instance
def inst4() -> ReadersWritersState:
    return ReadersWritersState(NumActors=4)


@coverage
def state_cov(s: ReadersWritersState) -> Expr:
    return Tuple(s.readers, s.writers, s.waiting)
