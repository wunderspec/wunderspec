"""
Flexible Paxos (FPaxos) in Wunderspec.

Translation of FPaxos.tla by Codex GPT 5.3:

https://github.com/fpaxos/fpaxos-tlaplus/tree/main
"""

from wunderspec import *
from wunderspec import Field, Param, StateVar, expr
from wunderspec.machine import Context, instance, invariant, state


@record
class Msg1A:
    bal: Field[int]


@record
class Msg1B:
    acc: Field[str]
    bal: Field[int]
    mbal: Field[int]
    mval: Field[int]


@record
class Msg2A:
    bal: Field[int]
    val: Field[int]


@record
class Msg2B:
    acc: Field[str]
    bal: Field[int]
    val: Field[int]


def mk_1a(bal: Expr) -> Expr:
    return Msg1A(bal=bal)  # type: ignore


def mk_1b(acc: Expr, bal: Expr, mbal: Expr, mval: Expr) -> Expr:
    return Msg1B(acc=acc, bal=bal, mbal=mbal, mval=mval)  # type: ignore


def mk_2a(bal: Expr, val: Expr) -> Expr:
    return Msg2A(bal=bal, val=val)  # type: ignore


def mk_2b(acc: Expr, bal: Expr, val: Expr) -> Expr:
    return Msg2B(acc=acc, bal=bal, val=val)  # type: ignore


@state
class FPaxosState(MachineStateBase):
    # TLA+ constants
    Value: Param[set[int]]
    Acceptor: Param[set[str]]
    Quorum1: Param[set[set[str]]]
    Quorum2: Param[set[set[str]]]
    Ballot: Param[set[int]]

    # TLA+ variables
    max_bal: StateVar[dict[str, int]]
    max_vbal: StateVar[dict[str, int]]
    max_val: StateVar[dict[str, int]]
    msg_1a: StateVar[set[Msg1A]]
    msg_1b: StateVar[set[Msg1B]]
    msg_2a: StateVar[set[Msg2A]]
    msg_2b: StateVar[set[Msg2B]]


def ballot_or_minus_one(s: FPaxosState) -> Expr:
    return s.Ballot | Set(-1)


@invariant
def quorum_assumption(s: FPaxosState):
    return And(
        Forall(q <= s.Acceptor for q in s.Quorum1),
        Forall(q <= s.Acceptor for q in s.Quorum2),
        Forall(~(q1 & q2).is_empty for q1 in s.Quorum1 for q2 in s.Quorum2),
    )


@action(inline=False)
def phase1a(c: Context[FPaxosState], b: Expr):
    s = c.state
    s.msg_1a = s.msg_1a | Set(mk_1a(b))


@action(inline=False)
def phase1b(c: Context[FPaxosState], a: Expr):
    s = c.state
    with c.one_of(s.msg_1a, "m") as m:
        c.assume(m.bal > s.max_bal[a])
        s.max_bal = s.max_bal.replace(a, m.bal)
        s.msg_1b = s.msg_1b | Set(mk_1b(a, m.bal, s.max_vbal[a], s.max_val[a]))


@action(inline=False)
def phase2a(c: Context[FPaxosState], b: Expr, v: Expr):
    s = c.state
    c.assume(~Exists(m.bal == b for m in s.msg_2a))  # type: ignore

    with c.one_of(s.Quorum1, "q") as q:
        q1b = c.cache(
            s.msg_1b.filter(lambda m: q.contains(m.acc) & (m.bal == b)), "q1b"
        )
        q1bv = c.cache(q1b.filter(lambda m: m.mbal >= Val(0)), "q1bv")

        c.assume(Forall(Exists(m.acc == a for m in q1b) for a in q))  # type: ignore
        c.assume(
            Or(
                q1bv.is_empty,
                Exists(
                    (m.mval == v) & Forall(m.mbal >= mm.mbal for mm in q1bv)
                    for m in q1bv
                ),
            )  # type: ignore
        )

    s.msg_2a = s.msg_2a | Set(mk_2a(b, v))


@action(inline=False)
def phase2b(c: Context[FPaxosState], a: Expr):
    s = c.state
    with c.one_of(s.msg_2a, "m") as m:
        c.assume(m.bal >= s.max_bal[a])
        s.max_bal = s.max_bal.replace(a, m.bal)
        s.max_vbal = s.max_vbal.replace(a, m.bal)
        s.max_val = s.max_val.replace(a, m.val)
        s.msg_2b = s.msg_2b | Set(mk_2b(a, m.bal, m.val))


@action(init=True)
def init(c: Context[FPaxosState]):
    s = c.state
    s.max_bal = s.Acceptor.map_to(lambda _: Val(-1))
    s.max_vbal = s.Acceptor.map_to(lambda _: Val(-1))
    s.max_val = s.Acceptor.map_to(lambda _: Val(-1))
    s.msg_1a = Set(Msg1A)
    s.msg_1b = Set(Msg1B)
    s.msg_2a = Set(Msg2A)
    s.msg_2b = Set(Msg2B)


