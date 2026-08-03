"""String normalisation for internal deduplication and output selection.

The canonical key is built *on top of* the official evaluator's own
``normalize_string``.  That is deliberate: the evaluator collapses predictions
sharing a normalised form, and its bipartite matcher lets one gold entity absorb
only one prediction.  Two surface forms of the same entity therefore cost
precision, so our internal key must be at least as aggressive as the scorer's.

The extra folding steps here (leading articles, parenthetical qualifiers) are
*key-only*.  The emitted string is always one of the model's original surface
forms, never a rewritten one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from cover_kbc.evaluation.official import official_normalize_string

#: Articles stripped from the *key* so "The Hague" and "Hague" collapse.
LEADING_ARTICLES = ("the", "a", "an", "la", "le", "el", "los", "las", "der", "die", "das")

_PARENTHETICAL = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
_LIST_MARKER = re.compile(r"^\s*(?:[-*•–—]+|\d{1,3}[.)])\s*")
_TRAILING_PUNCT = re.compile(r"[\s.,;:!?]+$")

#: Markup characters stripped from both ends of a fragment.  Parentheses are
#: excluded: they carry meaning inside names and are handled by the key policy.
_EDGE_JUNK = " \t{}[]<>|*#`~^_/\\"

#: Outputs that mean "no object", not "an entity literally called None".
ABSTAIN_TOKENS = frozenset(
    {
        "",
        "none",
        "no",
        "n a",
        "na",
        "nil",
        "null",
        "nothing",
        "unknown",
        "not known",
        "not applicable",
        "no answer",
        "no objects",
        "no object",
        "empty",
        "not available",
        "no data",
        "cannot determine",
        # normalize_string *drops* apostrophes, so "I don't know" folds to this.
        "i dont know",
        "i do not know",
        "unclear",
        "uncertain",
        "no such",
        "does not apply",
        "not listed",
        "none of the above",
    }
)

#: Phrases that mark a refusal or a non-answer rather than a candidate list.
#: Matched against the normalised text, so apostrophes are already gone.
REFUSAL_MARKERS = (
    "i cannot",
    "i cant",
    "i am unable",
    "im unable",
    "i am sorry",
    "im sorry",
    "as an ai",
    "i do not have",
    "i dont have",
    "i am not able",
    "im not able",
    "there is no information",
    "no information is available",
)


@dataclass(frozen=True)
class NormalizationPolicy:
    """Per-relation control over how the internal dedup key is built."""

    strip_leading_article: bool = True
    strip_parentheticals: bool = True
    max_words: int = 0  # 0 = unlimited; used to reject sentence-like outputs

    def key(self, value: str) -> str:
        return canonical_key(
            value,
            strip_leading_article=self.strip_leading_article,
            strip_parentheticals=self.strip_parentheticals,
        )


DEFAULT_POLICY = NormalizationPolicy()


def clean_surface(value: str) -> str:
    """Tidy a raw model fragment into a candidate surface form.

    Removes list markers and trailing punctuation but preserves capitalisation,
    diacritics and internal wording, because this is what gets submitted.
    """
    if not isinstance(value, str):
        return ""
    text = value.replace(" ", " ").strip()
    text = _LIST_MARKER.sub("", text)
    text = _TRAILING_PUNCT.sub("", text)
    # Strip markup debris from both ends: braces, brackets, fence remnants.
    text = text.strip(_EDGE_JUNK)
    # Strip symmetric wrapping quotes only.
    for opening, closing in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")):
        if len(text) >= 2 and text.startswith(opening) and text.endswith(closing):
            text = text[1:-1].strip()
    return " ".join(text.split())


def canonical_key(
    value: str,
    *,
    strip_leading_article: bool = True,
    strip_parentheticals: bool = True,
) -> str:
    """Internal deduplication key for an entity surface form."""
    text = clean_surface(value)
    if strip_parentheticals:
        stripped = _PARENTHETICAL.sub(" ", text).strip()
        # Only apply if something meaningful survives, e.g. keep "(1987)" alone.
        if stripped:
            text = stripped
    key = official_normalize_string(text)
    if strip_leading_article:
        parts = key.split()
        if len(parts) > 1 and parts[0] in LEADING_ARTICLES:
            key = " ".join(parts[1:])
    return key


def is_abstain(value: str) -> bool:
    """True when a fragment expresses "no object" rather than naming one."""
    if not isinstance(value, str):
        return True
    key = official_normalize_string(value)
    if key in ABSTAIN_TOKENS:
        return True
    # Catch "none." / "none found" style phrasings without matching real names.
    return key.split(" ", 1)[0] in {"none", "nil", "null"} and len(key) <= 24


def is_refusal(value: str) -> bool:
    """True when the output is a refusal or an "I have no information" reply.

    Checked on the whole generation, before splitting: a refusal sentence
    chopped on commas otherwise yields fragments short enough to pass for names.
    """
    if not isinstance(value, str):
        return False
    key = official_normalize_string(value)
    return any(marker in key for marker in REFUSAL_MARKERS)


def alphanumeric_ratio(value: str) -> float:
    """Share of non-space characters that are letters or digits.

    Used to reject punctuation debris such as code fences or stray braces.
    """
    dense = [c for c in value if not c.isspace()]
    if not dense:
        return 0.0
    return sum(1 for c in dense if c.isalnum()) / len(dense)


def preferred_surface_form(surfaces: Sequence[str]) -> str:
    """Pick the single surface form to emit for one semantic candidate.

    Deterministic by construction: most frequently generated form first, then
    shortest, then lexicographic.  Frequency is a mild signal that the form is
    the model's habitual name for the entity.
    """
    cleaned = [s for s in (clean_surface(s) for s in surfaces) if s]
    if not cleaned:
        return ""
    counts = Counter(cleaned)
    return min(cleaned, key=lambda s: (-counts[s], len(s), s))


def collapse_exact_duplicates(
    values: Iterable[str], policy: NormalizationPolicy = DEFAULT_POLICY
) -> list[str]:
    """Collapse values sharing a canonical key, keeping the preferred form.

    Output order follows first appearance, so the result is stable.
    """
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for value in values:
        surface = clean_surface(value)
        if not surface or is_abstain(surface):
            continue
        key = policy.key(surface)
        if not key:
            continue
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(surface)
    return [preferred_surface_form(grouped[key]) for key in order]
