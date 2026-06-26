"""
A simple implementation of a random walk engine. It generates traces as tuples
of concrete states. The initial state is computed with `init_action` and
subsequent states are evaluated with `next_action`.

The main point is to keep the code simple and understandable. A high-performance
exploration engine can consume the AST nodes and do the high-performance
computation in Rust or similar.

Igor Konnov, 2026
"""

import inspect
import random
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from typing import Iterator, Optional, TypeVar

from pyrsistent import PMap, pmap

from wunderspec.ast.action_ast import ActionCallNode, ActionNode, AssumeNode
from wunderspec.ast.ast import SourceSpan
from wunderspec.errors import EvaluationError, locate_eval_errors
from wunderspec.exec import (
    ActionProfiler,
    AssumptionViolated,
    ExecContext,
    RandomScheduler,
    Scheduler,
    action_execute,
    collect_action_names,
    state_view,
)
from wunderspec.expr import Expr
from wunderspec.interpreter import value
from wunderspec.interpreter_value import IValue, IValueNode, RecordValue, StateView
from wunderspec.lang import Val
from wunderspec.machine import Context, MachineStateBase
from wunderspec.source_tracking import enable_source_tracking
from wunderspec.sym_context import SymbolicContext

State = TypeVar("State", bound=MachineStateBase)
_Step = TypeVar("_Step")

# Callback type for action tracing
OnActionCallback = Callable[[ActionNode], None]
OnReplayStepCallback = Callable[[tuple[ActionNode, ...]], None]


def _walk_trace(
    max_steps: int,
    max_retries_per_step: int,
    do_step: Callable[[int], Optional[_Step]],
) -> list[_Step]:
    """Drive a single trace by repeatedly attempting one step.

    Calls ``do_step(step_index)`` for each attempt; a non-``None`` result is a
    successful step that is appended and advances the trace, while ``None`` is a
    failed attempt that consumes one retry. The trace is cut when it reaches
    ``max_steps`` in length or when the retry budget
    (``max_retries_per_step * max_steps``) is exhausted. ``do_step`` may raise to
    abort the walk early (used by :func:`locate_evaluation_error`).

    This is the single place that owns the retry-budget and cut policy shared by
    all the exploration functions below.
    """
    trace: list[_Step] = []
    retries = max_retries_per_step * max_steps
    while len(trace) < max_steps and retries > 0:
        result = do_step(len(trace))
        if result is not None:
            trace.append(result)
        else:
            retries -= 1
    return trace


def _evaluate_state_params(proto_state: State) -> dict[str, IValue]:
    """Evaluate proto-state parameters once for repeated StateView construction."""
    params: dict[str, IValue] = {}
    for p in proto_state._params:
        params[p] = value(getattr(proto_state, p))
    return params


@dataclass
class WalkSettings:
    """
    Configuration parameters for random walk.
    """

    seed: int | None = None
    max_steps: int = 100
    max_retries_per_step: int = 3
    max_size: int | None = None
    # Default bound for integer sampling (max int32)
    bound: int = 2**31 - 1


