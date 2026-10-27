"""Every stream the reader gets from a factory is closed again, also when reading fails."""

import io

import pytest

from conftest import OFFICIAL_NAMESPACE, SAMPLE_TEXT
from xml_auditfile import Auditfile, AuditfileSource, MemorySink, ReadMode, ReadOptions

CASES = ["valid", "unknown namespace", "dtd", "truncated", "root after first megabyte", "invalid custom xsd", "io failure"]


class Tracker:
    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0


class TrackedStream(io.BytesIO):
    def __init__(self, data: bytes, fail_after: int, tracker: Tracker) -> None:
        super().__init__(data)
        self._fail_after = fail_after
        self._tracker = tracker
        tracker.opened += 1

    def read(self, size: int = -1) -> bytes:
        if self.tell() >= self._fail_after:
            raise OSError("The network path is no longer available.")

        return super().read(min(size, self._fail_after - self.tell()) if size >= 0 else self._fail_after - self.tell())

    def close(self) -> None:
        if not self.closed:
            self._tracker.closed += 1

        super().close()


def create(name: str):
    root = SAMPLE_TEXT[SAMPLE_TEXT.index("<auditfile") :]
    content = {
        "unknown namespace": SAMPLE_TEXT.replace(f'xmlns="{OFFICIAL_NAMESPACE}"', 'xmlns="urn:unknown"'),
        "dtd": '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]>' + root,
        "truncated": SAMPLE_TEXT[: len(SAMPLE_TEXT) // 2],
        "root after first megabyte": '<?xml version="1.0"?><!--' + "x" * 1_100_000 + "-->" + root,
    }.get(name, SAMPLE_TEXT)
    data = content.encode("utf-8")
    fail_after = len(data) // 2 if name == "io failure" else len(data) + 1
    tracker = Tracker()
    source = AuditfileSource.from_factory(lambda: TrackedStream(data, fail_after, tracker), "tracked.xaf")
    options = ReadOptions(xsd=lambda: b"<xsd:schema") if name == "invalid custom xsd" else ReadOptions()
    return source, tracker, options


@pytest.mark.parametrize("name", CASES)
def test_read_closes_every_stream(name):
    source, tracker, options = create(name)
    Auditfile.read(source, MemorySink(), options)
    assert tracker.opened > 0
    assert tracker.closed == tracker.opened


@pytest.mark.parametrize("name", CASES)
def test_unverified_read_closes_every_stream(name):
    source, tracker, options = create(name)
    Auditfile.read(source, MemorySink(), options.with_mode(ReadMode.UNVERIFIED))
    assert tracker.closed == tracker.opened


@pytest.mark.parametrize("name", CASES)
def test_scan_closes_every_stream(name):
    source, tracker, options = create(name)
    Auditfile.scan(source, options)
    assert tracker.closed == tracker.opened


def test_a_failing_sink_still_closes_the_stream():
    source, tracker, options = create("valid")

    class FailingSink(MemorySink):
        def write(self, table, rows):
            raise RuntimeError("The database is down.")

    with pytest.raises(RuntimeError, match="database is down"):
        Auditfile.read(source, FailingSink(), options)

    assert tracker.closed == tracker.opened


def test_stopping_early_with_read_rows_closes_the_stream():
    source, tracker, _ = create("valid")
    reader = Auditfile.open(source, ReadOptions(mode=ReadMode.UNVERIFIED))

    rows = reader.read_rows()
    next(rows)
    rows.close()

    assert tracker.closed == tracker.opened
