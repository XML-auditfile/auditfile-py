"""Streaming reader for Dutch tax audit files (XAF and related), following auditfile-spec."""

from ._json import JsonFormatError
from .auditfile import Auditfile, AuditfileReader, SinkAbortError
from .custom import DialectRegistry, ViewRegistry
from .definitions import (
    AuditfileManifest,
    ColumnType,
    Dialect,
    DialectAction,
    DialectCorrection,
    ElementDefinition,
    LocalizedText,
    ManifestColumn,
    ManifestKey,
    ManifestReference,
    ManifestTable,
    TextLanguage,
    ViewAggregate,
    ViewColumn,
    ViewDefinition,
    ViewJoin,
    ViewJoinCondition,
    ViewOperator,
    ViewParameter,
    ViewValue,
)
from .diagnostics import AuditfileReport, Diagnostic, DiagnosticSeverity
from .memory import MemorySink
from .options import ReadCancelledError, ReadMode, ReadOptions, ReadProgress
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaColumn, SchemaTable, VersionSource
from .sinks import ImportContext, Sink, SinkCapabilities
from .source import AuditfileSource, PartInfo, SourcePart
from .versions import AuditfileVersion
from .views import ViewResult
from .xsd import XsdError

__all__ = [
    "Auditfile",
    "AuditfileManifest",
    "AuditfileReader",
    "AuditfileReport",
    "AuditfileRow",
    "AuditfileSchema",
    "AuditfileSource",
    "AuditfileVersion",
    "ColumnType",
    "Diagnostic",
    "DiagnosticSeverity",
    "Dialect",
    "DialectAction",
    "DialectCorrection",
    "DialectRegistry",
    "ElementDefinition",
    "ImportContext",
    "JsonFormatError",
    "LocalizedText",
    "ManifestColumn",
    "ManifestKey",
    "ManifestReference",
    "ManifestTable",
    "MemorySink",
    "PartInfo",
    "ReadCancelledError",
    "ReadMode",
    "ReadOptions",
    "ReadProgress",
    "SchemaColumn",
    "SchemaTable",
    "Sink",
    "SinkAbortError",
    "SinkCapabilities",
    "SourcePart",
    "TextLanguage",
    "VersionSource",
    "ViewAggregate",
    "ViewColumn",
    "ViewDefinition",
    "ViewJoin",
    "ViewJoinCondition",
    "ViewOperator",
    "ViewParameter",
    "ViewRegistry",
    "ViewResult",
    "ViewValue",
    "XsdError",
]
