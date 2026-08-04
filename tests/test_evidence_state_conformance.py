"""Module 5 conformance: evidence and uncertainty state (spec section 11).

Deterministic and synthetic throughout. No model is loaded anywhere.

The central property under test is *evidence orthogonality*: each of the five
score components owns a disjoint set of mechanisms, so one evidence event can
never be paid for twice.
"""

from __future__ import annotations

import ast
import inspect
import math
from pathlib import Path

import pytest

from cover_kbc.contracts.base import eligible_groups_for
from cover_kbc.contracts.registry import all_contracts, get_contract
from cover_kbc.elicitation.library import get_view
from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.scoring import (
    DEFAULT_SCORING,
    resolve_verification,
    ScoringConfig,
    acquisition_groups,
    assign_tier,
    candidate_state,
    contradicting_groups,
    contradiction_term,
    coverage_q,
    cross_model_term,
    decide_status,
    disagreement_term,
    inclusion_uncertainty,
    logit_term,
    query_state,
    score_candidate,
    supporting_acquisition_groups,
    support_term,
)
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    EdgeType,
    Evidence,
    EvidenceMode,
    IndependenceGroup,
    Query,
    VerificationLabel,
    VerificationResult,
    VerificationTier,
)

BORDERS = "countryLandBordersCountry"


def candidate(key="alpha", relation=BORDERS):
    return Candidate(key=key, display_value=key.title(), relation=relation)


def support(cand, group, mode=EvidenceMode.INDEPENDENT_RECALL, run="r0"):
    cand.add_evidence(
        Evidence(cand.key, EdgeType.SUPPORT, group, "v", "m", 0, run, mode=mode)
    )
    return cand


def verified(cand, label, valid, invalid, unknown, *, entropy=None, disagreement=None):
    edge = {
        VerificationLabel.VALID: EdgeType.SUPPORT,
        VerificationLabel.INVALID: EdgeType.CONTRADICT,
        VerificationLabel.UNKNOWN: EdgeType.UNKNOWN,
    }[label]
    cand.add_evidence(
        Evidence(cand.key, edge, IndependenceGroup.BLIND_VERIFIER, "blind_verifier",
                 "qwen", 0, "v0", mode=EvidenceMode.SHOWN_CANDIDATE, model_family="qwen")
    )
    cand.verifications.append(
        VerificationResult(
            candidate_key=cand.key, label=label, valid_prob=valid,
            invalid_prob=invalid, unknown_prob=unknown,
            entropy=entropy, prompt_disagreement=disagreement,
        )
    )
    return cand


# --- 1-5. component orthogonality (brief section 14) -------------------------


def components(cand, contract=None, config=DEFAULT_SCORING):
    contract = contract or get_contract(BORDERS)
    b = score_candidate(cand, contract, config)
    return (b.support, b.logit, b.cross_model, b.contradiction, b.disagreement)


def test_acquisition_support_moves_only_f():
    F, L, X, C, U = components(support(candidate(), IndependenceGroup.DIRECT_RECALL))
    assert F > 0
    assert (L, X, C, U) == (0.0, 0.0, 0.0, 0.0)


def test_blind_verifier_valid_moves_only_l():
    """The single most important Module-5 property.

    A shown-candidate agreement is paid once, through L(o). Letting it also
    raise F would mean the verifier manufactured the independent acquisition
    coverage that decides whether it needed verifying in the first place.
    """
    F, L, X, C, U = components(
        verified(candidate(), VerificationLabel.VALID, 0.9, 0.05, 0.05)
    )
    assert L > 0
    assert F == 0.0, "shown-candidate agreement inflated independent support"
    assert X == 0.0, "shown-candidate agreement was paid twice"
    assert (C, U) == (0.0, 0.0)


def test_independent_cross_model_recall_moves_only_x():
    F, L, X, C, U = components(
        support(candidate(), IndependenceGroup.CROSS_MODEL_RECALL)
    )
    assert X == pytest.approx(1.0)
    assert F == 0.0, "cross-model recall was paid in F as well as X"
    assert (L, C, U) == (0.0, 0.0, 0.0)


def test_explicit_invalid_moves_c_and_l_but_not_f():
    F, L, X, C, U = components(
        verified(candidate(), VerificationLabel.INVALID, 0.05, 0.90, 0.05)
    )
    assert C > 0 and L < 0
    assert F == 0.0, "a contradiction must not read as missing acquisition support"
    assert X == 0.0


