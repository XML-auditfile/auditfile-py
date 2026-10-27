from conftest import source, with_namespace
from xml_auditfile import Auditfile, AuditfileVersion, DiagnosticSeverity, MemorySink, ReadOptions, VersionSource


def test_unknown_namespace_is_fatal():
    report = Auditfile.scan(source(with_namespace("urn:example:unknown")))

    assert any(d.code == "XAF-V001" and d.severity is DiagnosticSeverity.FATAL for d in report.diagnostics)
    assert report.schema is None


def test_forced_version_reads_an_unknown_namespace_including_xsd_validation():
    report = Auditfile.read(
        source(with_namespace("urn:example:unknown")), MemorySink(), ReadOptions(version=AuditfileVersion.Financial.V3_2)
    )

    assert report.loaded
    assert report.schema.source is VersionSource.FORCED
    assert any(d.code == "XAF-V003" for d in report.diagnostics)
    assert not any(d.code == "XAF-D001" for d in report.diagnostics)


def test_resolver_derives_the_version_from_the_namespace():
    report = Auditfile.scan(source(with_namespace("http://www.auditfiles.nl/XAF/3.2/")), ReadOptions(use_resolver=True))

    assert report.schema.version_key == "xaf/3.2"
    assert report.schema.source is VersionSource.RESOLVED


def test_resolver_is_off_by_default():
    report = Auditfile.scan(source(with_namespace("http://www.auditfiles.nl/XAF/3.2/")))

    assert any(d.code == "XAF-V001" for d in report.diagnostics)


def test_a_version_key_is_accepted_as_option():
    assert ReadOptions(version="xaf/3.2").version is AuditfileVersion.Financial.V3_2