@action
def step(c: Context[FPaxosState]):
    s = c.state
    top_alts = iter(c.alternatives("ByBallot", "ByAcceptor"))

    with next(top_alts), c.one_of(s.Ballot, "b") as b:
        ballot_alts = iter(c.alternatives("Phase1a", "Phase2a"))
        with next(ballot_alts):
            phase1a(c, b)
        with next(ballot_alts), c.one_of(s.Value, "v") as v:
            phase2a(c, b, v)

    with next(top_alts), c.one_of(s.Acceptor, "a") as a:
        acc_alts = iter(c.alternatives("Phase1b", "Phase2b"))
        with next(acc_alts):
            phase1b(c, a)
        with next(acc_alts):
            phase2b(c, a)


@invariant
def type_ok(s: FPaxosState):
    b1 = ballot_or_minus_one(s)
    return And(
        Forall(b1.contains(s.max_bal[a]) for a in s.Acceptor),
        Forall(b1.contains(s.max_vbal[a]) for a in s.Acceptor),
        Forall((s.Value | Set(-1)).contains(s.max_val[a]) for a in s.Acceptor),
        Forall(s.Ballot.contains(m.bal) for m in s.msg_1a),
        Forall(
            And(
                s.Acceptor.contains(m.acc),
                s.Ballot.contains(m.bal),
                b1.contains(m.mbal),
                (s.Value | Set(-1)).contains(m.mval),
            )
            for m in s.msg_1b
        ),
        Forall(s.Ballot.contains(m.bal) & s.Value.contains(m.val) for m in s.msg_2a),
        Forall(
            And(
                s.Acceptor.contains(m.acc),
                s.Ballot.contains(m.bal),
                s.Value.contains(m.val),
            )
            for m in s.msg_2b
        ),
    )


@expr
def sent_2b(s: FPaxosState, a: Expr, v: Expr, b: Expr):
    return Exists((m.acc == a) & (m.val == v) & (m.bal == b) for m in s.msg_2b)  # type: ignore


@expr
def sent_2a(s: FPaxosState, v: Expr, b: Expr):
    return Exists((m.val == v) & (m.bal == b) for m in s.msg_2a)


@expr
def agreed(s: FPaxosState, v: Expr, b: Expr):
    return Exists(Forall(sent_2b(s, a, v, b) for a in q) for q in s.Quorum2)


@expr
def decided(s: FPaxosState, v: Expr):
    return Forall(agreed(s, v, b) for b in s.Ballot)


@expr
def no_future_proposal(s: FPaxosState, v: Expr, b: Expr):
    return Forall(
        ((b2 > b) & sent_2a(s, v2, b2)).implies(v == v2)
        for v2 in s.Value
        for b2 in s.Ballot
    )


@invariant
def safe_value(s: FPaxosState):
    return Forall(
        agreed(s, v, b).implies(no_future_proposal(s, v, b))
        for v in s.Value
        for b in s.Ballot
    )


@invariant
def safety(s: FPaxosState):
    return s.Value.filter(lambda v: decided(s, v)).size <= Val(1)


@invariant
def one_value_agreed_per_ballot(s: FPaxosState):
    return Forall(
        s.Value.filter(lambda v: agreed(s, v, b)).size <= Val(1) for b in s.Ballot
    )


@invariant
def one_vote_per_acceptor_per_ballot(s: FPaxosState):
    return Forall(
        s.Value.filter(lambda v: sent_2b(s, a, v, b)).size <= Val(1)
        for a in s.Acceptor
        for b in s.Ballot
    )


@invariant
def safe_states(s: FPaxosState):
    return Forall(
        And(
            Or(
                s.max_bal[a] == Val(-1),
                Exists(m.bal == s.max_bal[a] for m in s.msg_1a),
                Exists(m.bal == s.max_bal[a] for m in s.msg_2a),
            ),  # type: ignore
            s.max_bal[a] >= s.max_vbal[a],
            Forall(
                (m.acc == a).implies(
                    (m.bal <= s.max_bal[a]) & (m.mbal <= s.max_vbal[a])
                )
                for m in s.msg_1b
            ),
            Forall(
                (m.acc == a).implies((m.bal <= s.max_bal[a]) & (m.bal <= s.max_vbal[a]))
                for m in s.msg_2b
            ),
            Or(
                (s.max_vbal[a] == Val(-1)) & (s.max_val[a] == Val(-1)),  # type: ignore
                Exists(
                    (m.bal == s.max_vbal[a]) & (m.val == s.max_val[a]) for m in s.msg_2a
                ),
            ),  # type: ignore
        )
        for a in s.Acceptor
    )


