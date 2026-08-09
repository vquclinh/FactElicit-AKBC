"""The closed vocabularies the V3A failure analysis is written in.

Six stages, seven failure categories, five false-positive categories and eight
search states - all enums, all deterministic, none of them consulted by the
inference path.

The stages are the *observable* boundaries of the existing pipeline, not an
idealised model of it. Where a boundary the taxonomy names is not directly
observable, this module says so rather than inventing it: see
:data:`STAGE_SOURCES`, which records, for every stage, the concrete production
state it is read from.

One boundary is deliberately absent, and its absence is a finding rather than
an omission. Modules 20 and 21 **never remove a candidate**: Module 20 reserves
compute and Module 21 chooses the next action or STOP. A gold object the
controller costs us is one it stopped before acquiring, and that loss is
observable at :attr:`PipelineStage.ACQUIRED`, not at a later "the controller
dropped it" stage that does not exist. What *does* remove a candidate after
verification is the acceptance policy (``scoring.decide_status``), and that is
what :attr:`PipelineStage.CONTROL_SURVIVED` measures.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class PipelineStage(str, Enum):
    """Where a candidate stands, in irreversible pipeline order.

    Order matters: :data:`STAGE_ORDER` is what the failure taxonomy walks to
    find the last stage a gold object survived to.
    """

    #: The surfaces the model produced, before any *rejecting* normalisation.
    #: Read from ``GenerationRecord.raw_output`` through
    #: :func:`~cover_kbc.elicitation.parsing.acquisition_fragments`.
    ACQUIRED = "ACQUIRED"
    #: Admitted to Module 3's evidence graph as a candidate node, and not
    #: rejected by §9.3's hard contract rules.
    NORMALIZED = "NORMALIZED"
    #: A verifier call was actually spent on it - Module 4's blind verifier or
    #: Module 17's specialist suite, both of which land as a
    #: ``VerificationResult`` on the candidate.
    VERIFIER_REACHED = "VERIFIER_REACHED"
    #: ...and the verdict that scoring reads (the latest) was VALID.
    VERIFIER_ACCEPTED = "VERIFIER_ACCEPTED"
    #: Survived the acceptance policy: ``decide_status`` returned ACCEPTED.
    CONTROL_SURVIVED = "CONTROL_SURVIVED"
    #: Module 8 put it in ``ObjectEntities``.
    FINAL_EMITTED = "FINAL_EMITTED"


#: Stages in pipeline order. The single source of ordering for the whole
#: analysis - nothing below re-states it.
STAGE_ORDER: tuple[PipelineStage, ...] = (
    PipelineStage.ACQUIRED,
    PipelineStage.NORMALIZED,
    PipelineStage.VERIFIER_REACHED,
    PipelineStage.VERIFIER_ACCEPTED,
    PipelineStage.CONTROL_SURVIVED,
    PipelineStage.FINAL_EMITTED,
)


#: The production state each stage is read from. Written down because "the
#: nearest observable boundary" is a claim that has to be checkable against the
#: code, and because two of these are *not* the boundary a naive reading would
#: assume.
STAGE_SOURCES: Mapping[PipelineStage, str] = {
    PipelineStage.ACQUIRED:
        "acquisition_fragments(GenerationRecord.raw_output) over every record "
        "the query registered, including the verifier-family recall view",
    PipelineStage.NORMALIZED:
        "EvidenceGraph.candidates, excluding nodes apply_hard_contract_rules "
        "marked REJECTED",
    PipelineStage.VERIFIER_REACHED:
        "Candidate.verifications non-empty (Module 4 blind verification and "
        "Module 17 verdicts bridged through graph.add_verification)",
    PipelineStage.VERIFIER_ACCEPTED:
        "the latest VerificationResult's label is VALID - 'latest' because that "
        "is the one scoring.decide_status reads",
    PipelineStage.CONTROL_SURVIVED:
        "Candidate.status is ACCEPTED after scoring.decide_status, i.e. the "
        "last state before Module 8's relation-specific selection",
    PipelineStage.FINAL_EMITTED:
        "Prediction.object_entities",
}


class FailureCategory(str, Enum):
    """Where a gold object was irreversibly lost.

    Assigned from the *last* stage the object was present at, not the first one
    it was missing from. The two differ, because presence is not monotone: a
    numeric answer is emitted as a cluster median (``_emit_numeric``) that need
    never have been generated verbatim, so a gold value can be absent at
    ``ACQUIRED`` and present at ``FINAL_EMITTED``. "Earliest *irreversible*
    loss" is therefore the transition after the last stage it survived to.
    """

    NEVER_ACQUIRED = "NEVER_ACQUIRED"
    LOST_IN_NORMALIZATION = "LOST_IN_NORMALIZATION"
    NOT_SENT_TO_VERIFIER = "NOT_SENT_TO_VERIFIER"
    REJECTED_BY_VERIFIER = "REJECTED_BY_VERIFIER"
    DROPPED_AFTER_VERIFICATION = "DROPPED_AFTER_VERIFICATION"
    DROPPED_BY_FINAL_SELECTION = "DROPPED_BY_FINAL_SELECTION"
    SUCCESSFULLY_EMITTED = "SUCCESSFULLY_EMITTED"
    #: The query never produced an observable state to attribute against - a
    #: ``PIPELINE_ERROR`` row, or telemetry recorded before the stage existed.
    #: Never inferred from absence; only set when the source state is missing.
    NOT_OBSERVABLE = "NOT_OBSERVABLE"


#: The loss that follows each stage. ``FINAL_EMITTED`` has no successor loss -
#: surviving it is the success case - so it is absent by construction.
LOSS_AFTER_STAGE: Mapping[PipelineStage, FailureCategory] = {
    PipelineStage.ACQUIRED: FailureCategory.LOST_IN_NORMALIZATION,
    PipelineStage.NORMALIZED: FailureCategory.NOT_SENT_TO_VERIFIER,
    PipelineStage.VERIFIER_REACHED: FailureCategory.REJECTED_BY_VERIFIER,
    PipelineStage.VERIFIER_ACCEPTED: FailureCategory.DROPPED_AFTER_VERIFICATION,
    PipelineStage.CONTROL_SURVIVED: FailureCategory.DROPPED_BY_FINAL_SELECTION,
}


class FalsePositiveCategory(str, Enum):
    """Where a wrong object entered, or was allowed to stay.

    Read off the same observed stage sets as :class:`FailureCategory`, from the
    other side: a false positive is attributed to the **latest** stage it
    reached, because that is the last place that could have removed it and did
    not.
    """

    #: Proposed by acquisition and dropped by normalisation. Cost nothing.
    ACQUISITION_FP = "ACQUISITION_FP"
    #: Survived normalisation into the graph but never reached a verifier.
    #: Named for the alias/identity fold that admits it: two surface forms of
    #: one wrong entity are one node here, and the fold is what keeps it.
    NORMALIZATION_ALIAS_FP = "NORMALIZATION_ALIAS_FP"
    #: A verifier saw it and returned VALID.
    VERIFIER_FALSE_ACCEPT = "VERIFIER_FALSE_ACCEPT"
    #: The acceptance policy kept it - either without verification, or after a
    #: verdict that was not strong enough to remove it.
    CONTROL_FP_SURVIVAL = "CONTROL_FP_SURVIVAL"
    #: Module 8 emitted it. The only category that costs precision.
    FINAL_FP = "FINAL_FP"


#: The false-positive category earned by reaching each stage.
FP_AT_STAGE: Mapping[PipelineStage, FalsePositiveCategory] = {
    PipelineStage.ACQUIRED: FalsePositiveCategory.ACQUISITION_FP,
    PipelineStage.NORMALIZED: FalsePositiveCategory.NORMALIZATION_ALIAS_FP,
    PipelineStage.VERIFIER_REACHED: FalsePositiveCategory.NORMALIZATION_ALIAS_FP,
    PipelineStage.VERIFIER_ACCEPTED: FalsePositiveCategory.VERIFIER_FALSE_ACCEPT,
    PipelineStage.CONTROL_SURVIVED: FalsePositiveCategory.CONTROL_FP_SURVIVAL,
    PipelineStage.FINAL_EMITTED: FalsePositiveCategory.FINAL_FP,
}


class FailureSearchState(str, Enum):
    """What kind of situation this query is in, for a later planner to read.

    Computed deterministically from evidence already recorded plus the
    relation's :class:`~cover_kbc.contracts.relation_profile.RelationProfile`.

    **Telemetry only in V3A.** Module 21 does not see it, does not rank on it
    and does not change one action because of it. It exists so that V3C has a
    typed surface to consume instead of inventing one later.
    """

    #: Nothing survived normalisation. Recall, not judgement, is the problem.
    NO_CANDIDATE = "NO_CANDIDATE"
    #: Exactly one candidate, carried by a single acquisition mechanism.
    SINGLE_LOW_SUPPORT = "SINGLE_LOW_SUPPORT"
    #: Several candidates that cannot all be right for a single-valued relation,
    #: or numeric clusters that disagree.
    MULTIPLE_CONFLICTING = "MULTIPLE_CONFLICTING"
    #: A precision-dominated relation holding more unverified candidates than
    #: its set behaviour can justify.
    HIGH_FP_RISK = "HIGH_FP_RISK"
    #: An open set that is still yielding new members.
    SET_GROWING = "SET_GROWING"
    #: The verifier keeps answering UNKNOWN - §8.3's "not a contradiction".
    SEMANTIC_AMBIGUITY = "SEMANTIC_AMBIGUITY"
    #: Verified, accepted, and not moving. The stop case.
    STABLE_VERIFIED = "STABLE_VERIFIED"
    #: A relation that may legitimately be empty, with no positive null
    #: evidence either way - §10.3's "failed recall is not gold is empty".
    NULL_UNRESOLVED = "NULL_UNRESOLVED"


__all__ = [
    "FP_AT_STAGE",
    "FailureCategory",
    "FailureSearchState",
    "FalsePositiveCategory",
    "LOSS_AFTER_STAGE",
    "PipelineStage",
    "STAGE_ORDER",
    "STAGE_SOURCES",
]
