"""
Inductive invariant for the single-height Tendermint consensus.

V2: Fixed and extended by Igor Konnov, 2026.
V1: Translated from the TLA+ invariant module by Codex GPT 5.5:

https://github.com/cometbft/cometbft/blob/main/spec/light-client/accountability/TendermintAccInv_004_draft.tla

The original algorithm is written as pseudo-code in:

Ethan Buchman, Jae Kwon, Zarko Milosevic. The latest gossip on BFT consensus, 2019.
URL: https://arxiv.org/abs/1807.04938

The protocol itself lives in ``tendermint_single.py``.  This module intentionally
reuses that state machine and exports the combined spec surface via
``__wunderspec_all__``.
"""

import tendermint_single as _base
from tendermint_single import (
    NIL_ROUND,
    NIL_VALUE,
    ProposalMsg,
    Step,
    TendermintAccState,
    VoteKind,
    VoteMsg,
    action_names,
    all_procs,
    rounds,
    rounds_or_nil,
    senders,
    steps,
    threshold1,
    threshold2,
    values_or_nil,
)

from wunderspec import (
    AllMaps,
    AllSubsets,
    AllTuples,
    And,
    BoolExpr,
    Exists,
    Expr,
    Forall,
    Implies,
    Max,
    Or,
    Set,
    SetIf,
    Val,
    action,
)
from wunderspec.machine import Context, invariant

init = _base.init
init_with_faults = _base.init_with_faults
correct_step = _base.correct_step
faulty_step = _base.faulty_step
step = _base.step
agreement = _base.agreement
validity = _base.validity
n4_t1_f0 = _base.n4_t1_f0
n4_t1_f1 = _base.n4_t1_f1
n4_t1_f2 = _base.n4_t1_f2

__wunderspec_all__ = [
    "TendermintAccState",
    "init",
    "init_with_faults",
    "correct_step",
    "faulty_step",
    "step",
    "agreement",
    "validity",
    "n4_t1_f0",
    "n4_t1_f1",
    "n4_t1_f2",
    "ind_type_ok",
    "ind_inv",
    "ind_init",
    "typed_ind_inv",
    "min_cov",
    "all_no_future_messages_sent",
    "all_if_in_prevote_then_sent_prevote",
    "all_if_in_precommit_then_sent_precommit",
    "all_if_in_decided_then_received_proposal",
    "all_if_in_decided_then_received_two_thirds",
    "all_if_in_decided_then_valid_decision",
    "all_locked_round_iff_locked_value",
    "all_valid_round_iff_valid_value",
    "all_valid_and_locked_round_bounded",
    "all_if_valid_round_then_two_thirds_prevotes",
    "all_if_locked_round_then_sent_commit",
    "all_latest_precommit_has_locked_round",
    "all_if_sent_prevote_then_received_proposal_or_two_thirds",
    "if_sent_precommit_then_sent_prevote",
    "if_sent_precommit_then_received_two_thirds",
    "all_no_equivocation_by_correct",
    "precommits_lock_value",
    "precommit_locks_later_prevotes",
    "all_locked_proposer_reproposes",
    "all_past_start_round",
    "all_rounds_below_have_precommit_quorum",
    "all_valid_in_current_round_precommitted",
    "all_locked_round_below_valid_round",
    "all_if_valid_round_then_precommitted",
    "all_correct_proposal_valid_round_below_round",
]


def correct_senders(s: TendermintAccState, msgs: Expr) -> Expr:
    return senders(s, msgs) & s.Corr


def all_proposals(s: TendermintAccState) -> Expr:
    return Set(
        ProposalMsg(
            src=t[0],
            round=t[1],
            proposal=t[2],
            valid_round=t[3],
        )
        for t in AllTuples(all_procs(s), rounds(s), values_or_nil(s), rounds_or_nil(s))
    )


def all_prevotes(s: TendermintAccState) -> Expr:
    return Set(
        VoteMsg(
            src=t[0],
            round=t[1],
            kind=Val(VoteKind.PREVOTE),
            id=t[2],
        )
        for t in AllTuples(all_procs(s), rounds(s), values_or_nil(s))
    )


