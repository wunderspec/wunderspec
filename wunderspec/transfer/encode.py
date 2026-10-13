# Moved from wunderspec-lean commit 8cc1dcec2f7eb5f86345161a9a9bd677b2d72e66.
"""
Canonical JSON for the Wunderspec AST, matching Lean's *derived* `ToJson`
(see `Wunderspec/Data/AstJson.lean`). This is the Python reference encoding used
by the roundtrip check (`ws_lean.roundtrip`): we serialize the gathered spec here
and compare, value-for-value, against the JSON the Lean side dumps from the
emitted `<spec>_ast.lean`.

Encoding rules (Lean derived ToJson):
  * nullary constructor      -> the constructor name as a string
  * constructor with fields  -> {"ctorName": {field: value, ...}}
  * structure                -> {field: value, ...}
  * Option α                 -> value for `some`, None for `none`
  * α × β                    -> [a, b]
  * Int / Nat / Bool / String / List -> the obvious JSON scalar / array

Field names below must match the constructor parameter names in
`Wunderspec/Data/Ast.lean` exactly.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Callable

from wunderspec import __version__
from wunderspec.ast.action_ast import (
    ActionAndNode,
    ActionCallNode,
    ActionChoiceNode,
    ActionLetNode,
    AssignNode,
    AssumeNode,
    NondetChoiceNode,
)
from wunderspec.ast.ast import (
    AlgebraNode,
    ExprCallNode,
    InNode,
    IteNode,
    LetNode,
    LitNode,
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
    StrSort,
    TemporalSort,
    TupleSort,
    UnionSort,
)
from wunderspec.ast.temporal_ast import (
    AlwaysNode,
    EnabledNode,
    EventuallyNode,
    FairnessNode,
    ToTemporalNode,
)
from wunderspec.ast.tuple_ast import TupleCtorNode, TupleGetNode, TupleUpdateNode
from wunderspec.ast.union_ast import UnionCtorNode, UnionGetTagNode, UnionMatchNode
from wunderspec.transfer.errors import TransferError
from wunderspec.transfer.gather import GatheredSpec, GInstance

Json = Any


# ---------------------------------------------------------------------------
# Sorts
# ---------------------------------------------------------------------------

_ALGEBRA_OP = {
    "ADD": "add",
    "SUB": "sub",
    "MUL": "mul",
    "DIV": "div",
    "MOD": "mod",
    "POW": "pow",
    "NEG": "neg",
    "LT": "lt",
    "LE": "le",
    "GT": "gt",
    "GE": "ge",
    "EQ": "eq",
    "NE": "ne",
    "AND": "and",
    "OR": "or",
    "IMPLIES": "implies",
    "IFF": "iff",
    "NOT": "not",
    "UNION": "union",
    "INTERSECT": "intersect",
    "DIFFERENCE": "difference",
    "CARDINALITY": "cardinality",
    "SUBSETEQ": "subseteq",
    "FLATTEN": "flatten",
    "LIST_CONCAT": "listConcat",
    "LIST_SIZE": "listSize",
}


class Encoder:
    """An independent encoder; nominal names and file indices never escape an export."""

    def __init__(self, gathered: GatheredSpec | None = None):
        self.record_names = gathered.record_names if gathered else {}
        self.union_names = gathered.union_names if gathered else {}
        self.files = list(gathered.files) if gathered else []
        self.root = (
            Path(gathered.src_file).resolve().parent
            if gathered and gathered.src_file
            else None
        )

    def location(self, node: Any) -> list[int] | None:
        span = node.source_span
        if span is None or span.filename is None:
            return None
        if self.root is None:
            if span.filename not in self.files:
                return None
            filename = span.filename
        else:
            try:
                filename = (
                    Path(span.filename).resolve().relative_to(self.root).as_posix()
                )
            except ValueError:
                return None
        if filename not in self.files:
            self.files.append(filename)
        return [
            self.files.index(filename),
            span.lineno,
            span.col_offset,
            span.end_lineno,
            span.end_col_offset,
        ]

    def document_json(self, gathered: GatheredSpec) -> dict:
        spec = self.spec_json(gathered)
        return {
            "format": "wunderspec-transfer",
            "version": "1.1",
            "producer": gathered.producer
            or {"name": "wunderspec", "version": __version__},
            "files": self.files,
            "spec": spec,
        }

    def instance_json(self, inst: GInstance) -> Json:
        """Each parameter's value as the expression the factory built (a literal is a
        ``lit`` node), in machine-parameter order (format 1.1)."""
        return {
            "name": inst.name,
            "params": [[p, self.node_json(node)] for p, node in inst.params],
        }

    def _record_name_for(self, sort: Any) -> Any:
        return self.record_names.get(sort.name)

    def _union_name_for(self, sort: Any) -> Any:
        return self.union_names.get(sort.name)

    def sort_json(self, sort: Any) -> Json:
        if isinstance(sort, IntSort):
            return "int"
        if isinstance(sort, BoolSort):
            return "bool"
        if isinstance(sort, StrSort):
            return "str"
        if isinstance(sort, ActionSort):
            return "action"
        if isinstance(sort, TemporalSort):
            return "temporal"
        if isinstance(sort, EnumSort):
            et = sort.enum_type
            return {"enum": {"name": et.__name__, "variants": [m.name for m in et]}}
        if isinstance(sort, SetSort):
            return {"set": {"elem": self.sort_json(sort.elem_sort)}}
        if isinstance(sort, ListSort):
            return {"list": {"elem": self.sort_json(sort.elem_sort)}}
        if isinstance(sort, MapSort):
            return {
                "map": {
                    "key": self.sort_json(sort.key_sort),
                    "val": self.sort_json(sort.value_sort),
                }
            }
        if isinstance(sort, TupleSort):
            return {"tuple": {"elems": [self.sort_json(s) for s in sort.elem_sorts]}}
        if isinstance(sort, RecordSort):
            return {
                "record": {
                    "name": self._record_name_for(sort),
                    "fields": [[n, self.sort_json(s)] for n, s in sort.fields],
                }
            }
        if isinstance(sort, UnionSort):
            return {
                "union": {
                    "name": self._union_name_for(sort),
                    "variants": [
                        [t, (self.sort_json(s) if s is not None else None)]
                        for t, s in sort.variants
                    ],
                }
            }
        raise NotImplementedError(f"sort_json: {type(sort).__name__}")

    # ---------------------------------------------------------------------------
    # Literals / operators
    # ---------------------------------------------------------------------------

    def lit_json(self, value: Any) -> Json:
        if isinstance(value, bool):
            return {"bool": {"b": value}}
        if isinstance(value, int):
            return {"int": {"n": value}}
        if isinstance(value, str):
            return {"str": {"s": value}}
        if isinstance(value, Enum):
            return {"enum": {"enumName": type(value).__name__, "variant": value.name}}
        raise NotImplementedError(f"lit_json: {type(value).__name__}")

    def _algebra_op(self, op: Any) -> str:
        return _ALGEBRA_OP[op.name]

    def _quant(self, q: Any) -> str:
        return "forall" if q.name == "FORALL" else "exists"

    def _fair(self, kind: Any) -> str:
        return "weak" if kind.name == "WEAK" else "strong"

    # ---------------------------------------------------------------------------
    # Nodes
    # ---------------------------------------------------------------------------

    def _ns(self, nodes: Any) -> Json:
        return [self.node_json(n) for n in nodes]

    def _bindings(self, bindings: Any) -> Json:
        return [[self.node_json(v), self.node_json(d)] for v, d in bindings]

    def _named(self, pairs: Any) -> Json:
        return [[n, self.node_json(x)] for n, x in pairs]

    def _opt_node(self, x: Any) -> Json:
        return self.node_json(x) if x is not None else None

    # class -> (ctorName, fn(node) -> dict of Lean-field -> json)
    _NODE: dict[type, tuple[str, Callable[[Any, Any], dict]]] = {
        VarNode: (
            "var",
            lambda self, n: {
                "name": n.name,
                "sort": self.sort_json(n.sort),
                "uniqueName": n.unique_name,
            },
        ),
        LitNode: ("lit", lambda self, n: {"value": self.lit_json(n.value)}),
        LetNode: (
            "letE",
            lambda self, n: {
                "name": n.name,
                "value": self.node_json(n.value),
                "body": self.node_json(n.body),
            },
        ),
        ExprCallNode: (
            "exprCall",
            lambda self, n: {
                "opName": n.op_name,
                "args": self._ns(n.args),
                "body": self.node_json(n.body),
                "paramNames": list(n.param_names),
                "sort": self.sort_json(n.sort),
            },
        ),
        AlgebraNode: (
            "algebra",
            lambda self, n: {
                "resultSort": self.sort_json(n.sort),
                "op": self._algebra_op(n.op),
                "args": self._ns(n.args),
            },
        ),
        InNode: (
            "inE",
            lambda self, n: {
                "elem": self.node_json(n.elem),
                "setNode": self.node_json(n.set_node),
            },
        ),
        IteNode: (
            "ite",
            lambda self, n: {
                "condition": self.node_json(n.condition),
                "thenNode": self.node_json(n.then_node),
                "elseNode": self.node_json(n.else_node),
            },
        ),
        SetEnumNode: (
            "setEnum",
            lambda self, n: {
                "elemSort": self.sort_json(n.elem_sort),
                "elements": self._ns(n.elements),
            },
        ),
        SetIntOrNatNode: ("setIntOrNat", lambda self, n: {"isSigned": n.is_signed}),
        SetFilterNode: (
            "setFilter",
            lambda self, n: {
                "bindings": self._bindings(n.bindings),
                "body": self.node_json(n.body),
            },
        ),
        SetMapNode: (
            "setMap",
            lambda self, n: {
                "bindings": self._bindings(n.bindings),
                "body": self.node_json(n.body),
            },
        ),
        SetQuantNode: (
            "setQuant",
            lambda self, n: {
                "quant": self._quant(n.quant),
                "bindings": self._bindings(n.bindings),
                "body": self.node_json(n.body),
                "sort": self.sort_json(n.sort),
            },
        ),
        SetReduceNode: (
            "setReduce",
            lambda self, n: {
                "baseSet": self.node_json(n.base_set),
                "accVar": self.node_json(n.acc_var),
                "elemVar": self.node_json(n.elem_var),
                "func": self.node_json(n.fun),
                "initial": self.node_json(n.initial),
            },
        ),
        IntervalNode: (
            "interval",
            lambda self, n: {
                "lower": self.node_json(n.lower),
                "upper": self.node_json(n.upper),
            },
        ),
        ChooseNode: (
            "choose",
            lambda self, n: {
                "baseSet": self.node_json(n.base_set),
                "var": self.node_json(n.var),
                "predicate": self.node_json(n.predicate),
            },
        ),
        AllSubsetsNode: (
            "allSubsets",
            lambda self, n: {"baseSet": self.node_json(n.base_set)},
        ),
        AllMapsNode: (
            "allMaps",
            lambda self, n: {
                "keySet": self.node_json(n.key_set),
                "valueSet": self.node_json(n.value_set),
            },
        ),
        AllTuplesNode: ("allTuples", lambda self, n: {"sets": self._ns(n.sets)}),
        AllRecordsNode: (
            "allRecords",
            lambda self, n: {
                "name": self._record_name_for(n.sort.elem_sort),
                "fieldSets": self._named(sorted(n.field_sets.items())),
            },
        ),
        MapEnumNode: (
            "mapEnum",
            lambda self, n: {
                "keySort": self.sort_json(n.key_sort),
                "valueSort": self.sort_json(n.value_sort),
                "mappings": [
                    [self.node_json(k), self.node_json(v)]
                    for k, v in n.mappings.items()
                ],
            },
        ),
        MapLambdaNode: (
            "mapLambda",
            lambda self, n: {
                "baseSet": self.node_json(n.base_set),
                "var": self.node_json(n.var),
                "mapper": self.node_json(n.mapper),
            },
        ),
        MapGetNode: (
            "mapGet",
            lambda self, n: {
                "mapNode": self.node_json(n.map_node),
                "key": self.node_json(n.key),
            },
        ),
        MapSetNode: (
            "mapSet",
            lambda self, n: {
                "mapNode": self.node_json(n.base_map),
                "key": self.node_json(n.update_key),
                "value": self.node_json(n.update_value),
                "replaceOnly": n.replace_only,
            },
        ),
        MapKeysNode: (
            "mapKeys",
            lambda self, n: {"mapNode": self.node_json(n.map_node)},
        ),
        ListEnumNode: (
            "listEnum",
            lambda self, n: {
                "elemSort": self.sort_json(n.elem_sort),
                "elements": self._ns(n.elements),
            },
        ),
        ListRangeNode: (
            "listRange",
            lambda self, n: {
                "lower": self.node_json(n.lower),
                "upper": self.node_json(n.upper),
            },
        ),
        ListGetNode: (
            "listGet",
            lambda self, n: {
                "listNode": self.node_json(n.list_node),
                "index": self.node_json(n.index),
            },
        ),
        ListUpdateNode: (
            "listUpdate",
            lambda self, n: {
                "baseList": self.node_json(n.base_list),
                "index": self.node_json(n.index),
                "newValue": self.node_json(n.new_value),
            },
        ),
        ListSliceNode: (
            "listSlice",
            lambda self, n: {
                "baseList": self.node_json(n.base_list),
                "start": self.node_json(n.start),
                "stop": self.node_json(n.end),
            },
        ),
        ListFilterNode: (
            "listFilter",
            lambda self, n: {
                "baseList": self.node_json(n.base_list),
                "var": self.node_json(n.var),
                "predicate": self.node_json(n.predicate),
            },
        ),
        ListReduceNode: (
            "listReduce",
            lambda self, n: {
                "baseList": self.node_json(n.base_list),
                "accVar": self.node_json(n.acc_var),
                "elemVar": self.node_json(n.elem_var),
                "func": self.node_json(n.fun),
                "initial": self.node_json(n.initial),
            },
        ),
        ListKeysNode: (
            "listKeys",
            lambda self, n: {"listNode": self.node_json(n.list_node)},
        ),
        RecordCtorNode: (
            "recordCtor",
            lambda self, n: {
                "name": self._record_name_for(n.sort),
                "fields": self._named(n.fields),
            },
        ),
        RecordUpdateNode: (
            "recordUpdate",
            lambda self, n: {
                "baseRecord": self.node_json(n.base_record),
                "updates": self._named(n.updates),
            },
        ),
        RecordGetNode: (
            "recordGet",
            lambda self, n: {
                "recordNode": self.node_json(n.record_node),
                "fieldName": n.field_name,
            },
        ),
        TupleCtorNode: (
            "tupleCtor",
            lambda self, n: {"elements": self._ns(n.elements)},
        ),
        TupleUpdateNode: (
            "tupleUpdate",
            lambda self, n: {
                "baseTuple": self.node_json(n.base_tuple),
                "index": n.index,
                "newValue": self.node_json(n.new_value),
            },
        ),
        TupleGetNode: (
            "tupleGet",
            lambda self, n: {
                "tupleNode": self.node_json(n.tuple_node),
                "index": n.index,
            },
        ),
        UnionCtorNode: (
            "unionCtor",
            lambda self, n: {
                "unionSort": self.sort_json(n.sort),
                "tag": n.tag,
                "payload": self._opt_node(n.payload),
            },
        ),
        UnionGetTagNode: (
            "unionGetTag",
            lambda self, n: {"unionNode": self.node_json(n.union_node)},
        ),
        UnionMatchNode: (
            "unionMatch",
            lambda self, n: {
                "unionNode": self.node_json(n.union_node),
                "cases": [
                    [tag, [self._opt_node(var), self.node_json(body)]]
                    for tag, (var, body) in n.cases.items()
                ],
            },
        ),
        AssumeNode: ("assume", lambda self, n: {"cond": self.node_json(n.condition)}),
        AssignNode: (
            "assign",
            lambda self, n: {
                "var": self.node_json(n.var),
                "expr": self.node_json(n.expr),
            },
        ),
        NondetChoiceNode: (
            "nondetChoice",
            lambda self, n: {
                "var": self.node_json(n.var),
                "baseSet": self.node_json(n.base_set),
                "body": self.node_json(n.body),
            },
        ),
        ActionChoiceNode: (
            "actionChoice",
            lambda self, n: {
                "actions": self._ns(n.actions),
                "labels": (list(n.labels) if n.labels is not None else None),
            },
        ),
        ActionAndNode: ("actionAnd", lambda self, n: {"actions": self._ns(n.actions)}),
        ActionCallNode: (
            "actionCall",
            lambda self, n: {
                "actionName": n.action_name,
                "args": self._ns(n.args),
                "body": self.node_json(n.body),
            },
        ),
        ActionLetNode: (
            "actionLet",
            lambda self, n: {
                "name": n.name,
                "value": self.node_json(n.value),
                "body": self.node_json(n.body),
            },
        ),
        ToTemporalNode: (
            "toTemporal",
            lambda self, n: {"boolFormula": self.node_json(n.bool_formula)},
        ),
        AlwaysNode: (
            "always",
            lambda self, n: {"subformula": self.node_json(n.subformula)},
        ),
        EventuallyNode: (
            "eventually",
            lambda self, n: {"subformula": self.node_json(n.subformula)},
        ),
        EnabledNode: ("enabled", lambda self, n: {"action": self.node_json(n.action)}),
        FairnessNode: (
            "fairness",
            lambda self, n: {
                "kind": self._fair(n.kind),
                "action": self.node_json(n.action),
                "stutteringVars": list(n.stuttering_vars),
            },
        ),
    }

    def node_json(self, node: Any) -> Json:
        entry = self._NODE.get(type(node))
        if entry is None:
            raise TransferError("unknown_node_kind", location=node.source_span)
        ctor, fields_fn = entry
        fields = fields_fn(self, node)
        loc = self.location(node)
        if loc is not None:
            fields["loc"] = loc
        return {ctor: fields}

    # ---------------------------------------------------------------------------
    # Spec
    # ---------------------------------------------------------------------------

    def spec_json(self, g: GatheredSpec) -> Json:
        return {
            "name": g.name,
            "params": [[p, self.sort_json(s)] for p, s in g.params],
            "stateVars": [[v, self.sort_json(s)] for v, s in g.state_vars],
            "defs": [
                {
                    "name": d.name,
                    "kind": d.kind,
                    "params": [[p, self.sort_json(s)] for p, s in d.params],
                    "body": self.node_json(d.body),
                }
                for d in g.defs
            ],
            "extractedActions": [
                {
                    "name": d.name,
                    "params": [
                        [pn, self.sort_json(ps)]
                        for pn, ps in zip(d.param_names, d.param_sorts)
                    ],
                    "body": self.node_json(d.body),
                }
                for d in g.extracted
            ],
            "instances": [self.instance_json(inst) for inst in g.instances],
            "pureExprs": g.pure_exprs,
            "operatorComments": [
                [name, comment] for name, comment in sorted(g.operator_comments.items())
            ],
        }


def node_json(node: Any) -> Json:
    return Encoder().node_json(node)


def sort_json(sort: Any) -> Json:
    return Encoder().sort_json(sort)


def spec_json(gathered: GatheredSpec) -> Json:
    return Encoder(gathered).spec_json(gathered)


def document_json(gathered: GatheredSpec) -> dict:
    return Encoder(gathered).document_json(gathered)
