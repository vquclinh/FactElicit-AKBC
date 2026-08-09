"""The typed search state a later milestone will plan on. **Shadow in V3A.**

§8 of the V3A brief asks for a diagnostic state surface that V3C's
failure-aware Module 21 can consume, computed now so that the vocabulary is
settled before anything depends on it.

The whole of its V3A contract is:

* deterministic - the same record and profile always give the same state;
* derived from evidence the run already produced plus the relation's
  :class:`~cover_kbc.contracts.relation_profile.RelationProfile`;
* **read by nothing**. Module 21 does not receive it, is not passed it, and
  cannot reach it: it is written onto a telemetry record after the query is
  finished. No action choice, no budget decision and no emitted object moves
  because of a value computed here.

The rules below are ordered, and the order is the semantics: the first one that
matches wins, so a confident negative gate is never re-read as "no candidates",
and a single-valued relation holding two accepted answers is a conflict before
it is anything else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cover_kbc.contracts.relation_profile import (
    RelationProfile,
    SearchBias,
    SetBehavior,
)
from cover_kbc.diagnostics.stages import FailureSearchState, PipelineStage
from cover_kbc.types import CandidateStatus, VerificationLabel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cover_kbc.diagnostics.inference_telemetry import QueryInferenceRecord


def derive_failure_state(
    record: "QueryInferenceRecord", profile: RelationProfile,
) -> FailureSearchState:
    """Classify one finished query's situation.

    Args:
        record: an **observable** finished query. An unobservable record (a
            PIPELINE_ERROR row) has no state to classify and the recorder does
            not ask for one.
        profile: the relation's declared failure/search semantics.

    Returns:
        Exactly one :class:`FailureSearchState`. Total by construction - the
        last rule has no condition.
    """
    live = [c for c in record.candidates if not c.hard_rejected]
    accepted = [c for c in live
                if c.final_status == CandidateStatus.ACCEPTED.value]
    unresolved = [c for c in live
                  if c.final_status == CandidateStatus.UNRESOLVED.value]
    unknown_verdicts = [c for c in live
                        if c.verifier_label == VerificationLabel.UNKNOWN.value]

    # 1. A calibrated confident NO is an *answer*, not an absence (§10.3). It
    #    is settled evidence, so it is not an unresolved null.
    if record.gate_negative:
        return FailureSearchState.STABLE_VERIFIED

    # 2. Nothing survived normalisation. What that means depends entirely on
    #    whether the relation is allowed to be empty: for a scalar it is a flat
    #    recall failure, for a null-capable relation it is exactly §10.3's
    #    "failed recall is not the same as gold is empty".
    if not record.values_at(PipelineStage.NORMALIZED):
        return (FailureSearchState.NULL_UNRESOLVED if profile.allows_empty
                else FailureSearchState.NO_CANDIDATE)

    # 3. Candidates exist and none was accepted. An explicit UNKNOWN is the
    #    §8.3 case - the verifier declined rather than contradicted - and it is
    #    a different problem from having weak or conflicting evidence.
    if not accepted:
        if unknown_verdicts:
            return FailureSearchState.SEMANTIC_AMBIGUITY
        return (FailureSearchState.MULTIPLE_CONFLICTING if len(live) >= 2
                else FailureSearchState.SINGLE_LOW_SUPPORT)

    # 4. A single-valued relation holding two accepted answers cannot be right
    #    about both, whatever the evidence says about each.
    if not profile.is_set_valued and len(accepted) >= 2:
        return FailureSearchState.MULTIPLE_CONFLICTING

    # 5. An open set with unresolved members is still being discovered (§11.3's
    #    closure test has not been met).
    if profile.set_behavior is SetBehavior.OPEN_SET and unresolved:
        return FailureSearchState.SET_GROWING

    # 6. A precision-dominated relation carrying several *unverified* accepted
    #    candidates is the §11.2 failure: parent/subsidiary and index listings
    #    accumulate exactly here.
    if profile.search_bias is SearchBias.ELIMINATION:
        unverified = [c for c in accepted
                      if c.verifier_label != VerificationLabel.VALID.value]
        if len(unverified) >= 2:
            return FailureSearchState.HIGH_FP_RISK

    # 7. Everything emitted was independently verified VALID: the stop case.
    if accepted and all(
            c.verifier_label == VerificationLabel.VALID.value for c in accepted):
        return FailureSearchState.STABLE_VERIFIED

    # 8. One answer, one mechanism behind it. Worth another view before trusting.
    if len(accepted) == 1 and accepted[0].independent_support <= 1:
        return FailureSearchState.SINGLE_LOW_SUPPORT

    # 9. Accepted, plural, multi-mechanism, not conflicting and not open. There
    #    is no further work this vocabulary can name.
    return FailureSearchState.STABLE_VERIFIED


__all__ = ["derive_failure_state"]