def all_precommits(s: TendermintAccState) -> Expr:
    return Set(
        VoteMsg(
            src=t[0],
            round=t[1],
            kind=Val(VoteKind.PRECOMMIT),
            id=t[2],
        )
        for t in AllTuples(all_procs(s), rounds(s), values_or_nil(s))
    )


def benign_rounds_in_messages(msgs: Expr) -> BoolExpr:
    return Forall(Forall(m.round == r for m in msgs[r]) for r in msgs.keys)


def ind_proposal_ok(s: TendermintAccState, m: Expr) -> Expr:
    return And(
        all_procs(s).contains(m.src),
        rounds(s).contains(m.round),
        values_or_nil(s).contains(m.proposal),
        rounds_or_nil(s).contains(m.valid_round),
    )


def ind_vote_ok(s: TendermintAccState, m: Expr, kind: VoteKind) -> Expr:
    return And(
        all_procs(s).contains(m.src),
        rounds(s).contains(m.round),
        m.kind == kind,
        values_or_nil(s).contains(m.id),
    )


@invariant
def ind_type_ok(s: TendermintAccState) -> BoolExpr:
    valid_or_nil = s.ValidValues | Set(NIL_VALUE)
    return And(
        AllMaps(s.Corr, rounds(s)).contains(s.round),
        AllMaps(s.Corr, steps()).contains(s.step),
        AllMaps(s.Corr, valid_or_nil).contains(s.decision),
        AllMaps(s.Corr, valid_or_nil).contains(s.locked_value),
        AllMaps(s.Corr, rounds_or_nil(s)).contains(s.locked_round),
        AllMaps(s.Corr, valid_or_nil).contains(s.valid_value),
        AllMaps(s.Corr, rounds_or_nil(s)).contains(s.valid_round),
        s.msgs_propose.keys == rounds(s),
        Forall(
            Forall(all_proposals(s).contains(m) for m in s.msgs_propose[r])
            for r in rounds(s)
        ),
        benign_rounds_in_messages(s.msgs_propose),
        s.msgs_prevote.keys == rounds(s),
        Forall(
            Forall(all_prevotes(s).contains(m) for m in s.msgs_prevote[r])
            for r in rounds(s)
        ),
        benign_rounds_in_messages(s.msgs_prevote),
        s.msgs_precommit.keys == rounds(s),
        Forall(
            Forall(all_precommits(s).contains(m) for m in s.msgs_precommit[r])
            for r in rounds(s)
        ),
        benign_rounds_in_messages(s.msgs_precommit),
        action_names().contains(s.last_action),
    )


@invariant
def all_no_future_messages_sent(s: TendermintAccState) -> BoolExpr:
    def no_future_messages_for_larger_rounds(
        s: TendermintAccState, p: Expr
    ) -> BoolExpr:
        return Forall(
            And(
                Forall(m.src != p for m in s.msgs_propose[r]),
                Forall(m.src != p for m in s.msgs_prevote[r]),
                Forall(m.src != p for m in s.msgs_precommit[r]),
            )
            for r in SetIf(rr > s.round[p] for rr in rounds(s))
        )

    def no_future_messages_for_current_round(
        s: TendermintAccState, p: Expr
    ) -> BoolExpr:
        r = s.round[p]
        return And(
            (s.Proposer[r] == p) | Forall(m.src != p for m in s.msgs_propose[r]),
            Or(
                s.step[p] == Step.PREVOTE,
                s.step[p] == Step.PRECOMMIT,
                s.step[p] == Step.DECIDED,
                Forall(m.src != p for m in s.msgs_prevote[r]),
            ),
            Or(
                s.step[p] == Step.PRECOMMIT,
                s.step[p] == Step.DECIDED,
                Forall(m.src != p for m in s.msgs_precommit[r]),
            ),
        )

    return Forall(
        And(
            no_future_messages_for_current_round(s, p),
            no_future_messages_for_larger_rounds(s, p),
        )
        for p in s.Corr
    )


