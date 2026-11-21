"""
A split audit file of complete files: every part is a valid audit file of its own, with the header, the company and
the master data repeated, and its own share of the transactions with the counts and totals of that share.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from conftest import SAMPLE, SAMPLE_TEXT, source
from xml_auditfile import Auditfile, AuditfileReport, AuditfileSource, MemorySink, ReadMode, ReadOptions
from xml_auditfile.values import to_text

_TAIL = "    </transactions>\n  </company>\n</auditfile>\n"


def _totals(xml: str) -> str:
    """Line count and debit and credit totals of the transaction lines in a piece of XML."""
    count = debit = credit = 0
    for line in re.findall(r"<trLine>(.*?)</trLine>", xml, re.DOTALL):
        count += 1
        cents = round(float(re.search(r"<amnt>([^<]*)</amnt>", line)[1]) * 100)  # type: ignore[index]
        if "<amntTp>D</amntTp>" in line:
            debit += cents
        else:
            credit += cents

    return (
        f"      <linesCount>{count}</linesCount>\n"
        f"      <totalDebit>{debit / 100:.2f}</totalDebit>\n"
        f"      <totalCredit>{credit / 100:.2f}</totalCredit>\n"
    )


def _complete_parts(
    text: str = SAMPLE_TEXT, second_counts: str | None = None, second_master_data: Callable[[str], str] = lambda xml: xml
) -> tuple[str, str]:
    """
    The sample as two complete files: the first has the first transaction of journal V, the second repeats the header,
    the company and all master data (opening balance included) and has the rest: journal V again with its second
    transaction, journal M and the subledgers. Read as one, they are the sample.
    """
    master_data = text.index("    <customersSuppliers>")
    transactions = text.index("    <transactions>")
    journal = text.index("      <journal>")
    first_transaction = text.index("        <transaction>", journal)
    second_transaction = text.index("        <transaction>", text.index("</transaction>", first_transaction))
    end = text.index("    </transactions>")

    start = text[:master_data]
    master = text[master_data:transactions]
    journal_head = text[journal:first_transaction]
    first = f"{journal_head}{text[first_transaction:second_transaction]}      </journal>\n"
    second = f"{journal_head}{text[second_transaction:end]}"
    return (
        f"{start}{master}    <transactions>\n{_totals(first)}{first}{_TAIL}",
        f"{start}{second_master_data(master)}    <transactions>\n{second_counts or _totals(second)}{second}{_TAIL}",
    )


def _from_parts(parts: tuple[str, str]) -> AuditfileSource:
    first, second = (part.encode("utf-8") for part in parts)
    return AuditfileSource.from_factories(("split.xaf", lambda: first), ("split-2.xaf", lambda: second))


def _tree(sink: MemorySink, tables: list[str]) -> dict[str, list[str]]:
    """Every row by its place in the tree (table and ordinal from the root down), with its values: independent of ids."""
    by_id = {row.id: row for table in tables for row in sink.rows(table)}
    places: dict[int, str] = {}

    def place_of(row) -> str:  # type: ignore[no-untyped-def]
        place = places.get(row.id)
        if place is None:
            parent = "" if row.parent_id is None else place_of(by_id[row.parent_id])
            place = f"{parent}/{row.table.name}#{row.ordinal}"
            places[row.id] = place

        return place

    return {place_of(row): [to_text(v) for v in row.values] for row in by_id.values()}


def _codes(report: AuditfileReport) -> list[str]:
    return sorted(d.code for d in report.diagnostics)


def test_a_split_audit_file_of_complete_files_reads_as_the_whole_file():
    whole = MemorySink()
    split = MemorySink()
    whole_report = Auditfile.read(source(SAMPLE), whole)
    report = Auditfile.read(_from_parts(_complete_parts()), split)

    assert report.loaded
    assert len(report.parts) == 2
    assert _codes(report) == _codes(whole_report)
    assert dict(report.row_counts) == dict(whole_report.row_counts)

    # The header and the company are those of the first part, with its counts and totals.
    tables = list(whole_report.row_counts)
    expected = _tree(whole, tables)
    actual = _tree(split, tables)
    company = next(place for place in expected if place.endswith("/company#1"))
    for name in ("transactions_lines_count", "transactions_total_debit", "transactions_total_credit"):
        index = split.rows("company")[0].table.find_column(name).index  # type: ignore[union-attr]
        expected[company][index] = actual[company][index]

    assert actual == expected
    assert split.rows("company")[0].get_integer("transactions_lines_count") == 3
    assert [r.part for r in split.rows("transaction")] == [1, 2, 2]


def test_each_part_of_a_split_audit_file_of_complete_files_is_checked_against_its_own_counts_and_totals():
    counts = "      <linesCount>5</linesCount>\n      <totalDebit>0.00</totalDebit>\n      <totalCredit>0.00</totalCredit>\n"

    report = Auditfile.validate(_from_parts(_complete_parts(second_counts=counts)))

    found = [d for d in report.diagnostics if d.code == "XAF-R002"]
    assert [d.part for d in found] == [2, 2, 2]
    assert re.search(r"is 5, maar de regels geven 4", found[0].message)


def test_master_data_that_only_a_later_part_has_is_added_and_repeated_master_data_is_not():
    new_customer = "      <customerSupplier>\n        <custSupID>D9999</custSupID>\n        <custSupName>Nieuwe klant</custSupName>\n      </customerSupplier>\n"
    parts = _complete_parts(second_master_data=lambda xml: xml.replace("    </customersSuppliers>", f"{new_customer}    </customersSuppliers>"))
    whole = MemorySink()
    split = MemorySink()
    Auditfile.read(source(SAMPLE), whole)

    report = Auditfile.read(_from_parts(parts), split)

    # The sample refers to the unknown customer D9999 (XAF-R003); the second part has it.
    assert _codes(report) == ["XAF-R001"]
    customers = split.rows("customers_suppliers_customer_supplier")
    assert len(customers) == len(whole.rows("customers_suppliers_customer_supplier")) + 1
    assert customers[-1]["cust_sup_id"] == "D9999"
    assert customers[-1].part == 2
    for table in ("ledger_account", "ob_line", "customers_suppliers_customer_supplier_street_address"):
        assert len(split.rows(table)) == len(whole.rows(table))


def test_complete_parts_read_from_single_use_streams_are_validated_during_loading():
    first, second = (part.encode("utf-8") for part in _complete_parts())
    whole = Auditfile.validate(source(SAMPLE))

    report = Auditfile.read(AuditfileSource.from_streams(("split.xaf", first), ("split-2.xaf", second)), MemorySink())

    # No XSD findings: every part is valid by itself. The findings of the sample are errors, so the verified load is aborted.
    assert _codes(report) == sorted([*_codes(whole), "XAF-S005"])
    assert dict(report.row_counts) == dict(whole.row_counts)


def test_the_scan_of_complete_parts_has_no_row_counts_in_advance():
    report = Auditfile.scan(_from_parts(_complete_parts()))

    assert report.schema is not None and not report.schema.has_row_counts
    assert len(report.row_counts) == 0
    assert len(report.parts) == 2
    assert list(report.diagnostics) == []


def test_a_complete_part_that_is_not_valid_by_itself_stops_a_verified_read():
    first, second = _complete_parts()
    invalid = re.sub(r"<companyName>[^<]*</companyName>", "", second, count=1)

    report = Auditfile.read(_from_parts((first, invalid)), MemorySink())

    assert not report.loaded
    assert [(d.code, d.part) for d in report.diagnostics] == [("XAF-D001", 2)]
    # Line numbers continue over the parts, as for continuation parts: the second part starts on the last line of the first.
    line = invalid[: invalid.index("<taxRegistrationCountry>")].count("\n") + 1
    assert report.diagnostics[0].line == first.count("\n") + line


def test_a_later_part_with_another_root_element_or_namespace_is_refused():
    first, second = _complete_parts()
    other = second.replace('xmlns="http://www.auditfiles.nl/XAF/3.2"', 'xmlns="http://www.auditfiles.nl/XAF/4.0"')

    for mode in (ReadMode.VERIFIED, ReadMode.UNVERIFIED):
        report = Auditfile.read(_from_parts((first, other)), MemorySink(), ReadOptions(mode=mode))

        assert not report.loaded
        found = next(d for d in report.diagnostics if d.code == "XAF-V005")
        assert found.part == 2
        assert "{http://www.auditfiles.nl/XAF/4.0}auditfile" in found.message

    continuation = Auditfile.read(_from_parts((first, "<!-- Vervolgbestand 2 van 2 -->\n<transaction/>\n")), MemorySink(), ReadOptions(mode=ReadMode.UNVERIFIED))
    assert any(d.code == "XAF-V005" and d.part == 2 for d in continuation.diagnostics)


def test_a_later_part_that_starts_with_its_root_element_is_read():
    # Without an XML declaration the root element starts on the last line of the part before, which ends with a line break.
    first, second = _complete_parts()
    whole = Auditfile.validate(source(SAMPLE))

    report = Auditfile.validate(_from_parts((first, second[second.index("<auditfile") :])))

    assert _codes(report) == _codes(whole)
    assert dict(report.row_counts) == dict(whole.row_counts)


def test_text_outside_the_root_element_of_a_part_is_refused():
    first, second = _complete_parts()

    report = Auditfile.read(_from_parts((first, "Vervolg\n" + second)), MemorySink(), ReadOptions(mode=ReadMode.UNVERIFIED))

    assert not report.loaded
    assert (report.diagnostics[0].code, report.diagnostics[0].part) == ("XAF-X001", 2)


def test_two_root_elements_in_one_part_are_still_refused():
    first, second = _complete_parts()
    data = (first + re.sub(r"^<\?xml[^>]*\?>", "", second)).encode("utf-8")

    report = Auditfile.read(AuditfileSource.from_factory(lambda: data, "two.xaf"), MemorySink(), ReadOptions(mode=ReadMode.UNVERIFIED))

    assert not report.loaded
    assert report.diagnostics[0].code == "XAF-X001"
