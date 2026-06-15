"""
Concrete data structures that are produced by the AST interpreter. Since the
interpreter is not symbolic, it may easily generate large data structures. You
should avoid direct construction of IValue instances (and its descendants).
However, if you feel stuck when writing Wunderspec expressions, you can cut a
corner by writing Python code directly. You should consider rewriting it later.

Igor Konnov, 2026
"""

import itertools
import zlib
from abc import ABC, abstractmethod
from collections import namedtuple
from collections.abc import ItemsView, KeysView, Mapping, ValuesView
from enum import Enum
from functools import singledispatch
from typing import Any, Callable, Generic, Iterable, Iterator, Optional, TypeVar

from pyrsistent import pmap, pset, pvector

from wunderspec.ast.ast import Node
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
from wunderspec.expr import Expr

# ---------------------------------------------------------------------------
# Deterministic fingerprinting helpers (independent of PYTHONHASHSEED)
# ---------------------------------------------------------------------------
_FP_MASK: int = 0xFFFFFFFFFFFFFFFF
_FP_PRIME1: int = 0x9E3779B97F4A7C15  # golden-ratio derived
_FP_PRIME2: int = 0xBF58476D1CE4E5B9

# Type tags for fingerprinting
_TAG_BOOL: int = 1
_TAG_INT: int = 2
_TAG_STR: int = 3
_TAG_ENUM: int = 4
_TAG_RECORD: int = 5
_TAG_TUPLE: int = 6
_TAG_UNION: int = 7
_TAG_LIST: int = 8
_TAG_MAP: int = 9
_TAG_SET: int = 10


def _mix(a: int, b: int) -> int:
    """Deterministic 64-bit non-linear mixing function based on splitmix64.

    The structure mirrors the finaliser from the splitmix64 PRNG:

        Steele, G. L., Lea, D., and Flood, C. H. (2014).
        "Fast Splittable Pseudorandom Number Generators."
        In Proc. ACM OOPSLA 2014, pp. 453–472.
        https://doi.org/10.1145/2660193.2660195

    The two multiplicative constants (_FP_PRIME1 = 0x9e3779b97f4a7c15,
    _FP_PRIME2 = 0xbf58476d1ce4e5b9) are taken directly from that paper.
    The three XOR-shift steps ensure full avalanche: every output bit depends
    non-linearly on every input bit (a property a pure multiply-add chain lacks).

    A purely linear form ``((a * P1 + b) * P2)`` caused systematic fingerprint
    collisions: because ``fp(IntValue(k)) = C + k * P2``, integer fingerprints
    are evenly spaced, so multi-field states whose value deltas cancel in the
    linear combination produce identical hashes.  The XOR-shift steps break this
    linearity so small structured differences no longer cancel.
    """
    h = (a + b * _FP_PRIME1) & _FP_MASK
    h ^= h >> 30
    h = (h * _FP_PRIME2) & _FP_MASK
    h ^= h >> 27
    h ^= h >> 31  # final avalanche step (completes the splitmix64 finaliser)
    return h & _FP_MASK


def _str_fp(s: str) -> int:
    """Deterministic string fingerprint via zlib.crc32."""
    return zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF


class IValue(ABC):
    """A value resulting from interpretation of a specification expression."""

    __slots__ = ("_fp_cache",)

    _fp_cache: int  # lazily populated by fingerprint()

    @property
    @abstractmethod
    def sort(self) -> Sort:
        """Return the sort of this value."""
        ...

    def fingerprint(self) -> int:
        """Return a deterministic 64-bit fingerprint independent of PYTHONHASHSEED."""
        raise NotImplementedError(
            f"fingerprint not implemented for {type(self).__name__}"
        )

    def materialize(self) -> "IValue":
        """Produce a fully materialized value that can be added to a set."""
        return self

    def pretty(self, max_width: int = 80) -> str:
        """Pretty print this value, breaking nested structures across lines."""
        from wunderspec.pretty import pretty_value

        return pretty_value(self, max_width)

    def _repr_pretty_(self, p: Any, cycle: bool) -> None:
        """IPython pretty printing support."""
        if cycle:
            p.text(f"{type(self).__name__}(...)")
        else:
            p.text(self.pretty(max_width=p.max_width))

    def __rich__(self) -> Any:
        """rich rendering support (only invoked when rich is installed)."""
        from wunderspec.pretty import to_rich

        return to_rich(self.pretty())


class IValueNode(Node):
    """An AST node that stores an interpreted value"""

    def __init__(self, value: IValue):
        super().__init__(value.sort)
        self._value = value


class StateView:
    """A lightweight read-only view over a state variable mapping.

    Wraps a ``Mapping[str, IValue]`` (e.g. the ``PMap`` returned by
    ``random_traces``) so that attribute and item access return ``Expr``
    objects, exactly like a ``@state``-decorated class.  This lets you pass a
    ``StateView`` directly to invariant functions that expect a state.

    An optional *params* mapping supplies parameter values (constants such as
    ``NumActors``) that the invariant may reference.

    No data is copied — the view simply wraps the underlying mapping and
    creates ``Expr(IValueNode(v))`` on each access.
    """

    __slots__ = ("_mapping", "_params", "_cache")

    def __init__(
        self,
        mapping: Mapping[str, "IValue"],
        params: Mapping[str, "IValue"] | None = None,
    ) -> None:
        self._mapping = mapping
        self._params: Mapping[str, IValue] = params if params is not None else {}
        self._cache: dict[str, "Expr"] = {}

    # -- attribute access (s.readers) ------------------------------------

    def __getattr__(self, name: str) -> "Expr":
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        if name in self._mapping:
            result = Expr(IValueNode(self._mapping[name]))
        elif name in self._params:
            result = Expr(IValueNode(self._params[name]))
        else:
            raise AttributeError(f"StateView has no field '{name}'")
        self._cache[name] = result
        return result

    # -- dict-style access (s["readers"]) --------------------------------

    def __getitem__(self, name: str) -> "Expr":
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        if name in self._mapping:
            result = Expr(IValueNode(self._mapping[name]))
        elif name in self._params:
            result = Expr(IValueNode(self._params[name]))
        else:
            raise KeyError(name)
        self._cache[name] = result
        return result

    # -- iteration / Mapping-like helpers --------------------------------

    def keys(self) -> frozenset[str]:
        """All available field names (variables + parameters)."""
        return frozenset(self._mapping.keys()) | frozenset(self._params.keys())

    def __iter__(self) -> Iterator[str]:
        yield from self._mapping
        yield from self._params

    def __len__(self) -> int:
        return len(self._mapping) + len(self._params)

    def __contains__(self, name: object) -> bool:
        return name in self._mapping or name in self._params

    # -- conversion to Python values ------------------------------------

    def to_python(self) -> tuple:  # type: ignore[type-arg]
        """Convert every field to a native Python value (via ``to_python``)
        and return the result as a namedtuple.

        Field names are sorted alphabetically, consistent with
        ``to_python(RecordValue(...))``.
        """
        all_fields: dict[str, IValue] = dict(self._params)
        all_fields.update(self._mapping)
        field_names = tuple(sorted(all_fields))

        if field_names not in _record_class_cache:
            _record_class_cache[field_names] = namedtuple(  # type: ignore[misc]
                "Record", field_names
            )

        record_class = _record_class_cache[field_names]
        field_values = [to_python(all_fields[n]) for n in field_names]
        return record_class(*field_values)  # type: ignore[no-any-return]

    # -- repr -----------------------------------------------------------

    def __repr__(self) -> str:
        parts = [f"{k}={self._mapping[k]!r}" for k in self._mapping]
        for k in self._params:
            parts.append(f"{k}={self._params[k]!r}")
        return f"StateView({', '.join(parts)})"

    def pretty(self, max_width: int = 80) -> str:
        """Pretty print this state view, one field per line when it is wide."""
        from wunderspec.pretty import pretty_value

        return pretty_value(self, max_width)

    def _repr_pretty_(self, p: Any, cycle: bool) -> None:
        """IPython pretty printing support."""
        if cycle:
            p.text("StateView(...)")
        else:
            p.text(self.pretty(max_width=p.max_width))

    def __rich__(self) -> Any:
        """rich rendering support (only invoked when rich is installed)."""
        from wunderspec.pretty import to_rich

        return to_rich(self.pretty())


