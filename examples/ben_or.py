"""
Ben-Or 1983 Byzantine consensus protocol in Wunderspec.

Translation of:
https://github.com/konnov/apalache-examples/blob/main/ben-or83/Ben_or83.tla

The upstream TLA+ module also defines `InitWithFaults`; this translation keeps
the main `Init`/`Next` specification and the bounded instances used for model
checking.
"""

from enum import Enum, auto

from wunderspec import (
    AllMaps,
    AllRecords,
    AllSubsets,
    AllTuples,
    And,
    BoolExpr,
    Exists,
    Expr,
    Field,
    Forall,
    Map,
    Param,
    Set,
    SetIf,
    StateVar,
    Tuple,
    Val,
    record,
)
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    coverage,
    instance,
    invariant,
    state,
)

ReplicaId = int
Round = int
Value = int

VALUE_ZERO = 0
VALUE_ONE = 1
NO_DECISION = -1
NO_MSG_VALUE = -2


class Step(Enum):
    S1 = auto()
    S2 = auto()
    S3 = auto()


class Msg2Kind(Enum):
    D2 = auto()
    Q2 = auto()


@record
class Msg1:
    src: Field[ReplicaId]
    round: Field[Round]
    value: Field[Value]


@record
class Msg2:
    src: Field[ReplicaId]
    round: Field[Round]
    kind: Field[Msg2Kind]
    value: Field[Value]


def values() -> Expr:
    return Set(VALUE_ZERO, VALUE_ONE)


def steps() -> Expr:
    return Set(Step.S1, Step.S2, Step.S3)


def msg2_tags() -> Expr:
    return Set(Msg2Kind.D2, Msg2Kind.Q2)


def mk_m1(src: Expr, rnd: Expr, value: Expr) -> Expr:
    return Msg1(src=src, round=rnd, value=value)  # type: ignore[return-value]


def mk_d2(src: Expr, rnd: Expr, value: Expr) -> Expr:
    return Msg2(src=src, round=rnd, kind=Val(Msg2Kind.D2), value=value)  # type: ignore[return-value]


def mk_q2(src: Expr, rnd: Expr) -> Expr:
    return Msg2(
        src=src, round=rnd, kind=Val(Msg2Kind.Q2), value=Val(NO_MSG_VALUE)
    )  # type: ignore[return-value]


@state
class BenOrState(MachineStateBase):
    N: Param[int]
    T: Param[int]
    F: Param[int]
    CORRECT: Param[set[int]]
    FAULTY: Param[set[int]]
    ROUNDS: Param[set[int]]

    value: StateVar[dict[ReplicaId, Value]]
    decision: StateVar[dict[ReplicaId, Value]]
    round: StateVar[dict[ReplicaId, Round]]
    step: StateVar[dict[ReplicaId, Step]]
    msgs1: StateVar[dict[Round, set[Msg1]]]
    msgs2: StateVar[dict[Round, set[Msg2]]]
    # special variable to prioritize the search
    ghost_trigger: StateVar[bool]


def all_replicas(s: BenOrState) -> Expr:
    return s.CORRECT | s.FAULTY


def senders1(s: BenOrState, m1s: Expr) -> Expr:
    return SetIf(Exists(m.src == rid for m in m1s) for rid in all_replicas(s))


def senders2(s: BenOrState, m2s: Expr) -> Expr:
    return SetIf(Exists(m.src == rid for m in m2s) for rid in all_replicas(s))


def all_faulty_m1(s: BenOrState, rnd: Expr) -> Expr:
    return Set(mk_m1(t[0], rnd, t[1]) for t in AllTuples(s.FAULTY, values()))


def all_faulty_d2(s: BenOrState, rnd: Expr) -> Expr:
    return Set(mk_d2(t[0], rnd, t[1]) for t in AllTuples(s.FAULTY, values()))


def all_faulty_q2(s: BenOrState, rnd: Expr) -> Expr:
    return Set(mk_q2(src, rnd) for src in s.FAULTY)


@action(init=True)
def init(c: Context[BenOrState]):
    s = c.state
    with c.one_of(AllMaps(s.CORRECT, values()), "init_value") as init_value:
        s.value = init_value
    s.decision = Map(Val(NO_DECISION) for _ in s.CORRECT)
    s.round = Map(Val(1) for _ in s.CORRECT)
    s.step = Map(Val(Step.S1) for _ in s.CORRECT)
    s.msgs1 = Map(Set(Msg1) for _ in s.ROUNDS)
    s.msgs2 = Map(Set(Msg2) for _ in s.ROUNDS)
    s.ghost_trigger = Val(False)


