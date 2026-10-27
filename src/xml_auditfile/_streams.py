"""Reads the parts of a source one after another as one document."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Iterator, Sequence

from .source import PartInfo, SourcePart

PREFIX_SIZE = 1024
CHUNK_SIZE = 64 * 1024


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
            raise TypeError("A stream must produce bytes; text is refused because its encoding is unknown. Open files in binary mode.")

        return bytes(data) if data else b""

    return checked


def _skip_declaration(data: bytes) -> int:
    """Number of bytes of a leading UTF-8 BOM and XML declaration."""
    head = data[:PREFIX_SIZE]
    position = 3 if head.startswith(b"\xef\xbb\xbf") else 0
    if head.startswith(b"<?xml", position):
        end = head.find(b"?>", position + 5)
        if end >= 0:
            return end + 2

    return position


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

    def chunks(self) -> Iterator[bytes]:
        for part in self._parts:
            self.part = part.number
            stream, keep_open = part._open()
            read = _reader(stream)
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

                rest = first if part.number == 1 else first[_skip_declaration(first):]
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
