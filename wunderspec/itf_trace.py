"""Helpers for writing ITF traces from interpreted Wunderspec values."""

from __future__ import annotations

import json
from collections import namedtuple
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, cast

from itf_py import State as ItfState
from itf_py import Trace as ItfTrace
from itf_py import itf_variant, trace_to_json

from wunderspec.ast.sorts import (
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
)
from wunderspec.interpreter_value import (
    AbstractSetValue,
    BoolValue,
    EnumeratedSetValue,
    EnumValue,
    IntValue,
    IValue,
    ListValue,
    MapValue,
    RecordValue,
    StateView,
    StrValue,
    TupleValue,
    UnionValue,
)

_VARIANT_CLASSES: dict[tuple[str, tuple[str, ...]], type[Any]] = {}
_RECORD_CLASSES: dict[tuple[str, ...], type[tuple[Any, ...]]] = {}


class _HashableList(list[Any]):
    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(self))


class _HashableDict(dict[Any, Any]):
    def __hash__(self) -> int:  # type: ignore[override]
        return hash(frozenset(self.items()))


def _variant_object(tag: str, fields: dict[str, Any]) -> object:
    field_names = tuple(fields)
    key = (tag, field_names)
    cls = _VARIANT_CLASSES.get(key)
    if cls is None:
        cls = type(tag, (), {})
        itf_variant(cls)
        _VARIANT_CLASSES[key] = cls
    obj = cls()
    for name, value in fields.items():
        setattr(obj, name, value)
    return obj


def _record_object(fields: tuple[tuple[str, Any], ...]) -> tuple[Any, ...]:
    field_names = tuple(name for name, _value in fields)
    record_cls = _RECORD_CLASSES.get(field_names)
    if record_cls is None:
        record_cls = cast(
            type[tuple[Any, ...]],
            namedtuple("Record", field_names),  # type: ignore[misc]
        )
        _RECORD_CLASSES[field_names] = record_cls
    return record_cls(*(value for _name, value in fields))


def value_to_itf(value: IValue) -> Any:
    """Convert an interpreted value into a structure accepted by itf-py."""
    if isinstance(value, BoolValue):
        return value.value
    if isinstance(value, IntValue):
        return value.value
    if isinstance(value, StrValue):
        return value.value
    if isinstance(value, EnumValue):
        enum_value = value.value
        if isinstance(enum_value, Enum):
            return f"{type(enum_value).__name__}.{enum_value.name}"
        return str(enum_value)
    if isinstance(value, AbstractSetValue):
        materialized = value.materialize()
        if not isinstance(materialized, AbstractSetValue):
            raise TypeError(
                f"Expected materialized set, got {type(materialized).__name__}"
            )
        return frozenset(value_to_itf(elem) for elem in materialized)
    if isinstance(value, ListValue):
        return _HashableList(value_to_itf(elem) for elem in value.elements)
    if isinstance(value, TupleValue):
        return tuple(value_to_itf(elem) for elem in value.elements)
    if isinstance(value, MapValue):
        return _HashableDict(
            {
                value_to_itf(key): value_to_itf(val)
                for key, val in value.mappings.items()
            }
        )
    if isinstance(value, RecordValue):
        return _record_object(
            tuple((name, value_to_itf(val)) for name, val in value.fields)
        )
    if isinstance(value, UnionValue):
        if value.payload is None:
            return _variant_object(value.tag, {})
        return _variant_object(value.tag, {"value": value_to_itf(value.payload)})
    raise TypeError(f"Cannot convert {type(value).__name__} to ITF")


def state_view_values_to_itf(state: StateView) -> dict[str, Any]:
    """Convert all fields of a StateView into ITF-safe values."""
    values: dict[str, IValue] = {}
    values.update(state._params)  # type: ignore[attr-defined]
    values.update(state._mapping)  # type: ignore[attr-defined]
    return {name: value_to_itf(values[name]) for name in sorted(values)}


def build_itf_json(
    trace: tuple[StateView, ...],
    *,
    meta: dict[str, Any],
    params: list[str],
    vars: list[str],
    step_meta: list[dict[str, Any]] | None = None,
) -> Any:
    """Build the JSON-serializable ITF document for a StateView trace."""
    states: list[ItfState] = []
    for step_idx, state in enumerate(trace):
        meta_for_step = {"step": step_idx}
        if step_meta is not None and step_idx < len(step_meta):
            meta_for_step.update(step_meta[step_idx])
        states.append(
            ItfState(meta=meta_for_step, values=state_view_values_to_itf(state))
        )

    itf_trace = ItfTrace(
        meta=meta,
        params=params,
        vars=vars,
        loop=None,
        states=states,
    )
    return trace_to_json(itf_trace)


def itf_trace_json_line(
    trace: tuple[StateView, ...],
    *,
    meta: dict[str, Any],
    params: list[str],
    vars: list[str],
    step_meta: list[dict[str, Any]] | None = None,
) -> str:
    """Serialize a StateView trace as a single-line ITF JSON string (for NDJSON)."""
    return json.dumps(
        build_itf_json(trace, meta=meta, params=params, vars=vars, step_meta=step_meta)
    )


