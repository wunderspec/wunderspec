from collections.abc import Callable, Mapping
from typing import Optional, TypeVar, cast

from pyrsistent import PMap, pmap

from wunderspec.ast.action_ast import (
    ActionAndNode,
    ActionCallNode,
    ActionChoiceNode,
    ActionLetNode,
    ActionNode,
    AssignNode,
    AssumeNode,
    NondetChoiceNode,
)
from wunderspec.exec.action_profile import ActionProfiler
from wunderspec.exec.scheduler import (
    RandomScheduler,
    Scheduler,
    SchedulerAlternative,
    SchedulerRequestAlternative,
    SchedulerRequestOneOf,
    SchedulerValue,
)
from wunderspec.expr import Expr
from wunderspec.interpreter import value
from wunderspec.interpreter_value import BoolValue, IValue, IValueNode, StateView
from wunderspec.machine import MachineStateBase

State = TypeVar("State", bound=MachineStateBase)

# Callback type for action tracing
OnActionCallback = Callable[[ActionNode], None]
OnReplayStepCallback = Callable[[tuple[ActionNode, ...]], None]


def state_view(
    proto_state: MachineStateBase,
    mapping: Mapping[str, IValue],
    params: Mapping[str, IValue] | None = None,
) -> StateView:
    """Create a :class:`StateView` from a proto state and a variable mapping.

    Extracts parameter values from *proto_state* (evaluating each parameter
    expression to an ``IValue``) and combines them with *mapping* (the
    variable bindings, e.g. a ``PMap`` yielded by :func:`random_traces`).

    The resulting ``StateView`` supports attribute access returning ``Expr``
    objects, so it can be passed directly to invariant functions.
    """
    if params is None:
        params = {}
        for p in proto_state._params:
            params[p] = value(getattr(proto_state, p))
    return StateView(mapping, params)


def action_execute(
    node: ActionNode,
    env: PMap[str, IValue] = pmap(),
    scheduler: Optional[Scheduler] = None,
    on_action: Optional[OnActionCallback] = None,
    profiler: Optional[ActionProfiler] = None,
) -> Optional[PMap[str, IValue]]:
    """
    Interpret action nodes according to the scheduler. Produce a new environment
    that binds state fields to the interpreted values. If the execution is
    impossible, e.g., due to invalid assumptions made earlier, return None.
    """
    if scheduler is None:
        scheduler = RandomScheduler()

    if on_action is not None:
        on_action(node)

    match node:
        case AssumeNode():
            cond_value = value(node.condition, env)
            if cond_value == BoolValue(True):
                return env
            else:
                return None

        case AssignNode():
            var_name = node.var.name  # type: ignore[attr-defined]
            return env.set(var_name, value(node.expr, env))

        case ActionAndNode():
            current_env: PMap[str, IValue] = env
            for act in node.actions:
                result = action_execute(
                    act, current_env, scheduler, on_action, profiler
                )
                if result is None:
                    return None
                current_env = result

            return current_env

        case ActionChoiceNode():
            # TODO: refactor ActionChoiceNode to carry names.
            # It's not essential for random choice, but important for usability.
            names = tuple(f"act{i}" for i in range(len(node.actions)))
            decision = scheduler.decide(SchedulerRequestAlternative(names))
            if not isinstance(decision, SchedulerAlternative):
                raise ValueError(f"Expected SchedulerAlternative, found: {decision}")

            chosen_idx = int(decision.chosen.removeprefix("act"))
            chosen_action = node.actions[chosen_idx]
            return action_execute(chosen_action, env, scheduler, on_action, profiler)

        case ActionCallNode():
            if profiler is not None:
                profiler.enter(node.action_name)
            result = action_execute(
                cast(ActionNode, node.body), env, scheduler, on_action, profiler
            )
            if profiler is not None and result is not None:
                profiler.succeeded(node.action_name)
            return result

        case NondetChoiceNode():
            var_name = node.var.name  # type: ignore[attr-defined]
            if var_name in env:
                raise RuntimeError(f"Name collision: {var_name} is declared twice")
            body = cast(ActionNode, node.body)
            set_value = value(node.base_set, env)
            decision = scheduler.decide(
                SchedulerRequestOneOf(Expr(IValueNode(set_value)))
            )
            if not isinstance(decision, SchedulerValue):
                raise ValueError(f"Expected Value, found: {decision}")
            val = value(decision.value)

            env_with_binding = env.set(var_name, val)
            new_env = action_execute(
                body, env_with_binding, scheduler, on_action, profiler
            )
            if new_env is None:
                return None
            else:
                # remove the introduced binding
                return new_env.remove(var_name)

        case ActionLetNode():
            var_name = node.name
            if var_name in env:
                raise RuntimeError(f"Name collision: {var_name} is declared twice")
            val = value(node.value, env)
            env_with_binding = env.set(var_name, val)
            body = cast(ActionNode, node.body)
            new_env = action_execute(
                body, env_with_binding, scheduler, on_action, profiler
            )
            if new_env is None:
                return None
            else:
                return new_env.remove(var_name)

        case _:
            raise ValueError(f"Unsupported action node type: {type(node).__name__}")
