"""
Bakery mutual exclusion algorithm. A Wunderspec version of the TLA+ specification at:

https://github.com/tlaplus/Examples/blob/master/specifications/Bakery-Boulangerie/Bakery.tla

Igor Konnov, 2026
"""

from enum import Enum, auto

from wunderspec import Param, StateVar
from wunderspec.expr import Expr, IntExpr, SetExpr
from wunderspec.lang import Ints, Or, Set, Tuple, UnsignedInts, Val
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    coverage,
    instance,
    invariant,
    state,
)


class PC(Enum):
    """
    Program counter values for the Bakery algorithm. We could use strings here,
    but engineers often prefer enums for better IDE support.
    """

    CS = auto()
    NCS = auto()
    E1 = auto()
    E2 = auto()
    E3 = auto()
    E4 = auto()
    W1 = auto()
    W2 = auto()
    EXIT = auto()


PROCESS_PCS = (PC.NCS, PC.E1, PC.E2, PC.E3, PC.E4, PC.W1, PC.W2, PC.CS, PC.EXIT)


@state
class BakeryState(MachineStateBase):
    """
    The schema of a state in the Bakery algorithm. We annotate each field
    with its expression type, often `Expr` is enough, as well as with
    python types that are translated into sorts. Finally, we use
    `Param[int]` to indicate that `N` is a parameter, while
    `StateVar[...]` marks mutable variables.
    """

    N: Param[int]
    num: StateVar[dict[int, int]]
    flag: StateVar[dict[int, bool]]
    unchecked: StateVar[dict[int, set[int]]]
    max: StateVar[dict[int, int]]
    nxt: StateVar[dict[int, int]]
    pc: StateVar[dict[int, PC]]


def procs(s: BakeryState) -> SetExpr:
    """The set of process IDs."""
    return Set(Val(1), ..., s.N)


# a \prec b == \/ a[1] < b[1]
#              \/ (a[1] = b[1]) /\ (a[2] < b[2])
def precedes(a, b):
    return (a[0] < b[0]).if_(a[0] != b[0]).else_(a[1] < b[1])
    # return (a[0] < b[0]).or_((a[0] == b[0]).and_(a[1] < b[1]))


# ncs(self) == /\ pc[self] = "ncs"
#              /\ pc' = [pc EXCEPT ![self] = "e1"]
#              /\ UNCHANGED << num, flag, unchecked, max, nxt >>
@action(inline=False)
def ncs(c: Context[BakeryState], q: IntExpr):
    s = c.state
    c.assume(s.pc[q] == PC.NCS)
    s.pc[q] = PC.E1


# e1(self) == /\ pc[self] = "e1"
#             /\ \/ /\ flag' = [flag EXCEPT ![self] = ~ flag[self]]
#                   /\ pc' = [pc EXCEPT ![self] = "e1"]
#                   /\ UNCHANGED <<unchecked, max>>
#                \/ /\ flag' = [flag EXCEPT ![self] = TRUE]
#                   /\ unchecked' = [unchecked EXCEPT ![self] = Procs \ {self}]
#                   /\ max' = [max EXCEPT ![self] = 0]
#                   /\ pc' = [pc EXCEPT ![self] = "e2"]
#             /\ UNCHANGED << num, nxt >>
#
@action(inline=False)
def e1(c: Context[BakeryState], q: IntExpr):
    s = c.state
    c.assume(s.pc[q] == Val(PC.E1))
    toggle_flag, set_flag = c.alternatives("ToggleFlag", "SetFlag")
    with toggle_flag:
        s.flag[q] = ~s.flag[q]
        s.pc[q] = PC.E1
    with set_flag:
        s.flag[q] = True
        s.unchecked[q] = procs(s) - Set(q)
        s.max[q] = 0
        s.pc[q] = PC.E2


