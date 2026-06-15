"""
Pure AST nodes for record expressions.

These are data structures only - no operator logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .ast import Node
from .sorts import RecordSort


class RecordNode(Node, ABC):
    """Abstract base class for record AST nodes."""

    def __init__(self, record_sort: RecordSort):
        super().__init__(record_sort)

    @abstractmethod
    def __repr__(self):
        """Subclasses must implement their own representation."""
        pass


class RecordCtorNode(RecordNode):
    """Record constructor node with named field values.

    Fields are stored in sorted order by name (inherited from RecordSort).

    Example:
        person = RecordCtorNode(name=name_node, age=age_node, active=active_node)
    """

    def __init__(self, **fields: Node):
        """Initialize a record constructor node with named field values.

        Args:
            **fields: Named fields where each value is a Node.
                     Field names and sorts are extracted to create the RecordSort.
        """
        # Extract sorts from the field nodes
        field_sorts = {name: node.sort for name, node in fields.items()}
        record_sort = RecordSort(**field_sorts)

        super().__init__(record_sort)

        # Store fields in sorted order (matching RecordSort)
        self.fields = tuple(sorted(fields.items(), key=lambda x: x[0]))
        self._field_dict = dict(self.fields)  # For O(1) field lookup

    def __getitem__(self, field_name: str) -> Node:
        """Get the value node of a field by name."""
        return self._field_dict[field_name]

    def __contains__(self, field_name: str) -> bool:
        """Check if a field exists in this record."""
        return field_name in self._field_dict

    def __repr__(self):
        if self.fields:
            items = ", ".join(f"{name}={repr(node)}" for name, node in self.fields)
            return f"Record({items})"
        else:
            return "Record()"

    def __eq__(self, other):
        if not isinstance(other, RecordCtorNode):
            return False

        return self.sort == other.sort and self.fields == other.fields

    def __hash__(self):
        return hash((self.sort, self.fields))


class RecordUpdateNode(RecordNode):
    """Record update node: record with specified fields updated to new values.

    This node represents a record with some fields updated. The base record
    can be any RecordNode (constructor, variable, or another update).

    Example:
        updated = RecordUpdateNode(base_record, age=new_age_node, active=new_active_node)
    """

    def __init__(self, base_record: Node, **updates: Node):
        """Initialize a record update node.

        Args:
            base_record: The base record node to update (must have RecordSort).
            **updates: Field names and their new values. Field names must exist
                      in the base record and new values must have matching sorts.

        Raises:
            TypeError: If a field name doesn't exist or sort doesn't match.
        """
        if not isinstance(base_record.sort, RecordSort):
            raise TypeError(f"Base record must have RecordSort, got {base_record.sort}")

        super().__init__(base_record.sort)
        self.base_record = base_record

        # Validate updates: check field names exist and sorts match
        for field_name, new_value in updates.items():
            if field_name not in base_record.sort:
                raise TypeError(
                    f"Field '{field_name}' does not exist in record {base_record.sort}"
                )
            expected_sort = base_record.sort[field_name]
            if new_value.sort != expected_sort:
                raise TypeError(
                    f"Field '{field_name}' has sort {expected_sort}, "
                    f"but got value with sort {new_value.sort}"
                )

        # Store updates in sorted order
        self.updates = tuple(sorted(updates.items(), key=lambda x: x[0]))
        self._update_dict = dict(self.updates)  # For O(1) lookup

    def __repr__(self):
        if self.updates:
            updates_str = ", ".join(
                f"{name}={repr(node)}" for name, node in self.updates
            )
            return f"RecordUpdate({repr(self.base_record)}, {updates_str})"
        else:
            return repr(self.base_record)

    def __eq__(self, other):
        if not isinstance(other, RecordUpdateNode):
            return False

        return (
            self.sort == other.sort
            and self.base_record == other.base_record
            and self.updates == other.updates
        )

    def __hash__(self):
        return hash((self.sort, self.base_record, self.updates))


class RecordGetNode(Node):
    """Record field access node: record.field or record[field].

    This node represents accessing a single field from a record.
    The result sort is the sort of the accessed field.

    Example:
        age_access = RecordGetNode(person_record, "age")
    """

    def __init__(self, record_node: Node, field_name: str):
        """Initialize a record field access node.

        Args:
            record_node: The record node to access (must have RecordSort).
            field_name: The name of the field to access (must exist in record).

        Raises:
            TypeError: If the field name doesn't exist in the record.
        """
        if not isinstance(record_node.sort, RecordSort):
            raise TypeError(f"Record node must have RecordSort, got {record_node.sort}")

        if field_name not in record_node.sort:
            raise TypeError(
                f"Field '{field_name}' does not exist in record {record_node.sort}"
            )

        # The result type is the sort of the accessed field
        field_sort = record_node.sort[field_name]
        super().__init__(field_sort)

        self.record_node = record_node
        self.field_name = field_name

    def __repr__(self):
        return f"RecordGet({repr(self.record_node)}, {repr(self.field_name)})"

    def __eq__(self, other):
        if not isinstance(other, RecordGetNode):
            return False

        return (
            self.sort == other.sort
            and self.record_node == other.record_node
            and self.field_name == other.field_name
        )

    def __hash__(self):
        return hash((self.sort, self.record_node, self.field_name))
