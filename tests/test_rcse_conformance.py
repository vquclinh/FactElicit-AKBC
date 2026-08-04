"""Module 6 conformance: Residual Coverage & Saturation Estimator (spec §12).

Deterministic and synthetic throughout. No model is loaded anywhere.

The central properties: ``q_res`` measures *need to keep searching* and never a
cardinality; it is distinct from Module 5's candidate-level ``q(o)``; and the
three coverage axes (mandatory views, acquisition mechanisms, semantic facets)
stay separate rather than collapsing into one another.
"""

from __future__ import annotations

import ast
import inspect
import math
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import all_contracts, get_contract
from cover_kbc.controller import Action, ActionType, record_outcome
from cover_kbc.coverage import (
    DEFAULT_RCSE,
    ActionOutcome,
    GateState,
    NumericStability,
    RCSEState,
    declared_facets,
    estimate_residual,
    locality_competition,
    mandatory_view_gap,
    mean_inclusion_uncertainty,
    mechanism_gap,
    numeric_stability,
    semantic_facet_gap,
    trusted_keys,
    unresolved_mass,
    verifier_disagreement,
)
from cover_kbc.elicitation.library import views_for
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
from cover_kbc.scoring import DEFAULT_SCORING, assign_tier, coverage_q, score_candidate
from cover_kbc.types import (
    Budget,
    Candidate,
    CandidateStatus,
    EdgeType,
    Evidence,
    IndependenceGroup,
    ProgramType,
    Query,
    VerificationLabel,
    VerificationResult,
    VerificationTier,
)

BORDERS = "countryLandBordersCountry"


# --- helpers -----------------------------------------------------------------


def candidate(key, relation=BORDERS, mechanisms=0, status=None, score=None, numeric=None):
    contract = get_contract(relation)
    cand = Candidate(key=key, display_value=key.title(), relation=relation)
    groups = list(contract.eligible_independence_groups)
    for i in range(mechanisms):
        cand.add_evidence(
            Evidence(key, EdgeType.SUPPORT, groups[i % len(groups)], "v", "m", 0, f"{key}-r{i}")
        )
    if numeric is not None:
        cand.numeric_value = numeric
    if status is not None:
        cand.status = status
    if score is not None:
        cand.score = score
    return cand


def fully_covered(contract) -> RCSEState:
    """A state where every declared view, facet and mechanism has run."""
    state = RCSEState()
    state.executed_views.update(contract.all_views())
    for view in views_for(contract.relation, tuple(contract.all_views())):
        if view.facet_id:
            state.executed_facets.add(view.facet_id)
        if not view.is_gate:
            state.executed_groups.add(view.independence_group)
    return state


def with_actions(state, *, gains, tokens=200):
    for gain in gains:
        state.record(ActionOutcome("RUN_VIEW", new_trusted=gain, generated_tokens=tokens))
    return state


# --- 1-4. what q_res is, and is not -----------------------------------------


@pytest.mark.parametrize("contract", list(all_contracts()), ids=lambda c: c.relation)
def test_q_res_is_always_a_bounded_fraction(contract):
    for state in (RCSEState(), fully_covered(contract)):
        for cands in ([], [candidate("a", contract.relation, mechanisms=1)]):
            r = estimate_residual(contract, cands, state)
            assert 0.0 <= r.residual <= 1.0
            assert math.isfinite(r.residual)


