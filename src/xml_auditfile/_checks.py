"""Checks that see every row as it is emitted: keys and references, and the XAF business rules."""

from __future__ import annotations

from decimal import Decimal

from ._decimal import add, format_decimal
from .diagnostics import DiagnosticCollector, Location
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaTable
from .values import to_text

_MAX_PENDING_PER_REFERENCE = 100_000
_SEPARATOR = "\u001f"


def _composite(row: AuditfileRow, columns: tuple[int, ...]) -> str | None:
    parts = []
    for column in columns:
        value = row.values[column]
        if value is None:
            return None

        parts.append(to_text(value))

    return _SEPARATOR.join(parts)


def _display(value: str) -> str:
    return value.replace(_SEPARATOR, "/")


def _indexes(table: SchemaTable, columns: tuple[str, ...]) -> tuple[int, ...]:
    result = []
    for name in columns:
        column = table.find_column(name)
        if column is None:
            raise LookupError(f"Key column {name} not in {table.name}.")

        result.append(column.index)

    return tuple(result)


class _KeyState:
    __slots__ = ("table", "columns", "values")

    def __init__(self, table: SchemaTable, columns: tuple[int, ...]) -> None:
        self.table = table
        self.columns = columns
        self.values: set[str] = set()


class _ReferenceState:
    __slots__ = ("table", "column_names", "columns", "key", "pending")

    def __init__(self, table: SchemaTable, column_names: str, columns: tuple[int, ...], key: _KeyState) -> None:
        self.table = table
        self.column_names = column_names
        self.columns = columns
        self.key = key
        self.pending: set[str] = set()


class ReferenceChecker:
    """
    Checks keys (unique) and references (existing) from the schema.
    Only key values and unresolved reference values are kept in memory; references are resolved at the end,
    so a reference to a key that appears later (e.g. in a later part) is still found.
    """

    def __init__(self, schema: AuditfileSchema, diagnostics: DiagnosticCollector) -> None:
        self._diagnostics = diagnostics
        keys_by_name: dict[str, _KeyState] = {}
        self._keys_by_table: dict[int, list[_KeyState]] = {}
        self._references_by_table: dict[int, list[_ReferenceState]] = {}

        for table in schema.tables:
            for key in table.keys:
                state = _KeyState(table, _indexes(table, key.columns))
                keys_by_name[key.name] = state
                self._keys_by_table.setdefault(id(table), []).append(state)

        for table in schema.tables:
            for reference in table.references:
                target = keys_by_name.get(reference.key)
                if target is not None:
                    self._references_by_table.setdefault(id(table), []).append(
                        _ReferenceState(table, ", ".join(reference.columns), _indexes(table, reference.columns), target)
                    )

    def on_row(self, row: AuditfileRow) -> None:
        for key in self._keys_by_table.get(id(row.table), ()):
            value = _composite(row, key.columns)
            if value is not None:
                if value in key.values:
                    self._diagnostics.add("XAF-R004", Location(row.part, None, None, row.table.path), value=_display(value), table=row.table.name)
                else:
                    key.values.add(value)

        for reference in self._references_by_table.get(id(row.table), ()):
            value = _composite(row, reference.columns)
            if value is not None and value not in reference.key.values and len(reference.pending) < _MAX_PENDING_PER_REFERENCE:
                reference.pending.add(value)

    def on_end(self) -> None:
        for references in self._references_by_table.values():
            for reference in references:
                for value in sorted(v for v in reference.pending if v not in reference.key.values):
                    self._diagnostics.add(
                        "XAF-R003", Location(None, None, None, reference.table.path),
                        table=reference.table.name, column=reference.column_names, value=_display(value), target=reference.key.table.name,
                    )


class _Totals:
    __slots__ = ("count", "debit", "credit")

    def __init__(self) -> None:
        self.count = 0
        self.debit = Decimal(0)
        self.credit = Decimal(0)

    def add(self, amount: object, type_: object) -> None:
        self.count += 1
        if not isinstance(amount, Decimal):
            return

        if type_ == "D":
            self.debit = add(self.debit, amount)
        elif type_ == "C":
            self.credit = add(self.credit, amount)