def random_traces(
    proto_state: State,
    init_action: Callable[[Context[State]], None],
    step_action: Callable[[Context[State]], None],
    settings: WalkSettings | None = None,
    profiler: Optional[ActionProfiler] = None,
) -> Iterator[tuple[int, tuple[StateView, ...]]]:
    """
    Randomly walk by first applying `init_action` to `proto_state`,
    then making up to `settings.max_len` steps with `step_action`.
    This function translates the actions to AST nodes first, in
    order to avoid repetitive computations over expressions.

    When ``profiler`` is provided, the actions are translated with
    ``inline_all=False`` so that non-inline actions keep their identity as
    ``ActionCallNode`` and per-action tried/fired counts are accumulated.

    Yield every trace as ``(trace_seed, trace)``.
    If you don't need more traces, simply stop iterating.
    """
    if settings is None:
        settings = WalkSettings()

    rng = random.Random(settings.seed)

    # When not profiling, translate the actions with inline_all=True to avoid
    # ActionCallNode indirection in the per-step hot loop. When profiling, keep
    # inline_all=False so non-inline actions remain named ActionCallNodes.
    # Source tracking is enabled only around the one-time build so that AST
    # nodes carry source spans; this lets evaluation errors be traced back to
    # the spec source without slowing down the per-step hot loop below.
    inline_all = profiler is None
    with enable_source_tracking():
        sym_context = SymbolicContext(copy(proto_state), inline_all=inline_all)
        init_action(sym_context)
        init_node = sym_context.build()
        sym_context = SymbolicContext(copy(proto_state), inline_all=inline_all)
        step_action(sym_context)
        step_node = sym_context.build()
    if profiler is not None:
        # Seed every known action so even never-tried ones appear as 0/0.
        for node in (init_node, step_node):
            for name in collect_action_names(node):
                profiler.register(name)
    params = _evaluate_state_params(proto_state)

    while True:
        trace_seed = rng.randint(0, 2**63)
        trace_rng = random.Random(trace_seed)
        scheduler = RandomScheduler(rng=trace_rng, bound=settings.bound)
        env: PMap[str, IValue] = pmap()

        def do_step(step_index: int) -> Optional[StateView]:
            nonlocal env
            node = init_node if step_index == 0 else step_node
            try:
                new_env = action_execute(node, env, scheduler, profiler=profiler)
            except EvaluationError as ev:
                ev.trace_seed = trace_seed
                ev.step_index = step_index
                raise
            if new_env is None:
                return None
            env = new_env
            return state_view(proto_state, new_env, params)

        trace = _walk_trace(settings.max_steps, settings.max_retries_per_step, do_step)
        yield trace_seed, tuple(trace)


def random_traces_debug(
    proto_state: State,
    init_action: Callable[[Context[State]], None],
    step_action: Callable[[Context[State]], None],
    settings: WalkSettings | None = None,
) -> Iterator[tuple[int, tuple[StateView, ...]]]:
    """
    Randomly walk by first applying `init_action` to `proto_state`,
    then making up to `settings.max_len` steps with `step_action`.
    This function is more computationally expensive than `random_traces`.
    However, it has better debugging experience, as it raises exceptions
    directly from the specification code, not from the AST expressions.
    Hence, we recommend using this function for debugging.

    Yield every trace as ``(trace_seed, trace)``.
    If you don't need more traces, simply stop iterating.
    """
    if settings is None:
        settings = WalkSettings()

    rng = random.Random(settings.seed)

    while True:
        params = _evaluate_state_params(proto_state)
        trace_seed = rng.randint(0, 2**63)
        trace_rng = random.Random(trace_seed)
        scheduler = RandomScheduler(rng=trace_rng, bound=settings.bound)

        context = ExecContext(copy(proto_state), scheduler)

        def do_step(step_index: int) -> Optional[StateView]:
            try:
                act = init_action if step_index == 0 else step_action
                context.step(act)
                concrete_state = value(context.state)
                if not isinstance(concrete_state, RecordValue):
                    raise RuntimeError(
                        f"Expected state to be a RecordValue, found: {type(concrete_state)}"
                    )
                propagate_values(context, concrete_state)
                return state_view(proto_state, concrete_state, params)
            except AssumptionViolated:
                context.revert()
                return None

        trace = _walk_trace(settings.max_steps, settings.max_retries_per_step, do_step)
        yield trace_seed, tuple(trace)


