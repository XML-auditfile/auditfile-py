"""Sinks receive an import; a store gives one back."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import IntFlag
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .diagnostics import AuditfileReport
    from .rows import AuditfileRow
    from .schema import AuditfileSchema, SchemaTable
    from .source import SourcePart


class SinkCapabilities(IntFlag):
    """What a sink needs from the reader."""

    NONE = 0
    """Nothing special."""

    NEEDS_ROW_COUNTS = 1
    """The sink wants row counts before the first row (e.g. to pre-allocate); the reader scans first."""


class ImportContext:
    """Information at the start of an import."""

    __slots__ = ("schema", "parts")

    def __init__(self, schema: AuditfileSchema, parts: Sequence[SourcePart]) -> None:
        self.schema = schema
        """The import schema."""
        self.parts = tuple(parts)
        """The source parts."""


@runtime_checkable
class Sink(Protocol):
    """
    Receives an import: first the schema, then rows per table in batches, then completion or abort.
    All validation, merging and reporting happens before the sink, so a custom sink gets the same result as a built-in one.
    A ``capabilities`` attribute (SinkCapabilities) is optional.
    """

    def begin(self, context: ImportContext) -> None:
        """Start of an import. The schema contains row counts when the reader scanned first."""

    def write(self, table: SchemaTable, rows: Sequence[AuditfileRow]) -> None:
        """A batch of rows of one table. Child rows can arrive before their parent row; ids are already assigned."""

    def complete(self, report: AuditfileReport) -> None:
        """The import is complete; commit."""

    def abort(self, report: AuditfileReport) -> None:
        """The import failed; discard everything received since begin."""


class AuditfileStore(Protocol):
    """
    A stored import, e.g. in a database. Gives back the schema and the rows per table,
    so code can work with an import regardless of where it is stored. Implemented by connectors.
    """

    def get_schema(self) -> AuditfileSchema:
        """The schema of the stored import."""

    def read_rows(self, table: str, on_batch: Callable[[Sequence[AuditfileRow]], None]) -> None:
        """Reads the rows of one table in batches, in id order."""


class NullSink:
    """Discards everything; used for validation."""

    capabilities = SinkCapabilities.NONE

    def begin(self, context: ImportContext) -> None:
        pass

    def write(self, table: SchemaTable, rows: Sequence[AuditfileRow]) -> None:
        pass

    def complete(self, report: AuditfileReport) -> None:
        pass

    def abort(self, report: AuditfileReport) -> None:
        pass


def check_sink(sink: object) -> Sink:
    for method in ("begin", "write", "complete", "abort"):
        if not callable(getattr(sink, method, None)):
            raise TypeError(f"sink must have a {method} method.")

    return sink  # type: ignore[return-value]
