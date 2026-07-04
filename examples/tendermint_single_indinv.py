"""
Inductive invariant for the single-height Tendermint example.

Translated from the TLA+ invariant module:
https://github.com/cometbft/cometbft/blob/main/spec/light-client/accountability/TendermintAccInv_004_draft.tla

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
    AllTuples,
    And,
    BoolExpr,
    Exists,
    Expr,
    Forall,
    Implies,
    Or,
    Set,
    Val,
)
from wunderspec.machine import invariant

init = _base.init
init_with_faults = _base.init_with_faults
correct_step = _base.correct_step
faulty_step = _base.faulty_step
step = _base.step
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
    "n4_t1_f0",
    "n4_t1_f1",
    "n4_t1_f2",
    "ind_type_ok",
    "ind_inv",
    "typed_inv",
]


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
        Forall(ind_proposal_ok(s, m) for m in s.evidence_propose),
        Forall(ind_vote_ok(s, m, VoteKind.PREVOTE) for m in s.evidence_prevote),
        Forall(ind_vote_ok(s, m, VoteKind.PRECOMMIT) for m in s.evidence_precommit),
        action_names().contains(s.last_action),
    )


def evidence_contains_messages(s: TendermintAccState) -> BoolExpr:
    return And(
        Forall(s.msgs_propose[m.round].contains(m) for m in s.evidence_propose),
        Forall(s.msgs_prevote[m.round].contains(m) for m in s.evidence_prevote),
        Forall(s.msgs_precommit[m.round].contains(m) for m in s.evidence_precommit),
    )


def no_future_messages_for_larger_rounds(s: TendermintAccState, p: Expr) -> BoolExpr:
    return Forall(
        And(
            Forall(m.src != p for m in s.msgs_propose[r]),
            Forall(m.src != p for m in s.msgs_prevote[r]),
            Forall(m.src != p for m in s.msgs_precommit[r]),
        )
        for r in rounds(s).filter(lambda rr: rr > s.round[p])
    )


def no_future_messages_for_current_round(s: TendermintAccState, p: Expr) -> BoolExpr:
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


def all_no_future_messages_sent(s: TendermintAccState) -> BoolExpr:
    return Forall(
        And(
            no_future_messages_for_current_round(s, p),
            no_future_messages_for_larger_rounds(s, p),
        )
        for p in s.Corr
    )


def if_in_prevote_then_sent_prevote(s: TendermintAccState, p: Expr) -> BoolExpr:
    return Implies(
        s.step[p] == Step.PREVOTE,
        Exists(
            values_or_nil(s).contains(m.id) & (m.src == p)
            for m in s.msgs_prevote[s.round[p]]
        ),
    )


def all_if_in_prevote_then_sent_prevote(s: TendermintAccState) -> BoolExpr:
    return Forall(if_in_prevote_then_sent_prevote(s, p) for p in s.Corr)


def if_in_precommit_then_sent_precommit(s: TendermintAccState, p: Expr) -> BoolExpr:
    return Implies(
        s.step[p] == Step.PRECOMMIT,
        Exists(
            values_or_nil(s).contains(m.id) & (m.src == p)
            for m in s.msgs_precommit[s.round[p]]
        ),
    )


def all_if_in_precommit_then_sent_precommit(s: TendermintAccState) -> BoolExpr:
    return Forall(if_in_precommit_then_sent_precommit(s, p) for p in s.Corr)


def if_in_decided_then_valid_decision(s: TendermintAccState, p: Expr) -> BoolExpr:
    return (s.step[p] == Step.DECIDED) == s.ValidValues.contains(s.decision[p])


def all_if_in_decided_then_valid_decision(s: TendermintAccState) -> BoolExpr:
    return Forall(if_in_decided_then_valid_decision(s, p) for p in s.Corr)


def if_in_decided_then_received_proposal(s: TendermintAccState, p: Expr) -> BoolExpr:
    return Implies(
        s.step[p] == Step.DECIDED,
        Exists(
            Exists(
                And(
                    m.src == s.Proposer[r],
                    m.proposal == s.decision[p],
                )
                for m in s.msgs_propose[r] & s.evidence_propose
            )
            for r in rounds(s)
        ),
    )


def all_if_in_decided_then_received_proposal(s: TendermintAccState) -> BoolExpr:
    return Forall(if_in_decided_then_received_proposal(s, p) for p in s.Corr)


def if_in_decided_then_received_two_thirds(s: TendermintAccState, p: Expr) -> BoolExpr:
    return Implies(
        s.step[p] == Step.DECIDED,
        Exists(
            (s.msgs_precommit[r] & s.evidence_precommit)
            .filter(lambda m: m.id == s.decision[p])
            .size
            >= threshold2(s)
            for r in rounds(s)
        ),
    )


def all_if_in_decided_then_received_two_thirds(s: TendermintAccState) -> BoolExpr:
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
    return (s.msgs_prevote[vr] & s.evidence_prevote).filter(
        lambda m: m.id == v
    ).size >= threshold2(s)


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


def all_if_sent_prevote_then_received_proposal_or_two_thirds(
    s: TendermintAccState,
) -> BoolExpr:
    return Forall(
        if_sent_prevote_then_received_proposal_or_two_thirds(s, r) for r in rounds(s)
    )


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
                        (s.msgs_prevote[r] & s.evidence_prevote)
                        .filter(lambda m: m.id == mpc.id)
                        .size
                        >= threshold2(s),
                    ),
                    And(
                        mpc.id == NIL_VALUE,
                        s.msgs_prevote[r].size >= threshold2(s),
                    ),
                ),
            )
            for mpc in s.msgs_precommit[r]
        )
        for r in rounds(s)
    )


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


def locked_round_iff_locked_value(s: TendermintAccState, p: Expr) -> BoolExpr:
    return (s.locked_round[p] == NIL_ROUND) == (s.locked_value[p] == NIL_VALUE)


def all_locked_round_iff_locked_value(s: TendermintAccState) -> BoolExpr:
    return Forall(locked_round_iff_locked_value(s, p) for p in s.Corr)


def if_locked_round_then_sent_commit(s: TendermintAccState, p: Expr) -> BoolExpr:
    return Implies(
        s.locked_round[p] != NIL_ROUND,
        Exists(
            (r <= s.round[p])
            & Exists(
                (m.src == p) & (m.id == s.locked_value[p]) for m in s.msgs_precommit[r]
            )
            for r in rounds(s)
        ),
    )


def all_if_locked_round_then_sent_commit(s: TendermintAccState) -> BoolExpr:
    return Forall(if_locked_round_then_sent_commit(s, p) for p in s.Corr)


def latest_precommit_has_locked_round(s: TendermintAccState, p: Expr) -> BoolExpr:
    has_non_nil_precommit = Exists(
        Exists((m.src == p) & (m.id != NIL_VALUE) for m in s.msgs_precommit[r])
        for r in rounds(s)
    )
    return Implies(
        has_non_nil_precommit,
        And(
            Exists(
                Exists(
                    (m.src == p)
                    & (m.id != NIL_VALUE)
                    & (m.round == s.locked_round[p])
                    & (m.id == s.locked_value[p])
                    for m in s.msgs_precommit[r]
                )
                for r in rounds(s)
            ),
            Forall(
                Forall(
                    Implies(
                        (m.src == p) & (m.id != NIL_VALUE),
                        m.round <= s.locked_round[p],
                    )
                    for m in s.msgs_precommit[r]
                )
                for r in rounds(s)
            ),
        ),
    )


def all_latest_precommit_has_locked_round(s: TendermintAccState) -> BoolExpr:
    return Forall(latest_precommit_has_locked_round(s, p) for p in s.Corr)


def no_equivocation_by_correct(s: TendermintAccState, r: Expr, msgs: Expr) -> BoolExpr:
    return Forall(
        Exists(
            Forall((m.src != p) | (m.id == v) for m in msgs[r])
            for v in s.ValidValues | Set(NIL_VALUE)
        )
        for p in s.Corr
    )


def proposals_by_proposer(s: TendermintAccState, r: Expr, msgs: Expr) -> BoolExpr:
    return Exists(
        Forall(
            s.Faulty.contains(m.src) | ((m.src == s.Proposer[r]) & (m.proposal == v))
            for m in msgs[r]
        )
        for v in s.ValidValues
    )


def all_no_equivocation_by_correct(s: TendermintAccState) -> BoolExpr:
    return Forall(
        And(
            proposals_by_proposer(s, r, s.msgs_propose),
            no_equivocation_by_correct(s, r, s.msgs_prevote),
            no_equivocation_by_correct(s, r, s.msgs_precommit),
        )
        for r in rounds(s)
    )


def precommits_lock_value(s: TendermintAccState) -> BoolExpr:
    return Forall(
        Or(
            senders(
                s,
                s.msgs_precommit[r].filter(lambda m: m.id == v),
            ).size
            < threshold1(s),
            Forall(
                Forall(
                    senders(
                        s,
                        s.msgs_prevote[fr].filter(lambda m: m.id == w),
                    ).size
                    < threshold2(s)
                    for w in values_or_nil(s) - Set(v)
                )
                for fr in rounds(s).filter(lambda rr: rr > r)
            ),
        )
        for r in rounds(s)
        for v in s.ValidValues | Set(NIL_VALUE)
    )


@invariant
def ind_inv(s: TendermintAccState) -> BoolExpr:
    return And(
        evidence_contains_messages(s),
        all_no_future_messages_sent(s),
        all_if_in_prevote_then_sent_prevote(s),
        all_if_in_precommit_then_sent_precommit(s),
        all_if_in_decided_then_received_proposal(s),
        all_if_in_decided_then_received_two_thirds(s),
        all_if_in_decided_then_valid_decision(s),
        all_locked_round_iff_locked_value(s),
        all_if_locked_round_then_sent_commit(s),
        all_latest_precommit_has_locked_round(s),
        all_if_sent_prevote_then_received_proposal_or_two_thirds(s),
        if_sent_precommit_then_sent_prevote(s),
        if_sent_precommit_then_received_two_thirds(s),
        all_no_equivocation_by_correct(s),
        precommits_lock_value(s),
    )


@invariant
def typed_inv(s: TendermintAccState) -> BoolExpr:
    return ind_type_ok(s) & ind_inv(s)
