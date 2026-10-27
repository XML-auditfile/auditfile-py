import pytest

from xml_auditfile import AuditfileVersion
from xml_auditfile.versions import VERSION_GROUPS


def test_every_registry_entry_has_a_constant_and_vice_versa():
    constants = sorted(
        getattr(group, name).key for group in VERSION_GROUPS for name in vars(group) if name.startswith("V")
    )
    registry = sorted(v.key for v in AuditfileVersion.all())
    assert constants == registry


def test_xaf32_manifest_has_the_expected_structure():
    manifest = AuditfileVersion.Financial.V3_2.manifest
    assert len(manifest.tables) == 31
    assert manifest.required_tables == ("auditfile", "header", "company")
    assert manifest.find_table("tr_line").parent == "transaction"
    assert any(r.table == "ledger_account" for r in manifest.find_table("tr_line").references)


def test_xaf32_xsd_matches_its_checksum_and_compiles():
    version = AuditfileVersion.Financial.V3_2
    assert AuditfileVersion.sha256(version.open_xsd()) == version.manifest.xsd_sha256
    assert version.create_schema_set().count == 1


def test_every_version_has_a_manifest_and_a_compiling_xsd():
    for version in AuditfileVersion.all():
        assert version.manifest.tables, version.key
        assert AuditfileVersion.sha256(version.open_xsd()) == version.manifest.xsd_sha256, version.key
        assert version.create_schema_set().count > 0, version.key


def test_definitions_have_dutch_text_and_english_labels():
    amount = AuditfileVersion.Financial.V3_2.definitions["tr_line.amnt"]
    assert amount.label.english == "Amount"
    assert amount.definition.dutch == "Bedrag in locale valuta."


def test_parse_rejects_unknown_versions():
    with pytest.raises(ValueError):
        AuditfileVersion.parse("xaf/9.9")
