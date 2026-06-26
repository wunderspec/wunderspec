"""
etcd-raft consensus protocol in Wunderspec.

A translation of the etcd-raft TLA+ specification:

  https://github.com/etcd-io/raft/blob/main/tla/etcdraft.tla

The companion comments below (``# TLA+: <Name>  etcdraft.tla:<line>``) point at
the matching operator in that file.  Line numbers refer to the upstream ``main``
revision as of 2024-04-18 and the definitions are ordered to follow the source
as closely as Python's define-before-use rule allows, so the two files can be
read side by side.

Original Raft paper:

  Diego Ongaro and John Ousterhout. In Search of an Understandable Consensus
  Algorithm. USENIX ATC 2014. https://raft.github.io/raft.pdf

This module covers the full ``NextDynamic`` transition relation: leader
election, log replication, commit advancement, the four message handlers,
crash/restart with durable state, joint-config reconfiguration (add/delete
server, add learner, apply simple conf change), snapshots, and the unreliable
network actions (duplicate/drop).

Message model.  The original spec keeps two *bags* of messages, ``messages``
and ``pendingMessages``, flushed by ``Ready(i)``.  We reuse the user-space
``Bag`` (multiset) primitive from ``examples/bags.py`` for both.  ``WithMessage``
/ ``WithoutMessage`` map to ``Bag.add_one`` / ``Bag.remove_one``,
``BagToSet``/``DOMAIN`` to ``Bag.to_set``, and ``messages[m]`` (CopiesIn) to
``Bag.__getitem__``.

Message types.  The four RPC shapes are an ``@union Payload`` of typed bodies,
wrapped in an envelope ``Message`` record carrying the common fields
(``mterm``/``msource``/``mdest``).  The payload tag is the message type.

Deviations from the source (for bounded model checking only): terms, log
length, and reconfiguration count are capped by the ``MaxTerm``/``MaxLogLen``/
``MaxReconfig`` parameters via ``c.assume`` guards on the space-growing actions.
Server ids are ``1..N`` and ``Nil`` is ``0``.

Igor Konnov, 2026 (done with Claude Opus 4.8)
"""

from enum import Enum, auto

from bags import Bag

from wunderspec import (
    AllSubsets,
    And,
    Expr,
    Field,
    Forall,
    Implies,
    Interval,
    Ite,
    List,
    Map,
    Or,
    Set,
    SetIf,
    Tuple,
    Val,
    Variant,
    record,
    union,
)
from wunderspec.expr import UnionExpr
from wunderspec.machine import (
    Context,
    MachineStateBase,
    Param,
    StateVar,
    action,
    coverage,
    instance,
    invariant,
    state,
)

# =============================================================================
# Enumerations
# =============================================================================


# TLA+: CONSTANTS Follower, Candidate, Leader  etcdraft.tla:34
# (the DistinctRoles ASSUME at etcdraft.tla:757 is guaranteed by the enum)
class ServerState(Enum):
    FOLLOWER = auto()
    CANDIDATE = auto()
    LEADER = auto()


# TLA+: CONSTANT ValueEntry, ConfigEntry  etcdraft.tla:31
class EntryType(Enum):
    VALUE = auto()  # ValueEntry
    CONFIG = auto()  # ConfigEntry


# TLA+: msubtype string literals "app"/"heartbeat"/"snapshot"  etcdraft.tla:339
# Message subtypes for AppendEntries (kept as strings, matching the source).
APP = "app"
HEARTBEAT = "heartbeat"
SNAPSHOT = "snapshot"


# =============================================================================
# Records and the message union
# =============================================================================
# TLA+ models these as @typeAlias annotations (etcdraft.tla:65-71) and inline
# record literals; Python needs them declared up front.


@record
class EntryValue:
    """Unified payload of a log entry.

    ValueEntry uses ``val``; ConfigEntry uses ``newconf`` and ``learners``.

    TLA+: entry value shape, [val |-> v] / [newconf |-> .., learners |-> ..]
    etcdraft.tla:395, etcdraft.tla:431
    """

    val: Field[int]
    newconf: Field[set[int]]
    learners: Field[set[int]]


# TLA+: ENTRY typeAlias [term, value]  etcdraft.tla:65 (entry record at :386)
@record
class LogEntry:
    term: Field[int]
    type: Field[EntryType]
    value: Field[EntryValue]


@record
class Config:
    """A (possibly joint) configuration.

    ``incoming``/``outgoing`` are the two halves of ``jointConfig``; the source
    only ever produces single-stage configs (``outgoing = {}``).

    TLA+: config value [jointConfig, learners]  etcdraft.tla:132 (init at :251)
    """

    incoming: Field[set[int]]
    outgoing: Field[set[int]]
    learners: Field[set[int]]


@record
class Durable:
    """The persisted snapshot of a server's state.

    TLA+: durableState record  etcdraft.tla:230-236
    """

    currentTerm: Field[int]
    votedFor: Field[int]
    log: Field[int]  # Len(log) at persist time
    commitIndex: Field[int]
    config: Field[Config]


# TLA+: RVREQT typeAlias  etcdraft.tla:67
@record
class RVReq:
    mlastLogTerm: Field[int]
    mlastLogIndex: Field[int]


# TLA+: RVRESPT typeAlias  etcdraft.tla:68
@record
class RVResp:
    mvoteGranted: Field[bool]


# TLA+: AEREQT typeAlias  etcdraft.tla:69
@record
class AEReq:
    msubtype: Field[str]
    mprevLogIndex: Field[int]
    mprevLogTerm: Field[int]
    mentries: Field[list[LogEntry]]
    mcommitIndex: Field[int]


# TLA+: AERESPT typeAlias  etcdraft.tla:70
@record
class AEResp:
    msubtype: Field[str]
    msuccess: Field[bool]
    mmatchIndex: Field[int]


# TLA+: CONSTANTS RequestVoteRequest/Response, AppendEntriesRequest/Response
# etcdraft.tla:48-56 (the union tag plays the role of the mtype field)
@union
class Payload:
    RequestVoteReq: Variant[RVReq]
    RequestVoteResp: Variant[RVResp]
    AppendEntriesReq: Variant[AEReq]
    AppendEntriesResp: Variant[AEResp]


# TLA+: MSG typeAlias (message envelope)  etcdraft.tla:71
@record
class Message:
    mterm: Field[int]
    msource: Field[int]
    mdest: Field[int]
    payload: Field[Payload]


