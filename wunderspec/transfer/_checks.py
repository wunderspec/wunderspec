"""FR-030: scope and document constraints absent from the AST constructors.

Local typing and result sorts belong to the AST objects. This traversal uses
those objects' sorts rather than calculating a parallel type system.
"""

from typing import Any

from wunderspec.ast.ast import VarNode
from wunderspec.ast.sorts import (
    ActionSort,
    BoolSort,
    IntSort,
    ListSort,
    SetSort,
    TemporalSort,
)
from wunderspec.transfer._construct import Decoder
from wunderspec.transfer.errors import TransferError
from wunderspec.transfer.gather import GatheredSpec

# Instance values (format 1.1, rule 3a) are data: no action or temporal nodes.
_NOT_IN_VALUES = frozenset(
    {
        "assume",
        "assign",
        "nondetChoice",
        "actionChoice",
        "actionAnd",
        "actionCall",
        "actionLet",
        "toTemporal",
        "always",
        "eventually",
        "enabled",
        "fairness",
    }
)

_BODY_SORTS = {
    "init": (ActionSort(),),
    "action": (ActionSort(),),
    "invariant": (BoolSort(),),
    "example": (BoolSort(),),
    "temporal": (TemporalSort(), BoolSort()),
}


class ContextChecker:
    def __init__(self, decoder: Decoder):
        self.decoder = decoder
        self.state: frozenset[str] = frozenset()
        self.in_value = False

    def fail(self, value: dict, *parts: str | int, problem: str = "ill_sorted") -> None:
        raise TransferError(problem, self.decoder.path(value, *parts))

    def expect(self, value: dict, expected: Any) -> None:
        if self.decoder.node(value).sort != expected:
            self.fail(value)

    @staticmethod
    def key(var: VarNode) -> str:
        return f"{var.name}/{var.unique_name}" if var.unique_name else var.name

    def bind(self, value: dict, scope: dict, expected: Any = None) -> dict:
        var = self.decoder.node(value)
        if not isinstance(var, VarNode):
            self.fail(value)
        if expected is not None:
            self.expect(value, expected)
        return {**scope, self.key(var): var.sort}

    def element(self, value: dict, kind: type = SetSort) -> Any:
        sort = self.decoder.node(value).sort
        if not isinstance(sort, kind):
            self.fail(value)
        return sort.elem_sort

    def walk(self, value: Any, scope: dict) -> None:
        if isinstance(value, dict):
            if id(value) in self.decoder.nodes_by_id:
                self.node(value, scope)
            else:
                for child in value.values():
                    self.walk(child, scope)
        elif isinstance(value, list):
            for child in value:
                self.walk(child, scope)

    def node(self, value: dict, scope: dict) -> None:
        ((kind, f),) = value.items()
        if self.in_value and kind in _NOT_IN_VALUES:
            self.fail(value, kind)
        obj = self.decoder.node(value)
        handled: set[str] = set()
        if kind == "var":
            key = self.key(obj)
            if key not in scope:
                self.fail(value, kind, problem="unbound_variable")
            if obj.sort != scope[key]:
                self.fail(value, kind)
        elif kind in ("letE", "actionLet"):
            self.walk(f["value"], scope)
            self.walk(
                f["body"], {**scope, f["name"]: self.decoder.node(f["value"]).sort}
            )
            handled = {"value", "body"}
        elif kind == "exprCall":
            if len(f["args"]) != len(f["paramNames"]):
                self.fail(value, kind, "args")
            self.walk(f["args"], scope)
            inner = {
                **scope,
                **{
                    name: self.decoder.node(arg).sort
                    for name, arg in zip(f["paramNames"], f["args"], strict=True)
                },
            }
            self.walk(f["body"], inner)
            if obj.sort != self.decoder.sort(f["sort"]):
                self.fail(value, kind, "sort")
            handled = {"args", "body"}
        elif kind in ("setFilter", "setMap", "setQuant"):
            inner = scope
            for var, domain in f["bindings"]:
                self.walk(domain, inner)
                inner = self.bind(var, inner, self.element(domain))
            self.walk(f["body"], inner)
            if kind == "setFilter":
                self.expect(f["body"], BoolSort())
            handled = {"bindings", "body"}
        elif kind in ("choose", "mapLambda", "listFilter", "nondetChoice"):
            base = "baseList" if kind == "listFilter" else "baseSet"
            body = {
                "choose": "predicate",
                "mapLambda": "mapper",
                "listFilter": "predicate",
                "nondetChoice": "body",
            }[kind]
            self.walk(f[base], scope)
            # NondetChoiceNode already checks the domain/variable sort agreement.
            expected = (
                None
                if kind == "nondetChoice"
                else self.element(
                    f[base], ListSort if kind == "listFilter" else SetSort
                )
            )
            inner = self.bind(f["var"], scope, expected)
            self.walk(f[body], inner)
            if kind == "choose":
                self.expect(f[body], BoolSort())
            handled = {base, "var", body}
        elif kind in ("setReduce", "listReduce"):
            base = "baseSet" if kind == "setReduce" else "baseList"
            self.walk(f[base], scope)
            self.walk(f["initial"], scope)
            inner = self.bind(f["accVar"], scope, self.decoder.node(f["initial"]).sort)
            inner = self.bind(
                f["elemVar"],
                inner,
                self.element(f[base], SetSort if kind == "setReduce" else ListSort),
            )
            self.walk(f["func"], inner)
            handled = {base, "initial", "accVar", "elemVar", "func"}
        elif kind == "unionMatch":
            if len(f["cases"]) != len(obj.cases):
                self.fail(value, kind, "cases")
            self.walk(f["unionNode"], scope)
            for _, (var, body) in f["cases"]:
                inner = self.bind(var, scope) if var is not None else scope
                self.walk(body, inner)
            # UnionMatchNode checks exhaustiveness, payload and result sorts.
            handled = {"unionNode", "cases"}
        elif kind == "inE":
            self.expect(f["elem"], self.element(f["setNode"]))
        elif kind == "interval":
            self.expect(f["lower"], IntSort())
            self.expect(f["upper"], IntSort())
        elif kind == "assign":
            target = self.decoder.node(f["var"])
            if not isinstance(target, VarNode) or target.name not in self.state:
                self.fail(value, kind, "var")
        elif kind == "toTemporal":
            self.expect(f["boolFormula"], BoolSort())
        elif kind in ("always", "eventually"):
            if self.decoder.node(f["subformula"]).sort not in (
                BoolSort(),
                TemporalSort(),
            ):
                self.fail(value, kind, "subformula")
        elif kind in ("enabled", "fairness"):
            self.expect(f["action"], ActionSort())
        elif kind == "actionCall":
            self.expect(f["body"], ActionSort())
        for name, child in f.items():
            if name not in handled:
                self.walk(child, scope)

    def spec(self, document: dict, gathered: GatheredSpec) -> None:
        spec = document["spec"]
        base = dict(gathered.params + gathered.state_vars)
        self.state = frozenset(name for name, _ in gathered.state_vars)
        for definition, source in zip(gathered.defs, spec["defs"], strict=True):
            self.node(source["body"], {**base, **dict(definition.params)})
            allowed = _BODY_SORTS.get(definition.kind)
            if allowed is not None and definition.body.sort not in allowed:
                self.fail(source, "body")
        for action, source in zip(
            gathered.extracted, spec["extractedActions"], strict=True
        ):
            scope = {
                **base,
                **dict(zip(action.param_names, action.param_sorts, strict=True)),
            }
            self.node(source["body"], scope)
            self.expect(source["body"], ActionSort())
        params = dict(gathered.params)
        for source in spec["instances"]:
            for i, (name, value) in enumerate(source["params"]):
                self.instance_value(value, params[name], source, i)

    def instance_value(self, value: dict, declared: Any, source: dict, i: int) -> None:
        """Rule 3a: an instance parameter's value is a closed expression of the parameter's
        sort. With no machine parameters or state variables in scope, any variable not
        bound inside the value is unbound."""
        state, self.state, self.in_value = self.state, frozenset(), True
        try:
            self.node(value, {})
        finally:
            self.state, self.in_value = state, False
        if self.decoder.node(value).sort != declared:
            self.fail(source, "params", i, 1)
