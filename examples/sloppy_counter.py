"""
Multiprocessor sharded counter using one percpu_counter-style counter.
These counters are usually used for fast statistics counters.

Inspired by the LWN discussion of Linux's ``percpu_counter``:
https://lwn.net/Articles/170003/

Igor Konnov, 2026
"""

from wunderspec import (
    And,
    Expr,
    Or,
    Param,
    Set,
    StateVar,
    Tuple,
    Val,
    coverage,
    example,
    instance,
    invariant,
    state,
)
from wunderspec.expr import SetExpr
from wunderspec.machine import Context, MachineStateBase, action


@state
class SloppyCounterState(MachineStateBase):
    N: Param[int]  # the total number of CPUs
    BATCH: Param[int]  # the maximal lag before syncing
    WORD_WIDTH: Param[int]  # the number of bits in the CPU word
    global_count: StateVar[int]  # the global atomic_t counter
    local_count: StateVar[dict[int, int]]  # one local counter per CPU
    ghost_count: StateVar[int]  # the exact counter by observer


def cpus(s: SloppyCounterState) -> SetExpr:
    return Set(Val(1), ..., s.N)


def local_total(s: SloppyCounterState) -> Expr:
    return s.local_count.reduce(lambda acc, _cpu, count: acc + count, Val(0))  # type: ignore


def max_slop(s: SloppyCounterState) -> Expr:
    """The maximum counter slop over all CPUs"""
    return s.N * (s.BATCH - 1)


@action(init=True)
def init(c: Context[SloppyCounterState]):
    s = c.state
    s.global_count = Val(0)
    s.local_count = cpus(s).map_to(lambda _: Val(0))
    s.ghost_count = Val(0)


@action(inline=False)
def increment(c: Context[SloppyCounterState], cpu: Expr):
    """Increment the sloppy counter"""
    s = c.state
    # the observer keeps track of the precise value
    s.ghost_count = (s.ghost_count + 1) % (2**s.WORD_WIDTH)
    next_local = s.local_count[cpu] + 1
    keep_local, propagate = c.split(next_local < s.BATCH)

    with keep_local:  # cheap local increment
        s.local_count[cpu] = next_local

    with propagate:  # expensive atomic increase
        s.global_count = (s.global_count + next_local) % (2**s.WORD_WIDTH)
        s.local_count[cpu] = 0


@action
def step(c: Context[SloppyCounterState]):
    with c.one_of(cpus(c.state), "cpu") as cpu:
        increment(c, cpu)


@invariant
def accounting(s: SloppyCounterState) -> Expr:
    return s.ghost_count == (s.global_count + local_total(s)) % (2**s.WORD_WIDTH)


@invariant
def local_shards_bounded(s: SloppyCounterState) -> Expr:
    return cpus(s).forall(
        lambda cpu: And(
            s.local_count[cpu] >= 0,
            s.local_count[cpu] < s.BATCH,
        )
    )


@invariant
def approximation_bound(s: SloppyCounterState) -> Expr:
    return Or(
        And(  # no overflow of ghost_count
            s.global_count <= s.ghost_count,
            s.ghost_count <= s.global_count + max_slop(s),
        ),
        s.ghost_count <= (s.global_count + max_slop(s)) % (2**s.WORD_WIDTH),
    )


@example
def sloppy_read_possible(s: SloppyCounterState) -> Expr:
    return s.global_count < s.ghost_count


@coverage
def state_cov(s: SloppyCounterState) -> Expr:
    return Tuple(s.global_count, s.local_count, s.ghost_count)


@instance
def n2_batch3_8bit() -> SloppyCounterState:
    return SloppyCounterState(N=2, BATCH=3, WORD_WIDTH=8)


@instance
def n2_batch3_16bit() -> SloppyCounterState:
    return SloppyCounterState(N=2, BATCH=3, WORD_WIDTH=16)


@instance
def n2_batch100_32bit() -> SloppyCounterState:
    return SloppyCounterState(N=2, BATCH=100, WORD_WIDTH=32)


@instance
def n4_batch4_8bit() -> SloppyCounterState:
    return SloppyCounterState(N=4, BATCH=4, WORD_WIDTH=8)
