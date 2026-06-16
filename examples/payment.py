"""
Minimal payment service with saga compensation and message queues.

The service consumes charge/compensate commands from an inbound queue and emits
payment events to an outbound queue. Transaction ids are idempotency keys, so a
single payer can create multiple payments over time without allowing a duplicate
command for the same transaction to move money twice.
"""

from typing import Annotated

from wunderspec import (
    And,
    BoolExpr,
    Exists,
    Expr,
    Field,
    Forall,
    List,
    Map,
    Param,
    Set,
    StateVar,
    Tuple,
    Val,
    coverage,
    example,
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

NEW = Val("new")
CHARGED = Val("charged")
DECLINED = Val("declined")
CANCELLED = Val("cancelled")
COMPENSATED = Val("compensated")
STATUSES = Set(NEW, CHARGED, DECLINED, CANCELLED, COMPENSATED)

CHARGE_COMMAND = Val("charge")
COMPENSATE_COMMAND = Val("compensate")
COMMAND_KINDS = Set(CHARGE_COMMAND, COMPENSATE_COMMAND)

CHARGE_DUPLICATE = Val("charge_duplicate")
COMPENSATE_DUPLICATE = Val("compensate_duplicate")
EVENT_KINDS = Set(
    CHARGED,
    DECLINED,
    CANCELLED,
    COMPENSATED,
    CHARGE_DUPLICATE,
    COMPENSATE_DUPLICATE,
)
LEDGER_EVENTS = EVENT_KINDS


@record
class PaymentCommand:
    kind: Field[str]
    txn: Field[int]
    payer: Field[str]
    merchant: Field[str]
    amount: Field[int]


@record
class PaymentEvent:
    kind: Field[str]
    txn: Field[int]


@record
class PaymentDetails:
    payer: Field[str]
    merchant: Field[str]
    amount: Field[int]
    status: Field[str]


@state
class PaymentState(MachineStateBase):
    Payer: Param[set[str]]
    Merchant: Param[set[str]]
    Txn: Param[set[int]]
    Amounts: Param[set[int]]
    QueueLimit: Param[int]
    OpeningBalance: Param[dict[str, int]]

    balance: StateVar[dict[str, int]]
    payment: StateVar[dict[int, PaymentDetails]]
    ledger: StateVar[dict[int, list[str]]]
    command_queue: StateVar[list[PaymentCommand]]
    event_queue: StateVar[list[PaymentEvent]]


def charge_command(txn: Expr, payer: Expr, merchant: Expr, amount: Expr) -> Expr:
    return PaymentCommand(
        kind=CHARGE_COMMAND,
        txn=txn,
        payer=payer,
        merchant=merchant,
        amount=amount,
    )  # type: ignore[return-value]


def compensate_command(txn: Expr, payer: Expr, merchant: Expr, amount: Expr) -> Expr:
    return PaymentCommand(
        kind=COMPENSATE_COMMAND,
        txn=txn,
        payer=payer,
        merchant=merchant,
        amount=amount,
    )  # type: ignore[return-value]


def payment_event(kind: Annotated[Expr, str], txn: Annotated[Expr, int]) -> Expr:
    return PaymentEvent(kind=kind, txn=txn)  # type: ignore[return-value]


def payment_details(cmd: Annotated[Expr, PaymentCommand], status: Expr) -> Expr:
    return PaymentDetails(
        payer=cmd.payer,
        merchant=cmd.merchant,
        amount=cmd.amount,
        status=status,
    )  # type: ignore[return-value]


def default_payment_details(s: PaymentState) -> Expr:
    return PaymentDetails(
        payer=s.Payer.choose(lambda _: Val(True)),
        merchant=s.Merchant.choose(lambda _: Val(True)),
        amount=s.Amounts.choose(lambda _: Val(True)),
        status=NEW,
    )  # type: ignore[return-value]


def with_status(details: Annotated[Expr, PaymentDetails], status: Expr) -> Expr:
    return PaymentDetails(
        payer=details.payer,
        merchant=details.merchant,
        amount=details.amount,
        status=status,
    )  # type: ignore[return-value]


def accounts(s: PaymentState) -> Expr:
    return s.Payer | s.Merchant


def event_count(s: PaymentState, txn: Expr, event: Expr) -> Expr:
    return s.ledger[txn].filter(lambda recorded: recorded == event).size


def command_ok(s: PaymentState, cmd: Expr) -> BoolExpr:
    return And(
        COMMAND_KINDS.contains(cmd.kind),
        s.Txn.contains(cmd.txn),
        s.Payer.contains(cmd.payer),
        s.Merchant.contains(cmd.merchant),
        s.Amounts.contains(cmd.amount),
        cmd.payer != cmd.merchant,
    )


def event_ok(s: PaymentState, event: Expr) -> BoolExpr:
    return And(EVENT_KINDS.contains(event.kind), s.Txn.contains(event.txn))


@action(init=True)
def init(c: Context[PaymentState]):
    s = c.state
    s.balance = s.OpeningBalance
    s.payment = s.Txn.map_to(lambda _: default_payment_details(s))
    s.ledger = s.Txn.map_to(lambda _: List(str))
    s.command_queue = List(PaymentCommand)
    s.event_queue = List(PaymentEvent)


@action(inline=False)
def enqueue_charge(
    c: Context[PaymentState],
    txn: Expr,
    payer: Expr,
    merchant: Expr,
    amount: Expr,
):
    s = c.state
    cmd = charge_command(txn, payer, merchant, amount)
    c.assume(command_ok(s, cmd))
    c.assume(s.command_queue.size < s.QueueLimit)
    s.command_queue += List(cmd)


@action(inline=False)
def enqueue_compensation(
    c: Context[PaymentState],
    txn: Expr,
    payer: Expr,
    merchant: Expr,
    amount: Expr,
):
    s = c.state
    cmd = compensate_command(txn, payer, merchant, amount)
    c.assume(command_ok(s, cmd))
    c.assume(s.command_queue.size < s.QueueLimit)
    s.command_queue += List(cmd)


@action(inline=False)
def process_charge(c: Context[PaymentState]):
    s = c.state
    c.assume(~s.command_queue.is_empty)
    cmd = s.command_queue[0]
    c.assume(cmd.kind == CHARGE_COMMAND)
    c.assume(command_ok(s, cmd))
    c.assume(s.payment[cmd.txn].status == NEW)
    c.assume(event_count(s, cmd.txn, CHARGED) == 0)
    c.assume(s.balance[cmd.payer] >= cmd.amount)

    s.event_queue += List(payment_event(CHARGED, cmd.txn))
    s.balance[cmd.payer] -= cmd.amount
    s.balance[cmd.merchant] += cmd.amount
    s.payment[cmd.txn] = payment_details(cmd, CHARGED)
    s.ledger[cmd.txn] += List(CHARGED)
    s.command_queue = s.command_queue[1:]


@action(inline=False)
def decline_charge(c: Context[PaymentState]):
    s = c.state
    c.assume(~s.command_queue.is_empty)
    cmd = s.command_queue[0]
    c.assume(cmd.kind == CHARGE_COMMAND)
    c.assume(command_ok(s, cmd))
    c.assume(s.payment[cmd.txn].status == NEW)
    c.assume(s.balance[cmd.payer] < cmd.amount)

    s.event_queue += List(payment_event(DECLINED, cmd.txn))
    s.payment[cmd.txn] = payment_details(cmd, DECLINED)
    s.ledger[cmd.txn] += List(DECLINED)
    s.command_queue = s.command_queue[1:]


@action(inline=False)
def ignore_duplicate_charge(c: Context[PaymentState]):
    s = c.state
    c.assume(~s.command_queue.is_empty)
    cmd = s.command_queue[0]
    c.assume(cmd.kind == CHARGE_COMMAND)
    c.assume(command_ok(s, cmd))
    c.assume(s.payment[cmd.txn].status != NEW)
    c.assume(event_count(s, cmd.txn, CHARGE_DUPLICATE) == 0)

    s.event_queue += List(payment_event(CHARGE_DUPLICATE, cmd.txn))
    s.ledger[cmd.txn] += List(CHARGE_DUPLICATE)
    s.command_queue = s.command_queue[1:]


@action(inline=False)
def compensate(c: Context[PaymentState]):
    s = c.state
    c.assume(~s.command_queue.is_empty)
    cmd = s.command_queue[0]
    c.assume(cmd.kind == COMPENSATE_COMMAND)
    c.assume(command_ok(s, cmd))
    c.assume(s.payment[cmd.txn].status == CHARGED)
    c.assume(event_count(s, cmd.txn, CHARGED) == 1)
    c.assume(event_count(s, cmd.txn, COMPENSATED) == 0)

    details = s.payment[cmd.txn]
    c.assume(s.balance[details.merchant] >= details.amount)

    s.event_queue += List(payment_event(COMPENSATED, cmd.txn))
    s.balance[details.payer] += details.amount
    s.balance[details.merchant] -= details.amount
    s.payment[cmd.txn] = with_status(details, COMPENSATED)
    s.ledger[cmd.txn] += List(COMPENSATED)
    s.command_queue = s.command_queue[1:]


@action(inline=False)
def cancel_before_charge(c: Context[PaymentState]):
    s = c.state
    c.assume(~s.command_queue.is_empty)
    cmd = s.command_queue[0]
    c.assume(cmd.kind == COMPENSATE_COMMAND)
    c.assume(command_ok(s, cmd))
    c.assume(s.payment[cmd.txn].status == NEW)

    s.event_queue += List(payment_event(CANCELLED, cmd.txn))
    s.payment[cmd.txn] = payment_details(cmd, CANCELLED)
    s.ledger[cmd.txn] += List(CANCELLED)
    s.command_queue = s.command_queue[1:]


@action(inline=False)
def ignore_duplicate_compensation(c: Context[PaymentState]):
    s = c.state
    c.assume(~s.command_queue.is_empty)
    cmd = s.command_queue[0]
    c.assume(cmd.kind == COMPENSATE_COMMAND)
    c.assume(command_ok(s, cmd))
    c.assume(Set(DECLINED, CANCELLED, COMPENSATED).contains(s.payment[cmd.txn].status))
    c.assume(event_count(s, cmd.txn, COMPENSATE_DUPLICATE) == 0)

    s.event_queue += List(payment_event(COMPENSATE_DUPLICATE, cmd.txn))
    s.ledger[cmd.txn] += List(COMPENSATE_DUPLICATE)
    s.command_queue = s.command_queue[1:]


@action
def step(c: Context[PaymentState]):
    s = c.state
    alts = iter(
        c.alternatives(
            "EnqueueCharge",
            "EnqueueCompensation",
            "ProcessCharge",
            "DeclineCharge",
            "IgnoreDuplicateCharge",
            "Compensate",
            "CancelBeforeCharge",
            "IgnoreDuplicateCompensation",
        )
    )
    with (
        next(alts),
        c.one_of(s.Txn, "charge_txn") as txn,
        c.one_of(s.Payer, "charge_payer") as payer,
        c.one_of(s.Merchant, "charge_merchant") as merchant,
        c.one_of(s.Amounts, "charge_amount") as amount,
    ):
        enqueue_charge(c, txn, payer, merchant, amount)
    with (
        next(alts),
        c.one_of(s.Txn, "compensation_txn") as txn,
        c.one_of(s.Payer, "compensation_payer") as payer,
        c.one_of(s.Merchant, "compensation_merchant") as merchant,
        c.one_of(s.Amounts, "compensation_amount") as amount,
    ):
        enqueue_compensation(c, txn, payer, merchant, amount)
    with next(alts):
        process_charge(c)
    with next(alts):
        decline_charge(c)
    with next(alts):
        ignore_duplicate_charge(c)
    with next(alts):
        compensate(c)
    with next(alts):
        cancel_before_charge(c)
    with next(alts):
        ignore_duplicate_compensation(c)


@invariant
def type_invariant(s: PaymentState) -> BoolExpr:
    return And(
        s.Amounts.forall(lambda a: a > 0),
        s.Payer.size > 0,
        s.Merchant.size > 0,
        s.Amounts.size > 0,
        s.QueueLimit > 0,
        s.OpeningBalance.keys == accounts(s),
        s.balance.keys == accounts(s),
        accounts(s).forall(lambda account: s.OpeningBalance[account] >= 0),
        accounts(s).forall(lambda account: s.balance[account] >= 0),
        s.payment.keys == s.Txn,
        s.payment.keys.forall(
            lambda txn: And(
                s.Payer.contains(s.payment[txn].payer),
                s.Merchant.contains(s.payment[txn].merchant),
                s.Amounts.contains(s.payment[txn].amount),
                STATUSES.contains(s.payment[txn].status),
                s.payment[txn].payer != s.payment[txn].merchant,
            )
        ),
        s.ledger.keys == s.Txn,
        s.Txn.forall(
            lambda txn: s.ledger[txn].keys.forall(
                lambda i: LEDGER_EVENTS.contains(s.ledger[txn][i])
            )
        ),
        s.command_queue.keys.forall(lambda i: command_ok(s, s.command_queue[i])),
        s.event_queue.keys.forall(lambda i: event_ok(s, s.event_queue[i])),
    )


@invariant
def no_overdraft(s: PaymentState) -> BoolExpr:
    return Forall(s.balance[account] >= 0 for account in accounts(s))


@invariant
def no_double_charge(s: PaymentState) -> BoolExpr:
    return Forall(event_count(s, txn, CHARGED) <= Val(1) for txn in s.Txn)


@invariant
def all_inv(s: PaymentState) -> BoolExpr:
    return no_overdraft(s) & no_double_charge(s)


@example
def some_compensated(s: PaymentState) -> BoolExpr:
    return Exists(s.payment[txn].status == COMPENSATED for txn in s.Txn)


@coverage
def state_cov(s: PaymentState) -> Expr:
    return Tuple(
        s.balance,
        s.payment,
        s.ledger,
        s.command_queue,
        s.event_queue,
    )


@instance
def tiny() -> PaymentState:
    return PaymentState(
        Payer=Set("alice", "bob"),
        Merchant=Set("dave", "eve"),
        Txn=Set(1, ..., 5),
        Amounts=Set(1, ..., 9),
        QueueLimit=3,
        OpeningBalance=Map(("alice", 10), ("bob", 15), ("dave", 10), ("eve", 5)),
    )
