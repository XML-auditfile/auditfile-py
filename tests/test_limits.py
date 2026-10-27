"""Size limits: a huge value or document is reported, never read into memory as a whole."""

from conftest import SAMPLE_TEXT
from xml_auditfile import Auditfile, AuditfileSource, DiagnosticSeverity, MemorySink, ReadMode, ReadOptions

COMPANY_NAME = "<companyName>Bakkerij De Korenschoof B.V.</companyName>"


def read(text: str, sink: MemorySink, options: ReadOptions):
    data = text.encode("utf-8")
    return Auditfile.read(AuditfileSource.from_factory(lambda: data, "limits.xaf"), sink, options)


def test_a_value_longer_than_the_maximum_is_skipped_with_a_warning():
    sink = MemorySink()

    report = read(SAMPLE_TEXT.replace(COMPANY_NAME, "<companyName>" + "x" * 70_000 + "</companyName>"), sink, ReadOptions(mode=ReadMode.UNVERIFIED))

    assert report.loaded
    assert any(
        d.code == "XAF-S008" and "company.company_name" in d.message and "70000" in d.message and "65536" in d.message for d in report.diagnostics
    ), report.diagnostics
    assert sink.rows("company")[0]["company_name"] is None


def test_the_maximum_value_length_is_an_option():
    sink = MemorySink()

    report = read(SAMPLE_TEXT, sink, ReadOptions(max_value_length=10))

    assert any(d.code == "XAF-S008" and "company.company_name" in d.message for d in report.diagnostics)
    assert sink.rows("header")[0]["cur_code"] == "EUR"


def test_a_value_of_exactly_the_maximum_is_kept():
    sink = MemorySink()

    read(SAMPLE_TEXT, sink, ReadOptions(max_value_length=len("Bakkerij De Korenschoof B.V.")))

    assert sink.rows("company")[0]["company_name"] == "Bakkerij De Korenschoof B.V."


def test_a_document_larger_than_the_maximum_stops():
    report = read(SAMPLE_TEXT, MemorySink(), ReadOptions(max_characters_in_document=5000))

    assert any(d.code == "XAF-X001" and d.severity is DiagnosticSeverity.FATAL for d in report.diagnostics)
    assert not report.loaded
