"""Module 7 - the deterministic active controller and adaptive stopping.

The controller chooses what to do next from the evidence state. It is a
**rule-based, non-neural policy**: no RL, no learned scoring, no trained
component (spec section 13). That is both a rules-compliance requirement and a
reproducibility one - a deterministic policy replays exactly.

Action score (spec section 13.1)::

    A_t(a) = a*Yhat_t(a) + b*G_t(a) + c*U_t(a) - l*C(a) - r*D_t(a)

Every decision is logged with the state before, the estimated benefit, the
estimated cost, the redundancy penalty, and the resulting state - so a run can
be explained after the fact rather than merely reproduced.

Stopping is **relation-typed**, not one global formula: "we have searched
enough" means something different for a single scalar and for an open-ended
award list. A hard budget always overrides continuation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.elicitation.library import get_view
from cover_kbc.coverage import (
    DEFAULT_RCSE,
    ActionOutcome,
    GateState,
    RCSEConfig,
    RCSEState,
    ResidualEstimate,
    estimate_residual,
)
from cover_kbc.scoring import DEFAULT_SCORING, ScoringConfig, coverage_q
from cover_kbc.types import (
    Budget,
    Candidate,
    CandidateStatus,
    IndependenceGroup,
    ModelRole,
    ProgramType,
    VerificationTier,
)


class ActionType(str, Enum):
    """The controller's action space."""

    RUN_VIEW = "RUN_VIEW"
    RUN_FACET = "RUN_FACET"
    VERIFY = "VERIFY"
    ADVERSARIAL_VERIFY = "ADVERSARIAL_VERIFY"
    #: Candidate-conditioned reverse/alternate framing (Module 2's
    #: ``run_reverse_view``). An *acquisition* mechanism that happens to take a
    #: candidate as input - not verification, no label scoring.
    REVERSE_CHECK = "REVERSE_CHECK"
    CROSS_MODEL_CHECK = "CROSS_MODEL_CHECK"
    RESAMPLE = "RESAMPLE"
    STOP = "STOP"


#: Which runtime each action family requires. Declared here rather than
#: inferred at execution time, so staged orchestration routes an action to the
#: right model instead of silently substituting whichever happens to be
#: resident. The gate is scored by the
#: verifier role too (see ``score_gate``), so it is never a residency accident.
ACTION_ROLE: dict["ActionType", ModelRole] = {
    ActionType.RUN_VIEW: ModelRole.ENUMERATOR,
    ActionType.RUN_FACET: ModelRole.ENUMERATOR,
    ActionType.RESAMPLE: ModelRole.ENUMERATOR,
    ActionType.REVERSE_CHECK: ModelRole.ENUMERATOR,
    ActionType.VERIFY: ModelRole.VERIFIER,
    ActionType.ADVERSARIAL_VERIFY: ModelRole.VERIFIER,
    ActionType.CROSS_MODEL_CHECK: ModelRole.VERIFIER,
    ActionType.STOP: ModelRole.NONE,
}


@dataclass(frozen=True)
class Action:
    """One executable step, with the arguments needed to run it."""

    action_type: ActionType
    view_id: str = ""
    facet_id: str = ""
    candidate_key: str = ""
    reason: str = ""
    estimated_cost: float = 1.0

    @property
    def model_role(self) -> ModelRole:
        """Which runtime must execute this action."""
        return ACTION_ROLE.get(self.action_type, ModelRole.NONE)

    @property
    def identity(self) -> tuple[str, str, str]:
        """Stable identity including arguments.

        ``reverse(A)`` and ``reverse(B)`` are different actions, and so are
        ``verify(A)`` and ``verify(B)``. Deduplication and redundancy must key
        on this, never on the action type alone.
        """
        return (self.action_type.value, self.view_id, self.candidate_key)

    def to_json(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "view_id": self.view_id,
            "facet_id": self.facet_id,
            "candidate_key": self.candidate_key,
            "reason": self.reason,
            "estimated_cost": self.estimated_cost,
            "model_role": self.model_role.value,
        }


