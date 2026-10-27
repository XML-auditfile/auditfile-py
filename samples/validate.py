"""Validates an audit file: XSD, structure, keys and business rules.

Usage: python samples/validate.py <file.xaf> [--en] [--unverified] [--force xaf/3.2]
Exit code: 0 valid, 1 not valid, 2 usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path

from xml_auditfile import Auditfile, AuditfileSource, DiagnosticSeverity, ReadMode, ReadOptions, TextLanguage


def main(args: list[str]) -> int:
    force_index = args.index("--force") if "--force" in args else -1
    files = [a for i, a in enumerate(args) if not a.startswith("--") and (force_index < 0 or i != force_index + 1)]
    if not files:
        print("Usage: python samples/validate.py <file.xaf> [--en] [--unverified] [--force xaf/3.2]", file=sys.stderr)
        return 2

    options = ReadOptions(
        language=TextLanguage.ENGLISH if "--en" in args else TextLanguage.DUTCH,
        mode=ReadMode.UNVERIFIED if "--unverified" in args else ReadMode.VERIFIED,
        version=args[force_index + 1] if 0 <= force_index < len(args) - 1 else None,
    )

    # The reader never opens files itself: hand it a stream factory per part.
    source = AuditfileSource.from_factories(*((Path(p).name, lambda p=p: open(p, "rb")) for p in files))

    if options.mode is ReadMode.VERIFIED:
        report = Auditfile.validate(source, options)
    else:
        # Unverified: stream through all rows so every check runs, without keeping anything.
        reader = Auditfile.open(source, options)
        for _ in reader.read_rows():
            pass

        report = reader.report

    for diagnostic in report.diagnostics:
        print(diagnostic)

    errors = report.count(DiagnosticSeverity.ERROR) + report.count(DiagnosticSeverity.FATAL)
    print(f"{errors} errors, {report.count(DiagnosticSeverity.WARNING)} warnings")
    return 0 if report.is_valid else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
