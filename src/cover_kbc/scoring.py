"""Module 5 - evidence and uncertainty state (spec section 11), plus tiering.

Spec section 11 opens with the constraint that shapes this whole module: *"The
controller should not make decisions from a single scalar confidence."* So the
job here is not to produce a number, it is to produce a faithful **state** from
which Modules 6-8 can reason, of which ``S(o)`` is one deterministic summary.

Three deterministic, non-neural decisions live here:

``candidate_state``
    The full state: ``F``, ``L``, ``X``, ``C``, ``U`` alongside ``q(o)``,
    ``H_inc``, ``H_ver`` and ``U_prompt``, each separately inspectable.

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

**The evidence-accounting rule.** Each of the five terms owns a disjoint set of
evidence mechanisms, so one event cannot be paid for twice:

===================================  ===========================
mechanism                            term
===================================  ===========================
acquisition families (Module 2)      ``F(o)`` via ``q(o)``
blind verifier, shown the candidate  ``L(o)`` (and ``C``/``U``)
second model's independent recall    ``X(o)``
===================================  ===========================

The separation is enforced by *index set*, not by convention: ``F`` is computed
over ``acquisition_groups`` only, which structurally excludes the gate, the
verifier and cross-model recall. See :func:`acquisition_groups`.

No classifier is trained on graph features (spec section 11.3). Weights and
thresholds are ordinary configuration, deliberately few, because relations like
``awardWonBy`` have ten validation examples and would overfit anything richer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract, eligible_groups_for
from cover_kbc.elicitation.library import get_view
from cover_kbc.types import (
    Candidate,
    CandidateScore,
    CandidateStatus,
    EvidenceMode,
    IndependenceGroup,
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
    #: Escalate to the adversarial template when the relation contract declares
    #: near-miss classes and the candidate is this weakly supported.
    #:
    #: Spec section 10.5 puts a candidate in the adversarial tier when it "is a
    #: common near miss specified by the contract". Deciding that a *particular*
    #: candidate is a near miss would require factual knowledge, which is
    #: forbidden here. The proxy is non-factual and uses only what the
    #: architecture already knows: this relation is near-miss-prone (Module 0
    #: declares the classes) and this candidate rests on the thinnest evidence
    #: (Module 3 counts the mechanisms) - precisely where a near miss slips
    #: through. Better-supported candidates get the ordinary verifier.
    adversarial_max_support: int = 1
    #: Turn the rule above off entirely (the contract still supplies classes).
    adversarial_on_declared_near_misses: bool = True

    # -- acceptance ----------------------------------------------------------
    #: S(o) at or above which a candidate is emitted. Set so that a candidate
    #: found by a single mechanism still passes on its own: the job of removing
    #: bad candidates belongs to the verifier and the contradiction penalty,
    #: not to a support threshold that would also delete correct rare answers.
    accept_score: float = 0.2
    #: Calibrated P(VALID) below which a verified candidate is dropped.
    #: Fallback only: a relation contract that declares its own value wins.
    min_valid_prob: float = 0.40
    #: Drop candidates whose verifier verdict is UNKNOWN. Fallback only.
    drop_on_unknown: bool = True
    #: Emergency override, named explicitly. When set, the global values above
    #: replace the contract's, for every relation. Off by default: relation
    #: policy is authoritative, and overriding it should be a deliberate,
    #: visible act rather than an emergent effect of combining thresholds.
    force_global_verification_policy: bool = False
    #: Clip on L(o) so one extreme logit cannot dominate the sum.
    logit_clip: float = 3.0
    #: Epsilon inside the L(o) log-odds, guarding log(0) at a saturated verifier.
    logit_epsilon: float = 1e-6

    # -- evidence accounting -------------------------------------------------
    #: Whether the optional acquisition families can run *at all* under this
    #: configuration. It sets ``m(o)``: the groups "capable of expressing that
    #: candidate" (spec section 11.1).
    #:
    #: True for the target architecture, where the active controller may
    #: schedule any declared view, and for a fixed run that executes every
    #: optional view. False only for a mandatory-only ablation, where the
    #: optional families genuinely cannot produce a candidate and would
    #: otherwise depress ``q(o)`` for a mechanism that never had a chance.
    #:
    #: This is *availability*, never *execution*. Counting only executed groups
    #: would make ``q(o) = 1`` after a single direct view and tell the residual
    #: estimator the query was fully covered before exploring anything else.
    optional_views_available: bool = True

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


@dataclass(frozen=True)
class EffectiveVerification:
    """Verification thresholds actually in force for one relation.

    Resolution rule, matching :func:`cover_kbc.controller.resolve_stopping`:

    * the relation contract's value is **authoritative** where it declares one;
    * the global :class:`ScoringConfig` supplies the default where it does not;
    * ``force_global_verification_policy`` is the single, named escape hatch.

    Thresholds are never combined arithmetically. A relation that wants a lower
    acceptance bar than the global default - a recall-first operating point -
    must be able to have it.
    """

    auto_accept_support: int
    min_valid_prob: float
    drop_on_unknown: bool
    source: str

    def to_json(self) -> dict[str, object]:
        return {
            "auto_accept_support": self.auto_accept_support,
            "min_valid_prob": self.min_valid_prob,
            "drop_on_unknown": self.drop_on_unknown,
            "source": self.source,
        }


def resolve_verification(
    contract: RelationContract, config: ScoringConfig = DEFAULT_SCORING
) -> EffectiveVerification:
    """Which verification thresholds apply here, and where they came from."""
    if config.force_global_verification_policy:
        return EffectiveVerification(
            auto_accept_support=config.auto_accept_support,
            min_valid_prob=config.min_valid_prob,
            drop_on_unknown=config.drop_on_unknown,
            source="scoring_config(forced)",
        )

    policy = contract.verification
    declared = [
        name
        for name, value in (
            ("auto_accept_support", policy.auto_accept_independent_support),
            ("min_valid_prob", policy.accept_valid_prob),
            ("drop_on_unknown", policy.drop_on_unknown),
        )
        if value is not None
    ]
    source = f"contract:{contract.relation}" if declared else "scoring_config"
    if declared and len(declared) < 3:
        source += f"+scoring_config(defaults for {3 - len(declared)} field(s))"

    return EffectiveVerification(
        auto_accept_support=(
            policy.auto_accept_independent_support
            if policy.auto_accept_independent_support is not None
            else config.auto_accept_support
        ),
        min_valid_prob=(
            policy.accept_valid_prob
            if policy.accept_valid_prob is not None
            else config.min_valid_prob
        ),
        drop_on_unknown=(
            policy.drop_on_unknown
            if policy.drop_on_unknown is not None
            else config.drop_on_unknown
        ),
        source=source,
    )


# --------------------------------------------------------------------------
# Independent-support coverage (spec section 11.1)
# --------------------------------------------------------------------------


def acquisition_groups(
    contract: RelationContract, config: ScoringConfig = DEFAULT_SCORING
) -> tuple[IndependenceGroup, ...]:
    """``m(o)``: groups *capable of expressing* this candidate.

    Three things this deliberately is not:

    * **not a catalogue.** A family that cannot run under this configuration
      cannot express a candidate, so it is excluded rather than counted as a
      permanent miss.
    * **not "executed so far".** That would make ``q(o) = 1`` after a single
      direct view, telling Module 6 the query was saturated before any other
      mechanism was tried - the exact failure mode that destroys residual
      coverage.
    * **not every group with a support edge.** The existence gate returns a
      verdict rather than candidates; the blind verifier is *shown* the
      candidate and is paid through ``L(o)``; cross-model recall has its own
      term ``X(o)``. Including any of them here is double counting, and would
      also let ``g(o)`` exceed ``m(o)``.

    ``contract.eligible_independence_groups`` already maps only the
    candidate-acquisition view families, so gate, verifier, cross-model and the
    disabled factual-decoding branch are excluded by construction.
    """
    declared = tuple(contract.eligible_independence_groups)
    if config.optional_views_available:
        return declared
    reachable = set(
        eligible_groups_for(
            [get_view(contract.relation, view_id).family for view_id in contract.mandatory_views]
        )
    )
    return tuple(group for group in declared if group in reachable)


def supporting_acquisition_groups(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> tuple[IndependenceGroup, ...]:
    """``g(o)``: the subset of ``m(o)`` that actually supports this candidate.

    Drawn from the same index set as :func:`acquisition_groups`, so
    ``0 <= g(o) <= m(o)`` holds by construction rather than by clamping.
    """
    return tuple(
        group
        for group in acquisition_groups(contract, config)
        if (evidence := candidate.groups.get(group)) is not None
        and evidence.supports_candidate
    )


def coverage_q(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> float:
    """``q(o) = g(o) / m(o)`` from spec section 11.1, always in ``[0, 1]``."""
    eligible = acquisition_groups(contract, config)
    if not eligible:
        return 0.0
    return len(supporting_acquisition_groups(candidate, contract, config)) / len(eligible)


def inclusion_uncertainty(q: float) -> float:
    """``H_inc(o) = -q log q - (1-q) log(1-q)``: binary entropy of coverage.

    Zero at ``q = 0`` and ``q = 1``, maximal (``log 2``) at ``q = 0.5``.

    Read it with care. A low ``H_inc`` at ``q ≈ 0`` does **not** mean the
    candidate is confidently correct - it means the *inclusion state is
    unambiguous*, and here it is unambiguously unsupported. That is why
    :class:`CandidateState` always carries ``q`` alongside it: entropy alone
    cannot tell "nothing found it" from "everything found it".
    """
    if q <= 0.0 or q >= 1.0:
        return 0.0
    return -q * math.log(q) - (1.0 - q) * math.log(1.0 - q)


# --------------------------------------------------------------------------
# Score components (spec section 11.2)
# --------------------------------------------------------------------------


def support_term(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> float:
    """``F(o)``: independent *acquisition* support, i.e. ``q(o)``.

    Counts independence groups, never facets - slicing one mechanism into five
    facets must not read as five corroborations (spec section 7.3). And never
    raw runs: ten repeats of one view are one group, so they cannot buy
    confidence they did not earn.
    """
    return coverage_q(candidate, contract, config)


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
    odds = latest.log_odds(config.logit_epsilon)
    if odds is None:
        return 0.0
    return max(-1.0, min(1.0, odds / config.logit_clip))


def contradicting_groups(candidate: Candidate) -> tuple[IndependenceGroup, ...]:
    """Distinct *mechanisms* that explicitly contradicted the candidate.

    Mechanisms, not edges. Three INVALID verdicts from three verifier templates
    are one mechanism disagreeing three times, not three independent facts
    against the candidate - the same principle that stops ten repeats of one
    view counting as ten supports. Where templates genuinely conflict, that is
    prompt disagreement and belongs to ``U(o)``.
    """
    return tuple(
        group
        for group, evidence in candidate.groups.items()
        if evidence.contradicts_candidate
    )


def contradiction_term(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> float:
    """``C(o)``: explicit contradiction strength, normalised to ``[0, 1]``.

    Only *explicit signed* contradiction counts. None of these are
    contradictions, and none reach this term:

    * a candidate another view simply did not mention;
    * an optional view that never ran;
    * a candidate the second model did not independently recall;
    * an UNKNOWN verdict, which is an absence of evidence either way.

    The denominator is the set of mechanisms that could *possibly* contradict -
    the acquisition families plus the blind verifier - so numerator and
    denominator share one index set, exactly as ``g``/``m`` do.
    """
    denominator = max(1, len(acquisition_groups(contract, config)) + 1)
    return min(1.0, len(contradicting_groups(candidate)) / denominator)


def disagreement_term(candidate: Candidate) -> float:
    """``U(o)``: prompt-distribution disagreement ``U_prompt``, in ``[0, 1]``.

    Exactly one signal enters ``U``: the generalized JSD across verifier
    templates from spec section 10.4, which is what "prompt/view disagreement"
    in section 11.2 names.

    Verifier entropy ``H_ver`` and inclusion uncertainty ``H_inc`` are *not*
    summed in here. They measure different things on different scales, and the
    controller needs them separately - so :class:`CandidateState` exposes all
    three side by side.
    """
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

    Credited **only** for independent recall: the verifier-family model
    produced the name itself, never having been shown it. That is genuinely
    separate evidence about the world.

    Agreeing with a candidate it was *shown* earns nothing here. The proposal
    gives the verifier its own term - ``L(o)``, the calibrated log-odds - so
    paying it again through ``X`` would count one measurement twice, and the
    anchored agreement is the weaker of the two readings anyway. Spec section
    11.2 names ``X(o)`` "optional cross-model support" as a term distinct from
    the verifier's; nothing in it asks for shown-candidate agreement to appear
    in both places.
    """
    config = config or DEFAULT_SCORING
    recall = candidate.groups.get(IndependenceGroup.CROSS_MODEL_RECALL)
    if recall is None or not recall.supports_candidate:
        return 0.0
    # Defensive: only a genuinely unprompted mention is independent evidence.
    if not any(e.mode is EvidenceMode.INDEPENDENT_RECALL for e in recall.supports):
        return 0.0
    return 1.0