class BoolValue(IValue):
    """A boolean value resulting from interpretation."""

    __slots__ = ("_value",)

    def __init__(self, value: bool):
        self._value = value

    @property
    def sort(self) -> Sort:
        return BoolSort()

    @property
    def value(self) -> bool:
        return self._value

    def __str__(self):
        return "True" if self.value else "False"

    def __repr__(self):
        return "True" if self.value else "False"

    def __eq__(self, other):
        if type(other) is not BoolValue:
            return False
        return self._value == other._value

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            fp = _mix(_TAG_BOOL, int(self._value))
            self._fp_cache = fp
            return fp

    def __hash__(self):
        # Different constant from IntValue to avoid collisions (hash(True)==1, hash(False)==0)
        return int(self._value) ^ 0x517CC1B7


class IntValue(IValue):
    """An integer value resulting from interpretation."""

    __slots__ = ("_value",)

    def __init__(self, value: int):
        self._value = value

    @property
    def sort(self) -> Sort:
        return IntSort()

    @property
    def value(self) -> int:
        return self._value

    def __str__(self):
        return str(self.value)

    def __repr__(self):
        return str(self.value)

    def __eq__(self, other):
        if type(other) is not IntValue:
            return False
        return self._value == other._value

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            fp = _mix(_TAG_INT, self._value & _FP_MASK)
            self._fp_cache = fp
            return fp

    def __hash__(self):
        # Use a tagged hash to distinguish IntValue from raw Python ints
        # while avoiding tuple allocation.
        return self._value ^ 0x9E3779B9