def test_prompt_disagreement_moves_only_u():
    cand = verified(candidate(), VerificationLabel.VALID, 0.6, 0.2, 0.2, disagreement=0.4)
    before = len(supporting_acquisition_groups(cand, get_contract(BORDERS)))
    F, L, X, C, U = components(cand)
    assert U == pytest.approx(0.4)
    assert len(supporting_acquisition_groups(cand, get_contract(BORDERS))) == before


def test_one_event_never_earns_duplicate_credit():
    """Sweep every mechanism: each must touch exactly one component."""
    contract = get_contract(BORDERS)
    for group in IndependenceGroup:
        cand = support(candidate(), group)
        b = score_candidate(cand, contract, DEFAULT_SCORING)
        touched = [
            name
            for name, value in (
                ("F", b.support), ("L", b.logit), ("X", b.cross_model),
                ("C", b.contradiction), ("U", b.disagreement),
            )
            if value != 0.0
        ]
        assert len(touched) <= 1, f"{group.name} credited to {touched}"


# --- 6-8. repetition and facets do not create independence -------------------


def test_ten_repeats_of_one_view_count_once():
    contract = get_contract(BORDERS)
    cand = candidate()
    for i in range(10):
        support(cand, IndependenceGroup.DIRECT_RECALL, run=f"r{i}")
    assert len(supporting_acquisition_groups(cand, contract)) == 1
    assert cand.raw_support_count == 10          # diagnostics keep the repeats
    assert support_term(cand, contract) == pytest.approx(1 / 6)


def test_multiple_verifier_templates_are_one_mechanism():
    cand = candidate()
    for i in range(3):
        cand.add_evidence(
            Evidence(cand.key, EdgeType.CONTRADICT, IndependenceGroup.BLIND_VERIFIER,
                     "blind_verifier", "qwen", 0, f"v{i}", mode=EvidenceMode.SHOWN_CANDIDATE)
        )
    assert contradicting_groups(cand) == (IndependenceGroup.BLIND_VERIFIER,)
    assert cand.contradiction_count == 3         # raw edges still visible
    one = contradiction_term(cand, get_contract(BORDERS))
    single = candidate("beta")
    single.add_evidence(
        Evidence("beta", EdgeType.CONTRADICT, IndependenceGroup.BLIND_VERIFIER,
                 "blind_verifier", "qwen", 0, "v0", mode=EvidenceMode.SHOWN_CANDIDATE)
    )
    assert one == pytest.approx(contradiction_term(single, get_contract(BORDERS)))


def test_several_facets_remain_one_independence_support():
    contract = get_contract("awardWonBy")
    cand = candidate(relation=contract.relation)
    for facet in ("decade_1990", "decade_2000", "decade_2010"):
        cand.add_evidence(
            Evidence(cand.key, EdgeType.SUPPORT, IndependenceGroup.STRUCTURAL_DECOMPOSITION,
                     "awards_by_decade", "m", 0, facet)
        )
        cand.add_facet(facet)
    assert len(cand.facet_ids) == 3
    assert len(supporting_acquisition_groups(cand, contract)) == 1


# --- 9-15. g(o), m(o), q(o) semantics ----------------------------------------


@pytest.mark.parametrize("contract", list(all_contracts()), ids=lambda c: c.relation)
def test_g_never_exceeds_m_for_any_evidence(contract):
    """Saturate every mechanism; g must still be bounded by m semantically."""
    cand = candidate(relation=contract.relation)
    for group in IndependenceGroup:                      # everything, legal or not
        support(cand, group)
    g = len(supporting_acquisition_groups(cand, contract))
    m = len(acquisition_groups(contract))
    assert 0 <= g <= m, f"{contract.relation}: g={g} m={m}"
    assert 0.0 <= coverage_q(cand, contract) <= 1.0


def test_q_reaches_one_without_a_clamp_hiding_it():
    """q == 1 must mean 'every eligible mechanism found it', not a clamp."""
    contract = get_contract(BORDERS)
    cand = candidate()
    for group in contract.eligible_independence_groups:
        support(cand, group)
    assert coverage_q(cand, contract) == pytest.approx(1.0)
    # Adding non-acquisition evidence cannot push the ratio past 1.
    support(cand, IndependenceGroup.CROSS_MODEL_RECALL)
    verified(cand, VerificationLabel.VALID, 0.9, 0.05, 0.05)
    assert coverage_q(cand, contract) == pytest.approx(1.0)
    assert len(supporting_acquisition_groups(cand, contract)) == len(
        acquisition_groups(contract)
    )


