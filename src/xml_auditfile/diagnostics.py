"""Diagnostics with stable codes from auditfile-spec, and the report of a read."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import timedelta
from enum import IntEnum
from functools import lru_cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple

from . import _json as j
from ._resources import read_json
from .definitions import LocalizedText, TextLanguage
from .values import to_text

if TYPE_CHECKING:
    from .schema import AuditfileSchema
    from .source import PartInfo


class DiagnosticSeverity(IntEnum):
    """Severity of a diagnostic, from least to most severe."""

    INFO = 0
    """Information only."""

    WARNING = 1
    """Something to look at; the result is still usable."""

    ERROR = 2
    """The file does not meet the standard."""

    FATAL = 3
    """Reading stopped."""

    def __str__(self) -> str:
        return self.name.title()


class Diagnostic:
    """A finding about an audit file, with a stable code from auditfile-spec."""

    __slots__ = ("code", "layer", "severity", "message", "part", "line", "position", "path")

    def __init__(
        self, code: str, layer: str, severity: DiagnosticSeverity, message: str, part: int | None, line: int | None, position: int | None, path: str | None
    ) -> None:
        self.code = code
        """Code, e.g. "XAF-R001"."""
        self.layer = layer
        """"xml", "version", "structure", "xsd", "rules", "dialect" or "view"."""
        self.severity = severity
        """Severity."""
        self.message = message
        """Message in the requested language."""
        self.part = part
        """Source part (1-based), when known."""
        self.line = line
        """Line number, when known."""
        self.position = position
        """Position on the line, when known."""
        self.path = path
        """XML path, when known."""

    def __str__(self) -> str:
        if self.line is None:
            return f"{self.code} {self.severity}: {self.message}"

        return f"{self.code} {self.severity} (line {self.line}): {self.message}"

    def __repr__(self) -> str:
        return f"Diagnostic({self.code!r}, {self.severity!s}, {self.message!r})"


class Location(NamedTuple):
    """Where a diagnostic applies."""

    part: int | None = None
    line: int | None = None
    position: int | None = None
    path: str | None = None


NO_LOCATION = Location()

_SEVERITIES = {"info": DiagnosticSeverity.INFO, "warning": DiagnosticSeverity.WARNING, "error": DiagnosticSeverity.ERROR}


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict[str, Any]]:
    return {j.string(c, "code"): c for c in j.objects(read_json("diagnostics.json"), "codes")}


def _entry(code: str) -> dict[str, Any]:
    entry = _catalog().get(code)
    if entry is None:
        raise LookupError(f"Diagnostic code {code} is not defined in diagnostics.json.")

    return entry


def _severity(entry: dict[str, Any]) -> DiagnosticSeverity:
    return _SEVERITIES.get(j.string(entry, "severity"), DiagnosticSeverity.FATAL)


class DiagnosticCollector:
    """Collects diagnostics, formats messages from diagnostics.json and caps the total."""

    def __init__(self, language: TextLanguage, maximum: int) -> None:
        self._language = language
        self._maximum = maximum
        self._capped = False
        self.items: list[Diagnostic] = []
        self.has_fatal = False
        self.has_errors = False

    def add(self, code: str, location: Location = NO_LOCATION, **arguments: object) -> None:
        severity = _severity(_entry(code))
        self.has_fatal |= severity is DiagnosticSeverity.FATAL
        self.has_errors |= severity >= DiagnosticSeverity.ERROR

        if len(self.items) >= self._maximum and severity is not DiagnosticSeverity.FATAL:
            if not self._capped:
                self._capped = True
                self.items.append(self._create("XAF-X900", NO_LOCATION, {"max": self._maximum}))

            return

        self.items.append(self._create(code, location, arguments))

    def _create(self, code: str, location: Location, arguments: Mapping[str, object]) -> Diagnostic:
        entry = _entry(code)
        text = LocalizedText._from(j.obj(entry, "message"))
        message = (text.get(self._language) if text is not None else None) or code
        for name, value in arguments.items():
            message = message.replace("{" + name + "}", to_text(value))

        return Diagnostic(code, j.string(entry, "layer"), _severity(entry), message, location.part, location.line, location.position, location.path)


class AuditfileReport:
    """Outcome of a scan, validation or read."""

    def __init__(
        self,
        schema: AuditfileSchema | None,
        parts: Iterable[PartInfo],
        diagnostics: Iterable[Diagnostic],
        row_counts: Mapping[str, int],
        loaded: bool,
        duration: timedelta,
    ) -> None:
        self.schema = schema
        """The import schema, or None when the version could not be determined."""
        self.parts: tuple[PartInfo, ...] = tuple(parts)
        """Source parts with size and SHA-256 (empty when a part was not read completely)."""
        self.diagnostics: tuple[Diagnostic, ...] = tuple(diagnostics)
        """All diagnostics, in the order they were found."""
        self.row_counts: Mapping[str, int] = MappingProxyType(dict(row_counts))
        """Rows per table."""
        self.loaded = loaded
        """True when rows were handed to the sink and completed."""
        self.duration = duration
        """Time taken."""

    @property
    def is_valid(self) -> bool:
        """True when there are no errors and no fatal diagnostics."""
        return not any(d.severity >= DiagnosticSeverity.ERROR for d in self.diagnostics)

    def count(self, severity: DiagnosticSeverity) -> int:
        """Number of diagnostics with the given severity."""
        return sum(1 for d in self.diagnostics if d.severity == severity)
