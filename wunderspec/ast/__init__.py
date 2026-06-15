"""
Pure AST data structures for Wunderspec.

This module contains the core AST node classes as pure data structures
without operator logic.

Igor Konnov, 2025
"""

from .ast import (
    ARITH_OPS,
    BOOL_OPS,
    CMP_OPS,
    EQ_OPS,
    LIST_OPS,
    REL_OPS,
    SET_OPS,
    AlgebraNode,
    AlgebraOp,
    ExprCallNode,
    InNode,
    IteNode,
    LetNode,
    LitNode,
    Node,
    QuantOp,
    SourceSpan,
    VarNode,
)
from .list_ast import (
    ListEnumNode,
    ListFilterNode,
    ListGetNode,
    ListKeysNode,
    ListNode,
    ListRangeNode,
    ListReduceNode,
    ListSliceNode,
    ListUpdateNode,
)
from .map_ast import MapEnumNode, MapGetNode, MapLambdaNode, MapNode, MapSetNode
from .record_ast import RecordCtorNode, RecordGetNode, RecordNode, RecordUpdateNode
from .set_ast import (
    AllMapsNode,
    AllRecordsNode,
    AllSubsetsNode,
    AllTuplesNode,
    ChooseNode,
    IntervalNode,
    SetEnumNode,
    SetFilterNode,
    SetIntOrNatNode,
    SetMapNode,
    SetNode,
    SetQuantNode,
    SetReduceNode,
)
from .sorts import (
    BoolSort,
    EnumSort,
    IntSort,
    ListSort,
    MapSort,
    RecordSort,
    SetSort,
    Sort,
    StrSort,
    TupleSort,
    UnionSort,
    sort_of,
)
from .tuple_ast import TupleCtorNode, TupleGetNode, TupleNode, TupleUpdateNode
from .union_ast import UnionCtorNode, UnionGetTagNode, UnionMatchNode

__all__ = [
    # Base
    "Sort",
    "IntSort",
    "BoolSort",
    "StrSort",
    "EnumSort",
    "SetSort",
    "MapSort",
    "ListSort",
    "RecordSort",
    "sort_of",
    "SourceSpan",
    "Node",
    "VarNode",
    "LetNode",
    "ExprCallNode",
    "LitNode",
    "AlgebraNode",
    "InNode",
    "AlgebraOp",
    "ARITH_OPS",
    "CMP_OPS",
    "EQ_OPS",
    "REL_OPS",
    "BOOL_OPS",
    "SET_OPS",
    "QuantOp",
    # Set AST
    "SetNode",
    "SetEnumNode",
    "SetFilterNode",
    "SetMapNode",
    "SetQuantNode",
    "SetReduceNode",
    "SetIntOrNatNode",
    "IntervalNode",
    "ChooseNode",
    "AllSubsetsNode",
    "AllMapsNode",
    "AllTuplesNode",
    "AllRecordsNode",
    # Map AST
    "MapNode",
    "MapEnumNode",
    "MapLambdaNode",
    "MapGetNode",
    "MapSetNode",
    # Record AST
    "RecordNode",
    "RecordCtorNode",
    "RecordUpdateNode",
    "RecordGetNode",
    # Tuple AST
    "TupleSort",
    "TupleNode",
    "TupleCtorNode",
    "TupleUpdateNode",
    "TupleGetNode",
    # List AST
    "ListNode",
    "ListEnumNode",
    "ListRangeNode",
    "ListGetNode",
    "ListUpdateNode",
    "ListSliceNode",
    "ListFilterNode",
    "ListReduceNode",
    "ListKeysNode",
    "LIST_OPS",
    # Union AST
    "UnionSort",
    "UnionCtorNode",
    "UnionGetTagNode",
    "UnionMatchNode",
    # Ite
    "IteNode",
]
