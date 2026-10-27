"""Reads the parts of a source one after another as one document."""

from __future__ import annotations

import codecs
import hashlib
import io
import re
from collections.abc import Callable, Iterator, Sequence

from .source import PartInfo, SourcePart, SourceUsageError

PREFIX_SIZE = 1024
CHUNK_SIZE = 64 * 1024


class SourceError(Exception):
    """A part could not be opened or read; reported as XAF-X002, never raised to the caller."""

    def __init__(self, part: str, cause: BaseException) -> None:
        super().__init__(str(cause) or type(cause).__name__)
        self.part = part


class _TextStreamError(TypeError):
    """The caller passed a text stream: a programming error, not a source error."""


def _reader(stream: object) -> Callable[[int], bytes]:
    """A read(n) function for a supported stream."""
    if isinstance(stream, (bytes, bytearray, memoryview)):
        return io.BytesIO(bytes(stream)).read

    read = getattr(stream, "read", None)
    if not callable(read):
        raise TypeError("Unsupported stream: pass a binary file-like object (with read) or bytes.")

    def checked(size: int) -> bytes:
        data = read(size)
        if isinstance(data, str):
            raise _TextStreamError("A stream must produce bytes; text is refused because its encoding is unknown. Open files in binary mode.")

        return bytes(data) if data else b""

    return checked


class XmlEncodingError(Exception):
    """The declared encoding is not supported (XAF-X004)."""

    def __init__(self, encoding: str, message: str | None = None) -> None:
        super().__init__(message or f"Encoding '{encoding}' is not supported.")
        self.encoding = encoding
        self.part: int | None = None
        """The part, when one part differs from the first."""


class PartEncodingError(XmlEncodingError):
    """
    A continuation part in another encoding than the first part. The parts are decoded as one document,
    so it would be read as garbled text without any error; it is refused instead (XAF-X004).
    """

    def __init__(self, part: int, encoding: _PartEncoding, first: _PartEncoding) -> None:
        super().__init__(
            f"{encoding.label} (part 1: {first.label})", f"Part {part} is encoded as {encoding.label}, but part 1 as {first.label}."
        )
        self.part = part


_DECLARED = re.compile(rb"^<\?xml\s[^>]*?encoding\s*=\s*([\"'])([A-Za-z][A-Za-z0-9._-]*)\1")


class _PartEncoding:
    """
    The encoding of a part, from its byte order mark or XML declaration (UTF-8 when neither says otherwise),
    following the detection rules of XML 1.0 appendix F.
    """

    __slots__ = ("name", "bom_length", "unit")

    def __init__(self, name: str, bom_length: int, unit: str) -> None:
        self.name = name
        """Name as declared, e.g. "utf-8", "utf-16-le", "ISO-8859-1"."""
        self.bom_length = bom_length
        self.unit = unit
        """Codec to find the declaration with: one byte per character, or UTF-16."""

    @property
    def label(self) -> str:
        return self.name.upper()

    def same_as(self, other: _PartEncoding) -> bool:
        """True when both decode the same way; labels such as "latin1" and "ISO-8859-1" are the same codec."""
        return _canonical(self.name) == _canonical(other.name)

    @staticmethod
    def detect(data: bytes) -> _PartEncoding:
        if data.startswith(codecs.BOM_UTF8):
            return _PartEncoding("utf-8", 3, "latin-1")

        if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(b"<\x00?\x00"):
            return _PartEncoding("utf-16-le", 2 if data.startswith(codecs.BOM_UTF16_LE) else 0, "utf-16-le")

        if data.startswith(codecs.BOM_UTF16_BE) or data.startswith(b"\x00<\x00?"):
            return _PartEncoding("utf-16-be", 2 if data.startswith(codecs.BOM_UTF16_BE) else 0, "utf-16-be")

        match = _DECLARED.match(data[:512])
        return _PartEncoding(match[2].decode("ascii") if match is not None else "utf-8", 0, "latin-1")

    def skip_declaration(self, data: bytes) -> int:
        """Number of bytes of a leading byte order mark and XML declaration."""
        head = data[self.bom_length : PREFIX_SIZE]
        start, end = "<?xml".encode(self.unit), "?>".encode(self.unit)
        if head.startswith(start):
            width = len("?".encode(self.unit))
            index = head.find(end, len(start))
            while index >= 0 and index % width != 0:
                index = head.find(end, index + 1)

            if index >= 0:
                return self.bom_length + index + len(end)

        return self.bom_length


