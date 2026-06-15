"""
Unique name generation using context variables.

This module provides thread-safe unique name generation using Python's
contextvars. Each async task or thread gets its own counter, avoiding
collisions in concurrent scenarios.

Igor Konnov, 2026
"""

from contextvars import ContextVar

# Context variable holding the current counter value
_name_counter: ContextVar[int] = ContextVar("_name_counter", default=0)


def fresh_name(prefix: str = "_tmp") -> str:
    """Generate a fresh unique name with the given prefix.

    Args:
        prefix: The prefix for the generated name. Defaults to "_tmp".

    Returns:
        A unique name in the format "{prefix}{counter}".

    Example:
        >>> reset_name_counter()
        >>> fresh_name()
        '_tmp0'
        >>> fresh_name()
        '_tmp1'
        >>> fresh_name("var")
        'var2'
    """
    counter = _name_counter.get()
    _name_counter.set(counter + 1)
    return f"{prefix}{counter}"


def reset_name_counter() -> None:
    """Reset the name counter to 0.

    This is primarily useful for testing to ensure deterministic name generation.
    """
    _name_counter.set(0)


def get_name_counter() -> int:
    """Get the current value of the name counter.

    Returns:
        The current counter value.
    """
    return _name_counter.get()
