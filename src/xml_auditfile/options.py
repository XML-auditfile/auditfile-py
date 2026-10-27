"""Options for reading."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Union

from .definitions import TextLanguage

if TYPE_CHECKING:
    from .source import Stream
    from .versions import AuditfileVersion


CANCELLATION_INTERVAL = 10_000
"""XML events between two checks of the cancel event."""


class ReadCancelledError(Exception):
    """Reading was cancelled through ReadOptions.cancel."""


class ReadMode(str, Enum):
    """How the reader treats the file."""

    VERIFIED = "verified"
    """Validate against the XSD first; load only a valid file."""

    UNVERIFIED = "unverified"
    """Load directly with the manifest; elements outside the manifest are reported and skipped."""


class ReadProgress:
    """Progress of a read."""

    __slots__ = ("rows_read", "total_rows")

    def __init__(self, rows_read: int, total_rows: int | None) -> None:
        self.rows_read = rows_read
        """Rows read so far."""
        self.total_rows = total_rows
        """Total rows, when known from the scan phase."""

    def __repr__(self) -> str:
        return f"ReadProgress({self.rows_read}, {self.total_rows})"


@dataclass(frozen=True)
class ReadOptions:
    """Options for reading. The defaults suit most uses."""

    version: Union[AuditfileVersion, str, None] = None
    """Force a version, e.g. ``AuditfileVersion.Financial.V3_2`` or "xaf/3.2". None: determine from the namespace."""

    mode: ReadMode = ReadMode.VERIFIED
    """Verified (default) or unverified."""

    use_resolver: bool = False
    """Derive the version from an unknown namespace, e.g. ".../XAF/3.2/". Off by default."""

    xsd: Union[Callable[[], Stream], None] = None
    """Own XSD instead of the built-in one, as a factory of a binary stream or bytes. None: use the built-in XSD."""

    apply_dialects: bool = True
    """Apply known dialect corrections. On by default."""

    batch_size: int = 1000
    """Rows per table handed to the sink at once."""

    language: TextLanguage = TextLanguage.DUTCH
    """Language of diagnostic messages."""

    max_diagnostics: int = 1000
    """
    Maximum number of diagnostics in the report; the rest is counted but left out (XAF-X900). At least 1.
    Errors take the place of infos and warnings, and fatal diagnostics are always included.
    """

    max_depth: int = 64
    """Maximum element nesting; deeper files are refused."""

    max_characters_in_document: int = 4 * 1024**3
    """Maximum size of the whole document, all parts together, counted in bytes; a larger file stops with XAF-X001. 0 is no limit."""

    max_value_length: int = 65_536
    """Maximum characters of one value; a longer value is skipped with XAF-S008 and never held in memory as a whole.
    The longest value an XSD of auditfile-spec allows is 999 characters."""

    on_progress: Union[Callable[[ReadProgress], None], None] = None
    """Receives progress after every batch."""

    cancel: Union[threading.Event, None] = None
    """Set this event to cancel reading, in the scan phase as well as the load phase; the call then raises ReadCancelledError."""

    def __post_init__(self) -> None:
        from .versions import AuditfileVersion

        if isinstance(self.version, str):
            object.__setattr__(self, "version", AuditfileVersion.parse(self.version))
        elif self.version is not None and not isinstance(self.version, AuditfileVersion):
            raise TypeError('version must be an AuditfileVersion or a key such as "xaf/3.2".')

        object.__setattr__(self, "mode", ReadMode(self.mode))
        object.__setattr__(self, "language", TextLanguage(self.language))
        if not isinstance(self.max_characters_in_document, int) or self.max_characters_in_document < 0:
            raise ValueError("max_characters_in_document must be a non-negative integer.")

        for name in ("batch_size", "max_diagnostics", "max_depth", "max_value_length"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")

        for name in ("xsd", "on_progress"):
            if getattr(self, name) is not None and not callable(getattr(self, name)):
                raise TypeError(f"{name} must be callable.")

        if self.cancel is not None and not callable(getattr(self.cancel, "is_set", None)):
            raise TypeError("cancel must be a threading.Event.")

    def check_cancelled(self) -> None:
        """Raises ReadCancelledError when the cancel event is set."""
        if self.cancel is not None and self.cancel.is_set():
            raise ReadCancelledError("Reading was cancelled.")

    def with_mode(self, mode: ReadMode) -> ReadOptions:
        return replace(self, mode=mode)


DEFAULT_OPTIONS = ReadOptions()