class StrValue(IValue):
    """A string value resulting from interpretation."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    @property
    def sort(self) -> Sort:
        return StrSort()

    @property
    def value(self) -> str:
        return self._value

    def __str__(self):
        return self.value

    def __repr__(self):
        return repr(self.value)

    def __eq__(self, other):
        if type(other) is not StrValue:
            return False
        return self._value == other._value

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            fp = _mix(_TAG_STR, _str_fp(self._value))
            self._fp_cache = fp
            return fp

    def __hash__(self):
        return hash(self._value) ^ 0x9E3779B9


T = TypeVar("T", bound=Enum)


class EnumValue(Generic[T], IValue):
    """An enum value resulting from interpretation."""

    __slots__ = ("_value",)

    def __init__(self, value: T):
        self._value = value

    @property
    def sort(self) -> Sort:
        return EnumSort(type(self._value))

    @property
    def value(self) -> T:
        return self._value

    def __str__(self):
        return f"{type(self.value).__name__}.{self.value.name}"

    def __repr__(self):
        return f"{type(self.value).__name__}.{self.value.name}"

    def __eq__(self, other):
        if not isinstance(other, EnumValue):
            return False
        return self.value == other.value

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            fp = _mix(
                _TAG_ENUM, _str_fp(f"{type(self._value).__name__}.{self._value.name}")
            )
            self._fp_cache = fp
            return fp

    def __hash__(self):
        return hash(("EnumValue", self.value))


class RecordValue(IValue, Mapping[str, IValue]):
    """A record value resulting from interpretation.

    Records are immutable mappings from field names to values.
    Fields are stored in sorted order for canonical representation.
    """

    __slots__ = ("_fields", "_field_dict")

    def __init__(self, **fields: IValue):
        """Initialize a record value with named fields.

        Args:
            **fields: Field names mapped to their values (IValue instances).
        """
        # Store fields in sorted order for canonical representation
        self._fields = tuple(sorted(fields.items(), key=lambda x: x[0]))
        self._field_dict = dict(self._fields)

    @property
    def sort(self) -> Sort:
        field_sorts = {name: val.sort for name, val in self._fields}
        return RecordSort(**field_sorts)

    def __getitem__(self, field_name: str) -> IValue:
        """Get the value of a field by name."""
        return self._field_dict[field_name]

    def __contains__(self, field_name: object) -> bool:
        """Check if a field exists in this record."""
        return field_name in self._field_dict

    @property
    def fields(self) -> tuple[tuple[str, IValue], ...]:
        """Get all fields as a tuple of (name, value) pairs in sorted order."""
        return self._fields

    def __str__(self):
        if self._fields:
            items = ", ".join(f"{name}={value}" for name, value in self._fields)
            return f"Record({items})"
        else:
            return "Record()"

    def __repr__(self):
        if self._fields:
            items = ", ".join(f"{name}={repr(value)}" for name, value in self._fields)
            return f"Record({items})"
        else:
            return "Record()"

    def __eq__(self, other):
        if not isinstance(other, RecordValue):
            return False
        # Fields are already sorted, so we can compare tuples directly
        return self._fields == other._fields

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            fp = _TAG_RECORD
            for name, val in self._fields:
                fp = _mix(fp, _mix(_str_fp(name), val.fingerprint()))
            self._fp_cache = fp
            return fp

    def __hash__(self):
        # Hash based on sorted fields
        return hash(("RecordValue", self._fields))

    def __iter__(self) -> Iterator[str]:
        return iter(self._field_dict)

    def __len__(self) -> int:
        return len(self._fields)

    def keys(self) -> KeysView[str]:
        return self._field_dict.keys()

    def items(self) -> ItemsView[str, IValue]:
        return self._field_dict.items()

    def values(self) -> ValuesView[IValue]:
        return self._field_dict.values()


class TupleValue(IValue):
    """A tuple value resulting from interpretation.

    Tuples are immutable sequences with positional elements.
    Elements are stored as a tuple for efficiency.
    """

    __slots__ = ("_elements",)

    def __init__(self, *elements: IValue):
        """Initialize a tuple value with elements.

        Args:
            *elements: Variable number of element values (IValue instances).

        Raises:
            ValueError: If no elements provided.
        """
        if not elements:
            raise ValueError("Tuple must have at least one element")
        self._elements = elements

    @property
    def sort(self) -> Sort:
        elem_sorts = [elem.sort for elem in self._elements]
        return TupleSort(*elem_sorts)

    def __getitem__(self, index: int) -> IValue:
        """Get the value of an element by index."""
        return self._elements[index]

    def __len__(self) -> int:
        """Get the number of elements in this tuple."""
        return len(self._elements)

    def __contains__(self, index: int) -> bool:
        """Check if an index is valid in this tuple."""
        return 0 <= index < len(self._elements)

    @property
    def elements(self) -> tuple[IValue, ...]:
        """Get all elements as a tuple."""
        return self._elements

    def __str__(self):
        if self._elements:
            items = ", ".join(str(elem) for elem in self._elements)
            return f"({items})"
        else:
            return "()"

    def __repr__(self):
        if self._elements:
            items = ", ".join(repr(elem) for elem in self._elements)
            return f"({items})"
        else:
            return "()"

    def __eq__(self, other):
        if not isinstance(other, TupleValue):
            return False
        return self._elements == other._elements

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            fp = _TAG_TUPLE
            for elem in self._elements:
                fp = _mix(fp, elem.fingerprint())
            self._fp_cache = fp
            return fp

    def __hash__(self):
        return hash(("TupleValue", self._elements))


class UnionValue(IValue):
    """A union value: tag + optional payload."""

    __slots__ = ("_tag", "_payload")

    def __init__(self, tag: str, payload: IValue | None = None):
        self._tag = tag
        self._payload = payload

    @property
    def sort(self) -> Sort:
        # Note: This creates a single-variant UnionSort based on the value's tag.
        # For a complete union type, the full UnionSort should be known externally.
        payload_sort = self._payload.sort if self._payload is not None else None
        return UnionSort(**{self._tag: payload_sort})

    @property
    def tag(self) -> str:
        return self._tag

    @property
    def payload(self) -> IValue | None:
        return self._payload

    def __str__(self):
        if self._payload is not None:
            return f"{self._tag}({self._payload})"
        return f"{self._tag}"

    def __repr__(self):
        if self._payload is not None:
            return f"{self._tag}({repr(self._payload)})"
        return f"{self._tag}"

    def __eq__(self, other):
        if not isinstance(other, UnionValue):
            return False
        return self._tag == other._tag and self._payload == other._payload

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            payload_fp = self._payload.fingerprint() if self._payload is not None else 0
            fp = _mix(_TAG_UNION, _mix(_str_fp(self._tag), payload_fp))
            self._fp_cache = fp
            return fp

    def __hash__(self):
        return hash(("UnionValue", self._tag, self._payload))


class ListValue(IValue):
    """A list value resulting from interpretation.

    Lists are ordered sequences of values with the same sort.
    Uses pyrsistent's pvector for immutability.
    """

    __slots__ = ("_elements", "_elem_sort")

    def __init__(
        self,
        elements: "list | tuple[IValue, ...] | None" = None,
        elem_sort: Optional[Sort] = None,
    ):
        """Initialize a list value with elements.

        Args:
            elements: Sequence of element values (IValue instances).
            elem_sort: Optional sort for empty lists to avoid ValueError on .sort.
        """
        if elements is not None:
            self._elements = pvector(elements)
        else:
            self._elements = pvector()
        self._elem_sort = elem_sort

    @property
    def sort(self) -> Sort:
        if self._elem_sort is not None:
            return ListSort(self._elem_sort)
        if len(self._elements) == 0:
            raise ValueError("Cannot determine sort of empty ListValue")
        return ListSort(self._elements[0].sort)

    @classmethod
    def _from_pvector(cls, vec, elem_sort: Optional[Sort] = None) -> "ListValue":
        """Internal constructor from an existing pvector."""
        instance = cls.__new__(cls)
        instance._elements = vec
        instance._elem_sort = elem_sort
        return instance

    def __getitem__(self, index: int) -> IValue:
        """Get the value of an element by index."""
        return self._elements[index]  # type: ignore[no-any-return]

    def __len__(self) -> int:
        """Get the number of elements in this list."""
        return len(self._elements)

    def __contains__(self, index: int) -> bool:
        """Check if an index is valid in this list."""
        return 0 <= index < len(self._elements)

    @property
    def elements(self):
        """Get all elements as a pvector."""
        return self._elements

    def __str__(self):
        if self._elements:
            items = ", ".join(str(elem) for elem in self._elements)
            return f"[{items}]"
        else:
            return "[]"

    def __repr__(self):
        if self._elements:
            items = ", ".join(repr(elem) for elem in self._elements)
            return f"[{items}]"
        else:
            return "[]"

    def __eq__(self, other):
        if not isinstance(other, ListValue):
            return False
        if len(self._elements) != len(other._elements):
            return False
        return self._elements == other._elements

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            fp = _TAG_LIST
            for elem in self._elements:
                fp = _mix(fp, elem.fingerprint())
            self._fp_cache = fp
            return fp

    def __hash__(self):
        return hash(("ListValue", tuple(self._elements)))


class MapValue(IValue):
    """A map value resulting from interpretation.

    Maps are immutable dictionaries from keys to values.
    Uses pyrsistent's pmap for immutability.
    """

    __slots__ = ("_mappings", "_key_sort", "_value_sort")

    def __init__(
        self,
        mappings: dict[IValue, IValue] | None = None,
        key_sort: Optional[Sort] = None,
        value_sort: Optional[Sort] = None,
    ):
        """Initialize a map value with key-value mappings.

        Args:
            mappings: Dictionary mapping keys (IValue) to values (IValue).
            key_sort: The sort of map keys (required for empty maps).
            value_sort: The sort of map values (required for empty maps).
        """
        self._mappings = pmap(mappings) if mappings is not None else pmap()
        self._key_sort = key_sort
        self._value_sort = value_sort

    @classmethod
    def _from_pmap(
        cls,
        mappings_pmap,
        key_sort: Optional[Sort] = None,
        value_sort: Optional[Sort] = None,
    ) -> "MapValue":
        """Fast constructor that stores an already-built pmap directly."""
        obj = cls.__new__(cls)
        obj._mappings = mappings_pmap
        obj._key_sort = key_sort
        obj._value_sort = value_sort
        return obj

    @property
    def sort(self) -> Sort:
        if len(self._mappings) == 0:
            if self._key_sort is None or self._value_sort is None:
                raise ValueError("Cannot determine sort of empty MapValue")
            return MapSort(self._key_sort, self._value_sort)
        key, val = next(iter(self._mappings.items()))
        return MapSort(key.sort, val.sort)

    def __getitem__(self, key: IValue) -> IValue:
        """Get the value associated with a key."""
        try:
            return self._mappings[key]
        except KeyError:
            raise KeyError(f"Key {key} not found in map")

    def __contains__(self, key: IValue) -> bool:
        """Check if a key exists in this map."""
        return key in self._mappings

    def get(self, key: IValue, default: IValue | None = None) -> IValue | None:
        """Get the value for a key, or return default if not found."""
        try:
            return self[key]
        except KeyError:
            return default

    @property
    def mappings(self):
        """Get all mappings as a pmap."""
        return self._mappings

    def __str__(self):
        if self._mappings:
            items = ", ".join(
                f"{k} -> {v}"
                for k, v in sorted(self._mappings.items(), key=lambda kv: repr(kv[0]))
            )
            return f"Map({items})"
        else:
            return "Map()"

    def __repr__(self):
        if self._mappings:
            items = ", ".join(
                f"{repr(k)} -> {repr(v)}"
                for k, v in sorted(self._mappings.items(), key=lambda kv: repr(kv[0]))
            )
            return f"Map({items})"
        else:
            return "Map()"

    def __eq__(self, other):
        if not isinstance(other, MapValue):
            return False
        if len(self._mappings) != len(other._mappings):
            return False
        return self._mappings == other._mappings

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            # Sort by key fingerprint so the result is deterministic and
            # order-independent, yet still sensitive to which key maps to
            # which value (unlike a plain sum which is not).
            fp = _TAG_MAP
            for k, v in sorted(
                self._mappings.items(), key=lambda kv: kv[0].fingerprint()
            ):
                fp = _mix(fp, _mix(k.fingerprint(), v.fingerprint()))
            self._fp_cache = fp
            return fp

    def __hash__(self):
        return hash(("MapValue", self._mappings))


class AbstractSetValue(IValue):
    """An abstract set value resulting from (maybe partial) interpretation."""

    __slots__ = ("_materialized_cache",)

    def __init__(self):
        # Lazy cache for materialized form (used for hashing and cross-type equality)
        self._materialized_cache: Optional["EnumeratedSetValue"] = None

    @property
    @abstractmethod
    def sort(self) -> Sort:
        """Return the sort of this set value."""
        ...

    @abstractmethod
    def __contains__(self, element: IValue) -> bool:
        """Does this set representation contain an element."""
        pass

    @abstractmethod
    def __iter__(self) -> Iterator[IValue]:
        """Return an iterator over the elements of this set."""
        pass

    def _cardinality(self) -> "int | float | None":
        """Return the cardinality of this set.

        Returns an int for finite sets whose size is cheaply known,
        float('inf') for infinite sets, or None when the cardinality
        cannot be determined without iteration.  Subclasses that know
        their size should override this method.
        """
        return None

    def materialize(self) -> "IValue":
        """Return an enumerated version of this set value."""
        # Extract elem_sort from the set sort (SetSort -> elem_sort)
        try:
            set_sort = self.sort
            elem_sort = set_sort.elem_sort if isinstance(set_sort, SetSort) else None
        except (ValueError, AttributeError):
            elem_sort = None
        return EnumeratedSetValue(*[e.materialize() for e in self], elem_sort=elem_sort)

    def _get_materialized(self) -> "EnumeratedSetValue":
        """Get or create a cached materialized version of this set.

        Subclasses that are already materialized (like EnumeratedSetValue)
        should override this to return self.
        """
        if self._materialized_cache is None:
            materialized = self.materialize()
            assert isinstance(materialized, EnumeratedSetValue)
            self._materialized_cache = materialized
        return self._materialized_cache

    def __eq__(self, other):
        """Check equality using a three-step algorithm.

        1. Compare cardinalities — an inexpensive early-exit test.
        2. If both cardinalities are known and one set is an
           EnumeratedSetValue, iterate over it and test membership in
           the other set, avoiding materialisation of the non-enumerated
           set.
        3. Fall back to materialising both sets and comparing.

        Subclasses should first try their own specialised equality checks
        (e.g. comparing internal fields for the same type) and delegate
        to this method only for cross-type comparisons.
        """
        if not isinstance(other, AbstractSetValue):
            return False

        # Step 1: Cardinality check — cheap early exit when sizes differ.
        self_card = self._cardinality()
        other_card = other._cardinality()
        if self_card is not None and other_card is not None and self_card != other_card:
            return False

        # Step 2: When both cardinalities are known (and therefore equal after
        # step 1) and one side is already an EnumeratedSetValue, iterate over
        # its elements and test membership in the other set.  This avoids
        # materialising the non-enumerated set entirely.
        if self_card is not None and other_card is not None:
            if isinstance(self, EnumeratedSetValue):
                return all(e in other for e in self.material_set)
            if isinstance(other, EnumeratedSetValue):
                return all(e in self for e in other.material_set)

        # Step 3: Fall back to materialising both sets and comparing.
        return (
            self._get_materialized().material_set
            == other._get_materialized().material_set
        )

    def __hash__(self):
        """Hash based on the materialized set of elements."""
        return hash(self._get_materialized().material_set)


class IntervalSetValue(AbstractSetValue):
    """A set value representing an interval of integers [start, end]."""

    __slots__ = ("_start", "_end")

    def __init__(self, start: int, end: int):
        super().__init__()
        self._start = start
        self._end = end

    @property
    def sort(self) -> Sort:
        return SetSort(IntSort())

    @property
    def start(self) -> int:
        return self._start

    @property
    def end(self) -> int:
        return self._end

    def __contains__(self, element: IValue) -> bool:
        """Does this set representation contain an element."""
        if not isinstance(element, IntValue):
            return False
        return self.start <= element.value <= self.end

    def __iter__(self) -> Iterator[IValue]:
        """Return an iterator over the elements of this set."""
        for i in range(self.start, self.end + 1):
            yield IntValue(i)

    def _cardinality(self) -> "int | float | None":
        return max(0, self.end - self.start + 1)

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            # Consistent with EnumeratedSetValue fingerprint of the same elements.
            fp = _TAG_SET
            for e in sorted(self, key=lambda v: v.fingerprint()):
                fp = _mix(fp, e.fingerprint())
            self._fp_cache = fp
            return fp

    def __eq__(self, other):
        if not isinstance(other, AbstractSetValue):
            return False
        # Specialised check for the same type: compare bounds directly.
        if isinstance(other, IntervalSetValue):
            return self.start == other.start and self.end == other.end
        # Cross-type: delegate to the superclass algorithm.
        return super().__eq__(other)

    def __hash__(self):
        """Hash based on the materialized set (cached)."""
        return super().__hash__()

    def __str__(self):
        return f"{self.start}..{self.end}"

    def __repr__(self):
        return f"({self.start}..{self.end})"


class InfIntSetValue(AbstractSetValue):
    """An infinite set value representing either all integers (ℤ) or all non-negative integers (ℕ).

    This set cannot be iterated or materialized, but supports membership testing,
    equality comparison, and hashing.
    """

    __slots__ = ("_is_signed",)

    def __init__(self, is_signed: bool):
        super().__init__()
        self._is_signed = is_signed

    @property
    def sort(self) -> Sort:
        return SetSort(IntSort())

    @property
    def is_signed(self) -> bool:
        """True if this represents all integers (ℤ), False for non-negative integers (ℕ)."""
        return self._is_signed

    def __contains__(self, element: IValue) -> bool:
        """Check if an element is in this infinite set."""
        if not isinstance(element, IntValue):
            return False
        if self._is_signed:
            # All integers are in ℤ
            return True
        else:
            # Only non-negative integers are in ℕ
            return element.value >= 0

    def __iter__(self) -> Iterator[IValue]:
        """Iteration is not supported for infinite sets."""
        raise RuntimeError(
            f"Cannot iterate over infinite set {'Ints' if self._is_signed else 'UnsignedInts'}"
        )

    def materialize(self) -> "IValue":
        """Materialization is not supported for infinite sets."""
        raise RuntimeError(
            f"Cannot materialize infinite set {'Ints' if self._is_signed else 'UnsignedInts'}"
        )

    def _get_materialized(self) -> "EnumeratedSetValue":
        """Materialization is not supported for infinite sets."""
        raise RuntimeError(
            f"Cannot materialize infinite set {'Ints' if self._is_signed else 'UnsignedInts'}"
        )

    def _cardinality(self) -> "int | float | None":
        return float("inf")

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            name = "Ints" if self._is_signed else "UnsignedInts"
            fp = _mix(_TAG_SET, _str_fp(name))
            self._fp_cache = fp
            return fp

    def __eq__(self, other):
        """Check equality - only equal to another InfIntSetValue of the same kind."""
        if not isinstance(other, InfIntSetValue):
            return False
        return self._is_signed == other._is_signed

    def __hash__(self):
        """Hash based on the kind of infinite set."""
        return hash(("InfIntSetValue", self._is_signed))

    def __str__(self):
        return "Ints" if self._is_signed else "UnsignedInts"

    def __repr__(self):
        return "Ints" if self._is_signed else "UnsignedInts"


class SetFilterValue(AbstractSetValue):
    """A lazy filtered set value: { x ∈ S : P(x) }.

    This set does not materialize until needed. Iteration and membership
    testing are performed lazily by evaluating the predicate on demand.
    """

    __slots__ = ("_base_set", "_predicate_fn", "_elem_sort", "_len_cache")

    def __init__(
        self,
        base_set: AbstractSetValue,
        predicate_fn: Callable[[IValue], bool],
        elem_sort: Sort,
    ):
        super().__init__()
        self._base_set = base_set
        self._predicate_fn = predicate_fn
        self._elem_sort = elem_sort
        self._len_cache: Optional[int] = None

    @property
    def sort(self) -> Sort:
        return SetSort(self._elem_sort)

    def __contains__(self, element: IValue) -> bool:
        """Check if element is in base set and satisfies predicate."""
        if element not in self._base_set:
            return False
        return self._predicate_fn(element)

    def __iter__(self) -> Iterator[IValue]:
        """Yield elements from base set that satisfy the predicate."""
        for elem in self._base_set:
            if self._predicate_fn(elem):
                yield elem

    def __len__(self) -> int:
        """Count elements without full materialization.

        This is sound for filter because filtering cannot introduce duplicates.
        The result is cached since the data structure is immutable.
        """
        if self._len_cache is None:
            self._len_cache = sum(1 for _ in self)
        return self._len_cache

    def _cardinality(self) -> "int | float | None":
        return self._len_cache  # None when the length has not been computed yet

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            fp = _TAG_SET
            for e in sorted(self, key=lambda v: v.fingerprint()):
                fp = _mix(fp, e.fingerprint())
            self._fp_cache = fp
            return fp

    def __str__(self):
        return f"filter({self._base_set})"

    def __repr__(self):
        return f"SetFilterValue({self._base_set!r})"


class SetMapValue(AbstractSetValue):
    """A lazy mapped set value: { f(x) : x ∈ S }.

    This set does not materialize until needed. Iteration is performed
    lazily by evaluating the mapper on demand.
    """

    __slots__ = ("_base_set", "_mapper_fn", "_result_sort")

    def __init__(
        self,
        base_set: AbstractSetValue,
        mapper_fn: Callable[[IValue], IValue],
        result_sort: Sort,
    ):
        super().__init__()
        self._base_set = base_set
        self._mapper_fn = mapper_fn
        self._result_sort = result_sort

    @property
    def sort(self) -> Sort:
        return SetSort(self._result_sort)

    def __contains__(self, element: IValue) -> bool:
        """Check if any element in base set maps to this element.

        Note: This is O(n) as we must check all mappings.
        """
        for base_elem in self._base_set:
            if self._mapper_fn(base_elem) == element:
                return True
        return False

    def __iter__(self) -> Iterator[IValue]:
        """Yield mapped values for each element in base set."""
        for elem in self._base_set:
            yield self._mapper_fn(elem)

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            # Materialize first so that duplicate mapped values are deduplicated
            # (set semantics: {f(x) : x ∈ S} is a set, not a multiset).
            # Then delegate to EnumeratedSetValue.fingerprint for consistency.
            fp = self.materialize().fingerprint()
            self._fp_cache = fp
            return fp

    def __str__(self):
        return f"map({self._base_set})"

    def __repr__(self):
        return f"SetMapValue({self._base_set!r})"


class EnumeratedSetValue(AbstractSetValue):
    """A set value resulting from complete interpretation (using pyrsistent pset)."""

    __slots__ = ("_material_set", "_elem_sort", "_sorted_elements")

    def __init__(self, *elements: IValue, elem_sort: Optional[Sort] = None):
        super().__init__()
        # Keep public API unchanged; delegate to iterable-based constructor logic.
        self._material_set = self._materialize_values(elements)
        self._elem_sort = elem_sort
        self._sorted_elements: tuple[IValue, ...] | None = None

    @staticmethod
    def _materialize_values(elements: Iterable[IValue]):
        """Materialize iterable of IValue into a persistent set."""
        return pset(elem.materialize() for elem in elements)

    @classmethod
    def _from_value_iterable(
        cls,
        elements: Iterable[IValue],
        elem_sort: Optional[Sort] = None,
    ) -> "EnumeratedSetValue":
        """Internal fast constructor from iterable of interpreted values."""
        instance = cls.__new__(cls)
        AbstractSetValue.__init__(instance)
        instance._material_set = cls._materialize_values(elements)
        instance._elem_sort = elem_sort
        instance._sorted_elements = None
        return instance

    @property
    def sort(self) -> Sort:
        es = self._elem_sort
        if es is not None:
            return SetSort(es)
        if len(self._material_set) == 0:
            raise ValueError("Cannot determine sort of empty EnumeratedSetValue")
        first_elem = next(iter(self._material_set))
        es = first_elem.sort
        self._elem_sort = es  # cache for future calls
        return SetSort(es)

    @classmethod
    def _from_material_set(
        cls, material_set, elem_sort: Optional[Sort] = None
    ) -> "EnumeratedSetValue":
        """Internal constructor from an existing pset. Not intended for public use."""
        instance = cls.__new__(cls)
        AbstractSetValue.__init__(instance)
        instance._material_set = material_set
        instance._elem_sort = elem_sort
        instance._sorted_elements = None
        return instance

    @property
    def material_set(self):
        return self._material_set

    def materialize(self) -> "IValue":
        """Return an enumerated version of this set value."""
        return self

    def _get_materialized(self) -> "EnumeratedSetValue":
        """Already materialized, return self."""
        return self

    def _cardinality(self) -> "int | float | None":
        return len(self.material_set)

    def __eq__(self, other):
        if not isinstance(other, AbstractSetValue):
            return False
        # Specialised check for the same type: compare psets directly.
        if isinstance(other, EnumeratedSetValue):
            return self.material_set == other.material_set
        # Cross-type: delegate to the superclass algorithm.
        return super().__eq__(other)

    def __hash__(self):
        """Hash based on material_set."""
        return hash(self.material_set)

    def __contains__(self, element: IValue) -> bool:
        """Does this set representation contain an element."""
        return element in self.material_set

    def fingerprint(self) -> int:
        try:
            return self._fp_cache
        except AttributeError:
            # Sort by element fingerprint so the result is deterministic and
            # order-independent, yet avoids the additive collision risk of a
            # plain sum (e.g. fp(A)+fp(B) == fp(C)+fp(D) for different elems).
            fp = _TAG_SET
            for e in self:  # __iter__ already yields elements sorted by fingerprint
                fp = _mix(fp, e.fingerprint())
            self._fp_cache = fp
            return fp

    def __iter__(self) -> Iterator[IValue]:
        """Return an iterator over the elements of this set in deterministic order."""
        sorted_elements = self._sorted_elements
        if sorted_elements is None:
            self._cache_sorted_elements()
            sorted_elements = self._sorted_elements
            assert sorted_elements is not None
        return iter(sorted_elements)

    def element_at(self, index: int) -> IValue:
        """Return element at deterministic position."""
        sorted_elements = self._sorted_elements
        if sorted_elements is None:
            self._cache_sorted_elements()
            sorted_elements = self._sorted_elements
            assert sorted_elements is not None
        return sorted_elements[index]

    def _cache_sorted_elements(self) -> None:
        """Populate deterministic element cache on first use."""
        self._sorted_elements = tuple(
            sorted(self._material_set, key=lambda v: v.fingerprint())
        )

    def __str__(self):
        if not self.material_set:
            return "Set()"
        return "Set({{{}}})".format(
            ", ".join(str(e) for e in sorted(self.material_set, key=str))
        )

    def __repr__(self):
        if not self.material_set:
            return "Set()"
        return "Set({{{}}})".format(
            ", ".join(repr(e) for e in sorted(self.material_set, key=str))
        )


# =============================================================================
# All-set value classes (lazy representations of combinatorial sets)
# =============================================================================

MATERIALIZATION_BOUND: int = 10_000
"""Maximum number of elements to materialize from an All-set value."""


class AllSubsetsValue(AbstractSetValue):
    """Lazy representation of the power set of a finite base set."""

    __slots__ = ("_base_elements", "_elem_sort")

    def __init__(
        self, base_elements: tuple[IValue, ...], elem_sort: Optional[Sort] = None
    ):
        if not base_elements and elem_sort is None:
            raise ValueError("Must provide elem_sort for AllSubsets of empty set")
        super().__init__()
        self._base_elements = base_elements
        self._elem_sort = elem_sort

    @property
    def sort(self) -> Sort:
        return SetSort(SetSort(self._inner_elem_sort()))

    def _size(self) -> int:
        """Return the number of subsets (2^n)."""
        return int(2 ** len(self._base_elements))

    def __contains__(self, element: IValue) -> bool:
        """Check if element is a subset of the base set."""
        if not isinstance(element, AbstractSetValue):
            return False
        base_set = set(self._base_elements)
        for elem in element:
            if elem not in base_set:
                return False
        return True

    def _inner_elem_sort(self) -> Sort:
        """Return the element sort of the base set, if available."""
        if self._base_elements:
            return self._base_elements[0].sort
        if self._elem_sort is not None:
            return self._elem_sort
        raise ValueError("Cannot determine element sort of AllSubsetsValue")

    def __iter__(self) -> Iterator[IValue]:
        """Iterate over all subsets using bitmask enumeration."""
        size = self._size()
        n = len(self._base_elements)
        inner_sort = self._inner_elem_sort()
        for mask in range(size):
            yield EnumeratedSetValue(
                *(self._base_elements[i] for i in range(n) if (mask >> i) & 1),
                elem_sort=inner_sort,
            )

    def materialize(self) -> "IValue":
        """Materialize to EnumeratedSetValue if within bound."""
        size = self._size()
        if size > MATERIALIZATION_BOUND:
            raise RuntimeError(
                f"AllSubsets materialization exceeds bound: {size} > {MATERIALIZATION_BOUND}"
            )
        return EnumeratedSetValue(*list(self))

    def _cardinality(self) -> "int | float | None":
        return self._size()

    def __eq__(self, other):
        if isinstance(other, AllSubsetsValue):
            return set(self._base_elements) == set(other._base_elements)
        return super().__eq__(other)

    def __hash__(self):
        return hash(("AllSubsetsValue", frozenset(self._base_elements)))

    def fingerprint(self) -> int:
        raise NotImplementedError(
            "fingerprint not implemented for AllSubsetsValue; "
            "consistent fingerprinting with the materialized form is not yet resolved"
        )

    def __str__(self):
        return f"AllSubsets({{{', '.join(str(e) for e in self._base_elements)}}})"

    def __repr__(self):
        return f"AllSubsets({{{', '.join(repr(e) for e in self._base_elements)}}})"


class AllMapsValue(AbstractSetValue):
    """Lazy representation of all maps from a key set to a value set."""

    __slots__ = ("_keys", "_values")

    def __init__(self, keys: tuple[IValue, ...], values: tuple[IValue, ...]):
        super().__init__()
        self._keys = keys
        self._values = values

    @property
    def sort(self) -> Sort:
        if not self._keys or not self._values:
            raise ValueError(
                "Cannot determine sort of AllMaps with empty key or value set"
            )
        return SetSort(MapSort(self._keys[0].sort, self._values[0].sort))

    def _size(self) -> int:
        """Return the number of maps (|values|^|keys|)."""
        if not self._keys:
            return 1  # Empty domain -> one map (the empty map)
        return int(len(self._values) ** len(self._keys))

    def __contains__(self, element: IValue) -> bool:
        """Check if element is a valid map from keys to values."""
        if not isinstance(element, MapValue):
            return False
        key_set = set(self._keys)
        value_set = set(self._values)
        # Check map has exactly the right keys
        map_keys = set(element.mappings.keys())
        if map_keys != key_set:
            return False
        # Check all values are in the value set
        for v in element.mappings.values():
            if v not in value_set:
                return False
        return True

    def __iter__(self) -> Iterator[IValue]:
        """Iterate over all maps using Cartesian product."""
        if not self._keys:
            yield MapValue({})
            return
        for combo in itertools.product(self._values, repeat=len(self._keys)):
            yield MapValue(dict(zip(self._keys, combo)))

    def materialize(self) -> "IValue":
        """Materialize to EnumeratedSetValue if within bound."""
        size = self._size()
        if size > MATERIALIZATION_BOUND:
            raise RuntimeError(
                f"AllMaps materialization exceeds bound: {size} > {MATERIALIZATION_BOUND}"
            )
        return EnumeratedSetValue(*list(self))

    def _cardinality(self) -> "int | float | None":
        return self._size()

    def __eq__(self, other):
        if isinstance(other, AllMapsValue):
            return set(self._keys) == set(other._keys) and set(self._values) == set(
                other._values
            )
        return super().__eq__(other)

    def __hash__(self):
        return hash(("AllMapsValue", frozenset(self._keys), frozenset(self._values)))

    def fingerprint(self) -> int:
        raise NotImplementedError(
            "fingerprint not implemented for AllMapsValue; "
            "consistent fingerprinting with the materialized form is not yet resolved"
        )

    def __str__(self):
        keys_str = ", ".join(str(k) for k in self._keys)
        vals_str = ", ".join(str(v) for v in self._values)
        return f"AllMaps({{{keys_str}}}, {{{vals_str}}})"

    def __repr__(self):
        keys_str = ", ".join(repr(k) for k in self._keys)
        vals_str = ", ".join(repr(v) for v in self._values)
        return f"AllMaps({{{keys_str}}}, {{{vals_str}}})"


class AllTuplesValue(AbstractSetValue):
    """Lazy representation of the Cartesian product of sets (all tuples)."""

    __slots__ = ("_dimension_elements",)

    def __init__(self, dimension_elements: tuple[tuple[IValue, ...], ...]):
        super().__init__()
        self._dimension_elements = dimension_elements

    @property
    def sort(self) -> Sort:
        if not self._dimension_elements:
            raise ValueError("Cannot determine sort of AllTuples with no dimensions")
        elem_sorts = []
        for dim in self._dimension_elements:
            if not dim:
                raise ValueError("Cannot determine sort with empty dimension")
            elem_sorts.append(dim[0].sort)
        return SetSort(TupleSort(*elem_sorts))

    def _size(self) -> int:
        """Return the number of tuples (product of dimension sizes)."""
        result = 1
        for dim in self._dimension_elements:
            result *= len(dim)
        return result

    def __contains__(self, element: IValue) -> bool:
        """Check if element is a valid tuple from the Cartesian product."""
        if not isinstance(element, TupleValue):
            return False
        if len(element.elements) != len(self._dimension_elements):
            return False
        for i, elem in enumerate(element.elements):
            if elem not in self._dimension_elements[i]:
                return False
        return True

    def __iter__(self) -> Iterator[IValue]:
        """Iterate using diagonal (anti-diagonal) ordering by sum of indices."""
        if not self._dimension_elements:
            return
        # Use itertools.product for simplicity (still correct, just not diagonal)
        for combo in itertools.product(*self._dimension_elements):
            yield TupleValue(*combo)

    def materialize(self) -> "IValue":
        """Materialize to EnumeratedSetValue if within bound."""
        size = self._size()
        if size > MATERIALIZATION_BOUND:
            raise RuntimeError(
                f"AllTuples materialization exceeds bound: {size} > {MATERIALIZATION_BOUND}"
            )
        return EnumeratedSetValue(*list(self))

    def _cardinality(self) -> "int | float | None":
        return self._size()

    def __eq__(self, other):
        if isinstance(other, AllTuplesValue):
            if len(self._dimension_elements) != len(other._dimension_elements):
                return False
            for d1, d2 in zip(self._dimension_elements, other._dimension_elements):
                if set(d1) != set(d2):
                    return False
            return True
        return super().__eq__(other)

    def __hash__(self):
        return hash(
            ("AllTuplesValue", tuple(frozenset(d) for d in self._dimension_elements))
        )

    def fingerprint(self) -> int:
        raise NotImplementedError(
            "fingerprint not implemented for AllTuplesValue; "
            "consistent fingerprinting with the materialized form is not yet resolved"
        )

    def __str__(self):
        dims = ", ".join(
            f"{{{', '.join(str(e) for e in dim)}}}" for dim in self._dimension_elements
        )
        return f"AllTuples({dims})"

    def __repr__(self):
        dims = ", ".join(
            f"{{{', '.join(repr(e) for e in dim)}}}" for dim in self._dimension_elements
        )
        return f"AllTuples({dims})"


class AllRecordsValue(AbstractSetValue):
    """Lazy representation of all records with fields drawn from given sets."""

    __slots__ = ("_field_names", "_field_elements")

    def __init__(
        self,
        field_names: tuple[str, ...],
        field_elements: tuple[tuple[IValue, ...], ...],
    ):
        super().__init__()
        self._field_names = field_names
        self._field_elements = field_elements

    @property
    def sort(self) -> Sort:
        if not self._field_names:
            raise ValueError("Cannot determine sort of AllRecords with no fields")
        field_sorts = {}
        for i, name in enumerate(self._field_names):
            if not self._field_elements[i]:
                raise ValueError(f"Cannot determine sort with empty field {name}")
            field_sorts[name] = self._field_elements[i][0].sort
        return SetSort(RecordSort(**field_sorts))

    def _size(self) -> int:
        """Return the number of records (product of field set sizes)."""
        result = 1
        for elems in self._field_elements:
            result *= len(elems)
        return result

    def __contains__(self, element: IValue) -> bool:
        """Check if element is a valid record from the product."""
        if not isinstance(element, RecordValue):
            return False
        if set(element.keys()) != set(self._field_names):
            return False
        for i, name in enumerate(self._field_names):
            if element[name] not in self._field_elements[i]:
                return False
        return True

    def __iter__(self) -> Iterator[IValue]:
        """Iterate over all records."""
        if not self._field_names:
            return
        for combo in itertools.product(*self._field_elements):
            fields = {
                self._field_names[i]: combo[i] for i in range(len(self._field_names))
            }
            yield RecordValue(**fields)

    def materialize(self) -> "IValue":
        """Materialize to EnumeratedSetValue if within bound."""
        size = self._size()
        if size > MATERIALIZATION_BOUND:
            raise RuntimeError(
                f"AllRecords materialization exceeds bound: {size} > {MATERIALIZATION_BOUND}"
            )
        return EnumeratedSetValue(*list(self))

    def _cardinality(self) -> "int | float | None":
        return self._size()

    def __eq__(self, other):
        if isinstance(other, AllRecordsValue):
            if self._field_names != other._field_names:
                return False
            for e1, e2 in zip(self._field_elements, other._field_elements):
                if set(e1) != set(e2):
                    return False
            return True
        return super().__eq__(other)

    def __hash__(self):
        return hash(
            (
                "AllRecordsValue",
                self._field_names,
                tuple(frozenset(e) for e in self._field_elements),
            )
        )

    def fingerprint(self) -> int:
        raise NotImplementedError(
            "fingerprint not implemented for AllRecordsValue; "
            "consistent fingerprinting with the materialized form is not yet resolved"
        )

    def __str__(self):
        fields = ", ".join(
            f"{name}={{{', '.join(str(e) for e in self._field_elements[i])}}}"
            for i, name in enumerate(self._field_names)
        )
        return f"AllRecords({fields})"

    def __repr__(self):
        fields = ", ".join(
            f"{name}={{{', '.join(repr(e) for e in self._field_elements[i])}}}"
            for i, name in enumerate(self._field_names)
        )
        return f"AllRecords({fields})"


# =============================================================================
# to_python: Convert IValue to Python values
# =============================================================================

# Cache for dynamically created namedtuple classes (for records)
_record_class_cache: dict[tuple[str, ...], type] = {}

# Named tuple for union values
_UnionPython = namedtuple("_UnionPython", ["tag", "payload"])


@singledispatch
def _to_python_impl(v: IValue) -> Any:
    """Internal singledispatch implementation for to_python().

    Do not call directly - use to_python() instead.
    """
    raise NotImplementedError(f"to_python not implemented for {type(v).__name__}")


# Dict-based cache for dispatch lookup (avoids lru_cache key generation overhead)
_to_python_dispatch_cache: dict[type, Callable[[IValue], Any]] = {}


def _to_python_dispatch(tp: type) -> Callable[[IValue], Any]:
    """Cached dispatch lookup for to_python()."""
    try:
        return _to_python_dispatch_cache[tp]
    except KeyError:
        handler = _to_python_impl.dispatch(tp)
        _to_python_dispatch_cache[tp] = handler  # type: ignore[assignment]
        return handler  # type: ignore[return-value]


def _to_python_dispatch_clear_cache() -> None:
    """Clear the dispatch cache (needed after registering new handlers)."""
    _to_python_dispatch_cache.clear()


def to_python(v: IValue) -> Any:
    """Convert an IValue to a native Python value.

    Conversions:
        - BoolValue → bool
        - IntValue → int
        - StrValue → str
        - EnumValue → the Python Enum value
        - AbstractSetValue → frozenset (materialized first)
        - ListValue → tuple (elements converted recursively)
        - TupleValue → tuple (elements converted recursively)
        - MapValue → MappingProxyType (keys/values converted recursively)
        - RecordValue → namedtuple (fields converted recursively)
        - UnionValue → namedtuple("Union", ["tag", "payload"])

    This function uses singledispatch for extensibility. Users can register
    custom handlers:

        ```python
        @to_python.register(MyCustomValue)
        def _(v: MyCustomValue) -> Any:
            ...
        ```

    Note: After registering a new handler at runtime, call to_python.clear_cache()
    to ensure the new handler is used.

    Args:
        v: The IValue to convert

    Returns:
        The corresponding Python value
    """
    return _to_python_dispatch(type(v))(v)  # type: ignore[arg-type]


# Expose registration interface for extensibility
to_python.register = _to_python_impl.register  # type: ignore[attr-defined]
to_python.dispatch = _to_python_impl.dispatch  # type: ignore[attr-defined]
to_python.registry = _to_python_impl.registry  # type: ignore[attr-defined]
to_python.clear_cache = _to_python_dispatch_clear_cache  # type: ignore[attr-defined]


@_to_python_impl.register(BoolValue)
def _to_python_bool(v: BoolValue) -> bool:
    """Convert BoolValue to bool."""
    return v.value


@_to_python_impl.register(IntValue)
def _to_python_int(v: IntValue) -> int:
    """Convert IntValue to int."""
    return v.value


@_to_python_impl.register(StrValue)
def _to_python_str(v: StrValue) -> str:
    """Convert StrValue to str."""
    return v.value


@_to_python_impl.register(EnumValue)
def _to_python_enum(v: EnumValue) -> Enum:  # type: ignore[type-arg]
    """Convert EnumValue to its Python Enum value."""
    return v.value  # type: ignore[no-any-return]


@_to_python_impl.register(AbstractSetValue)
def _to_python_set(v: AbstractSetValue) -> frozenset:  # type: ignore[type-arg]
    """Convert AbstractSetValue to frozenset (materializes first)."""
    materialized = v.materialize()
    assert isinstance(materialized, EnumeratedSetValue)
    return frozenset(to_python(elem) for elem in materialized)


@_to_python_impl.register(ListValue)
def _to_python_list(v: ListValue) -> tuple:  # type: ignore[type-arg]
    """Convert ListValue to tuple (preserves order)."""
    return tuple(to_python(elem) for elem in v.elements)


@_to_python_impl.register(TupleValue)
def _to_python_tuple(v: TupleValue) -> tuple:  # type: ignore[type-arg]
    """Convert TupleValue to tuple."""
    return tuple(to_python(elem) for elem in v.elements)


@_to_python_impl.register(MapValue)
def _to_python_map(v: MapValue):  # type: ignore[type-arg]
    """Convert MapValue to pmap (immutable, hashable mapping)."""
    py_dict = {to_python(k): to_python(val) for k, val in v.mappings.items()}
    return pmap(py_dict)


@_to_python_impl.register(RecordValue)
def _to_python_record(v: RecordValue) -> tuple:  # type: ignore[type-arg]
    """Convert RecordValue to a namedtuple."""
    field_names = tuple(name for name, _ in v.fields)

    # Get or create the namedtuple class for this record structure
    if field_names not in _record_class_cache:
        _record_class_cache[field_names] = namedtuple("Record", field_names)

    record_class = _record_class_cache[field_names]
    field_values = [to_python(val) for _, val in v.fields]
    return record_class(*field_values)  # type: ignore[no-any-return]


@_to_python_impl.register(UnionValue)
def _to_python_union(v: UnionValue) -> tuple:  # type: ignore[type-arg]
    """Convert UnionValue to a namedtuple with tag and payload."""
    payload = to_python(v.payload) if v.payload is not None else None
    return _UnionPython(v.tag, payload)


@_to_python_impl.register(AllSubsetsValue)
def _to_python_all_subsets(v: AllSubsetsValue) -> frozenset:  # type: ignore[type-arg]
    """Convert AllSubsetsValue to frozenset (materializes first)."""
    try:
        return frozenset(to_python(elem) for elem in v)
    except RuntimeError as e:
        raise RuntimeError(f"Cannot convert AllSubsets to Python: {e}") from e


@_to_python_impl.register(AllMapsValue)
def _to_python_all_maps(v: AllMapsValue) -> frozenset:  # type: ignore[type-arg]
    """Convert AllMapsValue to frozenset (materializes first)."""
    try:
        return frozenset(to_python(elem) for elem in v)
    except RuntimeError as e:
        raise RuntimeError(f"Cannot convert AllMaps to Python: {e}") from e


@_to_python_impl.register(AllTuplesValue)
def _to_python_all_tuples(v: AllTuplesValue) -> frozenset:  # type: ignore[type-arg]
    """Convert AllTuplesValue to frozenset (materializes first)."""
    try:
        return frozenset(to_python(elem) for elem in v)
    except RuntimeError as e:
        raise RuntimeError(f"Cannot convert AllTuples to Python: {e}") from e


@_to_python_impl.register(AllRecordsValue)
def _to_python_all_records(v: AllRecordsValue) -> frozenset:  # type: ignore[type-arg]
    """Convert AllRecordsValue to frozenset (materializes first)."""
    try:
        return frozenset(to_python(elem) for elem in v)
    except RuntimeError as e:
        raise RuntimeError(f"Cannot convert AllRecords to Python: {e}") from e


@_to_python_impl.register(StateView)
def _to_python_state_view(v: StateView) -> tuple:  # type: ignore[misc, type-arg]
    """Convert StateView to a namedtuple (delegates to StateView.to_python)."""
    return v.to_python()


# =========================================================================
# from_python: Python value → IValue
# =========================================================================


def from_python(v: Any) -> IValue:
    """Convert a plain Python value to an ``IValue``.

    This is the inverse of ``to_python`` and is useful when ingesting
    data produced by external tools (e.g. ``itf_py.value_from_json``).

    Supported conversions (applied in order):
        - ``bool``         → ``BoolValue``     (checked before ``int``)
        - ``int``          → ``IntValue``
        - ``str``          → ``StrValue``
        - ``Enum``         → ``EnumValue``
        - ``frozenset``    → ``EnumeratedSetValue``
        - ITF variant      → ``UnionValue``     (namedtuple with ``_itf_variant``)
        - ``tuple``        → ``TupleValue``     (plain tuples only)
        - namedtuple       → ``RecordValue``    (tuples with ``_fields``)
        - ``list``         → ``ListValue``
        - ``Mapping``      → ``MapValue``       (dict, frozendict, …)

    All conversions are recursive: container elements are converted via
    ``from_python`` as well.

    Args:
        v: A plain Python value.

    Returns:
        The corresponding ``IValue``.

    Raises:
        TypeError: If *v* has an unsupported type.
    """
    # bool must be checked before int (bool is a subclass of int).
    if isinstance(v, bool):
        return BoolValue(v)
    if isinstance(v, int):
        return IntValue(v)
    if isinstance(v, str):
        return StrValue(v)
    if isinstance(v, Enum):
        return EnumValue(v)
    if isinstance(v, frozenset):
        elements = [from_python(e) for e in v]
        elem_sort = elements[0].sort if elements else None
        return EnumeratedSetValue(*elements, elem_sort=elem_sort)
    # ITF variant (Apalache tagged union): namedtuple with _itf_variant.
    # When the value is a record, fields are spread into the namedtuple.
    # When the value is a scalar, there is a single 'value' field.
    if isinstance(v, tuple) and hasattr(type(v), "_itf_variant"):
        tag = type(v).__name__
        fields = v._fields  # type: ignore[attr-defined]
        if fields == ("value",):
            # Scalar payload: the single "value" field is the payload.
            payload = from_python(v.value)  # type: ignore[attr-defined]
        else:
            # Record payload: all fields form the record.
            field_dict = {name: from_python(getattr(v, name)) for name in fields}
            payload = RecordValue(**field_dict)
        return UnionValue(tag, payload)
    # namedtuple: has _fields (check before plain tuple)
    if isinstance(v, tuple) and hasattr(v, "_fields"):
        fields = {
            name: from_python(getattr(v, name)) for name in v._fields  # type: ignore[union-attr]
        }
        return RecordValue(**fields)
    if isinstance(v, tuple):
        if not v:
            raise ValueError("Cannot convert empty tuple to TupleValue")
        return TupleValue(*(from_python(e) for e in v))
    if isinstance(v, list):
        elements = [from_python(e) for e in v]
        elem_sort = elements[0].sort if elements else None
        return ListValue(elements, elem_sort=elem_sort)
    # Mapping covers dict, frozendict, ImmutableDict, etc.
    if isinstance(v, Mapping):
        mappings = {from_python(k): from_python(val) for k, val in v.items()}
        if mappings:
            first_k, first_v = next(iter(mappings.items()))
            return MapValue(mappings, key_sort=first_k.sort, value_sort=first_v.sort)
        return MapValue(mappings)
    raise TypeError(f"Cannot convert {type(v).__name__} to IValue: {v!r}")


def from_python_with_sort(v: Any, sort: Sort) -> IValue:
    """Convert a Python value to an ``IValue`` using an expected Wunderspec sort.

    ITF represents maps and records with the same JSON/Python shape. Untyped
    ``from_python`` must therefore decode mappings as ``MapValue``. Schedule
    replay can use this helper when the sampled set element sort is known.
    """
    if isinstance(sort, BoolSort):
        if not isinstance(v, bool):
            raise TypeError(f"Expected Bool value, got {v!r}")
        return BoolValue(v)
    if isinstance(sort, IntSort):
        if not isinstance(v, int) or isinstance(v, bool):
            raise TypeError(f"Expected Int value, got {v!r}")
        return IntValue(v)
    if isinstance(sort, StrSort):
        if not isinstance(v, str):
            raise TypeError(f"Expected Str value, got {v!r}")
        return StrValue(v)
    if isinstance(sort, EnumSort):
        if isinstance(v, sort.enum_type):
            return EnumValue(v)
        if isinstance(v, str):
            try:
                member_name = v.removeprefix(f"{sort.enum_type.__name__}.")
                return EnumValue(sort.enum_type[member_name])
            except KeyError as e:
                raise TypeError(f"Expected {sort.enum_type.__name__}, got {v!r}") from e
        raise TypeError(f"Expected {sort.enum_type.__name__}, got {v!r}")
    if isinstance(sort, SetSort):
        if not isinstance(v, frozenset):
            raise TypeError(f"Expected set value, got {v!r}")
        return EnumeratedSetValue(
            *(from_python_with_sort(elem, sort.elem_sort) for elem in v),
            elem_sort=sort.elem_sort,
        )
    if isinstance(sort, ListSort):
        if not isinstance(v, list):
            raise TypeError(f"Expected list value, got {v!r}")
        return ListValue(
            [from_python_with_sort(elem, sort.elem_sort) for elem in v],
            elem_sort=sort.elem_sort,
        )
    if isinstance(sort, TupleSort):
        if not isinstance(v, tuple) or hasattr(v, "_fields"):
            raise TypeError(f"Expected tuple value, got {v!r}")
        if len(v) != len(sort.elem_sorts):
            raise TypeError(
                f"Expected {len(sort.elem_sorts)} tuple elements, got {v!r}"
            )
        return TupleValue(
            *(
                from_python_with_sort(elem, elem_sort)
                for elem, elem_sort in zip(v, sort.elem_sorts)
            )
        )
    if isinstance(sort, MapSort):
        if not isinstance(v, Mapping):
            raise TypeError(f"Expected map value, got {v!r}")
        return MapValue(
            {
                from_python_with_sort(key, sort.key_sort): from_python_with_sort(
                    val, sort.value_sort
                )
                for key, val in v.items()
            },
            key_sort=sort.key_sort,
            value_sort=sort.value_sort,
        )
    if isinstance(sort, RecordSort):
        if isinstance(v, Mapping):
            field_source = v
        elif isinstance(v, tuple) and hasattr(v, "_fields"):
            field_source = {
                name: getattr(v, name) for name in v._fields  # type: ignore[union-attr]
            }
        else:
            raise TypeError(f"Expected record value, got {v!r}")
        expected_fields = {name for name, _ in sort.fields}
        actual_fields = set(field_source)
        if actual_fields != expected_fields:
            raise TypeError(
                f"Expected record fields {sorted(expected_fields)}, "
                f"got {sorted(actual_fields)}"
            )
        return RecordValue(
            **{
                name: from_python_with_sort(field_source[name], field_sort)
                for name, field_sort in sort.fields
            }
        )
    if isinstance(sort, UnionSort):
        if not (isinstance(v, tuple) and hasattr(type(v), "_itf_variant")):
            raise TypeError(f"Expected union variant, got {v!r}")
        tag = type(v).__name__
        if tag not in sort:
            raise TypeError(f"Unexpected union variant {tag!r} for {sort!r}")
        payload_sort = sort[tag]
        if payload_sort is None:
            return UnionValue(tag)
        fields = v._fields  # type: ignore[attr-defined]
        if fields == ("value",):
            payload_value = v.value  # type: ignore[attr-defined]
        else:
            payload_value = {name: getattr(v, name) for name in fields}
        return UnionValue(tag, from_python_with_sort(payload_value, payload_sort))
    return from_python(v)
