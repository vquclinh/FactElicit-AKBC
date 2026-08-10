"""Class-B: scientific notation with a unit, at acquisition time.

The gap, as found in audit 0076
--------------------------------
``normalization.numeric._NUMBER_RE`` matches a run of digits and separators and
stops before an exponent. ``parse_numbers("7.5e4 m2")`` therefore reads the
mantissa ``7.5``, then hands ``"e4 m2"`` to the suffix reader, which finds no
unit it recognises at the start of that text. The observation becomes
``7.5`` with **no unit** instead of ``75000 m2`` - three orders of magnitude
wrong, and silently typed as unitless so nothing downstream can notice.

Audit 0076 fixed the *finalization* path only
(:mod:`cover_kbc.v3_1.numeric_recovery`) and deliberately left acquisition
alone: acquisition decides which candidates enter the evidence graph, which
decides Module 21's input state, which is what the Audit 0073 calibration
describes.

Why this is Class B and not a bug fix
--------------------------------------
It is *both*, and the classification follows the consequence rather than the
intent. Correcting the parser changes the numeric value of some candidates,
which changes clustering, which changes acceptance, which changes the
action-effect distribution M20/M21 were calibrated against. Correct or not, the
calibration no longer describes the run: **CALIBRATION_REVIEW_REQUIRED**.

It is therefore gated behind ``scientific_notation_acquisition`` and cannot be
reached from either SAFE config - a test asserts that ``V31SafeConfig`` has no
such field, so it cannot be enabled by editing a safe block.

Scope
-----
The rewrite is confined to *scientific-notation* numerals. A numeral without an
exponent takes the identical path it takes today, so ordinary parsing is
unchanged by construction rather than by inspection.
"""

from __future__ import annotations

import re

from cover_kbc.normalization.numeric import NumericValue, parse_number_token, parse_numbers

#: A scalar in scientific notation, with its exponent, anywhere in the text.
#: The mantissa alternation mirrors ``_NUMBER_RE`` so the two agree about what
#: a numeral looks like before the exponent begins.
_SCIENTIFIC_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<mantissa>[-+]?\d[\d.,    ']*\d|[-+]?\d)"
    r"[eE](?P<exponent>[-+]?\d+)"
)


def has_scientific_notation(text: str) -> bool:
    """Does ``text`` contain a numeral the shared parser would truncate?"""
    return bool(isinstance(text, str) and _SCIENTIFIC_RE.search(text))


def expand_scientific_notation(text: str) -> str:
    """Rewrite every ``m e n`` numeral into its plain-decimal equivalent.

    The rewrite happens *before* the shared parser runs, so unit reading,
    magnitude words, area conversion and the person-count type rules all stay
    exactly as they are - they simply receive a numeral they can read. Nothing
    in this module knows what a unit is, which is the point: it cannot disagree
    with the shared parser about one.

    A numeral whose mantissa does not parse is left untouched rather than
    guessed at.
    """
    if not has_scientific_notation(text):
        return text

    def _expand(match: re.Match[str]) -> str:
        mantissa = parse_number_token(match.group("mantissa"))
        if mantissa is None:
            return match.group(0)
        value = mantissa * (10.0 ** int(match.group("exponent")))
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return repr(value)

    return _SCIENTIFIC_RE.sub(_expand, text)


def parse_numbers_with_exponents(
    text: str, *, default_unit: str | None = None, enabled: bool = True
) -> list[NumericValue]:
    """``parse_numbers``, with scientific notation read rather than truncated.

    With ``enabled=False`` this is ``parse_numbers`` itself - same call, same
    arguments - so the disabled path cannot drift from production behaviour.
    """
    if not enabled:
        return parse_numbers(text, default_unit=default_unit)
    return parse_numbers(expand_scientific_notation(text), default_unit=default_unit)


__all__ = [
    "expand_scientific_notation",
    "has_scientific_notation",
    "parse_numbers_with_exponents",
]