def random_traces_replay(
    proto_state: State,
    init_action: Callable[[Context[State]], None],
    step_action: Callable[[Context[State]], None],
    settings: WalkSettings,
    scheduler: Optional[Scheduler] = None,
    on_action: Optional[OnActionCallback] = None,
    on_step: Optional[OnReplayStepCallback] = None,
) -> Iterator[tuple[int, tuple[StateView, ...]]]:
    """
    Replay a single trace using the seed from ``settings.seed`` directly
    as the trace RNG seed.  Uses ``inline_all=False`` so that
    ``ActionCallNode`` nodes are preserved in the AST, and enables source
    tracking so that source spans are recorded.

    Yields exactly one ``(trace_seed, trace)`` pair.
    """
    if scheduler is None and settings.seed is None:
        raise ValueError("settings.seed is required for replay")

    trace_seed = settings.seed if settings.seed is not None else 0
    if scheduler is None:
        trace_rng = random.Random(trace_seed)
        scheduler = RandomScheduler(rng=trace_rng, bound=settings.bound)

    # translate the actions with inline_all=False to keep ActionCallNode
    with enable_source_tracking():
        sym_context = SymbolicContext(copy(proto_state), inline_all=False)
        init_action(sym_context)
        init_node = sym_context.build()
        sym_context = SymbolicContext(copy(proto_state), inline_all=False)
        step_action(sym_context)
        step_node = sym_context.build()
    params = _evaluate_state_params(proto_state)

    env: PMap[str, IValue] = pmap()
    collect_actions = on_action is not None or on_step is not None

    def do_step(step_index: int) -> Optional[StateView]:
        nonlocal env
        node = init_node if step_index == 0 else step_node
        step_actions: list[ActionNode] = []
        replay_callback = step_actions.append if collect_actions else None

        new_env = action_execute(node, env, scheduler, replay_callback)
        if new_env is None:
            return None
        if on_step is not None:
            on_step(tuple(step_actions))
        if on_action is not None:
            for step_action_node in step_actions:
                on_action(step_action_node)
        env = new_env
        return state_view(proto_state, new_env, params)

    trace = _walk_trace(settings.max_steps, settings.max_retries_per_step, do_step)
    yield trace_seed, tuple(trace)


def locate_evaluation_error(
    proto_state: State,
    init_action: Callable[[Context[State]], None],
    step_action: Callable[[Context[State]], None],
    settings: WalkSettings,
    scheduler: Optional[Scheduler] = None,
) -> Optional[EvaluationError]:
    """Re-run a failing scenario to recover the chain of actions that led to
    an evaluation error.

    Reproduces the trace identified by ``settings.seed`` (or ``scheduler``)
    using ``inline_all=False`` so that ``ActionCallNode`` nodes are preserved,
    with source tracking enabled. When the same :class:`EvaluationError` is
    raised again, its ``action_chain`` is set to the actions collected for the
    failing step and the error is returned (not raised). Returns ``None`` if the
    failure does not reproduce.
    """
    if scheduler is None and settings.seed is None:
        raise ValueError("settings.seed is required to locate an evaluation error")

    trace_seed = settings.seed if settings.seed is not None else 0
    if scheduler is None:
        trace_rng = random.Random(trace_seed)
        scheduler = RandomScheduler(rng=trace_rng, bound=settings.bound)

    # translate the actions with inline_all=False to keep ActionCallNode
    with enable_source_tracking():
        sym_context = SymbolicContext(copy(proto_state), inline_all=False)
        init_action(sym_context)
        init_node = sym_context.build()
        sym_context = SymbolicContext(copy(proto_state), inline_all=False)
        step_action(sym_context)
        step_node = sym_context.build()

    env: PMap[str, IValue] = pmap()

    def do_step(step_index: int) -> Optional[PMap[str, IValue]]:
        nonlocal env
        node = init_node if step_index == 0 else step_node
        step_actions: list[ActionNode] = []
        try:
            new_env = action_execute(node, env, scheduler, step_actions.append)
        except EvaluationError as ev:
            ev.trace_seed = trace_seed
            ev.step_index = step_index
            ev.action_chain = tuple(step_actions)
            raise
        if new_env is None:
            return None
        env = new_env
        return new_env

    with locate_eval_errors():
        try:
            # The returned trace is ignored; we only care whether an
            # EvaluationError surfaces while reproducing the run.
            _walk_trace(settings.max_steps, settings.max_retries_per_step, do_step)
        except EvaluationError as ev:
            return ev

    return None


