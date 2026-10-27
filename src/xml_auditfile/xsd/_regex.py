"""Translates an XSD pattern facet to a Python regular expression.

XSD patterns are always anchored and have no ^/$ anchors; unsupported constructs are refused, never approximated.
"""

from __future__ import annotations

import re

_SYNTAX = set("\\|.-^?*+{}()[]$")


def _unsupported(pattern: str, construct: str) -> ValueError:
    return ValueError(f"XSD pattern '{pattern}' uses {construct}, which this validator does not support.")


def translate_pattern(pattern: str) -> re.Pattern[str]:
    """:raises ValueError: The pattern uses a construct this validator does not support."""
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 1
            n = pattern[i] if i < len(pattern) else ""
            if n == "d":
                # Python's \d on str patterns is any Unicode decimal digit (Nd), as in XSD.
                out.append("\\d")
            elif n == "D":
                out.append("\\D")
            elif n == "s":
                out.append(" \\t\\n\\r" if in_class else "[ \\t\\n\\r]")
            elif n == "S":
                if in_class:
                    raise _unsupported(pattern, "\\S inside a character class")
                out.append("[^ \\t\\n\\r]")
            elif n in ("n", "r", "t"):
                out.append("\\" + n)
            elif n in _SYNTAX:
                out.append("\\" + n)
            else:
                raise _unsupported(pattern, "\\" + n)

            i += 1
            continue

        if in_class:
            if c == "[":
                raise _unsupported(pattern, "character class subtraction")

            if c == "]":
                in_class = False

            out.append(c)
        elif c == "[":
            in_class = True
            out.append(c)
            if pattern[i + 1 : i + 2] == "^":
                out.append("^")
                i += 1
        elif c in ("^", "$"):
            out.append("\\" + c)
        elif c == ".":
            out.append("[^\\n\\r]")
        elif c == "(" and pattern[i + 1 : i + 2] == "?":
            raise _unsupported(pattern, "(?")
        else:
            out.append(c)

        i += 1

    if in_class:
        raise _unsupported(pattern, "an unclosed character class")

    try:
        return re.compile("(?:" + "".join(out) + ")")
    except re.error as error:
        raise ValueError(f"XSD pattern '{pattern}' is not valid: {error}") from None
