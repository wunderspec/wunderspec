"""
Error types shared across the interpreter and the exploration engines.

This module is intentionally import-light: ``interpreter.py`` imports it on the
hot path, so it must not pull in heavy modules at import time. References to
control-flow exception classes are resolved lazily inside
:func:`_is_control_flow`.

Igor Konnov, 2026
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Generator, Optional

if TYPE_CHECKING:
    from wunderspec.ast.action_ast import ActionNode
    from wunderspec.ast.ast import SourceSpan


# When enabled, ``wunderspec.interpreter.value`` captures raw evaluation
# exceptions as :class:`EvaluationError` annotated with the source location of
# the failing subexpression. Disabled by default so direct interpreter callers
# (and the test-suite) keep seeing the original exceptions unchanged; the
# exploration engines enable it around their evaluation loops.
_locate_errors_enabled: ContextVar[bool] = ContextVar(
    "_locate_errors_enabled", default=False
)


@contextmanager
def locate_eval_errors() -> Generator[None]:
    """Enable :class:`EvaluationError` capture in ``value()`` for this context."""
    token = _locate_errors_enabled.set(True)
    try:
        yield
    finally:
        _locate_errors_enabled.reset(token)


def is_locating_errors() -> bool:
    """Whether evaluation-error capture is currently enabled."""
    return _locate_errors_enabled.get()


class EvaluationError(Exception):
    """Wraps a raw interpreter exception with the source location of the
    innermost AST subexpression that was being evaluated.

    The interpreter raises this from :func:`wunderspec.interpreter.value` when
    an unexpected exception escapes expression evaluation. The exploration
    engines (``run``/``check``/``fuzz``) catch it, optionally enrich it with the
    chain of actions leading to the failure, and the API layer renders it as a
    user-facing error pointing back into the specification.

    The fields are mutable so that enclosing frames can fill in information that
    the innermost frame did not have:

    - ``span`` is filled by climbing ``value()`` frames until a node with a
      source span is found.
    - ``trace_seed``/``step_index`` are attached by the engine for reproduction.
    - ``action_chain`` is filled by a replay pass that recovers the named
      actions leading to the failing step.
    """

    def __init__(
        self,
        original: BaseException,
        span: Optional["SourceSpan"] = None,
    ) -> None:
        super().__init__(str(original))
        self.original = original
        self.span = span
        self.trace_seed: Optional[int] = None
        self.step_index: Optional[int] = None
        self.action_chain: Optional[tuple["ActionNode", ...]] = None


def _is_control_flow(exc: BaseException) -> bool:
    """Return True for exceptions that drive control flow and must propagate
    through :func:`wunderspec.interpreter.value` un-wrapped.

    ``AssumptionViolated`` in particular is normal flow during exploration
    (it drives the retry loops); wrapping it would silently break the engines.
    Imports are function-local to keep this module free of import cycles.
    """
    if isinstance(exc, (StopIteration, GeneratorExit, KeyboardInterrupt, SystemExit)):
        return True
    from wunderspec.exec.context import AssumptionViolated, _OutsideChosenPath
    from wunderspec.machine import ControlFlowError

    return isinstance(exc, (AssumptionViolated, _OutsideChosenPath, ControlFlowError))
