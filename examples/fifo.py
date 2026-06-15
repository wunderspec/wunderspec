"""
The specification of a FIFO that instantiates two channels.

Translated from:
https://github.com/tlaplus/Examples/blob/master/specifications/SpecifyingSystems/FIFO/InnerFIFO.tla
(Lamport, "Specifying Systems", Chapter 4)

This is what is called inner FIFO in Lamport's book.
Since we do not worry about variable hiding, we just call it FIFO.
"""

from typing import Annotated

# import the definitions from the channel module
import channel

from wunderspec import (
    And,
    Expr,
    Forall,
    List,
    ListExpr,
    Param,
    Set,
    StateVar,
    Tuple,
    Val,
    coverage,
)
from wunderspec.ast.list_ast import ListEnumNode
from wunderspec.ast.sorts import IntSort
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    instance,
    invariant,
    state,
)
from wunderspec.submachine import SubMachine


@state
class FifoState(MachineStateBase):
    # CONSTANT Message
    Message: Param[set[int]]
    # VARIABLE in
    cin: StateVar[channel.Chan]
    # VARIABLE out
    cout: StateVar[channel.Chan]
    # VARIABLE q
    q: StateVar[list[int]]


# InChan  == INSTANCE Channel WITH Data <- Message, chan <- in
# OutChan == INSTANCE Channel WITH Data <- Message, chan <- out
InChan = SubMachine[channel.ChannelState](Data="Message", chan="cin")
OutChan = SubMachine[channel.ChannelState](Data="Message", chan="cout")


# Init == /\ InChan!Init
#         /\ OutChan!Init
#         /\ q = << >>
@action(init=True)
def init(c: Context[FifoState]):
    channel.init(InChan(c))
    channel.init(OutChan(c))
    c.state.q = ListExpr(ListEnumNode(IntSort()))


# TypeInvariant  ==  /\ InChan!TypeInvariant
#                    /\ OutChan!TypeInvariant
#                    /\ q \in Seq(Message)
@invariant
def type_invariant(s: FifoState) -> Annotated[Expr, bool]:
    return And(
        channel.type_invariant(InChan.view(s)),
        channel.type_invariant(OutChan.view(s)),
        Forall(s.q[i].in_(s.Message) for i in s.q.keys),
    )


# SSend(msg)  ==  /\ InChan!Send(msg)
#                 /\ UNCHANGED <<out, q>>
@action
def ssend(c: Context[FifoState], msg: Annotated[Expr, int]):
    channel.send(InChan(c), msg)


# BufRcv == /\ InChan!Rcv
#           /\ q' = Append(q, in.val)
#           /\ UNCHANGED out
@action
def buf_rcv(c: Context[FifoState]):
    s = c.state
    val = c.cache(s.cin.val, "val")
    channel.rcv(InChan(c))
    s.q = s.q + List(val)


# BufSend == /\ q # << >>
#            /\ OutChan!Send(Head(q))
#            /\ q' = Tail(q)
#            /\ UNCHANGED in
@action
def buf_send(c: Context[FifoState]):
    s = c.state
    c.assume(~s.q.is_empty)
    channel.send(OutChan(c), s.q[0])
    s.q = s.q[1:]


# RRcv == /\ OutChan!Rcv
#         /\ UNCHANGED <<in, q>>
@action
def rrcv(c: Context[FifoState]):
    channel.rcv(OutChan(c))


# Next == \/ \E msg \in Message : SSend(msg)
#         \/ BufRcv
#         \/ BufSend
#         \/ RRcv
@action
def step(c: Context[FifoState]):
    s = c.state
    alts = iter(c.alternatives("ssend", "buf_rcv", "buf_send", "rrcv"))
    with next(alts), c.one_of(s.Message, "msg") as msg:
        ssend(c, msg)
    with next(alts):
        buf_rcv(c)
    with next(alts):
        buf_send(c)
    with next(alts):
        rrcv(c)


@instance
def inst01() -> FifoState:
    return FifoState(Message=Set(Val(0), Val(1)))


@instance
def inst012() -> FifoState:
    return FifoState(Message=Set(Val(0), Val(1), Val(2)))


@coverage
def state_cov(s: FifoState) -> Expr:
    return Tuple(s.cin, s.cout, s.q)