@pytest.mark.parametrize("contract", list(all_contracts()), ids=lambda c: c.relation)
def test_the_denominator_excludes_gate_verifier_and_cross_model(contract):
    groups = set(acquisition_groups(contract))
    assert IndependenceGroup.EXISTENCE_GATE not in groups
    assert IndependenceGroup.BLIND_VERIFIER not in groups
    assert IndependenceGroup.CROSS_MODEL_RECALL not in groups


@pytest.mark.parametrize("contract", list(all_contracts()), ids=lambda c: c.relation)
def test_the_disabled_factual_decoding_branch_never_enters_m(contract):
    assert IndependenceGroup.FACTUAL_DECODING not in acquisition_groups(contract)


def test_an_unavailable_optional_family_leaves_the_denominator():
    """A mechanism that cannot run must not be counted as a permanent miss."""
    contract = get_contract(BORDERS)
    full = acquisition_groups(contract, ScoringConfig(optional_views_available=True))
    limited = acquisition_groups(contract, ScoringConfig(optional_views_available=False))
    assert set(limited) < set(full)
    reachable = set(
        eligible_groups_for(
            [get_view(contract.relation, v).family for v in contract.mandatory_views]
        )
    )
    assert set(limited) == reachable


def test_an_unexecuted_but_available_family_stays_in_the_denominator():
    """m(o) is availability, never execution.

    Counting only executed groups would make one direct view look like total
    coverage and tell Module 6 to stop before anything else was tried.
    """
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    config = ScoringConfig(optional_views_available=True)
    assert len(acquisition_groups(contract, config)) == 6
    assert coverage_q(cand, contract, config) == pytest.approx(1 / 6)
    assert coverage_q(cand, contract, config) < 1.0


def test_the_active_controller_denominator_does_not_collapse_to_executed_only():
    config = PipelineConfig(enable_active_controller=True)
    assert config.scoring.optional_views_available is True
    contract = get_contract(BORDERS)
    assert len(acquisition_groups(contract, config.scoring)) == len(
        contract.eligible_independence_groups
    )


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, False),                                     # fixed mandatory-only
        ({"run_optional_views": True}, True),            # fixed multi-view
        ({"enable_active_controller": True}, True),      # target architecture
    ],
)
def test_the_run_mode_decides_availability(kwargs, expected):
    config = PipelineConfig(**kwargs)
    assert config.scoring.optional_views_available is expected
    # Selection must see the same rule, or Phase C would score differently.
    assert config.selection.scoring.optional_views_available is expected


# --- 16-19. the three uncertainties stay distinct ----------------------------


@pytest.mark.parametrize("q,expected", [(0.0, 0.0), (1.0, 0.0)])
def test_inclusion_uncertainty_is_zero_at_the_boundaries(q, expected):
    assert inclusion_uncertainty(q) == pytest.approx(expected)
    assert not math.isnan(inclusion_uncertainty(q))


def test_inclusion_uncertainty_is_maximal_at_one_half():
    assert inclusion_uncertainty(0.5) == pytest.approx(math.log(2))
    for q in (0.1, 0.25, 0.4, 0.6, 0.75, 0.9):
        assert inclusion_uncertainty(q) < inclusion_uncertainty(0.5)


def test_low_inclusion_uncertainty_is_not_a_confidence_signal():
    """H_inc is 0 at q=0 and q=1; only q says which situation it is."""
    contract = get_contract(BORDERS)
    nothing = candidate("nothing")
    everything = candidate("everything")
    for group in contract.eligible_independence_groups:
        support(everything, group)

    a = candidate_state(nothing, contract)
    b = candidate_state(everything, contract)
    assert a.inclusion_uncertainty == pytest.approx(b.inclusion_uncertainty)
    assert a.coverage == 0.0 and b.coverage == pytest.approx(1.0)


