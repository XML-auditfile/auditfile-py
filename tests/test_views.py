from decimal import Decimal

import pytest

from xml_auditfile import Auditfile, AuditfileSource, AuditfileVersion, MemorySink

# Two journals, two periods; account 1000 is debited 350 and credited 100.
XAF = """<?xml version="1.0" encoding="UTF-8"?>
<auditfile xmlns="http://www.auditfiles.nl/XAF/3.2"><header><fiscalYear>2025</fiscalYear><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate><curCode>EUR</curCode><dateCreated>2026-01-15</dateCreated></header>
<company><companyName>Voorbeeld BV</companyName><taxRegistrationCountry>NL</taxRegistrationCountry><taxRegIdent>NL001</taxRegIdent>
<generalLedger><ledgerAccount><accID>1000</accID><accDesc>Kas</accDesc><accTp>B</accTp></ledgerAccount><ledgerAccount><accID>4000</accID><accDesc>Huur</accDesc><accTp>P</accTp></ledgerAccount><ledgerAccount><accID>8000</accID><accDesc>Omzet</accDesc><accTp>P</accTp></ledgerAccount></generalLedger>
<periods><period><periodNumber>1</periodNumber><startDatePeriod>2025-01-01</startDatePeriod><endDatePeriod>2025-06-30</endDatePeriod></period><period><periodNumber>2</periodNumber><startDatePeriod>2025-07-01</startDatePeriod><endDatePeriod>2025-12-31</endDatePeriod></period></periods>
<transactions><linesCount>6</linesCount><totalDebit>450.00</totalDebit><totalCredit>450.00</totalCredit>
<journal><jrnID>V</jrnID><desc>Verkoop</desc>
<transaction><nr>1</nr><periodNumber>1</periodNumber><trDt>2025-03-01</trDt><trLine><nr>1</nr><accID>1000</accID><docRef>F1</docRef><effDate>2025-03-01</effDate><amnt>100.00</amnt><amntTp>D</amntTp></trLine><trLine><nr>2</nr><accID>8000</accID><docRef>F1</docRef><effDate>2025-03-01</effDate><amnt>100.00</amnt><amntTp>C</amntTp></trLine></transaction>
<transaction><nr>2</nr><periodNumber>2</periodNumber><trDt>2025-09-01</trDt><trLine><nr>1</nr><accID>1000</accID><docRef>F2</docRef><effDate>2025-09-01</effDate><amnt>250.00</amnt><amntTp>D</amntTp></trLine><trLine><nr>2</nr><accID>8000</accID><docRef>F2</docRef><effDate>2025-09-01</effDate><amnt>250.00</amnt><amntTp>C</amntTp></trLine></transaction>
</journal>
<journal><jrnID>M</jrnID><desc>Memoriaal</desc>
<transaction><nr>3</nr><periodNumber>2</periodNumber><trDt>2025-10-01</trDt><trLine><nr>1</nr><accID>4000</accID><docRef>H1</docRef><effDate>2025-10-01</effDate><amnt>100.00</amnt><amntTp>D</amntTp></trLine><trLine><nr>2</nr><accID>1000</accID><docRef>H1</docRef><effDate>2025-10-01</effDate><amnt>100.00</amnt><amntTp>C</amntTp></trLine></transaction>
</journal></transactions></company></auditfile>"""


def view(name):
    return AuditfileVersion.Financial.V3_2.find_view(name)


@pytest.fixture
def sink():
    result = MemorySink()
    data = XAF.encode("utf-8")
    report = Auditfile.read(AuditfileSource.from_factory(lambda: data, "views.xaf"), result)
    assert report.loaded, [str(d) for d in report.diagnostics]
    return result


def test_trial_balance_sums_debit_credit_and_balance_per_account(sink):
    result = sink.run(view("trial_balance"))

    assert result.is_available
    assert result.diagnostics == ()
    assert [r[0] for r in result.rows] == ["1000", "4000", "8000"]
    assert result.rows[0] == ("1000", "Kas", "B", Decimal("350.00"), Decimal("100.00"), Decimal("250.00"))
    assert str(result.rows[2][5]) == "-350.00"


def test_parameters_filter_the_input_rows(sink):
    result = sink.run(view("trial_balance"), {"period_from": 2, "date_to": "2025-09-30", "period_to": None})

    assert [r[0] for r in result.rows] == ["1000", "8000"]
    assert str(result.rows[0][5]) == "250.00"


def test_ancestor_joins_reach_through_the_parent_chain(sink):
    result = sink.run(view("journal_totals"))

    memoriaal = result.rows[0]
    assert memoriaal[0] == "M"
    assert memoriaal[3] == 2
    assert str(memoriaal[6]) == "0.00"


def test_unknown_parameter_is_rejected(sink):
    with pytest.raises(ValueError):
        sink.run(view("trial_balance"), {"year": 2025})


def test_view_of_another_type_is_not_available(sink):
    payroll = next(v for v in AuditfileVersion.all() if v.type == "xas").find_view("hours")

    result = sink.run(payroll)

    assert not result.is_available
    assert result.rows == ()
    assert [d.code for d in result.diagnostics] == ["XAF-Q001"]


def test_duplicate_lookup_key_uses_the_first_row_with_a_warning(sink):
    # Loading rejects duplicate keys, so the store hands out a ledger account twice: the first copy last.
    huur = sink.rows("ledger_account")[1]

    def rows(table):
        return [*sink.rows(table), huur] if table == "ledger_account" else sink.rows(table)

    result = view("trial_balance").run(sink.schema, rows)

    assert result.rows[1][1] == "Huur"
    assert [d.code for d in result.diagnostics] == ["XAF-Q002"]
