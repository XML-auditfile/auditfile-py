import datetime as dt
from decimal import Decimal

import pytest

from xml_auditfile import ColumnType
from xml_auditfile._decimal import add, format_decimal
from xml_auditfile.values import to_text, try_convert


@pytest.mark.parametrize(("text", "expected"), [("123.45", "123.45"), ("+123.4567", "123.4567"), ("-123", "-123")])
def test_decimals_use_xsd_notation(text, expected):
    ok, value = try_convert(text, ColumnType.DECIMAL)
    assert ok
    assert format_decimal(value) == expected


@pytest.mark.parametrize("text", ["123,45", "1.234,56", "+ 123", "1e5", "NaN", "Infinity"])
def test_local_notation_is_rejected(text):
    assert try_convert(text, ColumnType.DECIMAL) == (False, None)


@pytest.mark.parametrize("text", ["2007-12-25", "2007-12-25+01:00", "2007-12-25Z"])
def test_dates_may_have_a_time_zone(text):
    assert try_convert(text, ColumnType.DATE) == (True, dt.date(2007, 12, 25))


def test_impossible_dates_are_rejected():
    assert try_convert("2007-02-30", ColumnType.DATE) == (False, None)


def test_empty_text_is_none_for_typed_columns():
    assert try_convert("", ColumnType.DECIMAL) == (True, None)


def test_time_ignores_the_time_zone():
    assert try_convert("18:30:00+01:00", ColumnType.TIME) == (True, dt.time(18, 30))


def test_integers_stay_within_64_bits():
    assert try_convert("9223372036854775807", ColumnType.INTEGER) == (True, 9223372036854775807)
    assert try_convert("9223372036854775808", ColumnType.INTEGER) == (False, None)


def test_decimal_arithmetic_is_exact_and_keeps_the_scale():
    total = add(Decimal("0.10"), Decimal("0.2"))
    assert format_decimal(total) == "0.30"
    assert total == Decimal("0.3")
    assert to_text(Decimal("0.0000001")) == "0.0000001"
    assert try_convert("79228162514264337593543950336", ColumnType.DECIMAL) == (False, None)


def test_date_times_keep_their_time_zone():
    ok, value = try_convert("2025-01-02T10:00:00+01:00", ColumnType.DATETIME)
    assert ok
    assert value.utcoffset() == dt.timedelta(hours=1)
