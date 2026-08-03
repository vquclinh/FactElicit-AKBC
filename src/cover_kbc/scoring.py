"""Candidate scoring and verification tiering (spec sections 10.5 and 11.2).

Two deterministic, non-neural decisions live here:

``assign_tier``
    Which candidates are worth a verifier call, decided *before* any call is
    spent, from evidence already held.

``score_candidate``
    The auditable combination

        S(o) = a*F(o) + b*L(o) + c*X(o) - d*C(o) - e*U(o)

    Every component is stored separately on the candidate, never just the
    total: a scalar alone cannot distinguish a candidate carried by broad
    structural support from one carried by a single confident verifier call,
    and those two cases warrant different treatment.

No classifier is trained on graph features (spec section 11.3). Weights and
thresholds are ordinary configuration, deliberately few, because relations like
``awardWonBy`` have ten validation examples and would overfit anything richer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from cover_kbc.contracts.base import RelationContract
from cover_kbc.types import (
    Candidate,
    CandidateScore,
    CandidateStatus,
    VerificationLabel,
    VerificationTier,
)


@dataclass(frozen=True)
class ScoringConfig:
    """Weights and thresholds for ``S(o)`` and the tiering rules.

    Defaults are hand-set, not fitted. Any tuning must happen on train or a
    documented internal split and then be frozen before val is scored - tuning
    on val and reporting that same val number is not a measurement.
    """

    # -- S(o) weights --------------------------------------------------------
    alpha_support: float = 1.0
    beta_logit: float = 0.6
    gamma_cross_model: float = 0.5   # Mistral enumerator + Qwen verifier
    delta_contradiction: float = 1.5
    eta_disagreement: float = 1.0

    # -- tiering -------------------------------------------------------------
    #: Independent mechanism support at or above which a candidate skips
    #: verification entirely.
    auto_accept_support: int = 3
    #: At or below this support a candidate is always a verification target.
    verify_max_support: int = 2
    #: Normalised prompt disagreement above which a candidate is escalated.
    adversarial_disagreement: float = 0.15

    # -- acceptance ----------------------------------------------------------
    #: S(o) at or above which a candidate is emitted. Set so that a candidate
    #: found by a single mechanism still passes on its own: the job of removing
    #: bad candidates belongs to the verifier and the contradiction penalty,
    #: not to a support threshold that would also delete correct rare answers.
    accept_score: float = 0.2
    #: Calibrated P(VALID) below which a verified candidate is dropped.
    min_valid_prob: float = 0.40
    #: Drop candidates whose verifier verdict is UNKNOWN.
    drop_on_unknown: bool = True
    #: Clip on L(o) so one extreme logit cannot dominate the sum.
    logit_clip: float = 3.0
    #: Fraction of X(o) credited when the second model only *agreed with a
    #: candidate it was shown*, rather than recalling it independently.
    shown_candidate_weight: float = 0.25

    def weights(self) -> dict[str, float]:
        return {
            "alpha_support": self.alpha_support,
            "beta_logit": self.beta_logit,
            "gamma_cross_model": self.gamma_cross_model,
            "delta_contradiction": self.delta_contradiction,
            "eta_disagreement": self.eta_disagreement,
        }

    @classmethod
    def from_mapping(cls, config: Mapping[str, object] | None) -> "ScoringConfig":
        if not config:
            return cls()
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in config.items() if k in fields})  # type: ignore[arg-type]


DEFAULT_SCORING = ScoringConfig()


# --------------------------------------------------------------------------
# Score components
# --------------------------------------------------------------------------


def support_term(candidate: Candidate, contract: RelationContract) -> float:
    """``F(o)``: independent mechanism support, normalised to ``[0, 1]``.

    Uses ``independent_support``, never ``num_facets`` - slicing one mechanism
    into five facets must not read as five corroborations.
    """
    denominator = max(1, contract.coverage_denominator())
    return min(1.0, candidate.independent_support / denominator)


def logit_term(candidate: Candidate, config: ScoringConfig = DEFAULT_SCORING) -> float:
    """``L(o)``: clipped calibrated verifier log-odds, rescaled to ``[-1, 1]``.

    Returns 0.0 when the candidate was never verified. That is deliberately
    neutral: an unverified candidate has no verifier evidence either way, and
    must not be penalised as though it had been rejected.
    """
    verifications = [v for v in candidate.verifications if v.valid_prob is not None]
    if not verifications:
        return 0.0
    latest = verifications[-1]
    odds = latest.log_odds()
    if odds is None:
        return 0.0
    return max(-1.0, min(1.0, odds / config.logit_clip))


def contradiction_term(candidate: Candidate, contract: RelationContract) -> float:
    """``C(o)``: contradiction strength, normalised to ``[0, 1]``."""
    denominator = max(1, contract.coverage_denominator())
    return min(1.0, candidate.contradiction_count / denominator)


def disagreement_term(candidate: Candidate) -> float:
    """``U(o)``: normalised prompt-distribution disagreement in ``[0, 1]``."""
    values = [
        v.prompt_disagreement
        for v in candidate.verifications
        if v.prompt_disagreement is not None
    ]
    return max(values) if values else 0.0


def cross_model_term(
    candidate: Candidate, config: "ScoringConfig | None" = None
) -> float:
    """``X(o)``: heterogeneous support from the second model family.

    Weighted by *how* the second model supported it:

    * independent recall - the verifier-family model produced the name itself,
      never having been shown it. Genuinely separate evidence, full weight.
    * shown-candidate agreement - it merely agreed with a name we handed it.
      Anchoring makes this far cheaper to obtain, so it earns a fraction of the
      weight, and its calibrated strength is already carried by ``L(o)``.
    """
    from cover_kbc.types import EvidenceMode, IndependenceGroup

    config = config or DEFAULT_SCORING
    recall = candidate.groups.get(IndependenceGroup.CROSS_MODEL_RECALL)
    if recall and recall.supports_candidate:
        return 1.0

    verifier = candidate.groups.get(IndependenceGroup.BLIND_VERIFIER)
    if verifier and verifier.supports_candidate and all(
        e.mode is EvidenceMode.SHOWN_CANDIDATE for e in verifier.supports
    ):
        return config.shown_candidate_weight
    return 0.0


def score_candidate(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> CandidateScore:
    """Compute ``S(o)`` and store the component breakdown on the candidate."""
    support = support_term(candidate, contract)
    logit = logit_term(candidate, config)
    cross = cross_model_term(candidate, config)
    contradiction = contradiction_term(candidate, contract)
    disagreement = disagreement_term(candidate)

    total = (
        config.alpha_support * support
        + config.beta_logit * logit
        + config.gamma_cross_model * cross
        - config.delta_contradiction * contradiction
        - config.eta_disagreement * disagreement
    )

    breakdown = CandidateScore(
        support=support,
        logit=logit,
        cross_model=cross,
        contradiction=contradiction,
        disagreement=disagreement,
        weights=config.weights(),
        total=total,
    )
    candidate.score_breakdown = breakdown
    candidate.score = total
    return breakdown


# --------------------------------------------------------------------------
# Verification tiering (spec section 10.5)
# --------------------------------------------------------------------------


def assign_tier(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> VerificationTier:
    """Decide how much verification a candidate deserves.

    Evaluated in strict precedence order, so the outcome is reproducible and
    independent of candidate ordering.
    """
    # 1. Deterministic contract/type violation - no model call can rescue it.
    if candidate.status is CandidateStatus.REJECTED:
        return VerificationTier.HARD_REJECT

    # 2. Explicit conflict, or templates that disagree about it.
    if candidate.contradiction_count > 0:
        return VerificationTier.ADVERSARIAL_VERIFY
    if disagreement_term(candidate) > config.adversarial_disagreement:
        return VerificationTier.ADVERSARIAL_VERIFY

    # 3. Broad independent support and nothing against it.
    threshold = min(
        config.auto_accept_support, contract.verification.auto_accept_independent_support
    )
    if candidate.independent_support >= threshold:
        return VerificationTier.AUTO_ACCEPT

    # 4. Weak or threshold-adjacent support: worth a call.
    if candidate.independent_support <= config.verify_max_support:
        return VerificationTier.VERIFY

    return VerificationTier.UNRESOLVED


def verification_targets(
    candidates: list[Candidate],
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
    *,
    budget: int,
) -> list[Candidate]:
    """Pick which candidates actually get verifier calls, within budget.

    Adversarial cases first (a contradiction unresolved is the most expensive
    kind of error), then ordinary verification targets, weakest support first.
    Ties break on key so the selection is deterministic.
    """
    if budget <= 0:
        return []

    tiers = {c.key: assign_tier(c, contract, config) for c in candidates}
    for candidate in candidates:
        candidate.tier = tiers[candidate.key]

    ordered = sorted(
        (c for c in candidates if tiers[c.key] in (
            VerificationTier.ADVERSARIAL_VERIFY, VerificationTier.VERIFY
        )),
        key=lambda c: (
            0 if tiers[c.key] is VerificationTier.ADVERSARIAL_VERIFY else 1,
            c.independent_support,
            c.key,
        ),
    )
    return ordered[:budget]


def decide_status(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> CandidateStatus:
    """Final accept / reject / unresolved decision for one candidate.

    A generated candidate is never accepted merely because it was generated
    (spec Milestone-2 requirement 12): acceptance requires either broad
    independent support or a verifier signal that survives calibration.
    """
    if candidate.status is CandidateStatus.REJECTED:
        return CandidateStatus.REJECTED

    # Contract-level floor on how many independent mechanisms must have seen it.
    if candidate.independent_support < contract.selection.min_independent_support:
        return CandidateStatus.UNRESOLVED

    verifications = [v for v in candidate.verifications if v.valid_prob is not None]
    if verifications:
        latest = verifications[-1]
        if latest.label is VerificationLabel.INVALID:
            return CandidateStatus.REJECTED
        if latest.label is VerificationLabel.UNKNOWN and config.drop_on_unknown:
            # An explicit "I don't know" is not support. Fall through to the
            # score, which will only rescue a broadly supported candidate.
            if candidate.independent_support < config.auto_accept_support:
                return CandidateStatus.UNRESOLVED
        if (latest.valid_prob or 0.0) < config.min_valid_prob:
            return CandidateStatus.UNRESOLVED

    if candidate.tier is VerificationTier.AUTO_ACCEPT:
        return CandidateStatus.ACCEPTED
    if candidate.score >= config.accept_score:
        return CandidateStatus.ACCEPTED
    return CandidateStatus.UNRESOLVED


def summarize_tiers(candidates: list[Candidate]) -> dict[str, int]:
    """Tier histogram for the run trace."""
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.tier.value] = counts.get(candidate.tier.value, 0) + 1
    return dict(sorted(counts.items()))
