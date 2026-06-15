"""
Automatic source span tracking for AST nodes.

When enabled, every AST node records the source location of the user code
that created it.  Tracking is disabled by default for performance and can
be turned on in two ways:

1. Context manager:  ``with enable_source_tracking(): ...``
2. Environment variable:  ``WUNDERFUZZ_DEBUG=1``

The ``executing`` library is used to obtain precise column-level source
spans for the calling expression.  A per-frame cache ensures that a
single user expression that constructs several internal nodes only runs
the ``executing`` machinery once.

Igor Konnov, 2026
Generated with Claude Opus 4.6.
"""

from __future__ import annotations

import os
import platform
import sys
import types
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from wunderspec.ast import ast as _ast_module
from wunderspec.ast.ast import SourceSpan

_IS_CPYTHON = platform.python_implementation() == "CPython"

if _IS_CPYTHON:
    import executing  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Package-directory prefix used to skip internal frames
# ---------------------------------------------------------------------------
_WUNDERSPEC_DIR: str = (
    os.path.normpath(os.path.abspath(os.path.dirname(__file__))) + os.sep
)

# Cache from co_filename → normalized absolute path.  co_filename values are
# interned per code object, so the set is bounded by the number of loaded
# modules and this dict will not grow without bound.
_norm_filename_cache: dict[str, str] = {}


def _norm_filename(co_filename: str) -> str:
    """Return the normalized absolute path for *co_filename*, cached."""
    result = _norm_filename_cache.get(co_filename)
    if result is None:
        result = os.path.normpath(os.path.abspath(co_filename))
        _norm_filename_cache[co_filename] = result
    return result


# ---------------------------------------------------------------------------
# Context variables
# ---------------------------------------------------------------------------
_source_tracking_enabled: ContextVar[bool] = ContextVar(
    "_source_tracking_enabled",
    default=os.environ.get("WUNDERFUZZ_DEBUG") == "1",
)

# Cache: (id(frame), f_lasti) -> SourceSpan | None
# Prevents recomputation when a single user expression creates multiple nodes.
_cached_span: ContextVar[tuple[tuple[int, int], SourceSpan | None] | None] = ContextVar(
    "_cached_span", default=None
)


# ---------------------------------------------------------------------------
# Public context manager
# ---------------------------------------------------------------------------
@contextmanager
def enable_source_tracking() -> Generator[None]:
    """Enable automatic source-span capture for all AST nodes created
    inside this context."""
    if not _IS_CPYTHON:
        warnings.warn(
            "Source tracking requires CPython (using the 'executing' library). "
            "Spans will be None on this runtime.",
            stacklevel=2,
        )
    token = _source_tracking_enabled.set(True)
    try:
        yield
    finally:
        _source_tracking_enabled.reset(token)


# ---------------------------------------------------------------------------
# Internal: compute span for the nearest caller outside wunderspec
# ---------------------------------------------------------------------------
def _compute_source_span() -> SourceSpan | None:
    """Return a ``SourceSpan`` for the earliest stack frame outside the
    wunderspec package, or ``None`` if tracking is disabled or the frame
    cannot be identified."""
    if not _source_tracking_enabled.get():
        return None

    if not _IS_CPYTHON:
        return None

    # Walk the stack upward, skipping wunderspec-internal frames.
    # Start from frame 1 (our caller is Node.__init__).
    current: types.FrameType | None = sys._getframe(1)
    while current is not None:
        if not _norm_filename(current.f_code.co_filename).startswith(_WUNDERSPEC_DIR):
            break
        current = current.f_back
    else:
        return None

    if current is None:
        return None

    frame = current

    # Check the per-frame cache to avoid recomputation.
    cache_key = (id(frame), frame.f_lasti)
    cached = _cached_span.get()
    if cached is not None and cached[0] == cache_key:
        return cached[1]

    # Use the ``executing`` library for precise AST-node identification.
    span: SourceSpan | None = None
    try:
        ex = executing.Source.executing(frame)
        ast_node: Any = ex.node
        if ast_node is not None:
            span = SourceSpan(
                filename=frame.f_code.co_filename,
                lineno=ast_node.lineno,
                col_offset=ast_node.col_offset,
                end_lineno=getattr(ast_node, "end_lineno", ast_node.lineno),
                end_col_offset=getattr(ast_node, "end_col_offset", ast_node.col_offset),
            )
    except Exception:
        # executing may fail on synthetic frames, frozen code, etc.
        pass

    _cached_span.set((cache_key, span))
    return span


# ---------------------------------------------------------------------------
# Register the hook so that Node.__init__ calls _compute_source_span
# ---------------------------------------------------------------------------
if _IS_CPYTHON:
    _ast_module._source_span_hook = _compute_source_span
