# A simple Ponzi contract specification that is derived from the Quint spec:
#
# https://github.com/informalsystems/quint/blob/main/examples/solidity/SimplePonzi/simplePonzi.qnt
#
# This module specifies the functional part (the smart contract).

from typing import TypeAlias

from wunderspec import Field, Variant
from wunderspec.expr import Expr
from wunderspec.flow import flow, with_flow
from wunderspec.lang import Val, record, union

# Addresses are string literals
Addr: TypeAlias = str


# A state of the EVM that is observed/modified by the contract
@record
class EvmState:
    """The state of EVM, as relevant to the contract"""

    balances: Field[dict[Addr, int]]


@record
class PonziState:
    """The state of the Ponzi contract"""

    currentInvestor: Field[Addr]
    currentInvestment: Field[int]


@record
class ContractEnv:
    """The contract environment"""

    evm: Field[EvmState]
    ponzi: Field[PonziState]


@union
class Result:
    Ok: Variant[ContractEnv]
    Err: Variant[str]


def new_ponzi(owner: Addr) -> Expr:
    """Create a new Ponzi contract with the given owner."""
    return PonziState(  # type: ignore[return-value]
        currentInvestor=Val(owner),
        currentInvestment=Val(0),
    )


@with_flow
def receive(evm: Expr, ponzi: Expr, investor: Expr, amount: Expr) -> Expr:
    """Receive an investment and distribute the rewards (to the previous investor)."""
    with flow.if_(amount > evm.balances[investor]):
        flow.return_(Result.Err("Insufficient funds"))

    with flow.if_(amount < Val(11) * ponzi.currentInvestment / 10):
        flow.return_(Result.Err("New investment must be 110%% of the last one"))

    next_evm = evm.edit()
    next_evm.balances[investor] = evm.balances[investor] - amount
    next_evm.balances[ponzi.currentInvestor] = (
        evm.balances[ponzi.currentInvestor] + amount
    )
    evm = next_evm.result

    flow.return_(
        Result.Ok(
            ContractEnv(
                evm=evm,
                ponzi=PonziState(  # type: ignore[arg-type]
                    currentInvestor=investor,
                    currentInvestment=amount,
                ),
            )
        )
    )

    return flow.end()
