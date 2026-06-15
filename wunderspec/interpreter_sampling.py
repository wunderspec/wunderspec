"""
Random sampling strategies for interpreter values.
For starters, we implement uniform selection.

Igor Konnov, 2026
"""

import itertools
import random
from dataclasses import dataclass
from typing import Any, Protocol

from wunderspec.interpreter_value import (
    AbstractSetValue,
    AllMapsValue,
    AllRecordsValue,
    AllSubsetsValue,
    AllTuplesValue,
    EnumeratedSetValue,
    InfIntSetValue,
    IntervalSetValue,
    IntValue,
    IValue,
    MapValue,
    RecordValue,
    TupleValue,
)


class EmptySetError(ValueError):
    """Raised when attempting to sample from an empty set."""

    pass


@dataclass
class SamplingHint:
    """
    Sampling hints that are usually fed to the sampling strategies.
    """

    rng: random.Random | None
    size: int | None


class SamplingStrategy(Protocol):
    """A general protocol for random selection strategies"""

    def draw(self, base_set: IValue, hint: Any) -> IValue:
        """
        Draw a value from the `base_set`, possibly, using the provided
        `hint` object, e.g., RNG or upper bound on the value. If the
        implementation is not compatible with the hint, it should ignore the hint.

        It's the callee's responsibility to check that `base_set` is actually
        a set.
        """
        ...


class UniformSamplingStrategy(SamplingStrategy):
    """
    A simple selection strategy that picks set elements uniformly at random.
    In case, the upper bound `size` is provided, it restricts the search interval
    to the first `size` elements. What "first elements" means is specific to the
    actual representation of the set.
    """

    def draw(self, base_set: IValue, hint: Any) -> IValue:
        rng = hint.rng if hasattr(hint, "rng") and hint.rng else random.Random()
        bound = hint.size if hasattr(hint, "size") else None
        if bound is not None and bound <= 0:
            raise ValueError(f"Cannot draw with size={bound}")
        match base_set:
            case EnumeratedSetValue():
                set_size = len(base_set.material_set)
                # find the upper bound (exclusive)
                bound = min(bound, set_size) if bound else set_size
                if bound <= 0:
                    raise EmptySetError(
                        f"Cannot draw from the empty set {id(base_set)}"
                    )
                index = rng.randint(0, bound - 1)
                return base_set.element_at(index)

            case IntervalSetValue():
                # find the upper bound (inclusive)
                if bound:
                    upper = min(base_set.end, base_set.start + bound - 1)
                else:
                    upper = base_set.end

                index = rng.randint(base_set.start, upper)
                return IntValue(index)

            case InfIntSetValue():
                if base_set.is_signed:
                    # Ints: all integers (ℤ), including negative
                    if bound:
                        # Draw from both negative and positive numbers
                        # (actually, 2*bound - 1 of them: -bound+1 to bound-1)
                        return IntValue(rng.randint(-bound + 1, bound - 1))
                    else:
                        raise ValueError("Ints needs a 'size' hint")
                else:
                    # UnsignedInts: non-negative integers (ℕ)
                    if bound:
                        # Draw from non-negative integers only: [0, bound-1]
                        return IntValue(rng.randint(0, bound - 1))
                    else:
                        raise ValueError("UnsignedInts needs a 'size' hint")

            case AllSubsetsValue():
                # Direct index: pick a random bitmask
                n = len(base_set._base_elements)
                size = base_set._size()
                if size == 0:
                    raise EmptySetError("Cannot draw from empty AllSubsetsValue")
                upper = min(size, bound) if bound else size
                mask = rng.randint(0, upper - 1)
                inner_sort = base_set._inner_elem_sort()
                return EnumeratedSetValue(
                    *(base_set._base_elements[i] for i in range(n) if (mask >> i) & 1),
                    elem_sort=inner_sort,
                )

            case AllMapsValue():
                # Direct index: pick a random number and decompose to value indices
                keys = base_set._keys
                values = base_set._values
                if not keys:
                    return MapValue({})
                size = base_set._size()
                if size == 0:
                    raise EmptySetError("Cannot draw from empty AllMapsValue")
                upper = min(size, bound) if bound else size
                idx = rng.randint(0, upper - 1)
                # Decompose idx into indices for each key (mixed-radix)
                num_values = len(values)
                mapping = {}
                for key in keys:
                    mapping[key] = values[idx % num_values]
                    idx //= num_values
                return MapValue(mapping)

            case AllTuplesValue():
                # Direct index: pick a random number and decompose to dimension indices
                dims = base_set._dimension_elements
                if not dims:
                    raise EmptySetError("Cannot draw from empty AllTuplesValue")
                size = base_set._size()
                if size == 0:
                    raise EmptySetError("Cannot draw from empty AllTuplesValue")
                upper = min(size, bound) if bound else size
                idx = rng.randint(0, upper - 1)
                # Decompose idx into indices for each dimension (mixed-radix)
                elements = []
                for dim in dims:
                    dim_size = len(dim)
                    elements.append(dim[idx % dim_size])
                    idx //= dim_size
                return TupleValue(*elements)

            case AllRecordsValue():
                # Direct index: pick a random number and decompose to field indices
                field_names = base_set._field_names
                field_elements = base_set._field_elements
                if not field_names:
                    raise EmptySetError("Cannot draw from empty AllRecordsValue")
                size = base_set._size()
                if size == 0:
                    raise EmptySetError("Cannot draw from empty AllRecordsValue")
                upper = min(size, bound) if bound else size
                idx = rng.randint(0, upper - 1)
                # Decompose idx into indices for each field (mixed-radix)
                fields = {}
                for i, name in enumerate(field_names):
                    field_size = len(field_elements[i])
                    fields[name] = field_elements[i][idx % field_size]
                    idx //= field_size
                return RecordValue(**fields)

            case AbstractSetValue():
                # Generic fallback for lazy finite sets (e.g., SetFilterValue/SetMapValue).
                # For bounded draws, materialize only the prefix to stay compatible with
                # schedulers that replay a fixed random index (e.g., MockRNG).
                if bound is not None:
                    candidates = list(itertools.islice(base_set, bound))
                    if len(candidates) == 0:
                        raise EmptySetError(
                            f"Cannot draw from the empty set {id(base_set)}"
                        )
                    return candidates[rng.randint(0, len(candidates) - 1)]
                else:
                    chosen = None
                    count = 0
                    for elem in base_set:
                        count += 1
                        if rng.randint(0, count - 1) == 0:
                            chosen = elem
                    if chosen is None:
                        raise EmptySetError(
                            f"Cannot draw from the empty set {id(base_set)}"
                        )
                    return chosen

            case _:
                raise ValueError(f"Unexpected set type: {type(base_set)}")