def write_itf_trace(
    path: str | Path,
    trace: tuple[StateView, ...],
    *,
    meta: dict[str, Any],
    params: list[str],
    vars: list[str],
    step_meta: list[dict[str, Any]] | None = None,
) -> None:
    """Write a StateView trace as ITF JSON."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            build_itf_json(
                trace, meta=meta, params=params, vars=vars, step_meta=step_meta
            ),
            indent=2,
        )
    )


def itf_to_value(obj: Any, sort: Sort) -> IValue:
    """Convert one ITF-encoded JSON value into an interpreted value.

    Sort-directed, mirroring ``wunderspec.tlc_trace._raw_to_ivalue`` but reading
    the `Informal Trace Format <https://apalache-mc.org/docs/adr/015adr-trace.html>`_
    JSON that Apalache emits. The sort disambiguates encodings that share a JSON
    shape (e.g. a variant ``{"tag", "value"}`` versus a two-field record).
    """
    # Local import keeps the module-load order free of cycles.
    from wunderspec.tla import _union_tag_names

    if isinstance(sort, BoolSort):
        if not isinstance(obj, bool):
            raise ValueError(f"Expected Bool, got {obj!r}")
        return BoolValue(obj)
    if isinstance(sort, IntSort):
        return IntValue(_itf_int(obj))
    if isinstance(sort, StrSort):
        if not isinstance(obj, str):
            raise ValueError(f"Expected Str, got {obj!r}")
        return StrValue(obj)
    if isinstance(sort, EnumSort):
        if not isinstance(obj, str):
            raise ValueError(f"Expected enum string, got {obj!r}")
        suffix = f"_OF_{sort.enum_type.__name__.upper()}"
        return EnumValue(sort.enum_type[obj.removesuffix(suffix)])
    if isinstance(sort, SetSort):
        elems = _itf_collection(obj, "#set")
        return EnumeratedSetValue(
            *(itf_to_value(elem, sort.elem_sort) for elem in elems),
            elem_sort=sort.elem_sort,
        )
    if isinstance(sort, ListSort):
        elems = _itf_collection(obj, "#tup")
        return ListValue(
            [itf_to_value(elem, sort.elem_sort) for elem in elems],
            elem_sort=sort.elem_sort,
        )
    if isinstance(sort, TupleSort):
        elems = _itf_collection(obj, "#tup")
        if len(elems) != len(sort.elem_sorts):
            raise ValueError(f"Expected {len(sort.elem_sorts)} tuple elements")
        return TupleValue(
            *(
                itf_to_value(elem, elem_sort)
                for elem, elem_sort in zip(elems, sort.elem_sorts)
            )
        )
    if isinstance(sort, MapSort):
        return MapValue(
            {
                itf_to_value(key, sort.key_sort): itf_to_value(val, sort.value_sort)
                for key, val in _itf_map_pairs(obj)
            },
            key_sort=sort.key_sort,
            value_sort=sort.value_sort,
        )
    if isinstance(sort, RecordSort):
        if not isinstance(obj, dict):
            raise ValueError(f"Expected record, got {obj!r}")
        return RecordValue(
            **{
                name: itf_to_value(val, sort[name])
                for name, val in obj.items()
                if not name.startswith("#")
            }
        )
    if isinstance(sort, UnionSort):
        if not isinstance(obj, dict) or "tag" not in obj:
            raise ValueError(f"Expected variant, got {obj!r}")
        raw_tag = obj["tag"]
        if not isinstance(raw_tag, str):
            raise ValueError(f"Expected variant tag, got {raw_tag!r}")
        reverse_tags = {safe: tag for tag, safe in _union_tag_names(sort).items()}
        tag = reverse_tags.get(raw_tag, raw_tag)
        payload_sort = sort[tag]
        if payload_sort is None:
            return UnionValue(tag)
        return UnionValue(tag, itf_to_value(obj["value"], payload_sort))
    raise TypeError(f"Unsupported sort {sort!r}")


def _itf_int(obj: Any) -> int:
    """Decode an ITF integer (``{"#bigint": "n"}`` or a plain JSON number)."""
    if isinstance(obj, dict) and "#bigint" in obj:
        return int(obj["#bigint"])
    if isinstance(obj, int) and not isinstance(obj, bool):
        return obj
    raise ValueError(f"Expected Int, got {obj!r}")


def _itf_collection(obj: Any, tag: str) -> list[Any]:
    """Decode an ITF sequence/tuple/set into a list of elements."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and tag in obj:
        return list(obj[tag])
    raise ValueError(f"Expected {tag} collection, got {obj!r}")


def _itf_map_pairs(obj: Any) -> list[tuple[Any, Any]]:
    """Decode an ITF function (``{"#map": [[k, v], ...]}``) into pairs."""
    if isinstance(obj, dict) and "#map" in obj:
        return [(key, val) for key, val in obj["#map"]]
    if isinstance(obj, list):  # a function whose domain is 1..n is a sequence
        return [(idx, val) for idx, val in enumerate(obj, start=1)]
    raise ValueError(f"Expected function, got {obj!r}")


def read_itf_trace(
    document: Mapping[str, Any],
    *,
    state_sorts: Mapping[str, Sort],
    params: Mapping[str, IValue] | None = None,
) -> tuple[StateView, ...]:
    """Parse an ITF JSON document into a trace of StateViews.

    Only fields named in *state_sorts* are decoded; ITF ``#meta`` and any extra
    keys are ignored. *params* supplies constant values for the StateViews.
    """
    param_values = params if params is not None else {}
    states = []
    for state in document.get("states", []):
        values = {
            name: itf_to_value(raw, state_sorts[name])
            for name, raw in state.items()
            if name in state_sorts
        }
        states.append(StateView(values, param_values))
    return tuple(states)