@dataclass(frozen=True)
class ControllerConfig:
    """Weights and thresholds for the action score and stopping rules."""

    # -- action cost priors C(a) ---------------------------------------------
    #: Estimated cost of each action family, in logical model calls. Priors,
    #: not measurements: they are what the controller compares before acting,
    #: and are calibratable against logged costs once Colab runs exist.
    cost_run_view: float = 1.0
    cost_reverse_check: float = 1.0
    cost_resample: float = 1.0
    cost_verify: float = 1.0
    #: Adversarial verification runs several templates plus a control.
    cost_adversarial_verify: float = 2.0
    cost_cross_model: float = 1.5

    # -- action-score component constants ------------------------------------
    #: Yield estimate for a mechanism nothing is yet known about. Optimistic on
    #: purpose: an untried mechanism is what the controller most wants to try.
    untried_yield_prior: float = 0.5
    #: G_t for a mandatory view that has not run - the strongest gap there is.
    mandatory_gap_relevance: float = 1.0
    #: Optional gaps are scaled below the mandatory value, so spec section
    #: 13.2 step 1 ("run each mandatory initial view once") holds structurally
    #: rather than by luck of which residual happens to be larger.
    optional_gap_scale: float = 0.8
    #: D_t for a view whose independence group is already covered.
    covered_mechanism_redundancy: float = 0.5
    #: D_t floor for an explicit repeat, plus its per-repeat increment.
    resample_redundancy: float = 0.8
    repeat_redundancy_step: float = 0.1
    #: How much an acquisition action counts toward resolving existing
    #: uncertainty - real but indirect, so well below a verification.
    indirect_uncertainty: float = 0.5
    #: Extra U_t for the adversarial template over the ordinary one.
    adversarial_uncertainty_bonus: float = 0.2
    #: D_t added per verification a candidate already received.
    reverify_redundancy: float = 0.5

    # -- action score A_t(a) -------------------------------------------------
    alpha_yield: float = 1.0
    beta_gap: float = 1.0
    gamma_uncertainty: float = 0.8
    lambda_cost: float = 0.15
    rho_redundancy: float = 1.0

    # -- stopping ------------------------------------------------------------
    #: Residual below which the controller stops regardless of budget left.
    residual_stop: float = 0.25
    #: Consecutive zero-yield actions tolerated before stopping.
    saturation_patience: int = 2
    #: Jaccard stability at or above which a set counts as stable.
    stability_threshold: float = 1.0
    #: Verify before exploring further once this many candidates are unresolved.
    verify_first_unresolved: float = 0.5
    #: Score added to a verification action while that preference is in force.
    verify_first_bonus: float = 0.5
    #: Take stopping thresholds from the relation contract when it declares
    #: them. Spec section 12.3 makes stopping relation-typed; the fields below
    #: are the cross-relation fallback, not the authority.
    honor_contract_stopping: bool = True

    rcse: RCSEConfig = field(default_factory=lambda: DEFAULT_RCSE)

    @classmethod
    def from_mapping(cls, config: dict[str, Any] | None) -> "ControllerConfig":
        config = dict(config or {})
        rcse_cfg = config.pop("rcse", None)
        rcse = RCSEConfig(**rcse_cfg) if rcse_cfg else DEFAULT_RCSE
        fields = set(cls.__dataclass_fields__) - {"rcse"}
        return cls(rcse=rcse, **{k: v for k, v in config.items() if k in fields})

    def to_json(self) -> dict[str, Any]:
        return {
            "alpha_yield": self.alpha_yield,
            "beta_gap": self.beta_gap,
            "gamma_uncertainty": self.gamma_uncertainty,
            "lambda_cost": self.lambda_cost,
            "rho_redundancy": self.rho_redundancy,
            "residual_stop": self.residual_stop,
            "saturation_patience": self.saturation_patience,
            "stability_threshold": self.stability_threshold,
            "verify_first_unresolved": self.verify_first_unresolved,
            "honor_contract_stopping": self.honor_contract_stopping,
            "rcse": self.rcse.to_json(),
        }


