# A simple Ponzi contract specification that is derived from the Quint spec:
#
# https://github.com/informalsystems/quint/blob/main/examples/solidity/SimplePonzi/simplePonzi.qnt
#
# This module specifies the state machine for demonstration purposes.

from simple_ponzi import *

from wunderspec import (
    Context,
    Expr,
    MachineStateBase,
    Set,
    StateVar,
    Tuple,
    action,
    state,
)
from wunderspec.machine import coverage, invariant

ADDR: Expr = Set("alice", "bob", "charlie", "eve")


# the state schema for the Ponzi state machine
@state
class PonziMachineState(MachineStateBase):
    evm_state: StateVar[EvmState]
    ponzi_state: StateVar[PonziState]


# initialize the state machine
@action(init=True)
def init(c: Context[PonziMachineState]):
    s = c.state
    s.evm_state = EvmState(  # type: ignore[assignment]
        balances=ADDR.map_to(lambda _: Val(10_000))
    )
    s.ponzi_state = new_ponzi("alice")


# an investor is sending tokens, errors are ignored
@action
def on_receive(c: Context[PonziMachineState], investor: Expr, amount: Expr):
    s = c.state
    result = receive(s.evm_state, s.ponzi_state, investor, amount)
    c.assume(result.tag == "Ok")
    s.evm_state = result.match(Ok=lambda v: v.evm, default=s.evm_state)
    s.ponzi_state = result.match(Ok=lambda v: v.ponzi, default=s.ponzi_state)


# a single step by the state machine
@action
def step(c: Context[PonziMachineState]):
    s = c.state
    with c.one_of(ADDR) as investor:
        max_balance = s.evm_state.balances[investor]
        with c.one_of(Set(-10, ..., max_balance + 10)) as amount:
            on_receive(c, investor, amount)


@invariant
def balance_non_negative(s: PonziMachineState):
    return s.evm_state.balances.keys.forall(lambda a: s.evm_state.balances[a] >= 0)


@coverage
def state_cov(s: PonziMachineState) -> Expr:
    return Tuple(s.evm_state, s.ponzi_state)
