"""The promises of the public API: a truthful report, checked options, cancellation, and no silent surprises."""

import threading

import pytest

from conftest import SAMPLE, SAMPLE_TEXT, source
from xml_auditfile import (
    Auditfile,
    AuditfileSchema,
    AuditfileSource,
    AuditfileVersion,
    DiagnosticSeverity,
    JsonFormatError,
    MemorySink,
    ReadCancelledError,
    ReadMode,
    ReadOptions,
    SinkAbortError,
    SinkCapabilities,
)

UNVERIFIED = ReadOptions(mode=ReadMode.UNVERIFIED)


def from_text(text: str, name: str = "test.xaf") -> AuditfileSource:
    data = text.encode("utf-8")
    return AuditfileSource.from_factory(lambda: data, name)


def with_unknown_elements(count: int) -> str:
    """The sample with unknown elements in the header, each reported once as a warning (XAF-S003)."""
    return SAMPLE_TEXT.replace("<header>", "<header>" + "".join(f"<unknown{i}/>" for i in range(count)), 1)


def split(text: str) -> tuple[str, str]:
    """The sample cut before the first transaction."""
    cut = text.index("<transaction>")
    return text[:cut], text[cut:]


def parts(first: bytes, second: bytes) -> AuditfileSource:
    return AuditfileSource.from_factories(("part-1.xaf", lambda: first), ("part-2.xaf", lambda: second))


class FailingSink:
    capabilities = SinkCapabilities.NONE

    def __init__(self, abort_fails: bool) -> None:
        self._abort_fails = abort_fails

    def begin(self, context):
        pass

    def write(self, table, rows):
        raise TimeoutError("database gone")

    def complete(self, report):
        pass

    def abort(self, report):
        if self._abort_fails:
            raise RuntimeError("rollback failed")


def test_errors_are_counted_and_kept_when_the_list_is_full_of_warnings():
    report = Auditfile.validate(from_text(with_unknown_elements(20)), ReadOptions(mode=ReadMode.UNVERIFIED, max_diagnostics=5))

    assert not report.is_valid
    assert report.has_errors
    assert report.is_truncated
    assert report.count(DiagnosticSeverity.ERROR) == 2
    assert report.count(DiagnosticSeverity.WARNING) == 20
    assert any(d.code == "XAF-R001" for d in report.diagnostics)
    assert any(d.code == "XAF-R003" for d in report.diagnostics)
    assert report.diagnostics[-1].code == "XAF-X900"
    assert len(report.diagnostics) == 6


@pytest.mark.parametrize(
    "options",
    [{"max_diagnostics": 0}, {"max_diagnostics": -1}, {"batch_size": 0}, {"max_depth": 0}, {"max_value_length": 0}, {"max_characters_in_document": -1}],
)
def test_options_out_of_range_are_refused(options):
    with pytest.raises(ValueError):
        ReadOptions(**options)


def test_validate_respects_unverified_mode():
    invalid = SAMPLE_TEXT.replace("<amnt>327.00</amnt>", "<amnt>abc</amnt>", 1)

    verified = Auditfile.validate(from_text(invalid))
    unverified = Auditfile.validate(from_text(invalid), UNVERIFIED)

    assert any(d.code == "XAF-D001" for d in verified.diagnostics)
    assert not any(d.code == "XAF-D001" for d in unverified.diagnostics)
    assert any(d.code == "XAF-S004" for d in unverified.diagnostics)


def test_rows_can_be_iterated_only_once():
    reader = Auditfile.open(source(SAMPLE), UNVERIFIED)
    rows = list(reader.read_rows())

    assert rows
    with pytest.raises(RuntimeError, match="only once"):
        list(reader.read_rows())
    assert reader.report.row_counts["header"] == 1


def test_cancellation_stops_the_scan_and_the_load():
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(ReadCancelledError):
        Auditfile.scan(source(SAMPLE), ReadOptions(cancel=cancel))
    with pytest.raises(ReadCancelledError):
        Auditfile.validate(source(SAMPLE), ReadOptions(cancel=cancel))
    with pytest.raises(ReadCancelledError):
        Auditfile.validate(source(SAMPLE), ReadOptions(mode=ReadMode.UNVERIFIED, cancel=cancel))
    with pytest.raises(ReadCancelledError):
        Auditfile.open(source(SAMPLE), ReadOptions(cancel=cancel))


