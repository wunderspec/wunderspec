"""
A producer/consumer broker whose mailbox is a bag (multiset) of messages.

This is a runnable demo for the user-defined ``Bag`` primitive from
``bags.py``. Producers add a copy of a message to the mailbox bag (up to a
capacity bound), and consumers remove a copy of a message that is present.
Because the mailbox is just a ``dict[int, int]`` under the hood, it lives in a
state variable and is checked by all the usual Wunderspec tools.

Note how the spec only ever talks about *bags*: it reads the state variable
with ``Bag(s.bag)`` and writes it back with ``... .as_map``. The bag algebra
(``add_one``, ``remove_one``, ``cardinality``, ``contains``) lowers to the
standard Wunderspec AST behind the scenes.

Igor Konnov, 2026 (done with Claude Opus 4.8)
"""

# import the user-defined Bag primitive from the sibling module
from bags import Bag

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
class BrokerState(MachineStateBase):
    # the universe of message ids that may be produced
    Msg: Param[set[int]]
    # capacity bound on the total number of messages buffered at once
    Cap: Param[int]
    # the mailbox: a bag (multiset) of messages, element -> count
    bag: StateVar[dict[int, int]]
    # how many messages have been consumed so far
    delivered: StateVar[int]


@action(init=True)
def init(c: Context[BrokerState]):
    s = c.state
    s.bag = Bag.empty(int).as_map
    s.delivered = Val(0)


@action(inline=False)
def produce(c: Context[BrokerState], m: Expr):
    s = c.state
    b = Bag(s.bag)
    # respect the capacity bound
    c.assume(b.cardinality < s.Cap)
    s.bag = b.add_one(m).as_map


@action(inline=False)
def consume(c: Context[BrokerState], m: Expr):
    s = c.state
    b = Bag(s.bag)
    # only consume a message that is actually present
    c.assume(b.contains(m))
    s.bag = b.remove_one(m).as_map
    s.delivered += 1


@action
def step(c: Context[BrokerState]):
    s = c.state
    with c.one_of(s.Msg, "m") as m:
        alts = iter(c.alternatives("Produce", "Consume"))
        with next(alts):
            produce(c, m)
        with next(alts):
            consume(c, m)


@invariant
def non_negative(s: BrokerState) -> Expr:
    # Holds by construction: bag difference drops any element whose count
    # reaches 0, so every element in the domain has a strictly positive count.
    b = Bag(s.bag)
    return b.to_set().forall(lambda m: b[m] > 0)


@invariant
def capacity_bound(s: BrokerState) -> Expr:
    return Bag(s.bag).cardinality <= s.Cap


@invariant
def type_invariant(s: BrokerState) -> Expr:
    return Bag(s.bag).to_set().issubset(s.Msg)


@instance
def small() -> BrokerState:
    return BrokerState(Msg=Set(1, 2), Cap=3)


@instance
def medium() -> BrokerState:
    return BrokerState(Msg=Set(1, 2, 3), Cap=4)


@coverage
def state_cov(s: BrokerState) -> Expr:
    return Tuple(s.bag, s.delivered)
