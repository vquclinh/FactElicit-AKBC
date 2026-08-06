"""Module 21's public contract - the vocabulary of action value.

Proposal §17, verbatim::

    U_t(a) = α·Ĝ_verified(a) + β·ΔR̂(a) + γ·ΔĤ(a)
             − δ·Ĉost(a) − η·R̂edundancy(a) − κ·F̂P(a)

    "The estimates come from relation-specific historical bins on TRAIN; no
    neural policy is trained. M21 selects a* = arg max U_t(a) if U_t(a*) >
    τ_continue; otherwise it returns STOP."

Four separations run through every type below.

**M21 values actions, never candidates.** There is no
``candidate_truth_probability``, no ``planner_confidence`` and no
``final_answer_score``. The planner ranks *what to do next*; whether an object
belongs in the answer is Module 8's question and nobody else's.

**Estimates are historical, not derived.** Every one of the six components comes
from a TRAIN-calibrated bin. The planner never recomputes an entropy, invents a
transition model or reverse-engineers a gain from the current graph - doing so
would be a second factual scoring system competing with the audited one.

**Legality, affordability and value are three different questions.** The owning
module says an action is legal, Module 20 says it is affordable, and only then
does M21 ask whether it is worth doing. A high-utility action that Module 20
denies is not selectable, ever.

**Missing is not zero.** A component with no historical estimate fails loudly.
Silently reading it as zero would bias ``arg max`` toward whichever action
happens to have the sparsest history.

**No production numbers exist.** §17 supplies no value for α, β, γ, δ, η, κ or
τ_continue, and no TRAIN bins have been built. Everything numeric here is a
schema awaiting calibration - see :class:`EstimateSource`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

#: Bumped when the planner's arithmetic or artefact shape changes.
PLANNER_VERSION = "m21-v1"

PLANNER_DISCLAIMER = (
    "This is a shadow control decision over actions, not a factual judgement. "
    "It accepts no candidate, rejects none, and finalises nothing; Module 7 "
    "remains the production controller and Module 8 the finaliser."
)


class PlannerError(RuntimeError):
    """A planner contract was violated. Never swallowed, never defaulted."""


class ActionFamily(str, Enum):
    """The proposal's small action space, canonicalised once.

    Both core Module 7's action types and the upgraded modules' richer action
    identities adapt onto these families, so one semantic action never appears
    twice under two names. ``STOP`` is deliberately **absent**: §17 makes it the
    fallback when ``U_t(a*) > τ_continue`` fails, not a competitor with a
    fabricated utility.
    """

    SPECIALIST_PROBE = "SPECIALIST_PROBE"
    PSEUDO_MEMORY_PROBE = "PSEUDO_MEMORY_PROBE"
    CANDIDATE_FREE_RECALL = "CANDIDATE_FREE_RECALL"
    BLIND_VERIFY = "BLIND_VERIFY"
    SPECIALIST_VERIFY = "SPECIALIST_VERIFY"
    COUNTERFACTUAL_VERIFY = "COUNTERFACTUAL_VERIFY"
    REVERSE_CHECK = "REVERSE_CHECK"
    CROSS_MODEL_CHECK = "CROSS_MODEL_CHECK"
    RESAMPLE = "RESAMPLE"


class EstimateSource(str, Enum):
    """Where a historical package or a coefficient set came from.

    §17 puts the estimates on TRAIN, and TRAIN calibration has not been
    performed. ``SYNTHETIC_TEST`` exists so the planner's arithmetic can be
    tested with fictional numbers that shipped configuration **refuses**.
    """

    TRAIN_CALIBRATED = "TRAIN_CALIBRATED"
    SYNTHETIC_TEST = "SYNTHETIC_TEST"

    @property
    def is_production(self) -> bool:
        return self is EstimateSource.TRAIN_CALIBRATED


class DecisionKind(str, Enum):
    ACTION = "ACTION"
    STOP = "STOP"


class StopReason(str, Enum):
    """Why STOP, when STOP.

    Three reasons, kept apart because they mean different things to whoever
    reads the trace: nothing was legal, nothing legal was affordable, or
    everything affordable was judged not worth its cost.

    A missing calibration is **not** here. That is a configuration failure and
    raises; reporting it as STOP would let a broken planner look like a
    confident one.
    """

    NO_LEGAL_ACTION = "NO_LEGAL_ACTION"
    NO_AFFORDABLE_ACTION = "NO_AFFORDABLE_ACTION"
    UTILITY_BELOW_THRESHOLD = "UTILITY_BELOW_THRESHOLD"


class ActionExecutionStatus(str, Enum):
    """What the owning module says about this action right now."""

    ELIGIBLE = "ELIGIBLE"
    ALREADY_EXECUTED = "ALREADY_EXECUTED"
    INELIGIBLE = "INELIGIBLE"


def _finite(value: float, what: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PlannerError(f"{what} must be a number, got {value!r}")
    if not math.isfinite(float(value)):
        raise PlannerError(f"{what} is not finite ({value!r})")
    return float(value)


# --------------------------------------------------------------------------
# Legal actions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerActionCandidate:
    """One action its owner has already declared legal.

    M21 does **not** derive legality. A weak facet, a high residual or an
    unsupported candidate are reasons an action might be *valuable*; they are
    never reasons it is *permitted*. The owning registry decides that, and an
    action it calls ineligible or already-executed cannot be resurrected here.
    """

    action_id: str
    source_module: str
    family: ActionFamily
    #: What Module 20 would charge for it. Reused, never recomputed.
    budget_descriptor: Any
    target: str = ""
    facet_id: str = ""
    model_role: str = ""
    #: Which module declared this legal, and on what basis. Provenance only.
    legal_provenance: str = ""
    status: ActionExecutionStatus = ActionExecutionStatus.ELIGIBLE
    #: True only where the action contract explicitly permits repetition.
    repeatable: bool = False

    def __post_init__(self) -> None:
        if not self.action_id or not self.source_module:
            raise PlannerError("a planner action needs an id and an owning module")
        if not self.legal_provenance:
            raise PlannerError(
                f"action {self.action_id!r} carries no legal provenance; M21 "
                "may only consider actions an owner declared legal"
            )

    @property
    def is_eligible(self) -> bool:
        return self.status is ActionExecutionStatus.ELIGIBLE

    #: Canonical identity, including arguments. ``reverse(A)`` and
    #: ``reverse(B)`` are different actions; deduplication and tie-breaking key
    #: on this, never on the family alone.
    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.family.value, self.action_id, self.target, self.facet_id)

    def to_json(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id, "source_module": self.source_module,
            "family": self.family.value, "target": self.target,
            "facet_id": self.facet_id, "model_role": self.model_role,
            "legal_provenance": self.legal_provenance, "status": self.status.value,
            "repeatable": self.repeatable,
        }


# --------------------------------------------------------------------------
# Full-state snapshot
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerStateSnapshot:
    """Appendix C's *full state*, read-only.

    Every upstream layer is carried **as its own object**, never flattened into
    a score. Module 19's residual, Module 17's availability and Module 16's
    disagreement stay separately readable, because the historical bins select
    on them individually and collapsing them would destroy exactly the
    distinctions the bins are keyed by.
    """

    subject: str
    relation: str
    row_index: int
    program_type: str
    round_index: int = 0
    #: Module 9. Required: the bins are relation- and risk-specific.
    risk_profile: Any = None
    #: Corrected Layer-4 evidence state.
    layer4: Any = None
    #: Module 19 coverage-gap state. Context for bin lookup, never a utility term.
    coverage_gap: Any = None
    #: Module 20 plan and ledger snapshot. Affordability, never value.
    budget_plan: Any = None
    budget_ledger: Any = None
    #: Canonical identities of actions already executed this query.
    executed_actions: tuple[tuple[str, str, str, str], ...] = ()
    #: Which model role is currently resident. Inspected, never changed.
    resident_model_role: str = ""

    @property
    def query_key(self) -> tuple[str, str, int]:
        return (self.subject, self.relation, self.row_index)

    def to_json(self) -> dict[str, Any]:
        return {
            "SubjectEntity": self.subject, "Relation": self.relation,
            "row_index": self.row_index, "program_type": self.program_type,
            "round_index": self.round_index,
            "executed_actions": [list(a) for a in self.executed_actions],
            "resident_model_role": self.resident_model_role,
        }


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionUtilityBreakdown:
    """§17's six components and their weighted contributions, itemised.

    Every term is shown so the arithmetic can be checked against the proposal
    line by line. There is no seventh term, no relation-specific adjustment and
    no reuse of Module 7's own action score.
    """

    action_id: str
    #: Raw historical estimates.
    verified_gain: float
    delta_r: float
    delta_h: float
    expected_cost: float
    redundancy: float
    false_positive_risk: float
    #: Weighted contributions, signed exactly as §17 writes them.
    verified_gain_term: float
    delta_r_term: float
    delta_h_term: float
    cost_term: float
    redundancy_term: float
    false_positive_term: float
    utility: float
    bin_key: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "verified_gain": self.verified_gain, "delta_r": self.delta_r,
            "delta_h": self.delta_h, "expected_cost": self.expected_cost,
            "redundancy": self.redundancy,
            "false_positive_risk": self.false_positive_risk,
            "verified_gain_term": self.verified_gain_term,
            "delta_r_term": self.delta_r_term, "delta_h_term": self.delta_h_term,
            "cost_term": self.cost_term,
            "redundancy_term": self.redundancy_term,
            "false_positive_term": self.false_positive_term,
            "utility": self.utility, "bin_key": self.bin_key,
        }


@dataclass(frozen=True)
class PlannerCalibration:
    """§17's policy coefficients. **None are calibrated yet.**

    Deterministic policy coefficients, not neural weights: nothing is fitted by
    gradient, and nothing updates at run time. §17 names all seven and supplies
    no value for any of them, so every instance today is ``SYNTHETIC_TEST``.
    """

    calibration_version: str
    source: EstimateSource
    alpha: float
    beta: float
    gamma: float
    delta: float
    eta: float
    kappa: float
    tau_continue: float
    #: §17 says "1-2 step micro-lookahead". Nothing deeper exists.
    lookahead_depth: int = 1

    def __post_init__(self) -> None:
        for name in ("alpha", "beta", "gamma", "delta", "eta", "kappa",
                     "tau_continue"):
            _finite(getattr(self, name), f"planner calibration {name}")
        # The three penalties are subtracted in §17's equation. A negative
        # coefficient would silently turn a penalty into a reward.
        for name in ("delta", "eta", "kappa"):
            if getattr(self, name) < 0:
                raise PlannerError(
                    f"planner calibration {name} is negative; §17 subtracts this "
                    "term, so a negative coefficient would make a penalty a bonus"
                )
        for name in ("alpha", "beta", "gamma"):
            if getattr(self, name) < 0:
                raise PlannerError(
                    f"planner calibration {name} is negative; §17 adds this term"
                )
        if self.lookahead_depth not in (1, 2):
            raise PlannerError(
                f"unsupported lookahead depth {self.lookahead_depth}; §17 "
                "specifies 1-2 step micro-lookahead"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "calibration_version": self.calibration_version,
            "source": self.source.value,
            "alpha": self.alpha, "beta": self.beta, "gamma": self.gamma,
            "delta": self.delta, "eta": self.eta, "kappa": self.kappa,
            "tau_continue": self.tau_continue,
            "lookahead_depth": self.lookahead_depth,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PlannerCalibration":
        return cls(
            calibration_version=payload["calibration_version"],
            source=EstimateSource(payload["source"]),
            alpha=float(payload["alpha"]), beta=float(payload["beta"]),
            gamma=float(payload["gamma"]), delta=float(payload["delta"]),
            eta=float(payload["eta"]), kappa=float(payload["kappa"]),
            tau_continue=float(payload["tau_continue"]),
            lookahead_depth=int(payload.get("lookahead_depth", 1)),
        )


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SuccessorDiagnostics:
    """What depth-2 added, itemised so the extension stays inspectable."""

    action_id: str
    #: (probability, successor bin key, best second-step utility, second action)
    branches: tuple[tuple[float, str, float, str], ...]
    expected_successor_utility: float
    total_value: float

    def to_json(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "branches": [
                {"probability": p, "successor_bin": k, "best_utility": u,
                 "second_action": a}
                for p, k, u, a in self.branches
            ],
            "expected_successor_utility": self.expected_successor_utility,
            "total_value": self.total_value,
        }


@dataclass(frozen=True)
class DeniedAction:
    """An action Module 20 refused. Recorded, never silently dropped."""

    action_id: str
    reason: str
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"action_id": self.action_id, "reason": self.reason,
                "detail": self.detail}


@dataclass(frozen=True)
class MicroPlannerDecision:
    """One shadow control decision. Never a factual prediction."""

    planner_version: str
    subject: str
    relation: str
    row_index: int
    program_type: str
    round_index: int
    state_signature: str
    kind: DecisionKind
    tau_continue: float
    lookahead_depth: int
    history_version: str
    calibration_version: str
    legal_actions: tuple[str, ...] = ()
    affordable_actions: tuple[str, ...] = ()
    denied_actions: tuple[DeniedAction, ...] = ()
    utilities: tuple[ActionUtilityBreakdown, ...] = ()
    successors: tuple[SuccessorDiagnostics, ...] = ()
    selected_action: str = ""
    selected_value: float | None = None
    stop_reason: StopReason | None = None
    tie_break_reason: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "planner_version": self.planner_version,
            "SubjectEntity": self.subject, "Relation": self.relation,
            "row_index": self.row_index, "program_type": self.program_type,
            "round_index": self.round_index,
            "state_signature": self.state_signature,
            "decision_kind": self.kind.value,
            "tau_continue": self.tau_continue,
            "lookahead_depth": self.lookahead_depth,
            "history_version": self.history_version,
            "calibration_version": self.calibration_version,
            "legal_actions": list(self.legal_actions),
            "affordable_actions": list(self.affordable_actions),
            "denied_actions": [d.to_json() for d in self.denied_actions],
            "utilities": [u.to_json() for u in self.utilities],
            "successors": [s.to_json() for s in self.successors],
            "selected_action": self.selected_action,
            "selected_value": self.selected_value,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "tie_break_reason": self.tie_break_reason,
            "planner_disclaimer": PLANNER_DISCLAIMER,
            "errors": list(self.errors),
        }


__all__ = [
    "PLANNER_DISCLAIMER",
    "PLANNER_VERSION",
    "ActionExecutionStatus",
    "ActionFamily",
    "ActionUtilityBreakdown",
    "DecisionKind",
    "DeniedAction",
    "EstimateSource",
    "MicroPlannerDecision",
    "PlannerActionCandidate",
    "PlannerCalibration",
    "PlannerError",
    "PlannerStateSnapshot",
    "StopReason",
    "SuccessorDiagnostics",
]
