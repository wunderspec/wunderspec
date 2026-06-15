"""
Pure AST nodes for map expressions.

These are data structures only - no operator logic.
"""

from __future__ import annotations

from abc import ABC

from .ast import Node, VarNode
from .set_ast import SetNode
from .sorts import MapSort, Sort


class MapNode(Node, ABC):
    """Abstract base class for all map-related nodes."""

    def __init__(self, key_sort: Sort, value_sort: Sort):
        super().__init__(MapSort(key_sort, value_sort))
        self.key_sort = key_sort
        self.value_sort = value_sort


class MapEnumNode(MapNode):
    """Map enumeration node parameterized by key and value types (immutable dictionary)."""

    def __init__(
        self, key_sort: Sort, value_sort: Sort, mappings: dict[Node, Node] | None = None
    ):
        super().__init__(key_sort, value_sort)
        self.mappings = mappings if mappings is not None else {}

        # Validate that all keys and values have the correct sorts
        for key, value in self.mappings.items():
            if key.sort != key_sort:
                raise TypeError(f"Key {key} has sort {key.sort}, expected {key_sort}")
            if value.sort != value_sort:
                raise TypeError(
                    f"Value {value} has sort {value.sort}, expected {value_sort}"
                )

    def __repr__(self):
        if self.mappings:
            items = ", ".join(
                f"Tuple({repr(k)}, {repr(v)})" for k, v in self.mappings.items()
            )
            return f"Map({items})"
        else:
            return f"Map({repr(self.key_sort)}, {repr(self.value_sort)})"

    def __eq__(self, other):
        if not isinstance(other, MapEnumNode):
            return False

        return self.sort == other.sort and self.mappings == other.mappings

    def __hash__(self):
        return hash((self.sort, frozenset(self.mappings.items())))


class MapLambdaNode(MapNode):
    """Map construction via lambda node: [ x ∈ S |-> e(x) ]."""

    def __init__(
        self,
        base_set: Node,
        var: VarNode,
        mapper: Node,
    ):
        super().__init__(var.sort, mapper.sort)
        self.base_set = base_set
        self.var = var
        self.mapper = mapper

    def __repr__(self):
        return (
            f"MapLambda({repr(self.var)}, {repr(self.base_set)}, {repr(self.mapper)})"
        )

    def __eq__(self, other):
        if not isinstance(other, MapLambdaNode):
            return False

        return (
            self.sort == other.sort
            and self.base_set == other.base_set
            and self.var == other.var
            and self.mapper == other.mapper
        )

    def __hash__(self):
        return hash((self.sort, self.base_set, self.var, self.mapper))


class MapGetNode(Node):
    """Map lookup node: map[key]."""

    def __init__(self, map_node: Node, key: Node):
        # Validate that map_node has MapSort
        if not isinstance(map_node.sort, MapSort):
            raise TypeError(f"Map node must have MapSort, got {map_node.sort}")

        # The result type is the value sort of the map (not MapSort)
        super().__init__(map_node.sort.value_sort)

        self.map_node = map_node
        self.key = key

        # Validate key sort
        if key.sort != map_node.sort.key_sort:
            raise TypeError(
                f"Key {key} has sort {key.sort}, expected {map_node.sort.key_sort}"
            )

    def __repr__(self):
        return f"MapGet({repr(self.map_node)}, {repr(self.key)})"

    def __eq__(self, other):
        if not isinstance(other, MapGetNode):
            return False

        return (
            self.sort == other.sort
            and self.map_node == other.map_node
            and self.key == other.key
        )

    def __hash__(self):
        return hash((self.sort, self.map_node, self.key))


class MapSetNode(MapNode):
    """Map update node: map with key mapped to value.  If `replace_only` is True,
    then the map does not change if the key is not already present."""

    def __init__(
        self, map_node: Node, key: Node, value: Node, replace_only: bool = False
    ):
        # Validate that map_node has MapSort
        if not isinstance(map_node.sort, MapSort):
            raise TypeError(f"Map node must have MapSort, got {map_node.sort}")

        super().__init__(map_node.sort.key_sort, map_node.sort.value_sort)
        self.base_map = map_node
        self.update_key = key
        self.update_value = value
        self.replace_only = replace_only

        # Validate key and value sorts
        if key.sort != map_node.sort.key_sort:
            raise TypeError(
                f"Key {key} has sort {key.sort}, expected {map_node.sort.key_sort}"
            )
        if value.sort != map_node.sort.value_sort:
            raise TypeError(
                f"Value {value} has sort {value.sort}, expected {map_node.sort.value_sort}"
            )

    def __repr__(self):
        name = "MapReplace" if self.replace_only else "MapSet"
        return f"{name}({repr(self.base_map)}, {repr(self.update_key)}, {repr(self.update_value)})"

    def __eq__(self, other):
        if not isinstance(other, MapSetNode):
            return False

        return (
            self.sort == other.sort
            and self.base_map == other.base_map
            and self.update_key == other.update_key
            and self.update_value == other.update_value
            and self.replace_only == other.replace_only
        )

    def __hash__(self):
        return hash(
            (
                self.sort,
                self.base_map,
                self.update_key,
                self.update_value,
                self.replace_only,
            )
        )


class MapKeysNode(SetNode):
    """Map keys node: returns the set of keys in a map."""

    def __init__(self, map_node: Node):
        # Validate that map_node has MapSort
        if not isinstance(map_node.sort, MapSort):
            raise TypeError(f"Map node must have MapSort, got {map_node.sort}")

        # Initialize SetNode with the key sort
        super().__init__(map_node.sort.key_sort)

        self.map_node = map_node

    def __repr__(self):
        return f"MapKeys({repr(self.map_node)})"

    def __eq__(self, other):
        if not isinstance(other, MapKeysNode):
            return False

        return self.sort == other.sort and self.map_node == other.map_node

    def __hash__(self):
        return hash((self.sort, self.map_node))
