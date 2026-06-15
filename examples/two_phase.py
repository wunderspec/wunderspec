"""
Two-phase commit protocol (typed) in Wunderspec.

Translated from this source via LLM (Codex GPT 5.3):
https://github.com/apalache-mc/apalache/blob/main/test/tla/TwoPhaseTyped.tla
"""

from enum import Enum, auto

from wunderspec import (
    And,
    BoolExpr,
    Exists,
    Expr,
    Forall,
    Map,
    Param,
    Set,
    SetExpr,
    StateVar,
    Tuple,
    Val,
    coverage,
    example,
)
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    instance,
    invariant,
    state,
)


class RMState(Enum):
    WORKING = auto()
    PREPARED = auto()
    COMMITTED = auto()
    ABORTED = auto()


class TMState(Enum):
    INIT = auto()
    COMMITTED = auto()
    ABORTED = auto()


class MsgType(Enum):
    COMMIT = auto()
    ABORT = auto()
    PREPARED = auto()


Message = tuple[MsgType, int]


def mk_commit() -> Expr:
    return Tuple(Val(MsgType.COMMIT), Val(0))


def mk_abort() -> Expr:
    return Tuple(Val(MsgType.ABORT), Val(0))


def mk_prepared(rm: Expr) -> Expr:
    return Tuple(Val(MsgType.PREPARED), rm)


@state
class TwoPhaseState(MachineStateBase):
    N: Param[int]
    rm_state: StateVar[dict[int, RMState]]
    tm_state: StateVar[TMState]
    tm_prepared: StateVar[set[int]]
    msgs: StateVar[set[Message]]


def rms(s: TwoPhaseState) -> SetExpr:
    return Set(Val(1), ..., s.N)


def message_space(s: TwoPhaseState) -> SetExpr:
    return Set(mk_prepared(rm) for rm in rms(s)) | Set(mk_abort(), mk_commit())


@action(inline=False)
def tm_rcv_prepared(c: Context[TwoPhaseState], rm: Expr):
    s = c.state
    c.assume(s.tm_state == TMState.INIT)
    c.assume(s.msgs.contains(mk_prepared(rm)))
    s.tm_prepared |= Set(rm)


@action(inline=False)
def tm_commit(c: Context[TwoPhaseState]):
    s = c.state
    c.assume(s.tm_state == TMState.INIT)
    c.assume(s.tm_prepared == rms(s))
    s.tm_state = Val(TMState.COMMITTED)
    s.msgs |= Set(mk_commit())


@action(inline=False)
def tm_abort(c: Context[TwoPhaseState]):
    s = c.state
    c.assume(s.tm_state == TMState.INIT)
    s.tm_state = Val(TMState.ABORTED)
    s.msgs |= Set(mk_abort())


@action(inline=False)
def rm_prepare(c: Context[TwoPhaseState], rm: Expr):
    s = c.state
    c.assume(s.rm_state[rm] == RMState.WORKING)
    s.rm_state[rm] = RMState.PREPARED
    s.msgs |= Set(mk_prepared(rm))


@action(inline=False)
def rm_choose_to_abort(c: Context[TwoPhaseState], rm: Expr):
    s = c.state
    c.assume(s.rm_state[rm] == RMState.WORKING)
    s.rm_state[rm] = RMState.ABORTED


@action(inline=False)
def rm_rcv_commit_msg(c: Context[TwoPhaseState], rm: Expr):
    s = c.state
    c.assume(s.msgs.contains(mk_commit()))
    s.rm_state[rm] = RMState.COMMITTED


@action(inline=False)
def rm_rcv_abort_msg(c: Context[TwoPhaseState], rm: Expr):
    s = c.state
    c.assume(s.msgs.contains(mk_abort()))
    s.rm_state[rm] = RMState.ABORTED


@action(init=True)
def init(c: Context[TwoPhaseState]):
    s = c.state
    s.rm_state = Map(Val(RMState.WORKING) for _ in rms(s))
    s.tm_state = Val(TMState.INIT)
    s.tm_prepared = Set(int)
    s.msgs = Set(Message)


@action
def step(c: Context[TwoPhaseState]):
    s = c.state
    alts = iter(c.alternatives("TMCommit", "TMAbort", "RMAction"))
    with next(alts):
        tm_commit(c)
    with next(alts):
        tm_abort(c)
    with next(alts), c.one_of(rms(s), "rm") as rm:
        rm_alts = iter(
            c.alternatives(
                "TMRcvPrepared",
                "RMPrepare",
                "RMChooseToAbort",
                "RMRcvCommitMsg",
                "RMRcvAbortMsg",
            )
        )
        with next(rm_alts):
            tm_rcv_prepared(c, rm)
        with next(rm_alts):
            rm_prepare(c, rm)
        with next(rm_alts):
            rm_choose_to_abort(c, rm)
        with next(rm_alts):
            rm_rcv_commit_msg(c, rm)
        with next(rm_alts):
            rm_rcv_abort_msg(c, rm)


@invariant
def tp_type_ok(s: TwoPhaseState) -> BoolExpr:
    rm_states = Set(
        RMState.WORKING, RMState.PREPARED, RMState.COMMITTED, RMState.ABORTED
    )
    tm_states = Set(TMState.INIT, TMState.COMMITTED, TMState.ABORTED)
    return And(
        Forall(rm_states.contains(s.rm_state[rm]) for rm in rms(s)),
        tm_states.contains(s.tm_state),
        s.tm_prepared <= rms(s),
        s.msgs <= message_space(s),
    )


@invariant
def tc_consistent(s: TwoPhaseState) -> BoolExpr:
    return Forall(
        ~And(
            s.rm_state[rm1] == RMState.ABORTED,
            s.rm_state[rm2] == RMState.COMMITTED,
        )
        for rm1 in rms(s)
        for rm2 in rms(s)
    )


# A few instances for testing and model checking


@instance
def n2() -> TwoPhaseState:
    return TwoPhaseState(N=2)


@instance
def n3() -> TwoPhaseState:
    return TwoPhaseState(N=3)


@instance
def n5() -> TwoPhaseState:
    return TwoPhaseState(N=5)


@instance
def n10() -> TwoPhaseState:
    return TwoPhaseState(N=10)


@coverage
def state_cov(s: TwoPhaseState) -> Expr:
    return Tuple(s.rm_state, s.tm_state, s.tm_prepared, s.msgs)


@example
def some_aborted(s: TwoPhaseState) -> BoolExpr:
    return Exists(s.rm_state[rm] == RMState.ABORTED for rm in rms(s))


@example
def all_aborted(s: TwoPhaseState) -> BoolExpr:
    return Forall(s.rm_state[rm] == RMState.ABORTED for rm in rms(s))


@example
def some_committed(s: TwoPhaseState) -> BoolExpr:
    return Exists(s.rm_state[rm] == RMState.COMMITTED for rm in rms(s))


@example
def all_committed(s: TwoPhaseState) -> BoolExpr:
    return Forall(s.rm_state[rm] == RMState.COMMITTED for rm in rms(s))
