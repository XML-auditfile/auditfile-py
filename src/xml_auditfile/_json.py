"""Typed access to parsed JSON. Every accessor checks the type, so malformed input fails with a clear message."""

from __future__ import annotations

import json
from typing import Any


class JsonFormatError(ValueError):
    """JSON that does not have the expected structure."""


def parse_object(text: str, name: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise JsonFormatError(f"{name} is not valid JSON: {error}") from error

    if not isinstance(value, dict):
        raise JsonFormatError(f"{name} is not a JSON object.")

    return value


def _get(obj: dict[str, Any], key: str) -> Any:
    return obj.get(key)


def string(obj: dict[str, Any], key: str) -> str:
    value = _get(obj, key)
    if not isinstance(value, str):
        raise JsonFormatError(f"JSON: '{key}' must be a string.")

    return value


def optional_string(obj: dict[str, Any], key: str) -> str | None:
    return None if _get(obj, key) is None else string(obj, key)


def integer(obj: dict[str, Any], key: str) -> int:
    value = _get(obj, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise JsonFormatError(f"JSON: '{key}' must be an integer.")

    return value


def optional_integer(obj: dict[str, Any], key: str) -> int | None:
    return None if _get(obj, key) is None else integer(obj, key)


def boolean(obj: dict[str, Any], key: str) -> bool:
    return _get(obj, key) is True


def obj(parent: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = _get(parent, key)
    if value is None:
        return None

    if not isinstance(value, dict):
        raise JsonFormatError(f"JSON: '{key}' must be an object.")

    return value


def array(parent: dict[str, Any], key: str) -> list[Any]:
    value = _get(parent, key)
    if value is None:
        return []

    if not isinstance(value, list):
        raise JsonFormatError(f"JSON: '{key}' must be an array.")

    return value


def objects(parent: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items = array(parent, key)
    if not all(isinstance(item, dict) for item in items):
        raise JsonFormatError(f"JSON: '{key}' must contain objects.")

    return items


def strings(parent: dict[str, Any], key: str) -> list[str]:
    items = array(parent, key)
    if not all(isinstance(item, str) for item in items):
        raise JsonFormatError(f"JSON: '{key}' must contain strings.")

    return items
