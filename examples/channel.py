"""
Single-slot asynchronous channel in Wunderspec.

Translated from:
https://github.com/tlaplus/Examples/blob/master/specifications/SpecifyingSystems/FIFO/Channel.tla
(Lamport, "Specifying Systems", Chapter 3)

The channel carries a single value from the set Data (a set-valued parameter).
It uses a ready/acknowledge handshake: the sender flips `rdy` on Send; the
receiver flips `ack` on Rcv.  The channel is empty when rdy = ack and full
when rdy != ack.
"""

from typing import Annotated

from wunderspec import (
    And,
    Expr,
    Field,
    Param,
    Set,
    StateVar,
    Tuple,
    Val,
    coverage,
    record,
)
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    instance,
    invariant,
    state,
)


# chan == [val : Data, rdy : {0, 1}, ack : {0, 1}]
@record
class Chan:
    val: Field[int]
    rdy: Field[int]
    ack: Field[int]


@state
class ChannelState(MachineStateBase):
    # CONSTANT Data
    Data: Param[set[int]]
    # VARIABLE chan
    chan: StateVar[Chan]


# TypeInvariant == chan \in [val : Data, rdy : {0,1}, ack : {0,1}]
@invariant
def type_invariant(s: ChannelState) -> Annotated[Expr, bool]:
    bits = Set(Val(0), Val(1))
    return And(
        s.Data.contains(s.chan.val),
        bits.contains(s.chan.rdy),
        bits.contains(s.chan.ack),
    )


# Init == /\ TypeInvariant /\ chan.ack = chan.rdy
@action(init=True)
def init(c: Context[ChannelState]):
    # Any val in Data is valid initially (channel starts empty: rdy = ack = 0).
    with c.one_of(c.state.Data, "v") as v:
        c.state.chan = Chan(val=v, rdy=Val(0), ack=Val(0))  # type: ignore


# Send(d) == /\ chan.rdy = chan.ack
#            /\ chan' = [chan EXCEPT !.val = d, !.rdy = 1 - @]
@action(inline=False)
def send(c: Context[ChannelState], d: Annotated[Expr, int]):
    s = c.state
    c.assume(s.chan.rdy == s.chan.ack)
    s.chan.val = d
    s.chan.rdy = Val(1) - s.chan.rdy


# Rcv == /\ chan.rdy # chan.ack
#        /\ chan' = [chan EXCEPT !.ack = 1 - @]
@action(inline=False)
def rcv(c: Context[ChannelState]):
    s = c.state
    c.assume(s.chan.rdy != s.chan.ack)
    s.chan.ack = Val(1) - s.chan.ack


# Next == (\E d \in Data : Send(d)) \/ Rcv
@action
def step(c: Context[ChannelState]):
    s = c.state
    alts = iter(c.alternatives("send", "rcv"))
    with next(alts), c.one_of(s.Data, "d") as d:
        send(c, d)
    with next(alts):
        rcv(c)


@instance
def inst01() -> ChannelState:
    return ChannelState(Data=Set(Val(0), Val(1)))


@instance
def inst012() -> ChannelState:
    return ChannelState(Data=Set(Val(0), Val(1), Val(2)))


@coverage
def state_cov(s: ChannelState) -> Expr:
    return Tuple(s.chan.rdy, s.chan.ack)
