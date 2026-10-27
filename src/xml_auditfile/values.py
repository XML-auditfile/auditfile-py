"""Converts XML text to typed values, always in XSD notation and independent of locale."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from ._decimal import format_decimal, parse_decimal
from .definitions import ColumnType

_INTEGER = re.compile(r"[+-]?[0-9]+")
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_TIME_ZONE = re.compile(r"|Z|[+-][0-9]{2}:[0-9]{2}")
# Only the XSD notation: a date-time like 03/01/2017 10:00:00 or with a space instead of T is refused, never guessed.
_DATE_TIME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,7}))?(Z|[+-][0-9]{2}:[0-9]{2})?"
)
# hh:mm:ss with an optional fraction (dropped) and time zone (ignored), as for a date.
_TIME = re.compile(r"([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?")

# The range of .NET long, so every implementation accepts the same integers.
_MIN_INTEGER = -(2**63)
_MAX_INTEGER = 2**63 - 1

_MISSING = object()


def try_convert(raw: str, type_: ColumnType) -> tuple[bool, object]:
    """Converts text to a value of the type. Empty text becomes None. Returns (False, None) when the text is not valid."""
    if type_ is ColumnType.STRING:
        return True, raw

    text = raw.strip()
    if not text:
        return True, None

    value = _convert(text, type_)
    return (False, None) if value is _MISSING else (True, value)


def _convert(text: str, type_: ColumnType) -> object:
    if type_ is ColumnType.DECIMAL:
        parsed = parse_decimal(text)
        return _MISSING if parsed is None else parsed

    if type_ is ColumnType.INTEGER:
        if _INTEGER.fullmatch(text) is None or len(text) > 21:
            return _MISSING

        number = int(text)
        return number if _MIN_INTEGER <= number <= _MAX_INTEGER else _MISSING

    if type_ is ColumnType.DATE:
        # The date as written; an optional time zone (Z, +hh:mm, -hh:mm) is ignored, never converted.
        match = _DATE.fullmatch(text[:10])
        if match is None or _TIME_ZONE.fullmatch(text[10:]) is None:
            return _MISSING

        try:
            return dt.date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return _MISSING

    if type_ is ColumnType.DATETIME:
        match = _DATE_TIME.fullmatch(text)
        if match is None:
            return _MISSING

        zone = match[8]
        tzinfo = None
        if zone == "Z":
            tzinfo = dt.timezone.utc
        elif zone is not None:
            minutes = int(zone[1:3]) * 60 + int(zone[4:6])
            if minutes >= 24 * 60:
                return _MISSING
            tzinfo = dt.timezone(dt.timedelta(minutes=-minutes if zone[0] == "-" else minutes))

        fraction = (match[7] or "").ljust(6, "0")[:6]
        try:
            return dt.datetime(
                int(match[1]), int(match[2]), int(match[3]), int(match[4]), int(match[5]), int(match[6]), int(fraction), tzinfo
            )
        except ValueError:
            return _MISSING

    if type_ is ColumnType.TIME:
        match = _TIME.fullmatch(text)
        if match is None:
            return _MISSING

        try:
            return dt.time(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return _MISSING

    if type_ is ColumnType.BOOLEAN:
        if text in ("true", "1"):
            return True

        return False if text in ("false", "0") else _MISSING

    return text


def to_text(value: object) -> str:
    """Text form of a value for keys and messages: dates as yyyy-mm-dd, decimals as written, never scientific."""
    if value is None:
        return ""

    if isinstance(value, Decimal):
        return format_decimal(value)

    if isinstance(value, dt.datetime):
        return value.isoformat()

    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()

    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)
