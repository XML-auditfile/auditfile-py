"""The official XAF 3.2 test file from auditfile-spec, and variations of it written to a temporary folder."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from xml_auditfile import AuditfileSource

ROOT = Path(__file__).resolve().parent.parent
SPEC_ROOT = (ROOT / os.environ.get("SPEC_ROOT", "spec")).resolve()

OFFICIAL_NAMESPACE = "http://www.auditfiles.nl/XAF/3.2"
OFFICIAL = SPEC_ROOT / "sources" / "xaf" / "XAF_3.2_20170904" / "XmlAuditfileFinancieel3.2_test.xml"
OFFICIAL_TEXT = OFFICIAL.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def write(name: str, content: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix="auditfile-py-tests-")) / name
    path.write_bytes(content.encode("utf-8"))
    return path


def source(path: Path) -> AuditfileSource:
    """A file on disk as a source; the reader itself never opens files."""
    return AuditfileSource.from_factory(lambda: open(path, "rb"), path.name)


def with_namespace(ns: str) -> Path:
    return write("namespace.xaf", OFFICIAL_TEXT.replace(f'xmlns="{OFFICIAL_NAMESPACE}"', f'xmlns="{ns}"'))
