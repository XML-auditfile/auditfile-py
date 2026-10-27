import sys
from decimal import Decimal

import pytest

from conftest import SAMPLE, source
from xml_auditfile import Auditfile, AuditfileVersion, MemorySink, TextLanguage

pl = pytest.importorskip("polars")


@pytest.fixture(scope="module")
def sink():
    result = MemorySink()
    report = Auditfile.read(source(SAMPLE), result)
    assert report.loaded
    return result


def test_a_table_starts_with_the_ids_and_has_typed_columns(sink):
    lines = sink.to_polars("tr_line")

    assert lines.height == len(sink.rows("tr_line"))
    assert lines.columns == ["_id", "_parent_id", *(c.name for c in sink.schema.table("tr_line").columns)]
    assert lines["_id"].to_list() == [r.id for r in sink.rows("tr_line")]
    assert lines.schema["acc_id"] == pl.String
    assert lines.schema["_parent_id"] == pl.Int64
    assert lines.schema["eff_date"] == pl.Date
    assert isinstance(lines.schema["amnt"], pl.Decimal)
    assert lines["amnt"].to_list() == [r["amnt"] for r in sink.rows("tr_line")]
    assert lines["amnt"].sum() == sum(r["amnt"] for r in sink.rows("tr_line"))


def test_a_child_table_joins_its_parent(sink):
    lines = sink.to_polars("tr_line")
    transactions = sink.to_polars("transaction")

    joined = lines.join(transactions, left_on="_parent_id", right_on="_id", how="left", suffix="_transaction")

    first = sink.rows("tr_line")[0]
    parent = next(r for r in sink.rows("transaction") if r.id == first.parent_id)
    assert joined.filter(pl.col("_id") == first.id)["nr_transaction"].item() == parent["nr"]


def test_decimals_can_be_floats(sink):
    lines = sink.to_polars("tr_line", decimals="float")

    assert lines.schema["amnt"] == pl.Float64
    assert lines["amnt"][0] == float(sink.rows("tr_line")[0]["amnt"])


def test_decimals_of_different_scales_stay_exact():
    from xml_auditfile._frames import polars_frame
    from xml_auditfile.definitions import ColumnType

    frame = polars_frame([("amount", ColumnType.DECIMAL)], [(Decimal("1.5"),), (Decimal("2.25"),), (None,)], "decimal")

    assert frame["amount"].to_list() == [Decimal("1.50"), Decimal("2.25"), None]
    assert frame["amount"].sum() == Decimal("3.75")


def test_a_table_without_rows_has_all_columns(sink):
    empty = next(t for t in sink.schema.tables if not sink.rows(t.name))

    result = sink.to_polars(empty.name)

    assert result.height == 0
    assert result.columns == ["_id", "_parent_id", *(c.name for c in empty.columns)]


def test_an_unknown_table_or_option_is_refused(sink):
    with pytest.raises(KeyError, match="no table 'nothing'"):
        sink.to_polars("nothing")

    with pytest.raises(ValueError, match="decimals"):
        sink.to_polars("tr_line", decimals="money")


def test_a_view_has_its_column_names_or_labels(sink):
    result = sink.run(AuditfileVersion.Financial.V3_2.find_view("ledger"))

    frame = result.to_polars()
    labelled = result.to_polars(labels=TextLanguage.DUTCH)

    assert frame.columns == [c.name for c in result.columns]
    assert labelled.columns[-1] == "Bedrag"
    assert frame.height == len(result.rows)
    assert frame["amount"].sum() == sum(r[-1] for r in result.rows)
    assert frame.schema["date"] == pl.Date
    assert frame.schema["period"] == pl.Int64


def test_without_polars_the_error_says_how_to_install_it(sink, monkeypatch):
    monkeypatch.setitem(sys.modules, "polars", None)

    with pytest.raises(ImportError, match=r"pip install xml-auditfile\[polars\]"):
        sink.to_polars("tr_line")