@invariant
def all_if_in_prevote_then_sent_prevote(s: TendermintAccState) -> BoolExpr:
    def if_in_prevote_then_sent_prevote(s: TendermintAccState, p: Expr) -> BoolExpr:
        return Implies(
            s.step[p] == Step.PREVOTE,
            Exists(
                values_or_nil(s).contains(m.id) & (m.src == p)
                for m in s.msgs_prevote[s.round[p]]
            ),
        )

    return Forall(if_in_prevote_then_sent_prevote(s, p) for p in s.Corr)


@invariant
def all_if_in_precommit_then_sent_precommit(s: TendermintAccState) -> BoolExpr:
    def if_in_precommit_then_sent_precommit(s: TendermintAccState, p: Expr) -> BoolExpr:
        precommitted = s.step[p] == Step.PRECOMMIT
        sent_precommit = Exists(
            values_or_nil(s).contains(m.id) & (m.src == p)
            for m in s.msgs_precommit[s.round[p]]
        )
        return precommitted.implies(sent_precommit)

    return Forall(if_in_precommit_then_sent_precommit(s, p) for p in s.Corr)


@invariant
def all_if_in_decided_then_valid_decision(s: TendermintAccState) -> BoolExpr:
    def if_in_decided_then_valid_decision(s: TendermintAccState, p: Expr) -> BoolExpr:
        return (s.step[p] == Step.DECIDED) == s.ValidValues.contains(s.decision[p])

    return Forall(if_in_decided_then_valid_decision(s, p) for p in s.Corr)


@invariant
def all_if_in_decided_then_received_proposal(s: TendermintAccState) -> BoolExpr:
    def if_in_decided_then_received_proposal(
        s: TendermintAccState, p: Expr
    ) -> BoolExpr:
        decided = s.step[p] == Step.DECIDED
        ex = Exists(
            Exists(
                (m.src == s.Proposer[r]) & (m.proposal == s.decision[p])
                for m in s.msgs_propose[r]
            )
            for r in rounds(s)
        )
        return decided.implies(ex)

    return Forall(if_in_decided_then_received_proposal(s, p) for p in s.Corr)


@invariant
def all_if_in_decided_then_received_two_thirds(s: TendermintAccState) -> BoolExpr:
    def if_in_decided_then_received_two_thirds(
        s: TendermintAccState, p: Expr
    ) -> BoolExpr:
        decided = s.step[p] == Step.DECIDED
        received_two_thids = Exists(
            senders(s, SetIf(m.id == s.decision[p] for m in s.msgs_precommit[r])).size
            >= threshold2(s)
            for r in rounds(s)
        )
        return decided.implies(received_two_thids)

    return Forall(if_in_decided_then_received_two_thirds(s, p) for p in s.Corr)


def proposal_in_round(
    s: TendermintAccState, r: Expr, proposed_val: Expr, vr: Expr
) -> BoolExpr:
    return Exists(
        And(
            m.src == s.Proposer[r],
            m.proposal == proposed_val,
            m.valid_round == vr,
        )
        for m in s.msgs_propose[r]
    )


def two_thirds_prevotes(s: TendermintAccState, vr: Expr, v: Expr) -> BoolExpr:
    return senders(s, SetIf(m.id == v for m in s.msgs_prevote[vr])).size >= threshold2(
        s
    )


@invariant
def all_if_sent_prevote_then_received_proposal_or_two_thirds(
    s: TendermintAccState,
) -> BoolExpr:
    def if_sent_prevote_then_received_proposal_or_two_thirds(
        s: TendermintAccState, r: Expr
    ) -> BoolExpr:
        return Forall(
            Or(
                s.Faulty.contains(mpv.src),
                mpv.id == NIL_VALUE,
                And(
                    mpv.id != NIL_VALUE,
                    Or(
                        proposal_in_round(s, r, mpv.id, Val(NIL_ROUND)),
                        Exists(
                            proposal_in_round(s, r, mpv.id, vr)
                            & two_thirds_prevotes(s, vr, mpv.id)
                            for vr in rounds(s).filter(lambda rr: rr < r)
                        ),
                    ),
                ),
            )
            for mpv in s.msgs_prevote[r]
        )

    return Forall(
        if_sent_prevote_then_received_proposal_or_two_thirds(s, r) for r in rounds(s)
    )


