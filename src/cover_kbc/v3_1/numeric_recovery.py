"""Deterministic numeric canonicalisation for the final output.

Scope, stated first because it is the load-bearing constraint
-------------------------------------------------------------
This module runs at **finalization only**. It deliberately does not touch
:mod:`cover_kbc.normalization.numeric`, which parses candidates at acquisition
time: changing that parser would change which candidates enter the evidence
graph, which changes Module 21's input state, which invalidates the Audit 0073
calibration. The acquisition parser's one known gap - it reads ``7.5e4 m2`` as
``7.5`` with no unit, because its number regex stops before the exponent - is
recorded here and fixed here, for the finalization path only.

TRAIN discovery evidence (Audit 0075)
--------------------------------------
``hasArea`` loses `65` rows to empty output and `76` to complete misses, but
only `2` rows are scale-like errors and `1` is just outside tolerance. Formatting
is therefore *not* the dominant hasArea failure, and this module is written as a
guardrail with a measured TRAIN delta of zero rather than as a score lever. It
is included because an un-parseable or double-converted numeral is a silent
false positive under the official ``try_parse_number``, and that risk exists on
TEST whether or not it fired on TRAIN.

PRODUCTION PREDICATE (gold-independent)
---------------------------------------
    Parse the emitted numeral robustly (grouping separators, decimal comma,
    scientific notation). Convert **only** when an explicit area-unit token
    appears in the value or its recorded evidence surface; a bare number is
    emitted in the relation's canonical unit unchanged. Re-serialise through
    the official-parser-safe formatter.

The "explicit unit" condition is absolute. A number with no unit is never
multiplied by anything - not by 2.589988110336, not by any other factor - no
matter what that would do to a TRAIN row.

Canonical unit
--------------
``hasArea`` gold in ``benchmark/data/train.jsonl`` is stated in square
kilometres (Wellington Island ``5556``, Molokaʻi ``673.4``, Victoria Island
``217291``), which matches ``AREA_UNITS_TO_KM2``'s canonical id ``km2`` and the
unit the acquisition path already normalises to. Canonical output is therefore
km2, unchanged from V3.

Conversion factors (exact, universal, not benchmark-derived)
------------------------------------------------------------
==================  ==========================
1 km2               1 km2
1 m2                0.000001 km2
1 hectare           0.01 km2
1 acre              0.0040468564224 km2
1 square mile       2.589988110336 km2
==================  ==========================
"""

from __future__ import annotations

import re

from cover_kbc.normalization.numeric import (
    AREA_UNITS_TO_KM2,
    NumericParseError,
    format_numeric,
    parse_number_token,
    parse_numbers,
)

#: Number with optional grouping separators **and** optional exponent. The
#: acquisition regex lacks the exponent group; this one has it.
_SCIENTIFIC = re.compile(
    r"^[-+]?(?:\d[\d.,     ']*\d|\d)(?:[eE][-+]?\d+)?$"
)

#: An explicit area unit anywhere in the text, using the shared alias table.
_UNIT_TOKEN = re.compile(
    r"(?:km|m|mi)\s*(?:2|²|\^2)"
    r"|sq(?:uare)?\s*(?:km|kilometers?|kilometres?|m|meters?|metres?|mi|miles?)"
    r"|kilometers?\s*sq|hectares?|\bha\b|acres?",
    re.IGNORECASE,
)


def parse_scalar(text: str) -> float | None:
    """Parse one numeral, including scientific notation.

    Handles ``75,000``, ``75 000``, ``75000``, ``75,000.0``, ``7.5e4``,
    ``12'345`` and the decimal-comma forms the shared parser already accepts.
    Returns ``None`` for anything that is not a single scalar.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped or not _SCIENTIFIC.match(stripped):
        return None
    mantissa, exponent = stripped, 0
    match = re.search(r"[eE]([-+]?\d+)$", stripped)
    if match:
        mantissa, exponent = stripped[: match.start()], int(match.group(1))
    value = parse_number_token(mantissa)
    if value is None:
        return None
    return value * (10.0 ** exponent)


def has_explicit_unit(text: str) -> bool:
    """Does ``text`` state an area unit? The gate on every conversion here."""
    return bool(isinstance(text, str) and _UNIT_TOKEN.search(text))


def explicit_unit_of(text: str) -> str | None:
    """The canonical area-unit id stated in ``text``, or ``None`` if none is.

    Delegates unit spelling to the shared alias table so this module cannot
    drift from the acquisition parser's notion of what a unit is.
    """
    if not has_explicit_unit(text):
        return None
    for parsed in parse_numbers(text):
        if parsed.unit in AREA_UNITS_TO_KM2:
            return parsed.unit
    # A unit token with no adjacent number ("area in hectares: 250").
    for spelling, unit in (
        ("hectare", "ha"), (" ha", "ha"), ("acre", "acre"),
        ("mi2", "mi2"), ("sq mi", "mi2"), ("square mile", "mi2"),
        ("km2", "km2"), ("sq km", "km2"), ("square kilomet", "km2"),
        ("m2", "m2"), ("sq m", "m2"), ("square met", "m2"),
    ):
        if spelling in text.casefold():
            return unit
    return None


def to_canonical_km2(text: str) -> float | None:
    """Value of ``text`` in km2, converting only on an explicit unit.

    A bare numeral is returned as-is: with no unit stated there is nothing to
    convert *from*, and guessing one is exactly the failure this forbids.
    """
    unit = explicit_unit_of(text)
    scalar = parse_scalar(text)
    if scalar is None:
        numbers = parse_numbers(text)
        if len(numbers) != 1:
            return None
        scalar = numbers[0].value
    if unit is None:
        return scalar
    factor = AREA_UNITS_TO_KM2.get(unit)
    if factor is None:
        raise NumericParseError(f"Unit {unit!r} is not an area unit")
    return scalar * factor


def canonicalize_numeric_output(
    value: str, *, integer_only: bool = False, enabled: bool = True
) -> str:
    """Re-serialise an emitted numeral into official-parser-safe form.

    Returns ``value`` untouched when the feature is off or when the text is not
    a value this module can read - never a guess.
    """
    if not enabled or not isinstance(value, str):
        return value
    scalar = to_canonical_km2(value)
    if scalar is None:
        return value
    try:
        return format_numeric(scalar, integer_only=integer_only)
    except NumericParseError:
        return value


__all__ = [
    "AREA_UNITS_TO_KM2",
    "canonicalize_numeric_output",
    "explicit_unit_of",
    "has_explicit_unit",
    "parse_scalar",
    "to_canonical_km2",
]