class _TracingExecContext(ExecContext[State]):
    """ExecContext subclass that invokes an on_action callback in begin_action."""

    _allowed_attrs = ExecContext._allowed_attrs | {"_on_action"}

    def __init__(
        self,
        proto_state: State,
        scheduler: Scheduler,
        on_action: OnActionCallback,
    ):
        super().__init__(proto_state, scheduler)
        self._on_action = on_action

    def begin_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> tuple[object, ...]:
        if action_func is not None and self._on_action is not None:
            action_name = getattr(action_func, "_action_name", None)
            if action_name is not None:
                wrapped = getattr(action_func, "__wrapped__", action_func)
                try:
                    source_file = inspect.getfile(wrapped)  # type: ignore[arg-type]
                    source_lines = inspect.getsourcelines(wrapped)  # type: ignore[arg-type]
                    lineno = source_lines[1]
                except (TypeError, OSError):
                    source_file = None
                    lineno = 0

                dummy = ActionCallNode(action_name, (), AssumeNode(Val(True).node))
                if source_file is not None:
                    dummy.source_span = SourceSpan(
                        filename=source_file,
                        lineno=lineno,
                        col_offset=0,
                        end_lineno=lineno,
                        end_col_offset=0,
                    )
                self._on_action(dummy)
        return action_args


def random_traces_debug_replay(
    proto_state: State,
    init_action: Callable[[Context[State]], None],
    step_action: Callable[[Context[State]], None],
    settings: WalkSettings,
    scheduler: Optional[Scheduler] = None,
    on_action: Optional[OnActionCallback] = None,
    on_step: Optional[OnReplayStepCallback] = None,
) -> Iterator[tuple[int, tuple[StateView, ...]]]:
    """
    Replay a single trace in debug mode using the seed from
    ``settings.seed`` directly as the trace RNG seed.

    Yields exactly one ``(trace_seed, trace)`` pair.
    """
    if scheduler is None and settings.seed is None:
        raise ValueError("settings.seed is required for replay")

    trace_seed = settings.seed if settings.seed is not None else 0
    if scheduler is None:
        trace_rng = random.Random(trace_seed)
        scheduler = RandomScheduler(rng=trace_rng, bound=settings.bound)

    collect_actions = on_action is not None or on_step is not None
    step_actions: list[ActionNode] = []
    replay_callback = step_actions.append if collect_actions else None

    if collect_actions:
        assert replay_callback is not None
        context: ExecContext[State] = _TracingExecContext(
            copy(proto_state), scheduler, replay_callback
        )
    else:
        context = ExecContext(copy(proto_state), scheduler)

    def do_step(step_index: int) -> Optional[StateView]:
        if collect_actions:
            step_actions.clear()
        try:
            act = init_action if step_index == 0 else step_action
            context.step(act)
            concrete_state = value(context.state)
            if not isinstance(concrete_state, RecordValue):
                raise RuntimeError(
                    f"Expected state to be a RecordValue, found: {type(concrete_state)}"
                )
            if on_step is not None:
                on_step(tuple(step_actions))
            if on_action is not None:
                for step_action_node in step_actions:
                    on_action(step_action_node)
            propagate_values(context, concrete_state)
            return state_view(proto_state, concrete_state)
        except AssumptionViolated:
            context.revert()
            return None

    trace = _walk_trace(settings.max_steps, settings.max_retries_per_step, do_step)
    yield trace_seed, tuple(trace)


def propagate_values(c: "ExecContext[State]", sv: IValue) -> None:
    """
    Substitute state fields with their values. Since symbolic state tend to
    grow very fast after several transitions, we simply propagate the computed
    values into the state. Although the state is not truly symbolic anymore,
    its fields act as expression as long as concrete interpretation is concerned.
    """

    for var in c.state._vars:
        # hide the concrete value under IValueNode
        expr = Expr(IValueNode(sv[var]))  # type: ignore[index]
        setattr(c.state, var, expr)

    c.finalize()
