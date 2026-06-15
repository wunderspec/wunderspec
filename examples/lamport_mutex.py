"""
Lamport's distributed mutual-exclusion algorithm.

A Wunderspec translation of the TLA+ specification from:
L. Lamport: Time, Clocks and the Ordering of Events in a Distributed System.
CACM 21(7):558-565, 1978.

Original TLA+ specification: examples/LamportMutex.tla

Igor Konnov, 2026
"""

from enum import Enum, auto

from wunderspec import *
from wunderspec import Param, StateVar
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    coverage,
    invariant,
    state,
)


# Message types
class MsgType(Enum):
    REQ = auto()
    ACK = auto()
    REL = auto()


# Message is a record with type and clock fields
Message = tuple[MsgType, int]


def req_message(c: Expr) -> Expr:
    """Create a request message with the given clock value."""
    return Tuple(Val(MsgType.REQ), c)


def ack_message() -> Expr:
    """Create an acknowledgement message."""
    return Tuple(Val(MsgType.ACK), Val(0))


def rel_message() -> Expr:
    """Create a release message."""
    return Tuple(Val(MsgType.REL), Val(0))


@state
class LamportMutexState(MachineStateBase):
    """
    State schema for Lamport's mutex algorithm.

    - N: number of processes (parameter)
    - maxClock: maximum clock value for bounded model checking (parameter)
    - clock: local clock of each process
    - req: req[p][q] stores the clock from q's request received by p (0 if none)
    - ack: ack[p] stores the set of processes that have ack'ed p's request
    - network: network[p][q] is the queue of messages from p to q (FIFO)
    - crit: set of processes currently in the critical section
    """

    N: Param[int]
    maxClock: Param[int]
    clock: StateVar[dict[int, int]]
    req: StateVar[dict[int, dict[int, int]]]
    ack: StateVar[dict[int, set[int]]]
    network: StateVar[dict[int, dict[int, list[Message]]]]
    crit: StateVar[set[int]]


def procs(s: LamportMutexState) -> SetExpr:
    """The set of process IDs: 1..N"""
    return Set(Val(1), ..., s.N)


def beats(s: LamportMutexState, p: Expr, q: Expr) -> BoolExpr:
    """
    beats(p, q) is true if process p believes its request has higher priority
    than q's request. This is true if:
    - p has not received a request from q (req[p][q] = 0), OR
    - p's request has a smaller clock than q's (req[p][p] < req[p][q]), OR
    - there's a tie and p has a smaller ID (req[p][p] = req[p][q] and p < q)
    """
    return Or(
        s.req[p][q] == Val(0),
        s.req[p][p] < s.req[p][q],
        And(s.req[p][p] == s.req[p][q], p < q),
    )


def broadcast(s: LamportMutexState, sender: Expr, msg: Expr) -> Expr:
    """
    Broadcast a message from sender to all other processes.
    Returns the updated network[sender] map.
    """
    return procs(s).map_to(
        lambda r: Ite(
            sender == r,
            s.network[sender][r],
            s.network[sender][r] + List(msg),
        )
    )


# =============================================================================
# Actions
# =============================================================================


@action(inline=False)
def request(c: Context[LamportMutexState], p: Expr):
    """
    Process p requests access to the critical section.
    Precondition: p has no pending request (req[p][p] = 0)
    """
    s = c.state
    c.assume(s.req[p][p] == Val(0))

    # Record own request with current clock
    s.req[p][p] = s.clock[p]
    # Broadcast request message to all other processes
    s.network[p] = broadcast(s, p, req_message(s.clock[p]))
    # Initialize ack set with self
    s.ack[p] = Set(p)
    # clock and crit unchanged


@action(inline=False)
def receive_request(c: Context[LamportMutexState], p: Expr, q: Expr):
    """
    Process p receives a request from process q and acknowledges it.
    Precondition: there is a message in network[q][p] and it's a request
    """
    s = c.state
    # Check queue is not empty
    c.assume(~s.network[q][p].is_empty)

    m = s.network[q][p][0]
    msg_clock = m[1]

    # Check it's a request message
    c.assume(m[0] == Val(MsgType.REQ))

    # Record the request clock from q
    s.req[p][q] = msg_clock
    # Update local clock: max(msg_clock, clock[p]) + 1
    s.clock[p] = (
        (msg_clock + Val(1)).if_(msg_clock > s.clock[p]).else_(s.clock[p] + Val(1))
    )

    # Update network: remove from [q][p] and append to [p][q]
    # This requires composing two updates into one assignment
    queue_qp = s.network[q][p]
    new_queue_qp = queue_qp[1 : queue_qp.size]  # Tail
    new_queue_pq = s.network[p][q] + List(ack_message())
    new_network_q = s.network[q].replace(p, new_queue_qp)
    new_network_p = s.network[p].replace(q, new_queue_pq)
    s.network = s.network.replace(q, new_network_q).replace(p, new_network_p)
    # ack and crit unchanged


@action(inline=False)
def receive_ack(c: Context[LamportMutexState], p: Expr, q: Expr):
    """
    Process p receives an acknowledgement from process q.
    Precondition: there is a message in network[q][p] and it's an ack
    """
    s = c.state
    # Check queue is not empty
    c.assume(~s.network[q][p].is_empty)

    m = s.network[q][p][0]

    # Check it's an ack message
    c.assume(m[0] == Val(MsgType.ACK))

    # Add q to the ack set
    s.ack[p] = s.ack[p] | Set(q)
    # Remove message from queue (Tail)
    queue = s.network[q][p]
    s.network[q][p] = queue[1 : queue.size]
    # clock, req, crit unchanged


