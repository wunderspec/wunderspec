"""
Debugging utilities for Wunderspec. To be used during development.
"""

import inspect
from functools import wraps
from typing import Any, Callable, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


def print_trace(cls=None, *, enabled=True, private=False, name=None):  # type: ignore[no-untyped-def]
    """
    Class decorator that traces all instance method calls.

    Usage:
        @print_trace
        class A: ...

        @print_trace(enabled=False)
        class B: ...
    """

    def decorate(cls):  # type: ignore[no-untyped-def]
        label = name or cls.__qualname__

        for attr, fn in inspect.getmembers(cls, inspect.isfunction):
            # skip private / dunder methods
            if not private and attr.startswith("_"):
                continue

            if attr.startswith("__"):
                continue

            def make_wrapper(fn: _F, attr: str) -> _F:
                @wraps(fn)
                def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                    if enabled:
                        print(
                            f"[TRACE] {label}.{attr}(" f"args={args}, kwargs={kwargs})"
                        )
                    result = fn(self, *args, **kwargs)
                    if enabled:
                        print(f"[TRACE] {label}.{attr} -> {result!r}")
                    return result

                return wrapper  # type: ignore[return-value]

            setattr(cls, attr, make_wrapper(fn, attr))

        return cls

    return decorate(cls) if cls else decorate