def test_a_failing_abort_does_not_hide_the_original_error():
    with pytest.raises(SinkAbortError) as raised:
        Auditfile.read(source(SAMPLE), FailingSink(abort_fails=True), ReadOptions(batch_size=1))

    assert str(raised.value.error) == "database gone"
    assert str(raised.value.abort_error) == "rollback failed"


def test_an_abort_that_succeeds_reraises_the_original_error():
    with pytest.raises(TimeoutError, match="database gone"):
        Auditfile.read(source(SAMPLE), FailingSink(abort_fails=False), ReadOptions(batch_size=1))


def test_a_custom_xsd_factory_is_called_once():
    calls = []

    def xsd():
        calls.append(1)
        return AuditfileVersion.parse("xaf/3.2").open_xsd()

    report = Auditfile.validate(source(SAMPLE), ReadOptions(xsd=xsd))

    assert len(calls) == 1
    assert not any(d.code in ("XAF-D001", "XAF-D002", "XAF-X002") for d in report.diagnostics), report.diagnostics


def test_a_missing_required_field_is_reported_without_the_xsd():
    at = SAMPLE_TEXT.index("<amnt>327.00</amnt>", SAMPLE_TEXT.index("<trLine>"))
    missing = SAMPLE_TEXT[:at] + SAMPLE_TEXT[at + len("<amnt>327.00</amnt>") :]

    report = Auditfile.validate(from_text(missing), UNVERIFIED)

    found = [d for d in report.diagnostics if d.code == "XAF-S006"]
    assert len(found) == 1, report.diagnostics
    assert "amnt" in found[0].message
    assert "tr_line" in found[0].message


def test_a_file_with_all_required_fields_has_no_missing_fields():
    report = Auditfile.validate(source(SAMPLE), UNVERIFIED)

    assert not any(d.code == "XAF-S006" for d in report.diagnostics), report.diagnostics


def test_parts_in_different_encodings_are_refused():
    first, second = split(SAMPLE_TEXT.replace("Factuur 2025-001", "Factuur café"))
    report = Auditfile.read(
        parts(first.replace('encoding="UTF-8"', 'encoding="ISO-8859-1"').encode("latin-1"), ('<?xml version="1.0" encoding="UTF-8"?>' + second).encode("utf-8")),
        MemorySink(),
        UNVERIFIED,
    )

    found = [d for d in report.diagnostics if d.code == "XAF-X004"]
    assert len(found) == 1, report.diagnostics
    assert found[0].severity is DiagnosticSeverity.FATAL
    assert found[0].part == 2
    assert "UTF-8 (part 1: ISO-8859-1)" in found[0].message
    assert not report.loaded


@pytest.mark.parametrize(("name", "codec"), [("ISO-8859-1", "latin-1"), ("UTF-16", "utf-16")])
def test_parts_in_the_same_encoding_are_read_correctly(name, codec):
    first, second = split(SAMPLE_TEXT.replace("Factuur 2025-001", "Factuur café").replace('encoding="UTF-8"', f'encoding="{name}"'))
    sink = MemorySink()

    report = Auditfile.read(parts(first.encode(codec), f'<?xml version="1.0" encoding="{name}"?>{second}'.encode(codec)), sink, UNVERIFIED)

    assert not any(d.severity is DiagnosticSeverity.FATAL for d in report.diagnostics), report.diagnostics
    assert any(r["desc"] == "Factuur café" for r in sink.rows("tr_line"))


def test_a_schema_in_an_unknown_format_is_refused():
    schema = Auditfile.scan(source(SAMPLE)).schema
    assert schema is not None
    json = schema.to_json()

    assert AuditfileSchema.from_json(json).to_json() == json
    for broken in (
        json.replace('"formatVersion":1', '"formatVersion":2'),
        json.replace('"formatVersion":1,', ""),
        json.replace('"type":"decimal"', '"type":"money"', 1),
        json.replace('"source":"official"', '"source":"guessed"'),
    ):
        assert broken != json
        with pytest.raises(JsonFormatError):
            AuditfileSchema.from_json(broken)
