"""Parse raw model output into relation-typed atomic candidates (spec Module 3).

Parsing is intentionally forgiving about *shape* and strict about *type*.  A
model that ignores the format instruction should still yield candidates, but a
country name must never survive as a candidate for a numeric relation.

Malformed output never raises: it yields no candidates.  A parser exception in
the middle of a 477-query run would be far worse than an empty row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from cover_kbc.contracts.base import RelationContract
from cover_kbc.normalization.numeric import (
    AREA_UNITS_TO_KM2,
    NumericValue,
    parse_numbers,
)
from cover_kbc.normalization.strings import (
    alphanumeric_ratio,
    clean_surface,
    is_abstain,
    is_refusal,
)

#: Label prefixes a model tends to emit before the actual answer.
_LABEL_PREFIX = re.compile(
    r"^\s*(?:answer|answers|result|results|output|response|"
    r"countries|country|cities|city|exchanges|exchange|recipients|recipients list|"
    r"winners|capacity|area|list)\s*[:\-]\s*",
    re.IGNORECASE,
)

#: A gate view's YES/NO/UNKNOWN verdict.
_GATE_YES = re.compile(r"\byes\b", re.IGNORECASE)
_GATE_NO = re.compile(r"\bno\b", re.IGNORECASE)

#: Entity names longer than this are almost certainly prose, not a name.
MAX_ENTITY_WORDS = 12

#: Characters that separate list items.  Comma is handled separately because
#: entity names legitimately contain commas ("Washington, D.C.").
_STRONG_SEPARATORS = re.compile(r"[;|\n]")

#: Markdown code fences, which models wrap structured answers in.
_CODE_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*$|```", re.MULTILINE)

#: A fragment below this share of alphanumeric characters is punctuation debris.
MIN_ALPHANUMERIC_RATIO = 0.5


@dataclass(frozen=True)
class GateVerdict:
    """Result of a YES/NO/UNKNOWN gate view."""

    value: bool | None  # True = YES, False = NO, None = UNKNOWN
    raw: str

    @property
    def is_negative(self) -> bool:
        return self.value is False


def parse_gate(text: str) -> GateVerdict:
    """Read a gate view's verdict; anything ambiguous becomes UNKNOWN."""
    if not isinstance(text, str):
        return GateVerdict(None, "")
    head = text.strip()[:64]
    has_yes, has_no = bool(_GATE_YES.search(head)), bool(_GATE_NO.search(head))
    if has_yes and not has_no:
        return GateVerdict(True, head)
    if has_no and not has_yes:
        return GateVerdict(False, head)
    return GateVerdict(None, head)


def _strip_label(line: str) -> str:
    return _LABEL_PREFIX.sub("", line, count=1)


def _split_items(text: str) -> list[str]:
    """Split raw output into candidate fragments."""
    stripped = _CODE_FENCE.sub("", text).strip()

    # A well-behaved JSON array is the least ambiguous case.
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, list):
                return [str(item) for item in parsed if isinstance(item, (str, int, float))]

    items: list[str] = []
    for line in _STRONG_SEPARATORS.split(stripped):
        fragment = _strip_label(line).strip()
        if not fragment:
            continue
        # Only split on commas when the fragment looks like a list rather than
        # a sentence; a sentence usually has far more words than commas.
        if "," in fragment and len(fragment.split()) <= 4 * (fragment.count(",") + 1):
            items.extend(part for part in fragment.split(",") if part.strip())
        else:
            items.append(fragment)
    return items


def parse_entities(text: str, contract: RelationContract) -> list[str]:
    """Extract entity surface forms from raw output.

    Returns cleaned surface forms in order of appearance, with abstentions and
    sentence-like fragments dropped.  Deduplication happens later, in the
    evidence graph, so provenance for every mention is preserved.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    # Checked before splitting: a refusal chopped on commas yields fragments
    # short enough to pass for entity names.
    if is_abstain(text) or is_refusal(text):
        return []

    limit = contract.normalization.max_words or MAX_ENTITY_WORDS
    out: list[str] = []
    for fragment in _split_items(text):
        surface = clean_surface(fragment)
        if not surface or is_abstain(surface):
            continue
        if len(surface.split()) > limit:
            continue
        if alphanumeric_ratio(surface) < MIN_ALPHANUMERIC_RATIO:
            continue
        if not any(ch.isalpha() for ch in surface):
            continue
        out.append(surface)
    return out


def parse_numeric_values(text: str, contract: RelationContract) -> list[float]:
    """Extract scalars for a numeric relation, converted to the target unit.

    ``hasArea`` converts everything to km2.  ``hasCapacity`` is a person count,
    so any value carrying an area unit is a type error and is dropped.
    """
    if not isinstance(text, str) or not text.strip() or is_abstain(text):
        return []

    target = contract.selection.numeric_target_unit
    values: list[NumericValue] = parse_numbers(text, default_unit=None)

    out: list[float] = []
    for value in values:
        if target == "km2":
            unit = value.unit or "km2"
            factor = AREA_UNITS_TO_KM2.get(unit)
            if factor is None:
                continue
            out.append(value.value * factor)
        else:
            # Person counts: reject anything explicitly measured in area units,
            # and reject negative counts.
            if value.unit in AREA_UNITS_TO_KM2:
                continue
            if value.value < 0:
                continue
            out.append(value.value)
    return out