def _text(value: object) -> str:
    return format_decimal(value) if isinstance(value, Decimal) else str(value)


def _compare(row: AuditfileRow, field: str, expected: object, actual: object, diagnostics: DiagnosticCollector) -> None:
    if expected is None or expected == actual:
        return

    diagnostics.add(
        "XAF-R002", Location(row.part, None, None, row.table.path),
        field=field, table=row.table.name, expected=_text(expected), actual=_text(actual),
    )


class _TotalsRule:
    def __init__(self, child: SchemaTable, owner: SchemaTable, per_parent: bool, count: str, debit: str, credit: str) -> None:
        self._child = child
        self._owner = owner
        self._per_parent = per_parent
        self._count = count
        self._debit = debit
        self._credit = credit
        self._by_parent: dict[int, _Totals] = {}
        self._all = _Totals()

    def on_row(self, row: AuditfileRow, diagnostics: DiagnosticCollector) -> None:
        if row.table is self._child:
            totals = self._by_parent.setdefault(row.parent_id, _Totals()) if self._per_parent and row.parent_id is not None else self._all
            totals.add(row["amnt"], row["amnt_tp"])
            return

        if row.table is not self._owner:
            return

        actual = self._by_parent.pop(row.id, _Totals()) if self._per_parent else self._all
        _compare(row, self._count, row[self._count], actual.count, diagnostics)
        _compare(row, self._debit, row[self._debit], actual.debit, diagnostics)
        _compare(row, self._credit, row[self._credit], actual.credit, diagnostics)


class XafRules:
    """
    Business rules for Auditfile Financieel (XAF):
    every transaction balances, and the counts and totals in the headers match the lines.
    Rules whose tables or columns do not exist in a version are skipped.
    """

    def __init__(self, schema: AuditfileSchema, diagnostics: DiagnosticCollector) -> None:
        self._diagnostics = diagnostics
        self._transactions: dict[int, _Totals] = {}
        self._rules: list[_TotalsRule] = []
        self._line = schema.find_table("tr_line")
        self._transaction = schema.find_table("transaction")

        self._add_rule(schema, "tr_line", "company", False, "transactions_lines_count", "transactions_total_debit", "transactions_total_credit")
        self._add_rule(schema, "ob_line", "company", False, "opening_balance_lines_count", "opening_balance_total_debit", "opening_balance_total_credit")
        self._add_rule(schema, "sb_line", "subledger", True, "lines_count", "total_debit", "total_credit")
        self._add_rule(schema, "ob_sb_line", "ob_subledger", True, "lines_count", "total_debit", "total_credit")

    def on_row(self, row: AuditfileRow) -> None:
        if row.table is self._line and row.parent_id is not None:
            self._transactions.setdefault(row.parent_id, _Totals()).add(row["amnt"], row["amnt_tp"])
        elif row.table is self._transaction and row.id in self._transactions:
            lines = self._transactions.pop(row.id)
            if lines.debit != lines.credit:
                self._diagnostics.add(
                    "XAF-R001", Location(row.part, None, None, row.table.path), transaction=row["nr"], debit=lines.debit, credit=lines.credit
                )

        for rule in self._rules:
            rule.on_row(row, self._diagnostics)

    def on_end(self) -> None:
        pass

    def _add_rule(self, schema: AuditfileSchema, child: str, owner: str, per_parent: bool, count: str, debit: str, credit: str) -> None:
        child_table = schema.find_table(child)
        owner_table = schema.find_table(owner)
        if (
            child_table is None
            or child_table.find_column("amnt") is None
            or child_table.find_column("amnt_tp") is None
            or owner_table is None
            or owner_table.find_column(count) is None
        ):
            return

        self._rules.append(_TotalsRule(child_table, owner_table, per_parent, count, debit, credit))