# Wunderspec-only: tagged-union accessors. TLA+ reads record fields directly
# off an untyped message; here each RPC body is narrowed out of the union.
# Dummy bodies used as the (unreachable) default of a tag-narrowed match.
_DUMMY_RVREQ = RVReq(mlastLogTerm=Val(0), mlastLogIndex=Val(0))
_DUMMY_RVRESP = RVResp(mvoteGranted=Val(False))
_DUMMY_AEREQ = AEReq(
    msubtype=Val(APP),
    mprevLogIndex=Val(0),
    mprevLogTerm=Val(0),
    mentries=List(LogEntry),
    mcommitIndex=Val(0),
)
_DUMMY_AERESP = AEResp(msubtype=Val(APP), msuccess=Val(False), mmatchIndex=Val(0))


def _payload(m: Expr) -> UnionExpr:
    return UnionExpr(m.payload.node)


def as_rvreq(m: Expr) -> Expr:
    return _payload(m).match(RequestVoteReq=lambda x: x, default=_DUMMY_RVREQ)


def as_rvresp(m: Expr) -> Expr:
    return _payload(m).match(RequestVoteResp=lambda x: x, default=_DUMMY_RVRESP)


def as_aereq(m: Expr) -> Expr:
    return _payload(m).match(AppendEntriesReq=lambda x: x, default=_DUMMY_AEREQ)


def as_aeresp(m: Expr) -> Expr:
    return _payload(m).match(AppendEntriesResp=lambda x: x, default=_DUMMY_AERESP)


# Message constructors --------------------------------------------------------
# Wunderspec-only: TLA+ builds these as inline record literals (e.g. the
# RequestVote send at etcdraft.tla:307, AppendEntriesInRangeToPeer at :340).


def mk_request_vote_req(term, last_log_term, last_log_index, src, dst) -> Expr:
    return Message(
        mterm=term,
        msource=src,
        mdest=dst,
        payload=Payload.RequestVoteReq(  # type: ignore[attr-defined]
            RVReq(mlastLogTerm=last_log_term, mlastLogIndex=last_log_index)
        ),
    )


def mk_request_vote_resp(term, vote_granted, src, dst) -> Expr:
    return Message(
        mterm=term,
        msource=src,
        mdest=dst,
        payload=Payload.RequestVoteResp(  # type: ignore[attr-defined]
            RVResp(mvoteGranted=vote_granted)
        ),
    )


def mk_append_entries_req(
    subtype, term, prev_log_index, prev_log_term, entries, commit, src, dst
) -> Expr:
    return Message(
        mterm=term,
        msource=src,
        mdest=dst,
        payload=Payload.AppendEntriesReq(  # type: ignore[attr-defined]
            AEReq(
                msubtype=subtype,
                mprevLogIndex=prev_log_index,
                mprevLogTerm=prev_log_term,
                mentries=entries,
                mcommitIndex=commit,
            )
        ),
    )


def mk_append_entries_resp(subtype, term, success, match_index, src, dst) -> Expr:
    return Message(
        mterm=term,
        msource=src,
        mdest=dst,
        payload=Payload.AppendEntriesResp(  # type: ignore[attr-defined]
            AEResp(msubtype=subtype, msuccess=success, mmatchIndex=match_index)
        ),
    )


# =============================================================================
# State
# =============================================================================
# TLA+: VARIABLE declarations  etcdraft.tla:60-147 (field order follows them)


@state
class EtcdRaftState(MachineStateBase):
    """Global state of the etcd-raft protocol."""

    # Parameters
    Server: Param[set[int]]  # TLA+: CONSTANT Server  :28
    InitServer: Param[set[int]]  # TLA+: CONSTANT InitServer  :28
    Nil: Param[int]  # TLA+: CONSTANT Nil  :45
    MaxTerm: Param[int]  # Wunderspec-only: BMC bound (no TLA+ constant)
    MaxLogLen: Param[int]  # Wunderspec-only: BMC bound (no TLA+ constant)
    MaxReconfig: Param[int]  # Wunderspec-only: BMC bound (no TLA+ constant)

    # Message bags (element -> count)
    messages: StateVar[dict[Message, int]]  # TLA+: VARIABLE messages  :73
    pendingMessages: StateVar[dict[Message, int]]  # TLA+: pendingMessages  :75

    # Per-server volatile state (serverVars :94)
    currentTerm: StateVar[dict[int, int]]  # TLA+: VARIABLE currentTerm  :84
    state: StateVar[dict[int, ServerState]]  # TLA+: VARIABLE state  :88
    votedFor: StateVar[dict[int, int]]  # TLA+: VARIABLE votedFor  :93
    log: StateVar[dict[int, list[LogEntry]]]  # TLA+: VARIABLE log  :101
    commitIndex: StateVar[dict[int, int]]  # TLA+: VARIABLE commitIndex  :105

    # Candidate state (candidateVars :120)
    votesResponded: StateVar[dict[int, set[int]]]  # TLA+: votesResponded  :113
    votesGranted: StateVar[dict[int, set[int]]]  # TLA+: votesGranted  :118

    # Leader state (leaderVars :130)
    matchIndex: StateVar[dict[int, dict[int, int]]]  # TLA+: matchIndex  :127
    pendingConfChangeIndex: StateVar[dict[int, int]]  # TLA+: :129

    # Configuration (configVars :138)
    config: StateVar[dict[int, Config]]  # TLA+: VARIABLE config  :134
    reconfigCount: StateVar[int]  # TLA+: VARIABLE reconfigCount  :136

    # Durable (persisted) state
    durableState: StateVar[dict[int, Durable]]  # TLA+: VARIABLE durableState  :141


# =============================================================================
# Helpers
# =============================================================================
# TLA+: the "Helpers" block  etcdraft.tla:150-236


def is_quorum(subset: Expr, conf: Expr) -> Expr:
    """``subset \\in Quorum(conf)`` -- a majority subset of ``conf``.

    TLA+: Quorum(c)  etcdraft.tla:155
    """
    return subset.issubset(conf) & (subset.size * 2 > conf.size)


def quorums(conf: Expr) -> Expr:
    """``Quorum(conf)`` -- the set of all majority subsets of ``conf``.

    TLA+: Quorum(c)  etcdraft.tla:155
    """
    return SetIf(subset.size * 2 > conf.size for subset in AllSubsets(conf))


def last_term(log: Expr) -> Expr:
    """``LastTerm(log)`` -- term of the last entry, or 0 if empty.

    TLA+: LastTerm(xlog)  etcdraft.tla:159
    """
    return Val(0).if_(log.is_empty).else_(log[log.size - 1].term)


