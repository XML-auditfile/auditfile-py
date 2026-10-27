"""Rows of an import."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import SchemaTable


class AuditfileRow:
    """
    One row of a table. ``values`` follows the column order of ``table``; the Python type of a value follows the
    column type (str, Decimal, int, date, datetime, time, bool) or is None. Read a column with ``row["amnt"]``.
    """

    __slots__ = ("table", "id", "parent_id", "ordinal", "part", "values")

    def __init__(self, table: SchemaTable, id_: int, parent_id: int | None, ordinal: int, part: int, values: tuple[object, ...]) -> None:
        self.table = table
        """The table this row belongs to."""
        self.id = id_
        """Unique within the import; assigned in document order."""
        self.parent_id = parent_id
        """Id of the parent row, or None for the root."""
        self.ordinal = ordinal
        """Position under the parent row (1-based), per table."""
        self.part = part
        """Source part the row came from (1-based)."""
        self.values = values
        """Column values in column order."""

    def __getitem__(self, column: str) -> object:
        """Value of a column by name. :raises KeyError: The column does not exist."""
        found = self.table.find_column(column)
        if found is None:
            raise KeyError(f"Column '{column}' does not exist in {self.table.name}.")

        return self.values[found.index]

    def __repr__(self) -> str:
        return f"AuditfileRow({self.table.name}, id={self.id})"
