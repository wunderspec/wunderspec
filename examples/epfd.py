"""
Eventually Perfect Failure Detector (◇P) in Wunderspec.

A Wunderspec model of the "Increasing Timeout" eventually-perfect failure
detector (Cachin, Guerraoui & Rodrigues, *Introduction to Reliable and Secure
Distributed Programming*, Algorithm 2.7), in the style of the Lean formalization
at https://protocols-made-fun.com/lean/2025/06/10/lean-epfd-completeness.html .

Each process p runs a local detector that periodically (on a timeout) broadcasts
heartbeat *requests*, suspecting every process that did not answer the previous
round and restoring those that did. A non-crashed process answers a request with
a heartbeat *reply*; receiving a reply marks the sender alive. Whenever a process
is found to be both alive and suspected (a false positive), the detector raises
its timeout `delay`, which is what eventually makes the detector accurate under
partial synchrony.

This is the M4 fixture of the wunderspec-lean bootstrapping plan: it is meant to
be validated here with `wunderspec run` and then translated to Lean (M5b).
Authored for that plan, 2026.
"""

from enum import Enum, auto

from wunderspec import (
    Always,
    And,
    AndT,
    BoolExpr,
    Eventually,
    Expr,
    Implies,
    Param,
    Set,
    StateVar,
    Tuple,
    Val,
    WeakFair,
)
from wunderspec.expr import SetExpr
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    coverage,
    instance,
    invariant,
    state,
    temporal,
)


class MsgKind(Enum):
    """Heartbeat message kinds exchanged between detectors."""

    REQUEST = auto()
    REPLY = auto()


# A message is a tuple (kind, src, dst, timestamp). The timestamp is the value
# of the global clock when the message was sent.
Message = tuple[MsgKind, int, int, int]


def heartbeat(kind: Expr, src: Expr, dst: Expr, ts: Expr) -> Expr:
    """A heartbeat message `(kind, src, dst, ts)`."""
    return Tuple(kind, src, dst, ts)


@state
class EpfdState(MachineStateBase):
    """
    State schema for the eventually-perfect failure detector.

    - N: number of processes (parameter); process ids are 1..N
    - initDelay: initial timeout interval Δ (parameter)
    - clock: a fictitious global clock
    - crashed: processes that have permanently failed
    - sent: all heartbeat messages ever dispatched
    - rcvd: messages that have already been delivered
    - alive[p]: processes that p has heard from since its last timeout
    - suspected[p]: processes that p currently suspects to have crashed
    - delay[p]: p's adaptive timeout interval
    - nextTimeout[p]: clock value at which p's next timeout fires
    """

    N: Param[int]
    initDelay: Param[int]
    clock: StateVar[int]
    crashed: StateVar[set[int]]
    sent: StateVar[set[Message]]
    rcvd: StateVar[set[Message]]
    alive: StateVar[dict[int, set[int]]]
    suspected: StateVar[dict[int, set[int]]]
    delay: StateVar[dict[int, int]]
    nextTimeout: StateVar[dict[int, int]]


def procs(s: EpfdState) -> SetExpr:
    """The set of process ids: 1..N."""
    return Set(Val(1), ..., s.N)


# =============================================================================
# Actions
# =============================================================================


@action(inline=False)
def advance_clock(c: Context[EpfdState]):
    """The global clock ticks. Left unbounded so the trace semantics can express
    non-zenoness (the clock diverges); `wunderspec run` still bounds exploration
    by its step limit."""
    s = c.state
    s.clock = s.clock + Val(1)


@action(inline=False)
def crash(c: Context[EpfdState], p: Expr):
    """Process p crashes, joining the (monotone) crashed set."""
    s = c.state
    c.assume(~s.crashed.contains(p))
    s.crashed = s.crashed | Set(p)


@action(inline=False)
def timeout(c: Context[EpfdState], p: Expr):
    """
    p's timeout fires: suspect everyone it has not heard from, restore those it
    has, raise the delay on a false positive, and re-broadcast heartbeat
    requests to all processes.
    """
    s = c.state
    c.assume(~s.crashed.contains(p))
    c.assume(s.clock >= s.nextTimeout[p])

    # A process that is both alive and suspected was wrongly suspected: grow the
    # timeout so that, eventually, replies always arrive in time.
    false_positive = ~(s.alive[p] & s.suspected[p]).is_empty
    new_delay = (s.delay[p] + s.initDelay).if_(false_positive).else_(s.delay[p])

    # Everyone not heard from since the last round becomes suspected; the rest
    # (those in alive[p]) are implicitly restored.
    s.suspected[p] = procs(s) - s.alive[p]
    s.delay[p] = new_delay
    s.nextTimeout[p] = s.clock + new_delay
    # Re-arm: forget last round's replies and ask everyone again.
    s.alive[p] = Set(int)
    s.sent = s.sent | Set(
        heartbeat(Val(MsgKind.REQUEST), p, q, s.clock) for q in procs(s)
    )