DEFAULT_CONTROLLER = ControllerConfig()


@dataclass(frozen=True)
class EffectiveStopping:
    """Stopping thresholds actually in force for one relation.

    Resolved from the contract's :class:`~cover_kbc.contracts.base.StoppingPolicy`
    when ``honor_contract_stopping`` is set, otherwise from the global config.
    Kept explicit so a decision log can show which numbers were applied.
    """

    stability_threshold: float
    saturation_patience: int
    residual_stop: float
    source: str

    def to_json(self) -> dict[str, Any]:
        return {
            "stability_threshold": self.stability_threshold,
            "saturation_patience": self.saturation_patience,
            "residual_stop": self.residual_stop,
            "source": self.source,
        }


def resolve_stopping(
    contract: RelationContract, config: ControllerConfig = DEFAULT_CONTROLLER
) -> EffectiveStopping:
    """Which stopping thresholds apply to this relation, and where they came from."""
    if config.honor_contract_stopping:
        return EffectiveStopping(
            stability_threshold=contract.stopping.stability_threshold,
            saturation_patience=contract.stopping.saturation_patience,
            residual_stop=contract.stopping.residual_stop_threshold,
            source=f"contract:{contract.relation}",
        )
    return EffectiveStopping(
        stability_threshold=config.stability_threshold,
        saturation_patience=config.saturation_patience,
        residual_stop=config.residual_stop,
        source="controller_config",
    )


@dataclass
class ActionDecision:
    """A full audit record of one controller decision."""

    step: int
    chosen: Action
    score: float
    residual: ResidualEstimate
    considered: list[dict[str, Any]] = field(default_factory=list)
    state_before: dict[str, Any] = field(default_factory=dict)
    state_after: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "chosen": self.chosen.to_json(),
            "score": self.score,
            "residual": self.residual.to_json(),
            "considered": self.considered,
            "state_before": self.state_before,
            "state_after": self.state_after,
        }


def snapshot_state(
    candidates: Sequence[Candidate], budget: Budget, state: RCSEState
) -> dict[str, Any]:
    """Compact controller-state summary for the decision log."""
    accepted = [c for c in candidates if c.status is CandidateStatus.ACCEPTED]
    unresolved = [c for c in candidates if c.status is CandidateStatus.UNRESOLVED]
    return {
        "num_candidates": len(candidates),
        "num_accepted": len(accepted),
        "num_unresolved": len(unresolved),
        "calls_used": budget.calls_used,
        "calls_left": budget.calls_left,
        "tokens_used": budget.generated_tokens_used,
        "tokens_left": budget.tokens_left,
        "covered_views": sorted(state.executed_views),
        "consecutive_no_gain": state.consecutive_no_gain(),
    }


# --------------------------------------------------------------------------
# Action enumeration and scoring
# --------------------------------------------------------------------------


