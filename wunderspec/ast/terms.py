"""
Term constructors that mirror AST node __repr__ output.

This module provides constructor functions that create AST nodes
using the same syntax as their __repr__ representation, making it
possible to copy-paste repr output back into Python to recreate nodes.

Example:
    >>> from wunderspec.ast.terms import *
    >>> node = Assign(Var('x', IntSort()), SUB(Var('x', IntSort()), Lit(1)))
    >>> eval(repr(node)) == node
    True
"""

from wunderspec.ast.action_ast import (
    ActionAndNode,
    ActionChoiceNode,
    ActionLetNode,
    AssignNode,
    AssumeNode,
    NondetChoiceNode,
)
from wunderspec.ast.ast import (
    AlgebraNode,
    AlgebraOp,
    InNode,
    IteNode,
    LetNode,
    LitNode,
    Node,
    QuantOp,
    VarNode,
)
from wunderspec.ast.list_ast import (
    ListEnumNode,
    ListFilterNode,
    ListGetNode,
    ListKeysNode,
    ListRangeNode,
    ListReduceNode,
    ListSliceNode,
    ListUpdateNode,
)
from wunderspec.ast.map_ast import (
    MapEnumNode,
    MapGetNode,
    MapKeysNode,
    MapLambdaNode,
    MapSetNode,
)
from wunderspec.ast.record_ast import RecordCtorNode, RecordGetNode, RecordUpdateNode
from wunderspec.ast.set_ast import (
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
    SetQuantNode,
    SetReduceNode,
)
from wunderspec.ast.sorts import (
    ActionSort,
    BoolSort,
    EnumSort,
    IntSort,
    ListSort,
    MapSort,
    RecordSort,
    SetSort,
    Sort,
    StrSort,
    TemporalSort,
    TupleSort,
    UnionSort,
)
from wunderspec.ast.temporal_ast import (
    AlwaysNode,
    EnabledNode,
    EventuallyNode,
    Fair,
    FairnessNode,
    ToTemporalNode,
)
from wunderspec.ast.tuple_ast import TupleCtorNode, TupleGetNode, TupleUpdateNode
from wunderspec.ast.union_ast import UnionCtorNode, UnionGetTagNode, UnionMatchNode

# Re-export sorts for convenience
__all__ = [
    # Sorts
    "IntSort",
    "BoolSort",
    "StrSort",
    "ActionSort",
    "SetSort",
    "MapSort",
    "ListSort",
    "TupleSort",
    "RecordSort",
    "UnionSort",
    "EnumSort",
    # Basic nodes
    "Var",
    "Lit",
    "Let",
    "Ite",
    "In",
    # Arithmetic operators
    "ADD",
    "SUB",
    "MUL",
    "DIV",
    "MOD",
    "POW",
    "NEG",
    # Comparison operators
    "LT",
    "LE",
    "GT",
    "GE",
    "EQ",
    "NE",
    # Boolean operators
    "AND",
    "OR",
    "NOT",
    "IMPLIES",
    "IFF",
    # Set operators
    "UNION",
    "INTERSECT",
    "DIFFERENCE",
    "CARDINALITY",
    "SUBSETEQ",
    "FLATTEN",
    # List operators
    "LIST_CONCAT",
    "LIST_SIZE",
    # Set nodes
    "Set",
    "Ints",
    "UnsignedInts",
    "SetFilter",
    "SetMap",
    "SetQuant",
    "SetReduce",
    "Interval",
    "Choose",
    "AllSubsets",
    "AllMaps",
    "AllTuples",
    "AllRecords",
    # List nodes
    "List",
    "Range",
    "ListGet",
    "ListUpdate",
    "ListSlice",
    "ListFilter",
    "ListReduce",
    "ListKeys",
    # Map nodes
    "Map",
    "MapLambda",
    "MapGet",
    "MapSet",
    "MapReplace",
    "MapKeys",
    # Tuple nodes
    "Tuple",
    "TupleGet",
    "TupleUpdate",
    # Record nodes
    "Record",
    "RecordGet",
    "RecordUpdate",
    # Union nodes
    "UnionCtor",
    "UnionGetTag",
    "UnionMatch",
    # Action nodes
    "Assume",
    "Assign",
    "NondetData",
    "ActionChoice",
    "ActionAnd",
    # Temporal nodes
    "Always",
    "Eventually",
    "Enabled",
    "ToTemporal",
    "Fairness",  # Constructor for FairnessNode
    "Fair",  # Re-export enum for round-trip
    "FairnessNode",  # Direct node class for round-trip
    "TemporalSort",
]


