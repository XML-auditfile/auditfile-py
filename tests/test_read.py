import datetime as dt
import hashlib
import io
from decimal import Decimal

from conftest import OFFICIAL, OFFICIAL_TEXT, source, write
from xml_auditfile import Auditfile, AuditfileSchema, AuditfileSource, MemorySink, ReadMode, ReadOptions, VersionSource


def test_reads_the_official_test_file():
    sink = MemorySink()
    report = Auditfile.read(source(OFFICIAL), sink)

    assert report.loaded
    assert report.schema.source is VersionSource.OFFICIAL
    assert len(report.row_counts) == 27
    assert sink.rows("tr_line")[0]["amnt"] == Decimal("3.14")
    assert sink.rows("tr_line")[0]["eff_date"] == dt.date(1967, 8, 13)


def test_business_rules_find_the_unbalanced_transaction_in_the_official_test_file():
    report = Auditfile.validate(source(OFFICIAL))

    assert any(d.code == "XAF-R001" for d in report.diagnostics)
    assert any(d.code == "XAF-R003" for d in report.diagnostics)
    assert not report.is_valid


def test_rows_know_their_parent_and_order():
    sink = MemorySink()
    Auditfile.read(source(OFFICIAL), sink)

    transaction = sink.rows("transaction")[0]
    line = sink.rows("tr_line")[0]
    assert line.parent_id == transaction.id
    assert line.ordinal == 1
    assert line.id > transaction.id


def test_split_files_read_as_one_audit_file():
    cut = OFFICIAL_TEXT.index("<transaction>")
    first = OFFICIAL_TEXT[:cut].encode("utf-8")
    second = ("<!-- Vervolgbestand 2 van 2 -->\n" + OFFICIAL_TEXT[cut:]).encode("utf-8")

    whole = MemorySink()
    split = MemorySink()
    Auditfile.read(source(OFFICIAL), whole)
    report = Auditfile.read(AuditfileSource.from_factories(("split.xaf", lambda: first), ("split-2.xaf", lambda: second)), split)

    assert len(report.parts) == 2
    assert dict(report.row_counts) == dict(whole.report.row_counts)
    assert split.rows("tr_line")[0].part == 2


def test_single_use_stream_is_validated_during_load_and_aborted_when_invalid():
    invalid = OFFICIAL_TEXT.replace("<header>", "<header><vendorExtra>1</vendorExtra>", 1)
    stream = io.BytesIO(invalid.encode("utf-8"))
    sink = MemorySink()

    report = Auditfile.read(AuditfileSource.from_stream(stream), sink)

    assert sink.is_aborted
    assert any(d.code == "XAF-S005" for d in report.diagnostics)
    assert any(d.code == "XAF-D001" for d in report.diagnostics)
    assert not stream.closed


def test_unverified_mode_skips_unknown_elements_with_a_warning():
    path = write("extra.xaf", OFFICIAL_TEXT.replace("<header>", "<header><vendorExtra>1</vendorExtra>", 1))

    report = Auditfile.read(source(path), MemorySink(), ReadOptions(mode=ReadMode.UNVERIFIED))

    assert report.loaded
    assert any(d.code == "XAF-S003" and d.path == "auditfile/header/vendorExtra" for d in report.diagnostics)


def test_scan_counts_rows_and_the_schema_survives_json():
    report = Auditfile.scan(source(OFFICIAL))
    text = report.schema.to_json()
    back = AuditfileSchema.from_json(text)

    assert report.schema.has_row_counts
    assert report.schema.table("tr_line").row_count == 1
    assert back.to_json() == text
    assert back.table("tr_line").find_column("amnt").definition is not None


def test_parts_report_size_and_sha256_of_the_bytes_as_read():
    report = Auditfile.scan(source(OFFICIAL))
    data = OFFICIAL.read_bytes()

    assert report.parts[0].size == len(data)
    assert report.parts[0].sha256 == hashlib.sha256(data).hexdigest()


def test_files_in_a_single_byte_encoding_are_decoded():
    text = OFFICIAL_TEXT.replace('encoding="UTF-8"', 'encoding="windows-1252"', 1).replace("<companyName>", "<companyName>Café ", 1)
    data = text.encode("cp1252")
    sink = MemorySink()

    Auditfile.read(AuditfileSource.from_factory(lambda: data, "ansi.xaf"), sink)

    assert sink.rows("company")[0]["company_name"].startswith("Café ")


def test_reading_rows_yourself_sees_every_row():
    reader = Auditfile.open(source(OFFICIAL), ReadOptions(mode=ReadMode.UNVERIFIED))
    lines = sum(1 for row in reader.read_rows() if row.table.name == "tr_line")

    assert reader.schema.version_key == "xaf/3.2"
    assert lines == 1
    assert reader.report.row_counts["tr_line"] == 1
