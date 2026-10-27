"""Rows of an import."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Union

from .definitions import ColumnType

if TYPE_CHECKING:
    from .schema import SchemaColumn, SchemaTable

Column = Union[str, "SchemaColumn"]
"""A column name, or a SchemaColumn of the row's table (looked up once, for loops over many rows)."""


class AuditfileRow:
    """
    One row of a table. ``values`` follows the column order of ``table``; the Python type of a value follows the
    column type (str, Decimal, int, date, datetime, time, bool) or is None. Read a column with ``row["amnt"]``.

    Prefer the typed accessors (``get_decimal``, ``get_string``, ...): they check the column type and say what is wrong.
    Every accessor takes a column name or a ``SchemaColumn``; in a loop over many rows, look the column up once with
    ``table.find_column()`` and pass it.
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

    def __getitem__(self, column: Column) -> object:
        """Value of a column. :raises KeyError: The column does not exist in this table."""
        return self.values[self._column(column).index]

    def __contains__(self, column: object) -> bool:
        """True when this table has the column (a column may exist in one version and not in another)."""
        return isinstance(column, str) and self.table.find_column(column) is not None

    def get_string(self, column: Column) -> str | None:
        """A string column. :raises KeyError, TypeError:"""
        return self._typed(column, ColumnType.STRING)  # type: ignore[return-value]

    def get_decimal(self, column: Column) -> Decimal | None:
        """A decimal column, or an integer column as a Decimal (a column can be integer in one version and decimal in another)."""
        value = self._typed(column, ColumnType.DECIMAL, ColumnType.INTEGER)
        return Decimal(value) if isinstance(value, int) else value  # type: ignore[return-value]

    def get_integer(self, column: Column) -> int | None:
        """An integer column. :raises KeyError, TypeError:"""
        return self._typed(column, ColumnType.INTEGER)  # type: ignore[return-value]

    def get_date(self, column: Column) -> dt.date | None:
        """A date column. :raises KeyError, TypeError:"""
        return self._typed(column, ColumnType.DATE)  # type: ignore[return-value]

    def get_datetime(self, column: Column) -> dt.datetime | dt.date | None:
        """A date-time column, or a date column (a date). :raises KeyError, TypeError:"""
        return self._typed(column, ColumnType.DATETIME, ColumnType.DATE)  # type: ignore[return-value]

    def get_time(self, column: Column) -> dt.time | None:
        """A time column. :raises KeyError, TypeError:"""
        return self._typed(column, ColumnType.TIME)  # type: ignore[return-value]

    def get_boolean(self, column: Column) -> bool | None:
        """A boolean column. :raises KeyError, TypeError:"""
        return self._typed(column, ColumnType.BOOLEAN)  # type: ignore[return-value]

    def _column(self, column: Column) -> SchemaColumn:
        if isinstance(column, str):
            found = self.table.find_column(column)
            if found is None:
                raise KeyError(f"Column '{column}' does not exist in {self.table.name}.")

            return found

        columns = self.table.columns
        if not (0 <= column.index < len(columns) and columns[column.index] is column):
            raise KeyError(f"Column '{column.name}' does not belong to {self.table.name}.")

        return column

    def _typed(self, column: Column, type_: ColumnType, alternative: ColumnType | None = None) -> object:
        found = self._column(column)
        if found.type is not type_ and found.type is not alternative:
            raise TypeError(
                f"{self.table.name}.{found.name} is a {found.type.value} column, not {type_.value}; use {_ACCESSORS[found.type]}."
            )

        return self.values[found.index]

    def __repr__(self) -> str:
        return f"AuditfileRow({self.table.name}, id={self.id})"


_ACCESSORS = {
    ColumnType.STRING: "get_string",
    ColumnType.DECIMAL: "get_decimal",
    ColumnType.INTEGER: "get_integer",
    ColumnType.DATE: "get_date",
    ColumnType.DATETIME: "get_datetime",
    ColumnType.TIME: "get_time",
    ColumnType.BOOLEAN: "get_boolean",
}
