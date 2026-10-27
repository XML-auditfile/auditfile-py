"""When the reference check stops keeping unresolved values, it says so once (XAF-R017)."""

from conftest import SAMPLE_TEXT
from xml_auditfile import Auditfile, AuditfileSource, DiagnosticSeverity, MemorySink, ReadMode, ReadOptions


def with_unknown_customers(count: int) -> str:
    """The sample with extra lines in its first transaction, each referring to a customer that does not exist."""
    lines = "".join(
        f"<trLine><nr>x</nr><accID>1300</accID><docRef>d</docRef><effDate>2025-01-15</effDate><amnt>1.00</amnt><amntTp>D</amntTp><custSupID>U{i}</custSupID></trLine>"
        for i in range(count)
    )
    end = SAMPLE_TEXT.index("</transaction>")
    return SAMPLE_TEXT[:end] + lines + SAMPLE_TEXT[end:]


def read(text: str):
    # Room for the 100,000 XAF-R003 errors: a full list would leave out the XAF-R017 warning in favour of errors.
    data = text.encode("utf-8")
    return Auditfile.read(AuditfileSource.from_factory(lambda: data, "references.xaf"), MemorySink(), ReadOptions(mode=ReadMode.UNVERIFIED, max_diagnostics=200_000))


def test_reaching_the_pending_limit_is_reported_once():
    report = read(with_unknown_customers(100_001))

    warnings = [d for d in report.diagnostics if d.code == "XAF-R017"]
    assert len(warnings) == 1
    assert warnings[0].severity is DiagnosticSeverity.WARNING
    assert "tr_line.cust_sup_id" in warnings[0].message
    assert "100000" in warnings[0].message


def test_below_the_limit_there_is_no_warning():
    report = read(with_unknown_customers(10))

    assert not any(d.code == "XAF-R017" for d in report.diagnostics)
    assert any(d.code == "XAF-R003" for d in report.diagnostics)