def min2(a: Expr, b: Expr) -> Expr:
    # TLA+: inline Min({a, b})  (FiniteSetsExt), e.g. etcdraft.tla:337
    return a.if_(a < b).else_(b)


def max2(a: Expr, b: Expr) -> Expr:
    # TLA+: inline Max({a, b})  (Integers), e.g. etcdraft.tla:225
    return a.if_(a > b).else_(b)


def positions(log: Expr) -> Expr:
    """1-based index set ``1..Len(log)`` (``DOMAIN log`` in the source).

    TLA+: inline DOMAIN log, e.g. etcdraft.tla:804
    """
    return Set(k + 1 for k in log.keys)  # log keys are 0-based


def entry_at(log: Expr, n: Expr) -> Expr:
    """``log[n]`` for a 1-based index ``n`` (lists are 0-based here).

    TLA+: inline log[i][n] 1-based indexing (lists here are 0-based).
    """
    return log[n - 1]


def is_prefix(a: Expr, b: Expr) -> Expr:
    """Whether list ``a`` is a prefix of list ``b``.

    TLA+: IsPrefix(s, t)  (SequencesExt module), e.g. etcdraft.tla:783
    """
    return (a.size <= b.size) & Forall(a[k] == b[k] for k in a.keys)


@action(inline=True)
def send(c: Context[EtcdRaftState], m: Expr):
    """``Send(m)`` -- queue a message into ``pendingMessages``.

    TLA+: Send(m) / SendDirect(m)  etcdraft.tla:199 / :172
    (WithMessage etcdraft.tla:164 folds into Bag.add_one)
    """
    s = c.state
    s.pendingMessages = Bag(s.pendingMessages).add_one(m).as_map


@action(inline=True)
def discard(c: Context[EtcdRaftState], m: Expr):
    """``Discard(m)`` -- remove a message from ``messages``.

    TLA+: Discard(m) / DiscardDirect(m)  etcdraft.tla:201 / :190
    (WithoutMessage etcdraft.tla:169 folds into Bag.remove_one)
    """
    s = c.state
    s.messages = Bag(s.messages).remove_one(m).as_map


@action(inline=True)
def reply(c: Context[EtcdRaftState], response: Expr, request: Expr):
    """``Reply(response, request)`` -- queue ``response``, drop ``request``.

    TLA+: Reply(response, request) / ReplyDirect  etcdraft.tla:200 / :194
    """
    s = c.state
    s.pendingMessages = Bag(s.pendingMessages).add_one(response).as_map
    s.messages = Bag(s.messages).remove_one(request).as_map


def pending_of(s: EtcdRaftState, i: Expr) -> Bag:
    """``PendingMessages(i)`` -- the sub-bag of ``i``-sourced pending messages.

    TLA+: PendingMessages(i)  etcdraft.tla:176
    """
    return Bag(s.pendingMessages).filter(lambda m: m.msource == i)


def max_or_zero(s_expr: Expr) -> Expr:
    """``MaxOrZero(S)`` -- max of a set of non-negative ints, 0 if empty.

    TLA+: MaxOrZero(s)  etcdraft.tla:203
    """
    return s_expr.reduce(lambda acc, x: x.if_(x > acc).else_(acc), Val(0))


def get_config(s: EtcdRaftState, i: Expr) -> Expr:
    # TLA+: GetConfig(i)  etcdraft.tla:208 (GetJointConfig :205 folds in)
    return s.config[i].incoming


def get_outgoing(s: EtcdRaftState, i: Expr) -> Expr:
    # TLA+: GetOutgoingConfig(i)  etcdraft.tla:211
    return s.config[i].outgoing


def is_joint(s: EtcdRaftState, i: Expr) -> Expr:
    # TLA+: IsJointConfig(i)  etcdraft.tla:214
    return ~s.config[i].outgoing.is_empty


def get_learners(s: EtcdRaftState, i: Expr) -> Expr:
    # TLA+: GetLearners(i)  etcdraft.tla:217
    return s.config[i].learners


@action(inline=True)
def commit_to(c: Context[EtcdRaftState], i: Expr, c_index: Expr):
    """``CommitTo(i, c)`` -- advance the commit index monotonically.

    TLA+: CommitTo(i, c)  etcdraft.tla:224
    """
    s = c.state
    s.commitIndex[i] = max2(s.commitIndex[i], c_index)


def current_leaders(s: EtcdRaftState) -> Expr:
    # TLA+: CurrentLeaders  etcdraft.tla:227
    return SetIf(s.state[i] == ServerState.LEADER for i in s.Server)


@action(inline=True)
def persist_state(c: Context[EtcdRaftState], i: Expr):
    """``PersistState(i)``.

    TLA+: PersistState(i)  etcdraft.tla:229
    """
    s = c.state
    s.durableState[i] = Durable(
        currentTerm=s.currentTerm[i],
        votedFor=s.votedFor[i],
        log=s.log[i].size,
        commitIndex=s.commitIndex[i],
        config=s.config[i],
    )


def committed(s: EtcdRaftState, i: Expr) -> Expr:
    """``Committed(i) == SubSeq(log[i], 1, commitIndex[i])``.

    TLA+: Committed(i)  etcdraft.tla:772 (defined late, in the invariants;
    kept here so callers can be defined before use)
    """
    return s.log[i][0 : s.commitIndex[i]]  # noqa: E203


def select_last_config(s: EtcdRaftState, i: Expr) -> Expr:
    """Largest committed index of a ConfigEntry, or 0 (SelectLastInSubSeq).

    TLA+: SelectLastInSubSeq(...) usage  etcdraft.tla:466 (SequencesExt)
    """
    return max_or_zero(
        Interval(Val(1), s.commitIndex[i]).filter(
            lambda k: entry_at(s.log[i], k).type == EntryType.CONFIG
        )
    )


def mk_value(v: Expr) -> Expr:
    """A ``ValueEntry`` payload ``[val |-> v]`` (config fields empty).

    TLA+: inline [val |-> v]  etcdraft.tla:395
    """
    return EntryValue(val=v, newconf=Set(int), learners=Set(int))  # type: ignore


def last_index_with_term(log: Expr, t: Expr) -> Expr:
    """``MaxOrZero({n \\in DOMAIN log : log[n].term = t})`` (1-based).

    TLA+: inline in ElectionSafetyInv  etcdraft.tla:804
    """
    return max_or_zero(positions(log).filter(lambda n: entry_at(log, n).term == t))


# =============================================================================
# Init
# =============================================================================