def legal_actions(
    contract: RelationContract,
    candidates: Sequence[Candidate],
    state: RCSEState,
    budget: Budget,
    *,
    cross_model_available: bool = False,
    config: ControllerConfig = DEFAULT_CONTROLLER,
    allowed_roles: "frozenset[ModelRole] | None" = None,
) -> list[Action]:
    """Enumerate what the controller may legally do next.

    Legality is a property of *state*, never of the action name: a view already
    run is not offered, a candidate that already carries the reverse mechanism
    is not reverse-checked again, and resampling only becomes legal once no
    unexplored structural mechanism remains.
    """
    if budget.exhausted:
        return [Action(ActionType.STOP, reason="hard budget exhausted")]

    actions: list[Action] = []
    active = [c for c in candidates if c.status is not CandidateStatus.REJECTED]

    for view_id in contract.all_views():
        view = get_view(contract.relation, view_id)
        if view.is_reverse:
            # Candidate-conditioned: enumerated per candidate below, not as a
            # subject-only action, because it needs a candidate argument.
            continue
        if view_id in state.executed_views:
            continue
        mandatory = view_id in contract.mandatory_views
        actions.append(
            Action(
                ActionType.RUN_VIEW if mandatory else ActionType.RUN_FACET,
                view_id=view_id,
                facet_id=view.facet_id,
                reason="mandatory view not yet run" if mandatory else "unexplored optional facet",
                estimated_cost=config.cost_run_view,
            )
        )

    # Candidate-conditioned reverse/alternate acquisition. Offered per
    # candidate, and only where that candidate does not already carry the
    # mechanism - so running it on one candidate never makes another look
    # reverse-checked.
    for view_id in contract.all_views():
        view = get_view(contract.relation, view_id)
        if not view.is_reverse:
            continue
        for candidate in active:
            group = view.independence_group
            already = candidate.groups.get(group)
            if already is not None and already.supports_candidate:
                continue
            actions.append(
                Action(
                    ActionType.REVERSE_CHECK,
                    view_id=view_id,
                    facet_id=view.facet_id,
                    candidate_key=candidate.key,
                    reason="candidate lacks independent reverse evidence",
                    estimated_cost=config.cost_reverse_check,
                )
            )

    # Repeating an already-executed acquisition mechanism. Legal only once the
    # structural frontier is exhausted: spec section 7 makes repeated sampling
    # subordinate to structural diversity, so it must never be an alternative
    # to a mechanism nobody has tried yet.
    structural_remaining = any(
        a.action_type in (ActionType.RUN_VIEW, ActionType.RUN_FACET) for a in actions
    )
    if not structural_remaining:
        for view_id in sorted(state.executed_views):
            if view_id not in contract.all_views():
                continue
            view = get_view(contract.relation, view_id)
            if view.is_gate or view.is_reverse:
                continue
            if view.decode.deterministic:
                # A greedy view re-run with the same prompt returns the same
                # text, so the repeat carries no information and Module 3 would
                # reject its duplicate edge. Only a *sampled* view can yield
                # something new, which is what spec section 7 means by
                # repeated sampling being subordinate to structural diversity.
                continue
            actions.append(
                Action(
                    ActionType.RESAMPLE,
                    view_id=view_id,
                    facet_id=view.facet_id,
                    reason="no structural gap remains; repeat sampling for marginal evidence",
                    estimated_cost=config.cost_resample,
                )
            )

    for candidate in candidates:
        if candidate.status is CandidateStatus.REJECTED:
            # A hard contract violation is never worth a verifier call.
            continue
        if candidate.verifications:
            # Re-verifying with unchanged inputs would return the same verdict
            # and produce an identical evidence edge, which Module 3 rejects.
            # A repeat only becomes meaningful once new evidence arrives.
            continue
        if candidate.tier is VerificationTier.ADVERSARIAL_VERIFY:
            actions.append(
                Action(
                    ActionType.ADVERSARIAL_VERIFY,
                    candidate_key=candidate.key,
                    reason="contradiction or high prompt disagreement",
                    estimated_cost=config.cost_adversarial_verify,
                )
            )
        elif candidate.tier is VerificationTier.VERIFY:
            actions.append(
                Action(
                    ActionType.VERIFY,
                    candidate_key=candidate.key,
                    reason="weak or threshold-adjacent support",
                    estimated_cost=config.cost_verify,
                )
            )

    if cross_model_available and candidates:
        actions.append(
            Action(
                ActionType.CROSS_MODEL_CHECK,
                reason="independent recall from the second model family",
                estimated_cost=1.5,
            )
        )

    # An action instance runs at most once. RESAMPLE is exempt: repetition is
    # its entire purpose, and its own legality guard (a stochastic view) plus a
    # growing redundancy penalty bound it.
    actions = [
        a for a in actions
        if a.action_type is ActionType.RESAMPLE
        or a.identity not in state.executed_actions
    ]

    if allowed_roles is not None:
        # Staged execution: only actions whose model role is resident in this
        # phase are executable here. Filtering at enumeration keeps the
        # controller from choosing something it cannot run.
        actions = [a for a in actions if a.model_role in allowed_roles]

    actions.append(Action(ActionType.STOP, reason="no further action scored above stopping"))
    return actions