# e2(self) == /\ pc[self] = "e2"
#             /\ IF unchecked[self] # {}
#                   THEN /\ \E i \in unchecked[self]:
#                             /\ unchecked' = [unchecked EXCEPT ![self] = unchecked[self] \ {i}]
#                             /\ IF num[i] > max[self]
#                                   THEN /\ max' = [max EXCEPT ![self] = num[i]]
#                                   ELSE /\ TRUE
#                                        /\ max' = max
#                        /\ pc' = [pc EXCEPT ![self] = "e2"]
#                   ELSE /\ pc' = [pc EXCEPT ![self] = "e3"]
#                        /\ UNCHANGED << unchecked, max >>
#             /\ UNCHANGED << num, flag, nxt >>
@action(inline=False)
def e2(c: Context[BakeryState], q: IntExpr):
    s = c.state
    c.assume(s.pc[q] == Val(PC.E2))
    then_, else_ = c.split(~s.unchecked[q].is_empty)
    with then_, c.one_of(Ints, "i") as i:
        c.assume(i.in_(s.unchecked[q]))
        s.unchecked[q] -= Set(i)
        is_gt, not_gt = c.split(s.num[i] > s.max[q])
        with is_gt:
            s.max = s.max.replace(q, s.num[i])
        with not_gt:
            s.max = s.max
        s.pc[q] = PC.E2
    with else_:
        s.pc[q] = PC.E3


# e3(self) == /\ pc[self] = "e3"
#             /\ \/ /\ \E k \in Nat:
#                        num' = [num EXCEPT ![self] = k]
#                   /\ pc' = [pc EXCEPT ![self] = "e3"]
#                \/ /\ \E i \in {j \in Nat : j > max[self]}:
#                        num' = [num EXCEPT ![self] = i]
#                   /\ pc' = [pc EXCEPT ![self] = "e4"]
#             /\ UNCHANGED << flag, unchecked, max, nxt >>
@action(inline=False)
def e3(c, q):
    s = c.state
    c.assume(s.pc[q] == Val(PC.E3))
    to_e3, to_e4 = c.alternatives("ToE3", "ToE4")
    with to_e3, c.one_of(UnsignedInts, "k") as k:
        s.num[q] = k
        s.pc[q] = PC.E3
    with to_e4, c.one_of(UnsignedInts, "i") as i:
        c.assume(i > s.max[q])
        s.num[q] = i
        s.pc[q] = PC.E4


# e4(self) == /\ pc[self] = "e4"
#             /\ \/ /\ flag' = [flag EXCEPT ![self] = ~ flag[self]]
#                   /\ pc' = [pc EXCEPT ![self] = "e4"]
#                   /\ UNCHANGED unchecked
#                \/ /\ flag' = [flag EXCEPT ![self] = FALSE]
#                   /\ unchecked' = [unchecked EXCEPT ![self] = Procs \ {self}]
#                   /\ pc' = [pc EXCEPT ![self] = "w1"]
#             /\ UNCHANGED << num, max, nxt >>
@action(inline=False)
def e4(c, q):
    s = c.state
    c.assume(s.pc[q] == Val(PC.E4))
    toggle_flag, clear_flag = c.alternatives("ToggleFlag", "ClearFlag")
    with toggle_flag:
        s.flag[q] = ~s.flag[q]
        s.pc[q] = PC.E4
    with clear_flag:
        s.flag[q] = False
        s.unchecked[q] = procs(s) - Set(q)
        s.pc[q] = PC.W1


# w1(self) == /\ pc[self] = "w1"
#             /\ IF unchecked[self] # {}
#                   THEN /\ \E i \in unchecked[self]:
#                             nxt' = [nxt EXCEPT ![self] = i]
#                        /\ ~ flag[nxt'[self]]
#                        /\ pc' = [pc EXCEPT ![self] = "w2"]
#                   ELSE /\ pc' = [pc EXCEPT ![self] = "cs"]
#                        /\ nxt' = nxt
#             /\ UNCHANGED << num, flag, unchecked, max >>
@action(inline=False)
def w1(c, q):
    s = c.state
    c.assume(s.pc[q] == Val(PC.W1))
    then_, else_ = c.split(~s.unchecked[q].is_empty)
    with then_, c.one_of(s.unchecked[q], "i") as i:
        s.nxt[q] = i
        c.assume(~s.flag[s.nxt[q]])
        s.pc[q] = PC.W2
    with else_:
        s.pc[q] = PC.CS


# w2(self) == /\ pc[self] = "w2"
#             /\ \/ num[nxt[self]] = 0
#                \/ <<num[self], self>> \prec <<num[nxt[self]], nxt[self]>>
#             /\ unchecked' = [unchecked EXCEPT ![self] = unchecked[self] \ {nxt[self]}]
#             /\ pc' = [pc EXCEPT ![self] = "w1"]
#             /\ UNCHANGED << num, flag, max, nxt >>
@action(inline=False)
def w2(c, q):
    s = c.state
    c.assume(s.pc[q] == Val(PC.W2))
    c.assume(
        Or(
            s.num[s.nxt[q]] == Val(0),
            precedes((s.num[q], q), (s.num[s.nxt[q]], s.nxt[q])),
        )
    )
    s.unchecked[q] -= Set(s.nxt[q])
    s.pc[q] = PC.W1