# TLA+: Init / InitMessageVars..InitDurableState  etcdraft.tla:262 / :240-260
@action(init=True)
def init(c: Context[EtcdRaftState]):
    s = c.state
    init_conf = Config(incoming=s.InitServer, outgoing=Set(int), learners=Set(int))

    # TLA+: InitMessageVars  etcdraft.tla:240
    s.messages = Bag.empty(Message).as_map
    s.pendingMessages = Bag.empty(Message).as_map

    # TLA+: InitServerVars  etcdraft.tla:242
    s.currentTerm = Map(Val(0) for _ in s.Server)
    s.state = Map(Val(ServerState.FOLLOWER) for _ in s.Server)
    s.votedFor = Map(s.Nil for _ in s.Server)

    # TLA+: InitLogVars  etcdraft.tla:249
    s.log = Map(List(LogEntry) for _ in s.Server)
    s.commitIndex = Map(Val(0) for _ in s.Server)

    # TLA+: InitCandidateVars  etcdraft.tla:245
    s.votesResponded = Map(Set(int) for _ in s.Server)
    s.votesGranted = Map(Set(int) for _ in s.Server)

    # TLA+: InitLeaderVars  etcdraft.tla:247
    s.matchIndex = Map(Map(Val(0) for _ in s.Server) for _ in s.Server)
    s.pendingConfChangeIndex = Map(Val(0) for _ in s.Server)

    # TLA+: InitConfigVars  etcdraft.tla:251
    s.config = Map(init_conf for _ in s.Server)
    s.reconfigCount = Val(0)

    # TLA+: InitDurableState  etcdraft.tla:253
    s.durableState = Map(
        Durable(
            currentTerm=Val(0),
            votedFor=s.Nil,
            log=Val(0),
            commitIndex=Val(0),
            config=init_conf,  # type: ignore
        )
        for _ in s.Server
    )


# =============================================================================
# State transitions
# =============================================================================
# TLA+: "Define state transitions"  etcdraft.tla:270-493


@action(inline=False)
def restart(c: Context[EtcdRaftState], i: Expr):
    # TLA+: Restart(i)  etcdraft.tla:276
    s = c.state
    d = s.durableState[i]
    s.state[i] = Val(ServerState.FOLLOWER)
    s.votesResponded[i] = Set(int)
    s.votesGranted[i] = Set(int)
    s.matchIndex[i] = Map(Val(0) for _ in s.Server)
    s.pendingConfChangeIndex[i] = Val(0)
    s.pendingMessages = (Bag(s.pendingMessages) - pending_of(s, i)).as_map
    s.currentTerm[i] = d.currentTerm
    s.commitIndex[i] = d.commitIndex
    s.votedFor[i] = d.votedFor
    s.log[i] = s.log[i][0 : d.log]  # noqa: E203
    s.config[i] = d.config


@action(inline=False)
def timeout(c: Context[EtcdRaftState], i: Expr):
    # TLA+: Timeout(i)  etcdraft.tla:292
    s = c.state
    c.assume(
        Or(s.state[i] == ServerState.FOLLOWER, s.state[i] == ServerState.CANDIDATE)
    )
    c.assume(get_config(s, i).contains(i))
    c.assume(s.currentTerm[i] < s.MaxTerm)  # bound: terms
    s.state[i] = Val(ServerState.CANDIDATE)
    s.currentTerm[i] = s.currentTerm[i] + 1
    s.votedFor[i] = i
    s.votesResponded[i] = Set(int)
    s.votesGranted[i] = Set(int)


@action(inline=False)
def request_vote(c: Context[EtcdRaftState], i: Expr, j: Expr):
    # TLA+: RequestVote(i, j)  etcdraft.tla:303
    s = c.state
    c.assume(s.state[i] == ServerState.CANDIDATE)
    c.assume(
        ((get_config(s, i) | get_learners(s, i)) - s.votesResponded[i]).contains(j)
    )
    send(
        c,
        mk_request_vote_req(s.currentTerm[i], last_term(s.log[i]), s.log[i].size, i, j)
        .if_(i != j)
        .else_(mk_request_vote_resp(s.currentTerm[i], Val(True), i, i)),
    )


@action(inline=False)
def append_entries_in_range_to_peer(
    c: Context[EtcdRaftState], subtype: Expr, i: Expr, j: Expr, b: Expr, e: Expr
):
    # TLA+: AppendEntriesInRangeToPeer(subtype, i, j, range)  etcdraft.tla:323
    s = c.state
    c.assume(i != j)
    c.assume(b <= e)
    c.assume(s.state[i] == ServerState.LEADER)
    c.assume(get_config(s, i).contains(j) | get_learners(s, i).contains(j))
    prev_log_index = b - 1
    prev_log_term = (
        entry_at(s.log[i], prev_log_index)
        .term.if_((prev_log_index > 0) & (prev_log_index <= s.log[i].size))
        .else_(Val(0))
    )
    last_entry = min2(s.log[i].size, e - 1)
    entries = s.log[i][b - 1 : last_entry]  # noqa: E203
    commit = (
        min2(s.commitIndex[i], s.matchIndex[i][j])
        .if_(subtype == HEARTBEAT)
        .else_(min2(s.commitIndex[i], last_entry))
    )
    send(
        c,
        mk_append_entries_req(
            subtype,
            s.currentTerm[i],
            prev_log_index,
            prev_log_term,
            entries,
            commit,
            i,
            j,
        ),
    )


@action(inline=False)
def append_entries_to_self(c: Context[EtcdRaftState], i: Expr):
    # TLA+: AppendEntriesToSelf(i)  etcdraft.tla:352
    s = c.state
    c.assume(s.state[i] == ServerState.LEADER)
    send(
        c,
        mk_append_entries_resp(
            Val(APP), s.currentTerm[i], Val(True), s.log[i].size, i, i
        ),
    )


@action(inline=False)
def append_entries(c: Context[EtcdRaftState], i: Expr, j: Expr, b: Expr, e: Expr):
    # TLA+: AppendEntries(i, j, range)  etcdraft.tla:363
    append_entries_in_range_to_peer(c, Val(APP), i, j, b, e)


@action(inline=False)
def heartbeat(c: Context[EtcdRaftState], i: Expr, j: Expr):
    # TLA+: Heartbeat(i, j)  etcdraft.tla:366
    append_entries_in_range_to_peer(c, Val(HEARTBEAT), i, j, Val(1), Val(1))


@action(inline=False)
def send_snapshot(c: Context[EtcdRaftState], i: Expr, j: Expr, index: Expr):
    # TLA+: SendSnapshot(i, j, index)  etcdraft.tla:370
    append_entries_in_range_to_peer(c, Val(SNAPSHOT), i, j, Val(1), index + 1)