def _canonical(name: str) -> str:
    """The Python codec name of an encoding label, or the label itself when no codec knows it."""
    try:
        return codecs.lookup(name).name
    except LookupError:
        return name.lower()


class _Statistics:
    __slots__ = ("hash", "size", "lines", "last_was_carriage_return")

    def __init__(self) -> None:
        self.hash = hashlib.sha256()
        self.size = 0
        self.lines = 1
        self.last_was_carriage_return = False

    def track(self, data: bytes) -> None:
        if not data:
            return

        self.size += len(data)
        self.hash.update(data)
        # CR, LF and CRLF each end a line; a CRLF split over two chunks counts once.
        lines = data.count(b"\r") + data.count(b"\n") - data.count(b"\r\n")
        if self.last_was_carriage_return and data[:1] == b"\n":
            lines -= 1

        self.lines += lines
        self.last_was_carriage_return = data[-1:] == b"\r"


class ConcatenatedStream:
    """
    Reads the parts of a source one after another as one document.
    For continuation parts a leading byte order mark and XML declaration are skipped.
    Collects size, SHA-256 and line count per part when collect_statistics is set.
    """

    def __init__(self, parts: Sequence[SourcePart], collect_statistics: bool) -> None:
        self._parts = parts
        self.collect_statistics = collect_statistics
        self.completed: list[PartInfo] = []
        """Statistics of the parts read completely so far."""
        self.part = 0
        """Number (1-based) of the part the most recent chunk came from."""
        self.position = 0
        """Bytes handed out so far, over all parts."""
        self._encoding: _PartEncoding | None = None

    def chunks(self) -> Iterator[bytes]:
        for part in self._parts:
            self.part = part.number
            try:
                stream, keep_open = part._open()
            except SourceUsageError:
                raise
            except Exception as error:
                raise SourceError(part.name, error) from error

            raw = _reader(stream)

            def read(size: int, raw: Callable[[int], bytes] = raw, name: str = part.name) -> bytes:
                try:
                    return raw(size)
                except _TextStreamError:
                    raise
                except Exception as error:
                    raise SourceError(name, error) from error

            statistics = _Statistics() if self.collect_statistics else None
            try:
                prefix = bytearray()
                done = False
                while len(prefix) < PREFIX_SIZE:
                    data = read(PREFIX_SIZE - len(prefix))
                    if not data:
                        done = True
                        break

                    prefix += data

                first = bytes(prefix)
                if statistics is not None:
                    statistics.track(first)

                encoding = _PartEncoding.detect(first)
                if part.number == 1:
                    self._encoding = encoding
                elif self._encoding is not None and not encoding.same_as(self._encoding):
                    raise PartEncodingError(part.number, encoding, self._encoding)

                rest = first if part.number == 1 else first[encoding.skip_declaration(first) :]
                if rest:
                    self.position += len(rest)
                    yield rest

                while not done:
                    data = read(CHUNK_SIZE)
                    if not data:
                        break

                    if statistics is not None:
                        statistics.track(data)

                    self.position += len(data)
                    yield data

                if statistics is not None:
                    self.completed.append(PartInfo(part.number, part.name, statistics.size, statistics.hash.hexdigest(), statistics.lines))
            finally:
                close = getattr(stream, "close", None)
                if not keep_open and callable(close):
                    close()
