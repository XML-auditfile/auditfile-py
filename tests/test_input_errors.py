"""Bad input never raises: every case ends in a report with a fatal diagnostic."""

import io

import pytest

from conftest import SAMPLE, SAMPLE_TEXT, source
from xml_auditfile import Auditfile, AuditfileSource, DiagnosticSeverity, MemorySink, ReadMode, ReadOptions


class FailingStream(io.RawIOBase):
    """Hands out the first bytes of a file, then fails like a broken network share."""

    def __init__(self, data: bytes, fail_after: int) -> None:
        self._data = data
        self._fail_after = fail_after
        self._position = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self._position >= self._fail_after:
            raise OSError("The network path is no longer available.")

        end = min(self._position + (size if size >= 0 else len(self._data)), self._fail_after, len(self._data))
        chunk = self._data[self._position : end]
        self._position = end
        return chunk


def failing(after_bytes: int) -> AuditfileSource:
    data = SAMPLE_TEXT.encode("utf-8")
    return AuditfileSource.from_factory(lambda: FailingStream(data, after_bytes), "failing.xaf")


def test_a_root_element_after_the_first_megabyte_is_reported():
    root = SAMPLE_TEXT[SAMPLE_TEXT.index("<auditfile") :]
    data = ('<?xml version="1.0"?><!--' + "x" * 1_100_000 + "-->" + root).encode("utf-8")
    padded = AuditfileSource.from_factory(lambda: data, "padded.xaf")

    scan = Auditfile.scan(padded)
    read = Auditfile.read(padded, MemorySink())

    assert any(d.code == "XAF-X002" and d.severity is DiagnosticSeverity.FATAL for d in scan.diagnostics)
    assert any(d.code == "XAF-X002" for d in read.diagnostics)
    assert not read.loaded


@pytest.mark.parametrize(
    "xsd",
    [
        "<xsd:schema",
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"><xsd:element name="a" type="xsd:unknown"/></xsd:schema>',
    ],
)
def test_a_custom_xsd_that_does_not_compile_is_reported(xsd):
    report = Auditfile.read(source(SAMPLE), MemorySink(), ReadOptions(xsd=lambda: xsd.encode("utf-8")))

    assert any(d.code == "XAF-X002" and "XSD" in d.message for d in report.diagnostics), report.diagnostics
    assert not report.loaded


def test_a_failure_while_scanning_is_reported():
    report = Auditfile.scan(failing(2000))

    assert any(d.code == "XAF-X002" and "failing.xaf" in d.message for d in report.diagnostics), report.diagnostics
    assert report.schema is None


def test_a_failure_while_loading_is_reported_and_the_sink_aborts():
    sink = MemorySink()

    report = Auditfile.read(failing(2000), sink, ReadOptions(mode=ReadMode.UNVERIFIED))

    assert any(d.code == "XAF-X002" for d in report.diagnostics), report.diagnostics
    assert not report.loaded
    assert sink.is_aborted


def test_a_factory_that_is_refused_access_is_reported():
    def locked():
        raise PermissionError("Permission denied")

    report = Auditfile.scan(AuditfileSource.from_factory(locked, "locked.xaf"))

    assert any(d.code == "XAF-X002" and "Permission denied" in d.message for d in report.diagnostics)


def test_reading_a_single_use_source_twice_is_the_callers_mistake_and_raises():
    once = AuditfileSource.from_stream(io.BytesIO(SAMPLE_TEXT.encode("utf-8")))
    Auditfile.scan(once)

    with pytest.raises(RuntimeError, match="only be read once"):
        Auditfile.scan(once)


def test_a_large_file_handed_over_at_once_is_read_whatever_its_size():
    end = SAMPLE_TEXT.index("</company>")
    data = (SAMPLE_TEXT[:end] + " " * 2_000_000 + SAMPLE_TEXT[end:]).encode("utf-8")

    report = Auditfile.scan(AuditfileSource.from_factory(lambda: data, "large.xaf"))

    assert report.schema is not None and report.schema.version_key == "xaf/3.2", report.diagnostics