# =============================================================================
# Basic node constructors
# =============================================================================


def Var(name: str, sort):
    """Create a variable node."""
    return VarNode(name, sort)


def Lit(value):
    """Create a literal node."""
    return LitNode(value)


def Let(name: str, value, body):
    """Create a let-binding node."""
    return LetNode(name, value, body)


def Ite(condition, then_node, else_node):
    """Create an if-then-else node."""
    return IteNode(condition, then_node, else_node)


def In(elem, set_node):
    """Create a set membership node."""
    return InNode(elem, set_node)


# =============================================================================
# Algebra node constructors (operators)
# =============================================================================


def _make_algebra(op: AlgebraOp):
    """Create an algebra node constructor for the given operator."""

    def constructor(*args):
        if not args:
            raise ValueError(f"{op.name} requires at least one argument")
        # Infer result sort from operator and arguments
        result_sort: Sort
        if op in {
            AlgebraOp.LT,
            AlgebraOp.LE,
            AlgebraOp.GT,
            AlgebraOp.GE,
            AlgebraOp.EQ,
            AlgebraOp.NE,
        }:
            result_sort = BoolSort()
        elif op in {
            AlgebraOp.AND,
            AlgebraOp.OR,
            AlgebraOp.NOT,
            AlgebraOp.IMPLIES,
            AlgebraOp.IFF,
        }:
            result_sort = BoolSort()
        elif op == AlgebraOp.CARDINALITY:
            result_sort = IntSort()
        elif op == AlgebraOp.FLATTEN:
            # Result is set of inner element sort
            outer_sort = args[0].sort
            if isinstance(outer_sort, SetSort) and isinstance(
                outer_sort.elem_sort, SetSort
            ):
                result_sort = SetSort(outer_sort.elem_sort.elem_sort)
            else:
                raise TypeError("FLATTEN requires a set of sets")
        elif op in {
            AlgebraOp.UNION,
            AlgebraOp.INTERSECT,
            AlgebraOp.DIFFERENCE,
            AlgebraOp.SUBSETEQ,
        }:
            if op == AlgebraOp.SUBSETEQ:
                result_sort = BoolSort()
            else:
                result_sort = args[0].sort
        elif op == AlgebraOp.LIST_CONCAT:
            result_sort = args[0].sort
        elif op == AlgebraOp.LIST_SIZE:
            result_sort = IntSort()
        else:
            # Arithmetic operators preserve sort
            result_sort = args[0].sort
        return AlgebraNode(result_sort, op, *args)

    return constructor


# Arithmetic operators
ADD = _make_algebra(AlgebraOp.ADD)
SUB = _make_algebra(AlgebraOp.SUB)
MUL = _make_algebra(AlgebraOp.MUL)
DIV = _make_algebra(AlgebraOp.DIV)
MOD = _make_algebra(AlgebraOp.MOD)
POW = _make_algebra(AlgebraOp.POW)
NEG = _make_algebra(AlgebraOp.NEG)

# Comparison operators
LT = _make_algebra(AlgebraOp.LT)
LE = _make_algebra(AlgebraOp.LE)
GT = _make_algebra(AlgebraOp.GT)
GE = _make_algebra(AlgebraOp.GE)
EQ = _make_algebra(AlgebraOp.EQ)
NE = _make_algebra(AlgebraOp.NE)

# Boolean operators
AND = _make_algebra(AlgebraOp.AND)
OR = _make_algebra(AlgebraOp.OR)
NOT = _make_algebra(AlgebraOp.NOT)
IMPLIES = _make_algebra(AlgebraOp.IMPLIES)
IFF = _make_algebra(AlgebraOp.IFF)

# Set operators
UNION = _make_algebra(AlgebraOp.UNION)
INTERSECT = _make_algebra(AlgebraOp.INTERSECT)
DIFFERENCE = _make_algebra(AlgebraOp.DIFFERENCE)
CARDINALITY = _make_algebra(AlgebraOp.CARDINALITY)
SUBSETEQ = _make_algebra(AlgebraOp.SUBSETEQ)
FLATTEN = _make_algebra(AlgebraOp.FLATTEN)

# List operators
LIST_CONCAT = _make_algebra(AlgebraOp.LIST_CONCAT)
LIST_SIZE = _make_algebra(AlgebraOp.LIST_SIZE)


# =============================================================================
# Set node constructors
# =============================================================================


