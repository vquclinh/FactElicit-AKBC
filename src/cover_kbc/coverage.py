"""Module 6 - Residual Coverage & Saturation Estimator (spec section 12).

RCSE answers exactly one question:

    **is another inference action likely to add useful verified information?**

It deliberately does *not* answer "how many true objects still exist". Spec
section 12.1 removed the v1 Chao/capture-recapture estimator from the core for
two reasons that still hold: model-generated views are not independent
captures, so the capture-recapture assumptions are false; and the official
repository states that some very large or open-ended award rows have
necessarily *partial* gold, so even a perfect estimate of the real world's set
size would be the wrong target for the leaderboard.

So ``q_res`` in ``[0, 1]`` is a **need-to-continue** signal. It is not a
probability that n objects remain, not a cardinality, not a confidence that the
current answer is right, and **not a stopping decision** - Module 7 owns that.

``q(o)`` and ``q_res`` are different quantities and must never be aliased:

===========  ==========================================================
``q(o)``     *candidate*-level independent acquisition coverage
             (Module 5, spec section 11.1)
``q_res``    *query*-level remaining search need (this module)
===========  ==========================================================

A candidate can have ``q(o) = 1`` while the query still has high residual,
because a *different* object is missing.

**Three coverage axes, kept separate.** Modules 2-5 worked to keep these
distinct and this module must not re-merge them:

``mandatory_gap``
    Concrete required *views* that have not executed. Applied as a **floor** on
    ``q_res``, not as another weighted term - so missing mandatory work cannot
    be averaged away by a stable candidate set, and one missing view is never
    charged twice.
``mechanism_gap``
    Independent acquisition *mechanisms* (Module 5 independence groups) with
    nothing executed against them yet.
``facet_gap``
    Semantic *subspaces* within one mechanism (award decades, categories).
    Raises search need without ever demanding exhaustive coverage.

Every component is retained separately so a trace can show which signal drove a
decision, and each is oriented **higher = more reason to keep searching**
unless its name says otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.normalization.numeric import cluster_values
from cover_kbc.scoring import (
    DEFAULT_SCORING,
    ScoringConfig,
    acquisition_groups,
    coverage_q,
    inclusion_uncertainty,
    supporting_acquisition_groups,
)
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    IndependenceGroup,
    ProgramType,
    VerificationTier,
)


@dataclass(frozen=True)
class RCSEConfig:
    """Weights and thresholds for the residual signal. All configuration."""

    #: Window of recent actions used for the saturation statistic.
    saturation_window: int = 3
    #: Guards the marginal-yield denominator (the epsilon of spec section 12.2).
    yield_epsilon: float = 1e-9
    #: Verified yield per 1k tokens at which the yield signal saturates.
    yield_scale: float = 2.0
    #: Relative MAD at or above which a numeric cluster reads as dispersed.
    numeric_dispersion_threshold: float = 0.05
    #: Share of the dominant cluster a rival must reach to count as a genuine
    #: competitor rather than a stray parse.
    competitor_support_ratio: float = 0.5
    #: Weights of each residual component. They need not sum to 1; the result
    #: is normalised by the total weight actually applicable.
    w_yield: float = 1.0
    w_saturation: float = 1.0
    w_unresolved: float = 0.8
    w_facet_gap: float = 1.0
    w_mechanism_gap: float = 0.8
    w_disagreement: float = 0.6
    w_instability: float = 0.8
    w_inclusion: float = 0.5
    w_gate: float = 1.2
    w_competition: float = 1.0
    w_dispersion: float = 1.0
    #: Below this residual, continuing is not worth the compute. Read by
    #: Module 7; RCSE itself never stops anything.
    stop_threshold: float = 0.25

    def to_json(self) -> dict[str, float]:
        return {
            "saturation_window": self.saturation_window,
            "yield_epsilon": self.yield_epsilon,
            "yield_scale": self.yield_scale,
            "numeric_dispersion_threshold": self.numeric_dispersion_threshold,
            "competitor_support_ratio": self.competitor_support_ratio,
            "w_yield": self.w_yield,
            "w_saturation": self.w_saturation,
            "w_unresolved": self.w_unresolved,
            "w_facet_gap": self.w_facet_gap,
            "w_mechanism_gap": self.w_mechanism_gap,
            "w_disagreement": self.w_disagreement,
            "w_instability": self.w_instability,
            "w_inclusion": self.w_inclusion,
            "w_gate": self.w_gate,
            "w_competition": self.w_competition,
            "w_dispersion": self.w_dispersion,
            "stop_threshold": self.stop_threshold,
        }

    @classmethod
    def from_mapping(cls, config: Any) -> "RCSEConfig":
        if not config:
            return cls()
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(config).items() if k in fields})


DEFAULT_RCSE = RCSEConfig()


@dataclass
class ActionOutcome:
    """What one executed action yielded, for the saturation window.

    ``new_trusted`` is the increase in the *trusted set* attributable to this
    action - see :func:`trusted_keys`. It is deliberately not a count of raw
    mentions, parsed candidates, or verifier calls.
    """

    action: str
    new_trusted: int = 0
    new_candidates: int = 0
    generated_tokens: int = 0
    #: True when the action spent a verifier call rather than generating text.
    is_verification: bool = False
    #: Set when the token cost is synthetic (a scripted runtime), so a trace
    #: never presents a fabricated cost as a real measurement.
    synthetic_cost: bool = False

    @property
    def produced_value(self) -> bool:
        """Did this action increase the trusted set?

        A verification action that resolves an unresolved candidate counts,
        even though it generated no new candidate: that is real information,
        and treating it as fruitless would push the controller away from
        verification exactly when verification is what the query needs.
        """
        return self.new_trusted > 0

    def to_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "new_trusted": self.new_trusted,
            "new_candidates": self.new_candidates,
            "generated_tokens": self.generated_tokens,
            "is_verification": self.is_verification,
            "synthetic_cost": self.synthetic_cost,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ActionOutcome":
        return cls(
            action=str(payload.get("action", "")),
            new_trusted=int(payload.get("new_trusted", 0)),
            new_candidates=int(payload.get("new_candidates", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            is_verification=bool(payload.get("is_verification", False)),
            synthetic_cost=bool(payload.get("synthetic_cost", False)),
        )


@dataclass
class RCSEState:
    """Rolling record of what recent actions achieved.

    Three *separate* execution registers, one per coverage axis. Collapsing
    them would undo the facet/mechanism separation Modules 2-5 established.
    """

    outcomes: list[ActionOutcome] = field(default_factory=list)
    trusted_history: list[frozenset[str]] = field(default_factory=list)
    #: Concrete view ids that have run.
    executed_views: set[str] = field(default_factory=set)
    #: Semantic facet ids that have been covered.
    executed_facets: set[str] = field(default_factory=set)
    #: Independence groups with at least one executed action.
    executed_groups: set[IndependenceGroup] = field(default_factory=set)
    #: Identities of action *instances* already executed. Keyed on the full
    #: identity, so ``reverse(alpha)`` and ``reverse(beta)`` are tracked apart.
    #:
    #: Needed because an action's effect need not land on the candidate it was
    #: about: a reverse probe asking about `gamma` may answer "Alpha", leaving
    #: gamma without the mechanism. Keying legality on the candidate's evidence
    #: would then re-offer that identical probe forever.
    executed_actions: set[tuple[str, str, str]] = field(default_factory=set)

    def record(self, outcome: ActionOutcome) -> None:
        self.outcomes.append(outcome)

    def record_trusted(self, keys: Sequence[str]) -> None:
        self.trusted_history.append(frozenset(keys))

    @property
    def last_trusted(self) -> frozenset[str]:
        return self.trusted_history[-1] if self.trusted_history else frozenset()

    # -- component signals ---------------------------------------------------

    def marginal_yield(self, window: int, epsilon: float = 1e-9) -> float:
        """``Y_t`` from spec section 12.2: newly trusted per 1k generated tokens.

        Token-normalised so a cheap action that finds one object is not judged
        against an expensive one that finds two. When the recent window spent
        no tokens at all - a pure verification round - the ratio is undefined,
        so this reports a bounded "did it produce anything" instead of dividing
        by epsilon and returning a colossal fake yield.
        """
        recent = self.outcomes[-window:]
        if not recent:
            return 0.0
        tokens = sum(o.generated_tokens for o in recent)
        found = sum(o.new_trusted for o in recent)
        if tokens <= 0:
            return float(found > 0)
        return found / (tokens / 1000.0 + epsilon)

    def saturation(self, window: int) -> float:
        """``Sat_t`` in ``[0, 1]``: share of recent actions that added nothing.

        1.0 means the last ``window`` actions were all fruitless. This is
        *never* a stopping rule on its own: a wrong or incomplete set can be
        perfectly unproductive too, which is why the residual also carries
        coverage gaps and uncertainty.
        """
        recent = self.outcomes[-window:]
        if not recent:
            return 0.0
        return 1.0 - sum(1 for o in recent if o.produced_value) / len(recent)

    def set_stability(self) -> float:
        """Jaccard ``J_t`` between the last two trusted sets.

        High stability is useful but never sufficient on its own: a wrong or
        incomplete set can be perfectly stable. Two *empty* trusted sets are
        not treated as agreement - there is nothing to agree about, and scoring
        it 1.0 would let a query that has found nothing look settled.
        """
        if len(self.trusted_history) < 2:
            return 0.0
        a, b = self.trusted_history[-1], self.trusted_history[-2]
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)

    def consecutive_no_gain(self) -> int:
        count = 0
        for outcome in reversed(self.outcomes):
            if outcome.produced_value:
                break
            count += 1
        return count

    # -- persistence ---------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise the temporal state so Phase C can recompute ``q_res``.

        Yield and saturation cannot be recovered from the final graph - it
        records what was found, not *when* or at what cost - so the history is
        persisted rather than reconstructed.
        """
        return {
            "outcomes": [o.to_json() for o in self.outcomes],
            "trusted_history": [sorted(s) for s in self.trusted_history],
            "executed_views": sorted(self.executed_views),
            "executed_facets": sorted(self.executed_facets),
            "executed_groups": sorted(g.value for g in self.executed_groups),
            "executed_actions": sorted(list(a) for a in self.executed_actions),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> "RCSEState":
        payload = payload or {}
        return cls(
            outcomes=[ActionOutcome.from_json(o) for o in payload.get("outcomes", [])],
            trusted_history=[frozenset(s) for s in payload.get("trusted_history", [])],
            executed_views=set(payload.get("executed_views", [])),
            executed_facets=set(payload.get("executed_facets", [])),
            executed_groups={
                IndependenceGroup(g) for g in payload.get("executed_groups", [])
            },
            executed_actions={
                tuple(a) for a in payload.get("executed_actions", [])
            },
        )


@dataclass(frozen=True)
class GateState:
    """The relation's existence gate, as RCSE sees it.

    ``resolved`` false means the gate ran and was not confident either way,
    which must keep residual high rather than reading as permission to stop.
    """

    present: bool = False
    resolved: bool = False
    negative: bool = False

    def to_json(self) -> dict[str, bool]:
        return {"present": self.present, "resolved": self.resolved, "negative": self.negative}


@dataclass
class ResidualEstimate:
    """The residual signal plus every component that produced it."""

    residual: float
    components: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, float] = field(default_factory=dict)
    program_type: str = ""
    rationale: str = ""
    #: Deterministic reason codes explaining where the residual came from.
    reasons: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "residual": self.residual,
            "components": dict(self.components),
            "weights": dict(self.weights),
            "diagnostics": dict(self.diagnostics),
            "program_type": self.program_type,
            "rationale": self.rationale,
            "reasons": list(self.reasons),
        }