def test_the_three_uncertainties_are_separately_inspectable():
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    verified(cand, VerificationLabel.VALID, 0.5, 0.3, 0.2, entropy=0.9, disagreement=0.3)
    score_candidate(cand, contract, DEFAULT_SCORING)
    state = candidate_state(cand, contract)

    assert state.inclusion_uncertainty == pytest.approx(inclusion_uncertainty(1 / 6))
    assert state.verifier_entropy == pytest.approx(0.9)
    assert state.prompt_disagreement == pytest.approx(0.3)
    # Three different numbers, none overwriting another.
    assert len({round(state.inclusion_uncertainty, 6), 0.9, 0.3}) == 3
    # U(o) carries only U_prompt; the other two are not summed into it.
    assert disagreement_term(cand) == pytest.approx(0.3)


# --- 20-22. L(o) ------------------------------------------------------------


def test_the_logit_term_uses_calibrated_probabilities():
    cand = candidate()
    cand.verifications.append(
        VerificationResult(candidate_key="alpha", label=VerificationLabel.VALID,
                           valid_prob=0.8, invalid_prob=0.15, unknown_prob=0.05,
                           raw_logits={"VALID": -5.0, "INVALID": 5.0, "UNKNOWN": 0.0},
                           calibrated=True)
    )
    # The stored probabilities are calibrated; the raw logits say the opposite.
    assert logit_term(cand) > 0


def test_no_verification_gives_a_neutral_logit_term():
    assert logit_term(candidate()) == 0.0
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    assert logit_term(cand) == 0.0, "an unverified candidate must not be penalised"


@pytest.mark.parametrize(
    "valid,invalid,unknown",
    [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1e-12, 0.5, 0.5)],
)
def test_extreme_probabilities_stay_finite_and_clipped(valid, invalid, unknown):
    cand = candidate()
    cand.verifications.append(
        VerificationResult(candidate_key="alpha", label=VerificationLabel.VALID,
                           valid_prob=valid, invalid_prob=invalid, unknown_prob=unknown)
    )
    value = logit_term(cand)
    assert math.isfinite(value)
    assert -1.0 <= value <= 1.0


def test_invalid_and_unknown_stay_separate_in_stored_state():
    cand = verified(candidate(), VerificationLabel.UNKNOWN, 0.2, 0.3, 0.5)
    latest = cand.verifications[-1]
    assert latest.invalid_prob == pytest.approx(0.3)
    assert latest.unknown_prob == pytest.approx(0.5)


# --- 23-27. what is and is not a contradiction -------------------------------


def test_absence_from_another_view_is_not_a_contradiction():
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    # Five other mechanisms never mentioned it. That is missing support, not
    # evidence against it.
    assert contradicting_groups(cand) == ()
    assert contradiction_term(cand, contract) == 0.0


def test_a_candidate_the_second_model_did_not_recall_is_not_contradicted():
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    assert cross_model_term(cand) == 0.0          # no X credit...
    assert contradiction_term(cand, contract) == 0.0   # ...but no penalty either


def test_an_unknown_verdict_is_not_a_contradiction():
    contract = get_contract(BORDERS)
    cand = verified(candidate(), VerificationLabel.UNKNOWN, 0.2, 0.2, 0.6)
    assert contradicting_groups(cand) == ()
    assert contradiction_term(cand, contract) == 0.0


def test_an_unrun_optional_view_is_not_a_contradiction():
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    assert contradiction_term(cand, contract) == 0.0


def test_shown_candidate_verification_never_becomes_independent_recall():
    cand = verified(candidate(), VerificationLabel.VALID, 0.9, 0.05, 0.05)
    group = cand.groups[IndependenceGroup.BLIND_VERIFIER]
    assert all(e.mode is EvidenceMode.SHOWN_CANDIDATE for e in group.supports)
    assert cross_model_term(cand) == 0.0


def test_independent_recall_never_becomes_blind_verification():
    cand = support(candidate(), IndependenceGroup.CROSS_MODEL_RECALL)
    assert IndependenceGroup.BLIND_VERIFIER not in cand.groups
    assert logit_term(cand) == 0.0
    assert cross_model_term(cand) == pytest.approx(1.0)


# --- 28-30. monotonicity ------------------------------------------------------


def test_more_independent_support_never_lowers_the_score():
    contract = get_contract(BORDERS)
    previous = -math.inf
    cand = candidate()
    for group in contract.eligible_independence_groups:
        support(cand, group)
        total = score_candidate(cand, contract, DEFAULT_SCORING).total
        assert total >= previous
        previous = total