def mechanism_yield_prior(
    action: Action,
    contract: RelationContract,
    state: RCSEState,
    config: ControllerConfig = DEFAULT_CONTROLLER,
) -> float:
    """``Yhat_t(a)``: expected marginal *trusted* yield, in ``[0, 1]``.

    An **estimate for a future action**, deliberately distinct from Module 6's
    ``new_trusted``, which is an *observed past* outcome. The estimate is
    deterministic and uses only run history:

    * an untried mechanism gets the optimistic prior - it is exactly what the
      architecture has not learned anything about yet;
    * a mechanism already run is estimated from how productive its own past
      runs were, so a repeatedly fruitless view is not offered as though it
      were fresh.

    Verification history is excluded: that a verifier call resolved a candidate
    says nothing about whether another discovery view will generate one.
    """
    if not action.view_id:
        return config.untried_yield_prior
    runs = [
        o for o in state.outcomes
        if not o.is_verification and o.action != ActionType.STOP.value
    ]
    if action.view_id not in state.executed_views or not runs:
        return config.untried_yield_prior
    productive = sum(1 for o in runs if o.produced_value)
    return productive / len(runs)


def view_gap_relevance(
    action: Action,
    contract: RelationContract,
    state: RCSEState,
    residual: ResidualEstimate,
    config: ControllerConfig = DEFAULT_CONTROLLER,
) -> float:
    """``G_t(a)`` for an acquisition action, in ``[0, 1]``.

    Priority comes from what the *contract and state* say is missing, never
    from the action's enum member. A mandatory view that has not run is the
    highest-relevance acquisition action there is; an optional facet inherits
    the relation's current facet residual; a resample addresses no gap at all,
    because by construction it repeats a mechanism already covered.
    """
    if action.action_type is ActionType.RESAMPLE:
        return 0.0
    if action.view_id in contract.mandatory_views and action.view_id not in state.executed_views:
        return config.mandatory_gap_relevance
    if action.facet_id:
        return residual.components.get("facet_gap", 0.0) * config.optional_gap_scale
    return residual.components.get("mechanism_gap", 0.0) * config.optional_gap_scale


def mechanism_redundancy(
    action: Action,
    contract: RelationContract,
    state: RCSEState,
    config: ControllerConfig = DEFAULT_CONTROLLER,
) -> float:
    """``D_t(a)``: overlap with evidence already acquired, in ``[0, 1]``.

    Keyed on the **independence group**, not on prompt wording: two differently
    worded views of the same mechanism are redundant with each other, and the
    first use of a new mechanism is not redundant however many other views have
    run. Repeats of the same view accumulate, so resampling gets steadily less
    attractive rather than staying flat.
    """
    repeats = sum(
        1 for o in state.outcomes
        if not o.is_verification and o.action == action.action_type.value
    )
    if action.action_type is ActionType.RESAMPLE:
        # A repeat by definition, whatever view it names: already-covered
        # mechanism plus a growing penalty per repeat taken, so resampling
        # never masquerades as new structural diversity.
        return min(1.0, config.resample_redundancy + repeats * config.repeat_redundancy_step)
    if not action.view_id:
        return 0.0
    view = get_view(contract.relation, action.view_id)
    if view.is_gate:
        return 0.0
    group_used = view.independence_group in state.executed_groups
    return config.covered_mechanism_redundancy if group_used else 0.0


def candidate_impact(
    candidate: Candidate,
    contract: RelationContract,
    residual: ResidualEstimate,
    scoring: ScoringConfig = DEFAULT_SCORING,
) -> float:
    """How much resolving *this* candidate would reduce uncertainty, ``[0, 1]``.

    Deterministic, from evidence state only - never a factual value model. A
    candidate matters when the architecture has real evidence for it but cannot
    decide: broad acquisition support, an unresolved verdict, an explicit
    contradiction, or templates that disagree about it.
    """
    impact = coverage_q(candidate, contract, scoring)
    if candidate.contradiction_count > 0:
        impact = max(impact, 1.0)
    disagreements = [
        v.prompt_disagreement for v in candidate.verifications
        if v.prompt_disagreement is not None
    ]
    if disagreements:
        impact = max(impact, max(disagreements))
    if candidate.tier is VerificationTier.ADVERSARIAL_VERIFY:
        impact = max(impact, residual.components.get("unresolved_mass", 0.0))
    return max(0.0, min(1.0, impact))


