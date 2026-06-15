"""
Safe serialization for Wunderspec AST nodes using pickle
with a restricted unpickler.

Only classes from ``wunderspec.ast.*`` and Python builtins
(``builtins``, ``enum``, ``collections``) are allowed.
"""

import io
import pickle
from typing import Any

from .ast import Node

# Modules whose classes may appear in a pickled AST.
_ALLOWED_MODULES = frozenset(
    {
        "wunderspec.ast.ast",
        "wunderspec.ast.sorts",
        "wunderspec.ast.set_ast",
        "wunderspec.ast.map_ast",
        "wunderspec.ast.list_ast",
        "wunderspec.ast.record_ast",
        "wunderspec.ast.tuple_ast",
        "wunderspec.ast.union_ast",
        "wunderspec.ast.action_ast",
        "wunderspec.ast.temporal_ast",
        "builtins",
        "enum",
        "collections",
    }
)


class _RestrictedUnpickler(pickle.Unpickler):
    """Unpickler that only allows wunderspec.ast classes."""

    def find_class(self, module: str, name: str) -> Any:
        if module not in _ALLOWED_MODULES:
            raise pickle.UnpicklingError(
                f"Refused to unpickle class {name!r} "
                f"from untrusted module {module!r}"
            )
        return super().find_class(module, name)


def save_ast(node: Node) -> bytes:
    """Serialize an AST node to bytes."""
    return pickle.dumps(node)


def load_ast(data: bytes) -> Node:
    """Deserialize an AST node from bytes.

    Only allows wunderspec.ast classes and Python builtins.
    Raises ``pickle.UnpicklingError`` on untrusted input.
    """
    node = _RestrictedUnpickler(io.BytesIO(data)).load()
    if not isinstance(node, Node):
        raise TypeError(f"Expected a Node, got {type(node).__name__}")
    return node
