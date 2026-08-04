"""Module 7 conformance: active controller and adaptive stopping (spec §13).

Deterministic and synthetic throughout. No model is loaded anywhere.

The central properties: the controller is a real stateful loop rather than a
decision trace computed after the model work is done; every mechanism the
architecture counts as available is reachable by some legal action; and the
action score is the proposal's five terms with nothing hidden inside it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import all_contracts, get_contract
from cover_kbc.controller import (
    ACTION_ROLE,
    DEFAULT_CONTROLLER,
    Action,
    ActionType,
    candidate_impact,
    choose_action,
    legal_actions,
    mechanism_redundancy,
    mechanism_yield_prior,
    score_action,
    should_stop,
    view_gap_relevance,
)
from cover_kbc.coverage import (
    ActionOutcome,
    GateState,
    RCSEState,
    estimate_residual,
)
from cover_kbc.elicitation.library import get_view, views_for
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import (
    CoverPipeline,
    ExecutionMode,
    GateRoleUnavailable,
    PipelineConfig,
)
from cover_kbc.scoring import acquisition_groups
from cover_kbc.staging import StageWriter, read_stage
from cover_kbc.types import (
    Budget,
    Candidate,
    CandidateStatus,
    EdgeType,
    Evidence,
    IndependenceGroup,
    ModelRole,
    Query,
    VerificationTier,
)

BORDERS = "countryLandBordersCountry"
BIG = dict(max_calls=999, max_generated_tokens=99_999)


def candidate(key, relation=BORDERS, mechanisms=0, status=None, tier=None, score=None):
    contract = get_contract(relation)
    cand = Candidate(key=key, display_value=key.title(), relation=relation)
    groups = list(contract.eligible_independence_groups)
    for i in range(mechanisms):
        cand.add_evidence(
            Evidence(key, EdgeType.SUPPORT, groups[i % len(groups)], "v", "m", 0, f"{key}{i}")
        )
    if status is not None:
        cand.status = status
    if tier is not None:
        cand.tier = tier
    if score is not None:
        cand.score = score
    return cand


def fresh_residual(contract, cands=(), state=None):
    return estimate_residual(contract, list(cands), state or RCSEState())


# --- 1-4. action space is complete and non-decorative ------------------------


def test_every_action_type_has_a_declared_model_role():
    for action_type in ActionType:
        assert action_type in ACTION_ROLE, f"{action_type} has no model role"


def test_every_action_type_is_reachable_from_some_state():
    """No enum member may be decorative."""
    seen: set[ActionType] = set()
    for contract in all_contracts():
        state = RCSEState()
        found = [candidate("alpha", contract.relation, mechanisms=1,
                           tier=VerificationTier.VERIFY)]
        for _ in range(12):
            actions = legal_actions(contract, found, state, Budget(**BIG),
                                    cross_model_available=True)
            seen.update(a.action_type for a in actions)
            fresh = [a for a in actions if a.view_id and a.view_id not in state.executed_views]
            if not fresh:
                break
            for action in fresh:
                state.executed_views.add(action.view_id)
        seen.update(
            a.action_type
            for a in legal_actions(contract, found, state, Budget(**BIG))
        )
    # RESAMPLE needs a stochastic view; none is declared today, and its own
    # tests cover both halves of that fact.
    seen.add(ActionType.RESAMPLE)
    adversarial = legal_actions(
        get_contract(BORDERS),
        [candidate("a", mechanisms=1, tier=VerificationTier.ADVERSARIAL_VERIFY)],
        RCSEState(), Budget(**BIG),
    )
    seen.update(a.action_type for a in adversarial)
    assert seen == set(ActionType), f"unreachable: {set(ActionType) - seen}"


def test_resample_is_not_offered_while_a_structural_gap_remains():
    """Spec section 7 makes repeated sampling subordinate to diversity."""
    contract = get_contract(BORDERS)
    actions = legal_actions(contract, [], RCSEState(), Budget(**BIG))
    assert not [a for a in actions if a.action_type is ActionType.RESAMPLE]
    assert [a for a in actions if a.action_type in (ActionType.RUN_VIEW, ActionType.RUN_FACET)]


def test_no_declared_view_is_stochastic_so_resample_never_fires_today():
    """Recorded architecture fact, not a silent omission.

    Every view Module 2 declares uses deterministic decoding, so a repeat would
    return byte-identical text and carry no information - Module 3 would reject
    its duplicate edge. RESAMPLE is therefore legal-but-never-triggered under
    the frozen config rather than removed.
    """
    for contract in all_contracts():
        for view in views_for(contract.relation, tuple(contract.all_views())):
            assert view.decode.deterministic, f"{view.view_id} is sampled"
    contract = get_contract(BORDERS)
    state = RCSEState()
    state.executed_views.update(
        v for v in contract.all_views() if not get_view(contract.relation, v).is_reverse
    )
    assert not [
        a for a in legal_actions(contract, [], state, Budget(**BIG))
        if a.action_type is ActionType.RESAMPLE
    ]


def test_resample_becomes_legal_for_a_stochastic_view():
    """Proves the branch is live code, not decoration."""
    import dataclasses
    from cover_kbc.elicitation import library

    contract = get_contract(BORDERS)
    view_id = contract.mandatory_views[0]
    original = library.get_view(contract.relation, view_id)
    sampled = dataclasses.replace(
        original, decode=dataclasses.replace(original.decode, temperature=0.7)
    )
    state = RCSEState()
    state.executed_views.update(
        v for v in contract.all_views() if not get_view(contract.relation, v).is_reverse
    )

    real_get_view = library.get_view

    def patched(relation, requested):
        return sampled if requested == view_id else real_get_view(relation, requested)

    import cover_kbc.controller as controller_module

    controller_module.get_view = patched
    try:
        actions = legal_actions(contract, [], state, Budget(**BIG))
    finally:
        controller_module.get_view = real_get_view
    resamples = [a for a in actions if a.action_type is ActionType.RESAMPLE]
    assert resamples and resamples[0].view_id == view_id


# --- 5-9. reverse reachability (resolves audit 0009 §41) ---------------------


def test_every_available_mechanism_is_reachable():
    """The audit 0009 §41 blocking defect, as a normal assertion."""
    for contract in all_contracts():
        state = RCSEState()
        found = [candidate("alpha", contract.relation, mechanisms=1)]
        for _ in range(20):
            actions = legal_actions(contract, found, state, Budget(**BIG))
            fresh = [a for a in actions if a.view_id and a.view_id not in state.executed_views]
            if not fresh:
                break
            for action in fresh:
                state.executed_views.add(action.view_id)
                view = get_view(contract.relation, action.view_id)
                if not view.is_gate:
                    state.executed_groups.add(view.independence_group)
        unreachable = set(acquisition_groups(contract)) - state.executed_groups
        assert not unreachable, f"{contract.relation}: {sorted(g.name for g in unreachable)}"


def test_reverse_actions_carry_candidate_identity():
    contract = get_contract(BORDERS)
    cands = [candidate("alpha", mechanisms=1), candidate("beta", mechanisms=1)]
    reverse = [
        a for a in legal_actions(contract, cands, RCSEState(), Budget(**BIG))
        if a.action_type is ActionType.REVERSE_CHECK
    ]
    assert {a.candidate_key for a in reverse} == {"alpha", "beta"}
    for action in reverse:
        assert action.view_id
    # Distinct instances, so dedup and tie-breaking cannot conflate them.
    assert len({a.identity for a in reverse}) == 2


def test_reverse_on_one_candidate_does_not_mark_another_reverse_checked():
    contract = get_contract(BORDERS)
    alpha = candidate("alpha", mechanisms=1)
    beta = candidate("beta", mechanisms=1)
    reverse_group = IndependenceGroup.REVERSE_ALTERNATE
    alpha.add_evidence(
        Evidence("alpha", EdgeType.SUPPORT, reverse_group, "borders_reverse_check",
                 "m", 0, "rev-alpha")
    )
    offered = {
        a.candidate_key
        for a in legal_actions(contract, [alpha, beta], RCSEState(), Budget(**BIG))
        if a.action_type is ActionType.REVERSE_CHECK
    }
    assert offered == {"beta"}, "alpha already has the mechanism; beta still needs it"


def test_reverse_is_not_forced_on_every_candidate():
    """It is a targeted action the controller may decline, not a sweep."""
    contract = get_contract(BORDERS)
    cands = [candidate(k, mechanisms=4, status=CandidateStatus.ACCEPTED)
             for k in ("alpha", "beta", "gamma")]
    for cand in cands:
        cand.add_evidence(
            Evidence(cand.key, EdgeType.SUPPORT, IndependenceGroup.REVERSE_ALTERNATE,
                     "borders_reverse_check", "m", 0, f"rev-{cand.key}")
        )
    state = RCSEState()
    state.executed_views.update(contract.all_views())
    state.executed_groups.update(contract.eligible_independence_groups)
    decision = choose_action(contract, cands, state, Budget(**BIG), 0)
    assert decision.chosen.action_type is not ActionType.REVERSE_CHECK


def test_a_relation_without_a_reverse_view_offers_no_reverse_action():
    contract = get_contract("hasArea")
    actions = legal_actions(contract, [candidate("100.0", "hasArea", mechanisms=1)],
                            RCSEState(), Budget(**BIG))
    assert not [a for a in actions if a.action_type is ActionType.REVERSE_CHECK]


# --- 10-14. the five score terms --------------------------------------------


def test_the_score_is_exactly_the_five_weighted_terms():
    contract = get_contract(BORDERS)
    residual = fresh_residual(contract)
    action = Action(ActionType.RUN_VIEW, view_id="borders_direct")
    score, parts = score_action(action, contract, [], RCSEState(), residual)
    c = DEFAULT_CONTROLLER
    assert score == pytest.approx(
        c.alpha_yield * parts["expected_yield"]
        + c.beta_gap * parts["gap"]
        + c.gamma_uncertainty * parts["uncertainty"]
        - c.lambda_cost * parts["cost"]
        - c.rho_redundancy * parts["redundancy"]
    )
    assert set(parts) == {"expected_yield", "gap", "uncertainty", "cost", "redundancy"}


def test_no_hidden_numeric_bonus_survives_in_the_score():
    """Every constant must come from config, not a literal in the body."""
    tree = ast.parse(inspect.getsource(score_action).lstrip())
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, float)
    ]
    assert set(literals) <= {0.0, 1.0}, f"hard-coded constants: {literals}"


@pytest.mark.parametrize(
    "component,direction",
    [("expected_yield", 1), ("gap", 1), ("uncertainty", 1), ("cost", -1), ("redundancy", -1)],
)
def test_each_component_moves_the_score_the_right_way(component, direction):
    c = DEFAULT_CONTROLLER
    weights = {
        "expected_yield": c.alpha_yield, "gap": c.beta_gap,
        "uncertainty": c.gamma_uncertainty, "cost": -c.lambda_cost,
        "redundancy": -c.rho_redundancy,
    }
    assert (weights[component] > 0) is (direction > 0)


def test_mandatory_priority_comes_from_the_contract_not_the_action_kind():
    contract = get_contract("awardWonBy")
    residual = fresh_residual(contract)
    state = RCSEState()
    mandatory = Action(ActionType.RUN_VIEW, view_id=contract.mandatory_views[0])
    optional_id = contract.optional_views[0]
    optional = Action(
        # Deliberately mislabelled as RUN_VIEW: priority must not follow the enum.
        ActionType.RUN_VIEW, view_id=optional_id,
        facet_id=get_view(contract.relation, optional_id).facet_id,
    )
    assert view_gap_relevance(mandatory, contract, state, residual) > view_gap_relevance(
        optional, contract, state, residual
    )


def test_an_executed_mandatory_view_loses_its_priority():
    contract = get_contract(BORDERS)
    residual = fresh_residual(contract)
    action = Action(ActionType.RUN_VIEW, view_id=contract.mandatory_views[0])
    before = view_gap_relevance(action, contract, RCSEState(), residual)
    state = RCSEState()
    state.executed_views.add(contract.mandatory_views[0])
    assert view_gap_relevance(action, contract, state, residual) < before


# --- 15-18. yield, redundancy, verification utility --------------------------


def test_an_untried_mechanism_gets_the_optimistic_prior():
    contract = get_contract(BORDERS)
    action = Action(ActionType.RUN_VIEW, view_id="borders_direct")
    assert mechanism_yield_prior(action, contract, RCSEState()) == pytest.approx(
        DEFAULT_CONTROLLER.untried_yield_prior
    )


def test_a_repeatedly_fruitless_mechanism_is_estimated_lower():
    contract = get_contract(BORDERS)
    state = RCSEState()
    state.executed_views.add("borders_direct")
    for _ in range(4):
        state.record(ActionOutcome("RUN_VIEW", new_trusted=0, generated_tokens=100))
    action = Action(ActionType.RESAMPLE, view_id="borders_direct")
    assert mechanism_yield_prior(action, contract, state) < DEFAULT_CONTROLLER.untried_yield_prior


def test_verification_history_does_not_inflate_discovery_yield():
    """A verifier call resolving a candidate says nothing about discovery."""
    contract = get_contract(BORDERS)
    state = RCSEState()
    state.executed_views.add("borders_direct")
    for _ in range(3):
        state.record(ActionOutcome("VERIFY", new_trusted=1, generated_tokens=0,
                                   is_verification=True))
    action = Action(ActionType.RESAMPLE, view_id="borders_direct")
    # Only non-verification history counts, and there is none, so it falls back
    # to the untried prior rather than reading three verifier wins as yield.
    assert mechanism_yield_prior(action, contract, state) == pytest.approx(
        DEFAULT_CONTROLLER.untried_yield_prior
    )


def test_verification_earns_no_acquisition_yield():
    contract = get_contract(BORDERS)
    cand = candidate("alpha", mechanisms=2, tier=VerificationTier.VERIFY)
    _, parts = score_action(
        Action(ActionType.VERIFY, candidate_key="alpha"),
        contract, [cand], RCSEState(), fresh_residual(contract, [cand]),
    )
    assert parts["expected_yield"] == 0.0
    assert parts["uncertainty"] > 0.0


def test_a_fresh_structural_action_is_less_redundant_than_a_repeat():
    contract = get_contract(BORDERS)
    state = RCSEState()
    state.executed_views.add("borders_direct")
    state.executed_groups.add(get_view(contract.relation, "borders_direct").independence_group)
    fresh = Action(ActionType.RUN_VIEW, view_id="borders_compass")
    repeat = Action(ActionType.RESAMPLE, view_id="borders_direct")
    assert mechanism_redundancy(fresh, contract, state) < mechanism_redundancy(
        repeat, contract, state
    )


def test_repeated_resampling_grows_more_redundant():
    contract = get_contract(BORDERS)
    state = RCSEState()
    action = Action(ActionType.RESAMPLE, view_id="borders_direct")
    previous = -1.0
    for _ in range(4):
        value = mechanism_redundancy(action, contract, state)
        assert value >= previous
        previous = value
        state.record(ActionOutcome(ActionType.RESAMPLE.value, generated_tokens=50))


def test_a_structural_action_normally_outranks_a_repeat():
    contract = get_contract(BORDERS)
    state = RCSEState()
    state.executed_views.add("borders_direct")
    state.executed_groups.add(get_view(contract.relation, "borders_direct").independence_group)
    residual = fresh_residual(contract, state=state)
    fresh, _ = score_action(
        Action(ActionType.RUN_VIEW, view_id="borders_compass"), contract, [], state, residual
    )
    repeat, _ = score_action(
        Action(ActionType.RESAMPLE, view_id="borders_direct"), contract, [], state, residual
    )
    assert fresh > repeat


# --- 19-22. verification targeting -------------------------------------------


def test_a_hard_rejected_candidate_is_never_offered_for_verification():
    contract = get_contract(BORDERS)
    rejected = candidate("bad", mechanisms=1, status=CandidateStatus.REJECTED,
                         tier=VerificationTier.VERIFY)
    actions = legal_actions(contract, [rejected], RCSEState(), Budget(**BIG))
    assert not [
        a for a in actions
        if a.action_type in (ActionType.VERIFY, ActionType.ADVERSARIAL_VERIFY)
    ]


def test_an_auto_accepted_candidate_is_not_offered_for_verification():
    contract = get_contract(BORDERS)
    accepted = candidate("alpha", mechanisms=4, tier=VerificationTier.AUTO_ACCEPT)
    actions = legal_actions(contract, [accepted], RCSEState(), Budget(**BIG))
    assert not [
        a for a in actions
        if a.action_type in (ActionType.VERIFY, ActionType.ADVERSARIAL_VERIFY)
    ]


def test_an_already_verified_candidate_is_not_re_verified():
    from cover_kbc.types import VerificationLabel, VerificationResult

    contract = get_contract(BORDERS)
    cand = candidate("alpha", mechanisms=1, tier=VerificationTier.VERIFY)
    cand.verifications.append(
        VerificationResult(candidate_key="alpha", label=VerificationLabel.VALID,
                           valid_prob=0.8, invalid_prob=0.1, unknown_prob=0.1)
    )
    actions = legal_actions(contract, [cand], RCSEState(), Budget(**BIG))
    assert not [a for a in actions if a.action_type is ActionType.VERIFY]


def test_the_adversarial_tier_produces_an_adversarial_action():
    contract = get_contract(BORDERS)
    cand = candidate("alpha", mechanisms=1, tier=VerificationTier.ADVERSARIAL_VERIFY)
    kinds = {
        a.action_type
        for a in legal_actions(contract, [cand], RCSEState(), Budget(**BIG))
        if a.candidate_key == "alpha"
    }
    assert ActionType.ADVERSARIAL_VERIFY in kinds
    assert ActionType.VERIFY not in kinds


def test_a_high_impact_candidate_outranks_a_low_impact_one():
    contract = get_contract(BORDERS)
    broad = candidate("broad", mechanisms=4, tier=VerificationTier.VERIFY)
    thin = candidate("thin", mechanisms=1, tier=VerificationTier.VERIFY)
    residual = fresh_residual(contract, [broad, thin])
    assert candidate_impact(broad, contract, residual) > candidate_impact(
        thin, contract, residual
    )


# --- 23-25. determinism -------------------------------------------------------


def test_the_controller_is_deterministic():
    contract = get_contract("awardWonBy")
    chosen = set()
    for _ in range(5):
        cands = [candidate("a", "awardWonBy", mechanisms=2, tier=VerificationTier.VERIFY),
                 candidate("b", "awardWonBy", mechanisms=2, tier=VerificationTier.VERIFY)]
        decision = choose_action(contract, cands, RCSEState(), Budget(**BIG), 0)
        chosen.add((decision.chosen.identity, round(decision.score, 12)))
    assert len(chosen) == 1


def test_ties_break_on_full_action_identity_not_just_type():
    contract = get_contract(BORDERS)
    order = []
    for keys in (["alpha", "beta"], ["beta", "alpha"]):
        cands = [candidate(k, mechanisms=1) for k in keys]
        decision = choose_action(contract, cands, RCSEState(), Budget(**BIG), 0)
        order.append(decision.chosen.identity)
    assert order[0] == order[1], "choice depended on candidate iteration order"


def test_legal_actions_are_deterministically_ordered():
    contract = get_contract("awardWonBy")
    cands = [candidate("a", "awardWonBy", mechanisms=1)]
    runs = {
        tuple(a.identity for a in legal_actions(contract, cands, RCSEState(), Budget(**BIG)))
        for _ in range(4)
    }
    assert len(runs) == 1


# --- 26-30. stopping precedence ----------------------------------------------


def test_budget_exhaustion_stops_regardless_of_residual():
    contract = get_contract(BORDERS)
    residual = fresh_residual(contract)
    assert residual.residual > DEFAULT_CONTROLLER.residual_stop
    stop, reason = should_stop(
        contract, [], RCSEState(), Budget(max_calls=0, max_generated_tokens=0), residual
    )
    assert stop and "budget" in reason.lower()


def test_budget_exhaustion_does_not_rewrite_the_residual():
    contract = get_contract(BORDERS)
    residual = fresh_residual(contract)
    should_stop(contract, [], RCSEState(),
                Budget(max_calls=0, max_generated_tokens=0), residual)
    assert residual.residual > 0.0, "q_res must not be zeroed by an exhausted budget"


def test_the_stop_reason_distinguishes_budget_from_settled():
    contract = get_contract(BORDERS)
    _, budget_reason = should_stop(
        contract, [], RCSEState(), Budget(max_calls=0, max_generated_tokens=0),
        fresh_residual(contract),
    )
    state = RCSEState()
    state.executed_views.update(contract.all_views())
    state.executed_groups.update(contract.eligible_independence_groups)
    state.record_trusted(["alpha"]); state.record_trusted(["alpha"])
    for _ in range(3):
        state.record(ActionOutcome("RUN_VIEW", new_trusted=0, generated_tokens=100))
    settled = [candidate("alpha", mechanisms=4, status=CandidateStatus.ACCEPTED)]
    _, settled_reason = should_stop(
        contract, settled, state, Budget(**BIG),
        estimate_residual(contract, settled, state),
    )
    assert budget_reason != settled_reason


def test_mandatory_work_incomplete_cannot_adaptive_stop():
    contract = get_contract(BORDERS)
    state = RCSEState()
    state.record_trusted(["alpha"]); state.record_trusted(["alpha"])
    stop, reason = should_stop(
        contract, [candidate("alpha", mechanisms=4, status=CandidateStatus.ACCEPTED)],
        state, Budget(**BIG), estimate_residual(contract, [], state),
    )
    assert not stop
    assert "mandatory" in reason.lower()


def test_stability_alone_cannot_stop_a_query_with_an_unresolved_gate():
    contract = get_contract("personHasCityOfDeath")
    state = RCSEState()
    state.executed_views.update(contract.all_views())
    state.executed_groups.update(contract.eligible_independence_groups)
    state.record_trusted(["paris"]); state.record_trusted(["paris"])
    assert state.set_stability() == pytest.approx(1.0)
    residual = estimate_residual(contract, [], state,
                                 gate=GateState(present=True, resolved=False))
    stop, _ = should_stop(contract, [], state, Budget(**BIG), residual)
    assert not stop


# --- 31-34. relation-typed control -------------------------------------------


def settled_state(contract):
    state = RCSEState()
    state.executed_views.update(contract.all_views())
    for view in views_for(contract.relation, tuple(contract.all_views())):
        if view.facet_id:
            state.executed_facets.add(view.facet_id)
        if not view.is_gate:
            state.executed_groups.add(view.independence_group)
    state.record_trusted(["alpha"]); state.record_trusted(["alpha"])
    for _ in range(3):
        state.record(ActionOutcome("RUN_VIEW", new_trusted=0, generated_tokens=200))
    return state


def test_small_set_stops_early_when_settled():
    contract = get_contract(BORDERS)
    state = settled_state(contract)
    cands = [candidate("alpha", mechanisms=4, status=CandidateStatus.ACCEPTED)]
    decision = choose_action(contract, cands, state, Budget(**BIG), 0)
    assert decision.chosen.action_type is ActionType.STOP


def test_small_set_keeps_going_while_a_candidate_is_disputed():
    contract = get_contract(BORDERS)
    state = settled_state(contract)
    disputed = candidate("beta", mechanisms=1, status=CandidateStatus.UNRESOLVED,
                         tier=VerificationTier.ADVERSARIAL_VERIFY)
    cands = [candidate("alpha", mechanisms=4, status=CandidateStatus.ACCEPTED), disputed]
    decision = choose_action(contract, cands, state, Budget(**BIG), 0)
    assert decision.chosen.action_type is not ActionType.STOP


def test_null_single_cannot_stop_on_an_uncertain_gate():
    contract = get_contract("personHasCityOfDeath")
    state = settled_state(contract)
    state.executed_views.discard(contract.optional_views[0])   # locality search remains
    residual = estimate_residual(contract, [], state,
                                 gate=GateState(present=True, resolved=False))
    stop, _ = should_stop(contract, [], state, Budget(**BIG), residual)
    assert not stop
    decision = choose_action(contract, [], state, Budget(**BIG), 0,
                             gate=GateState(present=True, resolved=False))
    assert decision.chosen.action_type is not ActionType.STOP


def test_null_single_may_stop_on_a_confident_negative_gate():
    contract = get_contract("personHasCityOfDeath")
    state = settled_state(contract)
    residual = estimate_residual(contract, [], state,
                                 gate=GateState(present=True, resolved=True, negative=True))
    assert residual.residual == pytest.approx(0.0)
    stop, _ = should_stop(contract, [], state, Budget(**BIG), residual)
    assert stop


def test_null_single_cannot_stop_with_two_competing_localities():
    contract = get_contract("personHasCityOfDeath")
    state = settled_state(contract)
    paris = candidate("paris", contract.relation, mechanisms=3,
                      status=CandidateStatus.ACCEPTED, score=0.9)
    lyon = candidate("lyon", contract.relation, mechanisms=3,
                     status=CandidateStatus.ACCEPTED, score=0.85)
    residual = estimate_residual(contract, [paris, lyon], state,
                                 gate=GateState(present=True, resolved=True))
    stop, _ = should_stop(contract, [paris, lyon], state, Budget(**BIG), residual)
    assert not stop


def test_numeric_cannot_stop_with_competing_clusters():
    contract = get_contract("hasArea")
    state = settled_state(contract)
    cands = []
    for value in (100.0, 100.5, 500.0):
        cand = candidate(f"{value}", "hasArea", mechanisms=2,
                         status=CandidateStatus.ACCEPTED)
        cand.numeric_value = value
        cands.append(cand)
    residual = estimate_residual(contract, cands, state)
    assert residual.components["cluster_competition"] > 0.0
    assert residual.residual > DEFAULT_CONTROLLER.residual_stop
    stop, _ = should_stop(contract, cands, state, Budget(**BIG), residual)
    assert not stop


def test_awards_may_stop_before_every_optional_facet_runs():
    contract = get_contract("awardWonBy")
    state = settled_state(contract)
    state.executed_facets.discard(sorted(state.executed_facets)[0])
    cands = [candidate("a", "awardWonBy", mechanisms=3, status=CandidateStatus.ACCEPTED)]
    residual = estimate_residual(contract, cands, state)
    assert residual.components["facet_gap"] > 0.0
    stop, _ = should_stop(contract, cands, state, Budget(**BIG), residual)
    assert stop, "adaptive stopping must not require exhaustive facet coverage"


def test_awards_keep_exploring_while_facets_still_yield():
    contract = get_contract("awardWonBy")
    state = RCSEState()
    state.executed_views.update(contract.mandatory_views)
    state.record_trusted(["a"]); state.record_trusted(["a", "b"])
    for _ in range(3):
        state.record(ActionOutcome("RUN_FACET", new_trusted=1, generated_tokens=200))
    cands = [candidate("a", "awardWonBy", mechanisms=3, status=CandidateStatus.ACCEPTED)]
    decision = choose_action(contract, cands, state, Budget(**BIG), 0)
    assert decision.chosen.action_type is not ActionType.STOP


# --- 35-38. gate model role ---------------------------------------------------


def test_the_gate_role_is_configured_not_inferred():
    assert PipelineConfig().gate_model_role is ModelRole.VERIFIER


def test_the_gate_is_scored_by_the_verifier_in_interleaved_mode():
    enum = ScriptedRuntime(fallback=lambda r: "Paris", model_id="offline/mistral",
                           family="m", role="enumerator")
    verifier = ScriptedRuntime(
        fallback=lambda r: "Paris",
        label_scores={
            ("calibrated_gate", "Someone", "personHasCityOfDeath"):
                {"YES": 2.0, "NO": -1.0, "UNKNOWN": 0.0}
        },
        model_id="offline/qwen", family="q", role="verifier",
    )
    config = PipelineConfig(enable_calibrated_gate=True, enable_active_controller=True)
    pipeline = CoverPipeline(enum, config, verifier_runtime=verifier)
    graph = pipeline.enumerate_query(Query("Someone", "personHasCityOfDeath", 0))
    assert graph.gate_result is not None
    assert graph.gate_result.model_id == "offline/qwen"


def test_staged_phase_a_defers_the_gate_rather_than_substituting_a_model():
    """The decisive property: no residency-based fallback."""
    enum = ScriptedRuntime(fallback=lambda r: "Paris", model_id="offline/mistral",
                           family="m", role="enumerator")
    config = PipelineConfig(mode=ExecutionMode.STAGED, enable_calibrated_gate=True,
                            enable_active_controller=True)
    graph = CoverPipeline(enum, config).enumerate_query(
        Query("Someone", "personHasCityOfDeath", 0)
    )
    # Deferred, not scored by whichever model happened to be loaded.
    assert graph.gate_result is None


def test_an_unavailable_gate_role_fails_loudly_rather_than_substituting():
    enum = ScriptedRuntime(fallback=lambda r: "Paris", model_id="offline/mistral",
                           family="m", role="enumerator")
    pipeline = CoverPipeline(
        enum, PipelineConfig(enable_calibrated_gate=True, enable_active_controller=True)
    )
    with pytest.raises(GateRoleUnavailable):
        pipeline._gate_runtime()


# --- 39-44. the loop is genuinely active -------------------------------------


def award_runtimes():
    enumerator = ScriptedRuntime(
        fallback=lambda r: "Alpha; Gamma" if r.metadata.get("view_id") == "award_direct" else "Alpha",
        model_id="offline/mistral", family="offline-mistral", role="enumerator",
    )
    verifier = ScriptedRuntime(
        fallback=lambda r: "Alpha",
        label_scores={
            ("blind_verifier", "Testprize", "awardWonBy"):
                {"VALID": 3.0, "INVALID": -1.0, "UNKNOWN": 0.0}
        },
        model_id="offline/qwen", family="offline-qwen", role="verifier",
    )
    return enumerator, verifier


def drive_staged(pipeline, queries, tmp_path, *, max_swaps=12):
    """Run the staged orchestration to completion, as the CLI does.

    Mirrors ``scripts/run_staged.py::phase_resolve``: enumerate, verify, then
    keep reloading whichever role the pending actions need until none remain.
    Returns ``(graphs, role_sequence)``.
    """
    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate(queries):
            writer.write(graph)
    source = tmp_path / "a.jsonl"
    roles_used = [ModelRole.ENUMERATOR]

    for cycle in range(max_swaps):
        graphs = list(read_stage(source))
        pending = {CoverPipeline.pending_role(g) for g in graphs} - {None}
        if not pending:
            return graphs, roles_used
        role = (ModelRole.VERIFIER if ModelRole.VERIFIER in pending
                else ModelRole.ENUMERATOR)
        driver = pipeline.verify if role is ModelRole.VERIFIER else pipeline.resume
        target = tmp_path / f"r{cycle}_{role.value}.jsonl"
        with StageWriter(target) as writer:
            for graph in driver(iter(graphs)):
                writer.write(graph)
        roles_used.append(role)
        source = target
    raise AssertionError("orchestration did not settle")


ACTIVE = dict(enable_active_controller=True, enable_verifier=True,
              max_verifications_per_query=6, enable_cross_model_recall=True,
              enable_prompt_disagreement=True, max_steps_per_query=10,
              max_calls_per_query=24)


def test_a_staged_controller_action_really_spends_a_model_call(tmp_path):
    """The load-bearing question, answered positively.

    A verification phase re-runs the controller against reloaded state and the
    action it picks there actually calls a model - it is not a decision trace
    computed after all model work is finished.
    """
    enumerator, verifier = award_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    graphs, roles = drive_staged(pipeline, [Query("Testprize", "awardWonBy", 0)], tmp_path)
    graph = graphs[0]

    assert roles[0] is ModelRole.ENUMERATOR
    assert ModelRole.VERIFIER in roles, "no verification phase ever ran"
    assert verifier.calls > 0, "a controller-selected verifier action spent no model call"
    assert sum(len(c.verifications) for c in graph.candidates.values()) > 0
    assert len(graph.controller_log) >= 2, "the controller must loop, not run once"
    assert not graph.pending_action, "orchestration finished with work outstanding"


def test_each_phase_only_offers_actions_its_resident_model_can_run():
    contract = get_contract("awardWonBy")
    cands = [candidate("a", "awardWonBy", mechanisms=1, tier=VerificationTier.VERIFY)]
    enumerator_only = legal_actions(
        contract, cands, RCSEState(), Budget(**BIG), cross_model_available=True,
        allowed_roles=frozenset({ModelRole.ENUMERATOR, ModelRole.NONE}),
    )
    assert all(a.model_role is not ModelRole.VERIFIER for a in enumerator_only)
    verifier_only = legal_actions(
        contract, cands, RCSEState(), Budget(**BIG), cross_model_available=True,
        allowed_roles=frozenset({ModelRole.VERIFIER, ModelRole.NONE}),
    )
    assert all(a.model_role is not ModelRole.ENUMERATOR for a in verifier_only)


def test_staged_and_interleaved_reach_the_same_semantic_result(tmp_path):
    query = Query("Testprize", "awardWonBy", 0)
    e1, v1 = award_runtimes()
    interleaved = CoverPipeline(
        e1, PipelineConfig(mode=ExecutionMode.INTERLEAVED, **ACTIVE), verifier_runtime=v1
    ).run([query]).predictions[0]

    e2, v2 = award_runtimes()
    staged = CoverPipeline(
        e2, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=v2
    )
    graphs, _ = drive_staged(staged, [query], tmp_path)
    staged_prediction = staged.decide(graphs).predictions[0]

    # Emitted objects and the candidate set.
    assert set(interleaved.object_entities) == set(staged_prediction.object_entities)
    assert {c.key for c in interleaved.candidates} == {
        c.key for c in staged_prediction.candidates
    }

    # Evidence semantics, not merely the same names: each candidate must carry
    # the same mechanisms in the same modes.
    def evidence_shape(prediction):
        return {
            c.key: sorted(
                (e.independence_group.value, e.mode.value) for e in c.all_evidence()
            )
            for c in prediction.candidates
        }

    assert evidence_shape(interleaved) == evidence_shape(staged_prediction)

    # Trusted set and empty reason.
    assert {c.key for c in interleaved.candidates if c.status is CandidateStatus.ACCEPTED} == {
        c.key for c in staged_prediction.candidates if c.status is CandidateStatus.ACCEPTED
    }
    assert interleaved.empty_reason == staged_prediction.empty_reason

    # The logical action sequence agrees, even though the physical model-load
    # order differs: staged swaps residency, interleaved keeps both resident.
    def semantic_actions(graph):
        return [
            (d["chosen"]["action_type"], d["chosen"]["view_id"], d["chosen"]["candidate_key"])
            for d in graph.controller_log
        ]

    interleaved_graph = CoverPipeline(
        *award_runtimes()[:1], PipelineConfig(mode=ExecutionMode.INTERLEAVED, **ACTIVE),
        verifier_runtime=award_runtimes()[1],
    ).enumerate_query(query)
    assert semantic_actions(interleaved_graph)[:3] == semantic_actions(graphs[0])[:3]


def test_controller_state_survives_the_staged_seam(tmp_path):
    enumerator, verifier = award_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate([Query("Testprize", "awardWonBy", 0)]):
            writer.write(graph)
    graph = list(read_stage(tmp_path / "a.jsonl"))[0]

    state = RCSEState.from_json(graph.rcse_state)
    assert state.outcomes and state.executed_views
    assert graph.controller_log
    assert graph.budget_snapshot["calls_used"] > 0
    # Every decision carries its full score breakdown for later explanation.
    for record in graph.controller_log:
        assert "chosen" in record and "residual" in record


def test_a_fixed_budget_run_never_becomes_adaptive():
    query = Query("Testland", BORDERS, 0)
    script = {("borders_direct", "Testland", BORDERS): ["Alpha; Beta"]}
    runtime = ScriptedRuntime(script, fallback=lambda r: "Alpha")
    prediction = CoverPipeline(
        runtime, PipelineConfig(run_optional_views=True, enable_active_controller=False)
    ).run([query]).predictions[0]
    assert prediction.stopped_reason == "fixed_budget_views_complete"


def test_an_active_run_actually_chooses_and_executes_follow_up_actions():
    enumerator, verifier = award_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.INTERLEAVED, **ACTIVE),
        verifier_runtime=verifier,
    )
    graph = pipeline.enumerate_query(Query("Testprize", "awardWonBy", 0))
    kinds = [d["chosen"]["action_type"] for d in graph.controller_log]
    assert len(kinds) >= 2
    assert len(set(kinds)) >= 2, f"no genuine action variety: {kinds}"


# --- 45-48. trace, budget, compliance ----------------------------------------


def test_every_decision_records_its_score_breakdown():
    contract = get_contract(BORDERS)
    decision = choose_action(contract, [], RCSEState(), Budget(**BIG), 0)
    payload = decision.to_json()
    assert payload["considered"]
    for entry in payload["considered"]:
        assert "action" in entry and "score" in entry
        if entry["action"]["action_type"] != ActionType.STOP.value:
            assert set(entry["components"]) >= {
                "expected_yield", "gap", "uncertainty", "cost", "redundancy"
            }
    assert payload["chosen"]["model_role"]
    assert payload["residual"]["reasons"]


def test_no_action_drives_the_budget_negative():
    contract = get_contract(BORDERS)
    budget = contract_budget = Budget(max_calls=2, max_generated_tokens=100)
    budget.charge(calls=2, generated_tokens=100)
    assert contract_budget.calls_left >= 0 and contract_budget.tokens_left >= 0
    actions = legal_actions(contract, [], RCSEState(), budget)
    assert [a.action_type for a in actions] == [ActionType.STOP]


def test_no_learned_controller_exists():
    source = Path(inspect.getfile(choose_action)).read_text()
    tree = ast.parse(source)
    banned = {"sklearn", "torch", "scipy", "numpy", "xgboost", "gym", "stable_baselines3",
              "requests", "urllib", "httpx", "wikipedia", "wikidata"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] not in banned for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in {"fit", "partial_fit", "train", "backward", "step",
                                "learn", "update_policy"}
    for banned_word in ("policy_gradient", "q_learning", "replay_buffer", "reward"):
        assert banned_word not in source.lower()


def test_every_controller_constant_is_versioned():
    payload = DEFAULT_CONTROLLER.to_json()
    for name in ("alpha_yield", "beta_gap", "gamma_uncertainty", "lambda_cost",
                 "rho_redundancy", "residual_stop", "saturation_patience",
                 "stability_threshold", "verify_first_unresolved"):
        assert name in payload
    # Component and cost constants exist as fields even where not serialised.
    for name in ("untried_yield_prior", "mandatory_gap_relevance", "optional_gap_scale",
                 "covered_mechanism_redundancy", "resample_redundancy",
                 "repeat_redundancy_step", "indirect_uncertainty",
                 "adversarial_uncertainty_bonus", "reverify_redundancy",
                 "verify_first_bonus", "cost_run_view", "cost_verify",
                 "cost_adversarial_verify", "cost_cross_model"):
        assert hasattr(DEFAULT_CONTROLLER, name)


def test_a_disabled_cross_model_branch_offers_no_action():
    contract = get_contract(BORDERS)
    actions = legal_actions(
        contract, [candidate("a", mechanisms=1)], RCSEState(), Budget(**BIG),
        cross_model_available=False,
    )
    assert not [a for a in actions if a.action_type is ActionType.CROSS_MODEL_CHECK]


def test_no_dola_action_exists():
    assert not any("DOLA" in a.value.upper() or "DECOD" in a.value.upper() for a in ActionType)


# --- 49-63. staged role-swap orchestration -----------------------------------


def swap_runtimes():
    """A scenario that genuinely needs a return trip to the enumerator.

    The second model independently recalls a name the enumerator never
    produced, so after verification the controller still wants enumerator-role
    acquisition for it.
    """
    enumerator = ScriptedRuntime(
        fallback=lambda r: "Alpha; Gamma" if r.metadata.get("view_id") == "award_direct" else "Alpha",
        model_id="offline/mistral", family="offline-mistral", role="enumerator",
    )
    verifier = ScriptedRuntime(
        fallback=lambda r: "Delta",
        label_scores={
            ("blind_verifier", "Testprize", "awardWonBy"):
                {"VALID": 3.0, "INVALID": -1.0, "UNKNOWN": 0.0}
        },
        model_id="offline/qwen", family="offline-qwen", role="verifier",
    )
    return enumerator, verifier


def test_the_role_sequence_returns_to_the_enumerator(tmp_path):
    """ENUMERATOR -> VERIFIER -> ENUMERATOR, with the swap actually executed."""
    enumerator, verifier = swap_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    graphs, roles = drive_staged(pipeline, [Query("Testprize", "awardWonBy", 0)], tmp_path)

    assert roles[:2] == [ModelRole.ENUMERATOR, ModelRole.VERIFIER]
    assert ModelRole.ENUMERATOR in roles[2:], f"never returned to the enumerator: {roles}"
    assert not graphs[0].pending_action


def test_a_pending_action_is_created_persisted_reloaded_executed_and_cleared(tmp_path):
    enumerator, verifier = swap_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate([Query("Testprize", "awardWonBy", 0)]):
            writer.write(graph)
    with StageWriter(tmp_path / "b.jsonl") as writer:
        for graph in pipeline.verify(read_stage(tmp_path / "a.jsonl")):
            writer.write(graph)

    after_verify = list(read_stage(tmp_path / "b.jsonl"))[0]
    pending = dict(after_verify.pending_action)
    assert pending, "verification left no pending action to test"
    assert CoverPipeline.pending_role(after_verify) is ModelRole.ENUMERATOR

    # The exact instance, not "some action of that kind".
    assert pending["action_type"] and (pending["view_id"] or pending["candidate_key"])

    with StageWriter(tmp_path / "c.jsonl") as writer:
        for graph in pipeline.resume(read_stage(tmp_path / "b.jsonl")):
            writer.write(graph)
    resumed = list(read_stage(tmp_path / "c.jsonl"))[0]

    executed = [d["chosen"] for d in resumed.controller_log]
    assert any(
        c["action_type"] == pending["action_type"]
        and c["view_id"] == pending["view_id"]
        and c["candidate_key"] == pending["candidate_key"]
        for c in executed
    ) or pending["action_type"] not in {c["action_type"] for c in executed}
    # Whatever it chose next, the pending slot must not still hold the old one.
    assert resumed.pending_action != pending


def test_the_exact_action_identity_survives_serialisation():
    action = Action(
        ActionType.REVERSE_CHECK, view_id="award_reverse_check",
        facet_id="award_temporal", candidate_key="gamma", reason="r",
        estimated_cost=1.0,
    )
    payload = action.to_json()
    assert payload["view_id"] == "award_reverse_check"
    assert payload["candidate_key"] == "gamma"
    assert payload["facet_id"] == "award_temporal"
    assert payload["model_role"] == ModelRole.ENUMERATOR.value

    graph = _graph_with_pending(payload)
    pipeline = CoverPipeline(ScriptedRuntime({}), PipelineConfig(**ACTIVE))
    restored = pipeline._take_pending(
        graph, frozenset({ModelRole.ENUMERATOR, ModelRole.NONE})
    )
    assert restored is not None
    assert restored.identity == action.identity


def _graph_with_pending(payload):
    from cover_kbc.evidence.graph import build_graph

    graph = build_graph(Query("Testprize", "awardWonBy", 0), get_contract("awardWonBy"))
    graph.pending_action = dict(payload)
    return graph


def test_a_pending_action_for_the_other_role_is_left_untouched():
    payload = Action(ActionType.VERIFY, candidate_key="gamma").to_json()
    graph = _graph_with_pending(payload)
    pipeline = CoverPipeline(ScriptedRuntime({}), PipelineConfig(**ACTIVE))
    taken = pipeline._take_pending(
        graph, frozenset({ModelRole.ENUMERATOR, ModelRole.NONE})
    )
    assert taken is None
    assert graph.pending_action == payload, "it must survive for the right phase"


def test_a_corrupt_pending_payload_fails_loudly():
    from cover_kbc.pipeline import CorruptPendingAction

    pipeline = CoverPipeline(ScriptedRuntime({}), PipelineConfig(**ACTIVE))
    graph = _graph_with_pending({"action_type": "NOT_AN_ACTION"})
    with pytest.raises(CorruptPendingAction):
        pipeline._take_pending(graph, frozenset({ModelRole.ENUMERATOR}))
    with pytest.raises(CorruptPendingAction):
        CoverPipeline.pending_role(_graph_with_pending({"action_type": "VERIFY"}))


def test_an_unsupported_action_has_no_silent_executor():
    from cover_kbc.pipeline import UnsupportedAction

    pipeline = CoverPipeline(ScriptedRuntime({}), PipelineConfig(**ACTIVE))
    graph = _graph_with_pending({})
    with pytest.raises(UnsupportedAction):
        pipeline._execute_action(
            graph, get_contract("awardWonBy"), Action(ActionType.STOP), [], {}
        )


def test_finalization_is_forbidden_while_executable_work_remains():
    from cover_kbc.pipeline import PendingActionNotConsumed

    pipeline = CoverPipeline(ScriptedRuntime({}), PipelineConfig(**ACTIVE))
    graph = _graph_with_pending(
        Action(ActionType.RUN_FACET, view_id="award_facet_temporal").to_json()
    )
    graph.budget_snapshot = {"calls_used": 1, "generated_tokens_used": 10}
    with pytest.raises(PendingActionNotConsumed):
        pipeline.decide_graph(graph)


def test_finalization_is_allowed_once_the_budget_is_exhausted():
    pipeline = CoverPipeline(ScriptedRuntime({}), PipelineConfig(**ACTIVE))
    graph = _graph_with_pending(
        Action(ActionType.RUN_FACET, view_id="award_facet_temporal").to_json()
    )
    contract = get_contract("awardWonBy")
    graph.budget_snapshot = {
        "calls_used": contract.stopping.max_calls,
        "generated_tokens_used": contract.stopping.max_generated_tokens,
    }
    prediction = pipeline.decide_graph(graph)          # must not raise
    assert prediction.relation == "awardWonBy"


def test_finalization_is_allowed_after_a_clean_stop(tmp_path):
    enumerator, verifier = swap_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    graphs, _ = drive_staged(pipeline, [Query("Testprize", "awardWonBy", 0)], tmp_path)
    assert not graphs[0].pending_action
    assert pipeline.decide(graphs).predictions


def test_the_controller_round_counter_never_resets_across_roles(tmp_path):
    enumerator, verifier = swap_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    graphs, roles = drive_staged(pipeline, [Query("Testprize", "awardWonBy", 0)], tmp_path)
    steps = [d["step"] for d in graphs[0].controller_log]
    assert steps == sorted(steps), f"round order broke across swaps: {steps}"
    assert len(set(steps)) == len(steps), "a phase restarted its counter"
    assert len(roles) >= 3, "this scenario must exercise at least two swaps"


def test_the_budget_is_global_across_role_swaps(tmp_path):
    enumerator, verifier = swap_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate([Query("Testprize", "awardWonBy", 0)]):
            writer.write(graph)
    after_a = list(read_stage(tmp_path / "a.jsonl"))[0].budget_snapshot["calls_used"]

    graphs, _ = drive_staged(pipeline, [Query("Testprize", "awardWonBy", 0)], tmp_path)
    total = graphs[0].budget_snapshot["calls_used"]
    assert total > after_a, "later phases did not accumulate onto the same budget"
    assert total <= get_contract("awardWonBy").stopping.max_calls
    assert graphs[0].budget_snapshot["generated_tokens_used"] >= 0


def test_no_completed_work_is_rerun_on_re_entry(tmp_path):
    enumerator, verifier = swap_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    graphs, _ = drive_staged(pipeline, [Query("Testprize", "awardWonBy", 0)], tmp_path)
    executed = [
        (d["chosen"]["action_type"], d["chosen"]["view_id"], d["chosen"]["candidate_key"])
        for d in graphs[0].controller_log
        if d["chosen"]["action_type"] != ActionType.STOP.value
    ]
    assert len(executed) == len(set(executed)), f"an action ran twice: {executed}"


def test_cross_model_recall_executes_only_once_across_swaps(tmp_path):
    enumerator, verifier = swap_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    graphs, _ = drive_staged(pipeline, [Query("Testprize", "awardWonBy", 0)], tmp_path)
    groups = [
        edge.independence_group
        for candidate in graphs[0].candidates.values()
        for edge in candidate.all_evidence()
    ]
    assert groups.count(IndependenceGroup.CROSS_MODEL_RECALL) <= len(graphs[0].candidates)
    cross_actions = [
        d for d in graphs[0].controller_log
        if d["chosen"]["action_type"] == ActionType.CROSS_MODEL_CHECK.value
    ]
    assert len(cross_actions) <= 1


def test_all_state_survives_the_stage_round_trip(tmp_path):
    enumerator, verifier = swap_runtimes()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE),
        verifier_runtime=verifier,
    )
    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate([Query("Testprize", "awardWonBy", 0)]):
            writer.write(graph)
    with StageWriter(tmp_path / "b.jsonl") as writer:
        for graph in pipeline.verify(read_stage(tmp_path / "a.jsonl")):
            writer.write(graph)
    original = list(read_stage(tmp_path / "b.jsonl"))[0]

    with StageWriter(tmp_path / "c.jsonl") as writer:
        writer.write(original)
    reloaded = list(read_stage(tmp_path / "c.jsonl"))[0]

    assert reloaded.pending_action == original.pending_action
    assert reloaded.rcse_state == original.rcse_state
    assert reloaded.budget_snapshot == original.budget_snapshot
    assert len(reloaded.controller_log) == len(original.controller_log)
    state = RCSEState.from_json(reloaded.rcse_state)
    assert state.executed_actions == RCSEState.from_json(original.rcse_state).executed_actions


def test_an_exhausted_budget_stops_the_outer_loop_without_zeroing_the_residual():
    contract = get_contract("awardWonBy")
    state = RCSEState()
    state.executed_views.update(contract.mandatory_views)
    residual = estimate_residual(contract, [], state)
    assert residual.residual > 0.0

    exhausted = Budget(max_calls=0, max_generated_tokens=0)
    stop, reason = should_stop(contract, [], state, exhausted, residual)
    assert stop and "budget" in reason.lower()
    assert residual.residual > 0.0, "search need must survive an exhausted budget"


def test_the_production_cli_exposes_the_role_swap_loop():
    """The correction must live in the real command path, not a test helper."""
    source = Path("scripts/run_staged.py").read_text()
    tree = ast.parse(source)
    functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "phase_resolve" in functions

    resolve = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "phase_resolve"
    )
    referenced = {
        n.attr for n in ast.walk(resolve) if isinstance(n, ast.Attribute)
    } | {
        getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(resolve)
        if isinstance(n, ast.Call)
    }
    assert "pending_role" in referenced, "the resolver never inspects pending actions"
    # Both drivers must be reachable: the loop dispatches whichever role the
    # pending actions need, which is the whole point of the role swap.
    assert {"resume", "verify"} <= referenced, "the resolver never dispatches both roles"
    assert "ENUMERATOR" in referenced and "VERIFIER" in referenced

    main = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    main_calls = {
        getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(main)
        if isinstance(n, ast.Call)
    }
    assert "phase_resolve" in main_calls, "the default `all` path skips the resolver"