def test_stronger_contradiction_never_raises_the_score():
    contract = get_contract(BORDERS)
    weak = verified(support(candidate("a"), IndependenceGroup.DIRECT_RECALL),
                    VerificationLabel.INVALID, 0.4, 0.5, 0.1)
    strong = verified(support(candidate("b"), IndependenceGroup.DIRECT_RECALL),
                      VerificationLabel.INVALID, 0.02, 0.96, 0.02)
    assert (
        score_candidate(strong, contract, DEFAULT_SCORING).total
        <= score_candidate(weak, contract, DEFAULT_SCORING).total
    )


def test_higher_disagreement_never_raises_the_score():
    contract = get_contract(BORDERS)
    calm = verified(support(candidate("a"), IndependenceGroup.DIRECT_RECALL),
                    VerificationLabel.VALID, 0.8, 0.1, 0.1, disagreement=0.0)
    noisy = verified(support(candidate("b"), IndependenceGroup.DIRECT_RECALL),
                     VerificationLabel.VALID, 0.8, 0.1, 0.1, disagreement=0.5)
    assert (
        score_candidate(noisy, contract, DEFAULT_SCORING).total
        < score_candidate(calm, contract, DEFAULT_SCORING).total
    )


def test_stronger_valid_evidence_never_lowers_the_logit_term():
    previous = -math.inf
    for valid in (0.34, 0.5, 0.7, 0.9, 0.99):
        cand = candidate()
        rest = (1.0 - valid) / 2
        cand.verifications.append(
            VerificationResult(candidate_key="alpha", label=VerificationLabel.VALID,
                               valid_prob=valid, invalid_prob=rest, unknown_prob=rest)
        )
        value = logit_term(cand)
        assert value >= previous
        previous = value


# --- 31-33. score integrity ---------------------------------------------------


def test_a_hard_rejected_candidate_cannot_be_rescued_by_score():
    contract = get_contract(BORDERS)
    cand = candidate()
    for group in contract.eligible_independence_groups:
        support(cand, group)
    verified(cand, VerificationLabel.VALID, 0.99, 0.005, 0.005)
    support(cand, IndependenceGroup.CROSS_MODEL_RECALL)
    cand.status = CandidateStatus.REJECTED

    breakdown = score_candidate(cand, contract, DEFAULT_SCORING)
    assert breakdown.total > DEFAULT_SCORING.accept_score      # maximal evidence
    assert decide_status(cand, contract) is CandidateStatus.REJECTED
    assert assign_tier(cand, contract) is VerificationTier.HARD_REJECT


def test_the_breakdown_sums_exactly_to_the_reported_total():
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    support(cand, IndependenceGroup.CROSS_MODEL_RECALL)
    verified(cand, VerificationLabel.VALID, 0.7, 0.2, 0.1, disagreement=0.2)
    b = score_candidate(cand, contract, DEFAULT_SCORING)
    w = b.weights
    assert b.total == pytest.approx(
        w["alpha_support"] * b.support
        + w["beta_logit"] * b.logit
        + w["gamma_cross_model"] * b.cross_model
        - w["delta_contradiction"] * b.contradiction
        - w["eta_disagreement"] * b.disagreement
    )
    assert cand.score == pytest.approx(b.total)


def test_every_coefficient_comes_from_configuration():
    """No weight may be a literal inside the summation."""
    source = inspect.getsource(score_candidate)
    tree = ast.parse(source.lstrip())
    numbers = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    ]
    assert not numbers, f"hard-coded constants in score_candidate: {numbers}"
    for name in ("alpha_support", "beta_logit", "gamma_cross_model",
                 "delta_contradiction", "eta_disagreement"):
        assert name in DEFAULT_SCORING.weights()


def test_no_learned_fusion_exists():
    source = Path(inspect.getfile(score_candidate)).read_text()
    tree = ast.parse(source)
    banned_modules = {"sklearn", "torch", "scipy", "numpy", "xgboost", "lightgbm"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] not in banned_modules for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned_modules
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in {"fit", "partial_fit", "train", "backward", "step"}


# --- 34-36. status derives from state, never replaces it ---------------------


