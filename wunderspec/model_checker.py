"""
A simple model checker that checks state invariants by exploring all possible
states reachable from the initial state. This is not meant to be a full-fledged
model checker, but rather a simple implementation that can be used for testing
and debugging purposes.

Obviously, a model checker in Python is not expected to be very performant.
However, consider it as a reference implementation that can be used to test the
correctness of more sophisticated implementations (e.g. in Rust).

General temporal properties are to be supported in the future.

Igor Konnov, 2026
"""

from copy import copy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from pyrsistent import PMap, pmap

from wunderspec import IValue, RecordValue, StateView, state_view
from wunderspec.ast.action_ast import ActionNode
from wunderspec.ast.ast import LitNode, Node
from wunderspec.errors import EvaluationError
from wunderspec.exec.action_exec import action_execute
from wunderspec.exec.scheduler import (
    EnumerativeScheduler,
    RecordingScheduler,
    SchedulerDecision,
)
from wunderspec.interpreter import native_action_context, value
from wunderspec.interpreter_value import to_python
from wunderspec.machine import MachineState, MachineStateBase
from wunderspec.source_tracking import enable_source_tracking
from wunderspec.sym_context import SymbolicContext


@dataclass(frozen=True)
class ModelCheckerResult:
    """Result of a model checking run."""

    trace: Optional[tuple[StateView, ...]]
    # None if invariant holds, otherwise the first counterexample trace
    produced_states_cnt: int
    # Number of states explored during the check
    distinct_states_cnt: int
    # Number of distinct states visited during the check
    schedule: Optional[tuple[tuple[SchedulerDecision, ...], ...]] = None
    # Decisions for each accepted step in the first counterexample trace.
    traces: tuple[tuple[StateView, ...], ...] = ()
    # All counterexample traces collected (up to max_findings). ``trace`` is the
    # first of these, kept separately for backward compatibility.
    schedules: tuple[tuple[tuple[SchedulerDecision, ...], ...], ...] = ()
    # Decisions for each accepted step in each collected trace, aligned with
    # ``traces``. ``schedule`` is the first of these.


@dataclass(frozen=True)
class ModelCheckerInput:
    """Input to the model checker."""

    proto: MachineStateBase
    # The protocol instance to check
    init_node: ActionNode
    # The initial action node
    step_node: ActionNode
    # The step action node
    invariant_node: Node
    # The invariant expression node
    bound: int = 2**31 - 1
    # The bound for systematic exploration (e.g., max number of states to explore)
    max_steps: int | None = None
    # Optional DFS cutoff in step transitions from init. None means unlimited.
    shuffle_seed: int | None = None
    # If set, apply keyed permutation to diversify DFS exploration order.
    native_action_proto: MachineState | None = None
    # Prototype state used to build named action bodies for native Enabled(...).
    native_actions: Mapping[str, Callable[..., Any]] | None = None
    # Action functions available for native Enabled(...) resolution.


def init_model_checker_input(
    proto,
    init_action,
    step_action,
    invariant_expr=None,
    bound: int = 2**31 - 1,
    max_steps: int | None = None,
    shuffle_seed: int | None = None,
    native_action_proto: MachineState | None = None,
    native_actions: Mapping[str, Callable[..., Any]] | None = None,
) -> ModelCheckerInput:
    """Compile the model checker input from the protocol and the actions/invariant."""
    # Source tracking is enabled only around the one-time build so AST nodes
    # carry source spans, letting evaluation errors be traced back to the spec.
    with enable_source_tracking():
        sym_context = SymbolicContext(copy(proto), inline_all=True)
        init_action(sym_context)
        init_node = sym_context.build()
        sym_context = SymbolicContext(copy(proto), inline_all=True)
        step_action(sym_context)
        step_node = sym_context.build()
    if invariant_expr is not None:
        sym_state = copy(proto)
        invariant_node = invariant_expr(sym_state).node
    else:
        invariant_node = LitNode(True)
    return ModelCheckerInput(
        proto=proto,
        init_node=init_node,
        step_node=step_node,
        invariant_node=invariant_node,
        bound=bound,
        max_steps=max_steps,
        shuffle_seed=shuffle_seed,
        native_action_proto=native_action_proto,
        native_actions=native_actions,
    )


# Callback signature: (produced_states_cnt, distinct_states_cnt) -> None
CheckProgressCallback = Callable[[int, int], None]

# Callback invoked the moment a counterexample is found, before the search
# continues: (finding_index, trace_states, schedule) -> None
OnFindingCallback = Callable[
    [int, tuple[StateView, ...], tuple[tuple[SchedulerDecision, ...], ...]],
    None,
]


