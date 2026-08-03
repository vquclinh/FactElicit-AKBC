"""Module 6 - Residual Coverage & Saturation Estimator (RCSE).

RCSE answers **"is another inference action likely to add useful verified
information?"** It deliberately does *not* answer "how many true objects still
exist in the world".

That distinction is the whole point of replacing the v1 Chao estimator. Two
reasons it cannot be a cardinality estimate:

* model-generated views are not independent captures, so capture-recapture
  assumptions do not hold;
* the official repository notes that some very large or open-ended award rows
  necessarily have **partial gold**, so even a perfect estimate of the real
  world's set size would be the wrong target for the leaderboard.

So ``q_res`` in ``[0, 1]`` is a *need-to-continue* signal, not a probability
that n objects remain. Every component is retained separately so a trace can
show which signal drove a decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    ProgramType,
    VerificationTier,
)


@dataclass(frozen=True)
class RCSEConfig:
    """Weights and thresholds for the residual signal. All configuration."""

    #: Window of recent actions used for the saturation statistic.
    saturation_window: int = 3
    #: Weights of each residual component. They need not sum to 1; the result
    #: is normalised by the total weight actually applicable.
    w_yield: float = 1.0
    w_saturation: float = 1.0
    w_unresolved: float = 0.8
    w_facet_gap: float = 1.0
    w_disagreement: float = 0.6
    w_instability: float = 0.8
    #: Below this residual, continuing is not worth the compute.
    stop_threshold: float = 0.25

    def to_json(self) -> dict[str, float]:
        return {
            "saturation_window": self.saturation_window,
            "w_yield": self.w_yield,
            "w_saturation": self.w_saturation,
            "w_unresolved": self.w_unresolved,
            "w_facet_gap": self.w_facet_gap,
            "w_disagreement": self.w_disagreement,
            "w_instability": self.w_instability,
            "stop_threshold": self.stop_threshold,
        }


DEFAULT_RCSE = RCSEConfig()


@dataclass
class ActionOutcome:
    """What one executed action yielded, for the saturation window."""

    action: str
    new_verified: int = 0
    new_candidates: int = 0
    generated_tokens: int = 0

    @property
    def produced_value(self) -> bool:
        return self.new_verified > 0


@dataclass
class RCSEState:
    """Rolling record of what recent actions achieved."""

    outcomes: list[ActionOutcome] = field(default_factory=list)
    accepted_history: list[frozenset[str]] = field(default_factory=list)
    covered_facets: set[str] = field(default_factory=set)

    def record(self, outcome: ActionOutcome) -> None:
        self.outcomes.append(outcome)

    def record_accepted(self, keys: Sequence[str]) -> None:
        self.accepted_history.append(frozenset(keys))

    # -- component signals ---------------------------------------------------

    def marginal_yield(self, window: int) -> float:
        """Verified candidates per 1k generated tokens over the recent window.

        ``Y_t(a)`` from spec section 12.2, aggregated. Token-normalised so a
        cheap action that finds one object is not judged against an expensive
        one that finds two.
        """
        recent = self.outcomes[-window:]
        if not recent:
            return 0.0
        tokens = sum(o.generated_tokens for o in recent)
        found = sum(o.new_verified for o in recent)
        if tokens <= 0:
            return float(found > 0)
        return found / (tokens / 1000.0)

    def saturation(self, window: int) -> float:
        """``Sat_t`` in ``[0, 1]``: share of recent actions that added nothing.

        1.0 means the last ``window`` actions were all fruitless.
        """
        recent = self.outcomes[-window:]
        if not recent:
            return 0.0
        return 1.0 - sum(1 for o in recent if o.produced_value) / len(recent)

    def set_stability(self) -> float:
        """Jaccard ``J_t`` between the last two accepted sets.

        High stability is useful but never sufficient on its own: a wrong or
        incomplete set can be perfectly stable (spec section 13.3).
        """
        if len(self.accepted_history) < 2:
            return 0.0
        a, b = self.accepted_history[-1], self.accepted_history[-2]
        if not a and not b:
            return 1.0
        union = a | b
        return len(a & b) / len(union) if union else 1.0

    def consecutive_no_gain(self) -> int:
        count = 0
        for outcome in reversed(self.outcomes):
            if outcome.produced_value:
                break
            count += 1
        return count


@dataclass
class ResidualEstimate:
    """The residual signal plus every component that produced it."""

    residual: float
    components: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    program_type: str = ""
    rationale: str = ""

    @property
    def should_continue(self) -> bool:
        return self.residual >= 0.0  # threshold applied by the controller

    def to_json(self) -> dict[str, Any]:
        return {
            "residual": self.residual,
            "components": dict(self.components),
            "weights": dict(self.weights),
            "program_type": self.program_type,
            "rationale": self.rationale,
        }


def _unresolved_mass(candidates: Sequence[Candidate]) -> float:
    """Share of active candidates still unresolved."""
    active = [c for c in candidates if c.status is not CandidateStatus.REJECTED]
    if not active:
        return 0.0
    unresolved = sum(
        1
        for c in active
        if c.status is CandidateStatus.UNRESOLVED
        or c.tier in (VerificationTier.VERIFY, VerificationTier.ADVERSARIAL_VERIFY)
    )
    return unresolved / len(active)


def _verifier_disagreement(candidates: Sequence[Candidate]) -> float:
    """Highest prompt disagreement seen on any candidate."""
    values = [
        v.prompt_disagreement
        for c in candidates
        for v in c.verifications
        if v.prompt_disagreement is not None
    ]
    return max(values) if values else 0.0


def _facet_gap(contract: RelationContract, state: RCSEState) -> float:
    """Share of the contract's declared views not yet executed."""
    declared = set(contract.all_views())
    if not declared:
        return 0.0
    missing = declared - state.covered_facets
    return len(missing) / len(declared)


