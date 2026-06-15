"""
Sort classes for Wunderspec AST.

Sorts represent the types of expressions (Int, Bool, Set, Map, Enum).
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from functools import cache
from typing import Type, get_args, get_origin


class Sort:
    """Base class for all sorts (types)."""

    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"{type(self).__name__}()"

    def __eq__(self, other):
        """Compare sorts by type and name."""
        if not isinstance(other, Sort):
            return False
        return type(self) is type(other) and self.name == other.name

    def __hash__(self):
        """Make Sort hashable."""
        return hash((type(self).__name__, self.name))


class IntSort(Sort):
    """Integer sort (singleton)."""

    _instance = None
    _initialized: bool

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            super().__init__("Int")
            self._initialized = True


class BoolSort(Sort):
    """Boolean sort (singleton)."""

    _instance = None
    _initialized: bool

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            super().__init__("Bool")
            self._initialized = True


class ActionSort(Sort):
    """
    Action sort (singleton). We treat actions as a separate sort from Bool,
    to clearly distinguish between state-level expressions (Bool sort) and
    action-level expressions (Action sort).
    """

    _instance = None
    _initialized: bool

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            super().__init__("Action")
            self._initialized = True


class TemporalSort(Sort):
    """
    Temporal sort (singleton). While it is tempting to say that temporal
    formulas are just Boolean formulas, temporal formulas require a path
    (sequence of states) to be evaluated. Thus, we treat them as a separate
    sort in Wunderspec. This is similar to how the model checking theory
    defines temporal logic on top of atomic propositions, which can also
    have rich expressions underneath.
    """

    _instance = None
    _initialized: bool

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            super().__init__("Temporal")
            self._initialized = True


class StrSort(Sort):
    """String sort (singleton)."""

    _instance = None
    _initialized: bool

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            super().__init__("Str")
            self._initialized = True


class EnumSort(Sort):
    """Enumeration sort for user-defined enum types.

    Each EnumSort instance is tied to a specific Python Enum class.
    Two EnumSort instances are equal iff they wrap the same Enum class.

    Uses a per-class singleton cache to ensure EnumSort(MyEnum) always
    returns the same instance.
    """

    _instances: dict[Type[Enum], EnumSort] = {}

    def __new__(cls, enum_type: Type[Enum]):
        if enum_type in cls._instances:
            return cls._instances[enum_type]
        instance = super().__new__(cls)
        cls._instances[enum_type] = instance
        return instance

    def __init__(self, enum_type: Type[Enum]):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self.enum_type = enum_type
        super().__init__(f"EnumSort({enum_type.__name__})")
        self._initialized: bool = True

    def __repr__(self):
        return f"EnumSort({self.enum_type.__name__})"

    def __eq__(self, other):
        """Two EnumSorts are equal iff they wrap the same Enum class."""
        if not isinstance(other, EnumSort):
            return False
        return self.enum_type is other.enum_type

    def __hash__(self):
        """Hash based on the enum type identity."""
        return hash((type(self).__name__, self.enum_type.__name__))


class SetSort(Sort):
    """Set sort parameterized by the element sort."""

    def __init__(self, elem_sort: Sort):
        self.elem_sort = elem_sort
        super().__init__(f"Set({elem_sort.name})")

    def __repr__(self):
        return f"SetSort({repr(self.elem_sort)})"

    def __eq__(self, other):
        if not isinstance(other, SetSort):
            return False

        return self.elem_sort == other.elem_sort

    def __hash__(self):
        return hash((self.name, self.elem_sort))


class MapSort(Sort):
    """Map sort (immutable dictionary from keys to values)."""

    def __init__(self, key_sort: Sort, value_sort: Sort):
        self.key_sort = key_sort
        self.value_sort = value_sort
        super().__init__(f"Map({key_sort.name}, {value_sort.name})")

    def __repr__(self):
        return f"MapSort({repr(self.key_sort)}, {repr(self.value_sort)})"

    def __eq__(self, other):
        if not isinstance(other, MapSort):
            return False

        return self.key_sort == other.key_sort and self.value_sort == other.value_sort

    def __hash__(self):
        return hash((self.name, self.key_sort, self.value_sort))


class RecordSort(Sort):
    """Record sort with named fields.

    Fields are stored in sorted order by name to ensure structural equality
    is independent of the order in which fields are defined.

    Two RecordSort instances are equal iff they have the same set of
    (field_name, field_sort) pairs.

    Example:
        Person = RecordSort(name=StringSort(), age=IntSort(), active=BoolSort())
    """

    def __init__(self, **fields: Sort):
        """Initialize a record sort with named fields.

        Fields are automatically sorted by name for canonical ordering.

        Args:
            **fields: Named fields where each value is a Sort.
        """
        # Sort fields by name for canonical ordering
        self.fields = tuple(sorted(fields.items(), key=lambda x: x[0]))
        self._field_dict = dict(self.fields)  # For O(1) field lookup

        # Generate sort name
        if self.fields:
            field_strs = ", ".join(f"{name}={sort.name}" for name, sort in self.fields)
            name = f"RecordSort({field_strs})"
        else:
            name = "RecordSort()"
        super().__init__(name)

    def __getitem__(self, field_name: str) -> Sort:
        """Get the sort of a field by name."""
        return self._field_dict[field_name]

    def __contains__(self, field_name: str) -> bool:
        """Check if a field exists in this record."""
        return field_name in self._field_dict

    def __eq__(self, other):
        """Two RecordSorts are equal iff they have the same fields."""
        if not isinstance(other, RecordSort):
            return False
        return self.fields == other.fields

    def __hash__(self):
        """Hash based on the sorted fields."""
        return hash((type(self).__name__, self.fields))

    def __repr__(self):
        if self.fields:
            field_strs = ", ".join(f"{name}={repr(sort)}" for name, sort in self.fields)
            return f"RecordSort({field_strs})"
        return "RecordSort()"


class ListSort(Sort):
    """List sort parameterized by the element sort."""

    def __init__(self, elem_sort: Sort):
        self.elem_sort = elem_sort
        super().__init__(f"List({elem_sort.name})")

    def __repr__(self):
        return f"ListSort({repr(self.elem_sort)})"

    def __eq__(self, other):
        if not isinstance(other, ListSort):
            return False

        return self.elem_sort == other.elem_sort

    def __hash__(self):
        return hash((self.name, self.elem_sort))


class UnionSort(Sort):
    """Union sort with tagged variants.

    Each variant has a tag name and an optional payload sort.
    Variants are stored in sorted order by tag name.

    Example:
        Option = UnionSort(Some=IntSort(), None_=None)
    """

    def __init__(self, **variants: Sort | None):
        """Initialize a union sort with tagged variants.

        Args:
            **variants: Named variants where each value is a Sort (payload)
                        or None (no payload).
        """
        self.variants = tuple(sorted(variants.items(), key=lambda x: x[0]))
        self._variant_dict = dict(self.variants)

        parts = []
        for tag, sort in self.variants:
            parts.append(f"{tag}={sort.name}" if sort else tag)
        name = f"UnionSort({', '.join(parts)})"
        super().__init__(name)

    def __getitem__(self, tag: str) -> Sort | None:
        """Get the payload sort of a variant by tag."""
        return self._variant_dict[tag]

    def __contains__(self, tag: str) -> bool:
        """Check if a tag exists in this union."""
        return tag in self._variant_dict

    def __eq__(self, other):
        """Two UnionSorts are equal iff they have the same variants."""
        if not isinstance(other, UnionSort):
            return False
        return self.variants == other.variants

    def __hash__(self):
        """Hash based on the sorted variants."""
        return hash((type(self).__name__, self.variants))

    def __repr__(self):
        parts = []
        for tag, sort in self.variants:
            parts.append(f"{tag}={repr(sort)}" if sort else f"{tag}=None")
        return f"UnionSort({', '.join(parts)})"


class TupleSort(Sort):
    """Sort for tuple types with positional elements.

    Tuples are immutable sequences of values with potentially different sorts.
    Elements are accessed by index rather than by name.

    Example:
        pair_sort = TupleSort(IntSort(), BoolSort())  # (Int, Bool)
        triple_sort = TupleSort(IntSort(), IntSort(), IntSort())  # (Int, Int, Int)
    """

    def __init__(self, *elem_sorts: Sort):
        """Initialize a tuple sort with element sorts.

        Args:
            *elem_sorts: Variable number of sorts for tuple elements.
        """
        if not elem_sorts:
            raise ValueError("Tuple must have at least one element")
        name = f"TupleSort({', '.join(s.name for s in elem_sorts)})"
        super().__init__(name)
        self.elem_sorts = tuple(elem_sorts)

    def __getitem__(self, index: int) -> Sort:
        """Get the sort of an element by index."""
        return self.elem_sorts[index]

    def __len__(self) -> int:
        """Get the number of elements in this tuple."""
        return len(self.elem_sorts)

    def __contains__(self, index: int) -> bool:
        """Check if an index is valid for this tuple."""
        return 0 <= index < len(self.elem_sorts)

    def __eq__(self, other):
        if not isinstance(other, TupleSort):
            return False
        return self.elem_sorts == other.elem_sorts

    def __hash__(self):
        return hash((self.name, self.elem_sorts))

    def __repr__(self):
        return f"TupleSort({', '.join(repr(s) for s in self.elem_sorts)})"


# =============================================================================
# Type conversion helper
# =============================================================================


@cache  # type: ignore[arg-type]
def sort_of(py_type: Type) -> Sort:
    """Convert a Python type annotation to a DSL Sort.

    Supports:
    - Basic types: int, bool, str
    - Enum types -> EnumSort
    - Generic types: dict[K, V] -> MapSort, set[T] -> SetSort, tuple[T, ...] -> TupleSort
    - Dataclasses -> RecordSort (derived from fields)
    - Record types with _record_sort attribute
    - NewType -> resolves to the base type's Sort

    Args:
        py_type: A Python type annotation

    Returns:
        The corresponding DSL Sort

    Raises:
        TypeError: If the type cannot be converted to a Sort
    """
    # Handle NewType - resolve to the supertype
    if hasattr(py_type, "__supertype__"):
        return sort_of(py_type.__supertype__)

    # Handle basic types
    if py_type is int or py_type == int:
        return IntSort()
    elif py_type is bool or py_type == bool:
        return BoolSort()
    elif py_type is str or py_type == str:
        return StrSort()

    # Handle enum types
    try:
        if isinstance(py_type, type) and issubclass(py_type, Enum):
            return EnumSort(py_type)
    except TypeError:
        # issubclass can raise TypeError if py_type is not a class
        pass

    # Handle record types with _record_sort attribute
    if hasattr(py_type, "_record_sort"):
        return py_type._record_sort  # type: ignore[no-any-return]

    # Handle union types with _union_sort attribute
    if hasattr(py_type, "_union_sort"):
        return py_type._union_sort  # type: ignore[no-any-return]

    # Handle dataclasses - derive RecordSort from fields
    if dataclasses.is_dataclass(py_type):
        fields = dataclasses.fields(py_type)
        field_sorts = {}
        for field in fields:
            field_sorts[field.name] = sort_of(field.type)  # type: ignore[arg-type]
        return RecordSort(**field_sorts)  # type: ignore[return-value]

    # Handle generic types using get_origin
    origin = get_origin(py_type)
    if origin is not None:
        args = get_args(py_type)

        if origin is dict:
            if len(args) != 2:
                raise TypeError(
                    f"dict type must have exactly 2 type arguments, got {len(args)}"
                )
            key_sort = sort_of(args[0])
            value_sort = sort_of(args[1])
            return MapSort(key_sort, value_sort)

        elif origin is set:
            if len(args) != 1:
                raise TypeError(
                    f"set type must have exactly 1 type argument, got {len(args)}"
                )
            elem_sort = sort_of(args[0])
            return SetSort(elem_sort)

        elif origin is list:
            if len(args) != 1:
                raise TypeError(
                    f"list type must have exactly 1 type argument, got {len(args)}"
                )
            elem_sort = sort_of(args[0])
            return ListSort(elem_sort)

        elif origin is tuple:
            if not args:
                raise TypeError("tuple type must have type arguments")
            elem_sorts = tuple(sort_of(arg) for arg in args)
            return TupleSort(*elem_sorts)

    raise TypeError(f"Cannot convert Python type {py_type} to Sort")
