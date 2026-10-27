"""Audit file types and versions from the registry of auditfile-spec, and version resolution."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from . import _json as j
from . import custom
from ._resources import exists, read_bytes, read_json
from .definitions import AuditfileManifest, Dialect, DialectAction, ElementDefinition, ViewDefinition
from .diagnostics import NO_LOCATION, DiagnosticCollector
from .schema import VersionSource

if TYPE_CHECKING:
    from .options import ReadOptions
    from .xsd import SchemaSet

_registry: tuple[AuditfileVersion, ...] | None = None


class _Constant:
    """A version constant, resolved from the registry on first use."""

    def __init__(self, key: str) -> None:
        self._key = key

    def __get__(self, instance: object, owner: type) -> AuditfileVersion:
        return AuditfileVersion.parse(self._key)


class AuditfileVersion:
    """
    One audit file type and version, with its manifest, XSD, definitions and dialects.
    Use the constants, e.g. ``AuditfileVersion.Financial.V3_2``, or :meth:`parse` with "xaf/3.2".
    """

    def __init__(self, json: dict[str, Any]) -> None:
        self.key = j.string(json, "key")
        """Registry key, e.g. "xaf/3.2"."""
        self.type = j.string(json, "type")
        """Audit file type, e.g. "xaf"."""
        self.type_name = j.string(json, "typeName")
        """Type name used for version objects, e.g. "financial"."""
        self.version = j.string(json, "version")
        """Version, e.g. "3.2"."""
        self.folder = j.string(json, "folder")
        """Folder of the definitions, e.g. "standards/xaf/3_2"."""
        self.root_element = j.string(json, "rootElement")
        """Name of the root element."""
        self.provenance = j.string(json, "provenance")
        """"official" or "reconstructed"."""

        namespaces = j.objects(json, "namespaces")
        official = [n for n in namespaces if j.string(n, "kind") == "official"]
        if not official:
            raise j.JsonFormatError(f"registry.json: {self.key} has no official namespace.")

        self.namespace = j.string(official[0], "uri")
        """The official namespace."""
        self.aliases = tuple(j.string(n, "uri") for n in namespaces if j.string(n, "kind") == "alias")
        """Other namespaces known to be used for this version."""

    @staticmethod
    def all() -> tuple[AuditfileVersion, ...]:
        """All versions this reader knows, in registry order."""
        global _registry
        if _registry is None:
            _registry = tuple(AuditfileVersion(s) for s in j.objects(read_json("registry.json"), "standards"))

        return _registry

    @property
    def has_official_xsd(self) -> bool:
        """True when the XSD is the official one published by the Belastingdienst."""
        return self.provenance == "official"

    @cached_property
    def manifest(self) -> AuditfileManifest:
        """Structure: tables and columns."""
        return AuditfileManifest(read_json(f"{self.folder}/manifest.json"))

    @cached_property
    def definitions(self) -> Mapping[str, ElementDefinition]:
        """Definitions by key ("tr_line", "tr_line.amnt")."""
        entries = (ElementDefinition(e) for e in j.objects(read_json(f"{self.folder}/definition.json"), "entries"))
        return MappingProxyType({d.key: d for d in entries})

    @property
    def dialects(self) -> tuple[Dialect, ...]:
        """Known dialects of this version: the built-in ones, then those added with ``Auditfile.dialects.add``."""
        return self._dialects + custom.dialects._for(self.key)

    @property
    def views(self) -> tuple[ViewDefinition, ...]:
        """
        Views of this audit file type, e.g. the trial balance: the built-in ones, then those added with
        ``Auditfile.views.add``. Shared by all versions of the type.
        """
        return self._views + custom.views._for(self.type)

    @cached_property
    def _dialects(self) -> tuple[Dialect, ...]:
        return tuple(Dialect(d) for d in j.objects(read_json(f"{self.folder}/dialects.json"), "dialects"))

    @cached_property
    def _views(self) -> tuple[ViewDefinition, ...]:
        return ViewDefinition._load(self.type)

    def find_view(self, name: str) -> ViewDefinition | None:
        """Finds a view by name, or returns None."""
        return next((v for v in self.views if v.name == name), None)

    @staticmethod
    def find(key: str) -> AuditfileVersion | None:
        """Finds a version by key, e.g. "xaf/3.2" (case-insensitive). Returns None when unknown."""
        lower = str(key).lower()
        return next((v for v in AuditfileVersion.all() if v.key.lower() == lower), None)

    @staticmethod
    def parse(key: str) -> AuditfileVersion:
        """Returns the version for a key such as "xaf/3.2". :raises ValueError: The key is unknown."""
        if not isinstance(key, str):
            raise TypeError("key must be a str.")

        version = AuditfileVersion.find(key)
        if version is None:
            raise ValueError(f"Unknown audit file version '{key}'.")

        return version

    def open_xsd(self) -> bytes:
        """The bytes of the XSD of this version."""
        return read_bytes(f"{self.folder}/auditfile.xsd")

    def create_schema_set(self) -> SchemaSet:
        """The compiled XSD of this version, ready for validation. Compiled once and shared; it is immutable."""
        return self._schema_set

    @cached_property
    def _schema_set(self) -> SchemaSet:
        from .xsd import SchemaSet

        return SchemaSet.compile(f"{self.folder}/auditfile.xsd", lambda name: read_bytes(name) if exists(name) else None)

    @staticmethod
    def sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def __str__(self) -> str:
        return self.key

    def __repr__(self) -> str:
        return f"AuditfileVersion({self.key!r})"

    # The version constants. A test checks that every registry entry has a constant here and vice versa.
    # Names follow the registry: version with underscores, e.g. V3_2 for 3.2.

    class Financial:
        """Auditfile Financieel (XAF)."""

        V3_2 = _Constant("xaf/3.2")
        V4_0_3 = _Constant("xaf/4.0.3")

    class CashRegister:
        """Auditfile Afrekensystemen (XAA)."""

        V3_2 = _Constant("xaa/3.2")

    class TaxiOnboardComputer:
        """Auditfile Boordcomputer Taxi (XAB)."""

        V1_0 = _Constant("xab/1.0")

    class TripRegistration:
        """Auditfile Ritregistratie (XAR)."""

        V1_2 = _Constant("xar/1.2")
        V2_3_6_1 = _Constant("xar/2.3.6.1")

    class Payroll:
        """Auditfile Salaris (XAS)."""

        V2018_1_0 = _Constant("xas/2018.1.0")
        V2019_1_0 = _Constant("xas/2019.1.0")
        V2020_1_0 = _Constant("xas/2020.1.0")
        V2021_1_1 = _Constant("xas/2021.1.1")
        V2022_1_1 = _Constant("xas/2022.1.1")
        V2023_1_0 = _Constant("xas/2023.1.0")
        V2024_1_0 = _Constant("xas/2024.1.0")
        V2025_1_0 = _Constant("xas/2025.1.0")
        V2026_1_0 = _Constant("xas/2026.1.0")
        V2027_1_0 = _Constant("xas/2027.1.0")


VERSION_GROUPS = (
    AuditfileVersion.Financial,
    AuditfileVersion.CashRegister,
    AuditfileVersion.TaxiOnboardComputer,
    AuditfileVersion.TripRegistration,
    AuditfileVersion.Payroll,
)


class Resolution:
    """The outcome of version resolution."""

    def __init__(self, version: AuditfileVersion, source: VersionSource, file_namespace: str, dialect: Dialect | None) -> None:
        self.version = version
        self.source = source
        self.file_namespace = file_namespace
        self.dialect = dialect

    @property
    def needs_mapping(self) -> bool:
        return self.file_namespace != self.version.namespace

    def use_software(self, software: str, apply_dialects: bool) -> bool:
        """
        Recognizes a dialect by the software description in the header, when none was recognized by namespace.
        Returns True when a dialect was found.
        """
        if self.dialect is not None or not apply_dialects:
            return False

        self.dialect = next((d for d in self.version.dialects if d.software_description == software), None)
        return self.dialect is not None


def resolve_version(root_name: str, file_namespace: str, options: ReadOptions, diagnostics: DiagnosticCollector) -> Resolution | None:
    """
    Determines the version: forced by the caller, then the registry (official namespace, alias or dialect),
    then optionally the resolver. Anything else is fatal.
    """
    resolution = _find(file_namespace, options, diagnostics)
    if resolution is None:
        diagnostics.add("XAF-V001", NO_LOCATION, namespace=file_namespace)
        return None

    version = resolution.version
    if root_name != version.root_element:
        diagnostics.add("XAF-V005", NO_LOCATION, element=root_name, expected=version.root_element)
        return None

    if not version.has_official_xsd:
        diagnostics.add("XAF-V006", NO_LOCATION, version=version.key)

    if resolution.dialect is not None:
        replace = next(
            (c for c in resolution.dialect.corrections if c.action is DialectAction.REPLACE_NAMESPACE and c.from_ == file_namespace), None
        )
        if replace is not None:
            diagnostics.add(replace.code, NO_LOCATION, **{"from": file_namespace, "to": version.namespace})

    return resolution


def _find(file_namespace: str, options: ReadOptions, diagnostics: DiagnosticCollector) -> Resolution | None:
    forced = options.version
    if forced is not None:
        if file_namespace != forced.namespace:
            diagnostics.add("XAF-V003", NO_LOCATION, version=forced.key, namespace=file_namespace)

        return Resolution(forced, VersionSource.FORCED, file_namespace, _find_dialect(forced, file_namespace, options))

    for version in AuditfileVersion.all():
        if version.namespace == file_namespace:
            return Resolution(version, VersionSource.OFFICIAL, file_namespace, None)

    for version in AuditfileVersion.all():
        dialect = _find_dialect(version, file_namespace, options)
        if file_namespace in version.aliases or dialect is not None:
            diagnostics.add("XAF-V002", NO_LOCATION, namespace=file_namespace, version=version.key)
            return Resolution(version, VersionSource.ALIAS, file_namespace, dialect)

    if options.use_resolver:
        derived = _derive(file_namespace)
        if derived is not None:
            diagnostics.add("XAF-V004", NO_LOCATION, version=derived.key, namespace=file_namespace)
            return Resolution(derived, VersionSource.RESOLVED, file_namespace, None)

    return None


def _find_dialect(version: AuditfileVersion, file_namespace: str, options: ReadOptions) -> Dialect | None:
    if not options.apply_dialects:
        return None

    return next((d for d in version.dialects if file_namespace in d.namespaces), None)


def _digits(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)


def _derive(file_namespace: str) -> AuditfileVersion | None:
    """
    Derives type and version from the namespace segments, e.g. ".../XAF/3.2/" or ".../xaf/3_2".
    Returns a version only when exactly one matches.
    """
    segments = [s for s in re.split(r"[/:#]", file_namespace.lower()) if s]
    candidates = [
        v
        for v in AuditfileVersion.all()
        if v.type in segments
        and any(_digits(s) and _digits(s) == _digits(v.version) and re.fullmatch(r"[0-9._]+", s) for s in segments)
    ]
    return candidates[0] if len(candidates) == 1 else None
