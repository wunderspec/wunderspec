"""Deprecated pickle serialization for Wunderspec AST nodes (W1 / FR-032).

Load only AST files you produced yourself. The explicit class allowlist is defense
in depth, not a guarantee that pickle is suitable for untrusted input. Migrate to
``wunderspec.transfer`` when it becomes available in wunderspec 0.138.0.
"""

import io
import pickle
import pickletools
import warnings
from typing import Any

from . import (
    action_ast,
    ast,
    list_ast,
    map_ast,
    record_ast,
    set_ast,
    sorts,
    temporal_ast,
    tuple_ast,
    union_ast,
)
from .ast import Node

# Enumerate the data classes explicitly: never trust an entire module, its imported
# helpers, or newly added classes. REDUCE may resolve only these AST classes/enums.
_ALLOWED_TYPES: tuple[type, ...] = (
    ast.Node,
    ast.SourceSpan,
    ast.VarNode,
    ast.LetNode,
    ast.ExprCallNode,
    ast.AlgebraNode,
    ast.LitNode,
    ast.InNode,
    ast.IteNode,
    ast.AlgebraOp,
    ast.QuantOp,
    sorts.Sort,
    sorts.IntSort,
    sorts.BoolSort,
    sorts.ActionSort,
    sorts.TemporalSort,
    sorts.StrSort,
    sorts.EnumSort,
    sorts.SetSort,
    sorts.MapSort,
    sorts.RecordSort,
    sorts.ListSort,
    sorts.UnionSort,
    sorts.TupleSort,
    set_ast.SetNode,
    set_ast.SetEnumNode,
    set_ast.SetIntOrNatNode,
    set_ast.SetFilterNode,
    set_ast.SetMapNode,
    set_ast.SetQuantNode,
    set_ast.SetReduceNode,
    set_ast.IntervalNode,
    set_ast.ChooseNode,
    set_ast.AllSubsetsNode,
    set_ast.AllMapsNode,
    set_ast.AllTuplesNode,
    set_ast.AllRecordsNode,
    map_ast.MapNode,
    map_ast.MapEnumNode,
    map_ast.MapLambdaNode,
    map_ast.MapGetNode,
    map_ast.MapSetNode,
    map_ast.MapKeysNode,
    list_ast.ListNode,
    list_ast.ListEnumNode,
    list_ast.ListRangeNode,
    list_ast.ListGetNode,
    list_ast.ListUpdateNode,
    list_ast.ListSliceNode,
    list_ast.ListFilterNode,
    list_ast.ListReduceNode,
    list_ast.ListKeysNode,
    record_ast.RecordNode,
    record_ast.RecordCtorNode,
    record_ast.RecordUpdateNode,
    record_ast.RecordGetNode,
    tuple_ast.TupleNode,
    tuple_ast.TupleCtorNode,
    tuple_ast.TupleUpdateNode,
    tuple_ast.TupleGetNode,
    union_ast.UnionCtorNode,
    union_ast.UnionGetTagNode,
    union_ast.UnionMatchNode,
    action_ast.ActionNode,
    action_ast.AssumeNode,
    action_ast.AssignNode,
    action_ast.NondetChoiceNode,
    action_ast.ActionChoiceNode,
    action_ast.ActionAndNode,
    action_ast.ActionCallNode,
    action_ast.ActionLetNode,
    temporal_ast.TemporalNode,
    temporal_ast.ToTemporalNode,
    temporal_ast.AlwaysNode,
    temporal_ast.EventuallyNode,
    temporal_ast.EnabledNode,
    temporal_ast.Fair,
    temporal_ast.FairnessNode,
)
_ALLOWED_GLOBALS = {(cls.__module__, cls.__name__): cls for cls in _ALLOWED_TYPES}


class _RestrictedUnpickler(pickle.Unpickler):
    """Resolve only the explicit AST and sort classes and internal AST enums."""

    def find_class(self, module: str, name: str) -> Any:
        try:
            return _ALLOWED_GLOBALS[module, name]
        except KeyError:
            raise pickle.UnpicklingError(
                f"Refused to unpickle global {module}.{name}: untrusted module or name"
            ) from None


def save_ast(node: Node) -> bytes:
    """Serialize an AST node to bytes (deprecated; only for your own files)."""
    warnings.warn(
        "save_ast is deprecated; migrate to wunderspec.transfer "
        "(available in wunderspec 0.138.0).",
        DeprecationWarning,
        stacklevel=2,
    )
    return pickle.dumps(node)


def load_ast(data: bytes) -> Node:
    """Deserialize an AST node from a file you produced yourself (deprecated).

    Only explicit AST and sort classes and internal AST enums may be resolved.
    Raises ``pickle.UnpicklingError`` on forbidden globals or pickle extensions.
    """
    warnings.warn(
        "load_ast is deprecated; migrate to wunderspec.transfer "
        "(available in wunderspec 0.138.0). Load only files you produced yourself.",
        DeprecationWarning,
        stacklevel=2,
    )
    # EXT cache hits bypass find_class. AST pickles do not need extensions, so
    # reject them before unpickling rather than mutate the process-wide cache.
    try:
        for opcode, _, _ in pickletools.genops(data):
            if opcode.name in {"EXT1", "EXT2", "EXT4"}:
                raise pickle.UnpicklingError("Refused to unpickle extension opcode")
    except ValueError as exc:
        raise pickle.UnpicklingError("Malformed AST pickle") from exc
    node = _RestrictedUnpickler(io.BytesIO(data)).load()
    if not isinstance(node, Node):
        raise TypeError(f"Expected a Node, got {type(node).__name__}")
    return node