def Set(*elements):
    """Create a set enumeration node.

    Can be called as:
        Set(Lit(1), Lit(2))  - creates set with elements, infers elem_sort
        Set(IntSort())       - creates empty set with given elem_sort
    """
    if len(elements) == 0:
        raise ValueError("Set requires at least one argument (elements or elem_sort)")
    if len(elements) == 1 and isinstance(elements[0], Sort):
        # Empty set: Set(IntSort()) -> SetEnumNode(IntSort())
        return SetEnumNode(elements[0])
    # Non-empty set: Set(Lit(1), Lit(2)) -> SetEnumNode(IntSort(), Lit(1), Lit(2))
    elem_sort = elements[0].sort
    return SetEnumNode(elem_sort, *elements)


# Singleton infinite sets
Ints = SetIntOrNatNode(is_signed=True)
UnsignedInts = SetIntOrNatNode(is_signed=False)


def SetFilter(var, base_set, predicate):
    """Create a set filter node."""
    return SetFilterNode(base_set, var, predicate)


def SetMap(var, base_set, mapper):
    """Create a set map node."""
    return SetMapNode(base_set, var, mapper)


def SetQuant(quant_kind, var, base_set, predicate):
    """Create a set quantifier node."""
    if isinstance(quant_kind, str):
        quant_kind = QuantOp[quant_kind.upper()]
    return SetQuantNode(quant_kind, base_set, var, predicate)


def SetReduce(vars_tuple, base_set, fun, initial):
    """Create a set reduce node."""
    acc_var, elem_var = vars_tuple
    return SetReduceNode(base_set, acc_var, elem_var, fun, initial)


def Interval(lower, upper):
    """Create an interval node."""
    return IntervalNode(lower, upper)


def Choose(var, base_set, predicate):
    """Create a choose node."""
    return ChooseNode(base_set, var, predicate)


def AllSubsets(base_set):
    """Create an all-subsets (power set) node."""
    return AllSubsetsNode(base_set)


def AllMaps(key_set, value_set):
    """Create an all-maps node."""
    return AllMapsNode(key_set, value_set)


def AllTuples(*sets):
    """Create an all-tuples (Cartesian product) node."""
    return AllTuplesNode(sets)


def AllRecords(**field_sets):
    """Create an all-records node."""
    return AllRecordsNode(field_sets)


# =============================================================================
# List node constructors
# =============================================================================


def List(*elements):
    """Create a list enumeration node.

    Can be called as:
        List(Lit(1), Lit(2))  - creates list with elements, infers elem_sort
        List(IntSort())       - creates empty list with given elem_sort
    """
    if len(elements) == 0:
        raise ValueError("List requires at least one argument (elements or elem_sort)")
    if len(elements) == 1 and isinstance(elements[0], Sort):
        # Empty list: List(IntSort()) -> ListEnumNode(IntSort())
        return ListEnumNode(elements[0])
    # Non-empty list: List(Lit(1), Lit(2)) -> ListEnumNode(IntSort(), Lit(1), Lit(2))
    elem_sort = elements[0].sort
    return ListEnumNode(elem_sort, *elements)


def Range(lower, upper):
    """Create a list range node."""
    return ListRangeNode(lower, upper)


def ListGet(list_node, index):
    """Create a list get node."""
    return ListGetNode(list_node, index)


def ListUpdate(base_list, index, new_value):
    """Create a list update node."""
    return ListUpdateNode(base_list, index, new_value)


def ListSlice(base_list, start, end):
    """Create a list slice node."""
    return ListSliceNode(base_list, start, end)


def ListFilter(var, base_list, predicate):
    """Create a list filter node."""
    return ListFilterNode(base_list, var, predicate)


def ListReduce(vars_tuple, base_list, fun, initial):
    """Create a list reduce node."""
    acc_var, elem_var = vars_tuple
    return ListReduceNode(base_list, acc_var, elem_var, fun, initial)


def ListKeys(list_node):
    """Create a list keys node."""
    return ListKeysNode(list_node)


# =============================================================================
# Map node constructors
# =============================================================================


