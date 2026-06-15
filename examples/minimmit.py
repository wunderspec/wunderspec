"""
Minimmit BFT Consensus Protocol in Wunderspec.

Translated from the Quint specification:
  https://github.com/commonwarexyz/monorepo/blob/4ff08da00068d61d50f745be2942d6a45597ed46/pipeline/minimmit/quint/replica.qnt

Embedded definitions from the imported Quint modules:
  - types.qnt  — type aliases, constants, record types, vote constructors
  - defs.qnt   — pure helpers (max, last)

Claude Opus 4.6, 2026
"""

from enum import Enum, auto

from wunderspec import (
    AllMaps,
    AllSubsets,
    And,
    BoolExpr,
    Exists,
    Expr,
    Field,
    Forall,
    List,
    Or,
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

ReplicaId = str
ViewNumber = int
Block = str
ReplicaKey = str
Signature = str

GENESIS_BLOCK: Block = "GENESIS_BLOCK"
DUMMY_BLOCK: Block = "DUMMY_BLOCK"
EMPTY_BLOCK: Block = "EMPTY_BLOCK"
GENESIS_VIEW: ViewNumber = -1


class VoteKind(Enum):
    """Vote kinds sent by individual replicas."""

    NOTARIZE = auto()
    NULLIFY = auto()


class CertificateKind(Enum):
    """Certificate kinds assembled from quorums of votes."""

    NOTARIZATION = auto()
    NULLIFICATION = auto()
    FINALIZATION = auto()


@record
class Vote:
    """A notarize or nullify vote cast by a replica."""

    view: Field[ViewNumber]
    block: Field[Block]
    sig: Field[Signature]
    kind: Field[VoteKind]


@record
class Certificate:
    """A notarization, nullification, or finalization certificate."""

    view: Field[ViewNumber]
    kind: Field[CertificateKind]
    block: Field[Block]
    signatures: Field[set[Signature]]
    ghost_sender: Field[ReplicaId]


@record
class Proposal:
    """A leader proposal message."""

    block: Field[Block]
    view: Field[ViewNumber]
    block_parent: Field[Block]
    view_parent: Field[ViewNumber]
    sig: Field[Signature]


@record
class ReplicaStateRec:
    """Per-replica local state (corresponds to Quint's ReplicaState record)."""

    # Current view (height), initially 0
    view: Field[ViewNumber]
    # Whether this replica has nullified the current view
    nullified: Field[bool]
    # timer_cancelled ⟺ timer ≥ 2Δ
    timer_cancelled: Field[bool]
    # Ghost: last view for which a notarization was observed
    ghost_last_seen_notarization: Field[ViewNumber]
    # Ghost: blocks sent as votes per view (ViewNumber -> List[Block])
    ghost_sent_votes: Field[dict[ViewNumber, list[Block]]]
    # Last view this replica finalized
    last_finalized: Field[ViewNumber]
    # Whether this replica has sent a proposal in the current view
    propose_sent: Field[bool]
    # Notarized block per view (ViewNumber -> Block); EMPTY_BLOCK means none
    notarized: Field[dict[ViewNumber, Block]]


# ── State machine ─────────────────────────────────────────────────────────────


@state
class MinimmitState(MachineStateBase):
    """Global state of the Minimmit consensus protocol."""

    # ── Parameters (Quint constants) ──────────────────────────────────────────
    N: Param[int]
    F: Param[int]
    CORRECT: Param[set[ReplicaId]]
    BYZANTINE: Param[set[ReplicaId]]
    VIEWS: Param[set[ViewNumber]]
    VALID_BLOCKS: Param[set[Block]]
    INVALID_BLOCKS: Param[set[Block]]
    # Maps each replica identity to its signing key
    REPLICA_KEYS: Param[dict[ReplicaId, ReplicaKey]]

    # ── Variables ─────────────────────────────────────────────────────────────
    # Per-replica local states
    replica_state: StateVar[dict[ReplicaId, ReplicaStateRec]]
    # Leader function: view → replica id
    leader: StateVar[dict[ViewNumber, ReplicaId]]
    # Proposals broadcast by correct and byzantine replicas
    sent_proposal: StateVar[set[Proposal]]
    # Notarize/nullify votes broadcast by all replicas
    sent_vote: StateVar[set[Vote]]
    # Notarization/nullification/finalization certificates broadcast by all replicas
    sent_certificate: StateVar[set[Certificate]]
    # Votes received by each correct replica
    store_vote: StateVar[dict[ReplicaId, set[Vote]]]
    # Certificates received or assembled by each correct replica
    store_certificate: StateVar[dict[ReplicaId, set[Certificate]]]
    # Ghost: proposals received by each correct replica
    ghost_proposal: StateVar[dict[ReplicaId, set[Proposal]]]
    # Ghost: sequence of blocks committed by each correct replica
    ghost_committed_blocks: StateVar[dict[ReplicaId, list[Block]]]


# ── Derived quantities (Quint pure val) ───────────────────────────────────────


def replicas(s: MinimmitState) -> Expr:
    """All replicas: CORRECT ∪ BYZANTINE."""
    return s.CORRECT | s.BYZANTINE


def all_blocks(s: MinimmitState) -> Expr:
    """All blocks: VALID_BLOCKS ∪ INVALID_BLOCKS."""
    return s.VALID_BLOCKS | s.INVALID_BLOCKS


def L_quorum(s: MinimmitState) -> Expr:
    """Finalization quorum threshold: N − F."""
    return s.N - s.F


def M_quorum(s: MinimmitState) -> Expr:
    """Notarization quorum threshold: 2·F + 1."""
    return Val(2) * s.F + 1


# ── Message constructors ─────────────────────────────────────────────────────


def mk_notarize(view: Expr, replica_id: Expr, block: Expr) -> Expr:
    """Create a notarize vote."""
    return Vote(  # type: ignore[return-value]
        view=view, sig=replica_id, block=block, kind=Val(VoteKind.NOTARIZE)
    )


def mk_nullify(view: Expr, replica_id: Expr) -> Expr:
    """Create a nullify vote."""
    return Vote(  # type: ignore[return-value]
        view=view,
        sig=replica_id,
        block=Val(DUMMY_BLOCK),
        kind=Val(VoteKind.NULLIFY),
    )


def mk_certificate(
    view: Expr, block: Expr, sigs: Expr, sender: Expr, kind: Expr
) -> Expr:
    """Create a certificate record."""
    return Certificate(  # type: ignore[return-value]
        view=view, block=block, signatures=sigs, ghost_sender=sender, kind=kind
    )


def mk_proposal(
    view: Expr, view_parent: Expr, block: Expr, block_parent: Expr, sig: Expr
) -> Expr:
    """Create a proposal record."""
    return Proposal(  # type: ignore[return-value]
        view=view,
        view_parent=view_parent,
        block=block,
        block_parent=block_parent,
        sig=sig,
    )


def sig_of(s: MinimmitState, replica_id: Expr) -> Expr:
    """Get the signing key of a replica."""
    return s.REPLICA_KEYS[replica_id]


def create_notarization(
    s: MinimmitState, replica_id: Expr, view: Expr, block: Expr, votes: Expr
) -> tuple[BoolExpr, Expr]:
    """Assemble a notarization or finalization certificate from matching votes."""
    similar_votes = SetIf(
        (v.view == view) & (v.kind == VoteKind.NOTARIZE) & (v.block == block)
        for v in votes
    )
    votes_count = similar_votes.size
    cert_kind = (
        Val(CertificateKind.FINALIZATION)
        .if_(votes_count >= L_quorum(s))
        .else_(Val(CertificateKind.NOTARIZATION))
    )
    return (
        votes_count >= M_quorum(s),
        mk_certificate(
            view, block, Set(v.sig for v in similar_votes), replica_id, cert_kind
        ),
    )


def create_nullification(
    s: MinimmitState, replica_id: Expr, view: Expr, votes: Expr
) -> tuple[BoolExpr, Expr]:
    """Assemble a nullification certificate from matching votes."""
    similar_votes = SetIf(
        (v.view == view) & (v.kind == VoteKind.NULLIFY) & (v.block == Val(DUMMY_BLOCK))
        for v in votes
    )
    votes_count = similar_votes.size
    return (
        votes_count >= M_quorum(s),
        mk_certificate(
            view,
            Val(DUMMY_BLOCK),
            Set(v.sig for v in similar_votes),
            replica_id,
            Val(CertificateKind.NULLIFICATION),
        ),
    )


# ── Pure predicates (Quint pure def / def) ────────────────────────────────────


def select_votes(view: Expr, kind: Expr, votes: Expr) -> Expr:
    """Filter votes by view and kind."""
    return SetIf((v.kind == kind) & (v.view == view) for v in votes)


def has_notarized(r: Expr) -> BoolExpr:
    """Has the replica notarized a block in its current view?"""
    return r.notarized[r.view] != Val(EMPTY_BLOCK)


def has_notarized_view(replica_id: Expr, view: Expr, votes: Expr) -> BoolExpr:
    """Has the replica cast a notarize vote for `view`?"""
    return Exists(
        (v.view == view) & (v.kind == VoteKind.NOTARIZE) & (v.sig == replica_id)
        for v in votes
    )


def is_view_notarized(view: Expr, certificates: Expr) -> BoolExpr:
    """Does the certificate set contain a notarization or finalization for `view`?"""
    return Or(
        view == Val(GENESIS_VIEW),
        Exists(
            (cert.kind == CertificateKind.NOTARIZATION) & (cert.view == view)
            for cert in certificates
        ),
        Exists(
            (cert.kind == CertificateKind.FINALIZATION) & (cert.view == view)
            for cert in certificates
        ),
    )


def is_view_notarized_votes(
    s: MinimmitState, view: Expr, votes: Expr, block: Expr
) -> BoolExpr:
    """Does the vote set contain an M-quorum of notarize votes for (view, block)?"""
    return Or(
        view == Val(GENESIS_VIEW),
        Set(
            v.sig
            for v in SetIf(
                v.block == block
                for v in select_votes(view, Val(VoteKind.NOTARIZE), votes)
            )
        ).size
        >= M_quorum(s),
    )


def is_view_finalized(view: Expr, certificates: Expr) -> BoolExpr:
    """Does the certificate set contain a finalization for `view`?"""
    return Or(
        view == Val(GENESIS_VIEW),
        Exists(
            (cert.kind == CertificateKind.FINALIZATION) & (cert.view == view)
            for cert in certificates
        ),
    )


def is_view_finalized_votes(
    s: MinimmitState, view: Expr, votes: Expr, block: Expr
) -> BoolExpr:
    """Does the vote set contain an L-quorum of notarize votes for (view, block)?"""
    return Or(
        view == Val(GENESIS_VIEW),
        Set(
            v.sig
            for v in SetIf(
                v.block == block
                for v in select_votes(view, Val(VoteKind.NOTARIZE), votes)
            )
        ).size
        >= L_quorum(s),
    )


def is_view_nullified(view: Expr, certificates: Expr) -> BoolExpr:
    """Does the certificate set contain a nullification for `view`?"""
    return Or(
        view == Val(GENESIS_VIEW),
        Exists(
            (cert.kind == CertificateKind.NULLIFICATION) & (cert.view == view)
            for cert in certificates
        ),
    )


def is_view_nullified_votes(s: MinimmitState, view: Expr, votes: Expr) -> BoolExpr:
    """Does the vote set contain an M-quorum of nullify votes for `view`?"""
    return Or(
        view == Val(GENESIS_VIEW),
        Set(
            v.sig
            for v in SetIf(
                v.block == Val(DUMMY_BLOCK)
                for v in select_votes(view, Val(VoteKind.NULLIFY), votes)
            )
        ).size
        >= M_quorum(s),
    )


def are_views_nullified(
    s: MinimmitState, v1: Expr, v2: Expr, certificates: Expr
) -> BoolExpr:
    """Are all views in the open interval (v1, v2) nullified?"""
    return Forall(
        is_view_nullified(v, certificates)
        for v in SetIf((v > v1) & (v < v2) for v in s.VIEWS)
    )


def valid_parent(
    s: MinimmitState, view: Expr, view_parent: Expr, certificates: Expr
) -> BoolExpr:
    """Is (view_parent, block_parent) a valid parent chain? Parent must be notarized
    and all intermediate views must be nullified."""
    return is_view_notarized(view_parent, certificates) & are_views_nullified(
        s, view_parent, view, certificates
    )


def is_block_in_view_cannot_be_notarized(
    s: MinimmitState, b: Expr, view: Expr, votes: Expr
) -> BoolExpr:
    """Can block `b` no longer be notarized in `view` given the current votes?
    True when M or more signers have voted for a different block or to nullify."""
    vs1 = Set(
        v.sig
        for v in SetIf(
            (v.kind == VoteKind.NOTARIZE) & (v.view == view) & (v.block != b)
            for v in votes
        )
    )
    vs2 = Set(
        v.sig
        for v in SetIf((v.kind == VoteKind.NULLIFY) & (v.view == view) for v in votes)
    )
    return (vs1 | vs2).size >= M_quorum(s)


def is_contradicted(
    s: MinimmitState, self_rec: Expr, view: Expr, votes: Expr
) -> BoolExpr:
    """Is the notarized block for `view` contradicted by the received votes?"""
    block = self_rec.notarized[view]
    return And(
        block != Val(EMPTY_BLOCK),
        is_block_in_view_cannot_be_notarized(s, block, view, votes),
    )


def is_select_parent(
    s: MinimmitState, replica_id: Expr, parent_block: Expr, parent_view: Expr
) -> BoolExpr:
    """True iff (parent_block, parent_view) is the result of select_parent.
    All views (parent_view, current_view) must be nullified and parent_view notarized.
    """
    v = s.replica_state[replica_id].view
    certs = s.store_certificate[replica_id]
    return And(
        # All views strictly between parent_view and v are nullified (and not notarized)
        Forall(
            ((parent_view < i) & (i < v)).implies(
                And(~is_view_notarized(i, certs), is_view_nullified(i, certs))
            )
            for i in s.VIEWS
        ),
        # The parent block is notarized in parent_view, or this is genesis
        Or(
            Exists(
                And(
                    (cert.kind == CertificateKind.NOTARIZATION)
                    | (cert.kind == CertificateKind.FINALIZATION),
                    cert.view == parent_view,
                    cert.block == parent_block,
                )
                for cert in certs
            ),
            (parent_view < Val(0)) & (parent_block == Val(GENESIS_BLOCK)),
        ),
    )


def enter_new_view(s: MinimmitState, self_rec: Expr, cert: Expr) -> Expr:
    """Advance to cert.view + 1 if that view exists."""
    new_view = cert.view + Val(1)
    advances = self_rec.view < new_view
    updated = self_rec.edit()
    updated.propose_sent = Val(False).if_(advances).else_(self_rec.propose_sent)
    updated.nullified = Val(False).if_(advances).else_(self_rec.nullified)
    updated.timer_cancelled = Val(False).if_(advances).else_(self_rec.timer_cancelled)
    updated.view = new_view.if_(advances).else_(self_rec.view)
    updated.last_finalized = cert.view.if_(
        (self_rec.last_finalized < cert.view)
        & (cert.kind == CertificateKind.FINALIZATION)
    ).else_(self_rec.last_finalized)
    updated.ghost_last_seen_notarization = cert.view.if_(
        (self_rec.ghost_last_seen_notarization < cert.view)
        & (cert.kind != CertificateKind.NULLIFICATION)
    ).else_(self_rec.ghost_last_seen_notarization)
    return updated.result.if_(s.VIEWS.contains(new_view)).else_(self_rec)


# ── Actions ───────────────────────────────────────────────────────────────────


@action(init=True)
def init(c: Context[MinimmitState]):
    """Initialize all replicas with genesis state.
    Corresponds to Quint's init / initWithLeader."""
    s = c.state
    # Non-deterministically choose a total leader function: View → ReplicaId
    with c.one_of(AllMaps(s.VIEWS, replicas(s)), "leader_fn") as leader_fn:
        s.leader = leader_fn

    s.replica_state = s.CORRECT.map_to(
        lambda _: ReplicaStateRec(  # type: ignore[arg-type, return-value]
            view=Val(0),
            ghost_last_seen_notarization=Val(GENESIS_VIEW),
            last_finalized=Val(GENESIS_VIEW),
            notarized=s.VIEWS.map_to(lambda _: Val(EMPTY_BLOCK)),
            propose_sent=Val(False),
            nullified=Val(False),
            timer_cancelled=Val(False),
            ghost_sent_votes=s.VIEWS.map_to(lambda _: List(Block)),
        )
    )
    s.sent_proposal = Set(Proposal)
    s.sent_vote = Set(Vote)
    s.sent_certificate = Set(Certificate)
    s.store_vote = s.CORRECT.map_to(lambda _: Set(Vote))
    s.store_certificate = s.CORRECT.map_to(lambda _: Set(Certificate))
    s.ghost_committed_blocks = s.CORRECT.map_to(lambda _: List(Block))
    s.ghost_proposal = s.CORRECT.map_to(lambda _: Set(Proposal))


@action(inline=False)
def proposer_step(
    c: Context[MinimmitState],
    replica_id: Expr,
    new_block: Expr,
    parent_block: Expr,
    parent_view: Expr,
):
    """8.1. Proposer step: the view leader broadcasts a proposal.
    The proposal also counts as the leader's own notarize vote."""
    s = c.state
    self_rec = s.replica_state[replica_id]

    c.assume(replica_id == s.leader[self_rec.view])
    c.assume(~self_rec.propose_sent)
    c.assume(~self_rec.timer_cancelled)
    # make sure that parent_block and parent_view are chosen according to select_parent
    c.assume(is_select_parent(s, replica_id, parent_block, parent_view))

    # send the proposal
    proposal = mk_proposal(
        self_rec.view, parent_view, new_block, parent_block, sig_of(s, replica_id)
    )
    s.sent_proposal |= Set(proposal)

    # Record the proposal-as-notarize-vote in the replica's ghost state.
    s.replica_state[replica_id].propose_sent = True
    # "Treat propose(r, c, v, (c', v')) as r's notarize(c, v)"
    s.replica_state[replica_id].notarized[self_rec.view] = new_block
    s.replica_state[replica_id].ghost_sent_votes[self_rec.view] = (
        self_rec.ghost_sent_votes[self_rec.view] + List(new_block)
    )
    # "Treat propose(r, c, v, (c', v')) as r's notarize(c, v)"
    s.sent_vote |= Set(mk_notarize(self_rec.view, replica_id, new_block))
    s.ghost_proposal[replica_id] |= Set(proposal)


@action(inline=False)
def on_proposal(c: Context[MinimmitState], replica_id: Expr, proposal: Expr):
    """8.2. On receiving a valid proposal, cast a notarize vote."""
    s = c.state
    self_rec = s.replica_state[replica_id]
    certificates = s.store_certificate[replica_id]

    c.assume(~has_notarized(self_rec))
    c.assume(~self_rec.nullified)
    c.assume(proposal.block != Val(DUMMY_BLOCK))
    # Accept only proposals for current view we are working on
    c.assume(proposal.view == self_rec.view)
    # "If !verify(c, c'), return."
    c.assume(s.VALID_BLOCKS.contains(proposal.block))
    # the proposer is the leader of this view
    c.assume(proposal.sig == sig_of(s, s.leader[proposal.view]))
    c.assume(proposal.view_parent < proposal.view)
    c.assume(proposal.view_parent >= self_rec.last_finalized)
    c.assume(valid_parent(s, proposal.view, proposal.view_parent, certificates))

    # Send the notarize vote to all replicas (including ourselves).
    notarize_vote = mk_notarize(proposal.view, replica_id, proposal.block)
    s.sent_vote |= Set(notarize_vote)
    # Store proposal
    s.ghost_proposal[replica_id] |= Set(proposal)
    s.replica_state[replica_id].notarized[self_rec.view] = proposal.block
    s.replica_state[replica_id].ghost_sent_votes[self_rec.view] = (
        self_rec.ghost_sent_votes[self_rec.view] + List(proposal.block)
    )


@action(inline=False)
def on_nullify_by_contradiction(
    c: Context[MinimmitState], replica_id: Expr, view: Expr
):
    """8.6. Nullify by contradiction: if the notarized block can no longer win,
    send a nullify vote to prevent a liveness stall."""
    s = c.state
    self_rec = s.replica_state[replica_id]

    c.assume(self_rec.view == view)
    c.assume(has_notarized(self_rec))
    c.assume(~self_rec.nullified)
    c.assume(is_contradicted(s, self_rec, view, s.store_vote[replica_id]))

    s.sent_vote |= Set(mk_nullify(view, replica_id))
    s.replica_state[replica_id].nullified = True
    s.replica_state[replica_id].ghost_sent_votes[self_rec.view] = (
        self_rec.ghost_sent_votes[self_rec.view] + List(Val(DUMMY_BLOCK))
    )


@action(inline=False)
def process_certificate(
    c: Context[MinimmitState], replica_id: Expr, cert: Expr, is_new_cert: Expr
):
    """Internal: process a notarization, nullification, or finalization certificate.
    Corresponds to Quint's _process_certificate."""
    s = c.state
    self_rec = s.replica_state[replica_id]

    c.assume(cert.signatures.size >= M_quorum(s))

    # Should we send a notarize vote (late join case)?
    should_send_notarize_vote = And(
        (self_rec.view == cert.view),
        ~has_notarized(self_rec),
        ~self_rec.nullified,
        Or(
            cert.kind == CertificateKind.NOTARIZATION,
            cert.kind == CertificateKind.FINALIZATION,
        ),
        is_new_cert,
    )

    # Store and broadcast the certificate if it is new
    s.store_certificate[replica_id] = (
        (s.store_certificate[replica_id] | Set(cert))
        .if_(is_new_cert)
        .else_(s.store_certificate[replica_id])
    )
    s.sent_certificate = (
        (s.sent_certificate | Set(cert)).if_(is_new_cert).else_(s.sent_certificate)
    )

    # Append to committed blocks on finalization
    should_commit = And(
        cert.kind == CertificateKind.FINALIZATION,
        cert.view > self_rec.last_finalized,
        is_new_cert,
    )
    s.ghost_committed_blocks[replica_id] = (
        (s.ghost_committed_blocks[replica_id] + List(cert.block))
        .if_(should_commit)
        .else_(s.ghost_committed_blocks[replica_id])
    )

    # Advance view via the certificate
    new_self = enter_new_view(s, self_rec, cert)

    # If we should send a notarize vote, also record the block in ghost state
    updated_self = new_self.edit()
    updated_self.notarized[cert.view] = cert.block
    updated_self.ghost_sent_votes[cert.view] = new_self.ghost_sent_votes[
        cert.view
    ] + List(cert.block)
    updated_self_with_notarize = updated_self.result

    s.sent_vote = (
        (s.sent_vote | Set(mk_notarize(cert.view, replica_id, cert.block)))
        .if_(should_send_notarize_vote)
        .else_(s.sent_vote)
    )
    s.replica_state[replica_id] = updated_self_with_notarize.if_(
        should_send_notarize_vote
    ).else_(new_self)


@action(inline=False)
def on_vote_notarize(
    c: Context[MinimmitState],
    replica_id: Expr,
    view: Expr,
    block: Expr,
    votes: Expr,
):
    """8.4. On receiving a quorum subset of notarize votes, attempt to assemble
    a notarization or finalization certificate."""
    s = c.state
    store = s.store_vote[replica_id]

    c.assume(~votes.is_empty)
    c.assume(
        # Note, `view` is not the current view of the replica,
        # it is `view` from the input needed to be able pass the concrete view
        # parameter into underlying functions, e.g., `is_view_finalized`.
        Forall(
            And(v.view == view, v.kind == VoteKind.NOTARIZE, v.block == block)
            for v in votes
        )
    )

    new_store = store | votes
    s.store_vote[replica_id] = new_store

    certificates = s.store_certificate[replica_id]
    was_notarized = Or(
        is_view_notarized(view, certificates),
        is_view_notarized_votes(s, view, store, block),
    )
    now_notarized = is_view_notarized_votes(s, view, new_store, block)
    was_finalized = Or(
        is_view_finalized(view, certificates),
        is_view_finalized_votes(s, view, store, block),
    )
    now_finalized = is_view_finalized_votes(s, view, new_store, block)
    is_new_cert = Or(~was_notarized & now_notarized, ~was_finalized & now_finalized)

    has_cert, cert = create_notarization(s, replica_id, view, block, new_store)
    cert_br, no_cert_br = c.split(has_cert)
    with cert_br:
        process_certificate(c, replica_id, cert, is_new_cert)
    with no_cert_br:
        pass


@action(inline=False)
def on_vote_nullify(
    c: Context[MinimmitState], replica_id: Expr, view: Expr, votes: Expr
):
    """8.5.2. On receiving a quorum subset of nullify votes, attempt to assemble
    a nullification certificate."""
    s = c.state
    store = s.store_vote[replica_id]
    certificates = s.store_certificate[replica_id]

    c.assume(~votes.is_empty)
    c.assume(
        Forall(
            And(v.view == view, v.kind == VoteKind.NULLIFY, ~store.contains(v))
            for v in votes
        )
    )

    new_store = store | votes
    s.store_vote[replica_id] = new_store

    was_nullified = is_view_nullified(view, certificates)
    now_nullified = is_view_nullified_votes(s, view, new_store)
    is_new_cert = ~was_nullified & now_nullified

    has_cert, cert = create_nullification(s, replica_id, view, new_store)
    cert_br, no_cert_br = c.split(has_cert)
    with cert_br:
        process_certificate(c, replica_id, cert, is_new_cert)
    with no_cert_br:
        pass


@action(inline=False)
def on_certificate(c: Context[MinimmitState], replica_id: Expr, cert: Expr):
    """On receiving a valid certificate message, store it and possibly advance view."""
    s = c.state
    certificates = s.store_certificate[replica_id]

    c.assume(~certificates.contains(cert))
    c.assume(
        Or(
            And(
                cert.kind == CertificateKind.NULLIFICATION,
                cert.signatures.size >= M_quorum(s),
                ~is_view_nullified(cert.view, certificates),
            ),
            And(
                cert.kind == CertificateKind.NOTARIZATION,
                cert.signatures.size >= M_quorum(s),
                ~is_view_notarized(cert.view, certificates),
            ),
            And(
                cert.kind == CertificateKind.FINALIZATION,
                cert.signatures.size >= L_quorum(s),
                ~is_view_finalized(cert.view, certificates),
            ),
        )
    )
    # is_new_cert = True because the cert is not yet stored (checked above)
    process_certificate(c, replica_id, cert, Val(True))


@action(inline=False)
def on_timer_expired(c: Context[MinimmitState], replica_id: Expr):
    """Backup timer: if the timer fires and the replica has not notarized, broadcast nullify(v)."""
    s = c.state
    self_rec = s.replica_state[replica_id]

    c.assume(~self_rec.timer_cancelled)
    c.assume(~has_notarized(self_rec))
    c.assume(~self_rec.nullified)

    vote = mk_nullify(self_rec.view, replica_id)
    s.sent_vote |= Set(vote)
    s.replica_state[replica_id].nullified = True
    s.replica_state[replica_id].timer_cancelled = True
    s.replica_state[replica_id].ghost_sent_votes[self_rec.view] = (
        self_rec.ghost_sent_votes[self_rec.view] + List(Val(DUMMY_BLOCK))
    )


@action(inline=False)
def byzantine_replica_step(c: Context[MinimmitState]):
    """Byzantine behavior: the adversary injects arbitrary votes, certificates,
    and proposals into the network."""
    s = c.state
    c.assume(~s.BYZANTINE.is_empty)

    all_blks = all_blocks(s)
    # Capture sent_vote before modification: the cert block reads the original
    # value (matching Quint's parallel-assignment semantics for the three sub-actions).
    orig_sent_vote = s.sent_vote

    # ── Inject arbitrary well-typed vote messages ─────────────────────────────
    # nondet senders = BYZANTINE.powerset().oneOf()  (may be empty → 0 votes injected)
    # votes = senders.map(sender => {view, block, sig: sender, k})  → {} or {vote}
    with (
        c.one_of(AllSubsets(s.BYZANTINE), "byz_senders") as byz_senders,
        c.one_of(s.VIEWS, "byz_view") as byz_view,
        c.one_of(all_blks | Set(Val(DUMMY_BLOCK)), "byz_block") as byz_block,
        c.one_of(Set(VoteKind.NOTARIZE, VoteKind.NULLIFY), "byz_k") as byz_k,
    ):
        byz_votes = Set(
            Vote(  # type: ignore[return-value]
                view=byz_view, block=byz_block, sig=sender, kind=byz_k
            )
            for sender in byz_senders
        )
        s.sent_vote |= byz_votes

    # ── Inject arbitrary certificates ─────────────────────────────────────────
    with (
        c.one_of(s.BYZANTINE, "cert_sender") as cert_sender,
        c.one_of(s.VIEWS, "cert_view") as cert_view,
        c.one_of(all_blks, "cert_block") as cert_block,
        c.one_of(
            Set(VoteKind.NOTARIZE, VoteKind.NULLIFY), "cert_kind_v"
        ) as cert_kind_v,
    ):
        cert_votes = SetIf(
            (v.view == cert_view) & (v.block == cert_block) & (v.kind == cert_kind_v)
            for v in orig_sent_vote
        )
        with c.one_of(
            AllSubsets(Set(v.sig for v in cert_votes) | s.BYZANTINE), "byz_agg_sig"
        ) as byz_agg_sig:
            byz_cert_kind = (
                Val(CertificateKind.NULLIFICATION)
                .if_(cert_kind_v == VoteKind.NULLIFY)
                .else_(
                    Val(CertificateKind.FINALIZATION)
                    .if_(cert_votes.size >= L_quorum(s))
                    .else_(Val(CertificateKind.NOTARIZATION))
                )
            )
            s.sent_certificate |= Set(
                mk_certificate(
                    cert_view, cert_block, byz_agg_sig, cert_sender, byz_cert_kind
                )
            )

    # ── Inject arbitrary proposals ────────────────────────────────────────────
    with (
        c.one_of(s.VIEWS, "prop_view_parent") as prop_view_parent,
        c.one_of(s.VIEWS, "prop_view") as prop_view,
        c.one_of(s.BYZANTINE, "prop_sig") as prop_sig,
        c.one_of(all_blks, "prop_block") as prop_block,
        c.one_of(all_blks, "prop_block_parent") as prop_block_parent,
    ):
        s.sent_proposal |= Set(
            mk_proposal(
                prop_view, prop_view_parent, prop_block, prop_block_parent, prop_sig
            )
        )

    # replica_state, store_vote, store_certificate, ghost_committed_blocks,
    # ghost_proposal, leader are unchanged (implicit in wunderspec).


@action(inline=False)
def correct_replica_step(c: Context[MinimmitState]):
    """A step by a correct replica (all actions except the proposer step)."""
    s = c.state
    with c.one_of(s.CORRECT, "id") as replica_id:
        alts = iter(
            c.alternatives(
                "Timer",
                "Proposal",
                "VoteNotarize",
                "VoteNullify",
                "NullifyContradiction",
                "Certificate",
            )
        )

        with next(alts):
            on_timer_expired(c, replica_id)

        with next(alts):
            c.assume(~s.sent_proposal.is_empty)
            with c.one_of(s.sent_proposal, "p") as p:
                on_proposal(c, replica_id, p)

        with next(alts):
            with c.one_of(AllSubsets(s.sent_vote), "votes") as votes:
                c.assume(~votes.is_empty)
                with (
                    c.one_of(s.VIEWS, "view") as view,
                    c.one_of(all_blocks(s), "block") as block,
                ):
                    on_vote_notarize(c, replica_id, view, block, votes)

        with next(alts):
            with c.one_of(AllSubsets(s.sent_vote), "null_votes") as null_votes:
                c.assume(~null_votes.is_empty)
                with c.one_of(s.VIEWS, "null_view") as null_view:
                    on_vote_nullify(c, replica_id, null_view, null_votes)

        with next(alts):
            with c.one_of(s.VIEWS, "contra_view") as contra_view:
                on_nullify_by_contradiction(c, replica_id, contra_view)

        with next(alts):
            c.assume(~s.sent_certificate.is_empty)
            with c.one_of(s.sent_certificate, "cert") as cert:
                on_certificate(c, replica_id, cert)


@action
def step(c: Context[MinimmitState]):
    """A step by any correct or byzantine replica."""
    s = c.state
    alts = iter(c.alternatives("CorrectStep", "ProposerStep", "ByzantineStep"))

    with next(alts):
        correct_replica_step(c)

    with next(alts):
        with c.one_of(s.CORRECT, "prop_id") as prop_id:
            c.assume(prop_id == s.leader[s.replica_state[prop_id].view])
            with (
                # Non-deterministically choose the next block, use it only for the case of None below.
                c.one_of(s.VALID_BLOCKS, "new_block") as new_block,
                # Non-deterministically choose the parent block and parent view (checked in proposer_step)
                c.one_of(
                    all_blocks(s) | Set(Val(GENESIS_BLOCK)), "parent_block"
                ) as parent_block,
                c.one_of(
                    s.VIEWS | Set(Val(GENESIS_VIEW)), "parent_view"
                ) as parent_view,
            ):
                proposer_step(c, prop_id, new_block, parent_block, parent_view)

    with next(alts):
        byzantine_replica_step(c)
        # replica_state, store_vote, store_certificate, ghost_committed_blocks,
        # ghost_proposal, leader are unchanged (implicit: not assigned above).


# ── Invariants ────────────────────────────────────────────────────────────────


@invariant
def assumptions_valid(s: MinimmitState) -> BoolExpr:
    """val assumptions_valid: protocol parameter sanity checks."""
    return And(
        s.CORRECT.size + s.BYZANTINE.size == s.N,
        (s.CORRECT & s.BYZANTINE).is_empty,
        s.N >= Val(5) * s.F + Val(1),
        # Liveness guard: 2M ≤ L + 1
        Val(2) * M_quorum(s) <= L_quorum(s) + Val(1),
        # Every M-quorum and every L-quorum share at least one honest replica
        M_quorum(s) + L_quorum(s) > s.N + s.F,
    )


@invariant
def agreement(s: MinimmitState) -> BoolExpr:
    """val agreement: no two correct replicas disagree on the sequence of committed blocks."""

    def check_pair(p1: Expr, p2: Expr) -> BoolExpr:
        blocks1 = s.ghost_committed_blocks[p1]
        blocks2 = s.ghost_committed_blocks[p2]
        return Or(
            blocks1.size > blocks2.size,
            # blocks1 is a prefix of blocks2
            Forall(blocks1[i] == blocks2[i] for i in blocks1.keys),
        )

    return Forall(check_pair(p1, p2) for p1 in s.CORRECT for p2 in s.CORRECT)


def votes_seq_correct(votes: Expr) -> BoolExpr:
    """A replica sends at most one notarize vote then at most one nullify vote per view."""
    return Or(
        (votes.size == Val(2))
        & (votes[0] != Val(DUMMY_BLOCK))
        & (votes[1] == Val(DUMMY_BLOCK)),
        votes.size <= Val(1),
    )


@invariant
def no_vote_equivocation_inv(s: MinimmitState) -> BoolExpr:
    """A correct replica should not send two votes in the same view.
    Honest replicas may not broadcast a notarize(c, v) after first
    broadcasting a nullify(v)."""
    return Forall(
        votes_seq_correct(s.replica_state[replica_id].ghost_sent_votes[v])
        for replica_id in s.CORRECT
        for v in s.VIEWS
    )


@invariant
def no_nullification_and_finalization_in_same_view(s: MinimmitState) -> BoolExpr:
    """It is impossible to produce both a nullification and finalization
    certificate for the same slot v. If some honest player sees that iteration
    h is finalized, then no honest player will ever see that h is nullified."""

    def check(replica_id: Expr) -> BoolExpr:
        certs = s.store_certificate[replica_id]
        votes = s.store_vote[replica_id]
        views_nullified = Set(
            cert.view
            for cert in SetIf(
                cert.kind == CertificateKind.NULLIFICATION for cert in certs
            )
        )
        views_finalized = SetIf(
            Exists(
                is_view_finalized_votes(s, v, votes, block) for block in all_blocks(s)
            )
            for v in s.VIEWS
        )
        return (views_nullified & views_finalized).is_empty

    return Forall(check(replica_id) for replica_id in s.CORRECT)


@invariant
def no_proposal_equivocation(s: MinimmitState) -> BoolExpr:
    """No proposal equivocation: honest replicas send at most one proposal per view."""
    return Forall(
        Or(
            ~((m1.view == m2.view) & (m1.sig == m2.sig)),
            s.BYZANTINE.contains(m1.sig),
            m1.block == m2.block,
        )
        for m1 in s.sent_proposal
        for m2 in s.sent_proposal
    )


@invariant
def valid_last_finalized(s: MinimmitState) -> BoolExpr:
    """val valid_last_finalized: last_finalized ≤ ghost_last_seen_notarization."""
    return Forall(
        s.replica_state[replica_id].last_finalized
        <= s.replica_state[replica_id].ghost_last_seen_notarization
        for replica_id in s.CORRECT
    )


@invariant
def certificates_are_valid_inv(s: MinimmitState) -> BoolExpr:
    """Make sure that no invalid certificates are stored."""

    def cert_valid(cert: Expr) -> BoolExpr:
        return And(
            cert.view >= Val(0),
            Or(
                And(
                    cert.signatures.size >= M_quorum(s),
                    Or(
                        cert.kind == CertificateKind.NOTARIZATION,
                        cert.kind == CertificateKind.NULLIFICATION,
                    ),
                ),
                And(
                    cert.signatures.size >= L_quorum(s),
                    cert.kind == CertificateKind.FINALIZATION,
                ),
            ),
            Or(
                cert.kind == CertificateKind.NOTARIZATION,
                cert.kind == CertificateKind.NULLIFICATION,
                cert.kind == CertificateKind.FINALIZATION,
            ),
            (cert.block == Val(DUMMY_BLOCK))
            == (cert.kind == CertificateKind.NULLIFICATION),
            s.VALID_BLOCKS.contains(cert.block) | (cert.block == Val(DUMMY_BLOCK)),
        )

    return Forall(
        Forall(cert_valid(cert) for cert in s.store_certificate[replica_id])
        for replica_id in s.CORRECT
    )


@invariant
def notarized_consistency(s: MinimmitState) -> BoolExpr:
    """val notarized_consistency: replica's notarized map agrees with its sent votes."""
    return Forall(
        (s.replica_state[replica_id].notarized[v] != Val(EMPTY_BLOCK))
        == has_notarized_view(replica_id, v, s.sent_vote)
        for replica_id in s.CORRECT
        for v in s.VIEWS
    )


@invariant
def validity(s: MinimmitState) -> BoolExpr:
    """Suppose that a block B for some slot v is finalized, then no other
    block B' for slot v can be finalized."""

    def check(id1: Expr, id2: Expr, v: Expr) -> BoolExpr:
        certs1 = SetIf(
            And(
                cert.signatures.size >= L_quorum(s),
                cert.kind == CertificateKind.FINALIZATION,
                cert.view == v,
            )
            for cert in s.store_certificate[id1]
        )
        certs2 = SetIf(
            And(
                cert.signatures.size >= L_quorum(s),
                cert.kind == CertificateKind.FINALIZATION,
                cert.view == v,
            )
            for cert in s.store_certificate[id2]
        )
        return Or(
            certs1.is_empty,
            certs2.is_empty,
            Set(cert.block for cert in certs1) == Set(cert.block for cert in certs2),
        )

    return Forall(
        check(id1, id2, v) for id1 in s.CORRECT for id2 in s.CORRECT for v in s.VIEWS
    )


@invariant
def no_nullification_in_finalized_view(s: MinimmitState) -> BoolExpr:
    """If there is a finalized block in a view v, there is no nullification in the same view."""

    def check(replica_id: Expr, v: Expr) -> BoolExpr:
        votes = s.store_vote[replica_id]
        return Or(
            Forall(~is_view_finalized_votes(s, v, votes, b) for b in all_blocks(s)),
            Set(vote.sig for vote in select_votes(v, Val(VoteKind.NULLIFY), votes)).size
            < M_quorum(s),
        )

    return Forall(check(replica_id, v) for replica_id in s.CORRECT for v in s.VIEWS)


@invariant
def no_notarization_in_finalized_view(s: MinimmitState) -> BoolExpr:
    """If there is a finalized block in a view v,
    there is no notarization for another block in the same view."""

    def check(replica_id: Expr, v: Expr) -> BoolExpr:
        votes = s.store_vote[replica_id]
        return Forall(
            Or(
                ~is_view_finalized_votes(s, v, votes, b),
                Set(
                    vote.sig
                    for vote in SetIf(
                        vote.block != b
                        for vote in select_votes(v, Val(VoteKind.NOTARIZE), votes)
                    )
                ).size
                < M_quorum(s),
            )
            for b in all_blocks(s)
        )

    return Forall(check(replica_id, v) for replica_id in s.CORRECT for v in s.VIEWS)


@invariant
def safe_finalization(s: MinimmitState) -> BoolExpr:
    return And(
        no_notarization_in_finalized_view(s),
        no_nullification_in_finalized_view(s),
    )


@invariant
def all_invariants(s: MinimmitState) -> BoolExpr:
    return And(
        no_proposal_equivocation(s),
        agreement(s),
        no_vote_equivocation_inv(s),
        no_nullification_and_finalization_in_same_view(s),
        validity(s),
        valid_last_finalized(s),
        certificates_are_valid_inv(s),
        notarized_consistency(s),
        safe_finalization(s),
    )


# ── Example predicates (Quint "Test invariants") ──────────────────────────────
# Negate these invariants with a model checker to obtain example traces.


@invariant
def block_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace where some replica commits a block."""
    return ~Exists(
        s.ghost_committed_blocks[replica_id].size >= Val(1) for replica_id in s.CORRECT
    )


@invariant
def finalized_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace with last_finalized ≥ 0."""
    return ~Exists(
        s.replica_state[replica_id].last_finalized >= Val(0) for replica_id in s.CORRECT
    )


@invariant
def notarized_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace with a seen notarization."""
    return ~Exists(
        s.replica_state[replica_id].ghost_last_seen_notarization >= Val(0)
        for replica_id in s.CORRECT
    )


@invariant
def two_chained_blocks_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace with two committed blocks."""
    return ~Exists(
        s.ghost_committed_blocks[replica_id].size >= Val(2) for replica_id in s.CORRECT
    )


@invariant
def one_vote_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace where some replica has a vote."""
    return Forall(s.store_vote[replica_id].size <= Val(0) for replica_id in s.CORRECT)


@invariant
def votes_subquorum_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace with more than M votes."""
    return Forall(
        s.store_vote[replica_id].size <= M_quorum(s) for replica_id in s.CORRECT
    )


@invariant
def votes_quorum_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace with an L-quorum of votes."""
    return Forall(
        s.store_vote[replica_id].size <= L_quorum(s) for replica_id in s.CORRECT
    )


@invariant
def cert_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace where a replica has more than one certificate."""
    return Forall(
        s.store_certificate[replica_id].size <= Val(1) for replica_id in s.CORRECT
    )


@invariant
def view_example(s: MinimmitState) -> BoolExpr:
    """check this to find a trace where a replica reaches view ≥ 2."""
    return Forall(s.replica_state[replica_id].view < Val(2) for replica_id in s.CORRECT)


# ── Instances ─────────────────────────────────────────────────────────────────
# In these instances, REPLICA_KEYS is the identity map (each replica signs with
# its own name), which is the standard simplification for protocol verification.


def minimmit_instance(n: int, f: int, correct: Expr, byzantine: Expr) -> MinimmitState:
    """Build a bounded Minimmit instance with identity replica keys."""
    all_replicas = correct | byzantine
    return MinimmitState(
        N=n,
        F=f,
        CORRECT=correct,
        BYZANTINE=byzantine,
        VIEWS=Set(Val(0), ..., Val(6)),
        VALID_BLOCKS=Set("val_b0", "val_b1", "val_b2"),
        INVALID_BLOCKS=Set("inval_0", "inval_1"),
        REPLICA_KEYS=all_replicas.map_to(lambda k: k),
    )


@instance
def n6_t1_f0() -> MinimmitState:
    """N=6, F=1: 6 correct, 0 byzantine."""
    correct = Set("n0", "n1", "n2", "n3", "n4", "n5")
    byzantine = Set(str)
    return minimmit_instance(6, 1, correct, byzantine)


@instance
def n6_t1_f1() -> MinimmitState:
    """N=6, F=1: 5 correct, 1 byzantine."""
    correct = Set("n0", "n1", "n2", "n3", "n4")
    byzantine = Set("n5")
    return minimmit_instance(6, 1, correct, byzantine)


@instance
def n6_t1_f2() -> MinimmitState:
    """N=6, F=2: 4 correct, 2 byzantine."""
    correct = Set("n0", "n1", "n2", "n3")
    byzantine = Set("n4", "n5")
    return minimmit_instance(6, 2, correct, byzantine)


@instance
def n7_t1_f1() -> MinimmitState:
    """N=7, F=1: 6 correct, 1 byzantine."""
    correct = Set("n0", "n1", "n2", "n3", "n4", "n5")
    byzantine = Set("n6")
    return minimmit_instance(7, 1, correct, byzantine)


# ── Coverage ───────────────────────────────────────────────────────────────────


@coverage
def state_cov(s: MinimmitState) -> Expr:
    """Coverage predicate: a tuple of all mutable state variables."""
    return Tuple(
        s.replica_state,
        s.leader,
        s.sent_proposal,
        s.sent_vote,
        s.sent_certificate,
        s.store_vote,
        s.store_certificate,
        s.ghost_proposal,
        s.ghost_committed_blocks,
    )


@coverage
def min_cov(s: MinimmitState) -> Expr:
    """A tighter coverage predicate that excludes messages."""
    return Tuple(
        s.replica_state,
        s.leader,
        s.store_vote,
        s.store_certificate,
        s.ghost_proposal,
        s.ghost_committed_blocks,
    )
