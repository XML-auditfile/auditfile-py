"""Typed accessors check the column type and name the problem; values are read in XSD notation only."""

import datetime as dt
from decimal import Decimal

import pytest

from conftest import SAMPLE, source
from xml_auditfile import Auditfile, ColumnType, MemorySink
from xml_auditfile.values import try_convert


@pytest.fixture(scope="module")
def sink() -> MemorySink:
    result = MemorySink()
    Auditfile.read(source(SAMPLE), result)
    return result


def test_typed_accessors_return_the_value(sink):
    line = sink.rows("tr_line")[0]

    assert line.get_decimal("amnt") == Decimal("327.00")
    assert line.get_string("amnt_tp") == "D"
    assert line.get_date("eff_date") == dt.date(2025, 1, 15)
    assert line.get_datetime("eff_date") == dt.date(2025, 1, 15)


def test_a_column_looked_up_once_gives_the_same_values(sink):
    rows = sink.rows("tr_line")
    amount = rows[0].table.find_column("amnt")

    assert [r.get_decimal(amount) for r in rows] == [r.get_decimal("amnt") for r in rows]
    assert rows[0][amount] == rows[0]["amnt"]


def test_a_wrong_type_is_named(sink):
    line = sink.rows("tr_line")[0]

    with pytest.raises(TypeError, match=r"tr_line\.amnt is a decimal column.*get_decimal"):
        line.get_integer("amnt")
    with pytest.raises(TypeError):
        line.get_decimal("amnt_tp")


def test_an_unknown_column_is_named_and_in_says_so(sink):
    line = sink.rows("tr_line")[0]

    with pytest.raises(KeyError, match="'amount'"):
        line.get_decimal("amount")
    assert "amount" not in line
    assert "amnt" in line


def test_a_column_of_another_table_is_refused(sink):
    amount = sink.rows("tr_line")[0].table.find_column("amnt")

    with pytest.raises(KeyError):
        sink.rows("header")[0].get_decimal(amount)


@pytest.mark.parametrize("text", ["2017-01-03T10:00:00", "2017-01-03T10:00:00.5", "2017-01-03T10:00:00Z"])
def test_date_times_use_xsd_notation(text):
    ok, value = try_convert(text, ColumnType.DATETIME)
    assert ok
    assert value.date() == dt.date(2017, 1, 3)


@pytest.mark.parametrize("text", ["18:30:00", "18:30:00+01:00", "18:30:00Z", "18:30:00.250"])
def test_time_ignores_the_fraction_and_time_zone(text):
    assert try_convert(text, ColumnType.TIME) == (True, dt.time(18, 30))


@pytest.mark.parametrize(
    ("text", "type_"),
    [
        ("03/01/2017 10:00:00", ColumnType.DATETIME),
        ("2017-01-03 10:00:00", ColumnType.DATETIME),
        ("2017-01-03", ColumnType.DATETIME),
        ("2017-01-03T10:00", ColumnType.DATETIME),
        ("18:30", ColumnType.TIME),
        ("18:30:00 pm", ColumnType.TIME),
        ("18:30:00.", ColumnType.TIME),
        ("18:30:0012", ColumnType.TIME),
    ],
)
def test_other_notations_are_rejected(text, type_):
    assert not try_convert(text, type_)[0]
