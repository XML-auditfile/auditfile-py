"""One read of one source: resolve the version, optionally scan (validate and count), then load (stream rows)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

from ._checks import XAF, ReferenceChecker, XafRules
from ._streams import ConcatenatedStream, SourceError, _reader
from ._xml import END, START, TEXT, Event, XmlEncodingError, XmlReader, XmlSyntaxError
from .definitions import ColumnType, Dialect, DialectAction, DialectCorrection
from .diagnostics import NO_LOCATION, AuditfileReport, DiagnosticCollector, Location
from .options import CANCELLATION_INTERVAL, ReadMode, ReadOptions
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
    __slots__ = ("table", "is_value_table", "required")

    def __init__(self, table: SchemaTable) -> None:
        self.table = table
        # A repeating simple element: one "value" column read from the element text.
        self.is_value_table = len(table.columns) == 1 and table.columns[0].path == "."
        self.required: list[tuple[SchemaColumn, _PathNode | None]] = []
        """Columns with min_occurs >= 1, each with the group it is in (None for a direct child or attribute)."""


class _PathNode:
    """A known element or attribute path."""

    __slots__ = ("path", "table", "column", "is_group", "children", "attributes")

    def __init__(self, path: str) -> None:
        self.path = path
        """The full path, e.g. "auditfile/company/transactions"; attributes as ".../@name"."""
        self.table: _TablePlan | None = None
        self.column: tuple[_TablePlan, SchemaColumn] | None = None
        self.is_group = False
        """A flattened element or pure container: no table and no column."""
        self.children: dict[str, _PathNode] = {}
        self.attributes: dict[str, _PathNode] = {}

    def child_path(self, local: str) -> str:
        """The path of an unknown child, for a diagnostic."""
        return local if not self.path else f"{self.path}/{local}"


class MappingPlan:
    """
    The paths of an import as a tree, built once per import: every node knows its table, column or group.
    Reading an element is then one lookup of its local name under the current node, instead of building the
    full path as a string and looking that up. Only known paths have a node, so a file cannot make the tree grow.
    """

    def __init__(self, schema: AuditfileSchema) -> None:
        self.root = _PathNode("")
        """Above the root element: its only child is the root element of the version."""
        self._nodes: dict[str, _PathNode] = {}
        plans = []
        for table in schema.tables:
            plan = _TablePlan(table)
            plans.append(plan)
            self._node(table.path).table = plan
            for column in table.columns:
                if column.path != ".":
                    self._node(f"{table.path}/{column.path}").column = (plan, column)

        for node in self._nodes.values():
            node.is_group = node.table is None and node.column is None

        for plan in plans:
            for column in plan.table.columns:
                if column.min_occurs >= 1 and column.path != ".":
                    slash = column.path.rfind("/")
                    group = self._nodes[f"{plan.table.path}/{column.path[:slash]}"] if slash > 0 else None
                    plan.required.append((column, group))

    def _node(self, path: str) -> _PathNode:
        node = self._nodes.get(path)
        if node is not None:
            return node

        slash = path.rfind("/")
        parent = self.root if slash < 0 else self._node(path[:slash])
        name = path[slash + 1 :]
        node = _PathNode(path)
        if name.startswith("@"):
            parent.attributes[name[1:]] = node
        else:
            parent.children[name] = node

        self._nodes[path] = node
        return node


class _Frame:
    """An open table element."""

    __slots__ = ("plan", "id", "parent_id", "ordinal", "part", "node", "values", "present", "groups", "_ordinals")

    def __init__(self, plan: _TablePlan, id_: int, parent_id: int | None, ordinal: int, part: int, node: _PathNode) -> None:
        self.plan = plan
        self.id = id_
        self.parent_id = parent_id
        self.ordinal = ordinal
        self.part = part
        self.node = node
        self.values: list[object] = [None] * len(plan.table.columns)
        self.present: list[bool] = [False] * len(plan.table.columns)
        """Columns that occurred, also when their value was invalid."""
        self.groups: set[_PathNode] = set()
        """Groups that occurred within this row."""
        self._ordinals: dict[int, int] | None = None

    def next_ordinal(self, child: _TablePlan) -> int:
        if self._ordinals is None:
            self._ordinals = {}

        value = self._ordinals.get(id(child), 0) + 1
        self._ordinals[id(child)] = value
        return value


class _Capture:
    __slots__ = ("frame", "column", "path", "text", "length", "emit")

    def __init__(self, frame: _Frame, column: SchemaColumn, path: str, emit: bool) -> None:
        self.frame = frame
        self.column = column
        self.path = path
        self.text: list[str] = []
        self.length = 0
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
        self._nodes: list[_PathNode] = []
        """The open elements."""
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
        # Without the XSD nobody else notices a missing required field; with it, XAF-D001 already does.
        self._check_required = options.mode is ReadMode.UNVERIFIED

    def rows(self) -> Iterator[AuditfileRow]:
        """Rows in document order of their closing tags."""
        reader = self._reader
        validate = self._validate
        events = 0
        while True:
            if events % CANCELLATION_INTERVAL == 0:
                self._options.check_cancelled()

            events += 1
            try:
                event = reader.next()
            except XmlSyntaxError as error:
                self._diagnostics.add("XAF-X001", Location(error.part, error.line, error.column, self._current_path), detail=error.message)
                self.stopped = True
                return
            except XmlEncodingError as error:
                self._diagnostics.add("XAF-X004", Location(error.part, None, None, self._current_path), encoding=error.encoding)
                self.stopped = True
                return
            except SourceError as error:
                self._diagnostics.add("XAF-X002", Location(None, None, None, self._current_path), part=error.part, detail=str(error))
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
                # Beyond the maximum the value is only counted, never kept.
                self._capture.length += len(event.text)
                if self._capture.length <= self._options.max_value_length:
                    self._capture.text.append(event.text)

            if self._ready:
                yield from self._ready
                self._ready.clear()

            if self.stopped:
                return

    @property
    def _current_path(self) -> str | None:
        return self._nodes[-1].path if self._nodes else None

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

        current = self._nodes[-1] if self._nodes else self._plan.root
        node = current.children.get(event.local) if event.ns == self._namespace else None
        if node is None:
            self._skip(current.child_path(event.local), event)
            return

        top = self._frames[-1] if self._frames else None
        if node.table is not None:
            self._start_table(node, node.table, top, event)
        elif node.column is not None and top is not None and top.plan is node.column[0]:
            self._capture = _Capture(top, node.column[1], node.path, False)
        elif node.is_group:
            if top is not None:
                top.groups.add(node)
                self._read_attributes(node, top, event)

            self._nodes.append(node)
        else:
            self._skip(node.path, event)

    def _start_table(self, node: _PathNode, table: _TablePlan, top: _Frame | None, event: Event) -> None:
        self._next_id += 1
        frame = _Frame(table, self._next_id, top.id if top else None, top.next_ordinal(table) if top else 1, event.part, node)
        self._read_attributes(node, frame, event)
        if table.is_value_table:
            self._capture = _Capture(frame, table.table.columns[0], node.path, True)
            return

        self._frames.append(frame)
        self._nodes.append(node)

    def _end(self, event: Event) -> None:
        if self._skip_depth is not None:
            if event.depth == self._skip_depth:
                self._skip_depth = None

            return

        capture = self._capture
        if capture is not None:
            self._capture = None
            if capture.length > self._options.max_value_length:
                capture.frame.present[capture.column.index] = True
                self._diagnostics.add(
                    "XAF-S008", self._here(capture.path), table=capture.frame.plan.table.name, column=capture.column.name,
                    length=capture.length, max=self._options.max_value_length,
                )
            else:
                self._set_value(capture.frame, capture.column, "".join(capture.text), capture.path)
            if capture.emit:
                self._emit(capture.frame)

            return

        if not self._nodes:
            return

        node = self._nodes.pop()
        if self._frames and self._frames[-1].node is node:
            self._emit(self._frames.pop())

    def _skip(self, path: str, event: Event) -> None:
        key = "S003:" + path
        if key not in self._reported:
            self._reported.add(key)
            self._diagnostics.add("XAF-S003", self._here(path), path=path)

        self._skip_depth = event.depth

    def _read_attributes(self, element: _PathNode, frame: _Frame, event: Event) -> None:
        for ns, local, value in event.attributes:
            if ns in (_XMLNS, _XSI) or local == "xmlns":
                continue

            attribute = element.attributes.get(local)
            if attribute is not None and attribute.column is not None and attribute.column[0] is frame.plan:
                self._set_value(frame, attribute.column[1], value, attribute.path)

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

        frame.present[column.index] = True
        if path == self._software_path and isinstance(value, str) and self._resolution.use_software(value, self._options.apply_dialects):
            self._use_dialect(self._resolution.dialect)

    def _correct(self, raw: str, corrections: list[DialectCorrection], column: SchemaColumn, path: str) -> str:
        for correction in corrections:
            corrected = raw
            if correction.action is DialectAction.DECIMAL_COMMA and "," in raw and "." not in raw:
                corrected = raw.replace(",", ".")
            elif correction.action is DialectAction.DATE_FROM_DATE_TIME and column.type is ColumnType.DATE and len(raw.strip()) > 10:
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
            if correction.action is DialectAction.REPLACE_NAMESPACE:
                continue

            for path in correction.paths:
                self._corrections.setdefault(path, []).append(correction)

    def _check_required_columns(self, frame: _Frame) -> None:
        """
        XAF-S006 for each required column the row lacks. min_occurs is relative to the column's XML parent:
        a column inside an optional group is only required when that group occurs.
        """
        for column, group in frame.plan.required:
            if not frame.present[column.index] and (group is None or group in frame.groups):
                self._diagnostics.add("XAF-S006", self._here(frame.node.path), column=column.name, table=frame.plan.table.name)

    def _emit(self, frame: _Frame) -> None:
        if self._check_required:
            self._check_required_columns(frame)

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
        self._load_started = False
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
        """The load phase as a stream of rows. Diagnostics are collected along the way. Can be iterated once."""
        # A second iteration would read the file again and add to the same counts and diagnostics.
        if self._load_started:
            raise RuntimeError("The rows of an audit file can be iterated only once; collect them in a list to use them twice.")

        self._load_started = True
        if self.schema is None or self.resolution is None:
            return

        document = self._pending
        self._pending = None
        if document is None:
            document = self._open(ConcatenatedStream(self._source.parts, not self._parts))
            if document is None:
                return

        try:
            validate = None
            if self.validate_during_load:
                validate = self._validator(document.stream)
                if validate is None:
                    return

            observers: list[Any] = [ReferenceChecker(self.schema, self.diagnostics)]
            if self.resolution.version.type == XAF:
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

        # Without a scan phase a dialect recognized by the software description is only known now; the report names it.
        r = self.resolution
        if (r.dialect.name if r.dialect else None) != self.schema.dialect:
            self.schema = AuditfileSchema._create(
                r.version, r.file_namespace, r.source, r.dialect.name if r.dialect else None,
                self._row_counts_from_schema() if self.schema.has_row_counts else None,
            )

        self._loaded = True

    @property
    def loaded(self) -> bool:
        """True when the load phase has read the whole file."""
        return self._loaded

    def create_report(self, loaded: bool) -> AuditfileReport:
        counts = self._row_counts if self._loaded else self._row_counts_from_schema()
        return AuditfileReport(
            self.schema, self._parts, self.diagnostics.items, counts, loaded, timedelta(seconds=time.perf_counter() - self._started),
            counts=self.diagnostics.counts, truncated=self.diagnostics.is_truncated,
        )

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
            feed = None
            if validate:
                feed = self._validator(stream)
                if feed is None:
                    return

            try:
                counts = self._count_rows(reader, feed)
                if counts is None:
                    return
            except (XmlSyntaxError, XmlEncodingError, SourceError) as error:
                self._xml_error(error)
                return

            self._parts = list(stream.completed)
            self._check_tables(counts)
            r = self.resolution
            self.schema = AuditfileSchema._create(r.version, r.file_namespace, r.source, r.dialect.name if r.dialect else None, counts)
        finally:
            reader.close()

    def _count_rows(self, reader: XmlReader, feed: Callable[[Event], None] | None) -> dict[str, int] | None:
        """
        Walks the document (feeding the XSD validator, when validating) and counts the rows per table.
        Recognizes a dialect by the software description on the way. Returns None after XAF-X003.
        """
        r = self.resolution
        assert r is not None
        plan = MappingPlan(AuditfileSchema._create(r.version, r.file_namespace, r.source, None, None))
        root = plan.root.children.get(r.version.root_element)
        header = root.children.get("header") if root is not None else None
        software_node = header.children.get("softwareDesc") if header is not None else None
        counts = {t.name: 0 for t in r.version.manifest.tables}
        nodes: list[_PathNode | None] = []
        max_depth = self._options.max_depth
        namespace = r.version.namespace
        software: list[str] | None = None
        software_length = 0
        software_depth = -1
        events = 0

        while (event := reader.next()) is not None:
            if events % CANCELLATION_INTERVAL == 0:
                self._options.check_cancelled()

            events += 1
            if feed is not None:
                feed(event)

            # The dialect is recognized here already, so the schema handed to the sink names it.
            if software is not None:
                if event.kind == TEXT and software_length <= self._options.max_value_length:
                    software.append(event.text)
                    software_length += len(event.text)
                elif event.kind == END and event.depth == software_depth:
                    r.use_software("".join(software), self._options.apply_dialects)
                    software = None

            if event.kind != START:
                continue

            depth = event.depth
            if depth >= max_depth:
                self.diagnostics.add("XAF-X003", Location(event.part, event.line, event.column, None), max=max_depth)
                return None

            parent = nodes[depth - 1] if depth > 0 else plan.root
            node = parent.children.get(event.local) if parent is not None and event.ns == namespace else None
            if len(nodes) <= depth:
                nodes.append(node)
            else:
                nodes[depth] = node

            if node is None:
                continue

            if node.table is not None:
                counts[node.table.table.name] += 1
            elif node is software_node and r.dialect is None:
                software = []
                software_length = 0
                software_depth = depth

        return counts

    def _xml_error(self, error: XmlSyntaxError | XmlEncodingError | SourceError) -> None:
        if isinstance(error, SourceError):
            self.diagnostics.add("XAF-X002", NO_LOCATION, part=error.part, detail=str(error))
        elif isinstance(error, XmlSyntaxError):
            self.diagnostics.add("XAF-X001", Location(error.part, error.line, error.column, None), detail=error.message)
        else:
            self.diagnostics.add("XAF-X004", Location(error.part, None, None, None), encoding=error.encoding)

    def _open(self, stream: ConcatenatedStream) -> _Document | None:
        """Opens the document and reads up to the root element, resolving the version on first use."""
        reader = XmlReader(stream, self._options.max_characters_in_document)
        root = None
        try:
            while (event := reader.next()) is not None:
                if event.kind == START:
                    root = event if reader.root_offset is None or reader.root_offset < _PEEK_LIMIT else None
                    break

                # Only while the root has not been parsed yet: a source may hand over a large part at once.
                if reader.position > _PEEK_LIMIT and reader.root_offset is None:
                    break

            if root is None:
                if reader.position > _PEEK_LIMIT:
                    self.diagnostics.add("XAF-X002", NO_LOCATION, part=self._source.parts[0].name, detail="The root element was not found within the first megabyte.")
                else:
                    self.diagnostics.add("XAF-X001", Location(1, None, None, None), detail="Root element is missing.")
        except (XmlSyntaxError, XmlEncodingError, SourceError) as error:
            # A source that cannot be read is a finding (X002); a wrong argument is the caller's error and is raised.
            self._xml_error(error)

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

    def _validator(self, stream: ConcatenatedStream) -> Callable[[Event], None] | None:
        """The XSD validator as an event consumer that reports XAF-D001; None (after XAF-X002) when a custom XSD fails."""
        assert self.resolution is not None
        version = self.resolution.version
        if self._options.xsd is None:
            schema_set = version.create_schema_set()
        else:
            try:
                data = _read_all(self._options.xsd())
                if hashlib.sha256(data).hexdigest() != version.manifest.xsd_sha256:
                    self.diagnostics.add("XAF-D002", NO_LOCATION, version=version.key)

                schema_set = SchemaSet.compile("custom.xsd", lambda name: data if name == "custom.xsd" else None)
            except Exception as error:
                self.diagnostics.add("XAF-X002", NO_LOCATION, part="XSD", detail=str(error))
                return None

        part = [stream.part]

        def report(message: str, line: int | None, column: int | None) -> None:
            self.diagnostics.add("XAF-D001", Location(part[0], line, column, None), detail=message)

        # Keys and references are checked by the ReferenceChecker (XAF-R003, XAF-R004) from the manifest, which holds every
        # constraint of the XSD. The XSD's own identity constraints use XPaths without a namespace prefix, so in a namespaced
        # document they never match; evaluating them only costs time.
        validator = XsdValidator(schema_set, report, self._options.max_value_length, identity_constraints=False)

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
