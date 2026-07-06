"""
Single-height Tendermint consensus protocol in Wunderspec.

The original Tendermint consensus appears in: https://arxiv.org/abs/1807.04938.

The first version was translated by Codex GPT 5.5 from TLA+ specification:

https://github.com/cometbft/cometbft/blob/main/spec/light-client/accountability/TendermintAcc_004_draft.tla

We made additional specification extensions:

 - Make `init` initialize the state without messages from the faulty replicas
 - Add `init_with_faults` to initialize the state with messages from the faulty replicas
 - Add `faulty_step` to model the messages by the faulty replicas
 - Add `correct_step` to model the behavior by the correct replicas only

As in the original specification, timeouts are in general omitted, except when
they might affect safety.

Igor Konnov, 2026
"""

from enum import Enum, auto

from wunderspec import (
    AllMaps,
    AllSubsets,
    AllTuples,
    And,
    BoolExpr,
    Exists,
    Expr,
    Field,
    Forall,
    Implies,
    Interval,
    Map,
    Param,
    Set,
    SetIf,
    StateVar,
    Tuple,
    Val,
    example,
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

Process = int
Round = int
Value = int

NIL_ROUND = -1
NIL_VALUE = -1


class Step(Enum):
    """Local process step in the one-height Tendermint protocol."""

    PROPOSE = auto()
    PREVOTE = auto()
    PRECOMMIT = auto()
    DECIDED = auto()


class VoteKind(Enum):
    """The two Tendermint vote-message kinds represented by VoteMsg."""

    PREVOTE = auto()
    PRECOMMIT = auto()


ACTION_INIT = "INIT"
ACTION_INSERT_PROPOSAL = "INSERT_PROPOSAL"
ACTION_UPON_PROPOSAL_IN_PROPOSE = "UPON_PROPOSAL_PROPOSE"
ACTION_UPON_PROPOSAL_IN_PROPOSE_AND_PREVOTE = "UPON_PROPOSAL_PROPOSE_AND_PREVOTE"
ACTION_UPON_QUORUM_OF_PREVOTES_ANY = "UPON_QUORUM_PREVOTES_ANY"
ACTION_UPON_PROPOSAL_IN_PREVOTE_OR_COMMIT_AND_PREVOTE = (
    "UPON_PROPOSAL_PREVOTE_OR_COMMIT_AND_PREVOTE"
)
ACTION_UPON_QUORUM_OF_PRECOMMITS_ANY = "UPON_QUORUM_PRECOMMITS_ANY"
ACTION_UPON_PROPOSAL_IN_PRECOMMIT_NO_DECISION = "UPON_PROPOSAL_PRECOMMIT_NO_DECISION"
ACTION_ON_TIMEOUT_PROPOSE = "ON_TIMEOUT_PROPOSE"
ACTION_ON_QUORUM_OF_NIL_PREVOTES = "ON_QUORUM_NIL_PREVOTES"
ACTION_ON_ROUND_CATCHUP = "ON_ROUND_CATCHUP"


@record
class ProposalMsg:
    """Proposal message with proposer, round, proposed value, and valid round."""

    src: Field[Process]
    round: Field[Round]
    proposal: Field[Value]
    valid_round: Field[Round]


@record
class VoteMsg:
    """Prevote or precommit message with sender, round, kind, and value id."""

    src: Field[Process]
    round: Field[Round]
    kind: Field[VoteKind]
    id: Field[Value]


def mk_proposal(src: Expr, rnd: Expr, proposal: Expr, valid_round: Expr) -> Expr:
    """Build a PROPOSAL message record."""
    return ProposalMsg(
        src=src, round=rnd, proposal=proposal, valid_round=valid_round
    )  # type: ignore[return-value]


def mk_prevote(src: Expr, rnd: Expr, msg_id: Expr) -> Expr:
    """Build a PREVOTE message record."""
    return VoteMsg(
        src=src, round=rnd, kind=Val(VoteKind.PREVOTE), id=msg_id
    )  # type: ignore[return-value]


def mk_precommit(src: Expr, rnd: Expr, msg_id: Expr) -> Expr:
    """Build a PRECOMMIT message record."""
    return VoteMsg(
        src=src, round=rnd, kind=Val(VoteKind.PRECOMMIT), id=msg_id
    )  # type: ignore[return-value]


@state
class TendermintAccState(MachineStateBase):
    """State variables for the single-height Tendermint accountability model."""

    Corr: Param[set[Process]]
    Faulty: Param[set[Process]]
    N: Param[int]
    T: Param[int]
    ValidValues: Param[set[Value]]
    InvalidValues: Param[set[Value]]
    MaxRound: Param[int]
    Proposer: Param[dict[Round, Process]]

    round: StateVar[dict[Process, Round]]
    step: StateVar[dict[Process, Step]]
    decision: StateVar[dict[Process, Value]]
    locked_value: StateVar[dict[Process, Value]]
    locked_round: StateVar[dict[Process, Round]]
    valid_value: StateVar[dict[Process, Value]]
    valid_round: StateVar[dict[Process, Round]]

    msgs_propose: StateVar[dict[Round, set[ProposalMsg]]]
    msgs_prevote: StateVar[dict[Round, set[VoteMsg]]]
    msgs_precommit: StateVar[dict[Round, set[VoteMsg]]]
    last_action: StateVar[str]


def all_procs(s: TendermintAccState) -> Expr:
    """The set of all processes, correct and faulty."""
    return s.Corr | s.Faulty


def rounds(s: TendermintAccState) -> Expr:
    """The set of protocol rounds available in this bounded model."""
    return Interval(Val(0), s.MaxRound)


def rounds_or_nil(s: TendermintAccState) -> Expr:
    """Protocol rounds plus the sentinel nil round."""
    return rounds(s) | Set(NIL_ROUND)


def values(s: TendermintAccState) -> Expr:
    """All values that may appear in messages, valid or invalid."""
    return s.ValidValues | s.InvalidValues


def values_or_nil(s: TendermintAccState) -> Expr:
    """All protocol values plus the sentinel nil value."""
    return values(s) | Set(NIL_VALUE)


def steps() -> Expr:
    """All process control states used by the one-height protocol."""
    return Set(Step.PROPOSE, Step.PREVOTE, Step.PRECOMMIT, Step.DECIDED)


def action_names() -> Expr:
    """Action labels stored in the last-action bookkeeping variable."""
    return Set(
        ACTION_INIT,
        ACTION_INSERT_PROPOSAL,
        ACTION_UPON_PROPOSAL_IN_PROPOSE,
        ACTION_UPON_PROPOSAL_IN_PROPOSE_AND_PREVOTE,
        ACTION_UPON_QUORUM_OF_PREVOTES_ANY,
        ACTION_UPON_PROPOSAL_IN_PREVOTE_OR_COMMIT_AND_PREVOTE,
        ACTION_UPON_QUORUM_OF_PRECOMMITS_ANY,
        ACTION_UPON_PROPOSAL_IN_PRECOMMIT_NO_DECISION,
        ACTION_ON_TIMEOUT_PROPOSE,
        ACTION_ON_QUORUM_OF_NIL_PREVOTES,
        ACTION_ON_ROUND_CATCHUP,
    )


def is_valid(s: TendermintAccState, v: Expr) -> Expr:
    """The protocol validity predicate for proposed values."""
    return s.ValidValues.contains(v)


def threshold1(s: TendermintAccState) -> Expr:
    """The T + 1 evidence threshold: at least one process is correct."""
    return s.T + 1


def threshold2(s: TendermintAccState) -> Expr:
    """The 2T + 1 quorum threshold used by Tendermint."""
    return Val(2) * s.T + 1


def senders(s: TendermintAccState, msgs: Expr) -> Expr:
    """The set of processes that sent at least one message in a message set."""
    return SetIf(Exists(m.src == p for m in msgs) for p in all_procs(s))


def all_faulty_proposals(s: TendermintAccState) -> Expr:
    """All proposal messages that faulty processes may inject."""
    return Set(
        mk_proposal(t[0], t[1], t[2], t[3])
        for t in AllTuples(s.Faulty, rounds(s), values(s), rounds_or_nil(s))
    )


def all_faulty_prevotes(s: TendermintAccState) -> Expr:
    """All prevote messages that faulty processes may inject."""
    return Set(
        mk_prevote(t[0], t[1], t[2]) for t in AllTuples(s.Faulty, rounds(s), values(s))
    )


def all_faulty_precommits(s: TendermintAccState) -> Expr:
    """All precommit messages that faulty processes may inject."""
    return Set(
        mk_precommit(t[0], t[1], t[2])
        for t in AllTuples(s.Faulty, rounds(s), values(s))
    )


@action(init=True)
def init(c: Context[TendermintAccState]):
    """
    Algorithm 1, lines 1-9, adapted to one height.
    This initializer starts with empty message logs for faulty processes.
    """
    s = c.state
    s.round = Map(Val(0) for _ in s.Corr)
    s.step = Map(Val(Step.PROPOSE) for _ in s.Corr)
    s.decision = Map(Val(NIL_VALUE) for _ in s.Corr)
    s.locked_value = Map(Val(NIL_VALUE) for _ in s.Corr)
    s.locked_round = Map(Val(NIL_ROUND) for _ in s.Corr)
    s.valid_value = Map(Val(NIL_VALUE) for _ in s.Corr)
    s.valid_round = Map(Val(NIL_ROUND) for _ in s.Corr)
    s.msgs_propose = Map(Set(ProposalMsg) for _ in rounds(s))
    s.msgs_prevote = Map(Set(VoteMsg) for _ in rounds(s))
    s.msgs_precommit = Map(Set(VoteMsg) for _ in rounds(s))
    s.last_action = Val(ACTION_INIT)


@action(init=True)
def init_with_faults(c: Context[TendermintAccState]):
    """
    Algorithm 1, lines 1-9, adapted to one height.
    Extension: the initial message logs may contain arbitrary faulty messages.
    """
    s = c.state
    with (
        c.one_of(AllSubsets(all_faulty_proposals(s)), "faulty_proposals") as fprop,
        c.one_of(AllSubsets(all_faulty_prevotes(s)), "faulty_prevotes") as fprev,
        c.one_of(AllSubsets(all_faulty_precommits(s)), "faulty_precommits") as fcommit,
    ):
        s.round = Map(Val(0) for _ in s.Corr)
        s.step = Map(Val(Step.PROPOSE) for _ in s.Corr)
        s.decision = Map(Val(NIL_VALUE) for _ in s.Corr)
        s.locked_value = Map(Val(NIL_VALUE) for _ in s.Corr)
        s.locked_round = Map(Val(NIL_ROUND) for _ in s.Corr)
        s.valid_value = Map(Val(NIL_VALUE) for _ in s.Corr)
        s.valid_round = Map(Val(NIL_ROUND) for _ in s.Corr)
        s.msgs_propose = Map(
            fprop.filter(lambda m: m.round == rnd) for rnd in rounds(s)
        )
        s.msgs_prevote = Map(
            fprev.filter(lambda m: m.round == rnd) for rnd in rounds(s)
        )
        s.msgs_precommit = Map(
            fcommit.filter(lambda m: m.round == rnd) for rnd in rounds(s)
        )
        s.last_action = Val(ACTION_INIT)


@action(inline=True)
def broadcast_proposal(
    c: Context[TendermintAccState],
    src: Expr,
    rnd: Expr,
    proposal: Expr,
    valid_round: Expr,
):
    """Add a proposal message to the messages broadcast for its round."""
    s = c.state
    s.msgs_propose[rnd] |= Set(mk_proposal(src, rnd, proposal, valid_round))


@action(inline=True)
def broadcast_prevote(
    c: Context[TendermintAccState], src: Expr, rnd: Expr, msg_id: Expr
):
    """Add a prevote message to the messages broadcast for its round."""
    s = c.state
    s.msgs_prevote[rnd] |= Set(mk_prevote(src, rnd, msg_id))


@action(inline=True)
def broadcast_precommit(
    c: Context[TendermintAccState], src: Expr, rnd: Expr, msg_id: Expr
):
    """Add a precommit message to the messages broadcast for its round."""
    s = c.state
    s.msgs_precommit[rnd] |= Set(mk_precommit(src, rnd, msg_id))


@action(inline=True)
def start_round(c: Context[TendermintAccState], p: Expr, rnd: Expr):
    """
    10: upon start, enter StartRound(0)
    11: StartRound(round)
    12: round_p <- round
    13: step_p <- propose
    """
    s = c.state
    c.assume(s.step[p] != Step.DECIDED)
    s.round[p] = rnd
    s.step[p] = Val(Step.PROPOSE)


@action(inline=False)
def insert_proposal(c: Context[TendermintAccState], p: Expr):
    """
    14: if proposer(h_p, round_p) = p then
    15:   if validValue_p != nil then
    16:     proposal <- validValue_p
    17:   else:
    18:     proposal <- getValue()
    19:   broadcast <PROPOSAL, h_p, round_p, proposal, validRound_p>
    20: else:
    21:   schedule OnTimeoutPropose(h_p, round_p) ...
    """
    s = c.state
    rnd = c.cache(s.round[p])
    c.assume(p == s.Proposer[rnd])
    c.assume(s.step[p] == Step.PROPOSE)
    c.assume(Forall(m.src != p for m in s.msgs_propose[rnd]))
    with c.one_of(s.ValidValues, "v") as v:
        proposal = s.valid_value[p].if_(s.valid_value[p] != NIL_VALUE).else_(v)
        broadcast_proposal(c, p, rnd, proposal, s.valid_round[p])
        s.last_action = Val(ACTION_INSERT_PROPOSAL)


@action(inline=False)
def upon_proposal_in_propose(c: Context[TendermintAccState], p: Expr):
    """
    22: upon proposal (v, -1) from proposer while step_p = propose
    23:   if valid(v) and (lockedRound_p = -1 or lockedValue_p = v) then
    24:     broadcast PREVOTE for v
    25:   else
    26:     broadcast PREVOTE for nil
    27:   step_p <- prevote
    """
    s = c.state
    rnd = c.cache(s.round[p])
    c.assume(s.step[p] == Step.PROPOSE)
    with c.one_of(values(s), "v") as v:
        msg = mk_proposal(s.Proposer[rnd], rnd, v, Val(NIL_ROUND))
        c.assume(s.msgs_propose[rnd].contains(msg))
        vote_id = v.if_(
            is_valid(s, v)
            & ((s.locked_round[p] == NIL_ROUND) | (s.locked_value[p] == v))
        ).else_(NIL_VALUE)
        broadcast_prevote(c, p, rnd, vote_id)
        s.step[p] = Val(Step.PREVOTE)
        s.last_action = Val(ACTION_UPON_PROPOSAL_IN_PROPOSE)


@action(inline=False)
def upon_proposal_in_propose_and_prevote(c: Context[TendermintAccState], p: Expr):
    """
    28: upon proposal (v, vr) from proposer and 2f+1 prevotes for v in vr
        while step_p = propose and 0 <= vr < round_p
    29:   if valid(v) and (lockedRound_p <= vr or lockedValue_p = v) then
    30:     broadcast PREVOTE for v
    31:   else
    32:     broadcast PREVOTE for nil
    33:   step_p <- prevote
    """
    s = c.state
    rnd = c.cache(s.round[p])
    c.assume(s.step[p] == Step.PROPOSE)
    with c.one_of(values(s), "v") as v, c.one_of(rounds(s), "vr") as vr:
        c.assume(Val(0) <= vr)
        c.assume(vr < rnd)
        msg = mk_proposal(s.Proposer[rnd], rnd, v, vr)
        prevotes = c.cache(s.msgs_prevote[vr].filter(lambda m: m.id == v))
        c.assume(s.msgs_propose[rnd].contains(msg))
        c.assume(prevotes.size >= threshold2(s))
        vote_id = v.if_(
            is_valid(s, v) & ((s.locked_round[p] <= vr) | (s.locked_value[p] == v))
        ).else_(NIL_VALUE)
        broadcast_prevote(c, p, rnd, vote_id)
        s.step[p] = Val(Step.PREVOTE)
        s.last_action = Val(ACTION_UPON_PROPOSAL_IN_PROPOSE_AND_PREVOTE)


@action(inline=False)
def upon_quorum_of_prevotes_any(c: Context[TendermintAccState], p: Expr):
    """
    34: upon 2f+1 current-round prevotes while step_p = prevote
    35:   schedule OnTimeoutPrevote(h_p, round_p)

    This safety model does not store timers. The transition represents the
    timeout firing and immediately takes the nil-precommit path.
    """
    s = c.state
    rnd = c.cache(s.round[p])
    c.assume(s.step[p] == Step.PREVOTE)
    with c.one_of(AllSubsets(s.msgs_prevote[rnd]), "my_evidence") as ev:
        c.assume(senders(s, ev).size >= threshold2(s))
        broadcast_precommit(c, p, rnd, Val(NIL_VALUE))
        s.step[p] = Val(Step.PRECOMMIT)
        s.last_action = Val(ACTION_UPON_QUORUM_OF_PREVOTES_ANY)


@action(inline=False)
def upon_proposal_in_prevote_or_commit_and_prevote(
    c: Context[TendermintAccState], p: Expr
):
    """
    36: upon proposal (v, *) and 2f+1 current-round prevotes for v
        while valid(v) and step_p >= prevote
    37:   if step_p = prevote then
    38:     lockedValue_p <- v
    39:     lockedRound_p <- round_p
    40:     broadcast PRECOMMIT for v
    41:     step_p <- precommit
    42:   validValue_p <- v
    43:   validRound_p <- round_p
    """
    s = c.state
    rnd = c.cache(s.round[p])
    c.assume((s.step[p] == Step.PREVOTE) | (s.step[p] == Step.PRECOMMIT))
    with c.one_of(s.ValidValues, "v") as v, c.one_of(rounds_or_nil(s), "vr") as vr:
        msg = mk_proposal(s.Proposer[rnd], rnd, v, vr)
        prevotes = c.cache(s.msgs_prevote[rnd].filter(lambda m: m.id == v))
        c.assume(s.msgs_propose[rnd].contains(msg))
        c.assume(prevotes.size >= threshold2(s))
        prevote_step, precommit_step = c.split(s.step[p] == Step.PREVOTE)
        with prevote_step:
            s.locked_value[p] = v
            s.locked_round[p] = rnd
            broadcast_precommit(c, p, rnd, v)
            s.step[p] = Val(Step.PRECOMMIT)
        with precommit_step:
            pass
        s.valid_value[p] = v
        s.valid_round[p] = rnd
        s.last_action = Val(ACTION_UPON_PROPOSAL_IN_PREVOTE_OR_COMMIT_AND_PREVOTE)


@action(inline=False)
def upon_quorum_of_precommits_any(c: Context[TendermintAccState], p: Expr):
    """
    47: upon 2f+1 current-round precommits
    48:   schedule OnTimeoutPrecommit(h_p, round_p)

    This safety model does not store timers. The transition represents the
    timeout firing and immediately advances to the next modeled round.
    """
    s = c.state
    rnd = c.cache(s.round[p])
    with c.one_of(AllSubsets(s.msgs_precommit[rnd]), "my_evidence") as ev:
        c.assume(senders(s, ev).size >= threshold2(s))
        c.assume(rounds(s).contains(rnd + 1))
        start_round(c, p, rnd + 1)
        s.last_action = Val(ACTION_UPON_QUORUM_OF_PRECOMMITS_ANY)


@action(inline=False)
def upon_proposal_in_precommit_no_decision(c: Context[TendermintAccState], p: Expr):
    """
    49: upon proposal (v, *) and 2f+1 precommits for v while undecided
    50:   if valid(v) then
    51:     decision_p[h_p] <- v
    52:     h_p <- h_p + 1
    53:     reset locks, valid value, valid round, and message log
    54:     StartRound(0)

    This one-height model records the decision and moves the process to DECIDED.
    """
    s = c.state
    c.assume(s.decision[p] == NIL_VALUE)
    with (
        c.one_of(s.ValidValues, "v") as v,
        c.one_of(rounds(s), "rnd") as rnd,
        c.one_of(rounds_or_nil(s), "vr") as vr,
    ):
        msg = mk_proposal(s.Proposer[rnd], rnd, v, vr)
        precommits = c.cache(s.msgs_precommit[rnd].filter(lambda m: m.id == v))
        c.assume(s.msgs_propose[rnd].contains(msg))
        c.assume(precommits.size >= threshold2(s))
        s.decision[p] = v
        s.step[p] = Val(Step.DECIDED)
        s.last_action = Val(ACTION_UPON_PROPOSAL_IN_PRECOMMIT_NO_DECISION)


@action(inline=False)
def on_timeout_propose(c: Context[TendermintAccState], p: Expr):
    """
    57: OnTimeoutPropose(height, round)
    58:   if height = h_p and round = round_p and step_p = propose then
    59:     broadcast PREVOTE for nil
    60:     step_p <- prevote
    """
    s = c.state
    rnd = c.cache(s.round[p])
    c.assume(s.step[p] == Step.PROPOSE)
    c.assume(p != s.Proposer[rnd])
    broadcast_prevote(c, p, rnd, Val(NIL_VALUE))
    s.step[p] = Val(Step.PREVOTE)
    s.last_action = Val(ACTION_ON_TIMEOUT_PROPOSE)


@action(inline=False)
def on_quorum_of_nil_prevotes(c: Context[TendermintAccState], p: Expr):
    """
    44: upon 2f+1 current-round PREVOTE nil messages while step_p = prevote
    45:   broadcast PRECOMMIT for nil
    46:   step_p <- precommit
    """
    s = c.state
    rnd = c.cache(s.round[p])
    c.assume(s.step[p] == Step.PREVOTE)
    prevotes = c.cache(s.msgs_prevote[rnd].filter(lambda m: m.id == NIL_VALUE))
    c.assume(prevotes.size >= threshold2(s))
    broadcast_precommit(c, p, rnd, Val(NIL_VALUE))
    s.step[p] = Val(Step.PRECOMMIT)
    s.last_action = Val(ACTION_ON_QUORUM_OF_NIL_PREVOTES)


@action(inline=False)
def on_round_catchup(c: Context[TendermintAccState], p: Expr):
    """
    55: upon f+1 messages from a higher round
    56:   StartRound(round)
    """
    s = c.state
    with (
        c.one_of(rounds(s), "rnd") as rnd,
        c.one_of(AllSubsets(s.msgs_propose[rnd]), "ev_propose") as ev_propose,
        c.one_of(AllSubsets(s.msgs_prevote[rnd]), "ev_prevote") as ev_prevote,
        c.one_of(AllSubsets(s.msgs_precommit[rnd]), "ev_precommit") as ev_precommit,
    ):
        c.assume(rnd > s.round[p])
        faster = (
            senders(s, ev_propose) | senders(s, ev_prevote) | senders(s, ev_precommit)
        )
        c.assume(faster.size >= threshold1(s))
        start_round(c, p, rnd)
        s.last_action = Val(ACTION_ON_ROUND_CATCHUP)


@action(inline=False)
def faulty_step(c: Context[TendermintAccState]):
    """Inject arbitrary proposal, prevote, and precommit messages by faulty replicas."""
    s = c.state
    with c.one_of(rounds(s), "r") as r:
        # some faulty replicas send v as their proposal in round r, with valid round being vr
        with (
            c.one_of(AllSubsets(s.Faulty), "fps1") as fps,
            c.one_of(values(s), "v1") as v,
            c.one_of(rounds_or_nil(s), "vr1") as vr,
        ):
            s.msgs_propose[r] |= Set(mk_proposal(fp, r, v, vr) for fp in fps)
        # some faulty replicas send v as their prevote in round r
        with (
            c.one_of(AllSubsets(s.Faulty), "fps2") as fps,
            c.one_of(values(s), "v2") as v,
        ):
            s.msgs_prevote[r] |= Set(mk_prevote(fp, r, v) for fp in fps)
        # some faulty replicas send v as their precommit in round r
        with (
            c.one_of(AllSubsets(s.Faulty), "fps3") as fps,
            c.one_of(values(s), "v3") as v,
        ):
            s.msgs_precommit[r] |= Set(mk_precommit(fp, r, v) for fp in fps)


@action
def correct_step(c: Context[TendermintAccState]):
    """Choose one correct process and execute one enabled protocol transition."""
    s = c.state
    with c.one_of(s.Corr, "p") as p:
        alts = iter(
            c.alternatives(
                "InsertProposal",
                "UponProposalInPropose",
                "UponProposalInProposeAndPrevote",
                "UponQuorumOfPrevotesAny",
                "UponProposalInPrevoteOrCommitAndPrevote",
                "UponQuorumOfPrecommitsAny",
                "UponProposalInPrecommitNoDecision",
                "OnTimeoutPropose",
                "OnQuorumOfNilPrevotes",
                "OnRoundCatchup",
            )
        )
        with next(alts):
            insert_proposal(c, p)
        with next(alts):
            upon_proposal_in_propose(c, p)
        with next(alts):
            upon_proposal_in_propose_and_prevote(c, p)
        with next(alts):
            upon_quorum_of_prevotes_any(c, p)
        with next(alts):
            upon_proposal_in_prevote_or_commit_and_prevote(c, p)
        with next(alts):
            upon_quorum_of_precommits_any(c, p)
        with next(alts):
            upon_proposal_in_precommit_no_decision(c, p)
        with next(alts):
            on_timeout_propose(c, p)
        with next(alts):
            on_quorum_of_nil_prevotes(c, p)
        with next(alts):
            on_round_catchup(c, p)


@action
def step(c: Context[TendermintAccState]):
    """A system transition: either faulty message injection or a correct step."""
    fstep, cstep = c.alternatives("faulty_step", "correct_step")
    with fstep:
        faulty_step(c)
    with cstep:
        correct_step(c)


def proposal_ok(s: TendermintAccState, m: Expr, rnd: Expr) -> Expr:
    """Type and round well-formedness predicate for proposal messages."""
    return And(
        all_procs(s).contains(m.src),
        m.round == rnd,
        values(s).contains(m.proposal),
        rounds_or_nil(s).contains(m.valid_round),
    )


def vote_ok(s: TendermintAccState, m: Expr, rnd: Expr, kind: VoteKind) -> Expr:
    """Type and round well-formedness predicate for vote messages."""
    return And(
        all_procs(s).contains(m.src),
        m.round == rnd,
        m.kind == kind,
        values_or_nil(s).contains(m.id),
    )


def agreement_expr(s: TendermintAccState) -> Expr:
    """Agreement: any two decided correct processes decide the same value."""
    return Forall(
        (s.decision[p] == NIL_VALUE)
        | (s.decision[q] == NIL_VALUE)
        | (s.decision[p] == s.decision[q])
        for p in s.Corr
        for q in s.Corr
    )


def equivocation_in(msgs: Expr, p: Expr) -> Expr:
    """Whether process p sent two distinct messages of the same round and kind."""
    return Exists(
        And(
            m1 != m2,
            m1.src == p,
            m2.src == p,
            m1.round == m2.round,
        )
        for m1 in msgs
        for m2 in msgs
    )


@invariant
def assumptions_hold(s: TendermintAccState) -> BoolExpr:
    """Model assumptions needed for the bounded accountability instance."""
    return And(
        s.N == all_procs(s).size,
        (s.Corr & s.Faulty).is_empty,
        s.Faulty.size <= s.T,
        s.N > Val(3) * s.T,
        s.MaxRound >= 0,
        ~values(s).contains(Val(NIL_VALUE)),
        ~rounds(s).contains(Val(NIL_ROUND)),
        Forall(all_procs(s).contains(s.Proposer[rnd]) for rnd in rounds(s)),
    )


@invariant
def type_ok(s: TendermintAccState) -> BoolExpr:
    """Type invariant for protocol state and evidence variables."""
    return And(
        AllMaps(s.Corr, rounds(s)).contains(s.round),
        AllMaps(s.Corr, steps()).contains(s.step),
        AllMaps(s.Corr, values_or_nil(s)).contains(s.decision),
        AllMaps(s.Corr, values_or_nil(s)).contains(s.locked_value),
        AllMaps(s.Corr, rounds_or_nil(s)).contains(s.locked_round),
        AllMaps(s.Corr, values_or_nil(s)).contains(s.valid_value),
        AllMaps(s.Corr, rounds_or_nil(s)).contains(s.valid_round),
        Forall(
            Forall(proposal_ok(s, m, rnd) for m in s.msgs_propose[rnd])
            for rnd in rounds(s)
        ),
        Forall(
            Forall(vote_ok(s, m, rnd, VoteKind.PREVOTE) for m in s.msgs_prevote[rnd])
            for rnd in rounds(s)
        ),
        Forall(
            Forall(
                vote_ok(s, m, rnd, VoteKind.PRECOMMIT) for m in s.msgs_precommit[rnd]
            )
            for rnd in rounds(s)
        ),
        action_names().contains(s.last_action),
    )


@invariant
def agreement(s: TendermintAccState) -> BoolExpr:
    """The Tendermint safety property: agreement among correct processes."""
    return agreement_expr(s)  # type: ignore


@invariant
def validity(s: TendermintAccState) -> BoolExpr:
    """Protocol validity: correct decisions are valid values or nil."""
    return Forall(
        (s.decision[p] == NIL_VALUE) | s.ValidValues.contains(s.decision[p])
        for p in s.Corr
    )


@example
def no_agreement(s: TendermintAccState) -> BoolExpr:
    """Example of violating agreement, e.g. for N=4,T=1,F=2."""
    return Forall(
        Implies(
            (p != q) & (s.decision[p] != NIL_VALUE) & (s.decision[q] != NIL_VALUE),
            s.decision[p] != s.decision[q],
        )
        for p in s.Corr
        for q in s.Corr
    )


@example
def never_undecided_in_max_round(s: TendermintAccState) -> BoolExpr:
    """Example where all correct processes reach MaxRound but not all decide."""
    return Implies(
        Forall(s.round[p] == s.MaxRound for p in s.Corr),
        Forall(s.decision[p] != NIL_VALUE for p in s.Corr),
    )


@example
def some_decided(s: TendermintAccState) -> BoolExpr:
    """Example of a state where at least one correct process decided."""
    return Exists(s.decision[p] != NIL_VALUE for p in s.Corr)


@example
def all_decided(s: TendermintAccState) -> BoolExpr:
    """Example of a state where all correct processes decided."""
    return Forall(s.decision[p] != NIL_VALUE for p in s.Corr)


@coverage
def state_cov(s: TendermintAccState) -> Expr:
    """Full coverage projection over protocol state and evidence."""
    return Tuple(
        s.round,
        s.step,
        s.decision,
        s.locked_value,
        s.locked_round,
        s.valid_value,
        s.valid_round,
        s.msgs_propose,
        s.msgs_prevote,
        s.msgs_precommit,
    )


@coverage
def min_cov(s: TendermintAccState) -> Expr:
    """Reduced coverage projection over per-process protocol state."""
    return Tuple(
        s.round,
        s.step,
        s.decision,
        s.locked_value,
        s.locked_round,
        s.valid_value,
        s.valid_round,
    )


def tendermint_instance(n: int, t: int, corr: Expr, faulty: Expr) -> TendermintAccState:
    """Build a small bounded instance with two valid and one invalid value."""
    return TendermintAccState(
        Corr=corr,
        Faulty=faulty,
        N=n,
        T=t,
        ValidValues=Set(0, 1),
        InvalidValues=Set(2),
        MaxRound=3,
        Proposer=Map(n for n in Set(0, ..., 3)),
    )


@instance
def n4_t1_f0() -> TendermintAccState:
    """Four-process instance with no faulty processes."""
    return tendermint_instance(4, 1, Set(0, ..., 3), Set(int))


@instance
def n4_t1_f1() -> TendermintAccState:
    """Four-process instance with one faulty process."""
    return tendermint_instance(4, 1, Set(0, 1, 2), Set(3))


@instance
def n4_t1_f2() -> TendermintAccState:
    """Four-process instance with two faulty processes."""
    return tendermint_instance(4, 1, Set(0, 1), Set(2, 3))