# --------------------------------------------------------------------------
# The trusted set
# --------------------------------------------------------------------------


def trusted_keys(
    candidates: Sequence[Candidate],
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> frozenset[str]:
    """Candidates the architecture currently trusts.

    "Trusted" means *accepted by the current evidence state*, which is broader
    than "a verifier call happened": a candidate found by enough independent
    mechanisms is auto-accepted without any Module-4 call, and ignoring it
    would make broad structural agreement invisible to yield and stability.

    Deliberately excludes rejected and unresolved candidates. Computed rather
    than stored, so it cannot drift from the evidence behind it.
    """
    from cover_kbc.scoring import decide_status

    return frozenset(
        c.key
        for c in candidates
        if c.status is not CandidateStatus.REJECTED
        and decide_status(c, contract, config) is CandidateStatus.ACCEPTED
    )


# --------------------------------------------------------------------------
# Coverage-gap signals: three distinct axes
# --------------------------------------------------------------------------


def mandatory_view_gap(contract: RelationContract, state: RCSEState) -> float:
    """Share of *mandatory* views not yet executed, in ``[0, 1]``.

    Only mandatory views. Requiring every optional view before residual could
    fall would destroy adaptive stopping - the controller exists precisely to
    skip optional work that is not worth its cost.
    """
    mandatory = set(contract.mandatory_views)
    if not mandatory:
        return 0.0
    return len(mandatory - state.executed_views) / len(mandatory)


def mechanism_gap(
    contract: RelationContract,
    state: RCSEState,
    config: ScoringConfig = DEFAULT_SCORING,
) -> float:
    """Share of *available* acquisition mechanisms with nothing executed.

    Reads Module 5's availability rule, so a mechanism that cannot run under
    this configuration - an optional family in a mandatory-only run, the
    disabled factual-decoding branch - is not counted as a gap that can never
    close. Cross-model recall is not an acquisition mechanism here: Module 5
    scores it in ``X(o)``, outside ``F``, so it never leaves a permanent hole.

    An available-but-unexecuted mechanism is *unexplored*, which is a reason to
    search. It is never negative evidence about any candidate.
    """
    available = set(acquisition_groups(contract, config))
    if not available:
        return 0.0
    return len(available - state.executed_groups) / len(available)


def semantic_facet_gap(contract: RelationContract, state: RCSEState) -> float:
    """Share of declared semantic facets not yet covered, in ``[0, 1]``.

    Facets partition *one* mechanism (award decades inside
    ``STRUCTURAL_DECOMPOSITION``), so this is a different axis from
    :func:`mechanism_gap` and is never summed with it as though the same action
    were missing twice.

    Measured over the facets the relation's views actually declare. Only
    ``awardWonBy`` partitions its structural mechanism into facets today, so
    every other relation returns 0.0 - an honest "no facet search to do" rather
    than an artificial permanent gap.
    """
    declared = declared_facets(contract)
    if not declared:
        return 0.0
    return len(declared - state.executed_facets) / len(declared)


def declared_facets(contract: RelationContract) -> set[str]:
    """Semantic facets this relation's views partition their mechanism into.

    A view without a ``facet_id`` is not a facet - it is the whole mechanism -
    so it is excluded rather than counted as its own single-member subspace,
    which would make every relation look facet-partitioned.
    """
    from cover_kbc.elicitation.library import views_for

    views = views_for(contract.relation, tuple(contract.all_views()))
    return {view.facet_id for view in views if view.facet_id}


# --------------------------------------------------------------------------
# Evidence-state signals, read from Module 5
# --------------------------------------------------------------------------


def unresolved_mass(
    candidates: Sequence[Candidate],
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> float:
    """Unresolved-candidate pressure in ``[0, 1]``, weighted by importance.

    A thin hallucinated tail should not press as hard as one genuinely
    contested candidate, so each unresolved candidate is weighted by its
    Module-5 acquisition coverage ``q(o)``: something several independent
    mechanisms found and the architecture still cannot resolve is exactly the
    case worth another action.

    A floor keeps every unresolved candidate slightly visible, so a
    single-mechanism competitor never disappears entirely.
    """
    active = [c for c in candidates if c.status is not CandidateStatus.REJECTED]
    if not active:
        return 0.0
    total = 0.0
    for candidate in active:
        unresolved = (
            candidate.status is CandidateStatus.UNRESOLVED
            or candidate.tier in (VerificationTier.VERIFY, VerificationTier.ADVERSARIAL_VERIFY)
        )
        if unresolved:
            total += max(0.25, coverage_q(candidate, contract, config))
    return min(1.0, total / len(active))


def verifier_disagreement(candidates: Sequence[Candidate]) -> float:
    """``U_prompt``: highest prompt disagreement on any candidate."""
    values = [
        v.prompt_disagreement
        for c in candidates
        for v in c.verifications
        if v.prompt_disagreement is not None
    ]
    return max(values) if values else 0.0


def mean_inclusion_uncertainty(
    candidates: Sequence[Candidate],
    contract: RelationContract,
    config: ScoringConfig = DEFAULT_SCORING,
) -> float:
    """Mean ``H_inc(o)`` over active candidates, normalised to ``[0, 1]``.

    A *different* signal from verifier disagreement and verifier entropy:
    ``H_inc`` is ambiguity about how many mechanisms found the candidate;
    ``U_prompt`` is instability of the verdict under paraphrase. Both are
    carried separately so the controller can tell them apart.
    """
    active = [c for c in candidates if c.status is not CandidateStatus.REJECTED]
    if not active:
        return 0.0
    values = [inclusion_uncertainty(coverage_q(c, contract, config)) for c in active]
    return (sum(values) / len(values)) / math.log(2)


# --------------------------------------------------------------------------
# Relation-typed diagnostics
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NumericStability:
    """Deterministic cluster diagnostics for a numeric query.

    Diagnostics only. Module 8 owns which number is finally emitted; this reads
    the same pure ``cluster_values`` primitive Module 8 uses, so the two can
    never disagree about what a cluster is.
    """

    dominant_support: int = 0
    dispersion: float = 0.0
    competitor_ratio: float = 0.0
    num_clusters: int = 0


def numeric_stability(
    candidates: Sequence[Candidate],
    contract: RelationContract,
    scoring: ScoringConfig = DEFAULT_SCORING,
) -> NumericStability:
    """Dominant-cluster support, relative dispersion, and rival strength."""
    values: list[float] = []
    for candidate in candidates:
        if candidate.status is CandidateStatus.REJECTED or candidate.numeric_value is None:
            continue
        # One value per independent acquisition mechanism, so a figure two
        # mechanisms recalled pulls its cluster harder than one seen once.
        weight = max(1, len(supporting_acquisition_groups(candidate, contract, scoring)))
        values.extend([candidate.numeric_value] * weight)

    if not values:
        return NumericStability()

    clusters = cluster_values(values, threshold=contract.selection.numeric_cluster_threshold)
    if not clusters:
        return NumericStability()

    dominant = clusters[0]
    rival = clusters[1] if len(clusters) > 1 else None
    return NumericStability(
        dominant_support=dominant.size,
        dispersion=dominant.relative_mad,
        competitor_ratio=(rival.size / dominant.size) if rival and dominant.size else 0.0,
        num_clusters=len(clusters),
    )


def locality_competition(candidates: Sequence[Candidate]) -> float:
    """How contested the single answer is, for ``NULL_SINGLE``, in ``[0, 1]``.

    Zero with no answer or exactly one; rising as a second candidate approaches
    the leader's score. One stable locality with no rival is the settled case;
    two plausible localities must keep residual high.
    """
    active = sorted(
        (c for c in candidates if c.status is not CandidateStatus.REJECTED),
        key=lambda c: (-c.score, c.key),
    )
    if len(active) < 2:
        return 0.0
    best, rival = active[0], active[1]
    if best.score <= 0.0:
        return 1.0 if rival.score > 0.0 else 0.0
    return max(0.0, min(1.0, rival.score / best.score))


# --------------------------------------------------------------------------
# The residual itself (spec section 12.3)
# --------------------------------------------------------------------------


def estimate_residual(
    contract: RelationContract,
    candidates: Sequence[Candidate],
    state: RCSEState,
    config: RCSEConfig = DEFAULT_RCSE,
    *,
    gate: GateState | None = None,
    scoring: ScoringConfig = DEFAULT_SCORING,
) -> ResidualEstimate:
    """Compute ``q_res`` for one query, relation-typed (spec section 12.3).

    Each programme weights the signals differently, because "more search would
    help" means something different for a numeric scalar and an open-ended
    award list. The primitives are shared; only their interpretation is typed.

    ``mandatory_gap`` is applied as a **floor** rather than as another weighted
    term. That way incomplete mandatory acquisition can never be averaged away
    by a stable candidate set, and a missing mandatory view is not also charged
    as a mechanism gap.
    """
    window = config.saturation_window
    gate = gate or GateState()

    # -- shared primitives ---------------------------------------------------
    saturation = state.saturation(window)
    stability = state.set_stability()
    instability = 1.0 - stability
    raw_yield = state.marginal_yield(window, config.yield_epsilon)
    yield_signal = min(1.0, raw_yield / config.yield_scale) if config.yield_scale > 0 else 0.0
    unresolved = unresolved_mass(candidates, contract, scoring)
    disagreement = verifier_disagreement(candidates)
    inclusion = mean_inclusion_uncertainty(candidates, contract, scoring)
    mandatory_gap = mandatory_view_gap(contract, state)
    mech_gap = mechanism_gap(contract, state, scoring)
    facet_gap = semantic_facet_gap(contract, state)

    components = {
        "mandatory_gap": mandatory_gap,
        "mechanism_gap": mech_gap,
        "facet_gap": facet_gap,
        "unresolved_mass": unresolved,
        "marginal_yield": yield_signal,
        "unsaturated": 1.0 - saturation,
        "set_instability": instability,
        "verifier_disagreement": disagreement,
        "inclusion_uncertainty": inclusion,
    }
    diagnostics = {
        "saturation": saturation,
        "set_stability": stability,
        "raw_marginal_yield_per_1k_tokens": raw_yield,
        "consecutive_no_gain": float(state.consecutive_no_gain()),
        "num_actions": float(len(state.outcomes)),
        "trusted_set_size": float(len(state.last_trusted)),
    }

    program = contract.program_type

    if program is ProgramType.LARGE_OPEN_SET:
        # Awards: yield decay, facet coverage and the unresolved tail dominate.
        # Partial gold makes an unconstrained tail dangerous, so saturation is
        # the main permission to stop.
        terms = {
            "marginal_yield": (config.w_yield, yield_signal),
            "facet_gap": (config.w_facet_gap, facet_gap),
            "mechanism_gap": (config.w_mechanism_gap, mech_gap),
            "unresolved_mass": (config.w_unresolved * 0.5, unresolved),
            "unsaturated": (config.w_saturation, 1.0 - saturation),
        }
        # Nothing here is individually decisive: awards stop on the *balance*
        # of yield decay and coverage, never on one signal.
        blocking: tuple[str, ...] = ()
        rationale = "large-open-set: yield decay, facet coverage and tail drive continuation"
    elif program is ProgramType.NUMERIC:
        # Numeric: one scalar. Only cluster instability, dispersion or
        # disagreement justify more calls (spec table 6).
        numeric = numeric_stability(candidates, contract, scoring)
        dispersion = (
            min(1.0, numeric.dispersion / config.numeric_dispersion_threshold)
            if config.numeric_dispersion_threshold > 0
            else 0.0
        )
        competition = (
            min(1.0, numeric.competitor_ratio / config.competitor_support_ratio)
            if config.competitor_support_ratio > 0
            else float(numeric.competitor_ratio > 0)
        )
        components["numeric_dispersion"] = dispersion
        components["cluster_competition"] = competition
        diagnostics["dominant_cluster_support"] = float(numeric.dominant_support)
        diagnostics["relative_mad"] = numeric.dispersion
        diagnostics["num_clusters"] = float(numeric.num_clusters)
        terms = {
            "numeric_dispersion": (config.w_dispersion, dispersion),
            "cluster_competition": (config.w_competition, competition),
            "mechanism_gap": (config.w_mechanism_gap, mech_gap),
            "verifier_disagreement": (config.w_disagreement, disagreement),
        }
        # A genuine rival cluster means the answer is not determined, whatever
        # the other signals say (spec table 6: "dominant cluster stable").
        blocking = ("cluster_competition",)
        rationale = "numeric: continue only while the dominant cluster is unstable"
    elif program is ProgramType.NULL_SINGLE:
        # Null/single: the question is whether existence and the one locality
        # are resolved, not how much more could be found.
        gate_unresolved = 1.0 if (gate.present and not gate.resolved) else 0.0
        competition = locality_competition(candidates)
        components["gate_unresolved"] = gate_unresolved
        components["locality_competition"] = competition
        diagnostics["gate_present"] = float(gate.present)
        diagnostics["gate_negative"] = float(gate.negative)
        if gate.present and gate.resolved and gate.negative:
            # A confident negative is an answer, not an unfinished search.
            terms = {"gate_unresolved": (config.w_gate, 0.0)}
            blocking = ()
            rationale = "null-single: confident negative gate, no candidate search needed"
        else:
            terms = {
                "gate_unresolved": (config.w_gate, gate_unresolved),
                "locality_competition": (config.w_competition, competition),
                "unresolved_mass": (config.w_unresolved, unresolved),
                "mechanism_gap": (config.w_mechanism_gap, mech_gap),
                "verifier_disagreement": (config.w_disagreement, disagreement),
            }
            # Either an undecided gate or a real rival locality is on its own
            # enough to keep searching (spec table 6).
            blocking = ("gate_unresolved", "locality_competition")
            rationale = "null-single: continue while existence or locality is unresolved"
    else:  # SMALL_SET
        terms = {
            "mechanism_gap": (config.w_mechanism_gap, mech_gap),
            "unresolved_mass": (config.w_unresolved, unresolved),
            "set_instability": (config.w_instability, instability),
            "marginal_yield": (config.w_yield * 0.5, yield_signal),
            "inclusion_uncertainty": (config.w_inclusion, inclusion),
        }
        # Small sets should finish cheaply; only mandatory work blocks.
        blocking = ()
        rationale = "small-set: complete mandatory structure, then stop early"

    total_weight = sum(w for w, _ in terms.values())
    weighted = sum(w * v for w, v in terms.values()) / total_weight if total_weight else 0.0

    # Floors, not averages. A weighted mean dilutes a single decisive signal
    # against its zero-valued siblings - two competing death localities scored
    # 0.94 average down to 0.21 and read as "settled" - so the signals that are
    # on their own a sufficient reason to keep searching are applied as lower
    # bounds instead. `blocking` is typed: what makes a query unfinished
    # differs by programme.
    floors = [mandatory_gap, *(components.get(name, 0.0) for name in blocking)]
    residual = max(0.0, min(1.0, max(weighted, *floors)))

    return ResidualEstimate(
        residual=residual,
        components=components,
        weights={k: w for k, (w, _) in terms.items()},
        diagnostics=diagnostics,
        program_type=program.value,
        rationale=rationale,
        reasons=_reason_codes(components, mandatory_gap, weighted, program),
    )


def _reason_codes(
    components: dict[str, float],
    mandatory_gap: float,
    weighted: float,
    program: ProgramType,
) -> list[str]:
    """Deterministic explanations of why the residual sits where it does."""
    reasons: list[str] = []
    if mandatory_gap > 0.0:
        reasons.append("mandatory_views_incomplete")
        if mandatory_gap >= weighted:
            reasons.append("residual_floored_by_mandatory_gap")
    if components.get("mechanism_gap", 0.0) > 0.0:
        reasons.append("acquisition_mechanism_unexplored")
    if components.get("facet_gap", 0.0) > 0.0 and program is ProgramType.LARGE_OPEN_SET:
        reasons.append("semantic_facet_unexplored")
    if components.get("unresolved_mass", 0.0) > 0.0:
        reasons.append("unresolved_candidates_remain")
    if components.get("marginal_yield", 0.0) > 0.0:
        reasons.append("recent_actions_still_yielding")
    if components.get("unsaturated", 1.0) <= 0.0:
        reasons.append("recent_actions_saturated")
    if components.get("verifier_disagreement", 0.0) > 0.0:
        reasons.append("verifier_templates_disagree")
    if components.get("gate_unresolved", 0.0) > 0.0:
        reasons.append("existence_gate_unresolved")
    if components.get("locality_competition", 0.0) > 0.0:
        reasons.append("competing_localities")
    if components.get("cluster_competition", 0.0) > 0.0:
        reasons.append("competing_numeric_clusters")
    if components.get("numeric_dispersion", 0.0) > 0.0:
        reasons.append("numeric_cluster_dispersed")
    return reasons or ["state_settled"]
