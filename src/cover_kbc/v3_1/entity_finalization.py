"""Rejection-first finalization for stock listings, and city-of-death ranking.

companyTradesAtStockExchange - support dominance
-------------------------------------------------
TRAIN discovery evidence (Audit 0075): stock's dominant loss is precision, not
recall - `95` false positives against `33` false negatives, `54` rows
over-enumerated and `37` single-gold rows emitting several exchanges.

Reading the accepted candidates of those rows shows one repeated shape. The
true listing is carried by *several* independent acquisition mechanisms and the
speculative extras by exactly one::

    McBride plc   London Stock Exchange (support 2)  Irish Stock Exchange (1)
    BNP Paribas   Euronext Paris       (support 2)   Euronext Brussels    (1)
    JGC Holdings  Tokyo Stock Exchange (support 2)   Nagoya Stock Exchange(1)

In each case gold is exactly the maximally supported one. Module 8 emits both,
because ``select_small_set`` accepts every candidate that cleared the
acceptance policy and support only affects the *order*, never membership.

PRODUCTION PREDICATE (gold-independent)
    When the accepted listings for a stock query do not all share the same
    independent acquisition support, keep only those at the maximum and drop
    the rest. When they all tie - no evidence distinguishes them - keep them
    all, because support cannot arbitrate what it cannot separate.

This is a *relative* test, so it never imposes a fixed cardinality: a company
with three equally corroborated listings still emits three. Audit 0075's
"do not globally enforce cardinality=1" constraint is satisfied structurally
rather than by a threshold.

It is honestly a trade, not a free win: on TRAIN it improves `12` rows and
harms `7`, because a genuinely multi-listed company whose second exchange was
found once loses that exchange. Net relation macro-F1 is `+0.00700`.

personHasCityOfDeath - evidence ordering
-----------------------------------------
Audit 0075 warns that ``single_valued_keep_first_prediction`` *lowers* TRAIN
macro-F1 (`0.40333` -> `0.40216`), so a singleton guard must rank on evidence,
never on arrival order. :func:`rank_entity_candidates` is that ranking, written
as an explicit policy so a test can assert the property directly: it orders on
verifier acceptance, then independent support, then score, and falls back to
the candidate key only to make ties deterministic. Insertion order is never
consulted, and a contradicted candidate is dropped outright.

On TRAIN this reproduces the existing ``select_null_single`` output on every
row - the current behaviour was already evidence-based - so its measured delta
is `0.00000`. It is retained as an explicit, tested invariant rather than an
implicit consequence of ``_rank_key``.
"""

from __future__ import annotations

from typing import Callable, Sequence

from cover_kbc.normalization.strings import strict_key
from cover_kbc.types import Candidate, VerificationLabel
from cover_kbc.v3_1.retention import is_contradicted

#: Relation this module's listing rules belong to. Named rather than inferred
#: so no other SMALL_SET relation - borders above all - can pick them up.
STOCK_RELATION = "companyTradesAtStockExchange"

#: Values that are never an exchange, whatever the evidence says.
_NEVER_AN_EXCHANGE: frozenset[str] = frozenset({
    "none", "n a", "na", "nan", "null", "nil", "unknown", "empty",
    "valid", "invalid", "not applicable", "not listed", "private",
    "unlisted", "otc", "over the counter",
})


def _verifier_accepted(candidate: Candidate) -> bool:
    """Did the latest calibrated verdict come back VALID?"""
    verdicts = [v for v in candidate.verifications if v.valid_prob is not None]
    return bool(verdicts) and verdicts[-1].label is VerificationLabel.VALID


def reject_structurally_invalid_listings(
    candidates: Sequence[Candidate], subject: str, *, enabled: bool
) -> list[Candidate]:
    """Drop listing outputs that cannot be an exchange, structurally.

    Two rejections only, both decidable from the row itself:

    * the **subject company itself** - a company is not the exchange it trades
      on, and emitting it is a pure false positive;
    * an **abstention or status token** ("NONE", "unlisted", "OTC") that a
      label prefix or surrounding text smuggled past
      ``selection._NEVER_AN_OBJECT``.

    Ticker rejection is deliberately **not** implemented. It cannot be decided
    structurally: ``NYSE``, ``AIM``, ``SIX`` and ``Nasdaq`` are legitimate
    exchange names with exactly the shape - short, capitalised, no spaces - that
    any ticker heuristic keys on, and ``AIM`` is the gold answer for a TRAIN
    row. A rule that cannot separate the two would destroy real answers to
    remove hypothetical ones.
    """
    if not enabled:
        return list(candidates)
    subject_key = strict_key(subject)
    kept: list[Candidate] = []
    for candidate in candidates:
        key = strict_key(candidate.output_value)
        if not key or key == subject_key or key in _NEVER_AN_EXCHANGE:
            continue
        kept.append(candidate)
    return kept


def apply_support_dominance(
    candidates: Sequence[Candidate],
    support_of: Callable[[Candidate], int],
    *,
    enabled: bool,
) -> list[Candidate]:
    """Keep only the maximally supported listings, or all of them on a tie."""
    candidates = list(candidates)
    if not enabled or len(candidates) < 2:
        return candidates
    supports = [support_of(c) for c in candidates]
    top = max(supports)
    if min(supports) == top:
        return candidates  # nothing to arbitrate on
    return [c for c, s in zip(candidates, supports) if s >= top]


def rank_entity_candidates(
    candidates: Sequence[Candidate],
    support_of: Callable[[Candidate], int],
    *,
    drop_contradicted: bool = True,
) -> list[Candidate]:
    """Order single-valued entity candidates by evidence, never by arrival.

    The key is ``(verifier accepted, independent support, score)`` descending,
    with the candidate key last purely for determinism. Position in the input
    sequence contributes nothing, which is the property that separates this
    from the ``keep_first_prediction`` rule Audit 0075 measured as harmful.
    """
    pool = [c for c in candidates if not (drop_contradicted and is_contradicted([c]))]
    return sorted(
        pool,
        key=lambda c: (
            not _verifier_accepted(c),
            -support_of(c),
            -c.score,
            c.key,
        ),
    )


__all__ = [
    "STOCK_RELATION",
    "apply_support_dominance",
    "rank_entity_candidates",
    "reject_structurally_invalid_listings",
]