@action(inline=False)
def become_leader(c: Context[EtcdRaftState], i: Expr):
    # TLA+: BecomeLeader(i)  etcdraft.tla:375
    s = c.state
    c.assume(s.state[i] == ServerState.CANDIDATE)
    c.assume(is_quorum(s.votesGranted[i], get_config(s, i)))
    s.state[i] = Val(ServerState.LEADER)
    s.matchIndex[i] = Map(s.log[i].size.if_(j == i).else_(Val(0)) for j in s.Server)


def _conf_change(
    c: Context[EtcdRaftState],
    i: Expr,
    new_incoming: Expr,
    new_learners: Expr,
):
    """Shared body of AddNewServer/AddLearner/DeleteServer.

    Replicates a ConfigEntry (or a filler ValueEntry if a conf change is
    already pending) and records the pending conf-change index.

    TLA+: Replicate(i, v, t)  etcdraft.tla:383 (plus the pendingConfChangeIndex
    bookkeeping shared by the three reconfiguration actions)
    """
    s = c.state
    c.assume(s.state[i] == ServerState.LEADER)
    c.assume(~is_joint(s, i))
    c.assume(s.log[i].size < s.MaxLogLen)  # bound: log length
    c.assume(s.reconfigCount < s.MaxReconfig)  # bound: reconfigurations
    old_pending = s.pendingConfChangeIndex[i]
    cond = old_pending == 0
    new_len = s.log[i].size + 1
    entry = Ite(
        cond,
        LogEntry(
            term=s.currentTerm[i],
            type=Val(EntryType.CONFIG),
            value=EntryValue(val=Val(0), newconf=new_incoming, learners=new_learners),
        ),
        LogEntry(
            term=s.currentTerm[i], type=Val(EntryType.VALUE), value=mk_value(Val(0))
        ),
    )
    s.log[i] = s.log[i] + List(entry)
    s.pendingConfChangeIndex[i] = Ite(cond, new_len, old_pending)


@action(inline=False)
def client_request(c: Context[EtcdRaftState], i: Expr):
    # TLA+: ClientRequest(i, v)  etcdraft.tla:394 (Replicate body at :383)
    s = c.state
    c.assume(s.state[i] == ServerState.LEADER)
    c.assume(s.log[i].size < s.MaxLogLen)  # bound: log length
    entry = LogEntry(
        term=s.currentTerm[i],
        type=Val(EntryType.VALUE),
        value=mk_value(Val(0)),
    )
    s.log[i] = s.log[i] + List(entry)


@action(inline=False)
def advance_commit_index(c: Context[EtcdRaftState], i: Expr):
    # TLA+: AdvanceCommitIndex(i)  etcdraft.tla:403
    s = c.state
    c.assume(s.state[i] == ServerState.LEADER)
    conf = get_config(s, i)

    def agree(index: Expr) -> Expr:
        return SetIf(s.matchIndex[i][k] >= index for k in conf)

    agree_indexes = SetIf(
        is_quorum(agree(index), conf) for index in positions(s.log[i])
    )
    max_agree = max_or_zero(agree_indexes)
    new_commit = max_agree.if_(
        And(
            ~agree_indexes.is_empty,
            entry_at(s.log[i], max_agree).term == s.currentTerm[i],
        )
    ).else_(s.commitIndex[i])
    commit_to(c, i, new_commit)


@action(inline=False)
def add_new_server(c: Context[EtcdRaftState], i: Expr, j: Expr):
    # TLA+: AddNewServer(i, j)  etcdraft.tla:426
    s = c.state
    c.assume(~get_config(s, i).contains(j))
    _conf_change(c, i, get_config(s, i) | Set(j), get_learners(s, i))


@action(inline=False)
def add_learner(c: Context[EtcdRaftState], i: Expr, j: Expr):
    # TLA+: AddLearner(i, j)  etcdraft.tla:439
    s = c.state
    c.assume(~(get_config(s, i) | get_learners(s, i)).contains(j))
    _conf_change(c, i, get_config(s, i), get_learners(s, i) | Set(j))


@action(inline=False)
def delete_server(c: Context[EtcdRaftState], i: Expr, j: Expr):
    # TLA+: DeleteServer(i, j)  etcdraft.tla:452
    s = c.state
    c.assume((get_config(s, i) | get_learners(s, i)).contains(j))
    _conf_change(c, i, get_config(s, i) - Set(j), get_learners(s, i) - Set(j))


@action(inline=False)
def apply_simple_conf_change(c: Context[EtcdRaftState], i: Expr):
    # TLA+: ApplySimpleConfChange(i)  etcdraft.tla:464
    s = c.state
    c.assume(~is_joint(s, i))
    k = select_last_config(s, i)
    c.assume(k > 0)
    c.assume(k <= s.commitIndex[i])
    entry = entry_at(s.log[i], k)
    s.config[i] = Config(
        incoming=entry.value.newconf,
        outgoing=Set(int),
        learners=entry.value.learners,
    )
    applied = (s.state[i] == ServerState.LEADER) & (s.pendingConfChangeIndex[i] >= k)
    s.reconfigCount = (s.reconfigCount + 1).if_(applied).else_(s.reconfigCount)
    s.pendingConfChangeIndex[i] = Val(0).if_(applied).else_(s.pendingConfChangeIndex[i])


@action(inline=False)
def ready(c: Context[EtcdRaftState], i: Expr):
    # TLA+: Ready(i)  etcdraft.tla:477 (SendPendingMessages :184)
    s = c.state
    persist_state(c, i)
    pend_i = pending_of(s, i)
    s.messages = (pend_i + Bag(s.messages)).as_map
    s.pendingMessages = (Bag(s.pendingMessages) - pend_i).as_map


@action(inline=True)
def become_follower_of_term(c: Context[EtcdRaftState], i: Expr, t: Expr):
    """``BecomeFollowerOfTerm(i, t)``.

    TLA+: BecomeFollowerOfTerm(i, t)  etcdraft.tla:482 (defined ahead of its
    callers StepDownToFollower / UpdateTerm so Python can reference it)
    """
    s = c.state
    old_term = s.currentTerm[i]
    # Compute votedFor from the old term before overwriting currentTerm; the
    # three writes target independent variables, so order is immaterial (in the
    # source all are primed assignments evaluated against the unprimed state).
    s.votedFor[i] = s.Nil.if_(old_term != t).else_(s.votedFor[i])
    s.state[i] = Val(ServerState.FOLLOWER)
    s.currentTerm[i] = t