def check_dfs(
    input: ModelCheckerInput,
    exact: bool = False,
    on_progress: CheckProgressCallback | None = None,
    max_findings: int = 1,
    on_finding: OnFindingCallback | None = None,
) -> ModelCheckerResult:
    """
    Model checking by depth-first search. Returns up to ``max_findings``
    counterexample traces if the invariant is violated, or no traces if the
    invariant holds in all reachable states.

    This is a textbook depth-first search with state fingerprinting, adapted to
    our circumstates.

    Args:
        input: The model checker input.
        exact: If True, use exact state equality (RecordValue in a set) instead
               of fingerprint-based deduplication. Exact mode is slower but
               immune to fingerprint collisions. Defaults to False.
        on_progress: Optional callback invoked after each produced state with
                     (produced_states_cnt, distinct_states_cnt).
        max_findings: Stop after collecting this many distinct counterexample
                      traces. Each newly-discovered violating state yields one
                      trace and is then treated as a leaf (DFS does not descend
                      into it), so the search keeps looking for other violating
                      states. Defaults to 1.
        on_finding: Optional callback invoked the moment each counterexample is
                    recorded, with (finding_index, trace_states, schedule),
                    enabling streaming of findings as they are discovered.
    """
    # counterexample traces collected so far, and their schedules
    found_traces: list[tuple[StateView, ...]] = []
    found_schedules: list[tuple[tuple[SchedulerDecision, ...], ...]] = []

    def _result(produced: int, distinct: int) -> ModelCheckerResult:
        return ModelCheckerResult(
            trace=found_traces[0] if found_traces else None,
            produced_states_cnt=produced,
            distinct_states_cnt=distinct,
            schedule=found_schedules[0] if found_schedules else None,
            traces=tuple(found_traces),
            schedules=tuple(found_schedules),
        )

    # the current trace being explored
    trace: list[PMap[str, IValue]] = []
    # scheduler decisions that produced each state in ``trace``
    trace_schedule: list[tuple[SchedulerDecision, ...]] = []
    # the current states of the action/data schedulers for each step in the trace
    schedulers: list[Optional[EnumerativeScheduler]] = [None]
    # visited states: fingerprint set (fast) or RecordValue set (exact)
    visited_fingerprints: set[int] = set()
    visited_states: set[RecordValue] = set()
    # the total number of states produced during the exploration
    produced_states_cnt = 0
    while len(schedulers) > 0:
        # cutoff depth is counted in transitions from init:
        # depth(trace)=len(trace)-1 when trace is non-empty.
        if (
            input.max_steps is not None
            and len(trace) > 0
            and len(trace) > input.max_steps
        ):
            schedulers.pop()
            trace.pop()
            trace_schedule.pop()
            continue

        sched = schedulers[-1]
        if sched is None:
            # first time at this step, create a new scheduler
            sched = EnumerativeScheduler(
                bound=input.bound, shuffle_seed=input.shuffle_seed
            )
            schedulers[-1] = sched
        elif sched.enumerator.next_schedule() is False:
            # no more schedules to explore at this step, backtrack
            schedulers.pop()
            if len(trace) > 0:
                trace.pop()
                trace_schedule.pop()
                continue
            else:
                # we have exhausted all the schedules
                distinct = len(visited_states) if exact else len(visited_fingerprints)
                return _result(produced_states_cnt, distinct)

        # use the schedule to execute the action and get the new state
        node: ActionNode = input.init_node if len(trace) == 0 else input.step_node
        env: PMap[str, IValue] = trace[-1] if len(trace) > 0 else pmap()
        recording_sched = RecordingScheduler(sched)
        try:
            new_env = action_execute(node, env, recording_sched)
        except EvaluationError as ev:
            ev.step_index = len(trace)
            raise
        if new_env is None:
            # this schedule is not feasible, e.g., due to violated assumptions
            pass
        else:
            produced_states_cnt += 1
            distinct = len(visited_states) if exact else len(visited_fingerprints)
            if on_progress is not None:
                on_progress(produced_states_cnt, distinct)
            # check whether we have visited this state before
            state_rv = RecordValue(**new_env)
            is_new = (
                state_rv not in visited_states
                if exact
                else state_rv.fingerprint() not in visited_fingerprints
            )
            if is_new:
                # this is a new state
                if exact:
                    visited_states.add(state_rv)
                else:
                    visited_fingerprints.add(state_rv.fingerprint())
                trace.append(new_env)
                trace_schedule.append(tuple(recording_sched.decisions))
                schedulers.append(None)
                # check the invariant
                with native_action_context(
                    input.native_action_proto,
                    input.native_actions,
                ):
                    inv_value = to_python(value(input.invariant_node, new_env))
                if inv_value is False:
                    # invariant violated: record this counterexample trace
                    states = tuple(state_view(input.proto, env) for env in trace)
                    schedule_snapshot = tuple(trace_schedule)
                    found_traces.append(states)
                    found_schedules.append(schedule_snapshot)
                    if on_finding is not None:
                        on_finding(len(found_traces) - 1, states, schedule_snapshot)
                    distinct = (
                        len(visited_states) if exact else len(visited_fingerprints)
                    )
                    if len(found_traces) >= max_findings:
                        return _result(produced_states_cnt, distinct)
                    # treat the violating state as a leaf: backtrack instead of
                    # descending into it, and keep searching for other findings.
                    # The state stays in the visited set, so it is not revisited.
                    schedulers.pop()
                    trace.pop()
                    trace_schedule.pop()
    else:
        assert False, "error in the model checker loop"
