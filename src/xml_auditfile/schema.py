"""The schema of one import: the contract everything after the reader builds on."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import TYPE_CHECKING

from . import _json as j
from .definitions import ColumnType, ElementDefinition, ManifestKey, ManifestReference, _column_type

if TYPE_CHECKING:
    from .versions import AuditfileVersion


class VersionSource(str, Enum):
    """How the version of an import was determined."""

    OFFICIAL = "official"
    """The file uses the official namespace."""

    ALIAS = "alias"
    """The file uses a known alias namespace."""

    FORCED = "forced"
    """The caller forced the version."""

    RESOLVED = "resolved"
    """The resolver derived the version from the namespace."""


class SchemaColumn:
    """A column of an import table."""

    def __init__(
        self, name: str, xml_name: str, path: str, kind: str, type_: ColumnType, xsd_type: str, min_occurs: int, index: int,
        definition: ElementDefinition | None,
    ) -> None:
        self.name = name
        """Column name."""
        self.xml_name = xml_name
        """Element or attribute name."""
        self.path = path
        """Path relative to the table element."""
        self.kind = kind
        """"element" or "attribute"."""
        self.type = type_
        """Logical type; determines the Python type of the value."""
        self.xsd_type = xsd_type
        """XSD type name."""
        self.min_occurs = min_occurs
        """Minimum occurrences relative to the XML parent element."""
        self.index = index
        """Position in AuditfileRow.values."""
        self.definition = definition
        """Documentation, when available."""

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"SchemaColumn({self.name!r}, {self.type.value})"


class SchemaTable:
    """A table of an import."""

    def __init__(
        self, name: str, xml_name: str, path: str, parent: str | None, min_occurs: int, max_occurs: int | None,
        is_required: bool, is_expected: bool, row_count: int | None, index: int, columns: Iterable[SchemaColumn],
        keys: Iterable[ManifestKey], references: Iterable[ManifestReference], definition: ElementDefinition | None,
    ) -> None:
        self.name = name
        """Table name, e.g. "tr_line"."""
        self.xml_name = xml_name
        """Element name, e.g. "trLine"."""
        self.path = path
        """Full XML path."""
        self.parent = parent
        """Parent table name, or None for the root."""
        self.min_occurs = min_occurs
        """Minimum occurrences relative to the XML parent element."""
        self.max_occurs = max_occurs
        """Maximum occurrences relative to the XML parent element; None means unbounded."""
        self.is_required = is_required
        """Required by the standard."""
        self.is_expected = is_expected
        """Expected in practice."""
        self.row_count = row_count
        """Number of rows, when known from the scan phase."""
        self.index = index
        """Position in the schema."""
        self.columns: tuple[SchemaColumn, ...] = tuple(columns)
        """Columns; AuditfileRow.values uses the same order."""
        self.keys: tuple[ManifestKey, ...] = tuple(keys)
        """Natural keys."""
        self.references: tuple[ManifestReference, ...] = tuple(references)
        """References to keys of other tables."""
        self.definition = definition
        """Documentation, when available."""
        self._by_name = {c.name: c for c in self.columns}

    def find_column(self, name: str) -> SchemaColumn | None:
        """Finds a column by name, or returns None."""
        return self._by_name.get(name)

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"SchemaTable({self.name!r})"


class AuditfileSchema:
    """
    The schema of one import: tables, columns, types, keys, references, row counts and origin.
    Converts to and from JSON without loss (definitions are re-attached from the version when known).
    """

    def __init__(
        self, version_key: str, namespace: str, source: VersionSource, dialect: str | None, has_row_counts: bool, tables: Iterable[SchemaTable]
    ) -> None:
        from .versions import AuditfileVersion

        self.version_key = version_key
        """Version key, e.g. "xaf/3.2"."""
        self.version: AuditfileVersion | None = AuditfileVersion.find(version_key)
        """The version, or None when this reader does not know it (schema loaded from JSON)."""
        self.namespace = namespace
        """Namespace as found in the file."""
        self.source = source
        """How the version was determined."""
        self.dialect = dialect
        """Name of the applied dialect, or None."""
        self.has_row_counts = has_row_counts
        """True when the row count is known for every table."""
        self.tables: tuple[SchemaTable, ...] = tuple(tables)
        """All tables in document order."""
        self._by_name = {t.name: t for t in self.tables}

    def find_table(self, name: str) -> SchemaTable | None:
        """Finds a table by name, or returns None."""
        return self._by_name.get(name)

    def table(self, name: str) -> SchemaTable:
        """Returns a table by name. :raises KeyError: The table does not exist."""
        table = self._by_name.get(name)
        if table is None:
            raise KeyError(f"Table '{name}' does not exist.")

        return table

    def to_json(self) -> str:
        """Serializes the schema to compact JSON, e.g. for storage in a database."""
        return json.dumps(
            {
                "formatVersion": 1,
                "version": self.version_key,
                "namespace": self.namespace,
                "source": self.source.value,
                "dialect": self.dialect,
                "hasRowCounts": self.has_row_counts,
                "tables": [
                    {
                        "name": t.name,
                        "xmlName": t.xml_name,
                        "path": t.path,
                        "parent": t.parent,
                        "minOccurs": t.min_occurs,
                        "maxOccurs": t.max_occurs,
                        "required": t.is_required,
                        "expected": t.is_expected,
                        "rowCount": t.row_count,
                        "keys": [{"name": k.name, "columns": list(k.columns)} for k in t.keys],
                        "references": [
                            {"name": r.name, "columns": list(r.columns), "table": r.table, "key": r.key} for r in t.references
                        ],
                        "columns": [
                            {
                                "name": c.name,
                                "xmlName": c.xml_name,
                                "path": c.path,
                                "kind": c.kind,
                                "type": c.type.value,
                                "xsdType": c.xsd_type,
                                "minOccurs": c.min_occurs,
                            }
                            for c in t.columns
                        ],
                    }
                    for t in self.tables
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def from_json(text: str) -> AuditfileSchema:
        """Reads a schema written by :meth:`to_json`. :raises ValueError: The JSON is not a valid schema."""
        from .versions import AuditfileVersion

        if not isinstance(text, str):
            raise TypeError("json must be a str.")

        root = j.parse_object(text, "Schema JSON")
        version_key = j.string(root, "version")
        version = AuditfileVersion.find(version_key)
        definitions: Mapping[str, ElementDefinition] = version.definitions if version is not None else {}

        tables = []
        for index, t in enumerate(j.objects(root, "tables")):
            name = j.string(t, "name")
            columns = [
                SchemaColumn(
                    j.string(c, "name"), j.string(c, "xmlName"), j.string(c, "path"), j.string(c, "kind"),
                    _column_type(j.string(c, "type")), j.string(c, "xsdType"), j.integer(c, "minOccurs"), i,
                    definitions.get(f"{name}.{j.string(c, 'name')}"),
                )
                for i, c in enumerate(j.objects(t, "columns"))
            ]
            tables.append(
                SchemaTable(
                    name, j.string(t, "xmlName"), j.string(t, "path"), j.optional_string(t, "parent"),
                    j.integer(t, "minOccurs"), j.optional_integer(t, "maxOccurs"), j.boolean(t, "required"), j.boolean(t, "expected"),
                    j.optional_integer(t, "rowCount"), index, columns,
                    [ManifestKey(j.string(k, "name"), j.strings(k, "columns")) for k in j.objects(t, "keys")],
                    [
                        ManifestReference(j.string(r, "name"), j.strings(r, "columns"), j.string(r, "table"), j.string(r, "key"))
                        for r in j.objects(t, "references")
                    ],
                    definitions.get(name),
                )
            )

        try:
            source = VersionSource(j.string(root, "source"))
        except ValueError:
            source = VersionSource.OFFICIAL

        return AuditfileSchema(
            version_key, j.string(root, "namespace"), source, j.optional_string(root, "dialect"), j.boolean(root, "hasRowCounts"), tables
        )

    @staticmethod
    def _create(
        version: AuditfileVersion, namespace: str, source: VersionSource, dialect: str | None, row_counts: Mapping[str, int] | None
    ) -> AuditfileSchema:
        manifest = version.manifest
        definitions = version.definitions
        required = set(manifest.required_tables)
        expected = set(manifest.expected_tables)

        tables = [
            SchemaTable(
                t.name, t.xml_name, t.path, t.parent, t.min_occurs, t.max_occurs, t.name in required, t.name in expected,
                None if row_counts is None else row_counts.get(t.name, 0), t.index,
                [
                    SchemaColumn(c.name, c.xml_name, c.path, c.kind, c.type, c.xsd_type, c.min_occurs, c.index, definitions.get(f"{t.name}.{c.name}"))
                    for c in t.columns
                ],
                t.keys, t.references, definitions.get(t.name),
            )
            for t in manifest.tables
        ]

        return AuditfileSchema(version.key, namespace, source, dialect, row_counts is not None, tables)