def test_no_cardinality_estimator_drives_the_core():
    """Spec section 12.1 removed capture-recapture from the core."""
    source = Path(inspect.getfile(estimate_residual)).read_text()
    tree = ast.parse(source)
    names = {
        getattr(n.func, "attr", getattr(n.func, "id", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    for banned in ("chao", "chao1", "capture_recapture", "unseen_count",
                   "estimated_cardinality", "expected_total_objects"):
        assert banned not in names
    # No attribute on the result claims to be a count of remaining objects.
    r = estimate_residual(get_contract(BORDERS), [], RCSEState())
    for key in list(r.components) + list(r.diagnostics):
        assert "cardinality" not in key and "unseen" not in key


def test_q_res_is_not_candidate_coverage_q_of_o():
    """A fully covered candidate does not mean a settled query.

    Alpha is found by every eligible mechanism, so ``q(alpha) == 1``. The query
    has nonetheless run no views, so the search need must stay maximal.
    """
    contract = get_contract(BORDERS)
    alpha = candidate("alpha", mechanisms=len(contract.eligible_independence_groups))
    assert coverage_q(alpha, contract) == pytest.approx(1.0)

    r = estimate_residual(contract, [alpha], RCSEState())
    assert r.residual == pytest.approx(1.0)
    # Maximal candidate coverage, maximal query residual: the two quantities
    # move independently and here point in opposite directions.
    assert r.residual != pytest.approx(1.0 - coverage_q(alpha, contract))
    assert "q_coverage" not in r.components


def test_a_low_coverage_candidate_does_not_force_high_residual():
    """One weak hallucinated candidate must not dominate a settled query."""
    contract = get_contract(BORDERS)
    state = with_actions(fully_covered(contract), gains=[0, 0, 0])
    state.record_trusted(["alpha"])
    state.record_trusted(["alpha"])
    strong = candidate("alpha", mechanisms=4, status=CandidateStatus.ACCEPTED)
    weak = candidate("noise", mechanisms=1, status=CandidateStatus.REJECTED)
    r = estimate_residual(contract, [strong, weak], state)
    assert r.residual < DEFAULT_RCSE.stop_threshold


# --- 5-8. the three coverage axes stay distinct -----------------------------


def test_mandatory_mechanism_and_facet_gaps_are_separate_signals():
    contract = get_contract("awardWonBy")
    r = estimate_residual(contract, [], RCSEState())
    assert {"mandatory_gap", "mechanism_gap", "facet_gap"} <= set(r.components)


def test_a_mandatory_view_gap_reaches_the_residual():
    contract = get_contract(BORDERS)
    empty = estimate_residual(contract, [], RCSEState())
    assert empty.components["mandatory_gap"] == pytest.approx(1.0)
    assert "mandatory_views_incomplete" in empty.reasons

    state = RCSEState()
    state.executed_views.update(contract.mandatory_views)
    done = estimate_residual(contract, [], state)
    assert done.components["mandatory_gap"] == 0.0
    assert "mandatory_views_incomplete" not in done.reasons


def test_completing_a_mandatory_view_never_raises_the_mandatory_gap():
    contract = get_contract(BORDERS)
    state = RCSEState()
    previous = math.inf
    for view_id in contract.mandatory_views:
        state.executed_views.add(view_id)
        gap = mandatory_view_gap(contract, state)
        assert gap <= previous
        previous = gap
    assert previous == 0.0


def test_the_mandatory_gap_is_a_floor_that_stability_cannot_hide():
    """A perfectly stable, saturated, fully-resolved query still cannot look
    settled while a required view has not run."""
    contract = get_contract(BORDERS)
    state = with_actions(RCSEState(), gains=[0, 0, 0])
    state.record_trusted(["alpha"])
    state.record_trusted(["alpha"])
    state.executed_groups.update(contract.eligible_independence_groups)
    accepted = candidate("alpha", mechanisms=4, status=CandidateStatus.ACCEPTED)

    r = estimate_residual(contract, [accepted], state)
    assert r.components["mandatory_gap"] > 0.0
    assert r.residual >= r.components["mandatory_gap"]
    assert "residual_floored_by_mandatory_gap" in r.reasons


def test_a_facet_is_not_an_independence_group():
    """Module-2's separation survives into RCSE."""
    contract = get_contract("awardWonBy")
    facets = declared_facets(contract)
    groups = {g.value for g in contract.eligible_independence_groups}
    assert facets and not (facets & groups)
    # Award facets all partition the same structural mechanism.
    structural = [
        v for v in views_for(contract.relation, tuple(contract.all_views())) if v.facet_id
    ]
    assert len({v.independence_group for v in structural}) < len(facets)


@pytest.mark.parametrize(
    "relation", [c.relation for c in all_contracts() if c.relation != "awardWonBy"]
)
def test_a_relation_without_facets_has_no_permanent_facet_gap(relation):
    contract = get_contract(relation)
    assert declared_facets(contract) == set()
    assert semantic_facet_gap(contract, RCSEState()) == 0.0


# --- 9-12. available / executed / supporting --------------------------------


def test_an_unexecuted_mechanism_is_search_need_not_contradiction():
    contract = get_contract(BORDERS)
    alpha = candidate("alpha", mechanisms=1)
    r = estimate_residual(contract, [alpha], RCSEState())
    assert r.components["mechanism_gap"] > 0.0
    # It is a reason to search, never evidence against the candidate.
    assert alpha.contradiction_count == 0
    assert "acquisition_mechanism_unexplored" in r.reasons


def test_executing_a_mechanism_closes_its_gap():
    contract = get_contract(BORDERS)
    state = RCSEState()
    previous = 1.0
    for group in contract.eligible_independence_groups:
        state.executed_groups.add(group)
        gap = mechanism_gap(contract, state)
        assert gap <= previous
        previous = gap
    assert previous == 0.0


def test_an_unavailable_optional_family_leaves_no_permanent_gap():
    """A mandatory-only run must be able to reach mechanism_gap == 0."""
    from cover_kbc.scoring import ScoringConfig, acquisition_groups

    contract = get_contract(BORDERS)
    limited = ScoringConfig(optional_views_available=False)
    state = RCSEState()
    state.executed_groups.update(acquisition_groups(contract, limited))
    assert mechanism_gap(contract, state, limited) == 0.0


def test_cross_model_and_factual_decoding_never_leave_a_permanent_gap():
    """Both are optional branches scored outside F; neither belongs to m(o)."""
    from cover_kbc.scoring import acquisition_groups

    for contract in all_contracts():
        available = set(acquisition_groups(contract))
        assert IndependenceGroup.CROSS_MODEL_RECALL not in available
        assert IndependenceGroup.FACTUAL_DECODING not in available
        state = RCSEState()
        state.executed_groups.update(available)
        assert mechanism_gap(contract, state) == 0.0


# --- 13-18. verified yield ---------------------------------------------------


def test_the_trusted_set_is_the_accepted_set_not_every_candidate():
    contract = get_contract(BORDERS)
    accepted = candidate("alpha", mechanisms=3)
    score_candidate(accepted, contract, DEFAULT_SCORING)
    accepted.tier = assign_tier(accepted, contract, DEFAULT_SCORING)
    thin = candidate("noise", mechanisms=1)
    score_candidate(thin, contract, DEFAULT_SCORING)
    thin.tier = assign_tier(thin, contract, DEFAULT_SCORING)
    rejected = candidate("bad", mechanisms=3, status=CandidateStatus.REJECTED)

    trusted = trusted_keys([accepted, thin, rejected], contract)
    assert "alpha" in trusted
    assert "bad" not in trusted, "a rejected candidate is never trusted"
    assert "noise" not in trusted, "an unresolved candidate is never trusted"


def test_an_auto_accepted_candidate_counts_as_trusted_without_a_verifier_call():
    """Broad structural agreement is trust, even with no Module-4 call."""
    contract = get_contract(BORDERS)
    cand = candidate("alpha", mechanisms=3)
    score_candidate(cand, contract, DEFAULT_SCORING)
    cand.tier = assign_tier(cand, contract, DEFAULT_SCORING)
    assert cand.tier is VerificationTier.AUTO_ACCEPT
    assert cand.verifications == []
    assert "alpha" in trusted_keys([cand], contract)


def test_verified_yield_counts_the_trusted_set_delta():
    state = RCSEState()
    action = Action(ActionType.RUN_VIEW, view_id="borders_direct")
    first = record_outcome(state, action, trusted_keys=["a"], new_candidates=1,
                           generated_tokens=100)
    assert first.new_trusted == 1
    second = record_outcome(state, action, trusted_keys=["a"], new_candidates=1,
                            generated_tokens=100)
    assert second.new_trusted == 0, "re-mentioning a trusted candidate is not yield"
    third = record_outcome(state, action, trusted_keys=["a", "b"], new_candidates=1,
                           generated_tokens=100)
    assert third.new_trusted == 1


def test_raw_mentions_are_not_verified_yield():
    """Five raw candidates that never become trusted yield nothing."""
    state = RCSEState()
    action = Action(ActionType.RUN_VIEW, view_id="borders_direct")
    outcome = record_outcome(state, action, trusted_keys=[], new_candidates=5,
                             generated_tokens=300)
    assert outcome.new_candidates == 5
    assert outcome.new_trusted == 0
    assert not outcome.produced_value


def test_a_verification_action_that_resolves_a_candidate_counts_as_value():
    """No new candidate generated, but real information gained."""
    state = RCSEState()
    record_outcome(state, Action(ActionType.RUN_VIEW, view_id="v"),
                   trusted_keys=[], new_candidates=1, generated_tokens=100)
    outcome = record_outcome(state, Action(ActionType.VERIFY, candidate_key="a"),
                             trusted_keys=["a"], new_candidates=0, generated_tokens=0)
    assert outcome.is_verification
    assert outcome.new_trusted == 1
    assert outcome.produced_value


def test_a_synthetic_token_cost_is_marked_as_such():
    state = RCSEState()
    outcome = record_outcome(state, Action(ActionType.RUN_VIEW, view_id="v"),
                             trusted_keys=["a"], new_candidates=1, generated_tokens=3,
                             synthetic_cost=True)
    assert outcome.synthetic_cost is True
    assert outcome.to_json()["synthetic_cost"] is True


# --- 19-22. marginal yield and saturation ------------------------------------


def test_marginal_yield_is_normalised_by_generated_tokens():
    cheap, dear = RCSEState(), RCSEState()
    cheap.record(ActionOutcome("RUN_VIEW", new_trusted=1, generated_tokens=100))
    dear.record(ActionOutcome("RUN_VIEW", new_trusted=1, generated_tokens=1000))
    assert cheap.marginal_yield(3) > dear.marginal_yield(3)
    assert dear.marginal_yield(3) == pytest.approx(1.0, rel=1e-3)


def test_zero_token_actions_give_a_finite_bounded_yield():
    """A pure verification round must not divide by epsilon into a fake yield."""
    state = RCSEState()
    state.record(ActionOutcome("VERIFY", new_trusted=1, generated_tokens=0,
                               is_verification=True))
    value = state.marginal_yield(3, DEFAULT_RCSE.yield_epsilon)
    assert math.isfinite(value)
    assert 0.0 <= value <= 1.0


def test_saturation_rises_across_fruitless_actions():
    state = RCSEState()
    assert state.saturation(3) == 0.0                     # no history
    previous = -1.0
    for _ in range(3):
        state.record(ActionOutcome("RUN_VIEW", new_trusted=0, generated_tokens=100))
        value = state.saturation(3)
        assert value >= previous
        previous = value
    assert previous == pytest.approx(1.0)


def test_a_productive_action_lowers_saturation():
    state = with_actions(RCSEState(), gains=[0, 0, 0])
    assert state.saturation(3) == pytest.approx(1.0)
    state.record(ActionOutcome("RUN_VIEW", new_trusted=1, generated_tokens=100))
    assert state.saturation(3) < 1.0


def test_saturation_alone_cannot_zero_the_residual():
    """The strongest saturation signal cannot report a settled query while
    mandatory acquisition is outstanding."""
    contract = get_contract(BORDERS)
    state = with_actions(RCSEState(), gains=[0, 0, 0, 0])
    assert state.saturation(3) == pytest.approx(1.0)
    r = estimate_residual(contract, [], state)
    assert r.residual == pytest.approx(1.0)


# --- 23-25. set stability ----------------------------------------------------


def test_stability_needs_two_trusted_observations():
    state = RCSEState()
    assert state.set_stability() == 0.0
    state.record_trusted(["a"])
    assert state.set_stability() == 0.0
    state.record_trusted(["a"])
    assert state.set_stability() == pytest.approx(1.0)


def test_two_empty_trusted_sets_are_not_stable_agreement():
    """Nothing found twice is not a settled answer."""
    state = RCSEState()
    state.record_trusted([])
    state.record_trusted([])
    assert state.set_stability() == 0.0


def test_stability_is_jaccard_and_handles_growth():
    state = RCSEState()
    state.record_trusted(["a"])
    state.record_trusted(["a", "b"])
    assert state.set_stability() == pytest.approx(0.5)


def test_stability_alone_cannot_complete_a_query_with_unresolved_candidates():
    contract = get_contract(BORDERS)
    state = fully_covered(contract)
    state.record_trusted(["alpha"])
    state.record_trusted(["alpha"])
    with_actions(state, gains=[0, 0, 0])
    assert state.set_stability() == pytest.approx(1.0)

    disputed = candidate("beta", mechanisms=1, status=CandidateStatus.UNRESOLVED)
    disputed.tier = VerificationTier.VERIFY
    settled = estimate_residual(contract, [candidate("alpha", mechanisms=4,
                                                     status=CandidateStatus.ACCEPTED)], state)
    contested = estimate_residual(
        contract, [candidate("alpha", mechanisms=4, status=CandidateStatus.ACCEPTED), disputed],
        state,
    )
    assert contested.residual > settled.residual
    assert "unresolved_candidates_remain" in contested.reasons


# --- 26-29. Module-5 uncertainty signals stay distinct -----------------------


def test_unresolved_mass_weights_by_candidate_coverage():
    """A well-supported disputed candidate presses harder than a thin one."""
    contract = get_contract(BORDERS)
    thin = candidate("thin", mechanisms=1, status=CandidateStatus.UNRESOLVED)
    broad = candidate("broad", mechanisms=5, status=CandidateStatus.UNRESOLVED)
    assert unresolved_mass([broad], contract) > unresolved_mass([thin], contract)


def test_a_thin_unresolved_candidate_is_still_visible():
    contract = get_contract(BORDERS)
    thin = candidate("thin", mechanisms=1, status=CandidateStatus.UNRESOLVED)
    assert unresolved_mass([thin], contract) > 0.0


def test_rejected_candidates_do_not_add_unresolved_mass():
    contract = get_contract(BORDERS)
    rejected = candidate("bad", mechanisms=2, status=CandidateStatus.REJECTED)
    assert unresolved_mass([rejected], contract) == 0.0


def test_inclusion_uncertainty_disagreement_and_entropy_stay_separate():
    contract = get_contract(BORDERS)
    cand = candidate("alpha", mechanisms=3, status=CandidateStatus.ACCEPTED)
    cand.verifications.append(
        VerificationResult(candidate_key="alpha", label=VerificationLabel.VALID,
                           valid_prob=0.6, invalid_prob=0.2, unknown_prob=0.2,
                           entropy=0.9, prompt_disagreement=0.3)
    )
    r = estimate_residual(contract, [cand], fully_covered(contract))
    assert r.components["inclusion_uncertainty"] != r.components["verifier_disagreement"]
    assert verifier_disagreement([cand]) == pytest.approx(0.3)
    # H_ver is Module 4's and is not summed into either.
    assert cand.verifications[-1].entropy == pytest.approx(0.9)


def test_higher_verifier_disagreement_raises_the_residual():
    contract = get_contract(BORDERS)
    state = fully_covered(contract)
    state.record_trusted(["alpha"]); state.record_trusted(["alpha"])
    with_actions(state, gains=[0, 0, 0])

    def build(disagreement):
        cand = candidate("alpha", mechanisms=4, status=CandidateStatus.ACCEPTED)
        cand.verifications.append(
            VerificationResult(candidate_key="alpha", label=VerificationLabel.VALID,
                               valid_prob=0.8, invalid_prob=0.1, unknown_prob=0.1,
                               prompt_disagreement=disagreement)
        )
        return cand

    calm = estimate_residual(get_contract("hasArea"), [build(0.0)], fully_covered(get_contract("hasArea")))
    noisy = estimate_residual(get_contract("hasArea"), [build(0.8)], fully_covered(get_contract("hasArea")))
    assert noisy.residual > calm.residual


def test_mean_inclusion_uncertainty_is_bounded():
    contract = get_contract(BORDERS)
    for n in range(0, len(contract.eligible_independence_groups) + 1):
        value = mean_inclusion_uncertainty([candidate("a", mechanisms=n)], contract)
        assert 0.0 <= value <= 1.0


# --- 30-33. SMALL_SET --------------------------------------------------------


def small_set_settled():
    contract = get_contract(BORDERS)
    state = fully_covered(contract)
    state.record_trusted(["alpha", "beta"]); state.record_trusted(["alpha", "beta"])
    with_actions(state, gains=[0, 0, 0])
    cands = [candidate(k, mechanisms=4, status=CandidateStatus.ACCEPTED)
             for k in ("alpha", "beta")]
    return contract, cands, state


def test_small_set_settled_state_gives_low_residual():
    contract, cands, state = small_set_settled()
    r = estimate_residual(contract, cands, state)
    assert r.program_type == ProgramType.SMALL_SET.value
    assert r.residual < DEFAULT_RCSE.stop_threshold


def test_small_set_disputed_candidate_raises_residual():
    contract, cands, state = small_set_settled()
    settled = estimate_residual(contract, cands, state).residual
    disputed = candidate("gamma", mechanisms=3, status=CandidateStatus.UNRESOLVED)
    disputed.tier = VerificationTier.ADVERSARIAL_VERIFY
    assert estimate_residual(contract, cands + [disputed], state).residual > settled


def test_small_set_missing_mandatory_structure_keeps_residual_high():
    contract, cands, state = small_set_settled()
    state.executed_views.discard(contract.mandatory_views[-1])
    r = estimate_residual(contract, cands, state)
    assert r.residual >= r.components["mandatory_gap"] > 0.0


def test_small_set_finishes_more_cheaply_than_large_open_set():
    contract, cands, state = small_set_settled()
    small = estimate_residual(contract, cands, state).residual
    awards = get_contract("awardWonBy")
    open_state = fully_covered(awards)
    open_state.record_trusted(["alpha"]); open_state.record_trusted(["alpha"])
    with_actions(open_state, gains=[1, 1, 1])
    large = estimate_residual(
        awards, [candidate("alpha", "awardWonBy", mechanisms=3)], open_state
    ).residual
    assert small < large


# --- 34-38. NULL_SINGLE ------------------------------------------------------


def null_single_state():
    contract = get_contract("personHasCityOfDeath")
    state = fully_covered(contract)
    state.record_trusted(["paris"]); state.record_trusted(["paris"])
    with_actions(state, gains=[0, 0, 0])
    return contract, state


def test_null_single_uncertain_gate_keeps_residual_high():
    contract, state = null_single_state()
    uncertain = estimate_residual(contract, [], state,
                                  gate=GateState(present=True, resolved=False))
    assert uncertain.components["gate_unresolved"] == pytest.approx(1.0)
    assert "existence_gate_unresolved" in uncertain.reasons
    assert uncertain.residual > DEFAULT_RCSE.stop_threshold


def test_null_single_confident_negative_gate_needs_no_candidate_search():
    contract, state = null_single_state()
    r = estimate_residual(contract, [], state,
                          gate=GateState(present=True, resolved=True, negative=True))
    assert r.residual == pytest.approx(0.0)
    assert "confident negative" in r.rationale


def test_null_single_one_stable_locality_gives_low_residual():
    contract, state = null_single_state()
    paris = candidate("paris", contract.relation, mechanisms=3,
                      status=CandidateStatus.ACCEPTED, score=0.9)
    r = estimate_residual(contract, [paris], state,
                          gate=GateState(present=True, resolved=True, negative=False))
    assert r.components["locality_competition"] == 0.0
    assert r.residual < DEFAULT_RCSE.stop_threshold


def test_null_single_two_competing_localities_raise_residual():
    contract, state = null_single_state()
    paris = candidate("paris", contract.relation, mechanisms=3,
                      status=CandidateStatus.ACCEPTED, score=0.9)
    lyon = candidate("lyon", contract.relation, mechanisms=3,
                     status=CandidateStatus.ACCEPTED, score=0.85)
    single = estimate_residual(contract, [paris], state,
                               gate=GateState(present=True, resolved=True))
    both = estimate_residual(contract, [paris, lyon], state,
                             gate=GateState(present=True, resolved=True))
    assert both.components["locality_competition"] > 0.5
    assert both.residual > single.residual
    assert "competing_localities" in both.reasons


def test_resolving_the_competition_never_raises_null_single_residual():
    contract, state = null_single_state()
    paris = candidate("paris", contract.relation, mechanisms=3,
                      status=CandidateStatus.ACCEPTED, score=0.9)
    for rival_score in (0.85, 0.5, 0.2, 0.0):
        lyon = candidate("lyon", contract.relation, mechanisms=1,
                         status=CandidateStatus.ACCEPTED, score=rival_score)
        assert locality_competition([paris, lyon]) == pytest.approx(
            min(1.0, rival_score / 0.9)
        )


def test_no_candidates_is_not_the_same_state_as_a_confident_negative():
    contract, state = null_single_state()
    nothing_found = estimate_residual(contract, [], state,
                                      gate=GateState(present=True, resolved=False))
    confident_no = estimate_residual(contract, [], state,
                                     gate=GateState(present=True, resolved=True, negative=True))
    assert nothing_found.residual > confident_no.residual


# --- 39-43. NUMERIC ----------------------------------------------------------


def numeric_state():
    contract = get_contract("hasArea")
    state = fully_covered(contract)
    state.record_trusted(["100.0"]); state.record_trusted(["100.0"])
    with_actions(state, gains=[0, 0, 0])
    return contract, state


def test_numeric_stable_dominant_cluster_gives_low_residual():
    contract, state = numeric_state()
    cands = [candidate(f"{v}", "hasArea", mechanisms=2, numeric=v,
                       status=CandidateStatus.ACCEPTED) for v in (100.0, 100.5)]
    r = estimate_residual(contract, cands, state)
    assert r.program_type == ProgramType.NUMERIC.value
    assert r.components["cluster_competition"] == 0.0
    assert r.residual < DEFAULT_RCSE.stop_threshold


def test_numeric_competing_clusters_raise_residual():
    contract, state = numeric_state()
    stable = [candidate(f"{v}", "hasArea", mechanisms=2, numeric=v,
                        status=CandidateStatus.ACCEPTED) for v in (100.0, 100.5)]
    contested = stable + [candidate("500.0", "hasArea", mechanisms=2, numeric=500.0,
                                    status=CandidateStatus.ACCEPTED)]
    assert (
        estimate_residual(contract, contested, state).residual
        > estimate_residual(contract, stable, state).residual
    )
    assert "competing_numeric_clusters" in estimate_residual(contract, contested, state).reasons


def test_numeric_high_dispersion_raises_residual():
    contract, state = numeric_state()
    tight = [candidate(f"{v}", "hasArea", mechanisms=2, numeric=v) for v in (100.0, 100.1, 100.2)]
    loose = [candidate(f"{v}", "hasArea", mechanisms=2, numeric=v) for v in (100.0, 102.0, 104.0)]
    assert numeric_stability(loose, contract).dispersion >= numeric_stability(tight, contract).dispersion


def test_numeric_diagnostics_do_not_select_the_final_answer():
    """RCSE reads clusters; Module 8 picks the number."""
    contract, state = numeric_state()
    cands = [candidate(f"{v}", "hasArea", mechanisms=2, numeric=v) for v in (100.0, 500.0)]
    r = estimate_residual(contract, cands, state)
    assert isinstance(numeric_stability(cands, contract), NumericStability)
    # No representative value is emitted anywhere in the residual.
    for value in list(r.components.values()) + list(r.diagnostics.values()):
        assert value not in (100.0, 500.0)
    source = inspect.getsource(numeric_stability)
    assert "representative" not in source


def test_numeric_stability_handles_no_values():
    contract = get_contract("hasArea")
    assert numeric_stability([], contract) == NumericStability()
    r = estimate_residual(contract, [], RCSEState())
    assert math.isfinite(r.residual)


# --- 44-47. LARGE_OPEN_SET ---------------------------------------------------


def awards_state(gains):
    contract = get_contract("awardWonBy")
    state = fully_covered(contract)
    state.record_trusted(["a"]); state.record_trusted(["a"])
    with_actions(state, gains=gains)
    return contract, state


def test_large_open_set_productive_facet_search_keeps_residual_high():
    contract, productive = awards_state([1, 1, 1])
    _, saturated = awards_state([0, 0, 0])
    cands = [candidate("a", "awardWonBy", mechanisms=3, status=CandidateStatus.ACCEPTED)]
    assert (
        estimate_residual(contract, cands, productive).residual
        > estimate_residual(contract, cands, saturated).residual
    )
    assert "recent_actions_still_yielding" in estimate_residual(
        contract, cands, productive
    ).reasons


def test_large_open_set_saturated_with_low_tail_lowers_residual():
    contract, state = awards_state([0, 0, 0])
    cands = [candidate(k, "awardWonBy", mechanisms=3, status=CandidateStatus.ACCEPTED)
             for k in ("a", "b", "c")]
    r = estimate_residual(contract, cands, state)
    assert "recent_actions_saturated" in r.reasons
    assert r.residual < DEFAULT_RCSE.stop_threshold


def test_large_open_set_unresolved_tail_keeps_residual_elevated():
    contract, state = awards_state([0, 0, 0])
    settled = [candidate("a", "awardWonBy", mechanisms=3, status=CandidateStatus.ACCEPTED)]
    tail = [candidate(f"t{i}", "awardWonBy", mechanisms=3, status=CandidateStatus.UNRESOLVED)
            for i in range(4)]
    for cand in tail:
        cand.tier = VerificationTier.VERIFY
    assert (
        estimate_residual(contract, settled + tail, state).residual
        > estimate_residual(contract, settled, state).residual
    )


def test_covering_an_award_facet_never_raises_the_facet_gap():
    contract = get_contract("awardWonBy")
    state = RCSEState()
    previous = math.inf
    for facet in sorted(declared_facets(contract)):
        state.executed_facets.add(facet)
        gap = semantic_facet_gap(contract, state)
        assert gap <= previous
        previous = gap
    assert previous == 0.0


def test_award_search_does_not_require_every_facet_before_residual_can_fall():
    """Adaptive stopping must survive an uncovered optional facet."""
    contract = get_contract("awardWonBy")
    state = fully_covered(contract)
    state.executed_facets.discard(sorted(declared_facets(contract))[0])
    state.record_trusted(["a"]); state.record_trusted(["a"])
    with_actions(state, gains=[0, 0, 0])
    cands = [candidate("a", "awardWonBy", mechanisms=3, status=CandidateStatus.ACCEPTED)]
    r = estimate_residual(contract, cands, state)
    assert r.components["facet_gap"] > 0.0
    assert r.residual < 1.0


# --- 48-52. all four programmes are genuinely typed --------------------------


def test_every_program_type_gets_its_own_signal_set():
    seen = {}
    for contract in all_contracts():
        r = estimate_residual(contract, [], fully_covered(contract))
        seen.setdefault(contract.program_type, set()).update(r.weights)
    assert len(seen) == 4
    # No two programmes use an identical term set.
    signatures = {frozenset(v) for v in seen.values()}
    assert len(signatures) == 4


def test_typed_special_signals_only_appear_for_their_programme():
    for contract in all_contracts():
        r = estimate_residual(contract, [], fully_covered(contract))
        if contract.program_type is ProgramType.NUMERIC:
            assert "numeric_dispersion" in r.components
        else:
            assert "numeric_dispersion" not in r.weights
        if contract.program_type is ProgramType.NULL_SINGLE:
            assert "gate_unresolved" in r.components
        else:
            assert "gate_unresolved" not in r.weights


# --- 53-57. boundaries, determinism, compliance ------------------------------


@pytest.mark.parametrize("contract", list(all_contracts()), ids=lambda c: c.relation)
def test_edge_cases_stay_finite(contract):
    cases = [
        ([], RCSEState()),
        ([], fully_covered(contract)),
        ([candidate("a", contract.relation, mechanisms=0)], RCSEState()),
        ([candidate("a", contract.relation, mechanisms=1,
                    status=CandidateStatus.REJECTED)], fully_covered(contract)),
    ]
    zero_token = RCSEState()
    zero_token.record(ActionOutcome("RUN_VIEW", new_trusted=0, generated_tokens=0))
    cases.append(([], zero_token))
    for cands, state in cases:
        r = estimate_residual(contract, cands, state)
        assert math.isfinite(r.residual) and 0.0 <= r.residual <= 1.0
        for value in list(r.components.values()) + list(r.diagnostics.values()):
            assert math.isfinite(value)


def test_budget_exhaustion_does_not_rewrite_the_residual():
    """"No search value remains" and "no budget remains" are different claims."""
    contract = get_contract(BORDERS)
    r = estimate_residual(contract, [], RCSEState())
    assert r.residual == pytest.approx(1.0)
    exhausted = Budget(max_calls=0, max_generated_tokens=0)
    assert exhausted.exhausted
    # RCSE never saw the budget, and its answer is unchanged by it.
    again = estimate_residual(contract, [], RCSEState())
    assert again.residual == r.residual
    assert "budget" not in inspect.signature(estimate_residual).parameters


def test_rcse_neither_chooses_actions_nor_emits_predictions():
    source = Path(inspect.getfile(estimate_residual)).read_text()
    tree = ast.parse(source)
    calls = {
        getattr(n.func, "attr", getattr(n.func, "id", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    for owned_by_module_7_or_8 in ("choose_action", "legal_actions", "should_stop",
                                   "finalize", "select"):
        assert owned_by_module_7_or_8 not in calls
    # No exported name promises a decision.
    import cover_kbc.coverage as module

    assert not hasattr(module, "should_continue")
    assert not any(n.startswith("stop_") for n in dir(module) if callable(getattr(module, n, None)))


def test_rcse_is_deterministic():
    contract = get_contract("awardWonBy")
    outputs = set()
    for _ in range(3):
        _, state = awards_state([1, 0, 1])
        cands = [candidate("a", "awardWonBy", mechanisms=3, status=CandidateStatus.ACCEPTED)]
        r = estimate_residual(contract, cands, state)
        outputs.add((round(r.residual, 12), tuple(sorted(r.components.items())),
                     tuple(r.reasons)))
    assert len(outputs) == 1


def test_no_learned_model_or_retrieval_exists_in_module_6():
    source = Path(inspect.getfile(estimate_residual)).read_text()
    tree = ast.parse(source)
    banned = {"sklearn", "torch", "scipy", "numpy", "xgboost", "requests",
              "urllib", "httpx", "wikipedia", "wikidata"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] not in banned for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in {"fit", "partial_fit", "train", "backward", "step"}


def test_every_tunable_constant_comes_from_config():
    """No magic number inside the residual body."""
    tree = ast.parse(inspect.getsource(estimate_residual).lstrip())
    literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, float)
    ]
    # Only structural constants (0.0/1.0 bounds, the documented 0.5 tail
    # de-weighting) may appear; nothing that reads as a threshold.
    assert set(literals) <= {0.0, 1.0, 0.5}
    for name in ("saturation_window", "yield_scale", "numeric_dispersion_threshold",
                 "competitor_support_ratio", "stop_threshold"):
        assert name in DEFAULT_RCSE.to_json()


def test_the_residual_breakdown_is_traceable():
    contract = get_contract(BORDERS)
    r = estimate_residual(contract, [], RCSEState())
    assert r.reasons, "a residual must always explain itself"
    assert r.rationale
    assert set(r.weights) <= set(r.components), "every weighted term is reported"
    settled_contract, cands, state = small_set_settled()
    settled = estimate_residual(settled_contract, cands, state)
    assert settled.reasons != r.reasons


# --- 58-60. staged execution -------------------------------------------------


def test_rcse_state_survives_a_staged_round_trip(tmp_path):
    from cover_kbc.staging import StageWriter, read_stage

    relation, subject = "awardWonBy", "Testprize"
    runtime = ScriptedRuntime(
        fallback=lambda r: "Alpha; Beta", model_id="offline/m", family="offline"
    )
    config = PipelineConfig(mode=ExecutionMode.STAGED, enable_active_controller=True,
                            max_steps_per_query=6)
    pipeline = CoverPipeline(runtime, config)

    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate([Query(subject, relation, 0)]):
            writer.write(graph)

    graph = list(read_stage(tmp_path / "a.jsonl"))[0]
    state = RCSEState.from_json(graph.rcse_state)
    assert state.outcomes, "temporal history did not cross the staged seam"
    assert state.executed_views

    before = estimate_residual(graph.contract, graph.active_candidates(), state,
                               scoring=config.scoring)
    # Round-trip once more and recompute.
    with StageWriter(tmp_path / "b.jsonl") as writer:
        writer.write(graph)
    again = list(read_stage(tmp_path / "b.jsonl"))[0]
    after = estimate_residual(again.contract, again.active_candidates(),
                              RCSEState.from_json(again.rcse_state), scoring=config.scoring)

    assert before.to_json() == after.to_json()


def test_the_action_history_records_cost_and_yield_separately():
    state = RCSEState()
    record_outcome(state, Action(ActionType.RUN_VIEW, view_id="v"),
                   trusted_keys=["a"], new_candidates=3, generated_tokens=120)
    payload = state.to_json()
    row = payload["outcomes"][0]
    assert row["new_trusted"] == 1 and row["new_candidates"] == 3
    assert row["generated_tokens"] == 120
    assert RCSEState.from_json(payload).outcomes[0].new_trusted == 1


def test_a_fixed_budget_run_does_not_become_adaptive():
    """RCSE is computable in a fixed run without changing what executes."""
    relation, subject = BORDERS, "Testland"
    script = {("borders_direct", subject, relation): ["Alpha; Beta"]}
    fixed = PipelineConfig(run_optional_views=True, enable_active_controller=False)
    runtime = ScriptedRuntime(script, fallback=lambda r: "Alpha")
    result = CoverPipeline(runtime, fixed).run([Query(subject, relation, 0)])
    prediction = result.predictions[0]
    # The fixed path ran its view list, not a controller-chosen sequence.
    assert prediction.stopped_reason == "fixed_budget_views_complete"


# --- 61-64. typed blocking floors --------------------------------------------


def test_a_decisive_signal_is_not_diluted_by_its_zero_valued_siblings():
    """A weighted mean alone would average a real conflict away.

    Two competing localities score 0.94 on their own signal. Averaged against
    four zero-valued siblings that is 0.21 - below the stop threshold, i.e. the
    controller would be told a contested query was settled.
    """
    contract, state = null_single_state()
    paris = candidate("paris", contract.relation, mechanisms=3,
                      status=CandidateStatus.ACCEPTED, score=0.9)
    lyon = candidate("lyon", contract.relation, mechanisms=3,
                     status=CandidateStatus.ACCEPTED, score=0.85)
    r = estimate_residual(contract, [paris, lyon], state,
                          gate=GateState(present=True, resolved=True))

    competition = r.components["locality_competition"]
    weights = r.weights
    diluted = competition * weights["locality_competition"] / sum(weights.values())
    assert diluted < DEFAULT_RCSE.stop_threshold, "the dilution this guards against"
    assert r.residual >= competition
    assert r.residual > DEFAULT_RCSE.stop_threshold


def test_an_uncertain_gate_alone_blocks_completion():
    contract, state = null_single_state()
    settled = candidate("paris", contract.relation, mechanisms=3,
                        status=CandidateStatus.ACCEPTED, score=0.9)
    r = estimate_residual(contract, [settled], state,
                          gate=GateState(present=True, resolved=False))
    assert r.residual == pytest.approx(1.0)


def test_a_rival_numeric_cluster_alone_blocks_completion():
    contract, state = numeric_state()
    cands = [candidate(f"{v}", "hasArea", mechanisms=2, numeric=v,
                       status=CandidateStatus.ACCEPTED) for v in (100.0, 100.5, 500.0)]
    r = estimate_residual(contract, cands, state)
    assert r.components["cluster_competition"] > 0.0
    assert r.residual >= r.components["cluster_competition"]
    assert r.residual > DEFAULT_RCSE.stop_threshold


def test_programmes_that_stop_on_balance_declare_no_blocking_signal():
    """Small sets and awards must stay able to finish cheaply."""
    for relation in (BORDERS, "awardWonBy"):
        contract = get_contract(relation)
        state = fully_covered(contract)
        state.record_trusted(["a"]); state.record_trusted(["a"])
        with_actions(state, gains=[0, 0, 0])
        cands = [candidate("a", relation, mechanisms=3, status=CandidateStatus.ACCEPTED)]
        r = estimate_residual(contract, cands, state)
        assert r.residual < DEFAULT_RCSE.stop_threshold


# --- 65. blocking Module-7 dependency ----------------------------------------


def test_every_available_mechanism_is_reachable_by_some_legal_action():
    """Resolves the audit 0009 §41 blocking dependency.

    Every mechanism Module 5 counts as available must be reachable by some
    legal controller action. Candidate-conditioned families (reverse) are only
    offered once a candidate exists, which is their genuine legality condition,
    so the sweep supplies one.
    """
    from cover_kbc.controller import legal_actions
    from cover_kbc.elicitation.library import get_view
    from cover_kbc.scoring import acquisition_groups

    for contract in all_contracts():
        state = RCSEState()
        found = [candidate("alpha", contract.relation, mechanisms=1)]
        for _ in range(20):
            actions = legal_actions(
                contract, found, state, Budget(max_calls=999, max_generated_tokens=99_999)
            )
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
