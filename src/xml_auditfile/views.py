"""Runs a view over rows from any store.

An input row is the source row plus one slot per join; joins are left joins,
so a missing ancestor or lookup leaves its columns None.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from functools import cmp_to_key

from ._decimal import add, parse_decimal
from .definitions import ColumnType, TextLanguage, ViewColumn, ViewDefinition, ViewValue
from .diagnostics import NO_LOCATION, Diagnostic, DiagnosticCollector
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaColumn
from .values import to_text, try_convert

_MAXIMUM_DIAGNOSTICS = 100
_SEPARATOR = "\u001f"

Input = list["AuditfileRow | None"]


class ViewResult:
    """The outcome of running a ViewDefinition."""

    def __init__(self, view: ViewDefinition, is_available: bool, rows: Iterable[Sequence[object]], diagnostics: Iterable[Diagnostic]) -> None:
        self.view = view
        """The view that was run."""
        self.is_available = is_available
        """False when the version lacks a table or column the view needs (XAF-Q001); there are no rows then."""
        self.rows: tuple[tuple[object, ...], ...] = tuple(tuple(row) for row in rows)
        """Result rows, sorted by the view's order_by; one value per column."""
        self.diagnostics: tuple[Diagnostic, ...] = tuple(diagnostics)
        """Findings while running the view."""

    @property
    def columns(self) -> tuple[ViewColumn, ...]:
        """Output columns; each row has one value per column, in this order."""
        return self.view.columns


class _Ref:
    """A resolved "table.column": the input slot and the column."""

    __slots__ = ("slot", "column")

    def __init__(self, slot: int, column: SchemaColumn) -> None:
        self.slot = slot
        self.column = column

    def get(self, row_input: Input) -> object:
        row = row_input[self.slot]
        return None if row is None else row.values[self.column.index]


def _split(reference: str) -> tuple[str, str]:
    table, _, column = reference.partition(".")
    return table, column


def run_view(
    view: ViewDefinition,
    schema: AuditfileSchema,
    rows: Callable[[str], Iterable[AuditfileRow]],
    parameters: Mapping[str, object] | None,
    language: TextLanguage,
) -> ViewResult:
    diagnostics = DiagnosticCollector(language, _MAXIMUM_DIAGNOSTICS)
    slots = [view.source, *(j.table for j in view.joins)]

    missing = _find_missing(view, schema, slots)
    if missing is not None:
        diagnostics.add("XAF-Q001", NO_LOCATION, view=view.name, standard=schema.version_key, missing=missing)
        return ViewResult(view, False, [], diagnostics.items)

    def resolve(reference: str) -> _Ref:
        table, column = _split(reference)
        found = schema.table(table).find_column(column)
        assert found is not None
        return _Ref(slots.index(table), found)

    filters = _build_filters(view, parameters, resolve)
    columns = [(c, None if c.value is None else _Expression(c.value, resolve)) for c in view.columns]
    joins = _build_joins(view, schema, rows, resolve, diagnostics)

    inputs: list[Input] = []
    for source in rows(view.source):
        row_input: Input = [None] * len(slots)
        row_input[0] = source
        for i, join in enumerate(joins):
            row_input[i + 1] = join(row_input)

        if all(f(row_input) for f in filters):
            inputs.append(row_input)

    if view.is_aggregated:
        result = _aggregate(columns, inputs)
    else:
        result = [[expression.evaluate(i) for _, expression in columns] for i in inputs]  # type: ignore[union-attr]

    order = []
    for name in view.order_by:
        index = next((i for i, (c, _) in enumerate(columns) if c.name == name), -1)
        if index >= 0:
            order.append(index)

    if order:

        def compare_rows(a: list[object], b: list[object]) -> int:
            for index in order:
                compared = _compare(a[index], b[index])
                if compared != 0:
                    return compared

            return 0

        # sorted is stable, like the ordering of the other implementations.
        result = sorted(result, key=cmp_to_key(compare_rows))

    return ViewResult(view, True, result, diagnostics.items)


def _find_missing(view: ViewDefinition, schema: AuditfileSchema, slots: list[str]) -> str | None:
    """Returns the first table or "table.column" the view needs but the schema lacks, or None."""
    for table in slots:
        if schema.find_table(table) is None:
            return table

    for join in view.joins:
        if not join.on and _ancestor_path(schema, view.source, join.table) is None:
            return join.table

    references: list[str | None] = [
        *(r for j in view.joins for c in j.on for r in (c.left, c.right)),
        *(p.column for p in view.parameters),
        *(r for c in view.columns if c.value is not None for r in (c.value.column, c.value.by)),
    ]
    for reference in references:
        if reference is None:
            continue

        table, column = _split(reference)
        found = schema.find_table(table)
        if table not in slots or found is None or found.find_column(column) is None:
            return reference

    return None