# cs(self) == /\ pc[self] = "cs"
#             /\ TRUE
#             /\ pc' = [pc EXCEPT ![self] = "exit"]
#             /\ UNCHANGED << num, flag, unchecked, max, nxt >>
@action(inline=False)
def cs(c, q):
    s = c.state
    c.assume(s.pc[q] == Val(PC.CS))
    s.pc[q] = PC.EXIT


# exit(self) == /\ pc[self] = "exit"
#               /\ \/ /\ \E k \in Nat:
#                          num' = [num EXCEPT ![self] = k]
#                     /\ pc' = [pc EXCEPT ![self] = "exit"]
#                  \/ /\ num' = [num EXCEPT ![self] = 0]
#                     /\ pc' = [pc EXCEPT ![self] = "ncs"]
#               /\ UNCHANGED << flag, unchecked, max, nxt >>
@action(inline=False)
def exit(c, q):
    s = c.state
    c.assume(s.pc[q] == Val(PC.EXIT))
    stay_exit, to_ncs = c.alternatives("StayExit", "ToNCS")
    with stay_exit, c.one_of(Ints, "k") as k:
        c.assume(k >= 0)
        s.num[q] = k
        s.pc[q] = PC.EXIT
    with to_ncs:
        s.num[q] = 0
        s.pc[q] = PC.NCS


# p(self) == ncs(self) \/ e1(self) \/ e2(self) \/ e3(self) \/ e4(self)
#              \/ w1(self) \/ w2(self) \/ cs(self) \/ exit(self)
@action(inline=False)
def p(c, q):
    alts = iter(c.alternatives(*(label.name for label in PROCESS_PCS)))
    with next(alts):
        ncs(c, q)
    with next(alts):
        e1(c, q)
    with next(alts):
        e2(c, q)
    with next(alts):
        e3(c, q)
    with next(alts):
        e4(c, q)
    with next(alts):
        w1(c, q)
    with next(alts):
        w2(c, q)
    with next(alts):
        cs(c, q)
    with next(alts):
        exit(c, q)


# Init == (* Global variables *)
#        /\ num = [i \in Procs |-> 0]
#        /\ flag = [i \in Procs |-> FALSE]
#        (* Process p *)
#        /\ unchecked = [self \in Procs |-> {}]
#        /\ max = [self \in Procs |-> 0]
#        /\ nxt = [self \in Procs |-> 1]
#        /\ pc = [self \in ProcSet |-> "ncs"]
@action(init=True)
def Init(c):
    s, ps = c.state, procs(c.state)
    s.num = ps.map_to(lambda _: Val(0))
    s.flag = ps.map_to(lambda _: Val(False))
    s.unchecked = ps.map_to(lambda _: Set(int))
    s.max = ps.map_to(lambda _: Val(0))
    s.nxt = ps.map_to(lambda _: Val(1))
    s.pc = ps.map_to(lambda _: Val(PC.NCS))


# Next == (\E self \in Procs: p(self))
@action
def Next(c):
    with c.one_of(procs(c.state), "q") as q:
        p(c, q)


# Spec == /\ Init /\ [][Next]_vars
#         /\ \A self \in Procs : WF_vars((pc[self] # "ncs") /\ p(self))

# MutualExclusion == \A i,j \in Procs : (i # j) => ~ /\ pc[i] = "cs"
#                                                    /\ pc[j] = "cs"


@invariant
def mutual_exclusion(s: BakeryState):
    return procs(s).forall(
        lambda i: procs(s).forall(
            lambda j: (i != j).implies(
                (s.pc[i] != Val(PC.CS)).or_(s.pc[j] != Val(PC.CS))
            )
        )
    )
    # In the future, we want to write it like this as well:
    #
    # return forall((i != j).implies((s.pc[i] != Val(PC.CS)).or_(s.pc[j] != Val(PC.CS)))
    #              for i in procs(s) for j in procs(s))


@coverage
def state_cov(s: BakeryState) -> Expr:
    return Tuple(s.N, s.num, s.flag, s.unchecked, s.max, s.nxt, s.pc)


# A few proto-states for producing instances.

proto5 = BakeryState(N=5)


@instance
def n3() -> BakeryState:
    return BakeryState(N=3)