@invariant
def if_sent_precommit_then_received_two_thirds(
    s: TendermintAccState,
) -> BoolExpr:
    return Forall(
        Forall(
            Implies(
                s.Corr.contains(mpc.src),
                Or(
                    And(
                        s.ValidValues.contains(mpc.id),
                        senders(
                            s, SetIf(m.id == mpc.id for m in s.msgs_prevote[r])
                        ).size
                        >= threshold2(s),
                    ),
                    And(
                        mpc.id == NIL_VALUE,
                        senders(s, s.msgs_prevote[r]).size >= threshold2(s),
                    ),
                ),
            )
            for mpc in s.msgs_precommit[r]
        )
        for r in rounds(s)
    )


@invariant
def if_sent_precommit_then_sent_prevote(s: TendermintAccState) -> BoolExpr:
    return Forall(
        Forall(
            Implies(
                s.Corr.contains(mpc.src),
                Exists(m.src == mpc.src for m in s.msgs_prevote[r]),
            )
            for mpc in s.msgs_precommit[r]
        )
        for r in rounds(s)
    )


@invariant
def all_locked_round_iff_locked_value(s: TendermintAccState) -> BoolExpr:
    def locked_round_iff_locked_value(s: TendermintAccState, p: Expr) -> BoolExpr:
        return (s.locked_round[p] == NIL_ROUND) == (s.locked_value[p] == NIL_VALUE)

    return Forall(locked_round_iff_locked_value(s, p) for p in s.Corr)


@invariant
def all_valid_round_iff_valid_value(s: TendermintAccState) -> BoolExpr:
    def valid_round_iff_valid_value(s: TendermintAccState, p: Expr) -> BoolExpr:
        return (s.valid_round[p] == NIL_ROUND) == (s.valid_value[p] == NIL_VALUE)

    return Forall(valid_round_iff_valid_value(s, p) for p in s.Corr)


@invariant
def all_valid_and_locked_round_bounded(s: TendermintAccState) -> BoolExpr:
    return Forall(
        And(
            s.valid_round[p] <= s.round[p],
            s.locked_round[p] <= s.round[p],
        )
        for p in s.Corr
    )


@invariant
def all_if_valid_round_then_two_thirds_prevotes(s: TendermintAccState) -> BoolExpr:
    return Forall(
        Implies(
            s.valid_round[p] != NIL_ROUND,
            two_thirds_prevotes(s, s.valid_round[p], s.valid_value[p]),
        )
        for p in s.Corr
    )


@invariant
def all_if_locked_round_then_sent_commit(s: TendermintAccState) -> BoolExpr:
    def if_locked_round_then_sent_commit(s: TendermintAccState, p: Expr) -> BoolExpr:
        return Implies(
            s.locked_round[p] != NIL_ROUND,
            Exists(
                And(
                    r <= s.round[p],
                    Exists(
                        (m.src == p) & (m.id == s.locked_value[p])
                        for m in s.msgs_precommit[r]
                    ),
                )
                for r in rounds(s)
            ),
        )

    return Forall(if_locked_round_then_sent_commit(s, p) for p in s.Corr)


@invariant
def all_latest_precommit_has_locked_round(s: TendermintAccState) -> BoolExpr:
    def latest_precommit_has_locked_round(s: TendermintAccState, p: Expr) -> BoolExpr:
        return Or(
            # either, there is no locked round/value, and no precommits
            And(
                s.locked_round[p] == NIL_ROUND,
                s.locked_value[p] == NIL_VALUE,
                Forall(
                    Forall(
                        (m.src != p) | (m.id == NIL_VALUE) for m in s.msgs_precommit[r]
                    )
                    for r in rounds(s)
                ),
            ),
            # or, the locked round/value matches the latest precommit
            And(
                s.locked_round[p] != NIL_ROUND,
                s.locked_value[p] != NIL_VALUE,
                Forall(
                    Forall(
                        (m.src != p)
                        | (m.round <= s.locked_round[p])
                        | (m.id == NIL_VALUE)
                        for m in s.msgs_precommit[r]
                    )
                    for r in rounds(s)
                ),
                Exists(
                    (m.src == p) & (m.id == s.locked_value[p])
                    for m in s.msgs_precommit[s.locked_round[p]]
                ),
            ),
        )

    return Forall(latest_precommit_has_locked_round(s, p) for p in s.Corr)