def _ancestor_path(schema: AuditfileSchema, source: str, ancestor: str) -> list[str] | None:
    """Tables from the parent of `source` up to `ancestor`, or None when it is no ancestor."""
    path: list[str] = []
    table = schema.find_table(source)
    current = table.parent if table is not None else None
    while current is not None and current not in path:
        path.append(current)
        if current == ancestor:
            return path

        parent = schema.find_table(current)
        current = parent.parent if parent is not None else None

    return None


_TESTS: dict[str, Callable[[int], bool]] = {
    "=": lambda c: c == 0,
    "<": lambda c: c < 0,
    "<=": lambda c: c <= 0,
    ">": lambda c: c > 0,
    ">=": lambda c: c >= 0,
}


def _build_filters(view: ViewDefinition, parameters: Mapping[str, object] | None, resolve: Callable[[str], _Ref]) -> list[Callable[[Input], bool]]:
    filters: list[Callable[[Input], bool]] = []
    for name, raw in (parameters or {}).items():
        parameter = next((p for p in view.parameters if p.name == name), None)
        if parameter is None:
            raise ValueError(f"View '{view.name}' has no parameter '{name}'.")

        if raw is None:
            continue

        reference = resolve(parameter.column)
        value = _convert(raw, reference.column, parameter.name)
        test = _TESTS.get(parameter.operator)
        if test is None:
            raise NotImplementedError(f"View '{view.name}': operator '{parameter.operator}' is not supported.")

        def check(row_input: Input, reference: _Ref = reference, value: object = value, test: Callable[[int], bool] = test) -> bool:
            cell = reference.get(row_input)
            return cell is not None and test(_compare(cell, value))

        filters.append(check)

    return filters


def _build_joins(
    view: ViewDefinition, schema: AuditfileSchema, rows: Callable[[str], Iterable[AuditfileRow]], resolve: Callable[[str], _Ref],
    diagnostics: DiagnosticCollector,
) -> list[Callable[[Input], AuditfileRow | None]]:
    # One index by id over every table between the source and its joined ancestors.
    ancestor_tables: list[str] = []
    for join in view.joins:
        if not join.on:
            for table in _ancestor_path(schema, view.source, join.table) or []:
                if table not in ancestor_tables:
                    ancestor_tables.append(table)

    by_id: dict[int, AuditfileRow] = {}
    for table in ancestor_tables:
        for row in rows(table):
            by_id[row.id] = row

    joins: list[Callable[[Input], AuditfileRow | None]] = []
    for join in view.joins:
        if not join.on:

            def ancestor(row_input: Input, table: str = join.table) -> AuditfileRow | None:
                source = row_input[0]
                row = by_id.get(source.parent_id) if source is not None and source.parent_id is not None else None
                while row is not None:
                    if row.table.name == table:
                        return row

                    row = by_id.get(row.parent_id) if row.parent_id is not None else None

                return None

            joins.append(ancestor)
            continue

        left = [resolve(c.left) for c in join.on]
        right = [resolve(c.right) for c in join.on]
        index: dict[str, AuditfileRow] = {}
        width = max(r.slot for r in right) + 1
        for row in rows(join.table):
            cells: Input = [None] * width
            cells[right[0].slot] = row
            key = _key(right, cells)
            if key is None:
                continue

            if key in index:
                diagnostics.add("XAF-Q002", NO_LOCATION, view=view.name, table=join.table, key=key.replace(_SEPARATOR, "/"))
            else:
                index[key] = row

        def lookup(row_input: Input, left: list[_Ref] = left, index: dict[str, AuditfileRow] = index) -> AuditfileRow | None:
            key = _key(left, row_input)
            return None if key is None else index.get(key)

        joins.append(lookup)

    return joins


def _key(references: list[_Ref], row_input: Input) -> str | None:
    parts = []
    for reference in references:
        value = reference.get(row_input)
        if value is None:
            return None

        parts.append(to_text(value))

    return _SEPARATOR.join(parts)


def _aggregate(columns: list[tuple[ViewColumn, _Expression | None]], inputs: list[Input]) -> list[list[object]]:
    groups: dict[str, tuple[list[object], list[_Aggregator | None]]] = {}
    for row_input in inputs:
        values = [None if expression is None else expression.evaluate(row_input) for _, expression in columns]
        key = _SEPARATOR.join(
            ("=" + to_text(values[i]) if values[i] is not None else "") for i, (c, _) in enumerate(columns) if c.aggregate is None
        )
        group = groups.get(key)
        if group is None:
            group = (values, [None if c.aggregate is None else _Aggregator(c, e) for c, e in columns])
            groups[key] = group

        for i, aggregator in enumerate(group[1]):
            if aggregator is not None:
                aggregator.add(values[i])

    # A dict keeps insertion order: groups appear in the order they were first seen.
    return [[v if aggregators[i] is None else aggregators[i].result for i, v in enumerate(values)] for values, aggregators in groups.values()]  # type: ignore[union-attr]


