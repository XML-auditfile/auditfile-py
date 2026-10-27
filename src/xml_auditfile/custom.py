"""Dialects and views added at runtime, next to the built-in ones of auditfile-spec."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from . import _json as j
from .definitions import Dialect, ViewDefinition
from .diagnostics import _catalog

if TYPE_CHECKING:
    from .versions import AuditfileVersion

_T = TypeVar("_T", Dialect, ViewDefinition)


class _CustomList(Generic[_T]):
    """
    Added definitions by version key or type. The tuples are replaced on every change, never modified, so a caller
    that already took one keeps a consistent list.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._lists: dict[str, tuple[_T, ...]] = {}

    def get(self, key: str) -> tuple[_T, ...]:
        with self._lock:
            return self._lists.get(key, ())

    def add(self, key: str, added: tuple[_T, ...], existing: Callable[[], Iterable[str]], what: str) -> None:
        with self._lock:
            # Under the lock, so two files with the same name cannot both pass.
            names = set(existing())
            for definition in added:
                if definition.name in names:
                    raise ValueError(f"{what} '{definition.name}' already exists for {key}.")

                names.add(definition.name)

            self._lists[key] = self.get(key) + added

    def remove(self, key: str, name: str) -> bool:
        with self._lock:
            current = self.get(key)
            kept = tuple(d for d in current if d.name != name)
            if len(kept) == len(current):
                return False

            self._lists[key] = kept
            return True

    def clear(self) -> None:
        with self._lock:
            self._lists.clear()


def _parse(json: object, what: str) -> dict[str, Any]:
    """The JSON as an object: text is parsed, a mapping is used as it is."""
    if isinstance(json, bytes):
        json = json.decode("utf-8-sig")

    if isinstance(json, str):
        parsed = j.parse_object(json, what)
    elif isinstance(json, Mapping):
        parsed = dict(json)
    else:
        raise TypeError(f"{what} must be JSON text or a parsed JSON object.")

    version = j.optional_integer(parsed, "formatVersion")
    if version is not None and version != 1:
        raise j.JsonFormatError(f"{what}: formatVersion {version} is not supported; this reader reads format 1.")

    return parsed


def _to_version(version: object) -> AuditfileVersion:
    from .versions import AuditfileVersion

    if isinstance(version, str):
        return AuditfileVersion.parse(version)

    if not isinstance(version, AuditfileVersion):
        raise TypeError('version must be an AuditfileVersion or a key such as "xaf/3.2".')

    return version


def _check_codes(dialect: Dialect) -> None:
    codes = [code for code, entry in _catalog().items() if entry.get("layer") == "dialect"]
    for correction in dialect.corrections:
        if correction.code not in codes:
            raise j.JsonFormatError(f"Dialect '{dialect.name}': {correction.code} is not a dialect diagnostic ({', '.join(codes)}).")


class DialectRegistry:
    """
    Dialects added at runtime, available as ``Auditfile.dialects``. They are used like the built-in dialects of the
    version: recognized by namespace or header/softwareDesc, unless the read option ``apply_dialects`` is False.
    Thread-safe; a read that has started keeps the dialects it found.
    """

    def __init__(self) -> None:
        self._dialects: _CustomList[Dialect] = _CustomList()

    def add(self, version: AuditfileVersion | str, json: str | bytes | Mapping[str, Any]) -> tuple[Dialect, ...]:
        """
        Adds the dialects of a file in the format of dialects.json of auditfile-spec: ``{"formatVersion": 1, "dialects": [...]}``.
        The whole file is refused when any dialect is invalid or has a name the version already knows.

        :param version: The version the dialects are for, e.g. ``AuditfileVersion.Financial.V3_2`` or "xaf/3.2".
        :param json: JSON text (str or UTF-8 bytes), or the parsed object.
        :returns: The added dialects.
        :raises JsonFormatError: The JSON is invalid.
        :raises ValueError: The version is unknown or a dialect name is taken.
        """
        target = _to_version(version)
        added = tuple(Dialect(d) for d in j.objects(_parse(json, "Dialects"), "dialects"))
        for dialect in added:
            _check_codes(dialect)

        self._dialects.add(target.key, added, lambda: (d.name for d in target.dialects), "Dialect")
        return added

    def remove(self, version: AuditfileVersion | str, name: str) -> bool:
        """Removes an added dialect; built-in dialects cannot be removed. Returns True when it was found and removed."""
        return self._dialects.remove(_to_version(version).key, name)

    def clear(self) -> None:
        """Removes all added dialects, of every version."""
        self._dialects.clear()

    def _for(self, key: str) -> tuple[Dialect, ...]:
        return self._dialects.get(key)


class ViewRegistry:
    """
    Views added at runtime, available as ``Auditfile.views``. They appear in ``AuditfileVersion.views`` and
    ``find_view`` of every version of their type, after the built-in views. Thread-safe.
    """

    def __init__(self) -> None:
        self._views: _CustomList[ViewDefinition] = _CustomList()

    def add(self, json: str | bytes | Mapping[str, Any]) -> tuple[ViewDefinition, ...]:
        """
        Adds the views of a file in the format of views/<type>.json of auditfile-spec:
        ``{"formatVersion": 1, "type": "xaf", "views": [...]}``. The whole file is refused when any view is invalid
        or has a name the type already knows. Whether a view fits a version is decided when it runs (XAF-Q001).

        :param json: JSON text (str or UTF-8 bytes), or the parsed object.
        :returns: The added views.
        :raises JsonFormatError: The JSON is invalid.
        :raises ValueError: The type is unknown or a view name is taken.
        """
        from .versions import AuditfileVersion

        parsed = _parse(json, "Views")
        type_ = j.string(parsed, "type")
        version = next((v for v in AuditfileVersion.all() if v.type == type_), None)
        if version is None:
            raise ValueError(f"Unknown audit file type '{type_}'.")

        added = tuple(ViewDefinition(v) for v in j.objects(parsed, "views"))
        self._views.add(type_, added, lambda: (v.name for v in version.views), "View")
        return added

    def remove(self, type: str, name: str) -> bool:
        """Removes an added view of a type such as "xaf"; built-in views cannot be removed. Returns True when it was found and removed."""
        return self._views.remove(type, name)

    def clear(self) -> None:
        """Removes all added views, of every type."""
        self._views.clear()

    def _for(self, type: str) -> tuple[ViewDefinition, ...]:
        return self._views.get(type)


dialects = DialectRegistry()
"""The process-wide dialect registry, also ``Auditfile.dialects``."""

views = ViewRegistry()
"""The process-wide view registry, also ``Auditfile.views``."""