@invariant
def all_no_equivocation_by_correct(s: TendermintAccState) -> BoolExpr:
    def no_equivocation_by_correct(
        s: TendermintAccState, r: Expr, msgs: Expr
    ) -> BoolExpr:
        return Forall(
            Exists(
                Forall((m.src == p).implies(m.id == v) for m in msgs[r])
                for v in s.ValidValues | Set(NIL_VALUE)
            )
            for p in s.Corr
        )

    def proposals_by_proposer(s: TendermintAccState, r: Expr, msgs: Expr) -> BoolExpr:
        # A correct proposer sends at most one proposal per round, so all
        # non-faulty proposals in round r agree on both proposal and valid_round.
        return Exists(
            Exists(
                Forall(
                    s.Faulty.contains(m.src)
                    | (
                        (m.src == s.Proposer[r])
                        & (m.proposal == v)
                        & (m.valid_round == vr)
                    )
                    for m in msgs[r]
                )
                for vr in rounds_or_nil(s)
            )
            for v in s.ValidValues
        )

    return Forall(
        And(
            proposals_by_proposer(s, r, s.msgs_propose),
            no_equivocation_by_correct(s, r, s.msgs_prevote),
            no_equivocation_by_correct(s, r, s.msgs_precommit),
        )
        for r in rounds(s)
    )


@invariant
def precommits_lock_value(s: TendermintAccState) -> BoolExpr:
    return Forall(
        Or(
            senders(s, SetIf(m.id == v for m in s.msgs_precommit[r])).size
            < threshold2(s),
            Forall(
                Forall(
                    senders(s, SetIf(m.id == w for m in s.msgs_prevote[fr])).size
                    < threshold2(s)
                    for w in s.ValidValues - Set(v)
                )
                for fr in SetIf(rr > r for rr in rounds(s))
            ),
        )
        for r in rounds(s)
        for v in s.ValidValues
    )


@invariant
def precommit_locks_later_prevotes(s: TendermintAccState) -> BoolExpr:
    """Per-process support that makes precommits_lock_value inductive. If a correct
    process precommitted a non-NIL value (!= w) in round r (locking it), then it
    prevotes w in a later round r2 only if w reached a 2f+1 prevote quorum in some
    round in [r, r2) -- the only way it could re-lock to w. Quantifiers range over
    the small Corr/rounds/ValidValues domains; message pools appear only under
    existentials, keeping the SMT encoding cheap."""
    return Forall(
        Implies(
            And(
                r2 > r,
                Exists(
                    (m.src == p) & (m.id != NIL_VALUE) & (m.id != w)
                    for m in s.msgs_precommit[r]
                ),
                Exists((m.src == p) & (m.id == w) for m in s.msgs_prevote[r2]),
            ),
            Exists(
                two_thirds_prevotes(s, vr, w)
                for vr in SetIf((rr >= r) & (rr < r2) for rr in rounds(s))
            ),
        )
        for p in s.Corr
        for r in rounds(s)
        for w in s.ValidValues
        for r2 in rounds(s)
    )


@invariant
def all_locked_proposer_reproposes(s: TendermintAccState) -> BoolExpr:
    """A correct proposer that already locked (precommitted a non-NIL value in an
    earlier round) never sends a fresh proposal (valid_round == NIL): a non-NIL
    precommit sets valid_value, which never reverts to NIL, so insert_proposal
    re-proposes it with a non-NIL valid_round."""
    return Forall(
        Implies(
            s.Corr.contains(s.Proposer[r])
            & Exists(
                (m.src == s.Proposer[r]) & (m.valid_round == NIL_ROUND)
                for m in s.msgs_propose[r]
            ),
            Forall(
                ~Exists(
                    (m.src == s.Proposer[r]) & (m.id != NIL_VALUE)
                    for m in s.msgs_precommit[r0]
                )
                for r0 in SetIf(rr < r for rr in rounds(s))
            ),
        )
        for r in rounds(s)
    )