def Map(*args, **kwargs):
    """Create a map enumeration node.

    Can be called as:
        Map(Tuple(k1, v1), Tuple(k2, v2))  - from repr output (key-value tuples)
        Map(IntSort(), StrSort())          - empty map with given sorts
        Map(a=Lit(1), b=Lit(2))            - with string keys as kwargs
    """
    if kwargs and not args:
        # kwargs form: Map(a=Lit(1), b=Lit(2))
        # Convert string keys to Lit nodes
        if not kwargs:
            raise ValueError("Map requires at least one mapping or sorts for empty map")
        first_value = next(iter(kwargs.values()))
        key_sort = StrSort()
        value_sort = first_value.sort
        mappings: dict[Node, Node] = {LitNode(k): v for k, v in kwargs.items()}
        return MapEnumNode(key_sort, value_sort, mappings)
    elif len(args) == 2 and all(isinstance(a, Sort) for a in args):
        # Empty map: Map(IntSort(), StrSort())
        return MapEnumNode(args[0], args[1], {})
    elif args and all(isinstance(a, TupleCtorNode) for a in args):
        # Tuple form: Map(Tuple(k1, v1), Tuple(k2, v2), ...)
        # Each arg should be a tuple with 2 elements
        if not args:
            raise ValueError("Map requires at least one Tuple")
        first_tuple = args[0]
        key_sort = first_tuple.elements[0].sort
        value_sort = first_tuple.elements[1].sort
        mappings_from_tuples: dict[Node, Node] = {
            t.elements[0]: t.elements[1] for t in args
        }
        return MapEnumNode(key_sort, value_sort, mappings_from_tuples)
    else:
        raise ValueError("Map requires either Tuple nodes, two Sort args, or kwargs")


def MapLambda(var, base_set, mapper):
    """Create a map lambda node."""
    return MapLambdaNode(base_set, var, mapper)


def MapGet(map_node, key):
    """Create a map get node."""
    return MapGetNode(map_node, key)


def MapSet(base_map, key, value):
    """Create a map set (insert) node."""
    return MapSetNode(base_map, key, value, replace_only=False)


def MapReplace(base_map, key, value):
    """Create a map replace node."""
    return MapSetNode(base_map, key, value, replace_only=True)


def MapKeys(map_node):
    """Create a map keys node."""
    return MapKeysNode(map_node)


# =============================================================================
# Tuple node constructors
# =============================================================================


def Tuple(*elements):
    """Create a tuple constructor node."""
    return TupleCtorNode(*elements)


def TupleGet(tuple_node, index):
    """Create a tuple get node."""
    return TupleGetNode(tuple_node, index)


def TupleUpdate(base_tuple, index, new_value):
    """Create a tuple update node."""
    return TupleUpdateNode(base_tuple, index, new_value)


# =============================================================================
# Record node constructors
# =============================================================================


def Record(**fields):
    """Create a record constructor node."""
    return RecordCtorNode(**fields)


def RecordGet(record_node, field_name):
    """Create a record get node."""
    return RecordGetNode(record_node, field_name)


def RecordUpdate(base_record, **updates):
    """Create a record update node."""
    return RecordUpdateNode(base_record, **updates)


# =============================================================================
# Union node constructors
# =============================================================================


def UnionCtor(union_sort, tag, payload=None):
    """Create a union constructor node."""
    return UnionCtorNode(union_sort, tag, payload)


def UnionGetTag(union_node):
    """Create a union get-tag node."""
    return UnionGetTagNode(union_node)


def UnionMatch(union_node, **cases):
    """Create a union match node.

    Takes tag=body kwargs. For variants with payloads, the body expression
    can reference the payload value. The constructor doesn't add variable
    bindings - those should be included in the body expressions if needed.
    """
    # Convert {tag: body} to {tag: (None, body)} format expected by UnionMatchNode
    formatted_cases: dict[str, tuple[VarNode | None, Node]] = {
        tag: (None, body) for tag, body in cases.items()
    }
    return UnionMatchNode(union_node, formatted_cases)


# =============================================================================
# Action node constructors
# =============================================================================


def Assume(condition):
    """Create an assume action node."""
    return AssumeNode(condition)


def Assign(var, expr):
    """Create an assign action node."""
    return AssignNode(var, expr)


def NondetData(var, base_set, body):
    """Create a nondeterministic data action node."""
    return NondetChoiceNode(var, base_set, body)


def ActionChoice(*actions):
    """Create an action choice node."""
    return ActionChoiceNode(*actions)


def ActionAnd(*actions):
    """Create an action conjunction node."""
    return ActionAndNode(*actions)


def ActionLet(name, value, body):
    """Create an action let-binding node."""
    return ActionLetNode(name, value, body)


# =============================================================================
# Temporal node constructors
# =============================================================================


def Always(subformula):
    """Create an always (□) node."""
    return AlwaysNode(subformula)


def Eventually(subformula):
    """Create an eventually (◇) node."""
    return EventuallyNode(subformula)


def Enabled(action):
    """Create an enabled node."""
    return EnabledNode(action)


def ToTemporal(bool_formula):
    """Create a ToTemporal node that promotes a Boolean to a temporal formula."""
    return ToTemporalNode(bool_formula)


def Fairness(fairness_kind, action, stuttering_vars):
    """Create a fairness node."""
    return FairnessNode(fairness_kind, action, stuttering_vars)
