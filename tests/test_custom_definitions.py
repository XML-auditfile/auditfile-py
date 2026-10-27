"""Dialects and views added at runtime, in the format of auditfile-spec, next to the built-in ones."""

import json
import re
from decimal import Decimal

import pytest

from xml_auditfile import Auditfile, AuditfileSource, AuditfileVersion, JsonFormatError, MemorySink, ReadMode, ReadOptions

from conftest import OFFICIAL_NAMESPACE, SAMPLE_TEXT

DEVIATING_NAMESPACE = "http://www.auditfiles.nl/XAF/3.2/"

DIALECTS = {
    "formatVersion": 1,
    "dialects": [
        {
            "name": "test-package",
            "description": {"nl": "Testpakket met afwijkende namespace en decimale komma.", "en": "Test package with a deviating namespace and decimal comma."},
            "match": {"namespaces": [DEVIATING_NAMESPACE]},
            "corrections": [
                {"code": "XAF-C001", "action": "replaceNamespace", "from": DEVIATING_NAMESPACE, "to": OFFICIAL_NAMESPACE},
                {"code": "XAF-C002", "action": "decimalComma", "paths": ["auditfile/company/transactions/journal/transaction/trLine/amnt"]},
            ],
        }
    ],
}

VIEWS = {
    "formatVersion": 1,
    "type": "xaf",
    "views": [
        {
            "name": "lines_per_account",
            "label": {"nl": "Regels per rekening", "en": "Lines per account"},
            "source": "tr_line",
            "columns": [{"name": "account", "value": "tr_line.acc_id"}, {"name": "lines", "aggregate": "count"}],
            "orderBy": ["account"],
        },
        {"name": "needs_unknown_table", "source": "no_such_table", "columns": [{"name": "value", "value": "no_such_table.value"}]},
    ],
}

UNVERIFIED = ReadOptions(mode=ReadMode.UNVERIFIED)

# The sample in the deviating namespace, with decimal commas in the amounts of the transaction lines.
DEVIATING = re.sub(
    r"<trLine>[\s\S]*?</trLine>",
    lambda line: re.sub(r"<amnt>(\d+)\.(\d+)</amnt>", r"<amnt>\1,\2</amnt>", line.group(0), count=1),
    SAMPLE_TEXT.replace(f'xmlns="{OFFICIAL_NAMESPACE}"', f'xmlns="{DEVIATING_NAMESPACE}"'),
)


def from_text(text):
    data = text.encode("utf-8")
    return AuditfileSource.from_factory(lambda: data, "custom.xaf")


@pytest.fixture(autouse=True)
def clean_registries():
    yield
    Auditfile.dialects.clear()
    Auditfile.views.clear()


def test_an_added_view_is_found_and_runs_like_a_built_in_view():
    added = Auditfile.views.add(json.dumps(VIEWS))

    version = AuditfileVersion.Financial.V3_2
    assert [v.name for v in added] == ["lines_per_account", "needs_unknown_table"]
    assert version.find_view("lines_per_account") is added[0]
    assert version.find_view("trial_balance") is not None, "built-in views stay"
    assert AuditfileVersion.Financial.V4_0_3.find_view("lines_per_account") is added[0], "shared by all versions of the type"
    assert AuditfileVersion.Payroll.V2027_1_0.find_view("lines_per_account") is None, "not for another type"

    sink = MemorySink()
    Auditfile.read(from_text(SAMPLE_TEXT), sink, UNVERIFIED)
    result = sink.run(version.find_view("lines_per_account"))
    assert result.is_available
    assert result.diagnostics == ()
    lines = sink.rows("tr_line")
    assert sum(r[1] for r in result.rows) == len(lines)
    assert [r[0] for r in result.rows] == sorted({line.get_string("acc_id") for line in lines})

    unavailable = sink.run(version.find_view("needs_unknown_table"))
    assert not unavailable.is_available
    assert unavailable.diagnostics[0].code == "XAF-Q001"


def test_views_can_be_added_as_bytes_or_a_dict_and_removed_again():
    Auditfile.views.add(json.dumps(VIEWS).encode("utf-8"))
    assert Auditfile.views.remove("xaf", "lines_per_account")
    assert not Auditfile.views.remove("xaf", "lines_per_account")
    assert not Auditfile.views.remove("xaf", "trial_balance"), "built-in views cannot be removed"
    assert AuditfileVersion.Financial.V3_2.find_view("lines_per_account") is None
    assert AuditfileVersion.Financial.V3_2.find_view("needs_unknown_table") is not None

    Auditfile.views.clear()
    Auditfile.views.add(VIEWS)
    assert AuditfileVersion.Financial.V3_2.find_view("lines_per_account") is not None