@action(inline=False)
def step_down_to_follower(c: Context[EtcdRaftState], i: Expr):
    # TLA+: StepDownToFollower(i)  etcdraft.tla:490
    s = c.state
    c.assume((s.state[i] == ServerState.LEADER) | (s.state[i] == ServerState.CANDIDATE))
    become_follower_of_term(c, i, s.currentTerm[i])


# =============================================================================
# Message handlers
# =============================================================================
# TLA+: "Message handlers" -- i = recipient, j = sender, m = message
# etcdraft.tla:495-676


def handle_request_vote_request(c: Context[EtcdRaftState], i: Expr, j: Expr, m: Expr):
    # TLA+: HandleRequestVoteRequest(i, j, m)  etcdraft.tla:502
    s = c.state
    p = as_rvreq(m)
    log_ok = Or(
        p.mlastLogTerm > last_term(s.log[i]),
        And(
            p.mlastLogTerm == last_term(s.log[i]),
            p.mlastLogIndex >= s.log[i].size,
        ),
    )
    grant = And(
        m.mterm == s.currentTerm[i],
        log_ok,
        (s.votedFor[i] == s.Nil) | (s.votedFor[i] == j),
    )
    c.assume(m.mterm <= s.currentTerm[i])
    s.votedFor[i] = j.if_(grant).else_(s.votedFor[i])
    reply(c, mk_request_vote_resp(s.currentTerm[i], grant, i, j), m)


def handle_request_vote_response(c: Context[EtcdRaftState], i: Expr, j: Expr, m: Expr):
    # TLA+: HandleRequestVoteResponse(i, j, m)  etcdraft.tla:523
    s = c.state
    p = as_rvresp(m)
    c.assume(m.mterm == s.currentTerm[i])
    s.votesResponded[i] = s.votesResponded[i] | Set(j)
    s.votesGranted[i] = (
        (s.votesGranted[i] | Set(j)).if_(p.mvoteGranted).else_(s.votesGranted[i])
    )
    discard(c, m)


def reject_append_entries_request(
    c: Context[EtcdRaftState], i: Expr, j: Expr, m: Expr, log_ok: Expr
):
    # TLA+: RejectAppendEntriesRequest(i, j, m, logOk)  etcdraft.tla:538
    s = c.state
    c.assume(
        Or(
            m.mterm < s.currentTerm[i],
            And(
                m.mterm == s.currentTerm[i],
                s.state[i] == ServerState.FOLLOWER,
                ~log_ok,
            ),
        )
    )
    reply(
        c,
        mk_append_entries_resp(Val(APP), s.currentTerm[i], Val(False), Val(0), i, j),
        m,
    )


def return_to_follower_state(c: Context[EtcdRaftState], i: Expr, m: Expr):
    # TLA+: ReturnToFollowerState(i, m)  etcdraft.tla:554
    s = c.state
    c.assume(m.mterm == s.currentTerm[i])
    c.assume(s.state[i] == ServerState.CANDIDATE)
    s.state[i] = Val(ServerState.FOLLOWER)


def has_no_conflict(s: EtcdRaftState, i: Expr, index: Expr, ents: Expr) -> Expr:
    """``HasNoConflict(i, index, ents)``.

    TLA+: HasNoConflict(i, index, ents)  etcdraft.tla:560
    """
    return And(
        index <= s.log[i].size + 1,
        Forall(
            Implies(
                index + k - 1 <= s.log[i].size,
                entry_at(s.log[i], index + k - 1).term == entry_at(ents, k).term,
            )
            for k in Set(1, ..., ents.size)
        ),
    )


def append_entries_already_done(
    c: Context[EtcdRaftState], i: Expr, j: Expr, index: Expr, m: Expr, p: Expr
):
    # TLA+: AppendEntriesAlreadyDone(i, j, index, m)  etcdraft.tla:565
    s = c.state
    entries_len = p.mentries.size
    end = p.mprevLogIndex + entries_len
    c.assume(
        Or(
            index <= s.commitIndex[i],
            And(
                index > s.commitIndex[i],
                Or(
                    p.mentries.is_empty,
                    And(
                        ~p.mentries.is_empty,
                        end <= s.log[i].size,
                        has_no_conflict(s, i, index, p.mentries),
                    ),
                ),
            ),
        )
    )
    old_ci = s.commitIndex[i]
    s.commitIndex[i] = Ite(
        index <= old_ci,
        Ite(p.msubtype == HEARTBEAT, max2(old_ci, p.mcommitIndex), old_ci),
        max2(old_ci, min2(p.mcommitIndex, end)),
    )
    match_index = end.if_((p.msubtype == HEARTBEAT) | (index > old_ci)).else_(old_ci)
    reply(
        c,
        mk_append_entries_resp(
            p.msubtype, s.currentTerm[i], Val(True), match_index, i, j
        ),
        m,
    )


def conflict_append_entries_request(
    c: Context[EtcdRaftState], i: Expr, index: Expr, m: Expr, p: Expr
):
    # TLA+: ConflictAppendEntriesRequest(i, index, m)  etcdraft.tla:587
    s = c.state
    c.assume(~p.mentries.is_empty)
    c.assume(index > s.commitIndex[i])
    c.assume(~has_no_conflict(s, i, index, p.mentries))
    s.log[i] = s.log[i][0 : s.log[i].size - 1]  # noqa: E203


def no_conflict_append_entries_request(
    c: Context[EtcdRaftState], i: Expr, index: Expr, m: Expr, p: Expr
):
    # TLA+: NoConflictAppendEntriesRequest(i, index, m)  etcdraft.tla:595
    s = c.state
    c.assume(~p.mentries.is_empty)
    c.assume(index > s.commitIndex[i])
    c.assume(has_no_conflict(s, i, index, p.mentries))
    start = s.log[i].size - index + 1
    s.log[i] += p.mentries[start : p.mentries.size]  # noqa: E203


def accept_append_entries_request(
    c: Context[EtcdRaftState], i: Expr, j: Expr, log_ok: Expr, m: Expr, p: Expr
):
    # TLA+: AcceptAppendEntriesRequest(i, j, logOk, m)  etcdraft.tla:603
    s = c.state
    c.assume(m.mterm == s.currentTerm[i])
    c.assume(s.state[i] == ServerState.FOLLOWER)
    c.assume(log_ok)
    index = p.mprevLogIndex + 1
    sub = iter(c.alternatives("AlreadyDone", "Conflict", "NoConflict"))
    with next(sub):
        append_entries_already_done(c, i, j, index, m, p)
    with next(sub):
        conflict_append_entries_request(c, i, index, m, p)
    with next(sub):
        no_conflict_append_entries_request(c, i, index, m, p)


