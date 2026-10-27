"""Streaming reader for Dutch tax audit files (XAF and related), following auditfile-spec."""

from ._json import JsonFormatError
from .auditfile import Auditfile, AuditfileReader
from .definitions import (
    AuditfileManifest,
    ColumnType,
    Dialect,
    DialectCorrection,
    ElementDefinition,
    LocalizedText,
    ManifestColumn,
    ManifestKey,
    ManifestReference,
    ManifestTable,
    TextLanguage,
    ViewColumn,
    ViewDefinition,
    ViewJoin,
    ViewJoinCondition,
    ViewParameter,
    ViewValue,
)
from .diagnostics import AuditfileReport, Diagnostic, DiagnosticSeverity
from .memory import MemorySink
from .options import ReadMode, ReadOptions, ReadProgress
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaColumn, SchemaTable, VersionSource
from .sinks import AuditfileStore, ImportContext, Sink, SinkCapabilities
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
    "AuditfileStore",
    "AuditfileVersion",
    "ColumnType",
    "Diagnostic",
    "DiagnosticSeverity",
    "Dialect",
    "DialectCorrection",
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
    "ReadMode",
    "ReadOptions",
    "ReadProgress",
    "SchemaColumn",
    "SchemaTable",
    "Sink",
    "SinkCapabilities",
    "SourcePart",
    "TextLanguage",
    "VersionSource",
    "ViewColumn",
    "ViewDefinition",
    "ViewJoin",
    "ViewJoinCondition",
    "ViewParameter",
    "ViewResult",
    "ViewValue",
    "XsdError",
]
