"""RCSE, the active controller, and relation-typed adaptive stopping.

All synthetic. No model is loaded anywhere in this file.
"""

from __future__ import annotations

import pytest

from cover_kbc.contracts.registry import get_contract
from cover_kbc.controller import (
    DEFAULT_CONTROLLER,
    Action,
    ActionType,
    ControllerConfig,
    choose_action,
    legal_actions,
    record_outcome,
    score_action,
    should_stop,
    snapshot_state,
)
from cover_kbc.coverage import (
    DEFAULT_RCSE,
    ActionOutcome,
    RCSEState,
    estimate_residual,
)
from cover_kbc.types import (
    Budget,
    Candidate,
    CandidateStatus,
    EdgeType,
    Evidence,
    IndependenceGroup,
    VerificationTier,
)


def make_candidate(key, *, support=1, status=CandidateStatus.UNRESOLVED, tier=None):
    candidate = Candidate(key=key, display_value=key.title(), relation="x")
    groups = [
        IndependenceGroup.DIRECT_RECALL,
        IndependenceGroup.STRUCTURAL_DECOMPOSITION,
        IndependenceGroup.CONTRASTIVE_SEPARATION,
    ]
    for i in range(support):
        candidate.add_evidence(Evidence(key, EdgeType.SUPPORT, groups[i], "v", "m", 0, f"r{i}"))
    candidate.status = status
    if tier is not None:
        candidate.tier = tier
    return candidate


# --- RCSE state signals ----------------------------------------------------


def test_saturation_is_zero_when_every_action_produced_value():
    state = RCSEState()
    for _ in range(3):
        state.record(ActionOutcome("RUN_VIEW", new_verified=2, generated_tokens=100))
    assert state.saturation(3) == pytest.approx(0.0)


def test_saturation_is_one_when_nothing_was_produced():
    state = RCSEState()
    for _ in range(3):
        state.record(ActionOutcome("RUN_VIEW", new_verified=0, generated_tokens=100))
    assert state.saturation(3) == pytest.approx(1.0)


def test_marginal_yield_is_token_normalised():
    state = RCSEState()
    state.record(ActionOutcome("RUN_VIEW", new_verified=2, generated_tokens=1000))
    assert state.marginal_yield(3) == pytest.approx(2.0)


def test_consecutive_no_gain_counts_the_recent_tail():
    state = RCSEState()
    state.record(ActionOutcome("RUN_VIEW", new_verified=1, generated_tokens=10))
    state.record(ActionOutcome("RUN_VIEW", new_verified=0, generated_tokens=10))
    state.record(ActionOutcome("RUN_VIEW", new_verified=0, generated_tokens=10))
    assert state.consecutive_no_gain() == 2


def test_set_stability_is_jaccard_of_the_last_two_sets():
    state = RCSEState()
    state.record_accepted(["a", "b"])
    state.record_accepted(["a", "b"])
    assert state.set_stability() == pytest.approx(1.0)
    state.record_accepted(["a", "c"])
    assert state.set_stability() == pytest.approx(1 / 3)


def test_stability_needs_two_observations():
    state = RCSEState()
    state.record_accepted(["a"])
    assert state.set_stability() == 0.0


# --- RCSE residual ---------------------------------------------------------


def test_residual_is_not_a_cardinality_estimate():
    """q_res is a need-to-continue signal bounded to [0, 1], not a set size."""
    contract = get_contract("awardWonBy")
    state = RCSEState()
    estimate = estimate_residual(contract, [], state)
    assert 0.0 <= estimate.residual <= 1.0
    assert "yield" in estimate.rationale or "facet" in estimate.rationale


def test_residual_falls_once_facets_are_covered_and_yield_stops():
    contract = get_contract("awardWonBy")
    fresh = estimate_residual(contract, [], RCSEState())

    exhausted = RCSEState()
    exhausted.covered_facets.update(contract.all_views())
    for _ in range(3):
        exhausted.record(ActionOutcome("RUN_FACET", new_verified=0, generated_tokens=500))
    after = estimate_residual(contract, [], exhausted)
    assert after.residual < fresh.residual


def test_residual_components_are_all_traceable():
    contract = get_contract("countryLandBordersCountry")
    estimate = estimate_residual(contract, [], RCSEState())
    for key in (
        "marginal_yield", "saturation", "unresolved_mass", "facet_gap",
        "verifier_disagreement", "set_instability", "consecutive_no_gain",
    ):
        assert key in estimate.components
    assert estimate.weights