def handle_append_entries_request(c: Context[EtcdRaftState], i: Expr, j: Expr, m: Expr):
    # TLA+: HandleAppendEntriesRequest(i, j, m)  etcdraft.tla:618
    s = c.state
    p = as_aereq(m)
    log_ok = Or(
        p.mprevLogIndex == 0,
        And(
            p.mprevLogIndex > 0,
            p.mprevLogIndex <= s.log[i].size,
            p.mprevLogTerm == entry_at(s.log[i], p.mprevLogIndex).term,
        ),
    )
    c.assume(m.mterm <= s.currentTerm[i])
    alts = iter(c.alternatives("Reject", "ReturnToFollower", "Accept"))
    with next(alts):
        reject_append_entries_request(c, i, j, m, log_ok)
    with next(alts):
        return_to_follower_state(c, i, m)
    with next(alts):
        accept_append_entries_request(c, i, j, log_ok, m, p)


def handle_append_entries_response(
    c: Context[EtcdRaftState], i: Expr, j: Expr, m: Expr
):
    # TLA+: HandleAppendEntriesResponse(i, j, m)  etcdraft.tla:633
    s = c.state
    p = as_aeresp(m)
    c.assume(m.mterm == s.currentTerm[i])
    s.matchIndex[i][j] = (
        max2(s.matchIndex[i][j], p.mmatchIndex)
        .if_(p.msuccess)
        .else_(s.matchIndex[i][j])
    )
    discard(c, m)


def update_term(c: Context[EtcdRaftState], i: Expr, j: Expr, m: Expr):
    """``UpdateTerm`` -- step down to a newer term; message is left in place.

    TLA+: UpdateTerm(i, j, m)  etcdraft.tla:645
    """
    s = c.state
    c.assume(m.mterm > s.currentTerm[i])
    become_follower_of_term(c, i, m.mterm)


def drop_stale_response(c: Context[EtcdRaftState], i: Expr, j: Expr, m: Expr):
    # TLA+: DropStaleResponse(i, j, m)  etcdraft.tla:653
    s = c.state
    c.assume(m.mterm < s.currentTerm[i])
    discard(c, m)


@action(inline=False)
def receive(c: Context[EtcdRaftState], m: Expr):
    """``Receive(m)`` -- dispatch a message to its handler.

    TLA+: Receive(m) / ReceiveDirect(m)  etcdraft.tla:676 / :659
    """
    i = m.mdest
    j = m.msource
    alts = iter(
        c.alternatives(
            "UpdateTerm",
            "RequestVoteReq",
            "RequestVoteResp",
            "AppendEntriesReq",
            "AppendEntriesResp",
        )
    )

    with next(alts):
        update_term(c, i, j, m)

    with next(alts):
        c.assume(m.payload.tag == "RequestVoteReq")
        handle_request_vote_request(c, i, j, m)

    with next(alts):
        c.assume(m.payload.tag == "RequestVoteResp")
        sub = iter(c.alternatives("DropStale", "Handle"))
        with next(sub):
            drop_stale_response(c, i, j, m)
        with next(sub):
            handle_request_vote_response(c, i, j, m)

    with next(alts):
        c.assume(m.payload.tag == "AppendEntriesReq")
        handle_append_entries_request(c, i, j, m)

    with next(alts):
        c.assume(m.payload.tag == "AppendEntriesResp")
        sub = iter(c.alternatives("DropStale", "Handle"))
        with next(sub):
            drop_stale_response(c, i, j, m)
        with next(sub):
            handle_append_entries_response(c, i, j, m)


# =============================================================================
# Network state transitions (NextUnreliable)
# =============================================================================
# TLA+: "Network state transitions"  etcdraft.tla:685-700


@action(inline=False)
def duplicate_message(c: Context[EtcdRaftState], m: Expr):
    # TLA+: DuplicateMessage(m)  etcdraft.tla:689
    s = c.state
    c.assume(Bag(s.messages)[m] == 1)
    s.messages = Bag(s.messages).add_one(m).as_map


@action(inline=False)
def drop_message(c: Context[EtcdRaftState], m: Expr):
    # TLA+: DropMessage(m)  etcdraft.tla:696
    s = c.state
    c.assume(Bag(s.messages)[m] == 1)
    discard(c, m)


# =============================================================================
# Next (NextDynamic: async + crash + unreliable + reconfiguration)
# =============================================================================
# TLA+: NextAsync :705, NextCrash :719, NextUnreliable :725, NextDynamic :741


@action
def step(c: Context[EtcdRaftState]):
    s = c.state
    srv = s.Server
    msgs = Bag(s.messages).to_set()
    alts = iter(
        c.alternatives(
            "RequestVote",
            "BecomeLeader",
            "ClientRequest",
            "AdvanceCommitIndex",
            "AppendEntries",
            "AppendEntriesToSelf",
            "Heartbeat",
            "SendSnapshot",
            "Receive",
            "Timeout",
            "Ready",
            "StepDownToFollower",
            "Restart",
            "DuplicateMessage",
            "DropMessage",
            "AddNewServer",
            "AddLearner",
            "DeleteServer",
            "ApplySimpleConfChange",
        )
    )

    # --- NextAsync  etcdraft.tla:705 ---
    with next(alts), c.one_of(srv, "i") as i, c.one_of(srv, "j") as j:
        request_vote(c, i, j)

    with next(alts), c.one_of(srv, "i") as i:
        become_leader(c, i)

    with next(alts), c.one_of(srv, "i") as i:
        client_request(c, i)

    with next(alts), c.one_of(srv, "i") as i:
        advance_commit_index(c, i)

    with next(alts), c.one_of(srv, "i") as i, c.one_of(srv, "j") as j:
        rng = Interval(s.matchIndex[i][j] + 1, s.log[i].size + 1)
        with c.one_of(rng, "b") as b, c.one_of(rng, "e") as e:
            append_entries(c, i, j, b, e)

    with next(alts), c.one_of(srv, "i") as i:
        append_entries_to_self(c, i)

    with next(alts), c.one_of(srv, "i") as i, c.one_of(srv, "j") as j:
        heartbeat(c, i, j)

    with next(alts), c.one_of(srv, "i") as i, c.one_of(srv, "j") as j:
        with c.one_of(Interval(Val(1), s.commitIndex[i]), "index") as index:
            send_snapshot(c, i, j, index)

    with next(alts), c.one_of(msgs, "m") as m:
        receive(c, m)

    with next(alts), c.one_of(srv, "i") as i:
        timeout(c, i)

    with next(alts), c.one_of(srv, "i") as i:
        ready(c, i)

    with next(alts), c.one_of(srv, "i") as i:
        step_down_to_follower(c, i)

    # --- NextCrash  etcdraft.tla:719 ---
    with next(alts), c.one_of(srv, "i") as i:
        restart(c, i)

    # --- NextUnreliable  etcdraft.tla:725 ---
    with next(alts), c.one_of(msgs, "m") as m:
        duplicate_message(c, m)

    with next(alts), c.one_of(msgs, "m") as m:
        drop_message(c, m)

    # --- NextDynamic extras  etcdraft.tla:741 ---
    with next(alts), c.one_of(srv, "i") as i, c.one_of(srv, "j") as j:
        add_new_server(c, i, j)

    with next(alts), c.one_of(srv, "i") as i, c.one_of(srv, "j") as j:
        add_learner(c, i, j)

    with next(alts), c.one_of(srv, "i") as i, c.one_of(srv, "j") as j:
        delete_server(c, i, j)

    with next(alts), c.one_of(srv, "i") as i:
        apply_simple_conf_change(c, i)


