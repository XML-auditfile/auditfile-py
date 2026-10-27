import io
import re

from conftest import OFFICIAL_TEXT, source, write
from xml_auditfile import Auditfile, AuditfileSource, ReadMode, ReadOptions
from xml_auditfile.sinks import NullSink

UNVERIFIED = ReadOptions(mode=ReadMode.UNVERIFIED)


def test_document_type_definitions_are_refused():
    root = OFFICIAL_TEXT[OFFICIAL_TEXT.index("<auditfile") :]
    path = write("dtd.xaf", '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + root)

    report = Auditfile.scan(source(path))

    assert any(d.code == "XAF-X001" for d in report.diagnostics)
    assert report.schema is None


def test_entity_expansion_is_refused():
    root = OFFICIAL_TEXT[OFFICIAL_TEXT.index("<auditfile") :].replace("<fiscalYear>2016", "<fiscalYear>&lol;", 1)
    path = write("lol.xaf", '<?xml version="1.0"?><!DOCTYPE auditfile [<!ENTITY lol "lol">]>' + root)

    report = Auditfile.scan(source(path), UNVERIFIED)

    assert any(d.code == "XAF-X001" for d in report.diagnostics)
    assert report.schema is None


def test_truncated_files_stop_with_a_fatal_diagnostic():
    path = write("broken.xaf", OFFICIAL_TEXT[: len(OFFICIAL_TEXT) // 2])

    report = Auditfile.scan(source(path))

    assert any(d.code == "XAF-X001" and d.line and d.line > 0 for d in report.diagnostics)


def test_excessive_nesting_is_refused():
    deep = "<x>" * 100 + "</x>" * 100
    path = write("deep.xaf", OFFICIAL_TEXT.replace("<header>", "<header>" + deep, 1))

    report = Auditfile.scan(source(path), UNVERIFIED)

    assert any(d.code == "XAF-X003" for d in report.diagnostics)


def test_values_from_the_file_are_never_interpreted():
    path = write("amount.xaf", re.sub(r"<amnt>[^<]*</amnt>", "<amnt>{value}&amp;\\\\1</amnt>", OFFICIAL_TEXT, count=1))

    report = Auditfile.read(source(path), NullSink(), UNVERIFIED)

    assert any(d.code == "XAF-S004" and "'{value}&\\1'" in d.message for d in report.diagnostics), report.diagnostics


def test_an_unsupported_encoding_is_reported():
    path = write("encoding.xaf", OFFICIAL_TEXT.replace('encoding="UTF-8"', 'encoding="x-unknown-1"', 1))

    report = Auditfile.scan(source(path))

    assert any(d.code == "XAF-X004" for d in report.diagnostics)


def test_a_caller_owned_stream_is_left_open():
    stream = io.BytesIO(OFFICIAL_TEXT.encode("utf-8"))

    report = Auditfile.scan(AuditfileSource.from_stream(stream))

    assert report.schema.version_key == "xaf/3.2"
    assert not stream.closed


def test_text_streams_are_refused():
    try:
        Auditfile.scan(AuditfileSource.from_stream(io.StringIO(OFFICIAL_TEXT)))
    except TypeError as error:
        assert "binary" in str(error)
    else:
        raise AssertionError("A text stream must be refused.")
