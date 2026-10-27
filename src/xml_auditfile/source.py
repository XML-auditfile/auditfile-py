"""Where an audit file comes from. The reader never opens files itself; the caller supplies the streams."""

from __future__ import annotations

from collections.abc import Callable
from typing import BinaryIO, Union

Stream = Union[BinaryIO, bytes, bytearray, memoryview]
"""A binary stream (anything with ``read(n)`` returning bytes), or the bytes themselves."""


class SourcePart:
    """One part of a source."""

    __slots__ = ("number", "name", "reopenable", "_open")

    def __init__(self, number: int, name: str, open_: Callable[[], tuple[Stream, bool]], reopenable: bool) -> None:
        self.number = number
        """Part number (1-based)."""
        self.name = name
        """File or stream name."""
        self.reopenable = reopenable
        """True when the part can be opened more than once."""
        self._open = open_


class AuditfileSource:
    """
    Where an audit file comes from: one stream, or several streams for the parts of a split audit file.
    A factory can be read twice (scan, then load); a plain stream only once.
    """

    def __init__(self, parts: list[SourcePart]) -> None:
        if not parts:
            raise ValueError("A source needs at least one part.")

        self.parts: tuple[SourcePart, ...] = tuple(parts)
        """The parts in reading order."""

    @property
    def can_read_twice(self) -> bool:
        """True when every part can be opened again, so the reader can scan before loading."""
        return all(p.reopenable for p in self.parts)

    @staticmethod
    def from_stream(stream: Stream, name: str = "stream") -> AuditfileSource:
        """A stream that can be read only once. The reader does not close it."""
        return AuditfileSource.from_streams((name, stream))

    @staticmethod
    def from_streams(*parts: tuple[str, Stream]) -> AuditfileSource:
        """
        A split audit file as streams that can be read only once: the first part followed by its continuation parts, in order.
        The reader does not close the streams.
        """

        def single_use(stream: Stream) -> Callable[[], tuple[Stream, bool]]:
            used = False

            def open_() -> tuple[Stream, bool]:
                nonlocal used
                if used:
                    raise RuntimeError("This stream source can only be read once.")

                used = True
                return stream, True

            return open_

        result = []
        for i, part in enumerate(parts):
            name, stream = _pair(part, "stream")
            if stream is None:
                raise TypeError(f"Part {i + 1} has no stream.")

            result.append(SourcePart(i + 1, name, single_use(stream), False))

        return AuditfileSource(result)

    @staticmethod
    def from_factory(open_: Callable[[], Stream], name: str = "stream") -> AuditfileSource:
        """
        A stream factory that returns a fresh stream on every call, so the source can be read twice.
        The reader closes the streams it gets.

        Example: ``AuditfileSource.from_factory(lambda: open("admin.xaf", "rb"), "admin.xaf")``
        """
        return AuditfileSource.from_factories((name, open_))

    @staticmethod
    def from_factories(*parts: tuple[str, Callable[[], Stream]]) -> AuditfileSource:
        """
        A split audit file as stream factories: the first part followed by its continuation parts, in order.
        Every factory returns a fresh stream on every call. The reader closes the streams it gets.
        """
        result = []
        for i, part in enumerate(parts):
            name, factory = _pair(part, "factory")
            if not callable(factory):
                raise TypeError(f"Part {i + 1} needs a factory function.")

            result.append(SourcePart(i + 1, name, lambda factory=factory: (factory(), False), True))

        return AuditfileSource(result)


def _pair(part: object, what: str) -> tuple[str, object]:
    if not (isinstance(part, tuple) and len(part) == 2 and isinstance(part[0], str)):
        raise TypeError(f"Every part must be a (name, {what}) pair.")

    return part


class PartInfo:
    """Size, checksum and line count of a source part, known after it has been read once."""

    __slots__ = ("number", "name", "size", "sha256", "lines")

    def __init__(self, number: int, name: str, size: int, sha256: str, lines: int) -> None:
        self.number = number
        """Part number (1-based)."""
        self.name = name
        """File or stream name."""
        self.size = size
        """Size in bytes."""
        self.sha256 = sha256
        """SHA-256 of the bytes as read, lowercase hex."""
        self.lines = lines
        """Number of lines."""

    def __repr__(self) -> str:
        return f"PartInfo({self.number}, {self.name!r}, size={self.size}, sha256={self.sha256[:12]}…)"