def test_the_unknown_rescue_cannot_be_fed_by_the_verifier_itself():
    """The rescue must read acquisition support, or it is circular.

    A candidate verified VALID once and UNKNOWN later carries a verifier
    SUPPORT edge. Borders auto-accepts at 2, so counting that edge would let
    the verifier supply the second "independent" support that rescues its own
    later UNKNOWN. Only acquisition mechanisms may count.
    """
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    verified(cand, VerificationLabel.VALID, 0.9, 0.05, 0.05)     # adds a SUPPORT edge
    verified(cand, VerificationLabel.UNKNOWN, 0.2, 0.2, 0.6)

    assert cand.independent_support == 2, "raw count still includes the verifier"
    assert len(supporting_acquisition_groups(cand, contract)) == 1
    assert decide_status(cand, contract) is CandidateStatus.UNRESOLVED


def test_the_unknown_rescue_is_currently_unreachable():
    """Recorded finding, not an endorsement.

    ``read_labels`` labels by argmax, so an UNKNOWN verdict implies
    ``valid_prob < 0.5``. Every contract sets ``min_valid_prob >= 0.5``, so the
    later probability check returns UNRESOLVED regardless of how the rescue
    branch decides. The rescue can therefore never rescue today. It is kept
    because it is the correct semantics if a relation ever adopts a recall-first
    operating point below 0.5 - and it is now fed the right support notion.
    """
    for contract in all_contracts():
        effective = resolve_verification(contract, DEFAULT_SCORING)
        assert effective.min_valid_prob >= 0.5

        cand = candidate(relation=contract.relation)
        for group in contract.eligible_independence_groups:
            support(cand, group)
        verified(cand, VerificationLabel.UNKNOWN, 0.34, 0.33, 0.33)
        # Maximal acquisition support, yet still unresolved - via min_valid_prob.
        assert len(supporting_acquisition_groups(cand, contract)) == len(
            acquisition_groups(contract)
        )
        assert decide_status(cand, contract) is CandidateStatus.UNRESOLVED


def test_status_does_not_erase_the_evidence_behind_it():
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    verified(cand, VerificationLabel.INVALID, 0.05, 0.9, 0.05)
    score_candidate(cand, contract, DEFAULT_SCORING)
    cand.status = decide_status(cand, contract)

    assert cand.status is CandidateStatus.REJECTED
    state = candidate_state(cand, contract)
    # The supporting history survives the rejection.
    assert IndependenceGroup.DIRECT_RECALL in state.support_groups
    assert state.contradicting_groups == (IndependenceGroup.BLIND_VERIFIER,)
    assert cand.groups[IndependenceGroup.DIRECT_RECALL].supports_candidate


def test_an_unknown_verdict_does_not_erase_support():
    contract = get_contract(BORDERS)
    cand = support(candidate(), IndependenceGroup.DIRECT_RECALL)
    verified(cand, VerificationLabel.UNKNOWN, 0.3, 0.3, 0.4)
    state = candidate_state(cand, contract)
    assert IndependenceGroup.DIRECT_RECALL in state.support_groups
    assert state.verifier_label == VerificationLabel.UNKNOWN.value


# --- 37. query-level state ---------------------------------------------------


def test_query_state_separates_available_from_supported():
    contract = get_contract(BORDERS)
    a = support(candidate("a"), IndependenceGroup.DIRECT_RECALL)
    b = support(candidate("b"), IndependenceGroup.CONTRASTIVE_SEPARATION)
    state = query_state([a, b], contract)

    assert len(state["available_acquisition_groups"]) == 6
    assert set(state["supporting_acquisition_groups"]) == {
        IndependenceGroup.DIRECT_RECALL.value,
        IndependenceGroup.CONTRASTIVE_SEPARATION.value,
    }
    # What Module 6 needs to judge residual value: mechanisms still untried.
    assert len(state["unexplored_acquisition_groups"]) == 4
    assert state["num_candidates"] == 2


