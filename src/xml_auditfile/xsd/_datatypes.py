"""The XSD built-in simple types this validator supports. A type that is not listed is refused when the schema is compiled."""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from typing import NamedTuple

_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_INTEGER = re.compile(r"[+-]?[0-9]+")
_FLOAT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|[+-]?INF|NaN")
_ZONE = r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))?"
_DATE = re.compile(rf"(-?[0-9]{{4,}})-([0-9]{{2}})-([0-9]{{2}}){_ZONE}")
_DATE_TIME = re.compile(rf"(-?[0-9]{{4,}})-([0-9]{{2}})-([0-9]{{2}})T([0-9]{{2}}):([0-9]{{2}}):([0-9]{{2}})(?:\.[0-9]+)?{_ZONE}")
_TIME = re.compile(rf"([0-9]{{2}}):([0-9]{{2}}):([0-9]{{2}})(?:\.[0-9]+)?{_ZONE}")
_G_YEAR = re.compile(rf"-?[0-9]{{4,}}{_ZONE}")
_G_YEAR_MONTH = re.compile(rf"-?[0-9]{{4,}}-(?:0[1-9]|1[0-2]){_ZONE}")
_NAME_START = (
    "A-Za-z_À-ÖØ-öø-˿Ͱ-ͽͿ-῿‌-‍⁰-↏"
    "Ⰰ-⿯、-퟿豈-﷏ﷰ-�\U00010000-\U000effff"
)
_NAME_CHAR = _NAME_START + r"\-.0-9·̀-ͯ‿-⁀"
_NC_NAME = re.compile(f"[{_NAME_START}][{_NAME_CHAR}]*")
_NAME = re.compile(f"[{_NAME_START}:][{_NAME_CHAR}:]*")
_NMTOKEN = re.compile(f"[{_NAME_CHAR}:]+")
_LANGUAGE = re.compile(r"[a-zA-Z]{1,8}(?:-[a-zA-Z0-9]{1,8})*")
_BOOLEAN = re.compile(r"true|false|1|0")


class Builtin(NamedTuple):
    white_space: str
    numeric: bool
    parse: Callable[[str], object]
    id: bool = False


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28

    return 30 if month in (4, 6, 9, 11) else 31


def _valid_date(year: str, month: str, day: str) -> bool:
    m, d = int(month), int(day)
    return 1 <= m <= 12 and 1 <= d <= _days_in_month(int(year), m)


def _valid_time(hour: str, minute: str, second: str, text: str) -> bool:
    h, m, s = int(hour), int(minute), int(second)
    return (h < 24 and m < 60 and s < 60) or (h == 24 and m == 0 and s == 0 and re.search(r"\.[0-9]*[1-9]", text) is None)


def _string(pattern: re.Pattern[str] | None) -> Callable[[str], object]:
    return lambda value: value if pattern is None or pattern.fullmatch(value) else None


def _decimal(value: str) -> object:
    return Decimal(value) if _DECIMAL.fullmatch(value) else None


def _integer(minimum: int | None, maximum: int | None) -> Callable[[str], object]:
    def parse(value: str) -> object:
        if _INTEGER.fullmatch(value) is None:
            return None

        number = int(value)
        if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
            return None

        return Decimal(number)

    return parse


def _range(bits: int, signed: bool) -> Callable[[str], object]:
    return _integer(-(2 ** (bits - 1)), 2 ** (bits - 1) - 1) if signed else _integer(0, 2**bits - 1)


def _date(value: str) -> object:
    m = _DATE.fullmatch(value)
    return value if m is not None and _valid_date(m[1], m[2], m[3]) else None


def _date_time(value: str) -> object:
    m = _DATE_TIME.fullmatch(value)
    return value if m is not None and _valid_date(m[1], m[2], m[3]) and _valid_time(m[4], m[5], m[6], value) else None


def _time(value: str) -> object:
    m = _TIME.fullmatch(value)
    return value if m is not None and _valid_time(m[1], m[2], m[3], value) else None


BUILTINS: dict[str, Builtin] = {
    "anySimpleType": Builtin("preserve", False, _string(None)),
    "string": Builtin("preserve", False, _string(None)),
    "normalizedString": Builtin("replace", False, _string(None)),
    "token": Builtin("collapse", False, _string(None)),
    "language": Builtin("collapse", False, _string(_LANGUAGE)),
    "Name": Builtin("collapse", False, _string(_NAME)),
    "NCName": Builtin("collapse", False, _string(_NC_NAME)),
    "ID": Builtin("collapse", False, _string(_NC_NAME), id=True),
    "IDREF": Builtin("collapse", False, _string(_NC_NAME)),
    "NMTOKEN": Builtin("collapse", False, _string(_NMTOKEN)),
    "anyURI": Builtin("collapse", False, _string(None)),
    "boolean": Builtin("collapse", False, _string(_BOOLEAN)),
    "float": Builtin("collapse", False, _string(_FLOAT)),
    "double": Builtin("collapse", False, _string(_FLOAT)),
    "decimal": Builtin("collapse", True, _decimal),
    "integer": Builtin("collapse", True, _integer(None, None)),
    "nonNegativeInteger": Builtin("collapse", True, _integer(0, None)),
    "positiveInteger": Builtin("collapse", True, _integer(1, None)),
    "nonPositiveInteger": Builtin("collapse", True, _integer(None, 0)),
    "negativeInteger": Builtin("collapse", True, _integer(None, -1)),
    "long": Builtin("collapse", True, _range(64, True)),
    "int": Builtin("collapse", True, _range(32, True)),
    "short": Builtin("collapse", True, _range(16, True)),
    "byte": Builtin("collapse", True, _range(8, True)),
    "unsignedLong": Builtin("collapse", True, _range(64, False)),
    "unsignedInt": Builtin("collapse", True, _range(32, False)),
    "unsignedShort": Builtin("collapse", True, _range(16, False)),
    "unsignedByte": Builtin("collapse", True, _range(8, False)),
    "date": Builtin("collapse", False, _date),
    "dateTime": Builtin("collapse", False, _date_time),
    "time": Builtin("collapse", False, _time),
    "gYear": Builtin("collapse", False, _string(_G_YEAR)),
    "gYearMonth": Builtin("collapse", False, _string(_G_YEAR_MONTH)),
}


def normalize_white_space(value: str, mode: str) -> str:
    """Applies a whiteSpace facet value."""
    if mode == "preserve":
        return value

    replaced = value.replace("\t", " ").replace("\n", " ").replace("\r", " ")
    return replaced if mode == "replace" else re.sub(" {2,}", " ", replaced).strip(" ")


def digits_of(text: str) -> tuple[int, int]:
    """Total and fraction digits of a decimal in XSD notation, as the XSD facets count them; exact for any length."""
    whole, _, fraction = text.lstrip("+-").partition(".")
    fraction = fraction.rstrip("0")
    coefficient = (whole + fraction).lstrip("0")
    return max(len(coefficient), len(fraction), 1), len(fraction)
