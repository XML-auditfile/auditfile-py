"""MemorySink: keeps all rows in memory, per table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .definitions import TextLanguage, ViewDefinition
from .diagnostics import AuditfileReport
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaTable
from .sinks import ImportContext, SinkCapabilities
from .views import ViewResult


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
