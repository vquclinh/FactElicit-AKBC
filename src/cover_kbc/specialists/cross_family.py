"""The relation-agnostic cross-family recall primitive.

Proposal §10.2 gives Module 14 a cross-family recall branch, and §11.2 says
"M14's freshness branch may be invoked as a subroutine" for stock. This module
holds the part of that branch which is **not about death** — the recall-family
vocabulary and the eligibility decision — so Module 15 can reuse it without
importing anything from the null/temporal specialist.

Extracted from Module 14 rather than reimplemented, so there is one definition
of what "a genuinely distinct second family" means and one place to change it.
Module 14's behaviour is preserved exactly: its four rationale strings are
supplied by the caller, and a regression test pins all four.

**Not a freshness claim.** §10.2 says "a smaller/fresher model". This repository
establishes that one checkpoint is *smaller*; it establishes nothing about
either one's knowledge cutoff. ``CROSS_FAMILY`` therefore records an
architectural **role**, not a statement about training data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RecallFamily(str, Enum):
    """Which model family produced an observation."""

    PRIMARY_FAMILY = "PRIMARY_FAMILY"
    CROSS_FAMILY = "CROSS_FAMILY"


@dataclass(frozen=True)
class CrossFamilyDecision:
    """Whether the cross-family branch is planned, and why or why not.

    ``rationale`` is persisted, so a run always explains its own choice rather
    than leaving a reader to infer it from an absent probe.
    """

    eligible: bool
    rationale: str


#: Reason strings shared by every caller. The relation-local condition supplies
#: its own two, because only the caller knows what its condition was.
DISABLED = "disabled in configuration"
NO_DISTINCT_FAMILY = (
    "no genuinely distinct second model family is configured; a "
    "cross-family branch through the same checkpoint would be a "
    "resample, not a second family"
)


def decide_cross_family(
    *,
    enabled: bool,
    family_available: bool,
    local_condition_met: bool,
    local_condition_unmet_reason: str,
    eligible_reason: str,
) -> CrossFamilyDecision:
    """Three static conditions, evaluated in a fixed order.

    None of them is a planner decision: ``enabled`` is configuration,
    ``family_available`` is a property of the frozen model profile, and
    ``local_condition_met`` is a relation-local static signal the caller
    computes from upstream state. Nothing here reads yield, budget or
    expected value - those are Modules 19-21's.

    The order matters for the rationale: configuration first, then whether a
    second family exists at all, then the relation's own condition. A reader
    gets the *first* reason the branch was not planned, which is the actionable
    one.
    """
    if not enabled:
        return CrossFamilyDecision(eligible=False, rationale=DISABLED)
    if not family_available:
        return CrossFamilyDecision(eligible=False, rationale=NO_DISTINCT_FAMILY)
    if not local_condition_met:
        return CrossFamilyDecision(eligible=False, rationale=local_condition_unmet_reason)
    return CrossFamilyDecision(eligible=True, rationale=eligible_reason)


def distinct_families(enumerator_model_id: str, verifier_model_id: str) -> bool:
    """Are the two configured families genuinely different?

    Compares the **configured** model ids, mirroring
    ``CoverPipeline.cross_model_recall_available``: in staged Phase A one
    runtime object serves both roles, and judging by object identity would call
    a resample a second family.
    """
    return bool(enumerator_model_id) and bool(verifier_model_id) and (
        enumerator_model_id != verifier_model_id
    )
