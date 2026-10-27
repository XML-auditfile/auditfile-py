"""Entry point. Read an audit file into a sink, validate it, scan it, or stream its rows yourself."""

from __future__ import annotations

from collections.abc import Iterator

from . import custom
from ._session import ReadSession
from .diagnostics import AuditfileReport
from .options import DEFAULT_OPTIONS, ReadOptions, ReadProgress
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaTable
from .sinks import ImportContext, NullSink, Sink, SinkCapabilities, check_sink
from .source import AuditfileSource


class SinkAbortError(Exception):
    """The import failed, and aborting the sink failed too. Holds both errors."""

    def __init__(self, error: Exception, abort_error: Exception) -> None:
        super().__init__(f"The import failed ({error}), and aborting the sink failed too ({abort_error}).")
        self.error = error
        """Why the import failed."""
        self.abort_error = abort_error
        """Why aborting the sink failed."""


def _check_source(source: object) -> AuditfileSource:
    if not isinstance(source, AuditfileSource):
        raise TypeError("source must be an AuditfileSource.")

    return source


def _check_options(options: object) -> ReadOptions:
    if options is None:
        return DEFAULT_OPTIONS

    if not isinstance(options, ReadOptions):
        raise TypeError("options must be ReadOptions.")

    return options


class Auditfile:
    """
    Entry point. Read an audit file into a sink, validate it, scan it, or stream its rows yourself.

    Example::

        sink = MemorySink()
        report = Auditfile.read(AuditfileSource.from_factory(lambda: open("admin.xaf", "rb"), "admin.xaf"), sink)
    """

    dialects: custom.DialectRegistry = custom.dialects
    """Dialects added at runtime, next to the built-in ones: ``Auditfile.dialects.add("xaf/3.2", json)``. Shared by every read in this process."""

    views: custom.ViewRegistry = custom.views
    """Views added at runtime, next to the built-in ones: ``Auditfile.views.add(json)``, then ``version.find_view(name)``. Shared by every read in this process."""

    @staticmethod
    def read(source: AuditfileSource, sink: Sink, options: ReadOptions | None = None) -> AuditfileReport:
        """Reads an audit file into a sink. Verified mode (default) validates against the XSD first and loads only a valid file."""
        _check_source(source)
        check_sink(sink)
        settings = _check_options(options)

        session = ReadSession(source, settings)
        capabilities = SinkCapabilities(getattr(sink, "capabilities", SinkCapabilities.NONE))
        if not session.prepare(SinkCapabilities.NEEDS_ROW_COUNTS in capabilities):
            session.dispose()
            return session.create_report(False)

        schema = session.schema
        assert schema is not None
        batches: dict[int, tuple[SchemaTable, list[AuditfileRow]]] = {}
        total = sum(t.row_count or 0 for t in schema.tables) if schema.has_row_counts else None
        rows = 0

        try:
            sink.begin(ImportContext(schema, session.parts))
            for row in session.load():
                entry = batches.get(row.table.index)
                if entry is None:
                    entry = (row.table, [])
                    batches[row.table.index] = entry

                entry[1].append(row)
                rows += 1
                if len(entry[1]) >= settings.batch_size:
                    batches[row.table.index] = (row.table, [])
                    sink.write(row.table, entry[1])
                    if settings.on_progress is not None:
                        settings.on_progress(ReadProgress(rows, total))

            for index in sorted(batches):
                table, batch = batches[index]
                if batch:
                    sink.write(table, batch)

            if settings.on_progress is not None:
                settings.on_progress(ReadProgress(rows, total))
        except BaseException as error:
            session.dispose()
            try:
                sink.abort(session.create_report(False))
            except Exception as abort_error:
                # A failing rollback must not hide why the import failed: the caller gets both.
                if isinstance(error, Exception):
                    raise SinkAbortError(error, abort_error) from error

            raise

        failed = session.diagnostics.has_fatal or (session.validate_during_load and session.diagnostics.has_errors)
        report = session.create_report(not failed)
        if failed:
            sink.abort(report)
        else:
            sink.complete(report)

        return report

    @staticmethod
    def validate(source: AuditfileSource, options: ReadOptions | None = None) -> AuditfileReport:
        """
        Validates an audit file (structure, keys, business rules) without storing anything.
        In verified mode (the default) the XSD is checked first and the rest only for a valid file;
        unverified mode skips the XSD and checks everything else, which is faster but reports fewer deviations
        (e.g. XAF-S004 instead of XAF-D001 for an invalid value).
        """
        return Auditfile.read(source, NullSink(), options)

    @staticmethod
    def scan(source: AuditfileSource, options: ReadOptions | None = None) -> AuditfileReport:
        """
        The scan phase only: determines the version, validates against the XSD in verified mode,
        and counts rows per table. The report's schema contains the row counts.
        """
        session = ReadSession(_check_source(source), _check_options(options))
        session.prepare(True, True)
        session.dispose()
        return session.create_report(False)

    @staticmethod
    def open(source: AuditfileSource, options: ReadOptions | None = None) -> AuditfileReader:
        """Opens an audit file for reading rows yourself, without a sink."""
        session = ReadSession(_check_source(source), _check_options(options))
        ready = session.prepare(False)
        if not ready:
            session.dispose()

        return AuditfileReader(session, ready)


class AuditfileReader:
    """Reads rows one by one. Check `schema` for None before reading: None means the file cannot be read."""

    def __init__(self, session: ReadSession, ready: bool) -> None:
        self._session = session
        self._ready = ready
        self._started = False
        self._report: AuditfileReport | None = None

    @property
    def schema(self) -> AuditfileSchema | None:
        """The import schema, or None when the file cannot be read (see `report`)."""
        return self._session.schema if self._ready else None

    def read_rows(self) -> Iterator[AuditfileRow]:
        """
        Rows in document order; a row is returned when its element closes. The rows are read while you iterate,
        and can be iterated only once: collect them in a list when you need them twice.

        :raises RuntimeError: The rows are iterated a second time.
        """
        if self._started:
            raise RuntimeError("The rows of an audit file can be iterated only once; collect them in a list to use them twice.")

        self._started = True
        if self._ready:
            yield from self._session.load()

    @property
    def report(self) -> AuditfileReport:
        """
        The report; complete after read_rows has been iterated to the end. Until then every call gives the report so far;
        the complete report is made once and kept.
        """
        if self._report is not None:
            return self._report

        report = self._session.create_report(False)
        if not self._ready or self._session.loaded:
            self._report = report

        return report
