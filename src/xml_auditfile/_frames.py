"""pandas and polars DataFrames from tables and views. Both are optional:
``pip install xml-auditfile[pandas]`` or ``pip install xml-auditfile[polars]``."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from .definitions import ColumnType

if TYPE_CHECKING:
    import pandas
    import polars

Decimals = Literal["decimal", "float"]
"""How decimal columns end up in a DataFrame: exact decimals or float64."""

Columns = Sequence[tuple[str, "ColumnType | None"]]
"""(name, type) per column; a type of None is inferred from the values."""


def _module(name: str, method: str) -> Any:
    try:
        return __import__(name)
    except ImportError as error:
        raise ImportError(f"{method} needs {name}: pip install xml-auditfile[{name}]") from error


def _infer(values: Sequence[object]) -> ColumnType | None:
    """The column type of view values, from the first value that is not None; None when every value is None."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return ColumnType.BOOLEAN
        if isinstance(value, Decimal):
            return ColumnType.DECIMAL
        if isinstance(value, int):
            return ColumnType.INTEGER
        if isinstance(value, dt.datetime):
            return ColumnType.DATETIME
        if isinstance(value, dt.date):
            return ColumnType.DATE
        if isinstance(value, dt.time):
            return ColumnType.TIME
        return ColumnType.STRING

    return None


def _cells(columns: Columns, rows: Iterable[Sequence[object]], decimals: Decimals) -> list[tuple[str, ColumnType | None, list[object]]]:
    """(name, type, values) per column, with the types of None inferred."""
    if decimals not in ("decimal", "float"):
        raise ValueError(f"decimals must be 'decimal' or 'float', not {decimals!r}.")

    cells: list[list[object]] = [[] for _ in columns]
    for row in rows:
        for i, value in enumerate(row):
            cells[i].append(value)

    return [(name, type_ if type_ is not None else _infer(values), values) for (name, type_), values in zip(columns, cells)]


def _is_aware(values: list[object]) -> bool:
    # One time zone per column: with any aware value, everything is converted to UTC (naive values count as UTC).
    return any(isinstance(v, dt.datetime) and v.tzinfo is not None for v in values)


def _pandas_series(pd: Any, name: str, values: list[object], type_: ColumnType | None, decimals: Decimals) -> pandas.Series:
    if type_ is ColumnType.STRING:
        return pd.Series(values, name=name, dtype="string")

    if type_ is ColumnType.INTEGER:
        return pd.Series(values, name=name, dtype="Int64")

    if type_ is ColumnType.BOOLEAN:
        return pd.Series(values, name=name, dtype="boolean")

    if type_ is ColumnType.DECIMAL:
        if decimals == "float":
            return pd.Series([None if v is None else float(v) for v in values], name=name, dtype="float64")  # type: ignore[arg-type]
        return pd.Series(values, name=name, dtype="object")

    if type_ is ColumnType.DATE:
        return pd.Series(pd.to_datetime(values), name=name)

    if type_ is ColumnType.DATETIME:
        return pd.Series(pd.to_datetime(values, utc=_is_aware(values)), name=name)

    return pd.Series(values, name=name, dtype="object")


def pandas_frame(columns: Columns, rows: Iterable[Sequence[object]], decimals: Decimals, index: tuple[str, list[int]] | None = None) -> pandas.DataFrame:
    """A pandas DataFrame; decimals stay Decimal objects (object dtype) unless decimals is "float"."""
    cells = _cells(columns, rows, decimals)
    pd = _module("pandas", "to_pandas")
    result = pd.DataFrame({name: _pandas_series(pd, name, values, type_, decimals) for name, type_, values in cells},
                          columns=[name for name, _ in columns])
    if index is not None:
        result.index = pd.Index(index[1], name=index[0], dtype="int64")

    return result


def _scale(values: list[object]) -> int:
    """The largest number of decimals in the column, so the Decimal dtype holds every value exactly."""
    scales = [-v.as_tuple().exponent for v in values if isinstance(v, Decimal) and isinstance(v.as_tuple().exponent, int)]
    return max([0, *scales])


def _polars_series(pl: Any, name: str, values: list[object], type_: ColumnType | None, decimals: Decimals) -> polars.Series:
    if type_ is ColumnType.STRING:
        return pl.Series(name, values, dtype=pl.String)

    if type_ is ColumnType.INTEGER:
        return pl.Series(name, values, dtype=pl.Int64)

    if type_ is ColumnType.BOOLEAN:
        return pl.Series(name, values, dtype=pl.Boolean)

    if type_ is ColumnType.DECIMAL:
        if decimals == "float":
            return pl.Series(name, [None if v is None else float(v) for v in values], dtype=pl.Float64)  # type: ignore[arg-type]
        return pl.Series(name, values, dtype=pl.Decimal(38, _scale(values)))

    if type_ is ColumnType.DATE:
        return pl.Series(name, values, dtype=pl.Date)

    if type_ is ColumnType.DATETIME:
        if not _is_aware(values):
            return pl.Series(name, values, dtype=pl.Datetime("us"))
        utc = [v.astimezone(dt.timezone.utc).replace(tzinfo=None) if isinstance(v, dt.datetime) and v.tzinfo else v for v in values]
        return pl.Series(name, utc, dtype=pl.Datetime("us")).dt.replace_time_zone("UTC")

    if type_ is ColumnType.TIME:
        return pl.Series(name, values, dtype=pl.Time)

    return pl.Series(name, values, dtype=pl.Null)


def polars_frame(columns: Columns, rows: Iterable[Sequence[object]], decimals: Decimals) -> polars.DataFrame:
    """A polars DataFrame; decimals get an exact Decimal dtype unless decimals is "float"."""
    cells = _cells(columns, rows, decimals)
    pl = _module("polars", "to_polars")
    return pl.DataFrame([_polars_series(pl, name, values, type_, decimals) for name, type_, values in cells])