@action(inline=False)
def rcv_request(c: Context[EpfdState], m: Expr):
    """A non-crashed process delivers a heartbeat request and replies to it."""
    s = c.state
    c.assume(s.sent.contains(m))
    c.assume(~s.rcvd.contains(m))
    c.assume(m[0] == Val(MsgKind.REQUEST))
    src = m[1]
    dst = m[2]
    c.assume(~s.crashed.contains(dst))

    s.rcvd = s.rcvd | Set(m)
    s.sent = s.sent | Set(heartbeat(Val(MsgKind.REPLY), dst, src, s.clock))


@action(inline=False)
def rcv_reply(c: Context[EpfdState], m: Expr):
    """A non-crashed process delivers a heartbeat reply and marks the sender alive."""
    s = c.state
    c.assume(s.sent.contains(m))
    c.assume(~s.rcvd.contains(m))
    c.assume(m[0] == Val(MsgKind.REPLY))
    src = m[1]
    dst = m[2]
    c.assume(~s.crashed.contains(dst))

    s.rcvd = s.rcvd | Set(m)
    s.alive[dst] = s.alive[dst] | Set(src)


# =============================================================================
# Specification
# =============================================================================


@action(init=True)
def init(c: Context[EpfdState]):
    """Initial state predicate."""
    s = c.state
    ps = procs(s)

    s.clock = Val(0)
    s.crashed = Set(int)
    s.sent = Set(Message)
    s.rcvd = Set(Message)
    # Each detector starts by trusting everyone and suspecting no one.
    s.alive = ps.map_to(lambda _: ps)
    s.suspected = ps.map_to(lambda _: Set(int))
    s.delay = ps.map_to(lambda _: s.initDelay)
    s.nextTimeout = ps.map_to(lambda _: s.initDelay)


@action
def step(c: Context[EpfdState]):
    """Next-state relation."""
    s = c.state
    ps = procs(s)

    alts = iter(
        c.alternatives("AdvanceClock", "Crash", "Timeout", "RcvRequest", "RcvReply")
    )

    with next(alts):
        advance_clock(c)

    with next(alts), c.one_of(ps, "p") as p:
        crash(c, p)

    with next(alts), c.one_of(ps, "p") as p:
        timeout(c, p)

    with next(alts), c.one_of(s.sent, "m") as m:
        rcv_request(c, m)

    with next(alts), c.one_of(s.sent, "m") as m:
        rcv_reply(c, m)


# =============================================================================
# Invariants and Properties
# =============================================================================


@invariant
def type_ok(s: EpfdState) -> BoolExpr:
    """Type correctness: crashed/alive/suspected stay within the process set,
    and only sent messages can be received."""
    ps = procs(s)
    return And(
        s.crashed <= ps,
        ps.forall(lambda p: s.alive[p] <= ps),
        ps.forall(lambda p: s.suspected[p] <= ps),
        s.rcvd <= s.sent,
    )


# The state variables, used as the stuttering-variable list of the fairness
# conditions (the `⟨A⟩_vars` subscript).
VARS = (
    "clock",
    "crashed",
    "sent",
    "rcvd",
    "alive",
    "suspected",
    "delay",
    "nextTimeout",
)


@temporal
def fairness(s: EpfdState):
    """The fairness assumption under which the ◇P properties hold: the clock keeps
    ticking and every process keeps timing out (so detectors keep probing and
    re-evaluating their suspicions). Lowered to weak-fairness run conditions.

    (Message-delivery fairness — every message to a correct process is eventually
    delivered — also belongs here, but it ranges over the dynamic message set and
    is not expressible as a `WeakFair` over a fixed parameter; it is deferred.)"""
    ps = procs(s)
    return AndT(
        WeakFair(advance_clock, vars=VARS),
        ps.forall(lambda p: WeakFair(timeout, p, vars=VARS)),
    )


@temporal
def strong_completeness(s: EpfdState):
    """Eventually, every crashed process is permanently suspected by every
    correct process (◇□ completeness)."""
    ps = procs(s)
    return Eventually(
        Always(
            ps.forall(
                lambda p: ps.forall(
                    lambda q: Implies(
                        And(s.crashed.contains(p), ~s.crashed.contains(q)),
                        s.suspected[q].contains(p),
                    )
                )
            )
        )
    )


@temporal
def eventual_strong_accuracy(s: EpfdState):
    """Eventually, no correct process is suspected by any correct process
    (◇□ accuracy)."""
    ps = procs(s)
    return Eventually(
        Always(
            ps.forall(
                lambda p: ps.forall(
                    lambda q: Implies(
                        And(~s.crashed.contains(p), ~s.crashed.contains(q)),
                        ~s.suspected[q].contains(p),
                    )
                )
            )
        )
    )


@coverage
def state_cov(s: EpfdState) -> Expr:
    return Tuple(
        s.clock, s.crashed, s.sent, s.rcvd, s.alive, s.suspected, s.nextTimeout
    )


### a few proto-states for producing instances
proto3 = EpfdState(N=3, initDelay=1)
proto5 = EpfdState(N=5, initDelay=2)


@instance
def n3() -> EpfdState:
    return EpfdState(N=3, initDelay=1)
