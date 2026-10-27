"""Pull reader over a byte stream, on the standard library's expat parser.

DTDs are refused (so no entity can be declared or expanded and nothing external is loaded),
namespaces are resolved, and every event carries line, column, depth and source part.
"""

from __future__ import annotations

import re
from collections import deque
from xml.parsers import expat

from ._streams import ConcatenatedStream, XmlEncodingError

START = 1
END = 2
TEXT = 3

_DECLARED_ENCODING = re.compile(rb"^(?:\xef\xbb\xbf)?<\?xml\s[^>]*?encoding\s*=\s*([\"'])([A-Za-z][A-Za-z0-9._-]*)\1")
_UNKNOWN_ENCODING = expat.errors.codes[expat.errors.XML_ERROR_UNKNOWN_ENCODING]
_INCORRECT_ENCODING = expat.errors.codes[expat.errors.XML_ERROR_INCORRECT_ENCODING]


class Event:
    """A start, end or text event."""

    __slots__ = ("kind", "ns", "local", "attributes", "line", "column", "depth", "part", "text")

    def __init__(
        self, kind: int, ns: str, local: str, attributes: list[tuple[str, str, str]], line: int, column: int, depth: int, part: int, text: str
    ) -> None:
        self.kind = kind
        self.ns = ns
        self.local = local
        self.attributes = attributes
        self.line = line
        self.column = column
        self.depth = depth
        self.part = part
        self.text = text


class XmlSyntaxError(Exception):
    """The document is not well-formed XML."""

    def __init__(self, message: str, line: int | None, column: int | None, part: int) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.part = part


class _Refused(Exception):
    """Raised inside a handler to stop the parser."""

    def __init__(self, message: str, line: int, column: int) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


def _split(name: str) -> tuple[str, str]:
    ns, _, local = name.rpartition(" ")
    return ns, local