def test_query_state_computes_no_residual_or_action():
    """Module 5 exposes state; Module 6 owns residual search value."""
    # Inspect calls, not prose: the docstring legitimately names what Module 6 owns.
    tree = ast.parse(inspect.getsource(query_state).lstrip())
    calls = {
        getattr(n.func, "attr", getattr(n.func, "id", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    for owned_by_module_6 in ("estimate_residual", "marginal_yield", "saturation",
                              "choose_action", "set_stability"):
        assert owned_by_module_6 not in calls


# --- 38-39. numeric and null-single boundaries -------------------------------


def test_numeric_candidates_are_not_clustered_here():
    contract = get_contract("hasArea")
    near = []
    for i, value in enumerate((100.0, 101.0, 102.0)):
        cand = candidate(key=f"{value}", relation=contract.relation)
        cand.numeric_value = value
        support(cand, IndependenceGroup.DIRECT_RECALL, run=f"r{i}")
        near.append(cand)
    state = query_state(near, contract)
    # Three distinct nodes survive: merging by tolerance is Module 8's job.
    assert state["num_candidates"] == 3
    for source_fn in (support_term, coverage_q, candidate_state):
        assert "cluster" not in inspect.getsource(source_fn).lower()


def test_the_same_scalar_from_one_mechanism_is_not_independent():
    contract = get_contract("hasArea")
    cand = candidate(key="100.0", relation=contract.relation)
    cand.numeric_value = 100.0
    for i in range(4):
        support(cand, IndependenceGroup.DIRECT_RECALL, run=f"r{i}")
    assert len(supporting_acquisition_groups(cand, contract)) == 1


def test_a_null_single_uncertainty_does_not_become_confident_empty():
    contract = get_contract("personHasCityOfDeath")
    cand = support(candidate(relation=contract.relation), IndependenceGroup.DIRECT_RECALL)
    verified(cand, VerificationLabel.UNKNOWN, 0.3, 0.3, 0.4)
    status = decide_status(cand, contract)
    # Unresolved, never a confident rejection that would read as a clean empty.
    assert status is CandidateStatus.UNRESOLVED
    assert candidate_state(cand, contract).verifier_label == VerificationLabel.UNKNOWN.value


# --- 40-42. determinism, round-trip, compliance ------------------------------


def test_module_5_state_survives_a_staged_round_trip(tmp_path):
    from cover_kbc.staging import StageWriter, read_stage

    relation, subject = BORDERS, "Testland"
    enumerator = ScriptedRuntime(
        fallback=lambda r: "Alpha; Beta" if r.metadata.get("view_id") == "borders_direct" else "Alpha",
        model_id="offline/mistral", family="offline-mistral", role="enumerator",
    )
    verifier = ScriptedRuntime(
        fallback=lambda r: "Alpha",
        label_scores={("blind_verifier", subject, relation):
                      {"VALID": 2.0, "INVALID": -1.0, "UNKNOWN": 0.0}},
        model_id="offline/qwen", family="offline-qwen", role="verifier",
    )
    config = PipelineConfig(
        mode=ExecutionMode.STAGED, enable_verifier=True, use_calibration=True,
        max_verifications_per_query=4, enable_cross_model_recall=True,
        enable_active_controller=True,
    )
    pipeline = CoverPipeline(enumerator, config, verifier_runtime=verifier)

    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate([Query(subject, relation, 0)]):
            writer.write(graph)
    with StageWriter(tmp_path / "b.jsonl") as writer:
        for graph in pipeline.verify(read_stage(tmp_path / "a.jsonl")):
            writer.write(graph)

    graph = list(read_stage(tmp_path / "b.jsonl"))[0]
    contract = graph.contract
    before = query_state(graph.active_candidates(), contract, config.scoring)

    # Round-trip the already-persisted graph once more and recompute.
    with StageWriter(tmp_path / "c.jsonl") as writer:
        writer.write(graph)
    reloaded = list(read_stage(tmp_path / "c.jsonl"))[0]
    after = query_state(reloaded.active_candidates(), contract, config.scoring)

    assert before == after
    assert before["num_candidates"] > 0


def test_module_5_performs_no_retrieval():
    source = Path(inspect.getfile(score_candidate)).read_text()
    tree = ast.parse(source)
    banned = {"requests", "urllib", "httpx", "aiohttp", "socket", "wikipedia", "wikidata"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] not in banned for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned


def test_the_scripted_pipeline_stays_deterministic():
    script = {
        ("borders_direct", "Testland", BORDERS): ["Alpha; Beta"],
        ("borders_compass", "Testland", BORDERS): ["Alpha; Gamma"],
    }
    outputs = []
    for _ in range(3):
        result = CoverPipeline(
            ScriptedRuntime(script), PipelineConfig(run_optional_views=True)
        ).run([Query("Testland", BORDERS, 0)])
        prediction = result.predictions[0]
        outputs.append(
            (
                tuple(prediction.object_entities),
                tuple(round(c.score, 9) for c in prediction.candidates),
            )
        )
    assert len(set(outputs)) == 1