@invariant
def safe_1b(s: FPaxosState):
    return Forall(
        And(
            Exists(
                (m.acc == a)
                & Or(
                    And(
                        m.mbal == Val(-1),
                        m.mval == Val(-1),
                        ~Exists((m2.acc == a) & (m2.bal < m.bal) for m2 in s.msg_2b),
                        Forall(
                            ((m2.acc == a) & (m2.bal < m.bal)).implies(
                                (m2.mbal == Val(-1)) & (m2.mval == Val(-1))
                            )
                            for m2 in s.msg_1b
                        ),
                    ),
                    Exists((m.mbal == m2.bal) & (m.mval == m2.val) for m2 in s.msg_2a),
                )
                for a in s.Acceptor
            ),
            m.bal > m.mbal,
            Forall((m2.mbal == m.mbal).implies(m2.mval == m.mval) for m2 in s.msg_1b),
        )
        for m in s.msg_1b
    )


@invariant
def safe_2a(s: FPaxosState):
    return Forall(
        And(
            Exists(
                Exists(
                    Exists(
                        And(
                            Exists(
                                And(
                                    m_max.acc == a_max,
                                    m_max.bal == m.bal,
                                    m_max.mbal == bal_max,
                                    Or(
                                        bal_max == Val(-1),
                                        m_max.mval == m.val,
                                    ),
                                )
                                for m_max in s.msg_1b
                            ),
                            Forall(
                                Exists(
                                    And(
                                        m2.acc == a2,
                                        m2.bal == m.bal,
                                        bal_max >= m2.mbal,
                                    )
                                    for m2 in s.msg_1b
                                )
                                for a2 in q
                            ),
                        )
                        for bal_max in ballot_or_minus_one(s)
                    )
                    for a_max in q
                )
                for q in s.Quorum1
            ),
            Forall((m2.bal == m.bal).implies(m2.val == m.val) for m2 in s.msg_2a),
        )
        for m in s.msg_2a
    )


@invariant
def safe_2b(s: FPaxosState):
    return Forall(
        And(
            Exists(
                (m.acc == a)
                & Or(
                    And(
                        s.max_bal[a] >= m.bal,
                        s.max_vbal[a] == m.bal,
                        s.max_val[a] == m.val,
                    ),
                    m.bal < s.max_bal[a],
                )
                for a in s.Acceptor
            ),
            Exists((m2.bal == m.bal) & (m2.val == m.val) for m2 in s.msg_2a),
        )
        for m in s.msg_2b
    )


@invariant
def inv(s: FPaxosState):
    return And(
        quorum_assumption(s),
        type_ok(s),
        safe_states(s),
        safe_1b(s),
        safe_2a(s),
        safe_2b(s),
        safe_value(s),
        safety(s),
    )


## a few instances for testing


@instance
def two_acceptors() -> FPaxosState:
    return FPaxosState(
        Value=Set(0, 1, 2),
        Acceptor=Set("a1", "a2"),
        Quorum1=Set(Set("a1"), Set("a2")),
        Quorum2=Set(Set("a1", "a2")),
        Ballot=Set(0, 1, 2),
    )


@instance
def two_acceptors_buggy() -> FPaxosState:
    return FPaxosState(
        Value=Set(0, 1, 2),
        Acceptor=Set("a1", "a2"),
        Quorum1=Set(Set("a1"), Set("a2")),
        Quorum2=Set(Set("a1"), Set("a2")),
        Ballot=Set(0, 1, 2),
    )


@instance
def three_acceptors() -> FPaxosState:
    return FPaxosState(
        Value=Set(0, 1, 2),
        Acceptor=Set("a1", "a2", "a3"),
        Quorum1=Set(Set("a1", "a2"), Set("a1", "a3"), Set("a2", "a3")),
        Quorum2=Set(Set("a1", "a2"), Set("a1", "a3"), Set("a2", "a3")),
        Ballot=Set(0, 1, 2),
    )


@instance
def four_acceptors() -> FPaxosState:
    return FPaxosState(
        Value=Set(0, 1, 2),
        Acceptor=Set("a1", "a2", "a3", "a4"),
        Quorum1=Set(Set("a1", "a2"), Set("a3", "a4")),
        Quorum2=Set(Set("a1", "a3"), Set("a2", "a4")),
        Ballot=Set(0, 1, 2),
    )


@instance
def four_acceptors_large() -> FPaxosState:
    return FPaxosState(
        Value=Set(0, ..., 10),
        Acceptor=Set("a1", "a2", "a3", "a4"),
        Quorum1=Set(Set("a1", "a2"), Set("a3", "a4")),
        Quorum2=Set(Set("a1", "a3"), Set("a2", "a4")),
        Ballot=Set(0, ..., 10),
    )


# a coverage function


@coverage
def cov2(s: FPaxosState) -> Expr:
    return Tuple(s.max_bal, s.max_vbal, s.max_val)


@coverage
def state_cov(s: FPaxosState) -> Expr:
    return Tuple(
        s.max_bal, s.max_vbal, s.max_val, s.msg_1a, s.msg_1b, s.msg_2a, s.msg_2b
    )
