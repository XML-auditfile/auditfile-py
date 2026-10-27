"""Exact decimals in XSD notation, within the range of .NET decimal so every implementation accepts the same values."""

from __future__ import annotations

import decimal
import re
from decimal import Decimal

_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_MAX_SCALE = 28
_MAX = Decimal("79228162514264337593543950335")

# Additions never round: an inexact result raises instead of silently losing a cent.
EXACT = decimal.Context(prec=200, traps=[decimal.Inexact, decimal.InvalidOperation, decimal.Overflow])


def parse_decimal(text: str) -> Decimal | None:
    """Parses XSD notation (sign, digits, optional point; no exponent). Returns None when invalid or out of range."""
    if _PATTERN.fullmatch(text) is None:
        return None

    fraction = text.partition(".")[2]
    if len(fraction) > _MAX_SCALE:
        return None

    digits = text.lstrip("+-").replace(".", "").lstrip("0")
    if len(digits) > 29:
        return None

    value = Decimal(text)
    if abs(value) > _MAX:
        return None

    return value.copy_abs() if value.is_zero() else value


def add(a: Decimal, b: Decimal) -> Decimal:
    return EXACT.add(a, b)


def format_decimal(value: Decimal) -> str:
    """Invariant notation with the scale as written, never scientific: 100.00, -0.0000001."""
    text = format(value, "f")
    return text[1:] if text.startswith("-") and value.is_zero() else text
