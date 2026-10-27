"""Copies auditfile-spec/generated/ (fetched into spec/ by tools/fetch-spec.ps1) into the package as src/xml_auditfile/_spec/.

The reader reads its definitions only from there, never from elsewhere on disk.
Override the location of the spec with SPEC_ROOT=<path>.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    spec_root = (ROOT / os.environ.get("SPEC_ROOT", "spec")).resolve()
    generated = spec_root / "generated"
    if not (generated / "registry.json").is_file():
        print(f"auditfile-spec not found in '{spec_root}'. Run: pwsh tools/fetch-spec.ps1", file=sys.stderr)
        return 1

    target = ROOT / "src" / "xml_auditfile" / "_spec"
    if target.exists():
        shutil.rmtree(target)

    count = 0
    for path in sorted(generated.rglob("*")):
        if path.is_file() and path.suffix in (".json", ".xsd"):
            destination = target / path.relative_to(generated)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            count += 1

    lock = json.loads((ROOT / "spec.lock.json").read_text(encoding="utf-8"))
    (target / "spec.json").write_text(
        json.dumps({"repository": lock["repository"], "tag": lock["tag"], "commit": lock["commit"]}) + "\n", encoding="utf-8"
    )
    print(f"Embedded {count} files from {generated}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
