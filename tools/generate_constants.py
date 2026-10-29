"""Writes the version constants in src/xml_auditfile/versions.py from the registry of auditfile-spec.

Run after tools/fetch-spec.ps1, whenever spec.lock.json changes; the auditfile-spec update workflow does this too.
Only the parts between the "<generated>" markers are replaced. With --check nothing is written: the exit code is 1
when the constants differ from the registry.
Override the location of the spec with SPEC_ROOT=<path>.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "xml_auditfile" / "versions.py"

# The registry has no display names; a type that is not here gets its code.
TYPE_NAMES = {
    "xaf": "Auditfile Financieel",
    "xaa": "Auditfile Afrekensystemen",
    "xab": "Auditfile Boordcomputer Taxi",
    "xar": "Auditfile Ritregistratie",
    "xas": "Auditfile Salaris",
}


def version_order(version: str) -> list[tuple[int, str]]:
    return [(int(part), "") if part.isdigit() else (0, part) for part in re.split(r"[._-]", version)]


def groups(registry: dict) -> list[tuple[str, str, list[dict]]]:
    """(class name, docstring, standards) per type, in registry order, the versions in order."""
    by_type: dict[str, list[dict]] = {}
    for standard in registry["standards"]:
        by_type.setdefault(standard["type"], []).append(standard)

    result = []
    for code in by_type:
        standards = sorted(by_type[code], key=lambda s: version_order(s["version"]))
        type_name = standards[0]["typeName"]
        name = type_name[0].upper() + type_name[1:]
        doc = f"{TYPE_NAMES.get(code, 'Auditfile')} ({code.upper()})."
        result.append((name, doc, standards))

    return result


def constant_name(version: str) -> str:
    return "V" + re.sub(r"[^0-9A-Za-z]", "_", version)


def constants(registry: dict) -> str:
    blocks = []
    for name, doc, standards in groups(registry):
        lines = [f"    class {name}:", f'        """{doc}"""', ""]
        lines += [f'        {constant_name(s["version"])} = _Constant("{s["key"]}")' for s in standards]
        blocks.append("\n".join(lines) + "\n")

    return "\n".join(blocks)


def version_groups(registry: dict) -> str:
    lines = ["VERSION_GROUPS = ("]
    lines += [f"    AuditfileVersion.{name}," for name, _, _ in groups(registry)]
    return "\n".join(lines) + "\n)\n"


def replace(text: str, region: str, content: str) -> str:
    pattern = re.compile(rf"(^[ \t]*# <generated: {region}>\n).*?(^[ \t]*# </generated: {region}>$)", re.DOTALL | re.MULTILINE)
    if not pattern.search(text):
        raise SystemExit(f"{TARGET.relative_to(ROOT)} has no '<generated: {region}>' markers.")

    return pattern.sub(lambda m: m.group(1) + content + m.group(2), text, count=1)


def main() -> int:
    spec_root = (ROOT / os.environ.get("SPEC_ROOT", "spec")).resolve()
    registry_path = spec_root / "generated" / "registry.json"
    if not registry_path.is_file():
        print(f"auditfile-spec not found in '{spec_root}'. Run: pwsh tools/fetch-spec.ps1", file=sys.stderr)
        return 1

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    current = TARGET.read_text(encoding="utf-8")
    text = replace(current, "constants", constants(registry))
    text = replace(text, "groups", version_groups(registry))

    if "--check" in sys.argv[1:]:
        if text != current:
            print(
                f"The version constants in {TARGET.relative_to(ROOT).as_posix()} differ from the registry. "
                "Run: python tools/generate_constants.py",
                file=sys.stderr,
            )
            return 1

        print("The version constants match the registry.")
        return 0

    if text != current:
        TARGET.write_text(text, encoding="utf-8", newline="\n")
        print(f"Updated the version constants in {TARGET.relative_to(ROOT).as_posix()}.")
    else:
        print("The version constants are up to date.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