@invariant
def all_past_start_round(s: TendermintAccState) -> BoolExpr:
    """To be in a round, requires StartRound in the past"""

    def prevotes_or_precommits_senders(r: Expr) -> Expr:
        precommit_senders = senders(s, s.msgs_precommit[r])
        prevote_senders = senders(s, s.msgs_prevote[r])
        return prevote_senders | precommit_senders

    def past_start_round(s: TendermintAccState, p: Expr, r: Expr) -> BoolExpr:
        # the expressions below are symbolic and lazy, not evaluated immediately
        return Or(
            r > s.round[p],
            # 10: StartRound(0)
            r == 0,
            # 55: upon f + 1 (*, h_p, round, *, *) with round > round_p do
            # 56:   StartRound(round)
            prevotes_or_precommits_senders(r).size >= threshold1(s),
            # 47: upon 2f + 1(PRECOMMIT, h_p, round_p, *) for the first time do
            # 48:   schedule OnTimeoutPrecommit(h_p, round_p)...
            # ...
            # 67: StartRound(round_p + 1)
            senders(s, s.msgs_precommit[r - 1]).size >= threshold2(s),
        )

    return Forall(past_start_round(s, p, r) for p in s.Corr for r in rounds(s))


@invariant
def all_rounds_below_have_precommit_quorum(s: TendermintAccState) -> BoolExpr:
    """A correct process can only be in round r if every earlier round already
    collected a 2f+1 precommit quorum (the only way the global maximum round
    advances is upon_quorum_of_precommits_any, which needs 2f+1 precommits in
    round r-1).

    The inner constraint does not depend on the process, so instead of quantifying
    over Corr we take the maximum round reached by any correct process and require
    the quorum in every round strictly below it."""
    max_round = Max(s.round.values, default=0)
    return Forall(
        Implies(
            r < max_round,
            senders(s, s.msgs_precommit[r]).size >= threshold2(s),
        )
        for r in rounds(s)
    )


@invariant
def all_valid_in_current_round_precommitted(s: TendermintAccState) -> BoolExpr:
    """If a correct process set valid_round in the round it is still in, it has
    already passed PREVOTE: valid_round is only assigned by
    upon_proposal_in_prevote_or_commit_and_prevote (guard step PREVOTE/PRECOMMIT),
    which leaves the process in step PRECOMMIT. A round is never revisited and the
    step only advances within a round, so step must be PRECOMMIT or DECIDED.
    valid_round == NIL never equals round >= 0, so the NIL case is vacuous."""
    return Forall(
        Implies(
            s.valid_round[p] == s.round[p],
            Or(s.step[p] == Step.PRECOMMIT, s.step[p] == Step.DECIDED),
        )
        for p in s.Corr
    )


@invariant
def all_locked_round_below_valid_round(s: TendermintAccState) -> BoolExpr:
    """locked_* and valid_* are set together when locking (the prevote branch of
    upon_proposal_in_prevote_or_commit_and_prevote), and valid_round only advances
    afterwards, so locked_round <= valid_round. NIL_ROUND = -1 makes the unlocked
    case free and forces valid_round != NIL whenever the process is locked."""
    return Forall(s.locked_round[p] <= s.valid_round[p] for p in s.Corr)


@invariant
def all_if_valid_round_then_precommitted(s: TendermintAccState) -> BoolExpr:
    """Setting valid_round = r requires reaching step PREVOTE/PRECOMMIT in round r,
    after which the process has broadcast a precommit in round r (the prevote branch
    precommits the value; the precommit branch means it already precommitted)."""
    return Forall(
        Implies(
            s.valid_round[p] != NIL_ROUND,
            Exists(m.src == p for m in s.msgs_precommit[s.valid_round[p]]),
        )
        for p in s.Corr
    )