def score_candidate(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> CandidateScore:
    """Compute ``S(o)`` and store the component breakdown on the candidate."""
    support = support_term(candidate, contract, config)
    logit = logit_term(candidate, config)
    cross = cross_model_term(candidate, config)
    contradiction = contradiction_term(candidate, contract, config)
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
# Evidence and uncertainty state (spec section 11)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateState:
    """Everything Modules 6-8 may reason about for one candidate.

    Spec section 11: *"The controller should not make decisions from a single
    scalar confidence."* So ``score`` is only ever a summary here - the signals
    behind it stay individually readable, and three *different* uncertainties
    are kept apart rather than blended:

    ``inclusion_uncertainty``
        ``H_inc``, ambiguity of how many acquisition mechanisms found it.
    ``verifier_entropy``
        ``H_ver``, the verifier's own uncertainty across A/B/C.
    ``prompt_disagreement``
        ``U_prompt``, instability of the verdict across paraphrases.

    Collapsing them would be lossy in a way the controller cannot undo: a
    candidate that one mechanism found and the verifier is sure about is a
    different situation from one that every mechanism found and the verifier
    cannot decide, even where the two produce the same ``S(o)``.
    """

    key: str
    #: g(o) - acquisition mechanisms supporting it, and the groups themselves.
    support_groups: tuple[IndependenceGroup, ...] = ()
    #: m(o) - acquisition mechanisms capable of expressing it.
    eligible_groups: tuple[IndependenceGroup, ...] = ()
    contradicting_groups: tuple[IndependenceGroup, ...] = ()
    coverage: float = 0.0                 # q(o)
    inclusion_uncertainty: float = 0.0    # H_inc(o)
    verifier_entropy: float | None = None  # H_ver(o)
    prompt_disagreement: float | None = None  # U_prompt(o)
    verifier_label: str | None = None
    valid_prob: float | None = None
    #: Raw repetition count, kept for diagnostics only - never an input to F.
    raw_support_count: int = 0
    score: CandidateScore = field(default_factory=CandidateScore)
    status: str = ""
    tier: str = ""

    @property
    def independent_support(self) -> int:
        """``g(o)``: acquisition mechanisms, excluding verifier and cross-model."""
        return len(self.support_groups)

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "g_support": self.independent_support,
            "m_eligible": len(self.eligible_groups),
            "support_groups": [g.value for g in self.support_groups],
            "eligible_groups": [g.value for g in self.eligible_groups],
            "contradicting_groups": [g.value for g in self.contradicting_groups],
            "q_coverage": self.coverage,
            "H_inc": self.inclusion_uncertainty,
            "H_ver": self.verifier_entropy,
            "U_prompt": self.prompt_disagreement,
            "verifier_label": self.verifier_label,
            "valid_prob": self.valid_prob,
            "raw_support_count": self.raw_support_count,
            "score": self.score.to_json(),
            "status": self.status,
            "tier": self.tier,
        }