def score_action(
    action: Action,
    contract: RelationContract,
    candidates: Sequence[Candidate],
    state: RCSEState,
    residual: ResidualEstimate,
    config: ControllerConfig = DEFAULT_CONTROLLER,
    scoring: ScoringConfig = DEFAULT_SCORING,
) -> tuple[float, dict[str, float]]:
    """``A_t(a)`` with its components, so a choice can be explained."""
    if action.action_type is ActionType.STOP:
        # Stopping scores exactly the residual threshold in force for this
        # relation: any action that beats it is worth taking, any that does not
        # is not.
        baseline = resolve_stopping(contract, config).residual_stop
        return baseline, {"stop_baseline": baseline}

    by_key = {c.key: c for c in candidates}
    expected_yield = 0.0
    gap = 0.0
    uncertainty = 0.0
    redundancy = 0.0

    if action.action_type in (
        ActionType.RUN_VIEW, ActionType.RUN_FACET, ActionType.RESAMPLE
    ):
        expected_yield = mechanism_yield_prior(action, contract, state, config)
        # Mandatory priority derives from the *contract*, not from the action's
        # enum member: an optional view is also a RUN_VIEW in other relations,
        # and a RUN_FACET must not lose priority for being differently named.
        gap = view_gap_relevance(action, contract, state, residual, config)
        redundancy = mechanism_redundancy(action, contract, state, config)
        # A fresh acquisition mechanism can resolve a thinly supported
        # candidate by corroborating it, but only indirectly.
        uncertainty = residual.components.get("unresolved_mass", 0.0) * config.indirect_uncertainty

    elif action.action_type is ActionType.REVERSE_CHECK:
        candidate = by_key.get(action.candidate_key)
        if candidate is None:
            return float("-inf"), {}
        expected_yield = mechanism_yield_prior(action, contract, state, config)
        # Directly addresses this candidate's missing independent mechanism.
        gap = 1.0 - coverage_q(candidate, contract, scoring)
        uncertainty = candidate_impact(candidate, contract, residual, scoring)
        redundancy = mechanism_redundancy(action, contract, state, config)

    elif action.action_type in (ActionType.VERIFY, ActionType.ADVERSARIAL_VERIFY):
        candidate = by_key.get(action.candidate_key)
        if candidate is None:
            return float("-inf"), {}
        # Verification is not an entity-generation action, so it earns no
        # acquisition yield. Its value is the uncertainty it removes from one
        # specific candidate (spec section 13.2 step 6).
        expected_yield = 0.0
        uncertainty = candidate_impact(candidate, contract, residual, scoring)
        if action.action_type is ActionType.ADVERSARIAL_VERIFY:
            uncertainty = min(1.0, uncertainty + config.adversarial_uncertainty_bonus)
        gap = residual.components.get("unresolved_mass", 0.0)
        # Re-verifying a candidate whose state has not changed adds nothing.
        redundancy = min(1.0, len(candidate.verifications) * config.reverify_redundancy)

    elif action.action_type is ActionType.CROSS_MODEL_CHECK:
        expected_yield = mechanism_yield_prior(action, contract, state, config)
        uncertainty = residual.components.get("unresolved_mass", 0.0)
        gap = residual.components.get("mechanism_gap", 0.0)
        # Redundant once the second family has already recalled independently.
        already = any(
            IndependenceGroup.CROSS_MODEL_RECALL in c.groups for c in candidates
        )
        redundancy = 1.0 if already else 0.0

    components = {
        "expected_yield": expected_yield,
        "gap": gap,
        "uncertainty": uncertainty,
        "cost": action.estimated_cost,
        "redundancy": redundancy,
    }
    score = (
        config.alpha_yield * expected_yield
        + config.beta_gap * gap
        + config.gamma_uncertainty * uncertainty
        - config.lambda_cost * action.estimated_cost
        - config.rho_redundancy * redundancy
    )
    return score, components


