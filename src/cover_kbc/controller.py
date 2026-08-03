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
from cover_kbc.coverage import (
    DEFAULT_RCSE,
    ActionOutcome,
    RCSEConfig,
    RCSEState,
    ResidualEstimate,
    estimate_residual,
)
from cover_kbc.types import (
    Budget,
    Candidate,
    CandidateStatus,
    ProgramType,
    VerificationTier,
)


class ActionType(str, Enum):
    """The controller's action space."""

    RUN_VIEW = "RUN_VIEW"
    RUN_FACET = "RUN_FACET"
    VERIFY = "VERIFY"
    ADVERSARIAL_VERIFY = "ADVERSARIAL_VERIFY"
    CROSS_MODEL_CHECK = "CROSS_MODEL_CHECK"
    RESAMPLE = "RESAMPLE"
    STOP = "STOP"


@dataclass(frozen=True)
class Action:
    """One executable step, with the arguments needed to run it."""

    action_type: ActionType
    view_id: str = ""
    facet_id: str = ""
    candidate_key: str = ""
    reason: str = ""
    estimated_cost: float = 1.0

    def to_json(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "view_id": self.view_id,
            "facet_id": self.facet_id,
            "candidate_key": self.candidate_key,
            "reason": self.reason,
            "estimated_cost": self.estimated_cost,
        }


@dataclass(frozen=True)
class ControllerConfig:
    """Weights and thresholds for the action score and stopping rules."""

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
        "covered_views": sorted(state.covered_facets),
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
) -> list[Action]:
    """Enumerate what the controller may legally do next."""
    if budget.exhausted:
        return [Action(ActionType.STOP, reason="hard budget exhausted")]

    actions: list[Action] = []

    for view_id in contract.all_views():
        if view_id in state.covered_facets:
            continue
        mandatory = view_id in contract.mandatory_views
        actions.append(
            Action(
                ActionType.RUN_VIEW if mandatory else ActionType.RUN_FACET,
                view_id=view_id,
                reason="mandatory view not yet run" if mandatory else "unexplored optional facet",
                estimated_cost=1.0,
            )
        )

    for candidate in candidates:
        if candidate.tier is VerificationTier.ADVERSARIAL_VERIFY:
            actions.append(
                Action(
                    ActionType.ADVERSARIAL_VERIFY,
                    candidate_key=candidate.key,
                    reason="contradiction or high prompt disagreement",
                    estimated_cost=2.0,
                )
            )
        elif candidate.tier is VerificationTier.VERIFY:
            actions.append(
                Action(
                    ActionType.VERIFY,
                    candidate_key=candidate.key,
                    reason="weak or threshold-adjacent support",
                    estimated_cost=1.0,
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

    actions.append(Action(ActionType.STOP, reason="no further action scored above stopping"))
    return actions


def score_action(
    action: Action,
    contract: RelationContract,
    candidates: Sequence[Candidate],
    state: RCSEState,
    residual: ResidualEstimate,
    config: ControllerConfig = DEFAULT_CONTROLLER,
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

    if action.action_type in (ActionType.RUN_VIEW, ActionType.RUN_FACET):
        # An unrun view is expected to yield in proportion to the residual need.
        expected_yield = residual.components.get("marginal_yield", 0.0)
        gap = residual.components.get("facet_gap", 0.0)
        if action.action_type is ActionType.RUN_VIEW:
            gap += 0.5  # mandatory structure has priority over optional facets
        redundancy = 1.0 if action.view_id in state.covered_facets else 0.0
        uncertainty = residual.components.get("unresolved_mass", 0.0) * 0.5

    elif action.action_type in (ActionType.VERIFY, ActionType.ADVERSARIAL_VERIFY):
        candidate = by_key.get(action.candidate_key)
        if candidate is None:
            return float("-inf"), {}
        # Verification does not add candidates; its value is uncertainty removed.
        uncertainty = 1.0 if action.action_type is ActionType.ADVERSARIAL_VERIFY else 0.6
        gap = residual.components.get("unresolved_mass", 0.0)
        redundancy = min(1.0, len(candidate.verifications) * 0.5)

    elif action.action_type is ActionType.CROSS_MODEL_CHECK:
        expected_yield = residual.components.get("marginal_yield", 0.0) * 0.5
        uncertainty = residual.components.get("unresolved_mass", 0.0)
        # Redundant if the second family already recalled independently.
        from cover_kbc.types import IndependenceGroup

        already = any(
            IndependenceGroup.CROSS_MODEL_RECALL in c.groups for c in candidates
        )
        redundancy = 1.0 if already else 0.0

    elif action.action_type is ActionType.RESAMPLE:
        # Repeating a mechanism adds no independent evidence, so it is scored
        # as almost pure redundancy - it exists in the action space only for
        # completeness.
        expected_yield = residual.components.get("marginal_yield", 0.0) * 0.2
        redundancy = 1.0

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
) -> ActionDecision:
    """Pick the highest-scoring legal action, with a full decision record.

    Verification is preferred over more discovery once unresolved mass is high
    (spec section 13.2 step 6): when yield has collapsed but uncertainty has
    not, resolving what we already have beats generating more of it.
    """
    residual = estimate_residual(contract, candidates, state, config.rcse)
    state_before = snapshot_state(candidates, budget, state)

    actions = legal_actions(
        contract, candidates, state, budget, cross_model_available=cross_model_available
    )

    unresolved = residual.components.get("unresolved_mass", 0.0)
    prefer_verification = unresolved >= config.verify_first_unresolved

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
            score += 0.5
            components["verify_first_bonus"] = 0.5
        considered.append(
            {"action": action.to_json(), "score": score, "components": components}
        )
        # Ties break on action-type name, so the choice is deterministic.
        if best is None or (score, action.action_type.value) > (
            best[0], best[1].action_type.value
        ):
            best = (score, action)

    assert best is not None
    score, action = best

    stop, stop_reason = should_stop(contract, candidates, state, budget, residual, config)
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

    mandatory_done = set(contract.mandatory_views) <= state.covered_facets
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
        if not candidates and no_gain >= 1:
            return True, "null-single: no locality candidate produced"
    elif program is ProgramType.NUMERIC:
        instability = residual.components.get("set_instability", 1.0)
        if instability <= 0.0 and unresolved <= 0.0:
            return True, "numeric: dominant cluster stable, dispersion low"
    elif program is ProgramType.LARGE_OPEN_SET:
        facet_gap = residual.components.get("facet_gap", 1.0)
        if no_gain >= stopping.saturation_patience and facet_gap <= 0.0:
            return True, "large-open-set: verified yield saturated and no unvisited facet"

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
    new_verified: int,
    new_candidates: int,
    generated_tokens: int,
    accepted_keys: Sequence[str],
) -> ActionOutcome:
    """Fold an executed action's result back into the RCSE state."""
    outcome = ActionOutcome(
        action=action.action_type.value,
        new_verified=new_verified,
        new_candidates=new_candidates,
        generated_tokens=generated_tokens,
    )
    state.record(outcome)
    state.record_accepted(accepted_keys)
    if action.view_id:
        state.covered_facets.add(action.view_id)
    return outcome
