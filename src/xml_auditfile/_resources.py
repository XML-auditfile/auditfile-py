"""The files from auditfile-spec/generated/ that are embedded in this package (tools/embed_spec.py).

Names are relative paths such as "standards/xaf/3_2/manifest.json". Only embedded files can be read:
a name that would leave the embedded folder is refused.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Any

from ._json import parse_object

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable


def _root() -> Traversable:
    return resources.files(__package__).joinpath("_spec")


def _locate(name: str) -> Traversable | None:
    parts = name.split("/")
    if not name or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts):
        return None

    node = _root()
    for part in parts:
        node = node.joinpath(part)

    return node if node.is_file() else None


def exists(name: str) -> bool:
    return _locate(name) is not None


def read_bytes(name: str) -> bytes:
    node = _locate(name)
    if node is None:
        raise FileNotFoundError(
            f"Embedded definition '{name}' is missing. Was auditfile-spec/generated/ embedded when the package was built?"
        )

    return node.read_bytes()


def read_text(name: str) -> str:
    return read_bytes(name).decode("utf-8")


@lru_cache(maxsize=None)
def read_json(name: str) -> dict[str, Any]:
    return parse_object(read_text(name), name)
