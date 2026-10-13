"""Construct known AST classes from validated declarative data; never load user modules."""

from collections.abc import Callable
from enum import Enum
from typing import Any

from wunderspec.ast.ast import AlgebraOp, QuantOp, SourceSpan
from wunderspec.ast.temporal_ast import Fair
from wunderspec.transfer import encode
from wunderspec.transfer._parse import json_pointer
from wunderspec.transfer.errors import TransferError
from wunderspec.transfer.gather import GatheredSpec, GDef, GExtracted, GInstance


class Decoder:
    def __init__(self, document: dict):
        self.document = document
        self.nodes_by_id: dict[int, Any] = {}
        self._sorts: dict[int, Any] = {}
        self._nominal: dict[tuple[str, str], Any] = {}
        self._paths: dict[int, tuple[str | int, ...]] = {}
        self.enums: dict[str, Any] = {}
        self.record_names: dict[str, str] = {}
        self.union_names: dict[str, str] = {}
        declarations: dict[str, list[str]] = {}
        literals: dict[str, list[str]] = {}
        pending: list[tuple[Any, tuple[str | int, ...]]] = [(document, ())]
        while pending:
            value, path = pending.pop()
            if isinstance(value, dict):
                self._paths[id(value)] = path
                if "enum" in value and isinstance(value["enum"], dict):
                    f = value["enum"]
                    if "variants" in f:
                        previous = declarations.setdefault(f["name"], f["variants"])
                        if previous != f["variants"]:
                            raise TransferError(
                                "inconsistent_nominal_sort", self.path(value, "enum")
                            )
                    else:
                        members = literals.setdefault(f["enumName"], [])
                        if f["variant"] not in members:
                            members.append(f["variant"])
                pending.extend(
                    (v, (*path, k)) for k, v in reversed(list(value.items()))
                )
            elif isinstance(value, list):
                pending.extend(
                    (v, (*path, i)) for i, v in reversed(list(enumerate(value)))
                )
        for name in declarations.keys() | literals.keys():
            variants = declarations.get(name, literals.get(name, []))
            try:
                enum_factory: Any = Enum
                enum_type = enum_factory(name, variants, module=__name__)
            except (ValueError, TypeError):
                raise TransferError("invalid_identifier") from None
            if list(enum_type.__members__) != variants:
                raise TransferError("invalid_identifier")
            self.enums[name] = enum_type

    def path(self, value: dict, *parts: str | int) -> str:
        return json_pointer((*self._paths.get(id(value), ()), *parts))

    def remember(self, kind: str, name: str | None, sort: Any, value: dict) -> None:
        if name is not None:
            previous = self._nominal.setdefault((kind, name), sort)
            if previous != sort:
                raise TransferError(
                    "inconsistent_nominal_sort", self.path(value, next(iter(value)))
                )

    def sort(self, value: Any) -> Any:
        if isinstance(value, dict) and id(value) in self._sorts:
            return self._sorts[id(value)]
        try:
            result = self._sort(value)
        except (ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, TransferError):
                raise
            raise TransferError("ill_sorted", self.path(value)) from None
        if isinstance(value, dict):
            ((kind, fields),) = value.items()
            if kind in ("enum", "record", "union"):
                self.remember(kind, fields["name"], result, value)
            self._sorts[id(value)] = result
        return result

    def _sort(self, value: Any) -> Any:
        scalars = {
            "int": encode.IntSort,
            "bool": encode.BoolSort,
            "str": encode.StrSort,
            "action": encode.ActionSort,
            "temporal": encode.TemporalSort,
        }
        if isinstance(value, str):
            return scalars[value]()
        ((kind, f),) = value.items()
        if kind == "enum":
            return encode.EnumSort(self.enums[f["name"]])
        if kind == "set":
            return encode.SetSort(self.sort(f["elem"]))
        if kind == "list":
            return encode.ListSort(self.sort(f["elem"]))
        if kind == "map":
            return encode.MapSort(self.sort(f["key"]), self.sort(f["val"]))
        if kind == "tuple":
            return encode.TupleSort(*(self.sort(v) for v in f["elems"]))
        if kind == "record":
            sort = encode.RecordSort(**{n: self.sort(s) for n, s in f["fields"]})
            if f["name"] is not None:
                self.record_names[sort.name] = f["name"]
            return sort
        if kind == "union":
            union_sort = encode.UnionSort(
                **{n: self.sort(s) if s is not None else None for n, s in f["variants"]}
            )
            if f["name"] is not None:
                self.union_names[union_sort.name] = f["name"]
            return union_sort
        raise TransferError("wrong_type")

    def literal(self, value: dict) -> Any:
        ((kind, f),) = value.items()
        if kind == "enum":
            try:
                return self.enums[f["enumName"]][f["variant"]]
            except KeyError:
                raise TransferError("ill_sorted", self.path(value, kind)) from None
        return f[{"bool": "b", "int": "n", "str": "s"}[kind]]

    def nodes(self, values: list) -> tuple:
        return tuple(self.node(v) for v in values)

    def pairs(self, values: list) -> dict:
        return {n: self.node(v) for n, v in values}

    @staticmethod
    def mapped(factory: Callable[[dict], Any], pairs: list[tuple[Any, Any]]) -> Any:
        mapping = dict(pairs)
        if len(mapping) != len(pairs):
            # Constructors see dictionaries. Validate entries that conversion would
            # overwrite through the same constructor, without duplicating its rules.
            for key, value in pairs:
                factory({key: value})
        return factory(mapping)

    def optional(self, value: Any) -> Any:
        return self.node(value) if value is not None else None

    def node(self, value: dict) -> Any:
        if id(value) in self.nodes_by_id:
            return self.nodes_by_id[id(value)]
        ((kind, f),) = value.items()
        n, s = self.node, self.sort
        ns, pairs = self.nodes, self.pairs

        def bindings() -> list:
            return [(n(v), n(d)) for v, d in f["bindings"]]

        # An explicit constructor table: document strings can never select Python objects.
        constructors = {
            "var": lambda: encode.VarNode(
                f["name"], s(f["sort"]), unique_name=f["uniqueName"]
            ),
            "lit": lambda: encode.LitNode(self.literal(f["value"])),
            "letE": lambda: encode.LetNode(f["name"], n(f["value"]), n(f["body"])),
            "exprCall": lambda: encode.ExprCallNode(
                f["opName"], ns(f["args"]), n(f["body"]), tuple(f["paramNames"])
            ),
            "algebra": lambda: encode.AlgebraNode(
                s(f["resultSort"]), AlgebraOp[_OP[f["op"]]], *ns(f["args"])
            ),
            "inE": lambda: encode.InNode(n(f["elem"]), n(f["setNode"])),
            "ite": lambda: encode.IteNode(
                n(f["condition"]), n(f["thenNode"]), n(f["elseNode"])
            ),
            "setEnum": lambda: encode.SetEnumNode(s(f["elemSort"]), *ns(f["elements"])),
            "setIntOrNat": lambda: encode.SetIntOrNatNode(f["isSigned"]),
            "setFilter": lambda: encode.SetFilterNode(
                bindings=bindings(), body=n(f["body"])
            ),
            "setMap": lambda: encode.SetMapNode(bindings=bindings(), body=n(f["body"])),
            "setQuant": lambda: encode.SetQuantNode(
                QuantOp.FORALL if f["quant"] == "forall" else QuantOp.EXISTS,
                sort=s(f["sort"]),
                bindings=bindings(),
                body=n(f["body"]),
            ),
            "setReduce": lambda: encode.SetReduceNode(
                n(f["baseSet"]),
                n(f["accVar"]),
                n(f["elemVar"]),
                n(f["func"]),
                n(f["initial"]),
            ),
            "interval": lambda: encode.IntervalNode(n(f["lower"]), n(f["upper"])),
            "choose": lambda: encode.ChooseNode(
                n(f["baseSet"]), n(f["var"]), n(f["predicate"])
            ),
            "allSubsets": lambda: encode.AllSubsetsNode(n(f["baseSet"])),
            "allMaps": lambda: encode.AllMapsNode(n(f["keySet"]), n(f["valueSet"])),
            "allTuples": lambda: encode.AllTuplesNode(ns(f["sets"])),
            "allRecords": lambda: self.mapped(
                encode.AllRecordsNode, [(k, n(v)) for k, v in f["fieldSets"]]
            ),
            "mapEnum": lambda: self.mapped(
                lambda mappings: encode.MapEnumNode(
                    s(f["keySort"]), s(f["valueSort"]), mappings
                ),
                [(n(k), n(v)) for k, v in f["mappings"]],
            ),
            "mapLambda": lambda: encode.MapLambdaNode(
                n(f["baseSet"]), n(f["var"]), n(f["mapper"])
            ),
            "mapGet": lambda: encode.MapGetNode(n(f["mapNode"]), n(f["key"])),
            "mapSet": lambda: encode.MapSetNode(
                n(f["mapNode"]), n(f["key"]), n(f["value"]), f["replaceOnly"]
            ),
            "mapKeys": lambda: encode.MapKeysNode(n(f["mapNode"])),
            "listEnum": lambda: encode.ListEnumNode(
                s(f["elemSort"]), *ns(f["elements"])
            ),
            "listRange": lambda: encode.ListRangeNode(n(f["lower"]), n(f["upper"])),
            "listGet": lambda: encode.ListGetNode(n(f["listNode"]), n(f["index"])),
            "listUpdate": lambda: encode.ListUpdateNode(
                n(f["baseList"]), n(f["index"]), n(f["newValue"])
            ),
            "listSlice": lambda: encode.ListSliceNode(
                n(f["baseList"]), n(f["start"]), n(f["stop"])
            ),
            "listFilter": lambda: encode.ListFilterNode(
                n(f["baseList"]), n(f["var"]), n(f["predicate"])
            ),
            "listReduce": lambda: encode.ListReduceNode(
                n(f["baseList"]),
                n(f["accVar"]),
                n(f["elemVar"]),
                n(f["func"]),
                n(f["initial"]),
            ),
            "listKeys": lambda: encode.ListKeysNode(n(f["listNode"])),
            "recordCtor": lambda: encode.RecordCtorNode(**pairs(f["fields"])),
            "recordUpdate": lambda: self.mapped(
                lambda updates: encode.RecordUpdateNode(n(f["baseRecord"]), **updates),
                [(k, n(v)) for k, v in f["updates"]],
            ),
            "recordGet": lambda: encode.RecordGetNode(
                n(f["recordNode"]), f["fieldName"]
            ),
            "tupleCtor": lambda: encode.TupleCtorNode(*ns(f["elements"])),
            "tupleUpdate": lambda: encode.TupleUpdateNode(
                n(f["baseTuple"]), f["index"], n(f["newValue"])
            ),
            "tupleGet": lambda: encode.TupleGetNode(n(f["tupleNode"]), f["index"]),
            "unionCtor": lambda: encode.UnionCtorNode(
                s(f["unionSort"]), f["tag"], self.optional(f["payload"])
            ),
            "unionGetTag": lambda: encode.UnionGetTagNode(n(f["unionNode"])),
            "unionMatch": lambda: encode.UnionMatchNode(
                n(f["unionNode"]),
                {tag: (self.optional(v), n(body)) for tag, (v, body) in f["cases"]},
            ),
            "assume": lambda: encode.AssumeNode(n(f["cond"])),
            "assign": lambda: encode.AssignNode(n(f["var"]), n(f["expr"])),
            "nondetChoice": lambda: encode.NondetChoiceNode(
                n(f["var"]), n(f["baseSet"]), n(f["body"])
            ),
            "actionChoice": lambda: encode.ActionChoiceNode(
                *ns(f["actions"]),
                labels=tuple(f["labels"]) if f["labels"] is not None else None,
            ),
            "actionAnd": lambda: encode.ActionAndNode(*ns(f["actions"])),
            "actionCall": lambda: encode.ActionCallNode(
                f["actionName"], ns(f["args"]), n(f["body"])
            ),
            "actionLet": lambda: encode.ActionLetNode(
                f["name"], n(f["value"]), n(f["body"])
            ),
            "toTemporal": lambda: encode.ToTemporalNode(n(f["boolFormula"])),
            "always": lambda: encode.AlwaysNode(n(f["subformula"])),
            "eventually": lambda: encode.EventuallyNode(n(f["subformula"])),
            "enabled": lambda: encode.EnabledNode(n(f["action"])),
            "fairness": lambda: encode.FairnessNode(
                Fair.WEAK if f["kind"] == "weak" else Fair.STRONG,
                n(f["action"]),
                f["stutteringVars"],
            ),
        }
        try:
            result = constructors[kind]()
        except (
            ValueError,
            TypeError,
            KeyError,
            AssertionError,
            AttributeError,
            IndexError,
        ) as exc:
            if isinstance(exc, TransferError):
                raise
            path = self.path(value, kind)
            # Preserve the shared corpus's operand location on algebra failures.
            # The constructor owns the check; this only identifies its failing argument.
            if kind == "algebra" and len(f["args"]) > 1:
                first = n(f["args"][0]).sort
                for i, arg in enumerate(f["args"][1:], 1):
                    if n(arg).sort != first:
                        path = self.path(value, kind, "args", i)
                        break
            raise TransferError("ill_sorted", path) from None
        if kind in ("recordCtor", "allRecords") and f["name"] is not None:
            sort: Any = result.sort
            if kind == "allRecords":
                sort = sort.elem_sort
            self.remember("record", f["name"], sort, value)
            self.record_names[sort.name] = f["name"]
        self.nodes_by_id[id(value)] = result
        if "loc" in f:
            file, *span = f["loc"]
            result.source_span = SourceSpan(self.document["files"][file], *span)
        return result

    def instance(self, value: dict, order: list[str]) -> GInstance:
        """Each parameter's value, a closed expression, in machine-parameter order."""
        nodes = {p: self.node(v) for p, v in value["params"]}
        return GInstance(value["name"], [(p, nodes[p]) for p in order if p in nodes])

    def gathered(self) -> GatheredSpec:
        spec = self.document["spec"]

        def params(values: list) -> list:
            return [(name, self.sort(sort)) for name, sort in values]

        result = GatheredSpec(
            name=spec["name"],
            src_file=None,
            params=params(spec["params"]),
            state_vars=params(spec["stateVars"]),
            defs=[
                GDef(d["name"], d["kind"], self.node(d["body"]), params(d["params"]))
                for d in spec["defs"]
            ],
            extracted=[
                GExtracted(
                    d["name"],
                    tuple(n for n, _ in d["params"]),
                    tuple(self.sort(s) for _, s in d["params"]),
                    self.node(d["body"]),
                )
                for d in spec["extractedActions"]
            ],
            instances=[
                self.instance(d, [n for n, _ in spec["params"]])
                for d in spec["instances"]
            ],
            pure_exprs=list(spec["pureExprs"]),
            operator_comments=dict(spec["operatorComments"]),
            record_names=self.record_names,
            union_names=self.union_names,
            files=list(self.document["files"]),
            producer=dict(self.document["producer"]),
        )
        return result


_OP = {value: key for key, value in encode._ALGEBRA_OP.items()}