@invariant
def all_correct_proposal_valid_round_below_round(s: TendermintAccState) -> BoolExpr:
    """A correct proposer broadcasts (insert_proposal) while in step PROPOSE, where
    valid_round[p] < round[p] (it equals round only from PRECOMMIT on, and is
    bounded by round). So every proposal from a correct src has valid_round < round."""
    return Forall(
        Forall(
            Implies(s.Corr.contains(m.src), m.valid_round < r)
            for m in s.msgs_propose[r]
        )
        for r in rounds(s)
    )


@invariant
def ind_inv(s: TendermintAccState) -> BoolExpr:
    return And(
        all_no_future_messages_sent(s),
        all_if_in_prevote_then_sent_prevote(s),
        all_if_in_precommit_then_sent_precommit(s),
        all_if_in_decided_then_received_proposal(s),
        all_if_in_decided_then_received_two_thirds(s),
        all_if_in_decided_then_valid_decision(s),
        all_locked_round_iff_locked_value(s),
        all_valid_round_iff_valid_value(s),
        all_valid_and_locked_round_bounded(s),
        all_if_valid_round_then_two_thirds_prevotes(s),
        all_if_locked_round_then_sent_commit(s),
        all_latest_precommit_has_locked_round(s),
        all_if_sent_prevote_then_received_proposal_or_two_thirds(s),
        if_sent_precommit_then_sent_prevote(s),
        if_sent_precommit_then_received_two_thirds(s),
        all_no_equivocation_by_correct(s),
        precommits_lock_value(s),
        precommit_locks_later_prevotes(s),
        all_locked_proposer_reproposes(s),
        all_past_start_round(s),
        all_rounds_below_have_precommit_quorum(s),
        all_valid_in_current_round_precommitted(s),
        all_locked_round_below_valid_round(s),
        all_if_valid_round_then_precommitted(s),
        all_correct_proposal_valid_round_below_round(s),
    )


@action(init=True)
def ind_init(c: Context[TendermintAccState]) -> BoolExpr:  # type: ignore
    """Use this action as the initial action to reason about inductive invariants"""
    s = c.state
    valid_or_nil = s.ValidValues | Set(NIL_VALUE)
    with (
        c.one_of(AllMaps(s.Corr, rounds(s)), "iround") as iround,
        c.one_of(AllMaps(s.Corr, steps()), "istep") as istep,
        c.one_of(AllMaps(s.Corr, valid_or_nil), "idecision") as idecision,
        c.one_of(AllMaps(s.Corr, valid_or_nil), "ilocked_value") as ilocked_value,
        c.one_of(AllMaps(s.Corr, rounds_or_nil(s)), "ilocked_round") as ilocked_round,
        c.one_of(AllMaps(s.Corr, valid_or_nil), "ivalid_value") as ivalid_value,
        c.one_of(AllMaps(s.Corr, rounds_or_nil(s)), "ivalid_round") as ivalid_round,
        c.one_of(
            AllMaps(rounds(s), AllSubsets(all_proposals(s))), "imsgs_propose"
        ) as imsgs_propose,
        c.one_of(
            AllMaps(rounds(s), AllSubsets(all_prevotes(s))), "imsgs_prevote"
        ) as imsgs_prevote,
        c.one_of(
            AllMaps(rounds(s), AllSubsets(all_precommits(s))), "imsgs_precommit"
        ) as imsgs_precommit,
        c.one_of(action_names(), "iaction") as iaction,
    ):
        c.assume(benign_rounds_in_messages(imsgs_propose))
        c.assume(benign_rounds_in_messages(imsgs_prevote))
        c.assume(benign_rounds_in_messages(imsgs_precommit))
        s.round = iround
        s.step = istep
        s.decision = idecision
        s.locked_value = ilocked_value
        s.locked_round = ilocked_round
        s.valid_value = ivalid_value
        s.valid_round = ivalid_round
        s.msgs_propose = imsgs_propose
        s.msgs_prevote = imsgs_prevote
        s.msgs_precommit = imsgs_precommit
        s.last_action = iaction
        c.assume(ind_inv(s))


@invariant
def typed_ind_inv(s: TendermintAccState) -> BoolExpr:
    return ind_type_ok(s) & ind_inv(s)


@invariant
def false_inv(s: TendermintAccState) -> BoolExpr:
    return Val(False)
