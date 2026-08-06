"""Proposal §17's expected-value micro-planner. Shadow, non-neural.

    U_t(a) = α·Ĝ_verified(a) + β·ΔR̂(a) + γ·ΔĤ(a)
             − δ·Ĉost(a) − η·R̂edundancy(a) − κ·F̂P(a)

    a* = arg max U_t(a) if U_t(a*) > τ_continue, otherwise STOP.

The equation is transcribed once, in :func:`utility`, and asserted term by term
by test. There is no seventh term, no relation-specific adjustment, no
mandatory-view boost and no reuse of Module 7's own action score.

Three orderings are kept strictly apart, because collapsing them is how a
planner starts doing someone else's job:

1. **legality** - the owning module's answer, taken as given;
2. **affordability** - Module 20's answer, reused rather than recomputed, and
   never overridden by an expected cost;
3. **value** - M21's own answer, and the only one it owns.

An action that fails 1 or 2 never reaches 3. A high utility does not make an
action legal and does not make it affordable.

**STOP is not an action with a fabricated utility.** §17 makes it the fallback
when nothing legal and affordable clears the threshold. It is also not the
answer to a broken configuration: a missing calibration raises, because a
planner that reports STOP when it cannot think looks exactly like one that
thought and decided to stop.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping, Sequence

from cover_kbc.control.budget_types import BudgetSchedulerError
from cover_kbc.control.historical_bins import (
    HistoricalBinPackage,
    state_bin_key,
)
from cover_kbc.control.planner_types import (
    PLANNER_VERSION,
    ActionFamily,
    ActionUtilityBreakdown,
    DecisionKind,
    DeniedAction,
    EstimateSource,
    MicroPlannerDecision,
    PlannerActionCandidate,
    PlannerCalibration,
    PlannerError,
    PlannerStateSnapshot,
    StopReason,
    SuccessorDiagnostics,
)


def utility(
    bin_estimates: Any, calibration: PlannerCalibration, *, action_id: str,
    bin_key: str = "",
) -> ActionUtilityBreakdown:
    """§17's equation, transcribed exactly and itemised.

    Every estimate comes from the historical bin; none is derived from the
    current graph. The three penalty terms are **subtracted**, matching the
    proposal's signs, and each weighted contribution is recorded so the
    arithmetic can be checked line by line.
    """
    verified_gain = bin_estimates.expected_verified_gain
    delta_r = bin_estimates.expected_delta_r
    delta_h = bin_estimates.expected_delta_h
    expected_cost = bin_estimates.expected_cost
    redundancy = bin_estimates.expected_redundancy
    false_positive_risk = bin_estimates.expected_fp

    verified_gain_term = calibration.alpha * verified_gain
    delta_r_term = calibration.beta * delta_r
    delta_h_term = calibration.gamma * delta_h
    cost_term = calibration.delta * expected_cost
    redundancy_term = calibration.eta * redundancy
    false_positive_term = calibration.kappa * false_positive_risk

    return ActionUtilityBreakdown(
        action_id=action_id,
        verified_gain=verified_gain, delta_r=delta_r, delta_h=delta_h,
        expected_cost=expected_cost, redundancy=redundancy,
        false_positive_risk=false_positive_risk,
        verified_gain_term=verified_gain_term, delta_r_term=delta_r_term,
        delta_h_term=delta_h_term, cost_term=cost_term,
        redundancy_term=redundancy_term,
        false_positive_term=false_positive_term,
        utility=(
            verified_gain_term + delta_r_term + delta_h_term
            - cost_term - redundancy_term - false_positive_term
        ),
        bin_key=bin_key,
    )


def state_signature(state: PlannerStateSnapshot, bin_key: str) -> str:
    """Deterministic signature of the planned-over state.

    Derived from the query, the round and the resolved bin key - no clock, no
    RNG, no object identity - so an identical state produces an identical
    decision record.
    """
    payload = "|".join((
        "planner", PLANNER_VERSION, state.subject, state.relation,
        str(state.row_index), state.program_type, str(state.round_index), bin_key,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class MicroPlanner:
    """Module 21 in shadow: ranks actions, selects one or STOPs, executes none.

    Consumes the full state and a **legal** action list, prices affordability
    through Module 20, values actions through TRAIN historical bins, and returns
    a decision. It generates nothing, reserves nothing, mutates nothing, and
    Module 7 keeps production authority.
    """

    def __init__(
        self, history: HistoricalBinPackage, calibration: PlannerCalibration,
        *, planner_version: str = PLANNER_VERSION,
    ) -> None:
        if planner_version != PLANNER_VERSION:
            raise PlannerError(
                f"unsupported planner_version {planner_version!r}; this build "
                f"implements {PLANNER_VERSION}"
            )
        if history is None or calibration is None:
            raise PlannerError(
                "Module 21 needs both a historical bin package and a planner "
                "calibration; §17 puts every estimate on TRAIN history"
            )
        if calibration.source is not history.source:
            raise PlannerError(
                f"calibration source {calibration.source.value} does not match "
                f"history source {history.source.value}"
            )
        self.history = history
        self.calibration = calibration

    # -- upstream requirements -------------------------------------------

    def _require_state(self, state: PlannerStateSnapshot) -> None:
        """Every upstream layer M21 depends on must actually be present.

        Reconstructing a missing layer would mean inventing the state the bins
        are keyed by, which is the same error as inventing the estimates.
        """
        missing = [
            name for name, value in (
                ("M9 risk profile", state.risk_profile),
                ("Layer-4 evidence state", state.layer4),
                ("M19 coverage-gap state", state.coverage_gap),
                ("M20 budget plan", state.budget_plan),
            ) if value is None
        ]
        if missing:
            raise PlannerError(
                f"Module 21 requires {', '.join(missing)}; Appendix C gives it "
                "the full state and it may not reconstruct a missing layer"
            )
        profile = state.risk_profile
        if (profile.relation != state.relation
                or profile.subject != state.subject
                or getattr(profile, "row_index", state.row_index) != state.row_index):
            raise PlannerError(
                f"risk profile is for {profile.subject!r}/{profile.relation!r}, "
                f"not {state.subject!r}/{state.relation!r}"
            )
        profile_program = getattr(profile.program_type, "value", profile.program_type)
        if profile_program != state.program_type:
            raise PlannerError(
                f"risk profile ProgramType {profile_program!r} does not match "
                f"the state's {state.program_type!r}"
            )
        for name, layer in (("Layer-4", state.layer4),
                            ("M19", state.coverage_gap),
                            ("M20", state.budget_plan)):
            if (layer.subject, layer.relation, layer.row_index) != state.query_key:
                raise PlannerError(
                    f"{name} state is for "
                    f"{(layer.subject, layer.relation, layer.row_index)}, not "
                    f"{state.query_key}"
                )

    # -- action screening -------------------------------------------------

    def _screen(
        self, state: PlannerStateSnapshot, actions: Sequence[PlannerActionCandidate]
    ) -> tuple[list[PlannerActionCandidate], list[DeniedAction]]:
        """Keep the eligible actions; record why the others fell out.

        An action its owner calls ineligible or already-executed is **not**
        resurrected, however valuable it might look. Nothing is silently
        dropped: every exclusion is recorded.
        """
        seen: set[tuple[str, str, str, str]] = set()
        kept: list[PlannerActionCandidate] = []
        excluded: list[DeniedAction] = []
        for action in actions:
            if action.identity in seen:
                raise PlannerError(
                    f"duplicate action identity {action.identity}; one action "
                    "may not be offered twice"
                )
            seen.add(action.identity)
            descriptor = action.budget_descriptor
            if descriptor is not None and descriptor.query_key != state.query_key:
                raise PlannerError(
                    f"action {action.action_id!r} carries a Module 20 descriptor "
                    f"for {descriptor.query_key}, not {state.query_key}"
                )
            if not action.is_eligible:
                excluded.append(DeniedAction(
                    action.action_id, action.status.value,
                    "its owning module does not declare it eligible",
                ))
                continue
            if action.identity in state.executed_actions and not action.repeatable:
                excluded.append(DeniedAction(
                    action.action_id, "ALREADY_EXECUTED",
                    "this exact action already ran and its contract does not "
                    "permit a resample",
                ))
                continue
            kept.append(action)
        return kept, excluded

    def _affordable(
        self, ledger: Any, actions: Sequence[PlannerActionCandidate]
    ) -> tuple[list[PlannerActionCandidate], list[DeniedAction]]:
        """Module 20 decides. M21 reuses the answer and never recomputes it."""
        if ledger is None:
            raise PlannerError(
                "Module 21 has no Module 20 affordability state; an action's "
                "resource safety is Module 20's answer, not the planner's"
            )
        affordable: list[PlannerActionCandidate] = []
        denied: list[DeniedAction] = []
        for action in actions:
            descriptor = action.budget_descriptor
            if descriptor is None:
                raise PlannerError(
                    f"action {action.action_id!r} has no Module 20 descriptor, so "
                    "its affordability cannot be established"
                )
            probe = copy.deepcopy(ledger)
            try:
                outcome = probe.reserve(descriptor)
            except BudgetSchedulerError as error:
                raise PlannerError(
                    f"Module 20 refused to price {action.action_id!r}: {error}"
                ) from error
            if hasattr(outcome, "reason"):
                denied.append(DeniedAction(
                    action.action_id, outcome.reason.value, outcome.detail))
            else:
                affordable.append(action)
        return affordable, denied

    # -- valuation --------------------------------------------------------

    def _score(
        self, state: PlannerStateSnapshot, actions: Sequence[PlannerActionCandidate],
        *, bin_key: str,
    ) -> list[ActionUtilityBreakdown]:
        return [
            utility(
                self.history.lookup(
                    relation=state.relation, program_type=state.program_type,
                    state_bin_key=bin_key, family=action.family,
                    target_class=action.facet_id or action.target,
                ),
                self.calibration, action_id=action.action_id, bin_key=bin_key,
            )
            for action in actions
        ]

    def _lookahead(
        self, state: PlannerStateSnapshot, actions: Sequence[PlannerActionCandidate],
        breakdowns: Sequence[ActionUtilityBreakdown], *, bin_key: str,
    ) -> tuple[dict[str, float], tuple[SuccessorDiagnostics, ...]]:
        """§17's second step, as an undiscounted finite-horizon extension.

        ``value(a1) = U(a1) + Σ_i p_i · max_{a2} U(a2 | successor bin i)``

        Undiscounted and additive because **§17 supplies no discount term**, and
        inventing one would be a fitted number wearing a structural hat. The
        successor distribution is recorded TRAIN history, not a runtime guess,
        and no model is asked to picture the future state.

        Affordability is carried forward: a second action counts only if it is
        still affordable *after* the first one's Module 20 reservation. Expected
        historical cost never substitutes for that.
        """
        by_id = {action.action_id: action for action in actions}
        values: dict[str, float] = {}
        diagnostics: list[SuccessorDiagnostics] = []

        for breakdown in breakdowns:
            first = by_id[breakdown.action_id]
            root = self.history.lookup(
                relation=state.relation, program_type=state.program_type,
                state_bin_key=bin_key, family=first.family,
                target_class=first.facet_id or first.target,
            )
            if not root.successors:
                raise PlannerError(
                    f"depth-2 planning needs successor statistics, and the bin "
                    f"for {first.action_id!r} records none; depth-1 remains "
                    "available but must be requested explicitly"
                )

            # The budget after a1 is held. A hypothetical copy: Module 20's own
            # ledger is never touched.
            after_first = copy.deepcopy(state.budget_ledger)
            after_first.reserve(first.budget_descriptor)

            seconds = [
                action for action in actions
                # A one-shot action cannot be simulated twice, and the owner's
                # repeatability flag is the only thing that says otherwise.
                if action.identity != first.identity or action.repeatable
            ]
            affordable_seconds, _ = self._affordable(after_first, seconds)

            branches: list[tuple[float, str, float, str]] = []
            expected = 0.0
            for successor in sorted(
                root.successors, key=lambda s: s.successor_state_bin
            ):
                best_value = 0.0
                best_action = ""
                for candidate in affordable_seconds:
                    second = utility(
                        self.history.lookup(
                            relation=state.relation,
                            program_type=state.program_type,
                            state_bin_key=successor.successor_state_bin,
                            family=candidate.family,
                            target_class=candidate.facet_id or candidate.target,
                        ),
                        self.calibration, action_id=candidate.action_id,
                        bin_key=successor.successor_state_bin,
                    )
                    if (not best_action
                            or second.utility > best_value
                            or (second.utility == best_value
                                and candidate.identity < by_id[best_action].identity)):
                        best_value, best_action = second.utility, candidate.action_id
                branches.append((
                    successor.probability, successor.successor_state_bin,
                    best_value, best_action,
                ))
                expected += successor.probability * best_value

            total = breakdown.utility + expected
            values[breakdown.action_id] = total
            diagnostics.append(SuccessorDiagnostics(
                action_id=breakdown.action_id, branches=tuple(branches),
                expected_successor_utility=expected, total_value=total,
            ))
        return values, tuple(diagnostics)

    # -- decision ---------------------------------------------------------

    def plan(
        self, state: PlannerStateSnapshot,
        legal_actions: Sequence[PlannerActionCandidate],
    ) -> MicroPlannerDecision:
        """Appendix C: full state + legal actions -> next action or STOP.

        Returns a decision. **Executes nothing**: no generation, no
        verification, no reservation, no graph mutation, no change to Module 7's
        pending action or Module 8's finalisation.
        """
        self._require_state(state)
        bin_key = state_bin_key(state, self.history.binning)
        signature = state_signature(state, bin_key)
        depth = self.calibration.lookahead_depth

        def decide(kind, *, selected="", value=None, reason=None, tie="",
                   utilities=(), successors=(), legal=(), affordable=(),
                   denied=()):
            return MicroPlannerDecision(
                planner_version=PLANNER_VERSION, subject=state.subject,
                relation=state.relation, row_index=state.row_index,
                program_type=state.program_type, round_index=state.round_index,
                state_signature=signature, kind=kind,
                tau_continue=self.calibration.tau_continue, lookahead_depth=depth,
                history_version=self.history.history_version,
                calibration_version=self.calibration.calibration_version,
                legal_actions=tuple(legal), affordable_actions=tuple(affordable),
                denied_actions=tuple(denied), utilities=tuple(utilities),
                successors=tuple(successors), selected_action=selected,
                selected_value=value, stop_reason=reason, tie_break_reason=tie,
            )

        eligible, excluded = self._screen(state, legal_actions)
        legal_ids = tuple(sorted(a.action_id for a in eligible))
        if not eligible:
            return decide(DecisionKind.STOP, reason=StopReason.NO_LEGAL_ACTION,
                          denied=excluded)

        affordable, denied = self._affordable(state.budget_ledger, eligible)
        denied_all = tuple(excluded) + tuple(denied)
        affordable_ids = tuple(sorted(a.action_id for a in affordable))
        if not affordable:
            return decide(DecisionKind.STOP,
                          reason=StopReason.NO_AFFORDABLE_ACTION,
                          legal=legal_ids, denied=denied_all)

        breakdowns = self._score(state, affordable, bin_key=bin_key)
        successors: tuple[SuccessorDiagnostics, ...] = ()
        if depth == 2:
            values, successors = self._lookahead(
                state, affordable, breakdowns, bin_key=bin_key)
        else:
            values = {b.action_id: b.utility for b in breakdowns}

        # arg max, with a deterministic tie-break on canonical action identity:
        # insertion order, object id and hash order must never decide.
        by_id = {action.action_id: action for action in affordable}
        ranked = sorted(
            values.items(), key=lambda item: (-item[1], by_id[item[0]].identity))
        best_id, best_value = ranked[0]
        tied = [action_id for action_id, value in ranked if value == best_value]
        tie = (
            f"{len(tied)} actions share value {best_value}; the lowest canonical "
            f"identity wins" if len(tied) > 1 else ""
        )

        common = dict(
            utilities=breakdowns, successors=successors, legal=legal_ids,
            affordable=affordable_ids, denied=denied_all, tie=tie,
        )
        # §17: strictly greater. At exactly tau_continue, STOP.
        if best_value > self.calibration.tau_continue:
            return decide(DecisionKind.ACTION, selected=best_id, value=best_value,
                          **common)
        return decide(DecisionKind.STOP, value=best_value,
                      reason=StopReason.UTILITY_BELOW_THRESHOLD, **common)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


_ALLOWED_KEYS = {
    "enabled", "mode", "planner_version", "historical_bins", "planner_calibration",
}


class MicroPlannerConfig:
    """``micro_planner:`` block.

    Enabling the planner without TRAIN-calibrated history *and* coefficients
    fails loudly. §17 supplies no value for α, β, γ, δ, η, κ or τ_continue, and
    a planner running on invented coefficients would produce confident-looking
    decisions with nothing behind them.
    """

    def __init__(self, *, enabled: bool = False, mode: str = "shadow",
                 planner_version: str = PLANNER_VERSION,
                 historical_bins: str | None = None,
                 planner_calibration: str | None = None) -> None:
        if mode != "shadow":
            raise ValueError(
                f"unsupported micro_planner mode {mode!r}; only shadow exists "
                "in this milestone"
            )
        if planner_version != PLANNER_VERSION:
            raise ValueError(
                f"unsupported planner_version {planner_version!r}; this build "
                f"implements {PLANNER_VERSION}"
            )
        if enabled and not historical_bins:
            raise ValueError(
                "micro_planner.enabled is true but no historical_bins package "
                "is supplied; §17 states the estimates come from "
                "relation-specific historical bins on TRAIN, and none exist yet"
            )
        if enabled and not planner_calibration:
            raise ValueError(
                "micro_planner.enabled is true but no planner_calibration is "
                "supplied; §17 names alpha, beta, gamma, delta, eta, kappa and "
                "tau_continue and gives a value for none of them"
            )
        self.enabled = enabled
        self.mode = mode
        self.planner_version = planner_version
        self.historical_bins = historical_bins
        self.planner_calibration = planner_calibration

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "MicroPlannerConfig":
        payload = dict(payload or {})
        unknown = set(payload) - _ALLOWED_KEYS
        if unknown:
            raise ValueError(
                f"unknown micro_planner key(s) {sorted(unknown)}; allowed: "
                f"{sorted(_ALLOWED_KEYS)}"
            )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            planner_version=str(payload.get("planner_version", PLANNER_VERSION)),
            historical_bins=payload.get("historical_bins") or None,
            planner_calibration=payload.get("planner_calibration") or None,
        )


def load_planner_calibration(
    payload: Mapping[str, Any], *, allow_synthetic: bool = False
) -> PlannerCalibration:
    """Read coefficients, refusing a fixture where production is meant."""
    calibration = PlannerCalibration.from_json(payload)
    if not allow_synthetic and not calibration.source.is_production:
        raise PlannerError(
            f"planner calibration source is {calibration.source.value}, which is "
            "a test fixture and may not be used as production policy"
        )
    return calibration


def build_micro_planner(
    config: Mapping[str, Any] | None,
    history: HistoricalBinPackage | None = None,
    calibration: PlannerCalibration | None = None,
) -> MicroPlanner | None:
    """Construct the planner when configuration enables it."""
    if not config:
        return None
    parsed = MicroPlannerConfig.from_mapping(config)
    if not parsed.enabled:
        return None
    if history is None or calibration is None:
        raise PlannerError(
            "micro_planner is enabled but its TRAIN history and calibration "
            "were not loaded; no such package exists yet"
        )
    if calibration.source is EstimateSource.SYNTHETIC_TEST:
        raise PlannerError(
            "a SYNTHETIC_TEST planner calibration may not run outside tests"
        )
    return MicroPlanner(history, calibration,
                        planner_version=parsed.planner_version)


def core_action_family(action_type: Any) -> ActionFamily:
    """Adapt a core Module 7 action type onto a canonical planner family.

    An adapter, not a second action space: Module 7's vocabulary and the
    upgraded modules' richer identities both land on the same families, so one
    semantic action never competes with itself under two names.
    """
    value = getattr(action_type, "value", str(action_type))
    mapping = {
        "RUN_VIEW": ActionFamily.SPECIALIST_PROBE,
        "RUN_FACET": ActionFamily.SPECIALIST_PROBE,
        "VERIFY": ActionFamily.BLIND_VERIFY,
        "ADVERSARIAL_VERIFY": ActionFamily.COUNTERFACTUAL_VERIFY,
        "REVERSE_CHECK": ActionFamily.REVERSE_CHECK,
        "CROSS_MODEL_CHECK": ActionFamily.CROSS_MODEL_CHECK,
        "RESAMPLE": ActionFamily.RESAMPLE,
    }
    try:
        return mapping[value]
    except KeyError:
        raise PlannerError(
            f"unknown action family for core action type {value!r}"
        ) from None


__all__ = [
    "MicroPlanner",
    "MicroPlannerConfig",
    "build_micro_planner",
    "core_action_family",
    "load_planner_calibration",
    "state_signature",
    "utility",
]