def choose_action(
    contract: RelationContract,
    candidates: Sequence[Candidate],
    state: RCSEState,
    budget: Budget,
    step: int,
    *,
    config: ControllerConfig = DEFAULT_CONTROLLER,
    cross_model_available: bool = False,
    gate: GateState | None = None,
    scoring: ScoringConfig = DEFAULT_SCORING,
    allowed_roles: "frozenset[ModelRole] | None" = None,
) -> ActionDecision:
    """Pick the highest-scoring legal action, with a full decision record.

    Verification is preferred over more discovery once unresolved mass is high
    (spec section 13.2 step 6): when yield has collapsed but uncertainty has
    not, resolving what we already have beats generating more of it.
    """
    residual = estimate_residual(
        contract, candidates, state, config.rcse, gate=gate, scoring=scoring
    )
    state_before = snapshot_state(candidates, budget, state)

    actions = legal_actions(
        contract, candidates, state, budget,
        cross_model_available=cross_model_available, config=config,
        allowed_roles=allowed_roles,
    )

    unresolved = residual.components.get("unresolved_mass", 0.0)
    prefer_verification = unresolved >= config.verify_first_unresolved

    # `should_stop` is the single stopping authority. When it says continue,
    # the STOP *action* must not be selectable, or the two would contradict
    # each other on whether the query is finished.
    stop, stop_reason = should_stop(contract, candidates, state, budget, residual, config)
    if not stop:
        actions = [a for a in actions if a.action_type is not ActionType.STOP] or actions

    considered: list[dict[str, Any]] = []
    best: tuple[float, Action] | None = None
    for action in actions:
        score, components = score_action(
            action, contract, candidates, state, residual, config
        )
        if prefer_verification and action.action_type in (
            ActionType.VERIFY,
            ActionType.ADVERSARIAL_VERIFY,
        ):
            # Spec section 13.2 step 3: verify high-impact uncertain candidates
            # before spending budget on more discovery. Configured, not hidden.
            score += config.verify_first_bonus
            components["verify_first_bonus"] = config.verify_first_bonus
        considered.append(
            {"action": action.to_json(), "score": score, "components": components}
        )
        # Ties break on the action's *full* identity - type, view and candidate
        # - so reverse(A) and reverse(B) are ordered reproducibly rather than
        # by whichever the enumeration happened to reach first.
        if best is None or (score, action.identity) > (best[0], best[1].identity):
            best = (score, action)

    assert best is not None
    score, action = best

    if stop:
        action = Action(ActionType.STOP, reason=stop_reason)
        score = resolve_stopping(contract, config).residual_stop

    return ActionDecision(
        step=step,
        chosen=action,
        score=score,
        residual=residual,
        considered=considered,
        state_before=state_before,
    )


# --------------------------------------------------------------------------
# Adaptive stopping, by programme type (spec section 12.3 / 13)
# --------------------------------------------------------------------------


