"""Definitions from auditfile-spec: manifests, documentation, dialects and views."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from . import _json as j
from ._resources import exists, read_json

if TYPE_CHECKING:
    from .rows import AuditfileRow
    from .schema import AuditfileSchema
    from .views import ViewResult


class TextLanguage(str, Enum):
    """Language for definitions and diagnostic messages."""

    DUTCH = "nl"
    """Dutch: the official language of the Belastingdienst publications."""

    ENGLISH = "en"
    """English; falls back to Dutch where no translation exists."""


class LocalizedText:
    """A text in Dutch and English. English falls back to Dutch when empty."""

    __slots__ = ("dutch", "english")

    def __init__(self, dutch: str | None, english: str | None) -> None:
        self.dutch = dutch or None
        self.english = english or None

    def get(self, language: TextLanguage = TextLanguage.DUTCH) -> str | None:
        """The text in the requested language, falling back to the other language."""
        if TextLanguage(language) is TextLanguage.ENGLISH:
            return self.english or self.dutch

        return self.dutch or self.english

    def __str__(self) -> str:
        return self.get(TextLanguage.DUTCH) or ""

    def __repr__(self) -> str:
        return f"LocalizedText(dutch={self.dutch!r}, english={self.english!r})"

    @staticmethod
    def _from(json: dict[str, Any] | None) -> LocalizedText | None:
        return None if json is None else LocalizedText(j.optional_string(json, "nl"), j.optional_string(json, "en"))


class ColumnType(str, Enum):
    """Logical type of a column; the values are the type names of auditfile-spec."""

    STRING = "string"
    """Text (str)."""

    DECIMAL = "decimal"
    """Exact decimal number (decimal.Decimal)."""

    INTEGER = "integer"
    """Whole number (int, within the range of a 64-bit integer)."""

    DATE = "date"
    """Date without time (datetime.date)."""

    DATETIME = "datetime"
    """Date and time (datetime.datetime; aware when the file gives a time zone)."""

    TIME = "time"
    """Time of day (datetime.time)."""

    BOOLEAN = "boolean"
    """True or false (bool)."""


def _column_type(value: str) -> ColumnType:
    try:
        return ColumnType(value)
    except ValueError:
        raise j.JsonFormatError(f"Unknown column type '{value}'.") from None


class ManifestKey:
    """A natural key: the column values are unique within an import."""

    __slots__ = ("name", "columns")

    def __init__(self, name: str, columns: Iterable[str]) -> None:
        self.name = name
        """Key name from the XSD."""
        self.columns: tuple[str, ...] = tuple(columns)
        """Key columns."""


class ManifestReference:
    """A reference from columns of one table to a key of another table."""

    __slots__ = ("name", "columns", "table", "key")

    def __init__(self, name: str, columns: Iterable[str], table: str, key: str) -> None:
        self.name = name
        """Reference name from the XSD."""
        self.columns: tuple[str, ...] = tuple(columns)
        """Referencing columns."""
        self.table = table
        """Referenced table."""
        self.key = key
        """Referenced key."""


class ManifestColumn:
    """A column of a manifest table."""

    def __init__(self, json: dict[str, Any], index: int) -> None:
        self.index = index
        """Position within the table."""
        self.name = j.string(json, "name")
        """Column name, e.g. "amnt"."""
        self.xml_name = j.string(json, "xmlName")
        """Element or attribute name."""
        self.path = j.string(json, "path")
        """Path relative to the table element: "amnt", "currency/curCode", "@attr" or "."."""
        self.kind = j.string(json, "kind")
        """"element" or "attribute"."""
        self.type = _column_type(j.string(json, "type"))
        """Logical type."""
        self.xsd_type = j.string(json, "xsdType")
        """Name of the XSD type."""
        self.min_occurs = j.integer(json, "minOccurs")
        """Minimum occurrences relative to the XML parent element."""


class ManifestTable:
    """A table of the manifest."""

    def __init__(self, json: dict[str, Any], index: int) -> None:
        self.index = index
        """Position in the manifest."""
        self.name = j.string(json, "name")
        """Table name, e.g. "tr_line"."""
        self.xml_name = j.string(json, "xmlName")
        """Element name, e.g. "trLine"."""
        self.path = j.string(json, "path")
        """Full XML path, e.g. "auditfile/company/transactions/journal/transaction/trLine"."""
        self.parent = j.optional_string(json, "parent")
        """Name of the parent table, or None for the root."""
        self.min_occurs = j.integer(json, "minOccurs")
        """Minimum occurrences relative to the XML parent element."""
        self.max_occurs = j.optional_integer(json, "maxOccurs")
        """Maximum occurrences relative to the XML parent element; None means unbounded."""
        self.keys = tuple(ManifestKey(j.string(k, "name"), j.strings(k, "columns")) for k in j.objects(json, "keys"))
        """Natural keys."""
        self.references = tuple(
            ManifestReference(j.string(r, "name"), j.strings(r, "columns"), j.string(r, "table"), j.string(r, "key"))
            for r in j.objects(json, "references")
        )
        """References to keys of other tables."""
        self.columns = tuple(ManifestColumn(c, i) for i, c in enumerate(j.objects(json, "columns")))
        """Columns in XSD order."""


class AuditfileManifest:
    """The structure of one audit file version, from manifest.json."""

    def __init__(self, json: dict[str, Any]) -> None:
        self.format_version = j.integer(json, "formatVersion")
        """Format version of manifest.json."""
        if self.format_version != 1:
            raise NotImplementedError(f"Manifest format version {self.format_version} is not supported by this reader.")

        self.standard = j.string(json, "standard")
        """Audit file type, e.g. "xaf"."""
        self.version = j.string(json, "version")
        """Version, e.g. "3.2"."""
        self.namespace = j.string(json, "namespace")
        """Official namespace."""
        self.root_element = j.string(json, "rootElement")
        """Name of the root element."""
        xsd = j.obj(json, "xsd")
        self.xsd_sha256 = (j.optional_string(xsd, "sha256") if xsd is not None else None) or ""
        """SHA-256 of the official XSD."""
        self.required_tables = tuple(j.strings(json, "requiredTables"))
        """Tables required by the XSD."""
        self.expected_tables = tuple(j.strings(json, "expectedTables"))
        """Tables optional in the XSD but expected in practice."""
        self.tables = tuple(ManifestTable(t, i) for i, t in enumerate(j.objects(json, "tables")))
        """All tables in document order."""
        self._by_name = {t.name: t for t in self.tables}

    def find_table(self, name: str) -> ManifestTable | None:
        """Finds a table by name, or returns None."""
        return self._by_name.get(name)


class DialectCorrection:
    """One correction of a dialect."""

    def __init__(self, json: dict[str, Any]) -> None:
        self.code = j.string(json, "code")
        """Diagnostic code reported when the correction is applied."""
        self.action = j.string(json, "action")
        """"replaceNamespace", "decimalComma" or "dateFromDateTime"."""
        self.paths = tuple(j.strings(json, "paths"))
        """XML paths the correction applies to."""
        self.from_ = j.optional_string(json, "from")
        """Original value (replaceNamespace)."""
        self.to = j.optional_string(json, "to")
        """Replacement value (replaceNamespace)."""


class Dialect:
    """Corrections for a package that deviates from the standard, from dialects.json."""

    def __init__(self, json: dict[str, Any]) -> None:
        match = j.obj(json, "match")
        self.name = j.string(json, "name")
        """Dialect name."""
        self.description = LocalizedText._from(j.obj(json, "description"))
        """What the dialect is for."""
        self.namespaces = tuple(j.strings(match, "namespaces")) if match is not None else ()
        """Namespaces that select this dialect."""
        self.software_description = j.optional_string(match, "softwareDesc") if match is not None else None
        """Value of header/softwareDesc that selects this dialect."""
        self.corrections = tuple(DialectCorrection(c) for c in j.objects(json, "corrections"))
        """Corrections to apply."""


class ElementDefinition:
    """Documentation of a table, column or flattened group, from definition.json."""

    def __init__(self, json: dict[str, Any]) -> None:
        self.key = j.string(json, "key")
        """"tr_line" (table), "tr_line.amnt" (column) or "tr_line.currency" (group)."""
        self.kind = j.string(json, "kind")
        """"table", "column" or "group"."""
        self.label = LocalizedText._from(j.obj(json, "label"))
        """Official name, usually English only."""
        self.definition = LocalizedText._from(j.obj(json, "definition"))
        """Meaning according to the official documentation."""
        self.description = LocalizedText._from(j.obj(json, "description"))
        """Practical explanation."""
        codes = j.obj(json, "codes") or {}
        self.codes: Mapping[str, LocalizedText] = MappingProxyType(
            {code: LocalizedText._from(text if isinstance(text, dict) else None) or LocalizedText(None, None) for code, text in codes.items()}
        )
        """Meaning of code values, e.g. "D" = Debit."""


class ViewJoinCondition:
    """One equality condition of a join, as "table.column" references."""

    __slots__ = ("left", "right")

    def __init__(self, left: str, right: str) -> None:
        self.left = left
        """Column of the source or an earlier join."""
        self.right = right
        """Column of the joined table."""


class ViewJoin:
    """A table joined to the source of a view."""

    def __init__(self, json: dict[str, Any]) -> None:
        self.table = j.string(json, "table")
        """Table name."""
        conditions = []
        for pair in j.array(json, "on"):
            if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(p, str) for p in pair)):
                raise j.JsonFormatError("Embedded JSON: a join condition must be a pair of column references.")
            conditions.append(ViewJoinCondition(pair[0], pair[1]))

        self.on = tuple(conditions)
        """Equality conditions; empty means the table is an ancestor of the source."""


class ViewParameter:
    """An optional filter of a view."""

    def __init__(self, json: dict[str, Any]) -> None:
        self.name = j.string(json, "name")
        """Parameter name, e.g. "period_from"."""
        self.label = LocalizedText._from(j.obj(json, "label"))
        """Display name."""
        self.column = j.string(json, "column")
        """The filtered column, as "table.column"."""
        self.operator = j.string(json, "operator")
        """"=", "<", "<=", ">" or ">="."""


class ViewValue:
    """How a view column gets its value from an input row."""

    def __init__(self, operator: str, column: str, by: str | None, codes: Iterable[str]) -> None:
        self.operator = operator
        """
        "column": the value of `column`;
        "only": the value when `by` is one of `codes`, otherwise None;
        "signed": the value, negated when `by` is one of `codes`.
        """
        self.column = column
        """The value column, as "table.column"."""
        self.by = by
        """The column holding the code, as "table.column"."""
        self.codes: tuple[str, ...] = tuple(codes)
        """Codes for "only" (in) or "signed" (negative)."""

    @staticmethod
    def _from(json: Any) -> ViewValue:
        if isinstance(json, str):
            return ViewValue("column", json, None, ())

        if isinstance(json, dict):
            return ViewValue(
                j.string(json, "op"), j.string(json, "value"), j.optional_string(json, "by"), [*j.strings(json, "in"), *j.strings(json, "negative")]
            )

        raise j.JsonFormatError("Embedded JSON: invalid view column value.")


class ViewColumn:
    """An output column of a view."""

    def __init__(self, json: dict[str, Any]) -> None:
        self.name = j.string(json, "name")
        """Column name."""
        self.label = LocalizedText._from(j.obj(json, "label"))
        """Display name."""
        self.aggregate = j.optional_string(json, "aggregate")
        """"sum", "count", "min", "max", or None for a grouping column."""
        self.value = ViewValue._from(json["value"]) if json.get("value") is not None else None
        """The value per input row; None for a row count."""


class ViewDefinition:
    """
    A view from views/<type>.json: a report over the rows of an import, such as a trial balance.
    Run it with :meth:`run` on any store that can hand out the rows of a table.
    """

    def __init__(self, json: dict[str, Any]) -> None:
        self.name = j.string(json, "name")
        """View name, e.g. "trial_balance"."""
        self.label = LocalizedText._from(j.obj(json, "label"))
        """Display name."""
        self.description = LocalizedText._from(j.obj(json, "description"))
        """What the view shows."""
        self.source = j.string(json, "source")
        """The table with one input row per row of the view (before grouping)."""
        self.joins = tuple(ViewJoin(x) for x in j.objects(json, "joins"))
        """Tables joined to the source: an ancestor of the source, or a lookup on key columns."""
        self.parameters = tuple(ViewParameter(x) for x in j.objects(json, "parameters"))
        """Optional filters."""
        self.columns = tuple(ViewColumn(x) for x in j.objects(json, "columns"))
        """Output columns. When any column aggregates, the other columns group."""
        self.order_by = tuple(j.strings(json, "orderBy"))
        """Output column names to sort by, ascending."""

    @property
    def is_aggregated(self) -> bool:
        """True when at least one column aggregates."""
        return any(c.aggregate is not None for c in self.columns)

    def run(
        self,
        schema: AuditfileSchema,
        rows: Callable[[str], Iterable[AuditfileRow]],
        parameters: Mapping[str, object] | None = None,
        language: TextLanguage = TextLanguage.DUTCH,
    ) -> ViewResult:
        """
        Runs the view. `rows` returns the rows of a table by name.
        `parameters` holds filter values by parameter name; None values are ignored.

        :raises ValueError: A parameter is unknown or its value cannot be converted.
        """
        from .views import run_view

        if schema is None:
            raise TypeError("schema is required.")

        if not callable(rows):
            raise TypeError("rows must be a function that returns the rows of a table.")

        return run_view(self, schema, rows, parameters, TextLanguage(language))

    def __str__(self) -> str:
        return self.name

    @staticmethod
    def _load(type_: str) -> tuple[ViewDefinition, ...]:
        name = f"views/{type_}.json"
        return tuple(ViewDefinition(v) for v in j.objects(read_json(name), "views")) if exists(name) else ()