class XmlReader:
    def __init__(self, stream: ConcatenatedStream, max_bytes: int = 0) -> None:
        """max_bytes: size of the whole document; 0 is no limit."""
        self._max_bytes = max_bytes
        self._stream = stream
        self._chunks = stream.chunks()
        self._queue: deque[Event] = deque()
        self._depth = 0
        self._ended = False
        self._text: Event | None = None
        self._first: bytes | None = None
        self._map_from: str | None = None
        self._map_to = ""
        self._fed = 0
        self.root_offset: int | None = None
        """Bytes before the chunk in which the root element starts; precise to one chunk (64 KB)."""

        parser = expat.ParserCreate(namespace_separator=" ")
        parser.buffer_text = True
        parser.buffer_size = 65536
        parser.ordered_attributes = True
        parser.specified_attributes = True
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        parser.StartDoctypeDeclHandler = self._doctype
        parser.EntityDeclHandler = self._entity
        parser.ExternalEntityRefHandler = self._external
        parser.StartElementHandler = self._start
        parser.EndElementHandler = self._end
        parser.CharacterDataHandler = self._characters
        self._parser = parser

    @property
    def position(self) -> int:
        """Bytes handed to the parser so far."""
        return self._stream.position

    @property
    def part(self) -> int:
        """The source part being parsed."""
        return self._stream.part

    def map_namespace(self, from_: str, to: str) -> None:
        """Presents one namespace as another, e.g. an alias as the official namespace."""
        self._map_from = from_
        self._map_to = to

    def next(self) -> Event | None:
        """The next event, or None at the end of the document. :raises XmlSyntaxError, XmlEncodingError:"""
        queue = self._queue
        while not queue:
            if self._ended:
                return None

            self._feed()

        return self._map(queue.popleft())

    def push_back(self, event: Event) -> None:
        """Puts an event back so the next call returns it again."""
        self._queue.appendleft(event)

    def close(self) -> None:
        """Stops reading and closes the streams it opened."""
        self._ended = True
        self._chunks.close()

    def _feed(self) -> None:
        try:
            data = next(self._chunks, None)
        except BaseException:
            self._ended = True
            raise

        try:
            if data is None:
                self._ended = True
                self._parser.Parse(b"", True)
                self._flush()
                return

            if self._first is None:
                self._first = data[:512]

            self._fed += len(data)
            if self._max_bytes and self._fed > self._max_bytes:
                raise _Refused(f"The document is larger than {self._max_bytes} bytes.", self._parser.CurrentLineNumber, self._parser.CurrentColumnNumber + 1)

            self._parser.Parse(data, False)
            # Text is handed out per chunk, so a huge text node is never joined into one string here.
            self._flush()
        except _Refused as refused:
            self._ended = True
            raise XmlSyntaxError(refused.message, refused.line, refused.column, self._stream.part) from None
        except (LookupError, ValueError) as error:
            # Raised for an encoding Python has no codec for, or one expat cannot use (multi-byte).
            self._ended = True
            if isinstance(error, ValueError) and "encoding" not in str(error):
                raise

            match = _DECLARED_ENCODING.match(self._first or b"")
            raise XmlEncodingError(match[2].decode("ascii") if match is not None else str(error)) from None
        except expat.ExpatError as error:
            self._ended = True
            if error.code in (_UNKNOWN_ENCODING, _INCORRECT_ENCODING):
                match = _DECLARED_ENCODING.match(self._first or b"")
                if error.code == _UNKNOWN_ENCODING and match is not None:
                    raise XmlEncodingError(match[2].decode("ascii")) from None

            raise XmlSyntaxError(expat.ErrorString(error.code), error.lineno, error.offset + 1, self._stream.part) from None

    def _here(self) -> tuple[int, int]:
        return self._parser.CurrentLineNumber, self._parser.CurrentColumnNumber + 1

    def _doctype(self, *_: object) -> None:
        line, column = self._here()
        raise _Refused("For security reasons DTD is prohibited in this XML document.", line, column)

    def _entity(self, *_: object) -> None:
        line, column = self._here()
        raise _Refused("Entity declarations are not allowed.", line, column)

    def _external(self, *_: object) -> int:
        line, column = self._here()
        raise _Refused("External entities are not allowed.", line, column)

    def _start(self, name: str, attributes: list[str]) -> None:
        self._flush()
        ns, local = _split(name)
        pairs = []
        for i in range(0, len(attributes), 2):
            attribute_ns, attribute_local = _split(attributes[i])
            pairs.append((attribute_ns, attribute_local, attributes[i + 1]))

        parser = self._parser
        # Report the position of the name itself (1-based), as .NET does: after the "<".
        self._queue.append(
            Event(START, ns, local, pairs, parser.CurrentLineNumber, parser.CurrentColumnNumber + 2, self._depth, self._stream.part, "")
        )
        if self._depth == 0:
            self.root_offset = self._fed

        self._depth += 1

    def _end(self, name: str) -> None:
        self._flush()
        self._depth -= 1
        ns, local = _split(name)
        parser = self._parser
        self._queue.append(Event(END, ns, local, [], parser.CurrentLineNumber, parser.CurrentColumnNumber + 1, self._depth, self._stream.part, ""))

    def _characters(self, text: str) -> None:
        if self._text is None:
            parser = self._parser
            self._text = Event(TEXT, "", "", [], parser.CurrentLineNumber, parser.CurrentColumnNumber + 1, self._depth, self._stream.part, text)
        else:
            self._text.text += text

    def _flush(self) -> None:
        if self._text is not None:
            self._queue.append(self._text)
            self._text = None

    def _map(self, event: Event) -> Event:
        source = self._map_from
        if source is None or event.kind == TEXT:
            return event

        if event.ns == source:
            event.ns = self._map_to

        if event.kind == START and any(a[0] == source for a in event.attributes):
            event.attributes = [(self._map_to if a[0] == source else a[0], a[1], a[2]) for a in event.attributes]

        return event
