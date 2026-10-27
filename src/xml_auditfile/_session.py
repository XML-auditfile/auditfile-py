"""One read of one source: resolve the version, optionally scan (validate and count), then load (stream rows)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

from ._checks import ReferenceChecker, XafRules
from ._streams import ConcatenatedStream, _reader
from ._xml import END, START, TEXT, Event, XmlEncodingError, XmlReader, XmlSyntaxError
from .definitions import ColumnType, Dialect, DialectCorrection
from .diagnostics import NO_LOCATION, AuditfileReport, DiagnosticCollector, Location
from .options import ReadMode, ReadOptions
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaColumn, SchemaTable
from .source import AuditfileSource, PartInfo, SourcePart
from .values import try_convert
from .versions import Resolution, resolve_version
from .xsd import SchemaSet, XsdValidator

_PEEK_LIMIT = 1024 * 1024
_MAX_XSD_SIZE = 16 * 1024 * 1024
_XMLNS = "http://www.w3.org/2000/xmlns/"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"


class _TablePlan:
    __slots__ = ("table", "is_value_table")

    def __init__(self, table: SchemaTable) -> None:
        self.table = table
        # A repeating simple element: one "value" column read from the element text.
        self.is_value_table = len(table.columns) == 1 and table.columns[0].path == "."


class MappingPlan:
    """Lookup tables from absolute XML path to table, column or group, built once per import."""

    def __init__(self, schema: AuditfileSchema) -> None:
        self.tables: dict[str, _TablePlan] = {}
        self.columns: dict[str, tuple[_TablePlan, SchemaColumn]] = {}
        """Element and attribute columns; attributes as ".../@name"."""
        self.groups: set[str] = set()
        """Flattened elements and pure containers (no table, no column)."""

        for table in schema.tables:
            plan = _TablePlan(table)
            self.tables[table.path] = plan
            for column in table.columns:
                if column.path == ".":
                    continue

                path = f"{table.path}/{column.path}"
                self.columns[path] = (plan, column)
                self._add_ancestors(path)

            self._add_ancestors(table.path)

        self.groups.difference_update(self.tables)

    def _add_ancestors(self, path: str) -> None:
        slash = path.rfind("/")
        while slash > 0:
            self.groups.add(path[:slash])
            slash = path.rfind("/", 0, slash)


class _Frame:
    """An open table element."""

    __slots__ = ("plan", "id", "parent_id", "ordinal", "part", "path", "values", "_ordinals")

    def __init__(self, plan: _TablePlan, id_: int, parent_id: int | None, ordinal: int, part: int, path: str) -> None:
        self.plan = plan
        self.id = id_
        self.parent_id = parent_id
        self.ordinal = ordinal
        self.part = part
        self.path = path
        self.values: list[object] = [None] * len(plan.table.columns)
        self._ordinals: dict[int, int] | None = None

    def next_ordinal(self, child: _TablePlan) -> int:
        if self._ordinals is None:
            self._ordinals = {}

        value = self._ordinals.get(id(child), 0) + 1
        self._ordinals[id(child)] = value
        return value


class _Capture:
    __slots__ = ("frame", "column", "path", "text", "emit")

    def __init__(self, frame: _Frame, column: SchemaColumn, path: str, emit: bool) -> None:
        self.frame = frame
        self.column = column
        self.path = path
        self.text: list[str] = []
        self.emit = emit


class RowLoader:
    """
    The load phase: walks the XML once and turns elements into rows using the mapping plan.
    A row is emitted when its element closes, so all its columns are known; ids are assigned when it opens,
    so they follow document order. Only the open elements are kept in memory.
    """

    def __init__(
        self, reader: XmlReader, schema: AuditfileSchema, resolution: Resolution, options: ReadOptions,
        diagnostics: DiagnosticCollector, observers: list[Any], validate: Callable[[Event], None] | None,
    ) -> None:
        self._reader = reader
        self._plan = MappingPlan(schema)
        self._resolution = resolution
        self._options = options
        self._diagnostics = diagnostics
        self._observers = observers
        self._validate = validate
        self._namespace = resolution.version.namespace
        self._software_path = f"{resolution.version.root_element}/header/softwareDesc"
        self._paths: list[str] = []
        self._frames: list[_Frame] = []
        self._ready: list[AuditfileRow] = []
        self._reported: set[str] = set()
        self._corrections: dict[str, list[DialectCorrection]] = {}
        self._next_id = 0
        self._skip_depth: int | None = None
        self._capture: _Capture | None = None
        self._event: Event | None = None
        self.stopped = False
        """True when reading stopped on a fatal error."""
        self._use_dialect(resolution.dialect)

    def rows(self) -> Iterator[AuditfileRow]:
        """Rows in document order of their closing tags."""
        reader = self._reader
        validate = self._validate
        while True:
            try:
                event = reader.next()
            except XmlSyntaxError as error:
                self._diagnostics.add("XAF-X001", Location(error.part, error.line, error.column, self._current_path), detail=error.message)
                self.stopped = True
                return
            except XmlEncodingError as error:
                self._diagnostics.add("XAF-X004", NO_LOCATION, encoding=error.encoding)
                self.stopped = True
                return

            if event is None:
                return

            self._event = event
            if validate is not None:
                validate(event)

            kind = event.kind
            if kind == START:
                self._start(event)
            elif kind == END:
                self._end(event)
            elif self._capture is not None:
                self._capture.text.append(event.text)

            if self._ready:
                yield from self._ready
                self._ready.clear()

            if self.stopped:
                return

    @property
    def _current_path(self) -> str | None:
        return self._paths[-1] if self._paths else None

    def _start(self, event: Event) -> None:
        if self._skip_depth is not None:
            return

        if self._capture is not None:
            self._diagnostics.add(
                "XAF-X001", self._here(self._capture.path),
                detail=f"Element '{event.local}' is not allowed inside '{self._capture.path}', which holds a value.",
            )
            self.stopped = True
            return

        if event.depth >= self._options.max_depth:
            self._diagnostics.add("XAF-X003", self._here(self._current_path), max=self._options.max_depth)
            self.stopped = True
            return

        current = self._current_path
        path = event.local if current is None else f"{current}/{event.local}"
        if event.ns != self._namespace:
            self._skip(path, event)
            return

        top = self._frames[-1] if self._frames else None
        table = self._plan.tables.get(path)
        if table is not None:
            self._next_id += 1
            frame = _Frame(table, self._next_id, top.id if top else None, top.next_ordinal(table) if top else 1, event.part, path)
            self._read_attributes(path, frame, event)
            if table.is_value_table:
                self._capture = _Capture(frame, table.table.columns[0], path, True)
                return

            self._frames.append(frame)
            self._paths.append(path)
            return

        target = self._plan.columns.get(path)
        if target is not None and top is not None and top.plan is target[0]:
            self._capture = _Capture(top, target[1], path, False)
            return

        if path in self._plan.groups:
            if top is not None:
                self._read_attributes(path, top, event)

            self._paths.append(path)
            return

        self._skip(path, event)

    def _end(self, event: Event) -> None:
        if self._skip_depth is not None:
            if event.depth == self._skip_depth:
                self._skip_depth = None

            return

        capture = self._capture
        if capture is not None:
            self._capture = None
            self._set_value(capture.frame, capture.column, "".join(capture.text), capture.path)
            if capture.emit:
                self._emit(capture.frame)

            return

        if not self._paths:
            return

        path = self._paths.pop()
        if self._frames and self._frames[-1].path == path:
            self._emit(self._frames.pop())

    def _skip(self, path: str, event: Event) -> None:
        key = "S003:" + path
        if key not in self._reported:
            self._reported.add(key)
            self._diagnostics.add("XAF-S003", self._here(path), path=path)

        self._skip_depth = event.depth

    def _read_attributes(self, element_path: str, frame: _Frame, event: Event) -> None:
        for ns, local, value in event.attributes:
            if ns in (_XMLNS, _XSI) or local == "xmlns":
                continue

            path = f"{element_path}/@{local}"
            target = self._plan.columns.get(path)
            if target is not None and target[0] is frame.plan:
                self._set_value(frame, target[1], value, path)

    def _set_value(self, frame: _Frame, column: SchemaColumn, raw: str, path: str) -> None:
        corrections = self._corrections.get(path)
        if corrections is not None:
            raw = self._correct(raw, corrections, column, path)

        ok, value = try_convert(raw, column.type)
        if ok:
            frame.values[column.index] = value
        else:
            self._diagnostics.add(
                "XAF-S004", self._here(path), value=raw, column=f"{frame.plan.table.name}.{column.name}", type=column.type.value
            )

        if path == self._software_path and self._resolution.dialect is None and self._options.apply_dialects and isinstance(value, str):
            dialect = next((d for d in self._resolution.version.dialects if d.software_description == value), None)
            if dialect is not None:
                self._resolution.dialect = dialect
                self._use_dialect(dialect)

    def _correct(self, raw: str, corrections: list[DialectCorrection], column: SchemaColumn, path: str) -> str:
        for correction in corrections:
            corrected = raw
            if correction.action == "decimalComma" and "," in raw and "." not in raw:
                corrected = raw.replace(",", ".")
            elif correction.action == "dateFromDateTime" and column.type is ColumnType.DATE and len(raw.strip()) > 10:
                corrected = raw.strip()[:10]

            key = f"{correction.code}:{path}"
            if corrected != raw and key not in self._reported:
                self._reported.add(key)
                self._diagnostics.add(correction.code, self._here(path), path=path)

            raw = corrected

        return raw

    def _use_dialect(self, dialect: Dialect | None) -> None:
        self._corrections = {}
        for correction in dialect.corrections if dialect is not None else ():
            if correction.action == "replaceNamespace":
                continue

            for path in correction.paths:
                self._corrections.setdefault(path, []).append(correction)

    def _emit(self, frame: _Frame) -> None:
        row = AuditfileRow(frame.plan.table, frame.id, frame.parent_id, frame.ordinal, frame.part, tuple(frame.values))
        for observer in self._observers:
            observer.on_row(row)

        self._ready.append(row)

    def _here(self, path: str | None) -> Location:
        event = self._event
        return Location(event.part if event else None, event.line if event else None, event.column if event else None, path)


class _Document:
    __slots__ = ("stream", "reader")

    def __init__(self, stream: ConcatenatedStream, reader: XmlReader) -> None:
        self.stream = stream
        self.reader = reader


class ReadSession:
    """One read of one source. Holds the diagnostics and the part statistics."""

    def __init__(self, source: AuditfileSource, options: ReadOptions) -> None:
        self._source = source
        self._options = options
        self._started = time.perf_counter()
        self._row_counts: dict[str, int] = {}
        self._parts: list[PartInfo] = []
        self._loaded = False
        # A single-use source is opened once: the document used for version detection is kept for loading.
        self._pending: _Document | None = None
        self.diagnostics = DiagnosticCollector(options.language, options.max_diagnostics)
        self.resolution: Resolution | None = None
        self.schema: AuditfileSchema | None = None
        self.validate_during_load = False
        """True when the load phase must validate against the XSD itself (single-use source)."""

    @property
    def parts(self) -> tuple[SourcePart, ...]:
        return self._source.parts

    def prepare(self, sink_needs_row_counts: bool, scan_only: bool = False) -> bool:
        """Resolves the version and runs the scan phase when useful. Returns False when loading must not start."""
        verified = self._options.mode is ReadMode.VERIFIED
        want_scan = scan_only or verified or sink_needs_row_counts or len(self._source.parts) > 1

        if want_scan and self._source.can_read_twice:
            self._scan(verified)
            if self.diagnostics.has_fatal or (verified and self.diagnostics.has_errors):
                return False

            return not scan_only

        if want_scan:
            self.diagnostics.add("XAF-S005")

        self.validate_during_load = verified
        if not self._resolve_from_peek():
            return False

        r = self.resolution
        assert r is not None
        self.schema = AuditfileSchema._create(r.version, r.file_namespace, r.source, r.dialect.name if r.dialect else None, None)
        return not scan_only

    def load(self) -> Iterator[AuditfileRow]:
        """The load phase as a stream of rows. Diagnostics are collected along the way."""
        if self.schema is None or self.resolution is None:
            return

        document = self._pending
        self._pending = None
        if document is None:
            document = self._open(ConcatenatedStream(self._source.parts, not self._parts))
            if document is None:
                return

        try:
            validate = self._validator(document.stream) if self.validate_during_load else None
            observers: list[Any] = [ReferenceChecker(self.schema, self.diagnostics)]
            if self.resolution.version.type == "xaf":
                observers.append(XafRules(self.schema, self.diagnostics))

            loader = RowLoader(document.reader, self.schema, self.resolution, self._options, self.diagnostics, observers, validate)
            counts = self._row_counts
            for row in loader.rows():
                name = row.table.name
                counts[name] = counts.get(name, 0) + 1
                yield row

            if not loader.stopped:
                for observer in observers:
                    observer.on_end()

                self._check_tables(counts)
        finally:
            document.reader.close()

        if not self._parts:
            self._parts = list(document.stream.completed)

        self._loaded = True

    def create_report(self, loaded: bool) -> AuditfileReport:
        counts = self._row_counts if self._loaded else self._row_counts_from_schema()
        return AuditfileReport(self.schema, self._parts, self.diagnostics.items, counts, loaded, timedelta(seconds=time.perf_counter() - self._started))

    def dispose(self) -> None:
        """Releases a document kept for loading that will not be loaded after all."""
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.reader.close()

    def _row_counts_from_schema(self) -> dict[str, int]:
        tables = self.schema.tables if self.schema is not None else ()
        return {t.name: t.row_count for t in tables if t.row_count}

    def _scan(self, validate: bool) -> None:
        stream = ConcatenatedStream(self._source.parts, True)
        document = self._open(stream)
        if document is None:
            return

        reader = document.reader
        try:
            assert self.resolution is not None
            feed = self._validator(stream) if validate else None
            manifest = self.resolution.version.manifest
            tables_by_path = {t.path: t.name for t in manifest.tables}
            counts = {t.name: 0 for t in manifest.tables}
            paths: list[str | None] = []
            max_depth = self._options.max_depth
            namespace = manifest.namespace

            try:
                while (event := reader.next()) is not None:
                    if feed is not None:
                        feed(event)

                    if event.kind != START:
                        continue

                    depth = event.depth
                    if depth >= max_depth:
                        self.diagnostics.add("XAF-X003", Location(event.part, event.line, event.column, None), max=max_depth)
                        return

                    parent = paths[depth - 1] if depth > 0 else None
                    path = (event.local if depth == 0 else f"{parent}/{event.local}") if event.ns == namespace and (depth == 0 or parent is not None) else None
                    if len(paths) <= depth:
                        paths.append(path)
                    else:
                        paths[depth] = path

                    table = tables_by_path.get(path) if path is not None else None
                    if table is not None:
                        counts[table] += 1
            except (XmlSyntaxError, XmlEncodingError) as error:
                self._xml_error(error)
                return

            self._parts = list(stream.completed)
            self._check_tables(counts)
            r = self.resolution
            self.schema = AuditfileSchema._create(r.version, r.file_namespace, r.source, r.dialect.name if r.dialect else None, counts)
        finally:
            reader.close()

    def _xml_error(self, error: XmlSyntaxError | XmlEncodingError) -> None:
        if isinstance(error, XmlSyntaxError):
            self.diagnostics.add("XAF-X001", Location(error.part, error.line, error.column, None), detail=error.message)
        else:
            self.diagnostics.add("XAF-X004", NO_LOCATION, encoding=error.encoding)

    def _open(self, stream: ConcatenatedStream) -> _Document | None:
        """Opens the document and reads up to the root element, resolving the version on first use."""
        reader = XmlReader(stream)
        root = None
        try:
            while (event := reader.next()) is not None:
                if event.kind == START:
                    root = event
                    break

                if reader.position > _PEEK_LIMIT:
                    break

            if root is None:
                if reader.position > _PEEK_LIMIT:
                    self.diagnostics.add("XAF-X002", NO_LOCATION, part=self._source.parts[0].name, detail="The root element was not found within the first megabyte.")
                else:
                    self.diagnostics.add("XAF-X001", Location(1, None, None, None), detail="Root element is missing.")
        except (XmlSyntaxError, XmlEncodingError) as error:
            self._xml_error(error)
        except OSError as error:
            # A source that cannot be read is a finding; a later failure is the caller's error.
            if self.resolution is not None:
                reader.close()
                raise

            self.diagnostics.add("XAF-X002", NO_LOCATION, part=self._source.parts[0].name, detail=str(error))

        if root is None:
            reader.close()
            return None

        if self.resolution is None:
            self.resolution = resolve_version(root.local, root.ns, self._options, self.diagnostics)
            if self.resolution is None:
                reader.close()
                return None

        if self.resolution.needs_mapping:
            reader.map_namespace(self.resolution.file_namespace, self.resolution.version.namespace)

        reader.push_back(root)
        return _Document(stream, reader)

    def _resolve_from_peek(self) -> bool:
        if self.resolution is not None:
            return True

        document = self._open(ConcatenatedStream(self._source.parts, not self._source.can_read_twice))
        if document is None or self._source.can_read_twice:
            if document is not None:
                document.reader.close()

            return self.resolution is not None

        self._pending = document
        return True

    def _validator(self, stream: ConcatenatedStream) -> Callable[[Event], None]:
        """The XSD validator as an event consumer that reports XAF-D001."""
        assert self.resolution is not None
        version = self.resolution.version
        if self._options.xsd is None:
            schema_set = version.create_schema_set()
        else:
            data = _read_all(self._options.xsd())
            if hashlib.sha256(data).hexdigest() != version.manifest.xsd_sha256:
                self.diagnostics.add("XAF-D002", NO_LOCATION, version=version.key)

            schema_set = SchemaSet.compile("custom.xsd", lambda name: data if name == "custom.xsd" else None)

        part = [stream.part]

        def report(message: str, line: int | None, column: int | None) -> None:
            self.diagnostics.add("XAF-D001", Location(part[0], line, column, None), detail=message)

        validator = XsdValidator(schema_set, report)

        def feed(event: Event) -> None:
            part[0] = event.part
            if event.kind == START:
                validator.start(event)
            elif event.kind == END:
                validator.end(event)
            elif event.kind == TEXT:
                validator.text(event)

        return feed

    def _check_tables(self, counts: dict[str, int]) -> None:
        assert self.resolution is not None
        manifest = self.resolution.version.manifest
        for table in manifest.required_tables:
            if not counts.get(table):
                self.diagnostics.add("XAF-S001", NO_LOCATION, table=table)

        for table in manifest.expected_tables:
            if not counts.get(table):
                self.diagnostics.add("XAF-S002", NO_LOCATION, table=table)


def _read_all(stream: object) -> bytes:
    """Reads a custom XSD completely, refusing absurd sizes."""
    read = _reader(stream)
    chunks = []
    size = 0
    try:
        while data := read(65536):
            size += len(data)
            if size > _MAX_XSD_SIZE:
                raise ValueError("The XSD is larger than 16 MB.")

            chunks.append(data)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()

    return b"".join(chunks)
