"""Programmatic API for Wunderspec commands.

This module contains the command logic used by the CLI, exposed as Python-callable
functions without requiring subprocess execution.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import random
import re
import shlex
import sys
import tempfile
import time
from collections import OrderedDict
from copy import copy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal, NoReturn, Protocol, TextIO

from itf_py import value_from_json, value_to_json
from tqdm.auto import tqdm  # type: ignore[import-untyped]

from wunderspec._edition import feature_message, is_feature_enabled
from wunderspec.ast.action_ast import (
    ActionCallNode,
    ActionLetNode,
    ActionNode,
    NondetChoiceNode,
)
from wunderspec.ast.ast import LetNode, Node, VarNode
from wunderspec.ast.list_ast import ListFilterNode, ListReduceNode
from wunderspec.ast.map_ast import MapLambdaNode
from wunderspec.ast.set_ast import (
    ChooseNode,
    SetFilterNode,
    SetMapNode,
    SetQuantNode,
    SetReduceNode,
)
from wunderspec.errors import EvaluationError, locate_eval_errors
from wunderspec.exec import (
    RecordingScheduler,
    SchedulerAlternative,
    SchedulerChoiceIndex,
    SchedulerDecision,
    SchedulerRawValue,
    SchedulerSplit,
    SchedulerValue,
    ScriptedScheduler,
)
from wunderspec.explain import render_itf_bdd_explanation, render_itf_explanation
from wunderspec.expr import Expr
from wunderspec.interpreter import native_action_context, value
from wunderspec.interpreter_value import BoolValue, StateView
from wunderspec.itf_trace import itf_trace_json_line, value_to_itf, write_itf_trace
from wunderspec.lang import Not
from wunderspec.machine import MachineState, MachineStateBase, find_instance_factories
from wunderspec.model_checker import check_dfs, init_model_checker_input
from wunderspec.random_walk import (
    WalkSettings,
    locate_evaluation_error,
    random_traces,
    random_traces_debug,
    random_traces_debug_replay,
    random_traces_replay,
)
from wunderspec.sym_context import ActionDefs, SymbolicContext
from wunderspec.tla import to_tla, to_tla_instance
from wunderspec.trace_output import (
    BLUE,
    BOLD,
    RESET,
    TraceStyle,
    print_state,
    print_trace,
)


class ApiError(Exception):
    """A user-facing API/CLI error with an optional process exit code."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class Reporter(Protocol):
    """Output adapter used by API commands."""

    def info(self, msg: str) -> None: ...

    def success(self, msg: str) -> None: ...

    def warn(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...

    def hint(self, msg: str) -> None: ...

    def out(self, msg: str = "") -> None: ...


class _NullReporter:
    def info(self, msg: str) -> None:
        pass

    def success(self, msg: str) -> None:
        pass

    def warn(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass

    def hint(self, msg: str) -> None:
        pass

    def out(self, msg: str = "") -> None:
        pass


NULL_REPORTER = _NullReporter()
TLA2TOOLS_VERSION = "v1.8.0"
TLA2TOOLS_URL = (
    "https://github.com/tlaplus/tlaplus/releases/download/"
    f"{TLA2TOOLS_VERSION}/tla2tools.jar"
)
REPO_TLA2TOOLS_JAR = Path(__file__).resolve().parent.parent / "tla2tools.jar"
CACHE_TLA2TOOLS_JAR = (
    Path.home()
    / ".cache"
    / "wunderspec"
    / "tla2tools"
    / TLA2TOOLS_VERSION
    / "tla2tools.jar"
)
APALACHE_VERSION = "0.57.0"
APALACHE_URL = (
    "https://github.com/apalache-mc/apalache/releases/download/"
    f"v{APALACHE_VERSION}/apalache-{APALACHE_VERSION}.tgz"
)
REPO_APALACHE_JAR = Path(__file__).resolve().parent.parent / "apalache.jar"
CACHE_APALACHE_JAR = (
    Path.home()
    / ".cache"
    / "wunderspec"
    / "apalache"
    / f"v{APALACHE_VERSION}"
    / "apalache.jar"
)


def _resolve_reporter(reporter: Reporter | None) -> Reporter:
    return reporter if reporter is not None else NULL_REPORTER


@dataclass(frozen=True)
class PredicateKind:
    kind: Literal["invariant", "example"]
    name: str
    func: Callable[..., Any]
    native_eval: "NativeEvalContext | None" = None

    @property
    def outcome_kind(self) -> Literal["violation", "example_found"]:
        return "violation" if self.kind == "invariant" else "example_found"


@dataclass(frozen=True)
class NativeEvalContext:
    proto_state: MachineState
    actions: dict[str, Callable[..., Any]]


@dataclass(frozen=True)
class ConvertRequest:
    DEFAULT_TEXT_WIDTH: ClassVar[int] = 80
    DEFAULT_TEXT_INDENT: ClassVar[int] = 2

    source: str | Path
    output: str | Path
    defs: str | None = None
    instance: str | None = None
    text_width: int = DEFAULT_TEXT_WIDTH
    text_indent: int = DEFAULT_TEXT_INDENT
    main: str | None = None
    quint: str = "quint"
    run_seed: int = 0
    run_samples: int = 1


@dataclass(frozen=True)
class ConvertResult:
    output_module: str
    state_class_name: str
    definition_names: list[str]
    artifacts: list[str]
    error_count: int = 0
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TlcRequest:
    spec: str | Path
    instance: str
    property: str | None = None
    init: str = "init"
    step: str = "step"
    out_dir: str | Path | None = None
    keep_files: bool = False
    jar: str | Path | None = None
    java: str = "java"
    workers: int = 1
    simulate: bool = False
    max_findings: int = 1
    out_itf: str | Path | None = None
    max_memory: str | None = None


@dataclass(frozen=True)
class TlcResult:
    returncode: int
    out_dir: str
    command: list[str]
    stdout: str
    stderr: str
    artifacts: list[str]
    outcome_kind: Literal[
        "checked", "violation", "example_found", "example_not_found"
    ] = "checked"


@dataclass(frozen=True)
class ApalacheRequest:
    spec: str | Path
    instance: str
    property: str | None = None
    init: str = "init"
    step: str = "step"
    out_dir: str | Path | None = None
    keep_files: bool = False
    jar: str | Path | None = None
    java: str = "java"
    max_steps: int = 10
    max_samples: int = 100
    simulate: bool = False
    max_findings: int = 1
    out_itf: str | Path | None = None
    max_memory: str | None = None


@dataclass(frozen=True)
class ApalacheResult:
    returncode: int
    out_dir: str
    command: list[str]
    stdout: str
    stderr: str
    artifacts: list[str]
    outcome_kind: Literal[
        "checked", "violation", "example_found", "example_not_found"
    ] = "checked"


@dataclass(frozen=True)
class RunRequest:
    spec: str | Path
    instance: str | None = None
    debug: bool = False
    init: str = "init"
    step: str = "step"
    property: str | None = None
    max_samples: int = 1000
    max_steps: int = 20
    bound: int = 2**31 - 1
    seed: int | None = None
    max_findings: int = 1
    out_itf: str | Path | None = None
    no_progress: bool = False
    coverage: str | None = None
    timeout: float | None = None
    best_trace: bool | None = None


@dataclass(frozen=True)
class RunResult:
    seed: int
    samples_explored: int
    violations: int
    examples_found: int
    outcome_kind: Literal["none", "violation", "example_found"]
    artifacts: list[str]
    best_trace_seed: int | None = None
    best_trace_length: int = 0


@dataclass(frozen=True)
class ReplayRequest:
    spec: str | Path
    instance: str | None = None
    debug: bool = False
    init: str = "init"
    step: str = "step"
    property: str | None = None
    max_steps: int = 20
    bound: int = 2**31 - 1
    seed: int | None = None
    from_schedule: str | Path | None = None
    out_itf: str | Path | None = None
    out_schedule: str | Path | None = None


@dataclass(frozen=True)
class ReplayResult:
    trace_seed: int | None
    trace_length: int
    violation_step: int | None
    example_step: int | None
    outcome_kind: Literal["none", "violation", "example_found"]
    artifacts: list[str]


@dataclass(frozen=True)
class ExplainRequest:
    trace: str | Path
    bdd: bool = False


@dataclass(frozen=True)
class ExplainResult:
    trace_length: int
    violation_step: int | None
    example_step: int | None
    outcome_kind: Literal["none", "violation", "example_found"]


@dataclass(frozen=True)
class CheckRequest:
    spec: str | Path
    instance: str | None = None
    init: str = "init"
    step: str = "step"
    property: str | None = None
    max_steps: int | None = None
    bound: int = 2**31 - 1
    no_progress: bool = False
    timeout: float | None = None
    no_shuffle: bool = False
    seed: int | None = None
    out_schedule: str | Path | None = None
    max_findings: int = 1
    out_itf: str | Path | None = None
    # Stream found traces as ITF NDJSON to this path (or "-" for stdout).


@dataclass(frozen=True)
class CheckResult:
    produced_states: int
    distinct_states: int
    violation_found: bool
    example_found: bool
    outcome_kind: Literal["none", "violation", "example_found"]
    trace: tuple[StateView, ...] | None
    artifacts: list[str]
    schedule_path: str | None = None
    traces: tuple[tuple[StateView, ...], ...] = ()
    # All counterexample traces found (up to max_findings). ``trace`` is the
    # first of these, kept for backward compatibility.
    schedule_paths: tuple[str, ...] = ()
    # Replay-schedule file path for each trace, aligned with ``traces``.
    # ``schedule_path`` is the first of these.
    itf_path: str | None = None
    # ITF NDJSON target the findings were streamed to ("-" for stdout), if any.
    predicate_kind: Literal["invariant", "example"] | None = None
    # Kind of the resolved property, set regardless of whether a finding was
    # produced, so the CLI can phrase the no-finding message correctly.


@dataclass(frozen=True)
class FuzzRequest:
    spec: str | Path
    instance: str | None = None
    init: str = "init"
    step: str = "step"
    property: str | None = None
    coverage: str = "cov"
    max_generations: int = 100
    max_steps: int = 20
    bound: int = 2**31 - 1
    seed: int | None = None
    no_progress: bool = False
    no_energy: bool = False
    corpus_dir: str | Path | None = None
    timeout: float | None = None


@dataclass(frozen=True)
class FuzzResult:
    generations: int
    total_execs: int
    total_steps: int
    total_retries: int
    corpus_size: int
    violations: int
    examples_found: int
    outcome_kind: Literal["none", "violation", "example_found"]
    corpus_dir: str
    artifacts: list[str]


@dataclass(frozen=True)
class LintRequest:
    spec: str | Path
    effects_out: str | Path | None = None


@dataclass(frozen=True)
class LintResult:
    error_count: int
    errors: list[Any]
    warning_count: int
    warnings: list[Any]
    artifacts: list[str]


def _fatal(msg: str, exit_code: int = 1) -> NoReturn:
    raise ApiError(msg, exit_code=exit_code)


def to_camel_case(name: str) -> str:
    """Convert snake_case to CamelCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def load_module(path: Path) -> Any:
    """Load a Python module from a file path."""
    if not path.exists():
        _fatal(f"File not found: {path}")

    if not path.suffix == ".py":
        _fatal(f"Expected a .py file, got: {path}")

    module_name = path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        _fatal(f"Failed to load module from: {path}")

    # Add the file's directory to sys.path so sibling imports work
    # (e.g. fifo.py importing channel.py from the same directory).
    # This mirrors the behavior of `python fifo.py`.
    parent_dir = str(path.parent.resolve())
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        _fatal(f"Error loading module {path}: {e}")

    return module


def find_state_classes(module: Any) -> list[type[MachineState]]:
    """Find all @state-decorated classes in a module."""
    state_classes: list[type[MachineState]] = []

    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj is MachineStateBase:
            continue
        params = getattr(obj, "_params", None)
        vars_ = getattr(obj, "_vars", None)
        if isinstance(params, tuple) and isinstance(vars_, tuple):
            state_classes.append(obj)

    return state_classes


def is_action_function(func: Callable[..., Any]) -> bool:
    """Check if a function is decorated with @action."""
    return hasattr(func, "__wrapped__") and callable(func)


def build_action_ast(
    state_cls: type[MachineState], action_func: Callable[..., Any]
) -> tuple[ActionNode, ActionDefs]:
    """Build an AST node for an action function using SymbolicContext."""
    sym_state = state_cls()
    ctx = SymbolicContext(copy(sym_state))
    action_func(ctx)
    return ctx.build(), ctx.extracted_actions


def build_expr_ast(
    state_cls: type[MachineState], expr_func: Callable[..., Expr]
) -> Node:
    """Build an AST node for an expression function."""
    sym_state = state_cls()
    result = expr_func(sym_state)

    if not isinstance(result, Expr):
        _fatal(
            f"Function '{expr_func.__name__}' did not return an Expr, "
            f"got {type(result).__name__}"
        )

    return result.node


def _var_key(var: VarNode) -> tuple[str, Any, Any]:
    return (var.name, var.unique_name, var.sort)


def _has_free_vars(
    node: Node, bound: frozenset[tuple[str, Any, Any]] | None = None
) -> bool:
    bound = bound or frozenset()
    match node:
        case VarNode():
            return _var_key(node) not in bound

        case MapLambdaNode():
            next_bound = bound | {_var_key(node.var)}
            return _has_free_vars(node.base_set, bound) or _has_free_vars(
                node.mapper, next_bound
            )

        case SetFilterNode() | SetMapNode() | SetQuantNode():
            active_bound = bound
            for var, domain in node.bindings:
                if _has_free_vars(domain, active_bound):
                    return True
                active_bound = active_bound | {_var_key(var)}
            return _has_free_vars(node.body, active_bound)

        case SetReduceNode():
            reducer_bound = bound | {
                _var_key(node.acc_var),
                _var_key(node.elem_var),
            }
            return (
                _has_free_vars(node.base_set, bound)
                or _has_free_vars(node.initial, bound)
                or _has_free_vars(node.fun, reducer_bound)
            )

        case ListReduceNode():
            reducer_bound = bound | {
                _var_key(node.acc_var),
                _var_key(node.elem_var),
            }
            return (
                _has_free_vars(node.base_list, bound)
                or _has_free_vars(node.initial, bound)
                or _has_free_vars(node.fun, reducer_bound)
            )

        case ListFilterNode():
            next_bound = bound | {_var_key(node.var)}
            return _has_free_vars(node.base_list, bound) or _has_free_vars(
                node.predicate, next_bound
            )

        case ChooseNode():
            next_bound = bound | {_var_key(node.var)}
            return _has_free_vars(node.base_set, bound) or _has_free_vars(
                node.predicate, next_bound
            )

        case NondetChoiceNode(var=VarNode() as var):
            next_bound = bound | {_var_key(var)}
            return _has_free_vars(node.base_set, bound) or _has_free_vars(
                node.body, next_bound
            )

        case LetNode() | ActionLetNode():
            return _has_free_vars(node.value, bound) or _has_free_vars(
                node.body, bound | {(node.name, None, node.value.sort)}
            )

        case _:
            for child in node.__dict__.values():
                if isinstance(child, Node) and _has_free_vars(child, bound):
                    return True
                if isinstance(child, dict):
                    for k, v in child.items():
                        if isinstance(k, Node) and _has_free_vars(k, bound):
                            return True
                        if isinstance(v, Node) and _has_free_vars(v, bound):
                            return True
                elif isinstance(child, (tuple, list, set, frozenset)):
                    for item in child:
                        if isinstance(item, Node) and _has_free_vars(item, bound):
                            return True
            return False


def _resolve_instance_params(
    module: Any,
    state_cls: type[MachineState],
    instance: str,
) -> dict[str, Node]:
    if not hasattr(module, instance):
        _fatal(f"Instance object not found: {instance}")

    obj = getattr(module, instance)
    if callable(obj) and getattr(obj, "_is_instance", False):
        obj = obj()
    if not isinstance(obj, state_cls):
        _fatal(
            f"Instance '{instance}' must be either a prototype {state_cls.__name__} "
            f"object or a function decorated with '@instance', "
            f"got {type(obj).__name__}"
        )

    params: dict[str, Node] = {}
    for param_name in state_cls._params:
        expr_value = getattr(obj, param_name)
        if not isinstance(expr_value, Expr):
            _fatal(
                f"Instance '{instance}' has invalid parameter '{param_name}': "
                f"expected Expr, got {type(expr_value).__name__}"
            )
        if _has_free_vars(expr_value.node):
            _fatal(
                f"Instance '{instance}' leaves parameter '{param_name}' symbolic. "
                "All parameters must be fixed for --instance conversion."
            )
        params[param_name] = expr_value.node
    return params


def get_definition(module: Any, name: str) -> Callable[..., Any]:
    """Get a function or callable from a module by name."""
    if not hasattr(module, name):
        _fatal(f"Definition '{name}' not found in module")

    obj: Callable[..., Any] = getattr(module, name)
    if not callable(obj):
        _fatal(f"'{name}' is not callable")

    return obj


# Shared replay-schedule format. v2 carries `engine` and `representation` tags;
# v1 is the legacy untagged format (still accepted on read, treated as native).
_SCHEDULE_FORMAT_V2 = "wunderspec-check-schedule-v2"
_SCHEDULE_FORMAT_V1 = "wunderspec-check-schedule-v1"
# Identifier of this (Python) engine.
_SCHEDULE_ENGINE = "wunderspec"
# Engine-specific decisions vs. the portable single-`sample_value`-per-`one_of`
# form. Python always records and consumes the portable values representation.
_REPRESENTATION_NATIVE = "native"
_REPRESENTATION_VALUES = "values"


@dataclass(frozen=True)
class _LoadedSchedule:
    decisions: list[SchedulerDecision]
    step_count: int | None = None


def _load_schedule_decision(entry: Any, index_label: str) -> SchedulerDecision:
    if not isinstance(entry, dict):
        _fatal(
            f"Schedule entry {index_label} must be a JSON object, "
            f"got {type(entry).__name__}"
        )
    if len(entry) != 1:
        _fatal(
            f"Schedule entry {index_label} must have exactly one key "
            f"(choice or sample_value), got: {list(entry.keys())}"
        )
    if "choice" in entry:
        idx = entry["choice"]
        if not isinstance(idx, int):
            _fatal(
                f"Schedule entry {index_label} field 'choice' must be an integer, "
                f"got: {idx!r}"
            )
        return SchedulerChoiceIndex(idx)
    if "sample_value" in entry:
        try:
            sample = value_from_json(entry["sample_value"])
        except Exception as e:
            _fatal(f"Failed to parse sample_value in schedule entry {index_label}: {e}")
        return SchedulerRawValue(sample)
    _fatal(
        f"Schedule entry {index_label} must contain 'choice' or 'sample_value', "
        f"got: {list(entry.keys())}"
    )


def _load_schedule(path: Path) -> _LoadedSchedule:
    """Load step-indexed schedule decisions."""
    if not path.exists():
        _fatal(f"Schedule file not found: {path}")

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        _fatal(f"Invalid schedule JSON in {path}: {e}")

    if not isinstance(raw, dict):
        _fatal(f"Schedule must be a JSON object, got {type(raw).__name__}")

    fmt = raw.get("format")
    if fmt not in (_SCHEDULE_FORMAT_V2, _SCHEDULE_FORMAT_V1):
        _fatal(f"Unsupported schedule format: {fmt!r}")

    # v2 carries engine/representation tags; v1 is legacy (treated as native,
    # engine unknown). A native schedule produced by another engine cannot be
    # replayed here, because the engines decompose `one_of(All*)` differently.
    representation = raw.get("representation", "")
    engine = raw.get("engine", "")
    if (
        representation == _REPRESENTATION_NATIVE
        and engine
        and engine != _SCHEDULE_ENGINE
    ):
        _fatal(
            f"This schedule was produced by engine {engine!r} in the native "
            f"representation and cannot be replayed by {_SCHEDULE_ENGINE!r}. "
            f"Regenerate it in the portable values representation, e.g. "
            f"`wunderspec-rust check ... --compat`."
        )

    steps = raw.get("steps")
    if not isinstance(steps, list):
        _fatal("Step-indexed schedule field 'steps' must be a JSON array")

    decisions = []
    for step_idx, step in enumerate(steps):
        if not isinstance(step, list):
            _fatal(
                f"Schedule step #{step_idx} must be a JSON array, "
                f"got {type(step).__name__}"
            )
        for entry_idx, entry in enumerate(step):
            decisions.append(_load_schedule_decision(entry, f"#{step_idx}.{entry_idx}"))
    return _LoadedSchedule(decisions=decisions, step_count=len(steps))


def _schedule_decision_to_json(decision: SchedulerDecision) -> dict[str, Any]:
    if isinstance(decision, SchedulerValue):
        return {"sample_value": value_to_json(value_to_itf(value(decision.value)))}
    if isinstance(decision, SchedulerChoiceIndex):
        return {"choice": decision.index}
    if isinstance(decision, SchedulerSplit):
        return {"choice": 1 if decision.split_arm else 0}
    if isinstance(decision, SchedulerAlternative):
        chosen = decision.chosen
        if not chosen.startswith("act"):
            raise ValueError(f"Cannot serialize named alternative decision: {chosen}")
        return {"choice": int(chosen.removeprefix("act"))}
    raise TypeError(f"Cannot serialize scheduler decision: {type(decision).__name__}")


def _write_check_schedule(
    path: str | Path, schedule: tuple[tuple[SchedulerDecision, ...], ...]
) -> None:
    data = {
        "format": _SCHEDULE_FORMAT_V2,
        "engine": _SCHEDULE_ENGINE,
        "representation": _REPRESENTATION_VALUES,
        "steps": [
            [_schedule_decision_to_json(decision) for decision in step]
            for step in schedule
        ],
    }
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2))


class _ItfNdjsonSink:
    """Streams ITF traces as NDJSON (one JSON document per line).

    ``target`` is ``None`` to disable, ``"-"`` to write to ``sys.stdout``, or a
    path to write (truncating) to a file. Each ``emit`` is flushed so a ``-`` pipe
    consumer sees findings as soon as they are discovered.
    """

    def __init__(self, target: str | Path | None) -> None:
        self._stream: TextIO | None
        self._owns_stream: bool
        if target is None:
            self._stream = None
            self._owns_stream = False
        elif target == "-":
            self._stream = sys.stdout
            self._owns_stream = False
        else:
            out_path = Path(target)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = out_path.open("w", encoding="utf-8")
            self._owns_stream = True

    @property
    def enabled(self) -> bool:
        return self._stream is not None

    def emit(self, line: str) -> None:
        if self._stream is None:
            return
        self._stream.write(line + "\n")
        self._stream.flush()

    def close(self) -> None:
        if self._stream is not None and self._owns_stream:
            self._stream.close()
        self._stream = None


def _default_check_schedule_path() -> Path:
    fd, path = tempfile.mkstemp(prefix="wunderspec-check-", suffix=".schedule.json")
    os.close(fd)
    return Path(path)


def _check_schedule_path(out_schedule: str | Path | None, index: int) -> Path:
    """Path to write the replay schedule for the ``index``-th finding.

    With no ``--out-schedule``, each finding gets its own temporary file. With an
    explicit ``--out-schedule X``, the first finding goes to ``X`` and later ones
    to indexed siblings (e.g. ``X`` -> ``X.1.json``) so they do not overwrite.
    """
    if out_schedule is None:
        return _default_check_schedule_path()
    base = Path(out_schedule)
    if index == 0:
        return base
    return base.with_name(f"{base.stem}.{index}{base.suffix}")


def _indexed_path(base: Path, index: int) -> Path:
    """Path for the ``index``-th artifact: ``base`` for 0, indexed siblings after.

    Same scheme as :func:`_check_schedule_path` so multiple findings written to
    sibling files do not overwrite each other (e.g. ``MC_x.itf.json`` ->
    ``MC_x.itf.1.json``).
    """
    if index == 0:
        return base
    return base.with_name(f"{base.stem}.{index}{base.suffix}")


def _human_count(n: int) -> str:
    """Format large counts with SI suffixes: 1k, 10M, ..."""
    v = float(max(n, 0))
    units = ["", "k", "M", "G", "T", "P", "E"]
    unit_idx = 0
    while v >= 1000 and unit_idx < len(units) - 1:
        v /= 1000.0
        unit_idx += 1

    if unit_idx == 0:
        return str(int(v))
    if v >= 10:
        return f"{v:.0f}{units[unit_idx]}"
    return f"{v:.1f}{units[unit_idx]}"


def _human_duration(seconds: float) -> str:
    """Format durations in a compact human-readable form."""
    total = max(int(seconds), 0)
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{sec}s"
    hours, minute = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minute}m"
    days, hour = divmod(hours, 24)
    return f"{days}d{hour}h"


def _tqdm_progress_bar(**kwargs: Any) -> tqdm:
    """Create a tqdm bar without its optional monitor thread.

    Pyodide cannot start Python threads, so tqdm's monitor emits a warning there
    even though the progress bar itself still works.
    """
    tqdm.monitor_interval = 0
    return tqdm(**kwargs)


class _CheckProgress:
    """Throttled progress indicator for `wunderspec check` using tqdm."""

    _BAR_WIDTH = 30
    _SLUG_WIDTH = 4

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.last_render = 0.0
        self.started_at = time.monotonic()
        self._tick = 0
        self.bar: tqdm | None = None
        if self.enabled:
            self.bar = _tqdm_progress_bar(
                file=sys.stderr,
                bar_format="{desc}",
                mininterval=1.0,
                miniters=1,
                dynamic_ncols=True,
                leave=True,
            )

    def _bounce_bar(self) -> str:
        w = self._BAR_WIDTH
        slug = self._SLUG_WIDTH
        span = w - slug
        pos = self._tick % (2 * span)
        if pos >= span:
            pos = 2 * span - pos
        return "[" + " " * pos + "█" * slug + "▋" + " " * (span - pos) + "]"

    def _render(self) -> None:
        if self.bar is None:
            return
        self.bar.set_description_str(self._desc, refresh=True)

    def update(self, produced: int, distinct: int) -> None:
        if self.bar is None:
            return
        now = time.monotonic()
        if self.last_render and (now - self.last_render) < 1.0:
            return
        self.last_render = now
        self._tick += 1
        elapsed = max(now - self.started_at, 1e-9)
        bar = self._bounce_bar()
        self._desc = (
            f"{bar}"
            f" produced: {_human_count(produced)}"
            f" | distinct: {_human_count(distinct)}"
            f" | elapsed: {_human_duration(elapsed)}"
        )
        self._render()

    def finish(self) -> None:
        if self.bar is None:
            return
        self.bar.leave = False
        self.bar.close()


class _RunProgress:
    """Throttled progress indicator for `wunderspec run`."""

    def __init__(self, total: int, enabled: bool, track_cov: bool = False) -> None:
        self.total = max(total, 0)
        self.enabled = enabled and self.total > 0
        self.last_render = 0.0
        self.explored = 0
        self.cov_count = 0
        self._track_cov = track_cov
        self.started_at = time.monotonic()
        self.bar: tqdm | None = None
        if self.enabled:
            self.bar = _tqdm_progress_bar(
                total=self.total,
                file=sys.stderr,
                mininterval=1.0,
                miniters=1,
                dynamic_ncols=True,
                leave=True,
                bar_format="[{bar:30}] {desc}",
            )

    def _render(self, now: float) -> None:
        if self.bar is None:
            return

        done_ratio = self.explored / self.total
        done_percent = done_ratio * 100.0
        elapsed = max(now - self.started_at, 1e-9)
        samples_per_sec = int(self.explored / elapsed)
        remaining = max(self.total - self.explored, 0)
        eta_seconds = 0.0 if samples_per_sec <= 0 else remaining / samples_per_sec
        cov_suffix = (
            f" | cov: {_human_count(self.cov_count)}" if self._track_cov else ""
        )
        self.bar.set_description_str(
            f"{_human_count(self.explored)}/{_human_count(self.total)}"
            f" {done_percent:5.1f}%"
            f" | {_human_count(samples_per_sec)}/s"
            f" | {_human_duration(elapsed)} + {_human_duration(eta_seconds)}"
            f"{cov_suffix}",
            refresh=False,
        )

    def update(self, explored: int, *, force: bool = False) -> None:
        if self.bar is None:
            return

        new_explored = min(max(explored, 0), self.total)
        delta = new_explored - self.explored
        self.explored = new_explored
        if delta > 0:
            self.bar.update(delta)

        now = time.monotonic()
        if not force and self.last_render and (now - self.last_render) < 1.0:
            return

        self._render(now)
        self.last_render = now

    def finish(self) -> None:
        if self.bar is None:
            return
        self.update(self.explored, force=True)
        self.bar.close()

    def clear(self) -> None:
        if self.bar is None:
            return
        self.bar.leave = False
        self.bar.close()


_RUN_INVARIANT_CACHE_SIZE = 10_000


def _state_fingerprint_key(state: StateView) -> tuple[tuple[str, int], ...]:
    """Build a stable key for memoizing invariant results."""
    all_fields: dict[str, Any] = {}
    all_fields.update(state._params)  # type: ignore[attr-defined]
    all_fields.update(state._mapping)  # type: ignore[attr-defined]
    return tuple(sorted((name, val.fingerprint()) for name, val in all_fields.items()))


def _trace_style_for_reporter(reporter: Reporter) -> TraceStyle:
    return TraceStyle(
        color=bool(getattr(reporter, "use_color", False)),
        width=int(getattr(reporter, "trace_width", 80)),
    )


def _print_trace(trace: tuple[StateView, ...], reporter: Reporter) -> None:
    print_trace(trace, reporter, style=_trace_style_for_reporter(reporter))


def _format_replay_step_header(step_idx: int, reporter: Reporter) -> str:
    label = f"[Step {step_idx}]"
    style = _trace_style_for_reporter(reporter)
    if not style.color:
        return label
    return f"{BOLD}{BLUE}{label}{RESET}"


def _native_eval_context(
    proto_state: MachineState,
    module: Any,
) -> NativeEvalContext:
    actions: dict[str, Callable[..., Any]] = {
        getattr(func, "_action_name"): func
        for _name, func in inspect.getmembers(module, inspect.isfunction)
        if hasattr(func, "_action_name")
    }
    return NativeEvalContext(proto_state=proto_state, actions=actions)


def _resolve_predicate(
    module: Any,
    property_name: str | None,
    native_eval: NativeEvalContext | None = None,
) -> PredicateKind | None:
    if property_name is None:
        return None
    func = get_definition(module, property_name)
    is_inv = getattr(func, "_is_invariant", False)
    is_ex = getattr(func, "_is_example", False)
    if is_inv and is_ex:
        _fatal(
            f"--property '{property_name}' is marked as both @invariant and @example"
        )
    if is_inv:
        kind: Literal["invariant", "example"] = "invariant"
    elif is_ex:
        kind = "example"
    elif getattr(func, "_is_temporal", False):
        _fatal(
            f"--property '{property_name}' is a @temporal property; "
            f"temporal properties are checked with `wunderspec with-tlc` "
            f"or `wunderspec with-apalache`"
        )
    else:
        _fatal(
            f"--property '{property_name}' is not annotated; "
            f"mark it with @invariant or @example"
        )
    return PredicateKind(
        kind=kind, name=property_name, func=func, native_eval=native_eval
    )


def _predicate_matches(predicate: PredicateKind, state: StateView) -> bool:
    native_eval = predicate.native_eval
    with native_action_context(
        None if native_eval is None else native_eval.proto_state,
        None if native_eval is None else native_eval.actions,
    ):
        result = value(predicate.func(state))
    if not isinstance(result, BoolValue):
        return False
    if predicate.kind == "invariant":
        return not result.value
    return result.value


def _predicate_message(predicate: PredicateKind, state_idx: int) -> str:
    if predicate.kind == "invariant":
        return f"Invariant violation at state {state_idx}"
    return f"Example found at state {state_idx}"


def _predicate_summary(predicate: PredicateKind, matches: int, samples: int) -> str:
    if predicate.kind == "invariant":
        if matches == 0:
            return f"No invariant violations in {samples} samples"
        return f"Found {matches} invariant violation(s) in {samples} samples"
    if matches == 0:
        return f"No examples found in {samples} samples"
    return f"Found {matches} example trace(s) in {samples} samples"


def _replay_command_for_run(request: RunRequest, trace_seed: int) -> str:
    """Build a shell-safe replay command for a trace emitted by `run`."""
    args = ["wunderspec", "replay"]
    if request.instance is not None:
        args.extend(["--instance", request.instance])
    if request.debug:
        args.append("--debug")
    if request.init != "init":
        args.extend(["--init", request.init])
    if request.step != "step":
        args.extend(["--step", request.step])
    if request.property is not None:
        args.extend(["--property", request.property])
    args.extend(["--max-steps", str(request.max_steps)])
    if request.bound != 2**31 - 1:
        args.extend(["--bound", str(request.bound)])
    args.append(str(request.spec))
    args.extend(["--seed", str(trace_seed)])
    return " ".join(shlex.quote(arg) for arg in args)


def _replay_command_for_check(request: CheckRequest, schedule_path: str | Path) -> str:
    """Build a shell-safe replay command for a schedule emitted by `check`."""
    args = ["wunderspec", "replay"]
    if request.instance is not None:
        args.extend(["--instance", request.instance])
    if request.init != "init":
        args.extend(["--init", request.init])
    if request.step != "step":
        args.extend(["--step", request.step])
    if request.property is not None:
        args.extend(["--property", request.property])
    if request.bound != 2**31 - 1:
        args.extend(["--bound", str(request.bound)])
    args.append(str(request.spec))
    args.extend(["--from-schedule", str(schedule_path)])
    return " ".join(shlex.quote(arg) for arg in args)


def _search_command_for_run(request: RunRequest, seed: int) -> str:
    """Build a shell-safe run command that reruns the same search."""
    args = ["wunderspec", "run", f"--seed={seed}"]
    if request.instance is not None:
        args.extend(["--instance", request.instance])
    if request.debug:
        args.append("--debug")
    if request.init != "init":
        args.extend(["--init", request.init])
    if request.step != "step":
        args.extend(["--step", request.step])
    if request.property is not None:
        args.extend(["--property", request.property])
    if request.max_samples != 1000:
        args.extend(["--max-samples", str(request.max_samples)])
    if request.max_steps != 20:
        args.extend(["--max-steps", str(request.max_steps)])
    if request.bound != 2**31 - 1:
        args.extend(["--bound", str(request.bound)])
    if request.max_findings != 1:
        args.extend(["--max-findings", str(request.max_findings)])
    if request.out_itf is not None:
        args.extend(["--out-itf", str(request.out_itf)])
    if request.no_progress:
        args.append("--no-progress")
    if request.coverage is not None:
        args.extend(["--coverage", request.coverage])
    if request.timeout is not None:
        args.extend(["--timeout", str(request.timeout)])
    if request.best_trace is not None:
        args.extend(["--best-trace", "1" if request.best_trace else "0"])
    args.append(str(request.spec))
    return " ".join(shlex.quote(arg) for arg in args)


def _load_spec(
    *,
    spec: str | Path,
    instance: str | None,
    init: str,
    step: str,
    property_name: str | None,
) -> tuple[MachineState, Callable[..., Any], Callable[..., Any], PredicateKind | None]:
    """Load spec module and resolve state class + callable definitions."""
    source_path = Path(spec)
    module = load_module(source_path)

    state_classes = find_state_classes(module)
    if len(state_classes) == 0:
        _fatal("No @state-decorated class found")
    elif len(state_classes) > 1:
        _fatal("Multiple @state classes found, expected 1")
    state_cls = state_classes[0]

    if instance is not None:
        if not hasattr(module, instance):
            _fatal(f"Instance factory not found: {instance}")
        inst_func = getattr(module, instance)
        if not getattr(inst_func, "_is_instance", False):
            _fatal(f"'{instance}' is not decorated with @instance")
        proto = inst_func()
        if not isinstance(proto, state_cls):
            _fatal(
                f"@instance '{instance}' returned {type(proto).__name__}, "
                f"expected {state_cls.__name__}"
            )
    else:
        if state_cls._params:
            factories = [name for name, _func in find_instance_factories(module)]
            hint = (
                f" This spec defines @instance factories: {', '.join(factories)}."
                if factories
                else " Define an @instance factory that returns a configured state."
            )
            _fatal(
                f"Spec {state_cls.__name__} is parameterized "
                f"({', '.join(state_cls._params)}); use --instance NAME."
                f"{hint}"
            )
        proto = state_cls()

    init_func = get_definition(module, init)
    step_func = get_definition(module, step)
    predicate = _resolve_predicate(
        module,
        property_name,
        _native_eval_context(proto, module),
    )

    return proto, init_func, step_func, predicate


def convert(request: ConvertRequest, reporter: Reporter | None = None) -> ConvertResult:
    """Convert between supported specification formats."""
    rpt = _resolve_reporter(reporter)
    source_path = Path(request.source)
    output_path = Path(request.output)

    if not source_path.exists():
        _fatal(f"File not found: {source_path}")

    if source_path.suffix == ".qnt":
        if output_path.suffix != ".py":
            _fatal(
                f"Expected a .py output file for Quint conversion, got: {output_path}"
            )
        if request.text_width <= 0:
            _fatal(f"--text-width must be positive, got: {request.text_width}")
        if request.text_indent <= 0:
            _fatal(f"--text-indent must be positive, got: {request.text_indent}")
        if request.defs is not None:
            rpt.warn("--defs is ignored when converting from Quint")
        if request.instance is not None:
            rpt.warn("--instance is ignored when converting from Quint")
        try:
            from wunderspec.quint_convert import (
                QuintConvertError,
                QuintConvertOptions,
                convert_quint,
            )

            result = convert_quint(
                QuintConvertOptions(
                    source=source_path,
                    output=output_path,
                    main=request.main,
                    quint=request.quint,
                    run_seed=request.run_seed,
                    run_samples=request.run_samples,
                    text_width=request.text_width,
                    text_indent=request.text_indent,
                ),
                rpt,
            )
        except QuintConvertError as e:
            _fatal(str(e))
        return ConvertResult(
            output_module=result.output_module,
            state_class_name=result.state_class_name,
            definition_names=result.definition_names,
            artifacts=result.artifacts,
            error_count=result.error_count,
            diagnostics=result.diagnostics,
        )

    if not source_path.suffix == ".py":
        _fatal(f"Expected a .py file, got: {source_path}")
    if request.text_width <= 0:
        _fatal(f"--text-width must be positive, got: {request.text_width}")
    if request.text_indent <= 0:
        _fatal(f"--text-indent must be positive, got: {request.text_indent}")

    rpt.info(f"Loading module: {source_path}")
    module = load_module(source_path)

    state_classes = find_state_classes(module)

    if len(state_classes) == 0:
        _fatal("No @state-decorated class found in the module")
    elif len(state_classes) > 1:
        class_names = ", ".join(c.__name__ for c in state_classes)
        _fatal(
            f"Multiple @state-decorated classes found: {class_names}\n"
            "       Only one state class per module is supported"
        )

    state_cls = state_classes[0]
    rpt.info(f"Found state class: {state_cls.__name__}")

    def _var_key(var: VarNode) -> tuple[str, Any, Any]:
        return (var.name, var.unique_name, var.sort)

    def _has_free_vars(
        node: Node, bound: frozenset[tuple[str, Any, Any]] | None = None
    ) -> bool:
        bound = bound or frozenset()
        match node:
            case VarNode():
                return _var_key(node) not in bound

            case MapLambdaNode():
                next_bound = bound | {_var_key(node.var)}
                return _has_free_vars(node.base_set, bound) or _has_free_vars(
                    node.mapper, next_bound
                )

            case SetFilterNode() | SetMapNode() | SetQuantNode():
                active_bound = bound
                for var, domain in node.bindings:
                    if _has_free_vars(domain, active_bound):
                        return True
                    active_bound = active_bound | {_var_key(var)}
                return _has_free_vars(node.body, active_bound)

            case SetReduceNode():
                reducer_bound = bound | {
                    _var_key(node.acc_var),
                    _var_key(node.elem_var),
                }
                return (
                    _has_free_vars(node.base_set, bound)
                    or _has_free_vars(node.initial, bound)
                    or _has_free_vars(node.fun, reducer_bound)
                )

            case ListReduceNode():
                reducer_bound = bound | {
                    _var_key(node.acc_var),
                    _var_key(node.elem_var),
                }
                return (
                    _has_free_vars(node.base_list, bound)
                    or _has_free_vars(node.initial, bound)
                    or _has_free_vars(node.fun, reducer_bound)
                )

            case ListFilterNode():
                next_bound = bound | {_var_key(node.var)}
                return _has_free_vars(node.base_list, bound) or _has_free_vars(
                    node.predicate, next_bound
                )

            case ChooseNode():
                next_bound = bound | {_var_key(node.var)}
                return _has_free_vars(node.base_set, bound) or _has_free_vars(
                    node.predicate, next_bound
                )

            case NondetChoiceNode(var=VarNode() as var):
                next_bound = bound | {_var_key(var)}
                return _has_free_vars(node.base_set, bound) or _has_free_vars(
                    node.body, next_bound
                )

            case LetNode() | ActionLetNode():
                return _has_free_vars(node.value, bound) or _has_free_vars(
                    node.body, bound | {(node.name, None, node.value.sort)}
                )

            case _:
                for child in node.__dict__.values():
                    if isinstance(child, Node) and _has_free_vars(child, bound):
                        return True
                    if isinstance(child, dict):
                        for k, v in child.items():
                            if isinstance(k, Node) and _has_free_vars(k, bound):
                                return True
                            if isinstance(v, Node) and _has_free_vars(v, bound):
                                return True
                    elif isinstance(child, (tuple, list, set, frozenset)):
                        for item in child:
                            if isinstance(item, Node) and _has_free_vars(item, bound):
                                return True
                return False

    def _instance_target_module(module_name: str, source_module: str) -> str:
        suffix = f"_{source_module}"
        if module_name.startswith("MC") and module_name.endswith(suffix):
            return source_module
        match = re.fullmatch(r"MC[A-Za-z0-9-]*_(.+)", module_name)
        if match is None:
            _fatal(
                "When using --instance, output module name must match "
                "'MC[A-Za-z0-9-]*_<BaseModule>'.\n"
                "For example: MC_MySpec.tla or MC20_MySpec.tla."
            )
        return match.group(1)

    def _resolve_instance_params() -> dict[str, Node]:
        assert request.instance is not None
        if not hasattr(module, request.instance):
            _fatal(f"Instance object not found: {request.instance}")

        obj = getattr(module, request.instance)
        if callable(obj) and getattr(obj, "_is_instance", False):
            obj = obj()
        if not isinstance(obj, state_cls):
            _fatal(
                f"Instance '{request.instance}' must be either a prototype {state_cls.__name__} "
                f"object or a function decorated with '@instance', "
                f"got {type(obj).__name__}"
            )

        params: dict[str, Node] = {}
        for param_name in state_cls._params:
            expr_value = getattr(obj, param_name)
            if not isinstance(expr_value, Expr):
                _fatal(
                    f"Instance '{request.instance}' has invalid parameter '{param_name}': "
                    f"expected Expr, got {type(expr_value).__name__}"
                )
            if _has_free_vars(expr_value.node):
                _fatal(
                    f"Instance '{request.instance}' leaves parameter '{param_name}' symbolic. "
                    "All parameters must be fixed for --instance conversion."
                )
            params[param_name] = expr_value.node
        return params

    if request.instance is not None:
        if request.defs is not None:
            rpt.warn("--defs is ignored when --instance is used")
        fixed_params = _resolve_instance_params()
        module_name = output_path.stem
        target_module = _instance_target_module(module_name, source_path.stem)
        rpt.info(f"Generating TLA+ instance module: {module_name}")
        try:
            tla_output = to_tla_instance(
                state_cls,
                module_name,
                target_module,
                fixed_params,
                text_width=request.text_width,
                text_indent=request.text_indent,
            )
        except Exception as e:
            _fatal(f"Error generating TLA+ instance module: {e}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(tla_output)
        rpt.success(f"Wrote TLA+ specification to: {output_path}")
        return ConvertResult(
            output_module=module_name,
            state_class_name=state_cls.__name__,
            definition_names=[],
            artifacts=[output_path.name],
        )

    def _discover_defs() -> list[tuple[str, Any, bool]]:
        results = []
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if func.__module__ != module.__name__:
                continue
            if hasattr(func, "_action_name"):
                sig = inspect.signature(func)
                if len(sig.parameters) == 1:
                    results.append((name, func, True))
            elif getattr(func, "_is_invariant", False):
                results.append((name, func, False))
            elif getattr(func, "_is_example", False):
                results.append((name, func, False))
            elif getattr(func, "_is_temporal", False):
                results.append((name, func, False))
            elif getattr(func, "_is_coverage", False):
                results.append((name, func, False))
            elif getattr(func, "_is_expr", False):
                if getattr(func, "_is_expr_pure", False):
                    continue
                sig = inspect.signature(func)
                if len(sig.parameters) == 1:
                    results.append((name, func, False))
        if not results:
            _fatal(
                "No @action, @invariant, @example, @temporal, or @expr definitions found; use --defs"
            )
        return results

    def _resolve_defs() -> list[tuple[str, Any, bool]]:
        if request.defs is None:
            return _discover_defs()
        results = []
        for def_name in (d.strip() for d in request.defs.split(",")):
            func = get_definition(module, def_name)
            sig = inspect.signature(func)
            params = list(sig.parameters.values())
            if len(params) == 0:
                _fatal(f"Definition '{def_name}' takes no arguments")
            first_param_annotation = params[0].annotation
            is_action = False
            if first_param_annotation != inspect.Parameter.empty:
                ann_str = str(first_param_annotation)
                if "Context" in ann_str:
                    is_action = True
                elif (
                    state_cls.__name__ in ann_str or first_param_annotation is state_cls
                ):
                    is_action = False
                else:
                    is_action = is_action_function(func)
            else:
                is_action = is_action_function(func)
            results.append((def_name, func, is_action))
        return results

    nodes: dict[str, Node] = {}
    all_extracted_actions: ActionDefs = {}
    init_op_names: set[str] = set()

    def_items = _resolve_defs()

    for def_name, func, is_action in def_items:
        node: Node
        if is_action:
            rpt.info(f"Building AST for action: {def_name}")
            try:
                node, extracted = build_action_ast(state_cls, func)
                all_extracted_actions.update(extracted)
            except Exception as e:
                _fatal(f"Error building AST for action '{def_name}': {e}")
        else:
            rpt.info(f"Building AST for expression: {def_name}")
            try:
                expr_func = (
                    (lambda s, _func=func: Not(_func(s)))
                    if getattr(func, "_is_example", False)
                    else func
                )
                node = build_expr_ast(state_cls, expr_func)
            except Exception as e:
                _fatal(f"Error building AST for expression '{def_name}': {e}")

        tla_name = to_camel_case(def_name)
        nodes[tla_name] = node
        if getattr(func, "_is_init", False):
            init_op_names.add(tla_name)

    module_name = output_path.stem
    rpt.info(f"Generating TLA+ module: {module_name}")
    try:
        tla_output = to_tla(
            state_cls,
            module_name,
            extracted_actions=all_extracted_actions,
            init_ops=init_op_names or None,
            text_width=request.text_width,
            text_indent=request.text_indent,
            **nodes,
        )
    except Exception as e:
        _fatal(f"Error generating TLA+: {e}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(tla_output)

    rpt.success(f"Wrote TLA+ specification to: {output_path}")
    rpt.out()
    rpt.out("Summary:")
    rpt.out(f"  State class:  {state_cls.__name__}")
    rpt.out(f"  Parameters:   {', '.join(state_cls._params) or '(none)'}")
    rpt.out(f"  Variables:    {', '.join(state_cls._vars)}")
    rpt.out(f"  Definitions:  {', '.join(nodes.keys())}")
    rpt.out(f"  Output:       {output_path}")

    return ConvertResult(
        output_module=module_name,
        state_class_name=state_cls.__name__,
        definition_names=list(nodes.keys()),
        artifacts=[output_path.name],
    )


def tlc(request: TlcRequest, reporter: Reporter | None = None) -> TlcResult:
    """Generate TLA+ for a Wunderspec instance and run TLC."""
    if not is_feature_enabled("tlc"):
        _fatal(feature_message("tlc"))

    from wunderspec.tlc import run_tlc

    return run_tlc(request, _resolve_reporter(reporter))


def apalache(
    request: ApalacheRequest, reporter: Reporter | None = None
) -> ApalacheResult:
    """Generate TLA+ for a Wunderspec instance and run Apalache."""
    if not is_feature_enabled("apalache"):
        _fatal(feature_message("apalache"))

    from wunderspec.apalache import run_apalache

    return run_apalache(request, _resolve_reporter(reporter))


def _action_kind(node: ActionNode) -> str:
    kind_map = {
        "ActionAndNode": "and",
        "ActionChoiceNode": "alt",
        "ActionCallNode": "call",
        "ActionLetNode": "cache",
        "AssignNode": "assign",
        "AssumeNode": "assume",
        "NondetChoiceNode": "one_of",
    }
    return kind_map.get(node.__class__.__name__, node.__class__.__name__)


def _display_source_path(filename: str) -> str | None:
    path = Path(filename)
    abs_path = path.resolve() if path.is_absolute() else path.absolute().resolve()
    cwd = Path.cwd().resolve()
    try:
        abs_path.relative_to(cwd / ".venv" / "bin")
        return None
    except ValueError:
        pass
    argv0 = Path(sys.argv[0])
    if str(argv0) and argv0.name == abs_path.name:
        try:
            if argv0.resolve() == abs_path:
                return None
        except OSError:
            pass
    try:
        rendered = abs_path.relative_to(cwd)
    except ValueError:
        rendered = Path(Path(os.path.relpath(abs_path, cwd)))
    return rendered.as_posix()


def _format_source_span(span: Any, *, include_col: bool = True) -> str | None:
    if span is None or span.filename is None:
        return None
    filename = _display_source_path(span.filename)
    if filename is None:
        return None
    if include_col:
        return f"{filename}:{span.lineno}:{span.col_offset}"
    return f"{filename}:{span.lineno}"


def _format_action_node(node: ActionNode) -> str:
    span = getattr(node, "source_span", None)
    kind = _action_kind(node)
    location = _format_source_span(span)
    if location is not None:
        return f"{location} {kind}"
    return ""


def _format_evaluation_error(
    ev: EvaluationError,
    *,
    reproduce_cmd: str | None = None,
    debug_hint: bool = True,
) -> str:
    """Render an :class:`EvaluationError` as a user-facing message that points
    back into the specification source, instead of a raw Python traceback."""
    location = _format_source_span(ev.span)
    original = ev.original
    lines = ["Evaluation error while running the spec:"]
    lines.append(f"  {location}" if location else "  (source location unavailable)")
    lines.append(f"  {type(original).__name__}: {original}")

    if ev.action_chain:
        action_lines = [
            formatted
            for formatted in (_format_action_node(node) for node in ev.action_chain)
            if formatted
        ]
        if action_lines:
            lines.append("")
            lines.append("Action trace:")
            lines.extend(f"  > {line}" for line in action_lines)

    if ev.step_index is not None or ev.trace_seed is not None:
        lines.append("")
        where = []
        if ev.step_index is not None:
            where.append(f"step {ev.step_index}")
        if ev.trace_seed is not None:
            where.append(f"trace seed {ev.trace_seed}")
        lines.append("This happened at " + " of ".join(where) + ".")
    if reproduce_cmd is not None:
        lines.append(f"Reproduce with: {reproduce_cmd}")
    if debug_hint:
        lines.append("For a full Python traceback, re-run with --debug.")
    return "\n".join(lines)


def _locate_run_evaluation_error(
    ev: EvaluationError,
    proto: MachineState,
    init_func: Callable[..., None],
    step_func: Callable[..., None],
    settings: WalkSettings,
) -> EvaluationError:
    """Best-effort enrichment of an :class:`EvaluationError` with the chain of
    actions that led to it, by replaying the failing trace with action tracing.

    Falls back to the original (expression-located) error if the failure does
    not reproduce or the replay itself fails.
    """
    if ev.trace_seed is None:
        return ev
    try:
        located = locate_evaluation_error(
            proto,  # type: ignore[type-var]
            init_func,
            step_func,
            replace(settings, seed=ev.trace_seed),
        )
    except Exception:
        return ev
    return located if located is not None else ev


def run(request: RunRequest, reporter: Reporter | None = None) -> RunResult:
    """Run random walks for a specification."""
    rpt = _resolve_reporter(reporter)
    proto, init_func, step_func, predicate = _load_spec(
        spec=request.spec,
        instance=request.instance,
        init=request.init,
        step=request.step,
        property_name=request.property,
    )

    coverage_func = None
    coverage_native_eval: NativeEvalContext | None = None
    if request.coverage is not None:
        source_path = Path(request.spec)
        module = load_module(source_path)
        if not hasattr(module, request.coverage):
            _fatal(f"Coverage function not found: {request.coverage}")
        cov_candidate = getattr(module, request.coverage)
        if not callable(cov_candidate):
            _fatal(f"'{request.coverage}' is not callable")
        if not getattr(cov_candidate, "_is_coverage", False):
            _fatal(f"'{request.coverage}' is not decorated with @coverage")
        coverage_func = cov_candidate
        coverage_native_eval = _native_eval_context(proto, module)

    trace_sampler = random_traces_debug if request.debug else random_traces

    seed = request.seed if request.seed is not None else random.randrange(2**63)
    rpt.info(f"Seed: {seed}")
    rpt.out(f"Rerun the search with: {_search_command_for_run(request, seed)}")
    if request.timeout is not None and request.timeout <= 0:
        _fatal("--timeout must be > 0")

    settings = WalkSettings(
        seed=seed,
        max_steps=request.max_steps,
        max_retries_per_step=3,
        bound=request.bound,
    )
    run_started_at = time.monotonic()
    run_deadline = (
        run_started_at + request.timeout if request.timeout is not None else None
    )
    best_trace_enabled = (
        request.best_trace if request.best_trace is not None else (predicate is None)
    )
    if predicate is None:
        if best_trace_enabled:
            rpt.info(
                "No --property provided; use --property to search for a "
                "property. Looking for the longest trace."
            )
        else:
            rpt.info("No --property provided; use --property to search for a property.")

    examples_count = 0
    match_count = 0
    timed_out = False
    best_trace_seed: int | None = None
    best_trace: tuple[StateView, ...] = ()
    artifacts: list[str] = []
    sink = _ItfNdjsonSink(request.out_itf)
    itf_params = list(proto._params)
    itf_vars = list(proto._vars)
    cov_dict: dict[int, tuple[int, int, int]] = {}
    inv_cache: OrderedDict[tuple[tuple[str, int], ...], bool] = OrderedDict()
    progress = _RunProgress(
        request.max_samples,
        enabled=not request.no_progress,
        track_cov=coverage_func is not None,
    )
    progress.update(0, force=True)

    try:
        with locate_eval_errors():
            for trace_seed, t in trace_sampler(proto, init_func, step_func, settings):  # type: ignore[type-var]
                if run_deadline is not None and time.monotonic() >= run_deadline:
                    timed_out = True
                    break

                if best_trace_enabled and len(t) > len(best_trace):
                    best_trace_seed = trace_seed
                    best_trace = t

                if predicate is not None:
                    for i, s in enumerate(t):
                        state_key = _state_fingerprint_key(s)
                        cached_match = inv_cache.get(state_key)
                        if cached_match is None:
                            cached_match = _predicate_matches(predicate, s)
                            inv_cache[state_key] = cached_match
                            if len(inv_cache) > _RUN_INVARIANT_CACHE_SIZE:
                                inv_cache.popitem(last=False)
                        else:
                            inv_cache.move_to_end(state_key)

                        if cached_match:
                            progress.clear()
                            rpt.out(_predicate_message(predicate, i))
                            rpt.out(f"Trace seed: {trace_seed}")
                            _print_trace(t[: i + 1], rpt)
                            rpt.out(
                                "Replay with: "
                                f"{_replay_command_for_run(request, trace_seed)}"
                            )
                            if sink.enabled:
                                meta: dict[str, Any] = {
                                    "source": "wunderspec run",
                                    "trace_seed": trace_seed,
                                    "predicate_kind": predicate.kind,
                                    "predicate_name": predicate.name,
                                    (
                                        "violation_step"
                                        if predicate.kind == "invariant"
                                        else "example_step"
                                    ): i,
                                }
                                sink.emit(
                                    itf_trace_json_line(
                                        t[: i + 1],
                                        meta=meta,
                                        params=itf_params,
                                        vars=itf_vars,
                                    )
                                )
                            match_count += 1
                            break

                if coverage_func is not None:
                    for i, s in enumerate(t):
                        with native_action_context(
                            (
                                None
                                if coverage_native_eval is None
                                else coverage_native_eval.proto_state
                            ),
                            (
                                None
                                if coverage_native_eval is None
                                else coverage_native_eval.actions
                            ),
                        ):
                            fp = value(coverage_func(s)).fingerprint()
                        existing = cov_dict.get(fp)
                        if existing is None or i < existing[1]:
                            cov_dict[fp] = (
                                trace_seed,
                                i,
                                (existing[2] + 1 if existing else 1),
                            )
                        else:
                            cov_dict[fp] = (existing[0], existing[1], existing[2] + 1)
                    progress.cov_count = len(cov_dict)

                examples_count += 1
                progress.update(examples_count)
                if (
                    match_count >= request.max_findings
                    or examples_count >= request.max_samples
                ):
                    break
    except EvaluationError as ev:
        if request.debug:
            # In debug mode, preserve the native Python traceback.
            raise ev.original
        progress.clear()
        located = _locate_run_evaluation_error(
            ev, proto, init_func, step_func, settings
        )
        reproduce = (
            _replay_command_for_run(request, located.trace_seed)
            if located.trace_seed is not None
            else None
        )
        raise ApiError(
            _format_evaluation_error(located, reproduce_cmd=reproduce)
        ) from located
    finally:
        sink.close()

    progress.finish()

    if timed_out:
        elapsed = time.monotonic() - run_started_at
        rpt.info(
            f"Timeout reached after {_human_duration(elapsed)} "
            f"({examples_count} samples explored)"
        )

    if coverage_func is not None:
        cov_path = Path("coverage.json")
        ordered = OrderedDict(
            sorted(cov_dict.items(), key=lambda kv: (kv[1][2], kv[1][1]), reverse=True)
        )
        cov_path.write_text(json.dumps(ordered, indent=2))
        artifacts.append(cov_path.name)
        rpt.success(f"Coverage written to {cov_path} ({len(ordered)} entries)")

    if predicate is None:
        rpt.success(f"Explored {examples_count} samples without checking a predicate")
        outcome_kind: Literal["none", "violation", "example_found"] = "none"
    else:
        if match_count == 0:
            rpt.success(_predicate_summary(predicate, match_count, examples_count))
            outcome_kind = "none"
        elif predicate.kind == "invariant":
            rpt.error(_predicate_summary(predicate, match_count, examples_count))
            outcome_kind = "violation"
        else:
            rpt.info(_predicate_summary(predicate, match_count, examples_count))
            outcome_kind = "example_found"

    if best_trace_enabled and best_trace_seed is not None:
        rpt.out(f"Best trace seed: {best_trace_seed}")
        rpt.out(f"Best trace length: {len(best_trace)}")
        _print_trace(best_trace, rpt)
        rpt.out(f"Replay with: {_replay_command_for_run(request, best_trace_seed)}")

    return RunResult(
        seed=seed,
        samples_explored=examples_count,
        violations=(
            match_count
            if predicate is not None and predicate.kind == "invariant"
            else 0
        ),
        examples_found=(
            match_count if predicate is not None and predicate.kind == "example" else 0
        ),
        outcome_kind=outcome_kind,
        artifacts=artifacts,
        best_trace_seed=best_trace_seed,
        best_trace_length=len(best_trace),
    )


def replay(request: ReplayRequest, reporter: Reporter | None = None) -> ReplayResult:
    """Replay a single trace with action tracing."""
    rpt = _resolve_reporter(reporter)
    if request.seed is not None and request.from_schedule is not None:
        _fatal("--seed and --from-schedule are mutually exclusive")
    if request.seed is None and request.from_schedule is None:
        _fatal("replay requires exactly one of --seed or --from-schedule")

    proto, init_func, step_func, predicate = _load_spec(
        spec=request.spec,
        instance=request.instance,
        init=request.init,
        step=request.step,
        property_name=request.property,
    )

    artifacts: list[str] = []
    loaded_schedule = None
    if request.from_schedule is not None:
        loaded_schedule = _load_schedule(Path(request.from_schedule))
    replay_max_steps = (
        loaded_schedule.step_count
        if loaded_schedule is not None and loaded_schedule.step_count is not None
        else request.max_steps
    )

    settings = WalkSettings(
        seed=request.seed,
        max_steps=replay_max_steps,
        max_retries_per_step=3,
        bound=request.bound,
    )

    def _print_pretty_state(step_idx: int, state: StateView, indent: str = "") -> None:
        print_state(
            step_idx,
            state,
            rpt,
            indent=indent,
            style=_trace_style_for_reporter(rpt),
        )

    def _write_replay_itf(
        trace_seed: int,
        trace: tuple[StateView, ...],
        shown_steps: int,
        match_step: int | None,
    ) -> None:
        if not request.out_itf:
            return

        trace_meta: dict[str, Any] = {
            "source": "wunderspec replay",
            "trace_seed": trace_seed,
            "debug": request.debug,
            "bound": request.bound,
            "max_steps": replay_max_steps,
        }
        if predicate is not None:
            trace_meta["predicate_kind"] = predicate.kind
            trace_meta["predicate_name"] = predicate.name
        if match_step is not None and predicate is not None:
            trace_meta[
                "violation_step" if predicate.kind == "invariant" else "example_step"
            ] = match_step

        step_meta: list[dict[str, Any]] = []
        for step_idx in range(shown_steps):
            meta_for_step: dict[str, Any] = {}
            if step_idx < len(replay_steps):
                actions = [
                    formatted
                    for formatted in (
                        _format_action_node(node) for node in replay_steps[step_idx]
                    )
                    if formatted
                ]
                if actions:
                    meta_for_step["action_trace"] = actions
            step_meta.append(meta_for_step)

        out_path = Path(request.out_itf)
        write_itf_trace(
            out_path,
            trace[:shown_steps],
            meta=trace_meta,
            params=list(proto._params),
            vars=list(proto._vars),
            step_meta=step_meta,
        )
        artifacts.append(out_path.name)
        rpt.info(f"Wrote ITF trace to {out_path}")

    def _print_debug_replay_action_locations() -> None:
        rpt.out("Replay action source locations (debug mode, static list):")
        from wunderspec.source_tracking import enable_source_tracking
        from wunderspec.sym_context import SymbolicContext

        with enable_source_tracking():
            sym_context = SymbolicContext(copy(proto), inline_all=False)
            init_func(sym_context)
            init_node = sym_context.build()

            sym_context = SymbolicContext(copy(proto), inline_all=False)
            step_func(sym_context)
            step_node = sym_context.build()

        def iter_action_nodes(node: ActionNode, prefix: str = "") -> None:
            span = getattr(node, "source_span", None)
            location = _format_source_span(span, include_col=False)
            if location is not None:
                rpt.out(f"{prefix}{node.__class__.__name__} @ {location}")
            elif span is not None and span.filename is not None:
                pass
            else:
                rpt.out(f"{prefix}{node.__class__.__name__}")

            if isinstance(node, ActionCallNode):
                if hasattr(node, "body"):
                    iter_action_nodes(node.body, prefix + "  ")

        rpt.out("init:")
        iter_action_nodes(init_node, "  ")
        rpt.out("step:")
        iter_action_nodes(step_node, "  ")

    emit_human_trace = request.out_itf is None

    if request.debug and emit_human_trace:
        _print_debug_replay_action_locations()

    replay_steps: list[tuple[ActionNode, ...]] = []
    scripted_scheduler = None
    if loaded_schedule is not None:
        scripted_scheduler = ScriptedScheduler(loaded_schedule.decisions)

    # Optionally re-emit the replayed trace as a portable values schedule. We wrap
    # the scripted scheduler in a RecordingScheduler and slice its flat decision
    # log at each step boundary (signalled by on_step).
    recording_scheduler: RecordingScheduler | None = None
    emit_schedule_path: Path | None = None
    schedule_step_bounds: list[int] = []
    if request.out_schedule is not None:
        if scripted_scheduler is None:
            _fatal("--out-schedule requires --from-schedule")
        recording_scheduler = RecordingScheduler(scripted_scheduler)
        emit_schedule_path = Path(request.out_schedule)

    active_scheduler = (
        recording_scheduler if recording_scheduler is not None else scripted_scheduler
    )

    def _on_replay_step(actions: tuple[ActionNode, ...]) -> None:
        replay_steps.append(actions)
        if recording_scheduler is not None:
            schedule_step_bounds.append(len(recording_scheduler.decisions))

    if request.debug:
        sampler = random_traces_debug_replay(
            proto,  # type: ignore[type-var]
            init_func,
            step_func,
            settings,
            scheduler=active_scheduler,
            on_step=_on_replay_step,
        )
    else:
        sampler = random_traces_replay(
            proto,  # type: ignore[type-var]
            init_func,
            step_func,
            settings,
            scheduler=active_scheduler,
            on_step=_on_replay_step,
        )

    for trace_seed, t in sampler:  # type: ignore[type-var]
        match_step: int | None = None
        if predicate is not None:
            for i, s in enumerate(t):
                if _predicate_matches(predicate, s):
                    match_step = i
                    break

        shown_steps = len(t) if match_step is None else match_step + 1
        if emit_human_trace:
            rpt.out(
                f"Trace seed: "
                f"{trace_seed if request.seed is not None else 'from schedule'}"
            )
            rpt.out(f"Trace length: {len(t)}")

        if emit_human_trace and shown_steps > 0:
            rpt.out("Action trace:")
            for step_idx, step_nodes in enumerate(replay_steps[:shown_steps]):
                rpt.out(f"  {_format_replay_step_header(step_idx, rpt)}")
                for node in step_nodes:
                    formatted = _format_action_node(node)
                    if formatted:
                        rpt.out(f"    {formatted}")
                _print_pretty_state(step_idx, t[step_idx], "    ")
        _write_replay_itf(trace_seed, t, shown_steps, match_step)

        if emit_schedule_path is not None and recording_scheduler is not None:
            steps_decisions: list[tuple[SchedulerDecision, ...]] = []
            prev = 0
            for bound in schedule_step_bounds:
                steps_decisions.append(tuple(recording_scheduler.decisions[prev:bound]))
                prev = bound
            _write_check_schedule(emit_schedule_path, tuple(steps_decisions))
            artifacts.append(str(emit_schedule_path))
            if emit_human_trace:
                rpt.out(f"Wrote replay schedule to {emit_schedule_path}")

        if match_step is not None:
            assert predicate is not None
            rpt.out(_predicate_message(predicate, match_step))
            return ReplayResult(
                trace_seed=trace_seed if request.seed is not None else None,
                trace_length=len(t),
                violation_step=(
                    match_step
                    if predicate is not None and predicate.kind == "invariant"
                    else None
                ),
                example_step=(
                    match_step
                    if predicate is not None and predicate.kind == "example"
                    else None
                ),
                outcome_kind=(
                    predicate.outcome_kind if predicate is not None else "none"
                ),
                artifacts=artifacts,
            )

        return ReplayResult(
            trace_seed=trace_seed if request.seed is not None else None,
            trace_length=len(t),
            violation_step=None,
            example_step=None,
            outcome_kind="none",
            artifacts=artifacts,
        )

    return ReplayResult(
        trace_seed=request.seed,
        trace_length=0,
        violation_step=None,
        example_step=None,
        outcome_kind="none",
        artifacts=artifacts,
    )


def explain(request: ExplainRequest, reporter: Reporter | None = None) -> ExplainResult:
    """Explain an ITF trace without re-running the source specification."""
    rpt = _resolve_reporter(reporter)
    trace_path = Path(request.trace)
    if not trace_path.exists():
        _fatal(f"ITF trace not found: {trace_path}")
    try:
        raw = json.loads(trace_path.read_text())
    except json.JSONDecodeError as e:
        _fatal(f"Invalid ITF JSON in {trace_path}: {e}")
    if not isinstance(raw, dict):
        _fatal(f"ITF trace must be a JSON object: {trace_path}")

    try:
        renderer = render_itf_bdd_explanation if request.bdd else render_itf_explanation
        lines = renderer(raw, style=_trace_style_for_reporter(rpt))
    except Exception as e:
        _fatal(f"Invalid ITF trace in {trace_path}: {e}")
    for line in lines:
        rpt.out(line)

    meta = raw.get("#meta", {})
    if not isinstance(meta, dict):
        meta = {}
    states = raw.get("states", [])
    trace_length = len(states) if isinstance(states, list) else 0
    violation_step = meta.get("violation_step")
    example_step = meta.get("example_step")
    violation_step = violation_step if isinstance(violation_step, int) else None
    example_step = example_step if isinstance(example_step, int) else None
    if violation_step is not None:
        outcome_kind: Literal["none", "violation", "example_found"] = "violation"
    elif example_step is not None:
        outcome_kind = "example_found"
    else:
        outcome_kind = "none"
    return ExplainResult(
        trace_length=trace_length,
        violation_step=violation_step,
        example_step=example_step,
        outcome_kind=outcome_kind,
    )


def lint(request: LintRequest, reporter: Reporter | None = None) -> LintResult:
    """Lint a Wunderspec module."""
    from wunderspec.linter import analyze as run_lint_analysis
    from wunderspec.linter import render_effects_report

    rpt = _resolve_reporter(reporter)
    source_path = Path(request.spec)
    rpt.info(f"Linting: {source_path}")
    analysis = run_lint_analysis(source_path)
    artifacts: list[str] = []
    if request.effects_out is not None:
        effects_path = Path(request.effects_out)
        effects_path.write_text(render_effects_report(analysis.effects))
        artifacts.append(str(effects_path))
    return LintResult(
        error_count=len(analysis.errors),
        errors=analysis.errors,
        warning_count=len(analysis.warnings),
        warnings=analysis.warnings,
        artifacts=artifacts,
    )


def check(request: CheckRequest, reporter: Reporter | None = None) -> CheckResult:
    """Model-check a specification with exhaustive DFS."""
    rpt = _resolve_reporter(reporter)
    if request.max_steps is not None and request.max_steps < 0:
        _fatal("--max-steps must be >= 0")
    if request.timeout is not None and request.timeout <= 0:
        _fatal("--timeout must be > 0")
    if request.max_findings < 1:
        _fatal("--max-findings must be >= 1")

    proto, init_func, step_func, predicate = _load_spec(
        spec=request.spec,
        instance=request.instance,
        init=request.init,
        step=request.step,
        property_name=request.property,
    )

    shuffle_seed: int | None = None
    if not request.no_shuffle:
        shuffle_seed = (
            request.seed if request.seed is not None else random.randrange(2**63)
        )
        rpt.info(f"Shuffling with seed: {shuffle_seed}")

    mc_input = init_model_checker_input(
        proto,
        init_func,
        step_func,
        (
            None
            if predicate is None
            else (
                predicate.func
                if predicate.kind == "invariant"
                else (lambda s: Not(predicate.func(s)))
            )
        ),
        bound=request.bound,
        max_steps=request.max_steps,
        shuffle_seed=shuffle_seed,
        native_action_proto=(
            None
            if predicate is None or predicate.native_eval is None
            else predicate.native_eval.proto_state
        ),
        native_actions=(
            None
            if predicate is None or predicate.native_eval is None
            else predicate.native_eval.actions
        ),
    )

    progress = _CheckProgress(enabled=not request.no_progress)
    started_at = time.monotonic()
    deadline = started_at + request.timeout if request.timeout is not None else None
    produced_states = 0
    distinct_states = 0
    timed_out = False

    class _CheckTimeout(Exception):
        pass

    def _on_progress(produced: int, distinct: int) -> None:
        nonlocal produced_states, distinct_states
        produced_states = produced
        distinct_states = distinct
        progress.update(produced, distinct)
        if deadline is not None and time.monotonic() >= deadline:
            raise _CheckTimeout

    # Each finding is written (schedule, and ITF when requested) the moment it is
    # discovered, so partial results survive a timeout and `-` streams promptly.
    sink = _ItfNdjsonSink(request.out_itf)
    found_states: list[tuple[StateView, ...]] = []
    schedule_paths: list[str] = []
    artifacts: list[str] = []
    itf_params = list(proto._params)
    itf_vars = list(proto._vars)

    def _on_finding(
        idx: int,
        states: tuple[StateView, ...],
        schedule: tuple[tuple[SchedulerDecision, ...], ...],
    ) -> None:
        found_states.append(states)
        schedule_path_obj = _check_schedule_path(request.out_schedule, idx)
        _write_check_schedule(schedule_path_obj, schedule)
        schedule_paths.append(str(schedule_path_obj))
        artifacts.append(schedule_path_obj.name)
        if sink.enabled:
            meta: dict[str, Any] = {"source": "wunderspec check"}
            if predicate is not None:
                meta["predicate_kind"] = predicate.kind
                meta["predicate_name"] = predicate.name
                meta[
                    (
                        "violation_step"
                        if predicate.kind == "invariant"
                        else "example_step"
                    )
                ] = (len(states) - 1)
            sink.emit(
                itf_trace_json_line(states, meta=meta, params=itf_params, vars=itf_vars)
            )

    try:
        with locate_eval_errors():
            result = check_dfs(
                mc_input,
                on_progress=_on_progress,
                max_findings=request.max_findings,
                on_finding=_on_finding,
            )
    except _CheckTimeout:
        timed_out = True
        result = None
    except EvaluationError as ev:
        raise ApiError(_format_evaluation_error(ev, debug_hint=False)) from ev
    finally:
        progress.finish()
        sink.close()

    if timed_out:
        elapsed = time.monotonic() - started_at
        rpt.info(
            f"Timeout reached after {_human_duration(elapsed)} "
            f"(produced: {_human_count(produced_states)}, "
            f"distinct: {_human_count(distinct_states)})"
        )

    produced = result.produced_states_cnt if result is not None else produced_states
    distinct = result.distinct_states_cnt if result is not None else distinct_states
    has_finding = len(found_states) > 0
    itf_target = None if request.out_itf is None else str(request.out_itf)
    return CheckResult(
        produced_states=produced,
        distinct_states=distinct,
        violation_found=has_finding
        and predicate is not None
        and predicate.kind == "invariant",
        example_found=has_finding
        and predicate is not None
        and predicate.kind == "example",
        outcome_kind=(
            predicate.outcome_kind if has_finding and predicate is not None else "none"
        ),
        trace=found_states[0] if found_states else None,
        artifacts=artifacts,
        schedule_path=schedule_paths[0] if schedule_paths else None,
        traces=tuple(found_states),
        schedule_paths=tuple(schedule_paths),
        itf_path=itf_target,
        predicate_kind=predicate.kind if predicate is not None else None,
    )


def fuzz(request: FuzzRequest, reporter: Reporter | None = None) -> FuzzResult:
    """Run coverage-guided fuzzing on a specification."""
    if not is_feature_enabled("fuzz"):
        _fatal(feature_message("fuzz"))

    from wunderspec.fuzzer import FuzzerCorpus
    from wunderspec.fuzzer import fuzz as fuzzer_loop
    from wunderspec.petnames import funny_name

    rpt = _resolve_reporter(reporter)
    if request.timeout is not None and request.timeout <= 0:
        _fatal("--timeout must be > 0")

    proto, init_func, step_func, predicate = _load_spec(
        spec=request.spec,
        instance=request.instance,
        init=request.init,
        step=request.step,
        property_name=request.property,
    )

    # Load coverage function
    source_path = Path(request.spec)
    module = load_module(source_path)
    if not hasattr(module, request.coverage):
        _fatal(f"Coverage function not found: {request.coverage}")
    cov_candidate = getattr(module, request.coverage)
    if not callable(cov_candidate):
        _fatal(f"'{request.coverage}' is not callable")
    if not getattr(cov_candidate, "_is_coverage", False):
        _fatal(f"'{request.coverage}' is not decorated with @coverage")
    coverage_func = cov_candidate
    coverage_native_eval = _native_eval_context(proto, module)

    # Generate funny name from canonical args
    args_dict = {
        "spec": str(source_path.resolve()),
        "instance": request.instance,
        "init": request.init,
        "step": request.step,
        "coverage": request.coverage,
    }
    name = funny_name(args_dict)

    # Resolve corpus directory
    if request.corpus_dir is not None:
        corpus_base = Path(request.corpus_dir)
    else:
        corpus_base = Path("corpus")
    corpus_path = corpus_base / name

    # Load or create corpus
    corpus = FuzzerCorpus()
    corpus.load(corpus_path)
    corpus.save_args(corpus_path, args_dict)

    seed = request.seed if request.seed is not None else random.randrange(2**63)
    rpt.info(f"Fuzzing: {name}")
    rpt.info(f"Corpus: {corpus_path} ({len(corpus)} entries loaded)")
    rpt.info(f"Seed: {seed}")
    started_at = time.monotonic()

    try:
        with locate_eval_errors():
            stats = fuzzer_loop(
                proto,
                init_func,
                step_func,
                coverage_func=coverage_func,
                corpus=corpus,
                predicate=predicate,
                native_eval=coverage_native_eval,
                corpus_dir=corpus_path,
                max_generations=request.max_generations,
                max_steps=request.max_steps,
                bound=request.bound,
                seed=seed,
                no_progress=request.no_progress,
                no_energy=request.no_energy,
                timeout=request.timeout,
            )
    except KeyboardInterrupt:
        # Persist the current corpus before propagating Ctrl-C so interactive
        # fuzzing sessions can be resumed from the interrupted state.
        corpus.save(corpus_path)
        raise
    except EvaluationError as ev:
        corpus.save(corpus_path)
        raise ApiError(_format_evaluation_error(ev, debug_hint=False)) from ev

    if stats.timed_out:
        elapsed = time.monotonic() - started_at
        rpt.info(
            f"Timeout reached after {_human_duration(elapsed)} "
            f"(generations: {stats.generations}, execs: {_human_count(stats.total_execs)})"
        )

    # Persist corpus
    corpus.save(corpus_path)
    artifacts = [str(corpus_path)]

    if stats.violations > 0:
        rpt.error(f"Found {stats.violations} invariant violation(s)")
        outcome_kind: Literal["none", "violation", "example_found"] = "violation"
    elif stats.examples_found > 0:
        rpt.info(f"Found {stats.examples_found} example trace(s)")
        outcome_kind = "example_found"
    else:
        rpt.success(
            f"No {'examples' if predicate is not None and predicate.kind == 'example' else 'violations'} "
            f"in {stats.total_execs} executions"
            f" ({stats.corpus_size} corpus entries)"
        )
        outcome_kind = "none"

    return FuzzResult(
        generations=stats.generations,
        total_execs=stats.total_execs,
        total_steps=stats.total_steps,
        total_retries=stats.total_retries,
        corpus_size=stats.corpus_size,
        violations=stats.violations,
        examples_found=stats.examples_found,
        outcome_kind=outcome_kind,
        corpus_dir=str(corpus_path),
        artifacts=artifacts,
    )
