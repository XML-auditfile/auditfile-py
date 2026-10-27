"""Streaming validator for a compiled SchemaSet."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from .._keys import SEPARATOR as _SEPARATOR
from ._datatypes import digits_of, normalize_white_space
from ._schema_set import ComplexType, Constraint, ElementDeclaration, SchemaSet, SimpleType, qualified

_XSI = "http://www.w3.org/2001/XMLSchema-instance"
_XMLNS = "http://www.w3.org/2000/xmlns/"
_MAX_PENDING_REFERENCES = 100_000
_MAX_SHOWN = 100

Report = Callable[[str, "int | None", "int | None"], None]


def _shown(value: str) -> str:
    """Shortens a value for a message; values come from the file and can be long."""
    return value if len(value) <= _MAX_SHOWN else value[:_MAX_SHOWN] + "…"


def validate_simple(type_: SimpleType, raw: str) -> tuple[str | None, str | None]:
    """Validates a simple value. Returns (normalized value, None) or (None, error message)."""
    text = normalize_white_space(raw, type_.white_space)
    parsed = type_.builtin.parse(text)
    if parsed is None:
        return None, f"'{_shown(text)}' is not a valid value of the atomic type '{type_.name}'."

    steps = []
    step: SimpleType | None = type_
    while step is not None:
        if step.facets is not None:
            steps.append(step.facets)
        step = step.base

    for facets in reversed(steps):
        error = _check_facets(facets, text, parsed)
        if error is not None:
            return None, error

    return text, None


def _check_facets(facets: dict[str, Any], text: str, parsed: object) -> str | None:
    numeric = isinstance(parsed, Decimal)
    enumeration = facets["enumeration"]
    if enumeration is not None and not any((option == parsed) if numeric and isinstance(option, Decimal) else option == text for option in enumeration):
        return f"[facet 'enumeration'] The value '{_shown(text)}' is not an element of the set of allowed values."

    patterns = facets["patterns"]
    if patterns and not any(regex.fullmatch(text) for _, regex in patterns):
        sources = "' | '".join(source for source, _ in patterns)
        return f"[facet 'pattern'] The value '{_shown(text)}' is not accepted by the pattern '{sources}'."

    if not numeric:
        length = len(text)
        if facets["length"] is not None and length != facets["length"]:
            return f"[facet 'length'] The value '{_shown(text)}' has a length of '{length}'; this differs from the allowed length of '{facets['length']}'."

        if facets["minLength"] is not None and length < facets["minLength"]:
            return (
                f"[facet 'minLength'] The value '{_shown(text)}' has a length of '{length}'; "
                f"this underruns the allowed minimum length of '{facets['minLength']}'."
            )

        if facets["maxLength"] is not None and length > facets["maxLength"]:
            return (
                f"[facet 'maxLength'] The value '{_shown(text)}' has a length of '{length}'; "
                f"this exceeds the allowed maximum length of '{facets['maxLength']}'."
            )

        return None

    assert isinstance(parsed, Decimal)
    total, fraction = digits_of(text)
    if facets["totalDigits"] is not None and total > facets["totalDigits"]:
        return f"[facet 'totalDigits'] The value '{_shown(text)}' has more digits than are allowed ('{facets['totalDigits']}')."

    if facets["fractionDigits"] is not None and fraction > facets["fractionDigits"]:
        return f"[facet 'fractionDigits'] The value '{_shown(text)}' has more fractional digits than are allowed ('{facets['fractionDigits']}')."

    if facets["minInclusive"] is not None and parsed < facets["minInclusive"]:
        return f"[facet 'minInclusive'] The value '{_shown(text)}' is less than the minimum value allowed ('{facets['minInclusive']}')."

    if facets["maxInclusive"] is not None and parsed > facets["maxInclusive"]:
        return f"[facet 'maxInclusive'] The value '{_shown(text)}' is greater than the maximum value allowed ('{facets['maxInclusive']}')."

    if facets["minExclusive"] is not None and parsed <= facets["minExclusive"]:
        return f"[facet 'minExclusive'] The value '{_shown(text)}' must be greater than '{facets['minExclusive']}'."

    if facets["maxExclusive"] is not None and parsed >= facets["maxExclusive"]:
        return f"[facet 'maxExclusive'] The value '{_shown(text)}' must be less than '{facets['maxExclusive']}'."

    return None


def _key_value(type_: SimpleType, value: str) -> str:
    """The value used to compare key fields: numbers by value, everything else as normalized text."""
    if type_.builtin.numeric:
        whole, _, fraction = value.lstrip("+").partition(".")
        negative = whole.startswith("-")
        whole = whole.lstrip("-").lstrip("0") or "0"
        fraction = fraction.rstrip("0")
        text = whole + ("." + fraction if fraction else "")
        return "-" + text if negative and text != "0" else text

    return value


class _Frame:
    __slots__ = ("skip", "local", "type", "index", "count", "broken", "text", "length", "text_reported", "scopes", "notified", "active", "depth")

    def __init__(self, skip: bool, local: str, type_: Any, depth: int) -> None:
        self.skip = skip
        self.local = local
        self.type = type_
        self.index = 0
        self.count = 0
        self.broken = False
        self.text: list[str] | None = []
        self.length = 0
        self.text_reported = False
        self.scopes: list[_IdentityScope] = []
        """Identity constraints declared on this element."""
        self.notified: list[_IdentityScope] = []
        """Constraints that saw the start of this element, and so see its end."""
        self.active: list[_IdentityScope] = []
        """Constraints that can still select or capture something below this element."""
        self.depth = depth


class XsdValidator:
    """
    Streaming validator for a compiled SchemaSet. Feed it the element events of a document in order;
    it reports every violation through report(message, line, column) and never stops on its own.
    """

    def __init__(self, schema_set: SchemaSet, report: Report, max_value_length: int | None = None, identity_constraints: bool = True) -> None:
        """
        Longer values than max_value_length are not kept and not checked; the reader reports them (XAF-S008).
        identity_constraints evaluates key, unique and keyref; off when another check does that work.
        """
        self._max_value_length = max_value_length
        self._identity_constraints = identity_constraints
        self._set = schema_set
        self._report = report
        self._stack: list[_Frame] = []
        self._ids: set[str] = set()

    def start(self, event: Any) -> None:
        parent = self._stack[-1] if self._stack else None
        declaration: ElementDeclaration | None = None
        if parent is None:
            declaration = self._set.elements.get(qualified(event.ns, event.local))
            if declaration is None:
                self._error(event, f"Element '{event.local}': No matching global declaration available for the validation root.")
        elif not parent.skip:
            declaration = self._child(parent, event)

        frame = _Frame(True, event.local, None, event.depth) if declaration is None else self._open(declaration, event)
        if parent is not None and parent.active:
            frame.notified = parent.active
            frame.active = [scope for scope in parent.active if scope.start(event)]

        if declaration is not None and declaration.constraints and self._identity_constraints:
            frame.scopes = [_IdentityScope(constraint, event.depth) for constraint in declaration.constraints]
            for scope in frame.scopes:
                if scope.constraint.kind == "keyref":
                    scope.lookup = (lambda s=scope, f=frame: self._find_table(s.constraint.refer, f))

            frame.active = frame.active + frame.scopes

        self._stack.append(frame)

    def text(self, event: Any) -> None:
        if not self._stack:
            return

        frame = self._stack[-1]
        if frame.skip:
            return

        if isinstance(frame.type, SimpleType):
            if frame.text is not None:
                frame.length += len(event.text)
                if self._max_value_length is not None and frame.length > self._max_value_length:
                    frame.text = None
                else:
                    frame.text.append(event.text)
        elif isinstance(frame.type, ComplexType) and not frame.text_reported and event.text.strip(" \t\r\n"):
            frame.text_reported = True
            self._error(event, f"Element '{frame.local}': Character content other than whitespace is not allowed because the content type is 'element-only'.")

    def end(self, event: Any) -> None:
        if not self._stack:
            return

        frame = self._stack.pop()
        value = None
        if not frame.skip:
            if isinstance(frame.type, SimpleType) and frame.text is not None:
                normalized, error = validate_simple(frame.type, "".join(frame.text))
                if error is not None:
                    self._error(event, f"Element '{frame.local}': {error}")
                elif normalized is not None:
                    value = _key_value(frame.type, normalized)
                    if frame.type.builtin.id:
                        self._id(event, normalized)
            elif isinstance(frame.type, ComplexType) and not frame.broken:
                remaining = frame.type.particles[frame.index :]
                for i, particle in enumerate(remaining):
                    if (frame.count if i == 0 else 0) < particle.min:
                        self._error(event, f"Element '{frame.local}': Missing child element(s). Expected is ( {particle.local} ).")
                        break

        for scope in frame.notified:
            scope.end(event, value, self)

        if frame.scopes:
            for scope in frame.scopes:
                if scope.constraint.kind == "keyref":
                    scope.check_references(self._find_table(scope.constraint.refer, frame), self)

    def error(self, line: int | None, column: int | None, message: str) -> None:
        """Reports an identity-constraint violation."""
        self._report(message, line, column)

    def _error(self, event: Any, message: str) -> None:
        self._report(message, event.line, event.column)

    def _open(self, declaration: ElementDeclaration, event: Any) -> _Frame:
        type_ = declaration.type
        frame = _Frame(type_.kind == "any", event.local, type_, event.depth)
        if type_.kind != "any":
            self._attributes(type_, event)

        return frame

    def _child(self, parent: _Frame, event: Any) -> ElementDeclaration | None:
        if isinstance(parent.type, SimpleType):
            if not parent.broken:
                parent.broken = True
                self._error(event, f"Element '{parent.local}': Element content is not allowed, because the type definition is simple.")

            return None

        if parent.broken:
            return None

        particles = parent.type.particles
        while parent.index < len(particles):
            particle = particles[parent.index]
            if particle.ns == event.ns and particle.local == event.local:
                if parent.count < particle.max:
                    parent.count += 1
                    return particle
            elif parent.count < particle.min:
                break

            parent.index += 1
            parent.count = 0

        parent.broken = True
        if parent.index < len(particles):
            self._error(event, f"Element '{event.local}': This element is not expected. Expected is ( {particles[parent.index].local} ).")
        else:
            self._error(event, f"Element '{event.local}': This element is not expected.")

        return None

    def _attributes(self, type_: SimpleType | ComplexType, event: Any) -> None:
        seen = set()
        for ns, local, value in event.attributes:
            if ns in (_XMLNS, _XSI) or (ns == "" and local == "xmlns"):
                continue

            key = qualified(ns, local)
            declared = type_.attributes.get(key) if isinstance(type_, ComplexType) else None
            if declared is None:
                self._error(event, f"Element '{event.local}', attribute '{local}': The attribute '{local}' is not allowed.")
                continue

            seen.add(key)
            normalized, error = validate_simple(declared[1], value)
            if error is not None:
                self._error(event, f"Element '{event.local}', attribute '{local}': {error}")
            elif declared[1].builtin.id and normalized is not None:
                self._id(event, normalized)

        if isinstance(type_, ComplexType):
            for key, (local, _, required) in type_.attributes.items():
                if required and key not in seen:
                    self._error(event, f"Element '{event.local}': The attribute '{local}' is required but missing.")

    def _id(self, event: Any, value: str) -> None:
        if value in self._ids:
            self._error(event, f"ID '{_shown(value)}' is already defined.")
        else:
            self._ids.add(value)

    def _find_table(self, refer: str | None, frame: _Frame) -> set[str] | None:
        """The key table a keyref refers to: declared on the same element or on an ancestor."""
        for candidate in [frame, *reversed(self._stack)]:
            for scope in candidate.scopes:
                if scope.constraint.name == refer and scope.constraint.kind != "keyref":
                    return scope.table

        return None


class _Selected:
    __slots__ = ("depth", "values", "field_matches", "line", "column")

    def __init__(self, depth: int, fields: int, line: int, column: int) -> None:
        self.depth = depth
        self.values: list[str | None] = [None] * fields
        self.field_matches: dict[int, list[bool]] = {}
        self.line = line
        self.column = column


class _IdentityScope:
    """Tracks one key, unique or keyref below the element that declares it."""

    def __init__(self, constraint: Constraint, depth: int) -> None:
        self.constraint = constraint
        self.depth = depth
        self.table: set[str] = set()
        self.pending: list[tuple[str, int | None, int | None]] = []
        self.lookup: Callable[[], set[str] | None] | None = None
        self._matches: dict[int, list[bool]] = {}
        self._selected: list[_Selected] = []
        self._max_steps = max(len(path.steps) for path in constraint.selector)

    def start(self, event: Any) -> bool:
        """Sees the start of an element below the declaring element. Returns whether its descendants matter."""
        relative = event.depth - self.depth
        if relative > self._max_steps and not self._selected:
            return False

        name = qualified(event.ns, event.local)
        matches = []
        for i, path in enumerate(self.constraint.selector):
            previous = self._matches.get(relative - 1)
            step = path.steps[relative - 1] if 0 < relative <= len(path.steps) else None
            matches.append(
                0 < relative <= len(path.steps)
                and (relative == 1 or (previous is not None and previous[i]))
                and (step is None or step == name)
            )

        self._matches[relative] = matches
        if any(match and relative == len(self.constraint.selector[i].steps) for i, match in enumerate(matches)):
            self._selected.append(_Selected(event.depth, len(self.constraint.fields), event.line, event.column))

        for node in self._selected:
            below = event.depth - node.depth
            field_matches = []
            for i, field in enumerate(self.constraint.fields):
                if below == 0:
                    field_matches.append(True)
                    continue

                previous = node.field_matches.get(below - 1)
                step = field.steps[below - 1] if below <= len(field.steps) else None
                field_matches.append(below <= len(field.steps) and previous is not None and previous[i] and (step is None or step == name))

            node.field_matches[below] = field_matches
            for i, field in enumerate(self.constraint.fields):
                if field.attribute is not None and below == len(field.steps) and field_matches[i]:
                    for ns, local, value in event.attributes:
                        if qualified(ns, local) == field.attribute:
                            node.values[i] = value

        return bool(self._selected) or any(
            match and relative < len(self.constraint.selector[i].steps) for i, match in enumerate(matches)
        )

    def end(self, event: Any, value: str | None, validator: XsdValidator) -> None:
        for node in self._selected:
            below = event.depth - node.depth
            matches = node.field_matches.get(below)
            for i, field in enumerate(self.constraint.fields):
                if field.attribute is None and below == len(field.steps) and matches is not None and matches[i] and value is not None:
                    node.values[i] = value

        if self._selected and self._selected[-1].depth == event.depth:
            self._complete(self._selected.pop(), validator)

    def _complete(self, node: _Selected, validator: XsdValidator) -> None:
        kind = self.constraint.kind
        name = self.constraint.display_name
        if any(v is None for v in node.values):
            if kind == "key":
                validator.error(node.line, node.column, f"Element: Not all fields of key identity-constraint '{name}' evaluate to a node.")

            return

        value = _SEPARATOR.join(node.values)  # type: ignore[arg-type]
        if kind == "keyref":
            # Key tables only grow, so a value found now is found at the end too; only the rest waits.
            table = self.lookup() if self.lookup is not None else None
            if table is not None and value in table:
                return

            if len(self.pending) < _MAX_PENDING_REFERENCES:
                self.pending.append((value, node.line, node.column))

            return

        if value in self.table:
            shown = _shown("', '".join(node.values))  # type: ignore[arg-type]
            validator.error(node.line, node.column, f"Duplicate key-sequence ['{shown}'] in {kind} identity-constraint '{name}'.")
        else:
            self.table.add(value)

    def check_references(self, table: set[str] | None, validator: XsdValidator) -> None:
        if table is None:
            validator.error(None, None, f"The keyref '{self.constraint.display_name}' refers to a key that is not in scope.")
            return

        for value, line, column in self.pending:
            if value not in table:
                shown = _shown("', '".join(value.split(_SEPARATOR)))
                validator.error(line, column, f"No match found for key-sequence ['{shown}'] of keyref '{self.constraint.display_name}'.")
