"""Reads an audit file into memory and prints what was found.

Usage: python samples/read_to_memory.py <file.xaf> [continuation files...]
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

from xml_auditfile import Auditfile, AuditfileSource, MemorySink, TextLanguage, ViewResult
from xml_auditfile.values import to_text


def main(files: list[str]) -> int:
    if not files:
        print("Usage: python samples/read_to_memory.py <file.xaf> [continuation files...]")
        return 2

    sink = MemorySink()
    # The reader never opens files itself: hand it a stream factory per part.
    source = AuditfileSource.from_factories(*((Path(p).name, lambda p=p: open(p, "rb")) for p in files))
    report = Auditfile.read(source, sink)

    schema = report.schema
    print(f"Version  : {schema.version_key if schema else '-'} ({schema.source.value if schema else '-'})")
    for part in report.parts:
        print(f"Part {part.number}   : {part.name}, {part.size} bytes, sha256 {part.sha256[:12]}…")

    print(f"Loaded   : {report.loaded} in {report.duration.total_seconds() * 1000:.0f} ms")
    print()
    for table in schema.tables if schema else ():
        if table.name in report.row_counts:
            print(f"  {table.name:<40} {report.row_counts[table.name]:>8}")

    lines = sink.rows("tr_line")
    if lines:
        line = lines[0]
        print()
        print("First transaction line:")
        for column in line.table.columns:
            value = line.values[column.index]
            if value is not None:
                label = (column.definition.label.get(TextLanguage.ENGLISH) if column.definition and column.definition.label else None) or column.name
                print(f"  {column.name:<20} {to_text(value):<14} {label}")

    # Views are reports defined by auditfile-spec, e.g. the trial balance; they run over the rows in memory.
    for view in schema.version.views if report.loaded and schema and schema.version else ():
        result = sink.run(view, language=TextLanguage.ENGLISH)
        print()
        print(f"{(view.label.get(TextLanguage.ENGLISH) if view.label else None) or view.name} ({len(result.rows)} rows)")
        for diagnostic in result.diagnostics:
            print(f"  {diagnostic}")

        print_table(result, 10)

    print()
    for diagnostic in report.diagnostics:
        print(diagnostic)

    print("Valid." if report.is_valid else "Not valid.")
    return 0 if report.is_valid else 1


def print_table(result: ViewResult, max_rows: int) -> None:
    max_width = 30
    header = [(c.label.get(TextLanguage.ENGLISH) if c.label else None) or c.name for c in result.columns]
    cells = [[to_text(v.date() if isinstance(v, dt.datetime) else v) for v in row] for row in result.rows[:max_rows]]
    widths = [min(max_width, max([len(h)] + [len(r[i]) for r in cells])) for i, h in enumerate(header)]
    numeric = [any(isinstance(r[i], (Decimal, int)) for r in result.rows) for i in range(len(header))]

    def line(values: list[str]) -> str:
        out = []
        for i, v in enumerate(values):
            v = v if len(v) <= widths[i] else v[: widths[i] - 1] + "…"
            out.append(v.rjust(widths[i]) if numeric[i] else v.ljust(widths[i]))
        return "  " + "  ".join(out)

    print(line(header))
    for row in cells:
        print(line(row))

    if len(result.rows) > max_rows:
        print(f"  … {len(result.rows) - max_rows} more")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