def test_residual_is_relation_typed():
    numeric = estimate_residual(get_contract("hasArea"), [], RCSEState())
    awards = estimate_residual(get_contract("awardWonBy"), [], RCSEState())
    assert numeric.rationale != awards.rationale
    assert numeric.program_type == "NUMERIC"
    assert awards.program_type == "LARGE_OPEN_SET"


def test_unresolved_candidates_raise_the_residual():
    contract = get_contract("countryLandBordersCountry")
    state = RCSEState()
    state.covered_facets.update(contract.all_views())
    resolved = estimate_residual(
        contract, [make_candidate("a", status=CandidateStatus.ACCEPTED)], state
    )
    unresolved = estimate_residual(
        contract,
        [make_candidate("a", status=CandidateStatus.UNRESOLVED, tier=VerificationTier.VERIFY)],
        state,
    )
    assert unresolved.residual > resolved.residual


# --- action enumeration and scoring ---------------------------------------


def test_unrun_views_become_actions():
    contract = get_contract("countryLandBordersCountry")
    actions = legal_actions(contract, [], RCSEState(), Budget())
    view_ids = {a.view_id for a in actions if a.view_id}
    assert set(contract.mandatory_views) <= view_ids


def test_covered_views_are_not_offered_again():
    contract = get_contract("countryLandBordersCountry")
    state = RCSEState()
    state.covered_facets.add("borders_direct")
    actions = legal_actions(contract, [], state, Budget())
    assert "borders_direct" not in {a.view_id for a in actions}


def test_exhausted_budget_offers_only_stop():
    contract = get_contract("countryLandBordersCountry")
    budget = Budget(max_calls=1)
    budget.charge(calls=1)
    actions = legal_actions(contract, [], RCSEState(), budget)
    assert [a.action_type for a in actions] == [ActionType.STOP]


def test_uncertain_candidates_become_verification_actions():
    contract = get_contract("countryLandBordersCountry")
    candidates = [
        make_candidate("weak", tier=VerificationTier.VERIFY),
        make_candidate("conflict", tier=VerificationTier.ADVERSARIAL_VERIFY),
    ]
    types = {a.action_type for a in legal_actions(contract, candidates, RCSEState(), Budget())}
    assert ActionType.VERIFY in types
    assert ActionType.ADVERSARIAL_VERIFY in types


def test_cross_model_action_only_offered_when_a_second_model_exists():
    contract = get_contract("countryLandBordersCountry")
    candidates = [make_candidate("a")]
    without = legal_actions(contract, candidates, RCSEState(), Budget())
    with_second = legal_actions(
        contract, candidates, RCSEState(), Budget(), cross_model_available=True
    )
    assert ActionType.CROSS_MODEL_CHECK not in {a.action_type for a in without}
    assert ActionType.CROSS_MODEL_CHECK in {a.action_type for a in with_second}


def test_resampling_is_scored_as_redundant():
    """Repeating one mechanism is not corroboration, so it must not win."""
    contract = get_contract("countryLandBordersCountry")
    residual = estimate_residual(contract, [], RCSEState())
    resample, components = score_action(
        Action(ActionType.RESAMPLE), contract, [], RCSEState(), residual
    )
    fresh, _ = score_action(
        Action(ActionType.RUN_VIEW, view_id="borders_direct"),
        contract, [], RCSEState(), residual,
    )
    assert components["redundancy"] == 1.0
    assert resample < fresh


def test_mandatory_views_outrank_optional_facets():
    contract = get_contract("awardWonBy")
    residual = estimate_residual(contract, [], RCSEState())
    mandatory, _ = score_action(
        Action(ActionType.RUN_VIEW, view_id="award_direct"), contract, [], RCSEState(), residual
    )
    optional, _ = score_action(
        Action(ActionType.RUN_FACET, view_id="award_facet_category"),
        contract, [], RCSEState(), residual,
    )
    assert mandatory > optional


# --- decisions are auditable ----------------------------------------------


def test_every_decision_records_why_and_what_was_considered():
    contract = get_contract("countryLandBordersCountry")
    decision = choose_action(contract, [], RCSEState(), Budget(), step=0)
    payload = decision.to_json()
    assert payload["chosen"]["reason"]
    assert payload["considered"]
    assert payload["residual"]["components"]
    assert payload["state_before"]
    for entry in payload["considered"]:
        assert "score" in entry and "components" in entry