# =============================================================================
# Correctness invariants
# =============================================================================
# TLA+: "Correctness invariants"  etcdraft.tla:768-874


@invariant
def message_terms_le_current_term(s: EtcdRaftState) -> Expr:
    """No message carries a term higher than its sender's currentTerm.

    TLA+: MessageTermsLtCurrentTerm(m)  etcdraft.tla:777
    """
    return (
        Bag(s.messages).to_set().forall(lambda m: m.mterm <= s.currentTerm[m.msource])
    )


@invariant
def log_inv(s: EtcdRaftState) -> Expr:
    """Committed prefixes are totally ordered by prefix.

    TLA+: LogInv  etcdraft.tla:781
    """
    return Forall(
        Or(
            is_prefix(committed(s, i), committed(s, j)),
            is_prefix(committed(s, j), committed(s, i)),
        )
        for i in s.Server
        for j in s.Server
    )


@invariant
def more_than_one_leader_inv(s: EtcdRaftState) -> Expr:
    """At most one leader per term.

    TLA+: MoreThanOneLeaderInv  etcdraft.tla:792
    """
    return Forall(
        Implies(
            And(
                s.currentTerm[i] == s.currentTerm[j],
                s.state[i] == ServerState.LEADER,
                s.state[j] == ServerState.LEADER,
            ),
            i == j,
        )
        for i in s.Server
        for j in s.Server
    )


@invariant
def election_safety_inv(s: EtcdRaftState) -> Expr:
    """A leader's last index in its term dominates every server's.

    TLA+: ElectionSafetyInv  etcdraft.tla:800
    """
    return s.Server.forall(
        lambda i: Implies(
            s.state[i] == ServerState.LEADER,
            s.Server.forall(
                lambda j: last_index_with_term(s.log[i], s.currentTerm[i])
                >= last_index_with_term(s.log[j], s.currentTerm[i])
            ),
        )
    )


@invariant
def log_matching_inv(s: EtcdRaftState) -> Expr:
    """Equal terms at a position imply equal prefixes up to it.

    TLA+: LogMatchingInv  etcdraft.tla:808
    """
    return Forall(
        Interval(Val(1), min2(s.log[i].size, s.log[j].size)).forall(
            lambda n: Implies(
                entry_at(s.log[i], n).term == entry_at(s.log[j], n).term,
                s.log[i][0:n] == s.log[j][0:n],
            )
        )
        for i in s.Server
        for j in s.Server
    )


@invariant
def quorum_log_inv(s: EtcdRaftState) -> Expr:
    """Every quorum of i's config has a member holding i's committed prefix.

    TLA+: QuorumLogInv  etcdraft.tla:838
    """
    return s.Server.forall(
        lambda i: quorums(get_config(s, i)).forall(
            lambda subset: subset.exists(lambda j: is_prefix(committed(s, i), s.log[j]))
        )
    )


@invariant
def more_up_to_date_correct_inv(s: EtcdRaftState) -> Expr:
    """A more-up-to-date log contains every committed prefix.

    TLA+: MoreUpToDateCorrectInv  etcdraft.tla:848
    """
    return Forall(
        Implies(
            Or(
                last_term(s.log[i]) > last_term(s.log[j]),
                And(
                    last_term(s.log[i]) == last_term(s.log[j]),
                    s.log[i].size >= s.log[j].size,
                ),
            ),
            is_prefix(committed(s, j), s.log[i]),
        )
        for i in s.Server
        for j in s.Server
    )


@invariant
def leader_completeness_inv(s: EtcdRaftState) -> Expr:
    """A higher-term leader holds every committed entry at its position.

    TLA+: LeaderCompletenessInv  etcdraft.tla:859
    """

    def for_server(i: Expr) -> Expr:
        return positions(committed(s, i)).forall(
            lambda idx: current_leaders(s).forall(
                lambda lead: Implies(
                    s.currentTerm[lead] > entry_at(s.log[i], idx).term,
                    And(
                        idx <= s.log[lead].size,
                        entry_at(s.log[lead], idx) == entry_at(s.log[i], idx),
                    ),
                )
            )
        )

    return s.Server.forall(for_server)


@invariant
def committed_is_durable_inv(s: EtcdRaftState) -> Expr:
    """A leader never commits past what it has persisted.

    TLA+: CommittedIsDurableInv  etcdraft.tla:872
    """
    return s.Server.forall(
        lambda i: Implies(
            s.state[i] == ServerState.LEADER,
            s.commitIndex[i] <= s.durableState[i].log,
        )
    )


# =============================================================================
# Instances and coverage
# =============================================================================
# Wunderspec-only: the TLA+ side configures these via a separate MC model file.


@instance
def n3() -> EtcdRaftState:
    return EtcdRaftState(
        Server=Set(1, 2, 3),
        InitServer=Set(1, 2, 3),
        Nil=0,
        MaxTerm=2,
        MaxLogLen=3,
        MaxReconfig=1,
    )


@coverage
def state_cov(s: EtcdRaftState) -> Expr:
    return Tuple(
        s.messages,
        s.pendingMessages,
        s.currentTerm,
        s.state,
        s.votedFor,
        s.log,
        s.commitIndex,
        s.votesResponded,
        s.votesGranted,
        s.matchIndex,
        s.config,
    )