@action(init=True)
def init_with_faults(c: Context[BenOrState]):
    s = c.state
    with (
        c.one_of(AllMaps(s.CORRECT, values()), "init_value") as init_value,
        c.one_of(
            AllSubsets(AllRecords(src=s.FAULTY, round=s.ROUNDS, value=values())), "f1"
        ) as f1,
        c.one_of(
            AllSubsets(AllRecords(src=s.FAULTY, round=s.ROUNDS, value=values())), "f2d"
        ) as f2d,
        c.one_of(AllSubsets(AllRecords(src=s.FAULTY, round=s.ROUNDS)), "f2q") as f2q,
    ):
        s.value = init_value
        s.decision = Map(Val(NO_DECISION) for _ in s.CORRECT)
        s.round = Map(Val(1) for _ in s.CORRECT)
        s.step = Map(Val(Step.S1) for _ in s.CORRECT)
        s.msgs1 = s.ROUNDS.map_to(lambda rnd: f1.filter(lambda m: m.round == rnd))
        s.msgs2 = s.ROUNDS.map_to(
            lambda rnd: f2d.filter(lambda m: m.round == rnd).map(
                lambda msg: mk_d2(msg.src, rnd, msg.value)
            )
            | f2q.filter(lambda m: m.round == rnd).map(lambda msg: mk_q2(msg.src, rnd))
        )
        s.ghost_trigger = Val(False)


@action(inline=False)
def step1(c: Context[BenOrState], rid: Expr):
    s = c.state
    rnd = c.cache(s.round[rid])
    c.assume(s.step[rid] == Step.S1)
    # "send the message (1, r, x_P) to all the processes"
    s.msgs1[rnd] |= Set(mk_m1(rid, rnd, s.value[rid]))
    s.step[rid] = Step.S2
    s.ghost_trigger = Val(False)


@action(inline=False)
def step2(c: Context[BenOrState], rid: Expr):
    s = c.state
    rnd = c.cache(s.round[rid])
    c.assume(s.step[rid] == Step.S2)
    with c.one_of(AllSubsets(s.msgs1[rnd]), "received") as received:
        # "wait till messages of type (1, r, *) are received from N - T processes"
        c.assume(senders1(s, received).size >= s.N - s.T)
        alts = iter(c.alternatives("SendD2", "SendQ2"))

        def weight(v):
            return senders1(s, SetIf(m.value == v for m in received)).size

        with next(alts), c.one_of(values(), "v") as v:
            # "if more than (N + T)/2 messages have the same value v..."
            c.assume(Val(2) * weight(v) > s.N + s.T)
            # "...then send the message (2, r, v, D) to all processes"
            s.msgs2[rnd] |= Set(mk_d2(rid, rnd, v))
            s.step[rid] = Step.S3
            s.ghost_trigger = Val(True)
        with next(alts):
            c.assume(Forall(Val(2) * weight(v) <= s.N + s.T for v in values()))
            # "Else send the message (2, r, ?) to all processes"
            s.msgs2[rnd] |= Set(mk_q2(rid, rnd))
            s.step[rid] = Step.S3
            s.ghost_trigger = Val(True)


@action(inline=False)
def step3(c: Context[BenOrState], rid: Expr):
    s = c.state
    rnd = c.cache(s.round[rid])
    c.assume(s.step[rid] == Step.S3)
    with c.one_of(AllSubsets(s.msgs2[rnd]), "received") as received:
        # "Wait till messages of type (2, r, *) arrive from N - T processes"
        c.assume(senders2(s, received).size == s.N - s.T)
        # the condition below is to bound the number of rounds for model checking
        c.assume(s.ROUNDS.contains(rnd + 1))

        def weight(v):
            return senders2(
                s,
                SetIf((m.kind == Msg2Kind.D2) & (m.value == v) for m in received),
            ).size

        alts = iter(c.alternatives("AdoptOrDecide", "Randomize"))
        with next(alts), c.one_of(values(), "v") as v:
            # "(a) If there are at least T+1 D-messages (2, r, v, D),
            # then set x_P to v"
            weight_of_v = c.cache(weight(v))
            c.assume(weight_of_v >= s.T + 1)
            s.value[rid] = v
            # "(b) If there are more than (N + T)/2 D-messages..."
            decide_split, keep_split = c.split(Val(2) * weight_of_v > s.N + s.T)
            with decide_split:
                # "...then decide v"
                s.decision[rid] = v
            with keep_split:
                pass

        with next(alts), c.one_of(values(), "next_v") as next_v:
            # "(c) Else set x_P = 1 or 0 each with probability 1/2."
            # We replace probabilites with non-determinism.
            c.assume(Forall(weight(v) < s.T + 1 for v in values()))
            s.value[rid] = next_v

    # "Set r := r + 1 and go to step 1"
    s.round[rid] = rnd + 1
    s.step[rid] = Step.S1
    s.ghost_trigger = Val(True)


