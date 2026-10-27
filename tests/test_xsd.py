import re

import pytest

from conftest import OFFICIAL, OFFICIAL_TEXT, source
from xml_auditfile import Auditfile, AuditfileSource, AuditfileVersion
from xml_auditfile.xsd._regex import translate_pattern


def xsd_errors(text):
    data = text.encode("utf-8")
    report = Auditfile.scan(AuditfileSource.from_factory(lambda: data, "variant.xaf"))
    return [d.message for d in report.diagnostics if d.code == "XAF-D001"]


def test_the_official_test_file_is_valid_against_its_xsd():
    report = Auditfile.scan(source(OFFICIAL))
    assert [d for d in report.diagnostics if d.code.startswith("XAF-D")] == []


def test_facets_are_checked():
    errors = xsd_errors(OFFICIAL_TEXT.replace("<fiscalYear>2016</fiscalYear>", "<fiscalYear>2016-2017-2018</fiscalYear>", 1))
    assert len(errors) == 1
    assert "maxLength" in errors[0]


def test_enumerations_are_checked():
    errors = xsd_errors(OFFICIAL_TEXT.replace("<curCode>EUR</curCode>", "<curCode>XXX</curCode>", 1))
    assert len(errors) == 1
    assert "enumeration" in errors[0]


def test_the_element_order_of_a_sequence_is_checked():
    errors = xsd_errors(re.sub(r"<startDate>[^<]*</startDate>", "", OFFICIAL_TEXT, count=1))
    assert len(errors) == 1
    assert re.search("endDate.*not expected.*startDate", errors[0])


def test_dates_must_be_real_dates():
    errors = xsd_errors(OFFICIAL_TEXT.replace("<startDate>2016-01-01</startDate>", "<startDate>2016-02-30</startDate>", 1))
    assert len(errors) == 1
    assert "xs:date" in errors[0]


def test_text_in_element_only_content_is_refused():
    errors = xsd_errors(OFFICIAL_TEXT.replace("<header>", "<header>text", 1))
    assert len(errors) == 1
    assert "element-only" in errors[0]


def test_identity_constraints_with_prefixed_selectors_are_enforced():
    # XAA 3.2 uses prefixed XPath steps, so its keys select elements; XAF 3.2 does not.
    schema_set = AuditfileVersion.CashRegister.V3_2.create_schema_set()
    root = next(iter(schema_set.elements.values()))
    assert any(
        c.kind == "key" and all(s.startswith("{http://www.auditfiles.nl/XAA/3.2}") for s in c.selector[0].steps) for c in root.constraints
    )


def test_xsd_patterns_are_anchored_and_literal_where_xsd_is_literal():
    assert translate_pattern(r"\d{4}|(\d{4}\-\d{4})").fullmatch("2008-2009")
    assert not translate_pattern(r"\d{4}|(\d{4}\-\d{4})").fullmatch("x2008")
    assert translate_pattern(r"[\-+]?[0-9]{1,3}[.][0-9]{6}").fullmatch("-12.123456")
    assert translate_pattern("a$").fullmatch("a$")
    with pytest.raises(ValueError):
        translate_pattern("[a-z-[aeiou]]")
    with pytest.raises(ValueError):
        translate_pattern(r"\w+")