def estimate_residual(
    contract: RelationContract,
    candidates: Sequence[Candidate],
    state: RCSEState,
    config: RCSEConfig = DEFAULT_RCSE,
) -> ResidualEstimate:
    """Compute ``q_res`` for one query, relation-typed (spec section 12.3).

    Each programme weights the signals differently, because "more search would
    help" means different things for a numeric scalar and an open-ended award
    list.
    """
    window = config.saturation_window
    saturation = state.saturation(window)
    unresolved = _unresolved_mass(candidates)
    disagreement = _verifier_disagreement(candidates)
    facet_gap = _facet_gap(contract, state)
    stability = state.set_stability()
    instability = 1.0 - stability
    raw_yield = state.marginal_yield(window)
    # Squash yield into [0, 1]: any positive verified yield is a strong reason
    # to keep going, but the magnitude saturates quickly.
    yield_signal = min(1.0, raw_yield / 2.0)

    components = {
        "marginal_yield": yield_signal,
        "saturation": saturation,
        "unresolved_mass": unresolved,
        "facet_gap": facet_gap,
        "verifier_disagreement": disagreement,
        "set_instability": instability,
        "raw_marginal_yield_per_1k_tokens": raw_yield,
        "set_stability": stability,
        "consecutive_no_gain": float(state.consecutive_no_gain()),
    }

    program = contract.program_type
    if program is ProgramType.LARGE_OPEN_SET:
        # Awards: yield and facet coverage dominate. Saturation is the main
        # stop signal, and partial gold makes an unconstrained tail dangerous.
        terms = {
            "marginal_yield": (config.w_yield, yield_signal),
            "facet_gap": (config.w_facet_gap, facet_gap),
            "unresolved_mass": (config.w_unresolved * 0.5, unresolved),
            "saturation": (config.w_saturation, 1.0 - saturation),
        }
        rationale = "large-open-set: yield and facet coverage drive continuation"
    elif program is ProgramType.NUMERIC:
        # Numeric: one scalar. Only dispersion/disagreement justify more calls.
        terms = {
            "facet_gap": (config.w_facet_gap, facet_gap),
            "set_instability": (config.w_instability, instability),
            "verifier_disagreement": (config.w_disagreement, disagreement),
        }
        rationale = "numeric: continue only while the dominant cluster is unstable"
    elif program is ProgramType.NULL_SINGLE:
        # Null/single: the question is whether the existence gate and the one
        # locality are resolved, not how much more could be found.
        terms = {
            "unresolved_mass": (config.w_unresolved, unresolved),
            "facet_gap": (config.w_facet_gap, facet_gap),
            "verifier_disagreement": (config.w_disagreement, disagreement),
        }
        rationale = "null-single: continue only while existence or locality is unresolved"
    else:  # SMALL_SET
        terms = {
            "facet_gap": (config.w_facet_gap, facet_gap),
            "unresolved_mass": (config.w_unresolved, unresolved),
            "set_instability": (config.w_instability, instability),
        }
        rationale = "small-set: complete mandatory structure, then stop early"

    total_weight = sum(w for w, _ in terms.values())
    residual = (
        sum(w * v for w, v in terms.values()) / total_weight if total_weight else 0.0
    )

    return ResidualEstimate(
        residual=max(0.0, min(1.0, residual)),
        components=components,
        weights={k: w for k, (w, _) in terms.items()},
        program_type=program.value,
        rationale=rationale,
    )
