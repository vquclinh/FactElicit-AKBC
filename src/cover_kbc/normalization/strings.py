"""String normalisation, candidate identity, and output-form selection.

Candidate identity is split into two levels, because merging two entities is
not symmetric with failing to merge them:

``strict_key``
    The official evaluator's own ``normalize_string``, unchanged.  Two surface
    forms with the same strict key are *provably* one prediction to the scorer -
    it deduplicates on exactly this - so collapsing them is lossless and safe.

``alias_hint_key``
    A deliberately conservative extra fold, currently leading articles only.
    Used to merge "The Alpha Exchange" with "Alpha Exchange", which the scorer
    would otherwise treat as two predictions competing for one gold entity.

Parenthetical qualifiers are **not** folded. ``X (1998)`` and ``X (2011)``, or
``Springfield (Illinois)`` and ``Springfield (Missouri)``, are routinely
*different* entities; collapsing on the base string would silently destroy one
of them and cap recall. Article folding can at worst pick a different surface
form of the same entity; parenthetical folding can lose an entity outright, so
only the former is applied.

All folding is **key-only**. Emitted strings are always original model surface
forms, never rewritten.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from cover_kbc.evaluation.official import official_normalize_string

#: Articles stripped from the *alias hint* so "The Hague" and "Hague" group.
#:
#: English only, deliberately. Romance and Germanic articles are routinely part
#: of a proper name rather than a detachable article, and stripping them merges
#: genuinely different places: "Le Havre" -> "havre" collides with Havre,
#: Montana; "Los Angeles" -> "angeles" with Angeles, Philippines; likewise
#: El Paso / Paso, La Paz / Paz, Las Vegas / Vegas.
LEADING_ARTICLES = ("the", "a", "an")

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
    """Per-relation control over how candidate identity keys are built.

    ``merge_leading_article_variants`` is the only alias fold that is enabled
    anywhere.  There is deliberately no parenthetical-stripping switch: see the
    module docstring for why that fold is unsafe at any granularity.
    """

    merge_leading_article_variants: bool = True
    max_words: int = 0  # 0 = unlimited; used to reject sentence-like outputs

    def strict_key(self, value: str) -> str:
        """Evaluator-identical key. Collapsing on this is always lossless."""
        return strict_key(value)

    def alias_hint_key(self, value: str) -> str:
        """Conservative alias key used to group candidates internally."""
        return alias_hint_key(
            value, merge_leading_article_variants=self.merge_leading_article_variants
        )

    #: Back-compatible alias: the key the evidence graph groups candidates by.
    def key(self, value: str) -> str:
        return self.alias_hint_key(value)


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


def strict_key(value: str) -> str:
    """The official evaluator's normalisation, applied to a cleaned surface form.

    Two values sharing this key are a single prediction to the scorer, so
    collapsing them can never cost a true positive.
    """
    return official_normalize_string(clean_surface(value))


def strip_leading_article(key: str) -> str:
    """Drop one leading article from an already-normalised key."""
    parts = key.split()
    if len(parts) > 1 and parts[0] in LEADING_ARTICLES:
        return " ".join(parts[1:])
    return key


def alias_hint_key(value: str, *, merge_leading_article_variants: bool = True) -> str:
    """Conservative identity key: strict key plus optional article folding.

    Parenthetical qualifiers are preserved on purpose - "Springfield (Illinois)"
    and "Springfield (Missouri)" must stay distinct candidates.
    """
    key = strict_key(value)
    if merge_leading_article_variants:
        key = strip_leading_article(key)
    return key


def parenthetical_parts(value: str) -> tuple[str, str | None]:
    """Split a surface form into ``(base, qualifier)``.

    Diagnostic only - it lets traces show that two candidates share a base
    string while remaining distinct entities. Nothing merges on the base.
    """
    text = clean_surface(value)
    match = _PARENTHETICAL.search(text)
    if not match:
        return text, None
    base = _PARENTHETICAL.sub(" ", text).strip()
    qualifier = match.group().strip().strip("()[]{} ").strip()
    return (base or text), (qualifier or None)


def canonical_key(value: str, *, merge_leading_article_variants: bool = True) -> str:
    """Deprecated spelling of :func:`alias_hint_key`, kept for call-site stability."""
    return alias_hint_key(
        value, merge_leading_article_variants=merge_leading_article_variants
    )


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
    """Collapse values sharing an alias-hint key, keeping the preferred form.

    Output order follows first appearance, so the result is stable.
    Parenthetically-qualified variants survive as separate entries.
    """
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for value in values:
        surface = clean_surface(value)
        if not surface or is_abstain(surface):
            continue
        key = policy.alias_hint_key(surface)
        if not key:
            continue
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(surface)
    return [preferred_surface_form(grouped[key]) for key in order]