def should_stop(
    contract: RelationContract,
    candidates: Sequence[Candidate],
    state: RCSEState,
    budget: Budget,
    residual: ResidualEstimate,
    config: ControllerConfig = DEFAULT_CONTROLLER,
) -> tuple[bool, str]:
    """Relation-typed stopping decision. A hard budget always wins.

    Thresholds come from the relation contract (spec section 12.3), with the
    controller config as the cross-relation fallback.
    """
    if budget.exhausted:
        return True, "hard budget exhausted"

    stopping = resolve_stopping(contract, config)

    mandatory_done = set(contract.mandatory_views) <= state.executed_views
    if not mandatory_done:
        return False, "mandatory views incomplete"

    unresolved = residual.components.get("unresolved_mass", 0.0)
    stability = state.set_stability()
    no_gain = state.consecutive_no_gain()
    program = contract.program_type

    if program is ProgramType.SMALL_SET:
        if stability >= stopping.stability_threshold and unresolved <= 0.0:
            return True, "small-set: mandatory structure explored, set stable, nothing unresolved"
    elif program is ProgramType.NULL_SINGLE:
        accepted = [c for c in candidates if c.status is CandidateStatus.ACCEPTED]
        if len(accepted) == 1 and unresolved <= 0.0:
            return True, "null-single: one strongly verified locality, nothing unresolved"
        gate_unresolved = residual.components.get("gate_unresolved", 0.0) > 0.0
        if not candidates and no_gain >= 1 and not gate_unresolved:
            # "Nothing was generated" is not "confidently nobody". While the
            # existence gate is undecided, an empty candidate set is a reason
            # to keep searching, not permission to stop.
            return True, "null-single: no locality candidate produced"
    elif program is ProgramType.NUMERIC:
        # Spec table 6: "dominant cluster stable + low relative dispersion".
        # Module 6 already computes both; re-deriving stability from the
        # trusted-set Jaccard would let two disagreeing clusters look settled
        # merely because the accepted set stopped changing.
        competition = residual.components.get("cluster_competition", 0.0)
        dispersion = residual.components.get("numeric_dispersion", 0.0)
        instability = residual.components.get("set_instability", 1.0)
        if (
            competition <= 0.0
            and dispersion <= 0.0
            and instability <= 0.0
            and unresolved <= 0.0
        ):
            return True, "numeric: dominant cluster stable, dispersion low"
    elif program is ProgramType.LARGE_OPEN_SET:
        facet_gap = residual.components.get("facet_gap", 1.0)
        if no_gain >= stopping.saturation_patience and facet_gap <= 0.0:
            return True, "large-open-set: verified yield saturated and no unvisited facet"

    pending_verification = any(
        c.tier in (VerificationTier.VERIFY, VerificationTier.ADVERSARIAL_VERIFY)
        and not c.verifications
        and c.status is not CandidateStatus.REJECTED
        for c in candidates
    )
    if pending_verification and not budget.exhausted:
        # Spec section 13.2 step 3: verify high-impact uncertain candidates
        # before spending budget elsewhere - and before concluding the query is
        # finished. A low aggregate residual does not resolve a disputed answer.
        return False, "candidate awaiting verification"

    if residual.residual < stopping.residual_stop:
        return True, (
            f"residual {residual.residual:.3f} below stop threshold "
            f"{stopping.residual_stop} ({stopping.source})"
        )

    return False, "continue"


def record_outcome(
    state: RCSEState,
    action: Action,
    *,
    trusted_keys: Sequence[str],
    new_candidates: int,
    generated_tokens: int,
    independence_group: IndependenceGroup | None = None,
    facet_id: str = "",
    synthetic_cost: bool = False,
) -> ActionOutcome:
    """Fold an executed action's result back into the RCSE state.

    Verified yield is the **increase in the trusted set**, computed here from
    before/after rather than passed in. That is the smallest provenance-correct
    attribution available: the gain is credited to the action that produced it,
    and no later verification is retro-attributed to an earlier action.

    Raw mention counts and verifier call counts are deliberately not yield - a
    view repeating a name already trusted has added nothing.
    """
    before = state.last_trusted
    after = frozenset(trusted_keys)

    outcome = ActionOutcome(
        action=action.action_type.value,
        new_trusted=len(after - before),
        new_candidates=new_candidates,
        generated_tokens=generated_tokens,
        is_verification=action.action_type
        in (ActionType.VERIFY, ActionType.ADVERSARIAL_VERIFY),
        synthetic_cost=synthetic_cost,
    )
    state.record(outcome)
    state.record_trusted(after)

    state.executed_actions.add(action.identity)

    # Three separate registers, one per coverage axis.
    if action.view_id:
        state.executed_views.add(action.view_id)
    if facet_id:
        state.executed_facets.add(facet_id)
    if independence_group is not None:
        state.executed_groups.add(independence_group)
    return outcome
