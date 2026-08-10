"""FINAL_CANDIDATE_RETENTION - never answer "nothing" while an accepted candidate stands.

TRAIN discovery evidence (Audit 0075, reproduced in 0076)
---------------------------------------------------------
On the full 477-row TRAIN run, `55` rows recorded a non-empty
``CONTROL_SURVIVED`` stage - at least one candidate the acceptance policy
marked ACCEPTED - and an empty ``FINAL_EMITTED``. Every single one of them is
``hasArea``; no other relation loses an accepted candidate at Module 8. The
oracle-free counterfactual that emits those survivors moved TRAIN macro-F1 from
`0.40333` to `0.42045`.

The mechanism, which is a real bug and not a tuning knob
--------------------------------------------------------
:func:`cover_kbc.selection.select_numeric_robust` takes ``clusters[0]`` and, if
that cluster carries no accepted candidate, returns ``[]``. But
:func:`cover_kbc.normalization.numeric.cluster_values` orders clusters by
``(-size, relative_mad, representative)``. When every cluster has size one -
the normal case for a thinly recalled area - size and dispersion both tie, and
the winner is decided by *the numerically smallest value*. Acceptance plays no
part in that ordering, so a query whose only accepted candidate happens to be
the larger of two singleton clusters emits nothing at all.

Mangareva (row 1) is the whole bug in one row: candidate ``1`` is UNRESOLVED,
candidate ``15`` is ACCEPTED, gold is ``15.4``. The tiebreak hands the query to
``1``, ``1`` is not emittable, and the row goes out empty.

PRODUCTION PREDICATE (gold-independent)
---------------------------------------
    If the ordinary winning cluster carries no ACCEPTED candidate, restrict the
    pool to clusters that (a) carry at least one ACCEPTED candidate and (b) are
    not verified INVALID, and re-run the *unchanged* relation ordering over
    that pool. If no cluster qualifies, still emit nothing.

It reads only ``Candidate.status`` (the acceptance policy's own output),
``Candidate.verifications`` and cluster geometry - all inference-time state.
It never consults gold, TRAIN labels, row indices or the subject.

Contradiction safety is explicit: a cluster whose strongest verdict is INVALID
is never retained, so this can only ever rescue a candidate the pipeline itself
still believed in. The rule *widens the pool*; it never overrides an ordering
the pool already supports, so a query that already emits something is untouched.
"""

from __future__ import annotations

from typing import Callable, Sequence, TypeVar

from cover_kbc.types import Candidate, CandidateStatus, VerificationLabel

#: Cluster payload as Module 8 holds it: whatever the caller pairs with members.
_C = TypeVar("_C")


def is_accepted(candidates: Sequence[Candidate]) -> bool:
    """Does any candidate here carry the acceptance policy's ACCEPTED status?"""
    return any(c.status is CandidateStatus.ACCEPTED for c in candidates)


def is_contradicted(candidates: Sequence[Candidate]) -> bool:
    """Is the strongest verdict over these candidates INVALID?

    Mirrors ``selection._cluster_verdict``: a VALID anywhere outranks an
    INVALID, so only a cluster with an INVALID and no VALID counts as
    contradicted. Verdicts without a calibrated probability are not verdicts.
    """
    labels = [
        v.label
        for c in candidates
        for v in c.verifications
        if v.valid_prob is not None
    ]
    if not labels:
        return False
    return VerificationLabel.INVALID in labels and VerificationLabel.VALID not in labels


def is_retainable(candidates: Sequence[Candidate]) -> bool:
    """The retention predicate: accepted, and not contradicted."""
    return is_accepted(candidates) and not is_contradicted(candidates)


def retained_pool(
    clusters: Sequence[tuple[_C, list[Candidate]]],
    *,
    enabled: bool,
    members_of: Callable[[tuple[_C, list[Candidate]]], list[Candidate]] | None = None,
) -> list[tuple[_C, list[Candidate]]]:
    """Restrict ``clusters`` to retainable ones, or leave them untouched.

    Returns the original sequence when the feature is off, when nothing
    qualifies, or when the pool would not change - so the caller's ordering,
    tie-breaking and emptiness semantics are all preserved exactly.
    """
    pool = list(clusters)
    if not enabled:
        return pool
    members = members_of or (lambda entry: entry[1])
    retainable = [entry for entry in pool if members(entry) and is_retainable(members(entry))]
    return retainable or pool


__all__ = [
    "is_accepted",
    "is_contradicted",
    "is_retainable",
    "retained_pool",
]