def test_decisions_are_deterministic():
    contract = get_contract("countryLandBordersCountry")
    a = choose_action(contract, [], RCSEState(), Budget(), step=0)
    b = choose_action(contract, [], RCSEState(), Budget(), step=0)
    assert a.chosen == b.chosen and a.score == pytest.approx(b.score)


def test_state_snapshot_reports_budget_and_coverage():
    state = RCSEState()
    state.covered_facets.add("borders_direct")
    budget = Budget(max_calls=5)
    budget.charge(calls=2, generated_tokens=100)
    snapshot = snapshot_state([make_candidate("a")], budget, state)
    assert snapshot["calls_used"] == 2 and snapshot["calls_left"] == 3
    assert snapshot["covered_views"] == ["borders_direct"]


def test_record_outcome_marks_the_view_covered():
    state = RCSEState()
    action = Action(ActionType.RUN_VIEW, view_id="borders_direct")
    record_outcome(
        state, action, new_verified=1, new_candidates=1, generated_tokens=50, accepted_keys=["a"]
    )
    assert "borders_direct" in state.covered_facets
    assert state.outcomes[-1].produced_value


# --- adaptive stopping, per programme type --------------------------------


def _covered(contract):
    state = RCSEState()
    state.covered_facets.update(contract.all_views())
    return state


def test_never_stops_before_mandatory_views_are_done():
    contract = get_contract("countryLandBordersCountry")
    state = RCSEState()
    residual = estimate_residual(contract, [], state)
    stop, reason = should_stop(contract, [], state, Budget(), residual)
    assert not stop and "mandatory" in reason


def test_hard_budget_always_overrides_continuation():
    contract = get_contract("awardWonBy")
    budget = Budget(max_calls=1)
    budget.charge(calls=1)
    residual = estimate_residual(contract, [], RCSEState())
    stop, reason = should_stop(contract, [], RCSEState(), budget, residual)
    assert stop and "budget" in reason


def test_small_set_stops_once_stable_with_nothing_unresolved():
    contract = get_contract("countryLandBordersCountry")
    state = _covered(contract)
    state.record_accepted(["a", "b"])
    state.record_accepted(["a", "b"])
    candidates = [make_candidate("a", status=CandidateStatus.ACCEPTED)]
    residual = estimate_residual(contract, candidates, state)
    stop, reason = should_stop(contract, candidates, state, Budget(), residual)
    assert stop and "small-set" in reason


def test_null_single_stops_on_one_accepted_locality():
    contract = get_contract("personHasCityOfDeath")
    state = _covered(contract)
    candidates = [make_candidate("testville", status=CandidateStatus.ACCEPTED)]
    residual = estimate_residual(contract, candidates, state)
    stop, reason = should_stop(contract, candidates, state, Budget(), residual)
    assert stop and "null-single" in reason


def test_large_open_set_stops_only_on_saturation_and_full_facet_coverage():
    contract = get_contract("awardWonBy")
    state = _covered(contract)
    for _ in range(3):
        state.record(ActionOutcome("RUN_FACET", new_verified=0, generated_tokens=400))
    state.record_accepted(["a"])
    state.record_accepted(["a"])
    candidates = [make_candidate("a", status=CandidateStatus.ACCEPTED)]
    residual = estimate_residual(contract, candidates, state)
    stop, reason = should_stop(contract, candidates, state, Budget(), residual)
    assert stop
    assert "large-open-set" in reason or "residual" in reason


def test_large_open_set_keeps_going_while_facets_remain():
    contract = get_contract("awardWonBy")
    state = RCSEState()
    state.covered_facets.update(contract.mandatory_views)
    state.record(ActionOutcome("RUN_FACET", new_verified=3, generated_tokens=400))
    candidates = [make_candidate("a", status=CandidateStatus.ACCEPTED)]
    residual = estimate_residual(contract, candidates, state)
    stop, _ = should_stop(contract, candidates, state, Budget(), residual)
    assert not stop


def test_stopping_thresholds_come_from_config():
    contract = get_contract("countryLandBordersCountry")
    state = _covered(contract)
    residual = estimate_residual(contract, [], state)
    eager = ControllerConfig(residual_stop=1.0)
    patient = ControllerConfig(residual_stop=0.0)
    assert should_stop(contract, [], state, Budget(), residual, eager)[0]
    assert not should_stop(contract, [], state, Budget(), residual, patient)[0]


def test_default_controller_config_is_serialisable():
    payload = DEFAULT_CONTROLLER.to_json()
    assert payload["residual_stop"] == DEFAULT_RCSE.stop_threshold
    assert "rcse" in payload