def test_an_added_dialect_is_recognized_by_namespace_and_corrects_values():
    before = Auditfile.validate(from_text(DEVIATING), UNVERIFIED)
    assert before.diagnostics[0].code == "XAF-V001", "unknown without the dialect"

    (dialect,) = Auditfile.dialects.add("xaf/3.2", json.dumps(DIALECTS))
    assert dialect in AuditfileVersion.Financial.V3_2.dialects

    sink = MemorySink()
    report = Auditfile.read(from_text(DEVIATING), sink, UNVERIFIED)

    assert report.loaded, [str(d) for d in report.diagnostics]
    assert report.schema.dialect == "test-package"
    codes = [d.code for d in report.diagnostics]
    assert {"XAF-V002", "XAF-C001", "XAF-C002"} <= set(codes)
    assert "XAF-S004" not in codes, "every amount is corrected"
    first = re.search(r"<trLine>[\s\S]*?<amnt>([^<]+)</amnt>", SAMPLE_TEXT).group(1)
    assert sink.rows("tr_line")[0].get_decimal("amnt") == Decimal(first)

    ignored = Auditfile.validate(from_text(DEVIATING), ReadOptions(mode=ReadMode.UNVERIFIED, apply_dialects=False))
    assert ignored.diagnostics[0].code == "XAF-V001", "apply_dialects=False ignores added dialects too"


def test_a_removed_or_cleared_dialect_no_longer_applies():
    Auditfile.dialects.add(AuditfileVersion.Financial.V3_2, DIALECTS)
    assert Auditfile.dialects.remove("xaf/3.2", "test-package")
    assert not Auditfile.dialects.remove("xaf/3.2", "test-package")

    Auditfile.dialects.add("xaf/3.2", DIALECTS)
    Auditfile.dialects.clear()

    report = Auditfile.validate(from_text(DEVIATING), UNVERIFIED)
    assert report.diagnostics[0].code == "XAF-V001"


def test_invalid_files_are_refused_as_a_whole():
    def with_view(view):
        return {**VIEWS, "views": [VIEWS["views"][0], view]}

    def with_correction(correction):
        return {"dialects": [{**DIALECTS["dialects"][0], "corrections": [correction]}]}

    with pytest.raises(JsonFormatError):
        Auditfile.views.add("{ not json")
    with pytest.raises(JsonFormatError):
        Auditfile.views.add("[]")
    with pytest.raises(TypeError):
        Auditfile.views.add(42)
    with pytest.raises(JsonFormatError):
        Auditfile.views.add({**VIEWS, "formatVersion": 2})
    with pytest.raises(JsonFormatError):
        Auditfile.views.add({"views": VIEWS["views"]})
    with pytest.raises(ValueError, match="xyz"):
        Auditfile.views.add({**VIEWS, "type": "xyz"})
    with pytest.raises(ValueError, match="trial_balance"):
        Auditfile.views.add(with_view({"name": "trial_balance", "source": "tr_line"}))
    with pytest.raises(ValueError, match="lines_per_account"):
        Auditfile.views.add(with_view(VIEWS["views"][0]))
    with pytest.raises(JsonFormatError):
        Auditfile.views.add(with_view({"name": "x", "source": "tr_line", "columns": [{"name": "a", "aggregate": "median"}]}))
    assert AuditfileVersion.Financial.V3_2.find_view("lines_per_account") is None, "nothing of a refused file is added"

    with pytest.raises(ValueError):
        Auditfile.dialects.add("xaf/9.9", DIALECTS)
    with pytest.raises(TypeError):
        Auditfile.dialects.add(None, DIALECTS)
    with pytest.raises(JsonFormatError):
        Auditfile.dialects.add("xaf/3.2", with_correction({"code": "XAF-C002", "action": "guess", "paths": []}))
    with pytest.raises(JsonFormatError):
        Auditfile.dialects.add("xaf/3.2", with_correction({"code": "XAF-R001", "action": "decimalComma", "paths": []}))
    Auditfile.dialects.add("xaf/3.2", DIALECTS)
    with pytest.raises(ValueError, match="test-package"):
        Auditfile.dialects.add("xaf/3.2", DIALECTS)
    assert len(AuditfileVersion.Financial.V3_2.dialects) == 1
