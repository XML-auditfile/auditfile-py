"""Compiles the XSD subset used by auditfile-spec.

Global and local elements, sequences, attributes, simple types restricted by facets, and key/keyref/unique.
Anything else is refused with a clear error, so a custom XSD is never validated half.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from xml.parsers import expat

from ._datatypes import BUILTINS, Builtin
from ._regex import translate_pattern

XS = "http://www.w3.org/2001/XMLSchema"
_XML = "http://www.w3.org/XML/1998/namespace"


def qualified(ns: str, local: str) -> str:
    return "{" + ns + "}" + local


class XsdError(ValueError):
    """An XSD that cannot be compiled."""


class _Node:
    __slots__ = ("ns", "local", "attributes", "bindings", "children", "line")

    def __init__(self, ns: str, local: str, attributes: dict[str, str], bindings: dict[str, str], line: int) -> None:
        self.ns = ns
        self.local = local
        self.attributes = attributes
        self.bindings = bindings
        self.children: list[_Node] = []
        self.line = line


def _split(name: str) -> tuple[str, str]:
    ns, _, local = name.rpartition(" ")
    return ns, local


def _parse_tree(data: bytes, name: str) -> _Node | None:
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.ordered_attributes = True
    stack: list[_Node] = []
    pending: dict[str, str] = {}
    result: list[_Node] = []

    def refuse(*_: object) -> None:
        raise XsdError(f"{name}: a DTD is not allowed.")

    def namespace(prefix: str | None, uri: str | None) -> None:
        pending[prefix or ""] = uri or ""

    def start(tag: str, attributes: list[str]) -> None:
        parent = stack[-1] if stack else None
        bindings = dict(parent.bindings) if parent is not None else {"xml": _XML}
        bindings.update(pending)
        pending.clear()
        ns, local = _split(tag)
        values = {}
        for i in range(0, len(attributes), 2):
            attribute_ns, attribute_local = _split(attributes[i])
            if attribute_ns == "":
                values[attribute_local] = attributes[i + 1]

        node = _Node(ns, local, values, bindings, parser.CurrentLineNumber)
        if parent is None:
            result.append(node)
        else:
            parent.children.append(node)

        stack.append(node)

    def end(_: str) -> None:
        stack.pop()

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.StartNamespaceDeclHandler = namespace
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        raise XsdError(f"{name}: {error}") from None

    return result[0] if result else None


class _Document:
    def __init__(self, name: str, root: _Node | None) -> None:
        if root is None or root.ns != XS or root.local != "schema":
            raise XsdError(f"{name} is not an XSD schema.")

        self.name = name
        self.root = root
        self.target_namespace = root.attributes.get("targetNamespace", "")
        self.element_qualified = root.attributes.get("elementFormDefault") == "qualified"
        self.attribute_qualified = root.attributes.get("attributeFormDefault") == "qualified"


def _fail(document: _Document, node: _Node, message: str) -> XsdError:
    return XsdError(f"{document.name} (line {node.line}): {message}")


def _children(document: _Document, node: _Node) -> list[_Node]:
    result = []
    for child in node.children:
        if child.ns != XS:
            raise _fail(document, child, f"unexpected element {{{child.ns}}}{child.local}.")

        if child.local != "annotation":
            result.append(child)

    return result


def _allow(document: _Document, node: _Node, names: tuple[str, ...]) -> None:
    for name in node.attributes:
        if name not in names:
            raise _fail(document, node, f"attribute '{name}' on xs:{node.local} is not supported by this validator.")


def _resolve_qname(document: _Document, node: _Node, value: str) -> tuple[str, str]:
    prefix, _, local = value.rpartition(":")
    ns = node.bindings.get(prefix)
    if ns is None:
        if prefix == "":
            return "", local

        raise _fail(document, node, f"prefix '{prefix}' is not declared.")

    return ns, local


def _occurs(document: _Document, node: _Node, name: str, fallback: float) -> float:
    value = node.attributes.get(name)
    if value is None:
        return fallback

    if value == "unbounded" and name == "maxOccurs":
        return math.inf

    if re.fullmatch(r"[0-9]+", value) is None:
        raise _fail(document, node, f'{name}="{value}" is not valid.')

    return int(value)


def _resolve_location(base: str, location: str) -> str:
    """Resolves a schemaLocation relative to the including document; only relative names inside the definitions."""
    if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", location) or location.startswith("/") or "\\" in location:
        raise XsdError(f"{base}: schemaLocation '{location}' must be relative; only embedded definitions are allowed.")

    segments = base.split("/")[:-1]
    for segment in location.split("/"):
        if segment == "..":
            if not segments:
                raise XsdError(f"{base}: schemaLocation '{location}' points outside the definitions.")

            segments.pop()
        elif segment not in (".", ""):
            segments.append(segment)

    return "/".join(segments)


class SimpleType:
    __slots__ = ("name", "builtin", "base", "white_space", "facets")
    kind = "simple"

    def __init__(self, name: str, builtin: Builtin, base: SimpleType | None, white_space: str, facets: dict[str, Any] | None) -> None:
        self.name = name
        self.builtin = builtin
        self.base = base
        self.white_space = white_space
        self.facets = facets


class ComplexType:
    __slots__ = ("particles", "attributes")
    kind = "complex"

    def __init__(self) -> None:
        self.particles: list[ElementDeclaration] = []
        self.attributes: dict[str, tuple[str, SimpleType, bool]] = {}


class AnyType:
    """Accepts any content; used for elements without a type."""

    kind = "any"


ANY_TYPE = AnyType()


class ElementDeclaration:
    __slots__ = ("ns", "local", "min", "max", "type", "constraints")

    def __init__(self, ns: str, local: str, minimum: float, maximum: float) -> None:
        self.ns = ns
        self.local = local
        self.min = minimum
        self.max = maximum
        self.type: SimpleType | ComplexType | AnyType = ANY_TYPE
        self.constraints: list[Constraint] = []


class Path:
    __slots__ = ("steps", "attribute")

    def __init__(self, steps: list[str | None], attribute: str | None) -> None:
        self.steps = steps
        self.attribute = attribute


class Constraint:
    __slots__ = ("kind", "name", "display_name", "selector", "fields", "refer")

    def __init__(self, kind: str, name: str, display_name: str, selector: list[Path], fields: list[Path], refer: str | None) -> None:
        self.kind = kind
        self.name = name
        self.display_name = display_name
        self.selector = selector
        self.fields = fields
        self.refer = refer


_FACETS = ("length", "minLength", "maxLength", "totalDigits", "fractionDigits")
_BOUNDS = ("minInclusive", "maxInclusive", "minExclusive", "maxExclusive")


class SchemaSet:
    """A compiled XSD: the global element declarations of all its documents."""

    def __init__(self) -> None:
        self.elements: dict[str, ElementDeclaration] = {}
        """Global element declarations by "{ns}local"."""
        self.count = 0
        """Number of schema documents."""
        self._simple_types: dict[str, tuple[_Document, _Node]] = {}
        self._complex_types: dict[str, tuple[_Document, _Node]] = {}
        self._compiled: dict[str, SimpleType | ComplexType | None] = {}

    @staticmethod
    def compile(main_name: str, load: Callable[[str], bytes | None]) -> SchemaSet:
        """Compiles an XSD. Includes and imports are resolved only through `load`, never from disk or network."""
        result = SchemaSet()
        documents: list[_Document] = []
        loaded: set[str] = set()
        queue = [main_name]
        while queue:
            name = queue.pop(0)
            if name in loaded:
                continue

            loaded.add(name)
            data = load(name)
            if data is None:
                raise XsdError(f"Refused to resolve '{name}': only embedded definitions are allowed.")

            document = _Document(name, _parse_tree(data, name))
            documents.append(document)
            for node in _children(document, document.root):
                if node.local in ("import", "include"):
                    location = node.attributes.get("schemaLocation")
                    if location is None:
                        raise _fail(document, node, f"xs:{node.local} without schemaLocation is not supported.")

                    queue.append(_resolve_location(name, location))

        for document in documents:
            for node in _children(document, document.root):
                key = qualified(document.target_namespace, node.attributes.get("name", ""))
                if node.local == "simpleType":
                    result._simple_types[key] = (document, node)
                elif node.local == "complexType":
                    result._complex_types[key] = (document, node)
                elif node.local not in ("element", "import", "include"):
                    raise _fail(document, node, f"xs:{node.local} is not supported by this validator.")

        for document in documents:
            for node in _children(document, document.root):
                if node.local == "element":
                    element = result._element(document, node, True)
                    result.elements[qualified(element.ns, element.local)] = element

        result.count = len(documents)
        return result

    def _element(self, document: _Document, node: _Node, is_global: bool) -> ElementDeclaration:
        _allow(document, node, ("name", "type", "id") if is_global else ("name", "type", "minOccurs", "maxOccurs", "form", "id"))
        local = node.attributes.get("name")
        if local is None:
            raise _fail(document, node, "xs:element without a name (ref) is not supported by this validator.")

        form = node.attributes.get("form")
        qualified_name = is_global or (document.element_qualified if form is None else form == "qualified")
        element = ElementDeclaration(
            document.target_namespace if qualified_name else "",
            local,
            1 if is_global else _occurs(document, node, "minOccurs", 1),
            1 if is_global else _occurs(document, node, "maxOccurs", 1),
        )

        anonymous = None
        for child in _children(document, node):
            if child.local in ("simpleType", "complexType"):
                anonymous = child
            elif child.local in ("key", "keyref", "unique"):
                element.constraints.append(self._constraint(document, child))
            else:
                raise _fail(document, child, f"xs:{child.local} in xs:element is not supported by this validator.")

        type_name = node.attributes.get("type")
        if type_name is not None:
            element.type = self._type_by_name(document, node, type_name)
        elif anonymous is not None:
            element.type = self._simple_type(document, anonymous) if anonymous.local == "simpleType" else self._complex_type(document, anonymous)

        return element

    def _type_by_name(self, document: _Document, node: _Node, value: str) -> SimpleType | ComplexType | AnyType:
        ns, local = _resolve_qname(document, node, value)
        if ns == XS:
            return ANY_TYPE if local == "anyType" else self._builtin(document, node, local)

        key = qualified(ns, local)
        if key in self._compiled:
            compiled = self._compiled[key]
            if compiled is None:
                raise _fail(document, node, f"type '{value}' is defined in terms of itself.")

            return compiled

        simple = self._simple_types.get(key)
        complex_ = self._complex_types.get(key)
        if simple is None and complex_ is None:
            raise _fail(document, node, f"type '{value}' is not defined.")

        self._compiled[key] = None
        compiled_type: SimpleType | ComplexType = (
            self._simple_type(simple[0], simple[1], local) if simple is not None else self._complex_type(complex_[0], complex_[1])  # type: ignore[index]
        )
        self._compiled[key] = compiled_type
        return compiled_type

    def _builtin(self, document: _Document, node: _Node, local: str) -> SimpleType:
        builtin = BUILTINS.get(local)
        if builtin is None:
            raise _fail(document, node, f"built-in type xs:{local} is not supported by this validator.")

        return SimpleType(f"xs:{local}", builtin, None, builtin.white_space, None)

    def _simple_type(self, document: _Document, node: _Node, name: str | None = None) -> SimpleType:
        _allow(document, node, ("name", "id"))
        parts = _children(document, node)
        if len(parts) != 1 or parts[0].local != "restriction":
            raise _fail(document, node, "only xs:simpleType with xs:restriction is supported by this validator.")

        restriction = parts[0]
        _allow(document, restriction, ("base", "id"))
        base: SimpleType | ComplexType | AnyType | None = None
        facet_nodes = []
        for child in _children(document, restriction):
            if child.local == "simpleType":
                base = self._simple_type(document, child)
            else:
                facet_nodes.append(child)

        base_name = restriction.attributes.get("base")
        if base_name is not None:
            base = self._type_by_name(document, restriction, base_name)

        if not isinstance(base, SimpleType):
            raise _fail(document, restriction, "xs:restriction needs a simple base type.")

        facets: dict[str, Any] = {"enumeration": None, "patterns": [], **{f: None for f in _FACETS + _BOUNDS}}
        white_space = base.white_space
        for facet in facet_nodes:
            _allow(document, facet, ("value", "fixed", "id"))
            value = facet.attributes.get("value")
            if value is None:
                raise _fail(document, facet, f"xs:{facet.local} needs a value.")

            if facet.local == "enumeration":
                facets["enumeration"] = (facets["enumeration"] or []) + [value]
            elif facet.local == "pattern":
                try:
                    facets["patterns"].append((value, translate_pattern(value)))
                except ValueError as error:
                    raise _fail(document, facet, str(error)) from None
            elif facet.local in _FACETS:
                if re.fullmatch(r"[0-9]+", value) is None:
                    raise _fail(document, facet, f"xs:{facet.local} value '{value}' is not a non-negative integer.")

                facets[facet.local] = int(value)
            elif facet.local in _BOUNDS:
                bound = base.builtin.parse(value.strip()) if base.builtin.numeric else None
                if not isinstance(bound, Decimal):
                    raise _fail(document, facet, f"xs:{facet.local} is supported on numeric types only.")

                facets[facet.local] = bound
            elif facet.local == "whiteSpace":
                if value not in ("preserve", "replace", "collapse"):
                    raise _fail(document, facet, f"xs:whiteSpace value '{value}' is not valid.")

                white_space = value
            else:
                raise _fail(document, facet, f"facet xs:{facet.local} is not supported by this validator.")

        if facets["enumeration"] is not None and base.builtin.numeric:
            parsed = [(v, base.builtin.parse(v.strip())) for v in facets["enumeration"]]
            facets["enumeration"] = [v if number is None else number for v, number in parsed]

        return SimpleType(name or base.name, base.builtin, base, white_space, facets)

    def _complex_type(self, document: _Document, node: _Node) -> ComplexType:
        _allow(document, node, ("name", "mixed", "id"))
        if node.attributes.get("mixed", "false") != "false":
            raise _fail(document, node, "mixed content is not supported by this validator.")

        result = ComplexType()
        sequences = 0
        for child in _children(document, node):
            if child.local == "sequence":
                sequences += 1
                if sequences > 1:
                    raise _fail(document, child, "more than one xs:sequence.")

                _allow(document, child, ("id",))
                for particle in _children(document, child):
                    if particle.local != "element":
                        raise _fail(document, particle, f"xs:{particle.local} in xs:sequence is not supported by this validator.")

                    result.particles.append(self._element(document, particle, False))
            elif child.local == "attribute":
                _allow(document, child, ("name", "type", "use", "form", "id"))
                local = child.attributes.get("name")
                if local is None:
                    raise _fail(document, child, "xs:attribute without a name (ref) is not supported by this validator.")

                form = child.attributes.get("form")
                ns = document.target_namespace if (document.attribute_qualified if form is None else form == "qualified") else ""
                use = child.attributes.get("use", "optional")
                inner = next((c for c in _children(document, child) if c.local == "simpleType"), None)
                type_name = child.attributes.get("type")
                if type_name is not None:
                    attribute_type = self._type_by_name(document, child, type_name)
                elif inner is not None:
                    attribute_type = self._simple_type(document, inner)
                else:
                    attribute_type = self._builtin(document, child, "anySimpleType")

                if not isinstance(attribute_type, SimpleType):
                    raise _fail(document, child, "an attribute needs a simple type.")

                if use != "prohibited":
                    result.attributes[qualified(ns, local)] = (local, attribute_type, use == "required")
            else:
                raise _fail(document, child, f"xs:{child.local} in xs:complexType is not supported by this validator.")

        return result

    def _constraint(self, document: _Document, node: _Node) -> Constraint:
        _allow(document, node, ("name", "refer", "id"))
        kind = node.local
        name = node.attributes.get("name")
        selector: list[Path] | None = None
        fields: list[Path] = []
        for child in _children(document, node):
            _allow(document, child, ("xpath", "id"))
            xpath = child.attributes.get("xpath")
            if xpath is None or child.local not in ("selector", "field"):
                raise _fail(document, child, f"xs:{child.local} in xs:{kind} is not supported by this validator.")

            if child.local == "selector":
                selector = [self._path(document, child, path, False) for path in xpath.split("|")]
            else:
                fields.append(self._path(document, child, xpath, True))

        if name is None or selector is None or not fields:
            raise _fail(document, node, f"xs:{kind} needs a name, a selector and at least one field.")

        refer = None
        if kind == "keyref":
            value = node.attributes.get("refer")
            if value is None:
                raise _fail(document, node, "xs:keyref needs refer.")

            refer = qualified(*_resolve_qname(document, node, value))

        return Constraint(kind, qualified(document.target_namespace, name), name, selector, fields, refer)

    def _path(self, document: _Document, node: _Node, xpath: str, field: bool) -> Path:
        """Parses a restricted XPath: "./a/b", "a/b", "@x", "a/@x" or ".". Steps match {ns}local or *."""
        steps: list[str | None] = []
        attribute = None
        parts = [p.strip() for p in xpath.strip().split("/")]
        for i, part in enumerate(parts):
            if part == "." and i == 0:
                continue

            if part in ("", ".", "..") or "::" in part or "[" in part:
                raise _fail(document, node, f"XPath '{xpath}' is not supported by this validator.")

            if part.startswith("@"):
                if not field or i != len(parts) - 1:
                    raise _fail(document, node, f"XPath '{xpath}' is not supported by this validator.")

                attribute = self._name_test(document, node, part[1:])
                continue

            steps.append(self._name_test(document, node, part))

        return Path(steps, attribute)

    def _name_test(self, document: _Document, node: _Node, test: str) -> str | None:
        if test == "*":
            return None

        # XPath 1.0: an unprefixed name has no namespace; the default namespace does not apply.
        if ":" not in test:
            return qualified("", test)

        return qualified(*_resolve_qname(document, node, test))
