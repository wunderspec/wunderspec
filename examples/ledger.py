# The specification of a simple accounting system. A client registers a
# transaction with the authority, which assigns it a sequence number.
# This sequence number is used in the ledger entry.
#
# Igor Konnov, 2026.

from typing_extensions import Annotated

from wunderspec import *

BANK = "bank"


@record
class LedgerEntry:
    """A single ledger entry"""

    seq_no: Field[int]
    debit: Field[int]
    credit: Field[int]


@state
class LedgerState(MachineStateBase):
    # the set of companies in the system
    COMPANIES: Param[set[str]]
    # the set of accounts (in the accounting sense) in the system
    ACCOUNTS: Param[set[str]]
    # the set of possible transaction amounts in the system
    AMOUNTS: Param[set[int]]
    # the initial balance in the bank
    INITIAL_BALANCE: Param[int]
    # the latest registered sequence number for each company
    registered_seq_no: StateVar[dict[str, int]]
    # the pending sequence number for each company, if not -1
    pending_seq_no: StateVar[dict[str, int]]
    # the ledger entries for each company
    ledger: StateVar[dict[str, list[LedgerEntry]]]
    # balances for each company
    balances: StateVar[dict[str, int]]


@action(init=True)
def init(c: Context[LedgerState]):
    s = c.state
    s.registered_seq_no = Map(0 for _ in s.COMPANIES)
    s.pending_seq_no = Map(-1 for _ in s.COMPANIES)
    s.ledger = Map(List(LedgerEntry) for _ in s.COMPANIES)
    s.balances = Map(
        s.INITIAL_BALANCE.if_(a == BANK).else_(0) for a in s.COMPANIES | Set(BANK)
    )


@action
def start_transaction(c: Context[LedgerState], company: Annotated[Expr, str]):
    """The client registers a new transaction with the authority."""
    s = c.state
    c.assume(s.pending_seq_no[company] == -1)
    s.pending_seq_no[company] = s.registered_seq_no[company]
    s.registered_seq_no[company] += 1


@action
def commit_transaction(
    c: Context[LedgerState],
    company: Annotated[Expr, str],
    debit: Annotated[Expr, int],
    credit: Annotated[Expr, int],
):
    """The client commits a pending transaction with the given debit and credit amounts."""
    s = c.state
    seq_no = s.pending_seq_no[company]
    # the client commits the transaction in their ledger
    c.assume(seq_no != -1)
    c.assume((credit >= 0) & (s.balances[company] >= credit - debit))
    c.assume((debit >= 0) & (s.balances[BANK] >= debit - credit))
    s.balances[company] += debit - credit
    s.balances[BANK] += credit - debit
    s.ledger[company] += List(LedgerEntry(seq_no=seq_no, debit=debit, credit=credit))
    s.pending_seq_no[company] = -1


@action
def crash_and_restart(c: Context[LedgerState], company: Annotated[Expr, str]):
    """The client crashes and restarts, losing any pending transaction."""
    s = c.state
    s.pending_seq_no[company] = -1


@action
def step(c: Context[LedgerState]):
    """A single step of the system"""
    start, commit, crash = c.alternatives("start", "commit", "crash")
    with start, c.one_of(c.state.COMPANIES, "c") as company:
        start_transaction(c, company)
    with (
        commit,
        c.one_of(c.state.COMPANIES, "c") as company,
        c.one_of(c.state.AMOUNTS, "debit") as debit,
        c.one_of(c.state.AMOUNTS, "credit") as credit,
    ):
        commit_transaction(c, company, debit, credit)
    with crash, c.one_of(c.state.COMPANIES, "c") as company:
        crash_and_restart(c, company)


@invariant
def no_gaps(s: LedgerState):
    """There should be no gaps in the sequence numbers of committed transactions."""
    # THIS INVARIANT IS VIOLATED!

    def no_gap_for_company(company: Expr) -> BoolExpr:
        entries = s.ledger[company]
        return Forall(
            Or(
                (i == 0) & (entries[i].seq_no == 0),
                (i > 0) & (entries[i].seq_no == entries[i - 1].seq_no + 1),
            )
            for i in entries.keys
        )

    return Forall(no_gap_for_company(c) for c in s.COMPANIES)


@invariant
def no_total_change(s: LedgerState):
    """The sum of all balances remains the same"""
    return s.balances.reduce(lambda s, _, v: s + v, Val(0)) == s.INITIAL_BALANCE


@example
def ledger_3_entries(s: LedgerState) -> BoolExpr:
    """Produce an example of a ledger with 3 entries for some company."""
    return Exists(s.ledger[c].size >= 3 for c in s.COMPANIES)


@instance
def tiny_ledger() -> LedgerState:
    """A tiny instance of two companies and two amounts"""
    return LedgerState(
        COMPANIES=Set("Alice", "Bob"),
        ACCOUNTS=Set("Alice", "Bob", BANK),
        AMOUNTS=Set(0, ..., 10),
        INITIAL_BALANCE=100,
    )