def _convert(value: object, column: SchemaColumn, parameter: str) -> object:
    error = ValueError(f"Parameter '{parameter}': '{value}' is not a valid {column.type.value}.")
    type_ = column.type
    if type_ is ColumnType.DECIMAL:
        if isinstance(value, Decimal):
            return value

        if isinstance(value, int) and not isinstance(value, bool):
            return Decimal(value)

        if isinstance(value, (str, float)):
            parsed = parse_decimal(str(value).strip()) if isinstance(value, str) else _float_decimal(value)
            if parsed is not None:
                return parsed

        raise error

    if type_ is ColumnType.INTEGER:
        if isinstance(value, int) and not isinstance(value, bool):
            return value

        if isinstance(value, str):
            ok, parsed = try_convert(value, ColumnType.INTEGER)
            if ok and parsed is not None:
                return parsed

        raise error

    if type_ in (ColumnType.DATE, ColumnType.DATETIME):
        if isinstance(value, dt.date):
            return value

        if isinstance(value, str):
            ok, parsed = try_convert(value, ColumnType.DATETIME if len(value.strip()) > 10 else ColumnType.DATE)
            if ok and parsed is not None:
                return parsed

        raise error

    if type_ is ColumnType.TIME:
        if isinstance(value, dt.time):
            return value

        if isinstance(value, str):
            ok, parsed = try_convert(value, ColumnType.TIME)
            if ok and parsed is not None:
                return parsed

        raise error

    if type_ is ColumnType.BOOLEAN:
        if isinstance(value, bool):
            return value

        if value in ("true", "false"):
            return value == "true"

        raise error

    return to_text(value)


def _float_decimal(value: float) -> Decimal | None:
    """A float as its shortest decimal text, e.g. 0.1 as 0.1; exponent notation is refused."""
    return parse_decimal(repr(value))


def _compare(a: object, b: object) -> int:
    """Orders None first, numbers by value, dates by time, strings by code point."""
    if a is None or b is None:
        return 0 if a is b else (-1 if a is None else 1)

    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)

    numeric = (int, Decimal)
    if isinstance(a, numeric) and isinstance(b, numeric) and not isinstance(a, bool) and not isinstance(b, bool):
        return (a > b) - (a < b)

    if type(a) is type(b):
        try:
            return (a > b) - (a < b)  # type: ignore[operator]
        except TypeError:
            pass

    sa, sb = to_text(a), to_text(b)
    return (sa > sb) - (sa < sb)


class _Expression:
    def __init__(self, value: ViewValue, resolve: Callable[[str], _Ref]) -> None:
        if value.operator not in ("column", "only", "signed"):
            raise NotImplementedError(f"View value operator '{value.operator}' is not supported.")

        self._value = value
        self._column = resolve(value.column)
        self._by = None if value.by is None else resolve(value.by)

    @property
    def type(self) -> ColumnType:
        return self._column.column.type

    def evaluate(self, row_input: Input) -> object:
        cell = self._column.get(row_input)
        if self._value.operator == "column" or cell is None:
            return cell

        by = self._by.get(row_input) if self._by is not None else None
        listed = by is not None and to_text(by) in self._value.codes
        if self._value.operator == "only":
            return cell if listed else None

        return _negate(cell) if listed else cell


def _negate(value: object) -> object:
    if isinstance(value, Decimal):
        return -value

    if isinstance(value, int) and not isinstance(value, bool):
        return -value

    return value


class _Aggregator:
    def __init__(self, column: ViewColumn, expression: _Expression | None) -> None:
        self._kind = column.aggregate
        if self._kind not in ("sum", "count", "min", "max"):
            raise NotImplementedError(f"View aggregate '{self._kind}' is not supported.")

        self._count_rows = expression is None
        self._decimal = expression is not None and expression.type is ColumnType.DECIMAL
        self._count = 0
        self._sum = Decimal(0)
        self._extreme: object = None

    @property
    def result(self) -> object:
        if self._kind == "count":
            return self._count

        if self._kind == "sum":
            return self._sum if self._decimal else int(self._sum)

        return self._extreme

    def add(self, value: object) -> None:
        if value is None and not self._count_rows:
            return

        self._count += 1
        if self._kind == "sum":
            self._decimal |= isinstance(value, Decimal)
            self._sum = add(self._sum, value if isinstance(value, Decimal) else Decimal(int(value)))  # type: ignore[arg-type]
        elif self._kind == "min":
            if self._extreme is None or _compare(value, self._extreme) < 0:
                self._extreme = value
        elif self._kind == "max":
            if self._extreme is None or _compare(value, self._extreme) > 0:
                self._extreme = value