@action(inline=False)
def enter(c: Context[LamportMutexState], p: Expr):
    """
    Process p enters the critical section.
    Preconditions:
    - p has received acks from all processes (ack[p] = Proc)
    - p's request beats all other processes' requests
    """
    s = c.state
    # All processes have acknowledged
    c.assume(s.ack[p] == procs(s))
    # p beats all other processes
    c.assume((procs(s) - Set(p)).forall(lambda q: beats(s, p, q)))

    # Top-level set update (not nested map update)
    s.crit = s.crit | Set(p)
    # clock, req, ack, network unchanged


@action(inline=False)
def exit_cs(c: Context[LamportMutexState], p: Expr):
    """
    Process p exits the critical section and notifies other processes.
    Precondition: p is in the critical section
    """
    s = c.state
    c.assume(s.crit.contains(p))

    # Top-level set update
    s.crit = s.crit - Set(p)

    # Broadcast release message
    s.network[p] = broadcast(s, p, rel_message())
    # Clear own request
    s.req[p][p] = 0
    # Clear ack set
    s.ack[p] = Set(int)
    # clock unchanged


@action(inline=False)
def receive_release(c: Context[LamportMutexState], p: Expr, q: Expr):
    """
    Process p receives a release notification from process q.
    Precondition: there is a message in network[q][p] and it's a release
    """
    s = c.state
    # Check queue is not empty
    c.assume(~s.network[q][p].is_empty)

    m = s.network[q][p][0]

    # Check it's a release message
    c.assume(m[0] == Val(MsgType.REL))

    # Clear q's request
    s.req[p][q] = 0
    # Remove message from queue (Tail)
    queue = s.network[q][p]
    s.network[q][p] = queue[1 : queue.size]
    # clock, ack, crit unchanged


# =============================================================================
# Specification
# =============================================================================


@action(init=True)
def init(c: Context[LamportMutexState]):
    """Initial state predicate."""
    s = c.state
    ps = procs(s)

    # All clocks start at 1
    s.clock = ps.map_to(lambda _: Val(1))
    # No requests received (all zeros)
    s.req = ps.map_to(lambda _: ps.map_to(lambda _: Val(0)))
    # No acks received (empty sets)
    s.ack = ps.map_to(lambda _: Set(int))
    # Empty network queues
    s.network = ps.map_to(lambda _: ps.map_to(lambda _: List(Message)))
    # No one in critical section
    s.crit = Set(int)


@action
def step(c: Context[LamportMutexState]):
    """Next-state relation."""
    s = c.state
    ps = procs(s)

    alts = iter(c.alternatives("Request", "Enter", "Exit", "ReceiveMsg"))

    # Request, Enter, or Exit by some process p
    with next(alts), c.one_of(ps, "p") as p:
        request(c, p)

    with next(alts), c.one_of(ps, "p") as p:
        enter(c, p)

    with next(alts), c.one_of(ps, "p") as p:
        exit_cs(c, p)

    # Receive message: p receives from q (where p != q)
    with next(alts), c.one_of(ps, "p") as p, c.one_of(ps, "q") as q:
        c.assume(p != q)
        msg_alts = iter(
            c.alternatives("ReceiveRequest", "ReceiveAck", "ReceiveRelease")
        )
        with next(msg_alts):
            receive_request(c, p, q)
        with next(msg_alts):
            receive_ack(c, p, q)
        with next(msg_alts):
            receive_release(c, p, q)


# =============================================================================
# Invariants and Properties
# =============================================================================


@invariant
def type_ok(s: LamportMutexState) -> BoolExpr:
    """Type correctness predicate."""
    ps = procs(s)
    return And(
        # clock[p] is a positive integer for all p
        ps.forall(lambda p: s.clock[p] >= Val(1)),
        # req[p][q] is a natural number for all p, q
        ps.forall(lambda p: ps.forall(lambda q: s.req[p][q] >= Val(0))),
        # ack[p] is a subset of Proc for all p
        ps.forall(lambda p: s.ack[p] <= ps),
        # crit is a subset of Proc
        s.crit <= ps,
    )


@invariant
def clock_constraint(s: LamportMutexState) -> BoolExpr:
    """State constraint for bounded model checking."""
    return procs(s).forall(lambda p: s.clock[p] <= s.maxClock)


@invariant
def bounded_network(s: LamportMutexState) -> BoolExpr:
    """No channel ever contains more than three messages."""
    ps = procs(s)
    return ps.forall(lambda p: ps.forall(lambda q: s.network[p][q].size <= Val(3)))


@invariant
def mutex(s: LamportMutexState) -> BoolExpr:
    """
    The main safety property: mutual exclusion.
    At most one process can be in the critical section at any time.
    """
    return s.crit.forall(lambda p: s.crit.forall(lambda q: p == q))


@coverage
def state_cov(s: LamportMutexState) -> Expr:
    return Tuple(s.N, s.maxClock, s.clock, s.req, s.ack, s.network, s.crit)


### a few proto-states for producing instances
proto3_5 = LamportMutexState(N=3, maxClock=5)
proto5_10 = LamportMutexState(N=5, maxClock=10)


@instance
def n2_max_clock5() -> LamportMutexState:
    return LamportMutexState(N=2, maxClock=5)
