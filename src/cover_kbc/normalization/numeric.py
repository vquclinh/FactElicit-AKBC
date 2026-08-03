"""Numeric parsing, unit conversion, clustering and output formatting.

Two hard requirements drive this module:

1. Anything we emit for a numeric relation must survive the evaluator's
   ``try_parse_number`` (``float(value.replace(",", "").strip())``).  A string
   like ``"5556 km2"`` parses to ``None``, so it can never be a true positive
   while still counting against precision.  Formatted output is therefore a
   bare numeral.
2. The 5% relative tolerance means that clustering, not token likelihood,
   decides the answer (spec section 14.2).
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

#: Multiplicative factors to convert a unit into square kilometres.
AREA_UNITS_TO_KM2: dict[str, float] = {
    "km2": 1.0,
    "m2": 1e-6,
    "ha": 0.01,
    "mi2": 2.589988110336,
    "acre": 0.0040468564224,
}

#: Surface spellings mapped onto the canonical unit ids above.  Keys are
#: compared after lowercasing and stripping every non-alphanumeric character.
_UNIT_ALIASES: dict[str, str] = {
    "km2": "km2",
    "sqkm": "km2",
    "sqkms": "km2",
    "kmsq": "km2",
    "squarekilometer": "km2",
    "squarekilometers": "km2",
    "squarekilometre": "km2",
    "squarekilometres": "km2",
    "m2": "m2",
    "sqm": "m2",
    "msq": "m2",
    "squaremeter": "m2",
    "squaremeters": "m2",
    "squaremetre": "m2",
    "squaremetres": "m2",
    "ha": "ha",
    "hectare": "ha",
    "hectares": "ha",
    "mi2": "mi2",
    "sqmi": "mi2",
    "sqmiles": "mi2",
    "misq": "mi2",
    "squaremile": "mi2",
    "squaremiles": "mi2",
    "acre": "acre",
    "acres": "acre",
}

#: Magnitude words that scale a bare number.
MAGNITUDES: dict[str, float] = {
    "hundred": 1e2,
    "thousand": 1e3,
    "k": 1e3,
    "lakh": 1e5,
    "million": 1e6,
    "mn": 1e6,
    "crore": 1e7,
    "billion": 1e9,
    "bn": 1e9,
    "trillion": 1e12,
}

#: Unicode spaces used as digit-group separators, plus the Swiss apostrophe.
_GROUPING_WHITESPACE = re.compile("[     ]")

#: A number possibly containing grouping separators.
_NUMBER_RE = re.compile(
    "[-+]?\\d[\\d.,     ']*\\d|[-+]?\\d"
)

#: Characters after a number scanned for a magnitude word and/or unit.
_UNIT_LOOKAHEAD = 28

#: Superscript two, written explicitly so the source stays ASCII-legible.
_SUPERSCRIPT_TWO = "²"


class NumericParseError(ValueError):
    """Raised when a string that must be numeric cannot be parsed."""


@dataclass(frozen=True)
class NumericValue:
    """One parsed scalar with its source text and (optional) unit."""

    value: float
    unit: str | None
    raw: str

    def to_km2(self) -> float:
        """Convert to km2, assuming km2 when no unit was stated."""
        factor = AREA_UNITS_TO_KM2.get(self.unit or "km2")
        if factor is None:
            raise NumericParseError(f"Unit {self.unit!r} is not an area unit")
        return self.value * factor


def _looks_like_grouped(parts: Sequence[str]) -> bool:
    """Heuristic for ``1.234.567`` meaning 1234567 rather than a decimal.

    Grouping is assumed only when there is more than one separator and every
    group after the first is exactly three digits.  A single ``1.234`` stays a
    decimal, which is far more common in model output.
    """
    return len(parts) > 2 and all(len(p) == 3 and p.isdigit() for p in parts[1:])


def parse_number_token(token: str) -> float | None:
    """Parse one numeric token, handling thousands separators and decimals.

    Recognises ``1,234.5`` (en), ``1.234,5`` (de/es), ``1 234,5`` (fr) and
    ``12'345`` (ch).  Returns ``None`` if the token is not a number.
    """
    text = _GROUPING_WHITESPACE.sub("", token.strip()).replace("'", "")
    if not text:
        return None

    sign = ""
    if text[0] in "+-":
        sign, text = text[0], text[1:]
    if not text or not text[0].isdigit():
        return None

    has_comma, has_dot = "," in text, "." in text
    if has_comma and has_dot:
        # The rightmost separator is the decimal mark.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        parts = text.split(",")
        # "1,234" / "1,234,567" are grouped thousands; "1,5" is a decimal comma.
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3):
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    elif has_dot and _looks_like_grouped(text.split(".")):
        text = text.replace(".", "")

    try:
        return float(sign + text)
    except ValueError:
        return None


def _canonical_unit(text: str) -> str | None:
    """Map a unit spelling onto a canonical unit id, or ``None``."""
    key = text.lower().replace(_SUPERSCRIPT_TWO, "2")
    key = re.sub(r"[^a-z0-9]", "", key)
    if not key:
        return None
    key = key.replace("square", "sq")
    return _UNIT_ALIASES.get(key) or _UNIT_ALIASES.get(key.replace("sq", "square"))


def _read_suffix(tail: str) -> tuple[str | None, float]:
    """Read an optional magnitude word and unit immediately after a number."""
    multiplier = 1.0
    rest = tail.lstrip()

    word = re.match(r"[A-Za-z]+", rest)
    if word and word.group().lower() in MAGNITUDES:
        multiplier = MAGNITUDES[word.group().lower()]
        rest = rest[word.end() :].lstrip()

    # A unit is at most two whitespace-separated tokens, e.g. "square miles".
    unit_match = re.match(
        r"[A-Za-z" + _SUPERSCRIPT_TWO + r"]+(?:\s*\.?\s*[A-Za-z0-9" + _SUPERSCRIPT_TWO + r"]+)?",
        rest,
    )
    if not unit_match:
        return None, multiplier

    unit = _canonical_unit(unit_match.group())
    if unit is None:
        first = re.match(r"[A-Za-z" + _SUPERSCRIPT_TWO + r"]+", rest)
        if first:
            unit = _canonical_unit(first.group())
    return unit, multiplier


def parse_numbers(text: str, *, default_unit: str | None = None) -> list[NumericValue]:
    """Extract every scalar in ``text``, with unit and magnitude applied.

    Never raises on malformed input - an unparseable string simply yields no
    values.
    """
    if not isinstance(text, str) or not text.strip():
        return []

    out: list[NumericValue] = []
    for match in _NUMBER_RE.finditer(text):
        # Skip digits glued to the end of a word: the "2" of "km2"/"mi2" is
        # part of the unit, not a second scalar.
        if match.start() > 0 and text[match.start() - 1].isalpha():
            continue
        value = parse_number_token(match.group())
        if value is None:
            continue
        unit, multiplier = _read_suffix(text[match.end() : match.end() + _UNIT_LOOKAHEAD])
        out.append(
            NumericValue(
                value=value * multiplier,
                unit=unit or default_unit,
                raw=match.group().strip(),
            )
        )
    return out


def to_km2(values: Iterable[NumericValue]) -> list[float]:
    """Convert parsed values to km2, dropping any with a non-area unit."""
    out = []
    for value in values:
        try:
            out.append(value.to_km2())
        except NumericParseError:
            continue
    return out


def relative_distance(a: float, b: float) -> float:
    """``d(x_i, x_j)`` from spec section 14.2."""
    denominator = max(abs(a), abs(b))
    if denominator == 0:
        return 0.0 if a == b else math.inf
    return abs(a - b) / denominator


@dataclass(frozen=True)
class NumericCluster:
    """A group of mutually close values plus its robust representative."""

    values: tuple[float, ...]
    representative: float
    relative_mad: float

    @property
    def size(self) -> int:
        return len(self.values)


def _relative_mad(values: Sequence[float], epsilon: float = 1e-9) -> float:
    """Median absolute deviation relative to the median (spec section 14.2)."""
    if len(values) < 2:
        return 0.0
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    return mad / (abs(median) + epsilon)


def cluster_values(values: Sequence[float], threshold: float = 0.05) -> list[NumericCluster]:
    """Group values by relative distance and summarise each group.

    Uses single-linkage over the sorted sequence, which is deterministic and
    adequate for the handful of scalars one query produces.  Clusters are
    returned largest-first, ties broken by tighter dispersion then lower
    representative, so selection never depends on input order.
    """
    finite = sorted(v for v in values if math.isfinite(v))
    if not finite:
        return []

    groups: list[list[float]] = [[finite[0]]]
    for value in finite[1:]:
        if relative_distance(groups[-1][-1], value) <= threshold:
            groups[-1].append(value)
        else:
            groups.append([value])

    clusters = [
        NumericCluster(
            values=tuple(group),
            representative=statistics.median(group),
            relative_mad=_relative_mad(group),
        )
        for group in groups
    ]
    return sorted(clusters, key=lambda c: (-c.size, c.relative_mad, c.representative))


def dominant_cluster(values: Sequence[float], threshold: float = 0.05) -> NumericCluster | None:
    """The cluster whose median is the numeric answer, or ``None`` if empty."""
    clusters = cluster_values(values, threshold=threshold)
    return clusters[0] if clusters else None


def format_numeric(value: float, *, integer_only: bool = False) -> str:
    """Format a scalar as a bare numeral the official parser accepts.

    Precision beyond four significant digits is pointless under a 5% tolerance,
    so large values are rounded to integers for readability.
    """
    if not math.isfinite(value):
        raise NumericParseError(f"Cannot format non-finite value {value!r}")
    if integer_only or abs(value) >= 1000:
        return str(int(round(value)))
    if value == round(value):
        return str(int(round(value)))
    return f"{value:.6g}"
