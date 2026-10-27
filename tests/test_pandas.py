import sys
from decimal import Decimal

import pytest

from conftest import SAMPLE, source
from xml_auditfile import Auditfile, AuditfileVersion, MemorySink, TextLanguage

pd = pytest.importorskip("pandas")


@pytest.fixture(scope="module")
def sink():
    result = MemorySink()
    report = Auditfile.read(source(SAMPLE), result)
    assert report.loaded
    return result


def test_a_table_has_the_row_id_as_index_and_typed_columns(sink):
    lines = sink.to_pandas("tr_line")

    assert len(lines) == len(sink.rows("tr_line"))
    assert lines.index.name == "_id"
    assert list(lines.index) == [r.id for r in sink.rows("tr_line")]
    assert list(lines.columns) == ["_parent_id", *(c.name for c in sink.schema.table("tr_line").columns)]
    assert str(lines["acc_id"].dtype) == "string"
    assert str(lines["_parent_id"].dtype) == "Int64"
    assert lines["eff_date"].dtype.kind == "M"
    assert lines["amnt"].dtype == object
    assert lines["amnt"].iloc[0] == sink.rows("tr_line")[0]["amnt"]
    assert isinstance(lines["amnt"].sum(), Decimal)


def test_a_child_table_joins_its_parent(sink):
    lines = sink.to_pandas("tr_line")
    transactions = sink.to_pandas("transaction")

    joined = lines.merge(transactions, left_on="_parent_id", right_index=True, suffixes=("", "_transaction"))

    first = sink.rows("tr_line")[0]
    parent = next(r for r in sink.rows("transaction") if r.id == first.parent_id)
    assert joined["nr_transaction"].iloc[0] == parent["nr"]


def test_decimals_can_be_floats(sink):
    lines = sink.to_pandas("tr_line", decimals="float")

    assert lines["amnt"].dtype == "float64"
    assert lines["amnt"].iloc[0] == float(sink.rows("tr_line")[0]["amnt"])


def test_a_table_without_rows_has_all_columns(sink):
    empty = next(t for t in sink.schema.tables if not sink.rows(t.name))

    result = sink.to_pandas(empty.name)

    assert len(result) == 0
    assert list(result.columns) == ["_parent_id", *(c.name for c in empty.columns)]


def test_an_unknown_table_or_option_is_refused(sink):
    with pytest.raises(KeyError, match="no table 'nothing'"):
        sink.to_pandas("nothing")

    with pytest.raises(ValueError, match="decimals"):
        sink.to_pandas("tr_line", decimals="money")


def test_a_view_has_its_column_names_or_labels(sink):
    result = sink.run(AuditfileVersion.Financial.V3_2.find_view("ledger"))

    frame = result.to_pandas()
    labelled = result.to_pandas(labels=TextLanguage.DUTCH)

    assert list(frame.columns) == [c.name for c in result.columns]
    assert labelled.columns[-1] == "Bedrag"
    assert len(frame) == len(result.rows)
    assert frame["amount"].sum() == sum(r[-1] for r in result.rows)
    assert frame["date"].dtype.kind == "M"
    assert str(frame["period"].dtype) == "Int64"


def test_without_pandas_the_error_says_how_to_install_it(sink, monkeypatch):
    monkeypatch.setitem(sys.modules, "pandas", None)

    with pytest.raises(ImportError, match=r"pip install xml-auditfile\[pandas\]"):
        sink.to_pandas("tr_line")