def candidate_state(
    candidate: Candidate,
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> CandidateState:
    """Assemble the full Module-5 state for one candidate.

    Reads the graph; never mutates it. Deterministic, and recomputable from
    persisted evidence alone, so it need not be serialised separately.
    """
    support = supporting_acquisition_groups(candidate, contract, config)
    eligible = acquisition_groups(contract, config)
    q = coverage_q(candidate, contract, config)
    verified = [v for v in candidate.verifications if v.valid_prob is not None]
    latest = verified[-1] if verified else None

    return CandidateState(
        key=candidate.key,
        support_groups=support,
        eligible_groups=eligible,
        contradicting_groups=contradicting_groups(candidate),
        coverage=q,
        inclusion_uncertainty=inclusion_uncertainty(q),
        verifier_entropy=latest.entropy if latest else None,
        prompt_disagreement=latest.prompt_disagreement if latest else None,
        verifier_label=latest.label.value if latest and latest.label else None,
        valid_prob=latest.valid_prob if latest else None,
        raw_support_count=candidate.raw_support_count,
        score=candidate.score_breakdown,
        status=candidate.status.value,
        tier=candidate.tier.value,
    )


def query_state(
    candidates: Sequence[Candidate],
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> dict[str, Any]:
    """Query-level evidence state for Modules 6-8.

    Reports *availability* and *support* separately, which is what Module 6
    needs to judge residual search value. It deliberately stops short of
    computing that value: no residual, no marginal yield, no next action.
    """
    eligible = acquisition_groups(contract, config)
    states = [candidate_state(c, contract, config) for c in candidates]
    supported: set[IndependenceGroup] = set()
    for state in states:
        supported.update(state.support_groups)

    return {
        "available_acquisition_groups": [g.value for g in eligible],
        "supporting_acquisition_groups": sorted(g.value for g in supported),
        "unexplored_acquisition_groups": sorted(
            g.value for g in eligible if g not in supported
        ),
        "num_candidates": len(states),
        "num_unresolved": sum(1 for s in states if s.status == CandidateStatus.UNRESOLVED.value),
        "num_contradicted": sum(1 for s in states if s.contradicting_groups),
        "max_prompt_disagreement": max(
            (s.prompt_disagreement for s in states if s.prompt_disagreement is not None),
            default=0.0,
        ),
        "candidates": [s.to_json() for s in states],
    }


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

    # 3. Broad independent support and nothing against it. Acquisition support
    #    only: a verifier that agreed with a name it was shown must not be able
    #    to lift the candidate past the threshold that decides whether it needs
    #    verifying at all.
    support = len(supporting_acquisition_groups(candidate, contract, config))
    threshold = resolve_verification(contract, config).auto_accept_support
    if support >= threshold:
        return VerificationTier.AUTO_ACCEPT

    # 4. Weak or threshold-adjacent support: worth a call. A thinly supported
    #    candidate of a near-miss-prone relation gets the adversarial template,
    #    which names the contract's near-miss classes.
    if support <= config.verify_max_support:
        if (
            config.adversarial_on_declared_near_misses
            and contract.verification.adversarial_classes
            and support <= config.adversarial_max_support
        ):
            return VerificationTier.ADVERSARIAL_VERIFY
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
            len(supporting_acquisition_groups(c, contract, config)),
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
    support = len(supporting_acquisition_groups(candidate, contract, config))
    if support < contract.selection.min_independent_support:
        return CandidateStatus.UNRESOLVED

    # The contract owns relation-specific verification semantics; the global
    # config is the default for anything it does not declare. Values are never
    # combined - a relation must be able to choose a lower acceptance bar than
    # the global default when recall matters more than precision.
    effective = resolve_verification(contract, config)

    verifications = [v for v in candidate.verifications if v.valid_prob is not None]
    if verifications:
        latest = verifications[-1]
        if latest.label is VerificationLabel.INVALID:
            return CandidateStatus.REJECTED
        if latest.label is VerificationLabel.UNKNOWN and effective.drop_on_unknown:
            # An explicit "I don't know" is not support. Fall through to the
            # score, which will only rescue a candidate that *acquisition*
            # mechanisms found broadly. Using total support here would be
            # circular: the same verifier call that returned UNKNOWN would
            # supply the support that rescues its own UNKNOWN.
            if support < effective.auto_accept_support:
                return CandidateStatus.UNRESOLVED
        if (latest.valid_prob or 0.0) < effective.min_valid_prob:
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
