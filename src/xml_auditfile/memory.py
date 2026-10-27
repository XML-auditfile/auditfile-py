"""MemorySink: keeps all rows in memory, per table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ._frames import Decimals, pandas_frame, polars_frame
from .definitions import ColumnType, TextLanguage, ViewDefinition
from .diagnostics import AuditfileReport
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaTable
from .sinks import ImportContext, SinkCapabilities
from .views import ViewResult

if TYPE_CHECKING:
    import pandas
    import polars


class MemorySink:
    """Keeps all rows in memory, per table. Useful for tests and small files."""

    capabilities = SinkCapabilities.NONE

    def __init__(self) -> None:
        self._tables: dict[str, list[AuditfileRow]] = {}
        self.schema: AuditfileSchema | None = None
        """The import schema, after begin."""
        self.report: AuditfileReport | None = None
        """The report, after completion or abort."""
        self.is_aborted = False
        """True when the import was aborted; no rows are kept then."""

    def rows(self, table: str) -> Sequence[AuditfileRow]:
        """Rows of a table; empty when the table has no rows."""
        return tuple(self._tables.get(table, ()))

    def run(self, view: ViewDefinition, parameters: Mapping[str, object] | None = None, language: TextLanguage = TextLanguage.DUTCH) -> ViewResult:
        """Runs a view, e.g. ``sink.run(sink.schema.version.find_view("trial_balance"))``, over the rows in memory."""
        if self.schema is None:
            raise RuntimeError("Read an audit file into this sink first.")

        return view.run(self.schema, lambda table: self._tables.get(table, ()), parameters, language)

    def to_pandas(self, table: str, decimals: Decimals = "decimal") -> pandas.DataFrame:
        """
        The rows of a table as a pandas DataFrame (needs ``pip install xml-auditfile[pandas]``).

        The index ``_id`` is the row id and the column ``_parent_id`` the id of the parent row, so a child table
        joins its parent with
        ``child.merge(parent, left_on="_parent_id", right_index=True, suffixes=("", "_parent"))``. Strings, integers
        and booleans get nullable pandas types, dates and date-times datetime64 (date-times in UTC when the file gives a
        time zone), times stay datetime.time. Decimals stay exact Decimal objects; ``decimals="float"`` gives float64.
        A table without rows gives an empty DataFrame with all columns.

        :raises KeyError: The schema has no table of this name.
        """
        schema_table, rows = self._frame_source(table)
        columns = [("_parent_id", ColumnType.INTEGER), *((c.name, c.type) for c in schema_table.columns)]
        return pandas_frame(columns, ((r.parent_id, *r.values) for r in rows), decimals, ("_id", [r.id for r in rows]))

    def to_polars(self, table: str, decimals: Decimals = "decimal") -> polars.DataFrame:
        """
        The rows of a table as a polars DataFrame (needs ``pip install xml-auditfile[polars]``).

        The columns ``_id`` (the row id) and ``_parent_id`` (the id of the parent row) come first, so a child table
        joins its parent with ``child.join(parent, left_on="_parent_id", right_on="_id", suffix="_parent")``. Every
        column gets the polars type of its column type (String, Int64, Boolean, Date, Datetime in UTC when the file
        gives a time zone, Time). Decimals get an exact Decimal type with the largest scale in the column;
        ``decimals="float"`` gives Float64. A table without rows gives an empty DataFrame with all columns.

        :raises KeyError: The schema has no table of this name.
        """
        schema_table, rows = self._frame_source(table)
        columns = [("_id", ColumnType.INTEGER), ("_parent_id", ColumnType.INTEGER), *((c.name, c.type) for c in schema_table.columns)]
        return polars_frame(columns, ((r.id, r.parent_id, *r.values) for r in rows), decimals)

    def _frame_source(self, table: str) -> tuple[SchemaTable, list[AuditfileRow]]:
        if self.schema is None:
            raise RuntimeError("Read an audit file into this sink first.")

        schema_table = self.schema.find_table(table)
        if schema_table is None:
            raise KeyError(f"The schema of {self.schema.version_key} has no table '{table}'.")

        return schema_table, self._tables.get(table, [])

    def begin(self, context: ImportContext) -> None:
        self._tables.clear()
        self.schema = context.schema
        self.is_aborted = False

    def write(self, table: SchemaTable, rows: Sequence[AuditfileRow]) -> None:
        self._tables.setdefault(table.name, []).extend(rows)

    def complete(self, report: AuditfileReport) -> None:
        self.report = report

    def abort(self, report: AuditfileReport) -> None:
        self._tables.clear()
        self.report = report
        self.is_aborted = True
