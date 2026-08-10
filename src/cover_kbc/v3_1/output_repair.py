"""Structural repair of leaked enumeration lines in the final output.

TRAIN discovery evidence (Audit 0076)
-------------------------------------
`76` values emitted for ``awardWonBy`` on the TRAIN run are not entities at
all. They are whole *bucket lines* from a decade-wise or category-wise
enumeration that acquisition kept verbatim::

    'Groups: NONE'          'Organisations: NONE'      '1920s: NONE'
    'Individuals: Albert Schweitzer'                   '1990s: Alan Shearer'
    '1901-1909: Wilhelm Conrad Röntgen'

Both halves cost score. ``'Groups: NONE'`` is a pure false positive - the
payload is the prompt's own "no members" token. ``'1990s: Alan Shearer'`` names
a real winner but can never match gold, because the evaluator normalises the
whole string and ``'1990s alan shearer' != 'alan shearer'``. One row, *Order of
the Golden Fleece (Georgia)*, emitted four values of which all four were
``: NONE`` - the correct answer was empty.

Why this is a finalization bug and not a prompt problem
------------------------------------------------------
``selection._NEVER_AN_OBJECT`` already refuses to emit a bare ``NONE``,
``N/A`` or ``unknown``: the pipeline's own contract says an abstention marker
is not an object. The guard is defeated purely by the label prefix. This module
extends the *existing* guard through one level of enumeration label, and does
nothing else.

PRODUCTION PREDICATE (gold-independent)
---------------------------------------
    Split a final value on its first ``':'``. If the prefix is a *bucket label*
    - a temporal bucket (``1990s``, ``1901-1909``, ``1974``) or one of a closed
    vocabulary of generic enumeration nouns (``Individuals``, ``Groups``,
    ``Organisations``, ...) - then the value is a leaked enumeration line:
    drop it when the payload normalises to an abstention token, otherwise
    reduce it to the payload. Any other value is returned untouched.

The label vocabulary is generic English enumeration structure. It contains no
subject, no answer, and no relation-specific fact; ``'Groups: NONE'`` is
repaired identically whichever award produced it.
"""

from __future__ import annotations

import re
from typing import Iterable

from cover_kbc.normalization.strings import strict_key

#: Payloads that mean "nothing here", normalised through the evaluator's own
#: folding. A superset of ``selection._NEVER_AN_OBJECT`` in normalised form.
ABSTENTION_PAYLOADS: frozenset[str] = frozenset({
    "none", "n a", "na", "nan", "null", "nil", "nothing", "empty",
    "unknown", "not applicable", "not available", "no data",
    "no winner", "no winners", "no recipient", "no recipients",
    "valid", "invalid",
})

#: Generic enumeration category nouns a bucketed prompt uses as headers. Purely
#: structural English vocabulary - never an entity, never an answer.
CATEGORY_LABELS: frozenset[str] = frozenset({
    "individual", "individuals", "person", "persons", "people",
    "group", "groups", "team", "teams",
    "organisation", "organisations", "organization", "organizations",
    "project", "projects", "company", "companies", "institution", "institutions",
    "winner", "winners", "recipient", "recipients",
    "category", "categories", "example", "examples",
    "other", "others", "note", "notes", "answer", "answers", "entry", "entries",
})

#: Temporal bucket headers: ``1990s``, ``1901-1909``, ``1974`` (any dash form).
_TEMPORAL_LABEL = re.compile(
    r"^\d{3,4}s$|^\d{4}\s*[-‐-―]\s*\d{4}$|^\d{4}$"
)


def is_bucket_label(label: str) -> bool:
    """Is ``label`` an enumeration bucket header rather than part of a name?"""
    text = label.strip()
    if not text:
        return False
    if _TEMPORAL_LABEL.match(text):
        return True
    return strict_key(text) in CATEGORY_LABELS


def is_abstention(payload: str) -> bool:
    """Does ``payload`` mean the bucket was empty?"""
    return strict_key(payload) in ABSTENTION_PAYLOADS


def repair_value(value: str) -> str | None:
    """Repair one emitted value. ``None`` means "do not emit this at all"."""
    if ":" not in value:
        return value
    label, payload = value.split(":", 1)
    if not is_bucket_label(label):
        return value
    payload = payload.strip()
    if not payload or is_abstention(payload):
        return None
    return payload


def repair_values(values: Iterable[str], *, enabled: bool) -> list[str]:
    """Repair a whole row, dropping abstention buckets and folding duplicates.

    Repair can make two values identical (``'1990s: X'`` and ``'Individuals: X'``
    both reduce to ``X``), so the result is deduplicated on ``strict_key`` - the
    evaluator's own normalisation, the only fold that is lossless by
    construction. Order is preserved.
    """
    values = list(values)
    if not enabled:
        return values
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        repaired = repair_value(value)
        if repaired is None:
            continue
        key = strict_key(repaired)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(repaired)
    return out


__all__ = [
    "ABSTENTION_PAYLOADS",
    "CATEGORY_LABELS",
    "is_abstention",
    "is_bucket_label",
    "repair_value",
    "repair_values",
]