@action(inline=False)
def faulty_step(c: Context[BenOrState]):
    s = c.state
    with (
        c.one_of(s.ROUNDS, "r") as rnd,
        c.one_of(AllSubsets(all_faulty_m1(s, rnd)), "f1") as f1,
        c.one_of(AllSubsets(all_faulty_d2(s, rnd)), "f2d") as f2d,
        c.one_of(AllSubsets(all_faulty_q2(s, rnd)), "f2q") as f2q,
    ):
        s.msgs1[rnd] |= f1
        s.msgs2[rnd] |= f2d | f2q
        s.ghost_trigger = Val(True)


@action
def step(c: Context[BenOrState]):
    s = c.state
    top_alts = iter(c.alternatives("CorrectStep", "FaultyStep"))

    with next(top_alts), c.one_of(s.CORRECT, "id") as rid:
        correct_alts = iter(c.alternatives("Step1", "Step2", "Step3"))
        with next(correct_alts):
            step1(c, rid)
        with next(correct_alts):
            step2(c, rid)
        with next(correct_alts):
            step3(c, rid)

    with next(top_alts):
        faulty_step(c)


@invariant
def assumptions_hold(s: BenOrState) -> BoolExpr:
    return And(
        s.N > Val(5) * s.T,
        s.CORRECT.size == s.N - s.F,
        s.FAULTY.size == s.F,
        s.ROUNDS.contains(1),
        ~values().contains(Val(NO_DECISION)),
    )


@invariant
def type_ok(s: BenOrState) -> BoolExpr:
    return And(
        AllMaps(s.CORRECT, values()).contains(s.value),
        AllMaps(s.CORRECT, values() | Set(NO_DECISION)).contains(s.decision),
        AllMaps(s.CORRECT, s.ROUNDS).contains(s.round),
        AllMaps(s.CORRECT, steps()).contains(s.step),
        Forall(
            Forall(
                And(
                    all_replicas(s).contains(m.src),
                    m.round == rnd,
                    values().contains(m.value),
                )
                for m in s.msgs1[rnd]
            )
            for rnd in s.ROUNDS
        ),
        Forall(
            Forall(
                And(
                    all_replicas(s).contains(m.src),
                    m.round == rnd,
                    msg2_tags().contains(m.kind),
                    ((m.kind == Msg2Kind.D2) & values().contains(m.value))
                    | ((m.kind == Msg2Kind.Q2) & (m.value == Val(NO_MSG_VALUE))),
                )
                for m in s.msgs2[rnd]
            )
            for rnd in s.ROUNDS
        ),
    )


@invariant
def agreement_inv(s: BenOrState) -> BoolExpr:
    return Forall(
        (s.decision[id1] == Val(NO_DECISION))
        | (s.decision[id2] == Val(NO_DECISION))
        | (s.decision[id1] == s.decision[id2])
        for id1 in s.CORRECT
        for id2 in s.CORRECT
    )


@coverage
def state_cov(s: BenOrState) -> Expr:
    return Tuple(s.value, s.decision, s.round, s.step, s.msgs1, s.msgs2)


@coverage
def min_cov(s: BenOrState) -> Expr:
    return Tuple(s.value, s.decision, s.round, s.step)


@instance
def n6t1f0() -> BenOrState:
    return BenOrState(
        N=6,
        T=1,
        F=0,
        CORRECT=Set(0, 1, 2, 3, 4, 5),
        FAULTY=Set(int),
        ROUNDS=Set(Val(1), ..., Val(3)),
    )


@instance
def n6t1f1() -> BenOrState:
    return BenOrState(
        N=6,
        T=1,
        F=1,
        CORRECT=Set(0, 1, 2, 3, 4),
        FAULTY=Set(5),
        ROUNDS=Set(Val(1), ..., Val(3)),
    )


@instance
def n6t1f2() -> BenOrState:
    return BenOrState(
        N=6,
        T=1,
        F=2,
        CORRECT=Set(0, 1, 2, 3),
        FAULTY=Set(4, 5),
        ROUNDS=Set(Val(1), ..., Val(3)),
    )
