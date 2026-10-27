"""Checks that see every row as it is emitted: keys and references, and the XAF business rules."""

from __future__ import annotations

from decimal import Decimal

from ._decimal import add, format_decimal
from ._keys import SEPARATOR as _SEPARATOR
from .definitions import ColumnType
from .diagnostics import DiagnosticCollector, Location
from .rows import AuditfileRow
from .schema import AuditfileSchema, SchemaColumn, SchemaTable
from .values import to_text

_MAX_PENDING_PER_REFERENCE = 100_000


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
    __slots__ = ("table", "column_names", "columns", "key", "pending", "overflowed")

    def __init__(self, table: SchemaTable, column_names: str, columns: tuple[int, ...], key: _KeyState) -> None:
        self.table = table
        self.column_names = column_names
        self.columns = columns
        self.key = key
        self.pending: set[str] = set()
        self.overflowed = False
        """True once the pending limit was reached and XAF-R017 was reported."""


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
            if value is None or value in reference.key.values or value in reference.pending:
                continue

            if len(reference.pending) < _MAX_PENDING_PER_REFERENCE:
                reference.pending.add(value)
            elif not reference.overflowed:
                # From here on new values are not checked; say so once, so a clean result is not mistaken for a complete one.
                reference.overflowed = True
                self._diagnostics.add(
                    "XAF-R017", Location(row.part, None, None, row.table.path),
                    table=row.table.name, column=reference.column_names, target=reference.key.table.name, max=_MAX_PENDING_PER_REFERENCE,
                )

    def on_end(self) -> None:
        for references in self._references_by_table.values():
            for reference in references:
                for value in sorted(v for v in reference.pending if v not in reference.key.values):
                    self._diagnostics.add(
                        "XAF-R003", Location(None, None, None, reference.table.path),
                        table=reference.table.name, column=reference.column_names, value=_display(value), target=reference.key.table.name,
                    )


XAF = "xaf"
"""Audit file type the XAF rules apply to."""

_AMOUNT = "amnt"
_AMOUNT_TYPE = "amnt_tp"
_DEBIT = "D"
_CREDIT = "C"


class _Lines:
    """A table of lines with an amount and a debit/credit indicator, with its columns looked up once."""

    __slots__ = ("table", "amount", "amount_type")

    def __init__(self, table: SchemaTable, amount: SchemaColumn, amount_type: SchemaColumn) -> None:
        self.table = table
        self.amount = amount
        self.amount_type = amount_type

    @staticmethod
    def find(table: SchemaTable | None) -> _Lines | None:
        """None when the table or a column does not exist, or has another type, in this version."""
        amount = table.find_column(_AMOUNT) if table is not None else None
        amount_type = table.find_column(_AMOUNT_TYPE) if table is not None else None
        if table is None or amount is None or amount_type is None:
            return None

        if amount.type not in (ColumnType.DECIMAL, ColumnType.INTEGER) or amount_type.type is not ColumnType.STRING:
            return None

        return _Lines(table, amount, amount_type)


class _Totals:
    __slots__ = ("count", "debit", "credit")

    def __init__(self) -> None:
        self.count = 0
        self.debit = Decimal(0)
        self.credit = Decimal(0)

    def add(self, row: AuditfileRow, lines: _Lines) -> None:
        self.count += 1
        amount = row.get_decimal(lines.amount)
        if amount is None:
            return

        type_ = row.get_string(lines.amount_type)
        if type_ == _DEBIT:
            self.debit = add(self.debit, amount)
        elif type_ == _CREDIT:
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
    def __init__(self, lines: _Lines, owner: SchemaTable, per_parent: bool, count: SchemaColumn, debit: SchemaColumn, credit: SchemaColumn) -> None:
        self._lines = lines
        self._owner = owner
        self._per_parent = per_parent
        self._count = count
        self._debit = debit
        self._credit = credit
        self._by_parent: dict[int, _Totals] = {}
        self._all = _Totals()

    def on_row(self, row: AuditfileRow, diagnostics: DiagnosticCollector) -> None:
        if row.table is self._lines.table:
            totals = self._by_parent.setdefault(row.parent_id, _Totals()) if self._per_parent and row.parent_id is not None else self._all
            totals.add(row, self._lines)
            return

        if row.table is not self._owner:
            return

        actual = self._by_parent.pop(row.id, _Totals()) if self._per_parent else self._all
        _compare(row, self._count.name, row.get_integer(self._count), actual.count, diagnostics)
        _compare(row, self._debit.name, row.get_decimal(self._debit), actual.debit, diagnostics)
        _compare(row, self._credit.name, row.get_decimal(self._credit), actual.credit, diagnostics)


class XafRules:
    """
    Business rules for Auditfile Financieel (XAF):
    every transaction balances, and the counts and totals in the headers match the lines.
    Rules whose tables or columns do not exist in a version are skipped. Columns are looked up once, not per row.
    """

    def __init__(self, schema: AuditfileSchema, diagnostics: DiagnosticCollector) -> None:
        self._diagnostics = diagnostics
        self._transactions: dict[int, _Totals] = {}
        self._rules: list[_TotalsRule] = []
        self._lines = _Lines.find(schema.find_table("tr_line"))
        self._transaction = schema.find_table("transaction")
        self._transaction_number = self._transaction.find_column("nr") if self._transaction is not None else None

        self._add_rule(schema, "tr_line", "company", False, "transactions_lines_count", "transactions_total_debit", "transactions_total_credit")
        self._add_rule(schema, "ob_line", "company", False, "opening_balance_lines_count", "opening_balance_total_debit", "opening_balance_total_credit")
        self._add_rule(schema, "sb_line", "subledger", True, "lines_count", "total_debit", "total_credit")
        self._add_rule(schema, "ob_sb_line", "ob_subledger", True, "lines_count", "total_debit", "total_credit")

    def on_row(self, row: AuditfileRow) -> None:
        if self._lines is not None and row.table is self._lines.table and row.parent_id is not None:
            self._transactions.setdefault(row.parent_id, _Totals()).add(row, self._lines)
        elif row.table is self._transaction and row.id in self._transactions:
            lines = self._transactions.pop(row.id)
            if lines.debit != lines.credit:
                number = row[self._transaction_number] if self._transaction_number is not None else None
                self._diagnostics.add(
                    "XAF-R001", Location(row.part, None, None, row.table.path), transaction=number, debit=lines.debit, credit=lines.credit
                )

        for rule in self._rules:
            rule.on_row(row, self._diagnostics)

    def on_end(self) -> None:
        pass

    def _add_rule(self, schema: AuditfileSchema, child: str, owner: str, per_parent: bool, count: str, debit: str, credit: str) -> None:
        lines = _Lines.find(schema.find_table(child))
        owner_table = schema.find_table(owner)
        if lines is None or owner_table is None:
            return

        count_column = owner_table.find_column(count)
        debit_column = owner_table.find_column(debit)
        credit_column = owner_table.find_column(credit)
        if (
            count_column is None
            or count_column.type is not ColumnType.INTEGER
            or debit_column is None
            or debit_column.type is not ColumnType.DECIMAL
            or credit_column is None
            or credit_column.type is not ColumnType.DECIMAL
        ):
            return

        self._rules.append(_TotalsRule(lines, owner_table, per_parent, count_column, debit_column, credit_column))
