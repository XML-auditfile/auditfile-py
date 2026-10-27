"""The XAF 3.2 sample of this repository (tests/data), with two intended findings (XAF-R001, XAF-R003),
and variations of it written to a temporary folder."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from xml_auditfile import AuditfileSource

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL_NAMESPACE = "http://www.auditfiles.nl/XAF/3.2"
SAMPLE = ROOT / "tests" / "data" / "xaf-3.2-sample.xaf"
SAMPLE_TEXT = SAMPLE.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


_RUN_FOLDER = Path(tempfile.mkdtemp(prefix="xmlauditfile-py-tests-"))
"""One folder per test run, removed when it ends."""
atexit.register(shutil.rmtree, _RUN_FOLDER, ignore_errors=True)


def write(name: str, content: str) -> Path:
    path = Path(tempfile.mkdtemp(dir=_RUN_FOLDER)) / name
    path.write_bytes(content.encode("utf-8"))
    return path


def source(path: Path) -> AuditfileSource:
    """A file on disk as a source; the reader itself never opens files."""
    return AuditfileSource.from_factory(lambda: open(path, "rb"), path.name)


def with_namespace(ns: str) -> Path:
    return write("namespace.xaf", SAMPLE_TEXT.replace(f'xmlns="{OFFICIAL_NAMESPACE}"', f'xmlns="{ns}"'))
