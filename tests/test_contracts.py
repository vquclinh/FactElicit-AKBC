"""Relation contracts, the typed program router, and the view library."""

from __future__ import annotations

import pytest

from cover_kbc import RELATIONS
from cover_kbc.contracts.registry import CONTRACTS, UnknownRelationError, all_contracts, get_contract
from cover_kbc.contracts.router import PROGRAM_BY_RELATION, check_router_consistency, route
from cover_kbc.elicitation.library import check_library_covers_contracts, get_view
from cover_kbc.evaluation.official import relation_types
from cover_kbc.types import Cardinality, OutputType, ProgramType


def test_every_official_relation_has_a_contract():
    assert set(CONTRACTS) == set(RELATIONS) == set(relation_types())


def test_router_matches_the_specification_table():
    expected = {
        "countryLandBordersCountry": ProgramType.SMALL_SET,
        "companyTradesAtStockExchange": ProgramType.SMALL_SET,
        "personHasCityOfDeath": ProgramType.NULL_SINGLE,
        "hasArea": ProgramType.NUMERIC,
        "hasCapacity": ProgramType.NUMERIC,
        "awardWonBy": ProgramType.LARGE_OPEN_SET,
    }
    assert PROGRAM_BY_RELATION == expected
    for relation, program in expected.items():
        assert route(relation) is program


def test_router_consistency_check_passes():
    """Contracts agree with the spec table and the official RELATION_TYPE map."""
    check_router_consistency()


def test_view_library_and_contracts_agree():
    check_library_covers_contracts()


def test_unknown_relation_raises():
    with pytest.raises(UnknownRelationError):
        get_contract("seriesHasNumberOfEpisodes")


@pytest.mark.parametrize("contract", all_contracts(), ids=lambda c: c.relation)
def test_contract_is_internally_consistent(contract):
    contract.validate()


def test_numeric_relations_are_numeric_to_the_evaluator():
    official = relation_types()
    for contract in all_contracts():
        expected = OutputType.NUMBER if official[contract.relation] == "numeric" else OutputType.ENTITY
        assert contract.output_type is expected


def test_numeric_relations_expect_exactly_one_object():
    for relation in ("hasArea", "hasCapacity"):
        contract = get_contract(relation)
        assert contract.cardinality is Cardinality.EXACTLY_ONE
        assert contract.max_objects == 1


def test_death_city_allows_an_empty_answer():
    contract = get_contract("personHasCityOfDeath")
    assert contract.allows_empty
    assert contract.max_objects == 1


def test_verifier_definition_states_both_positives_and_near_misses():
    for contract in all_contracts():
        text = contract.verifier_definition()
        assert "Counts as correct:" in text
        assert "Does NOT count:" in text
        for rule in contract.hard_negative_rules:
            assert rule in text


def test_every_mandatory_view_renders_with_the_subject():
    for contract in all_contracts():
        for view_id in contract.mandatory_views:
            view = get_view(contract.relation, view_id)
            prompt = view.render(
                subject="SUBJECT_X", definition=contract.verifier_definition(), accepted=[]
            )
            assert "SUBJECT_X" in prompt
            assert "{" not in prompt.replace("{}", "")


def test_missingness_views_receive_the_accepted_set():
    contract = get_contract("countryLandBordersCountry")
    view = get_view(contract.relation, "borders_missing")
    assert view.needs_accepted_set
    prompt = view.render(subject="S", definition=contract.definition, accepted=["Alpha", "Beta"])
    assert "Alpha; Beta" in prompt


def test_contracts_contain_no_factual_lookup_tables():
    """A contract may define semantics; it may never carry world knowledge."""
    for contract in all_contracts():
        blob = " ".join(
            (*contract.positive_rules, *contract.hard_negative_rules, contract.definition)
        )
        # A definition talks about the subject generically, never about named entities.
        assert "subject" in blob.lower()
        assert not any(char.isdigit() for char in blob), contract.relation


# ==========================================================================
# Module 0 conformance: the contract must be executable, not dead metadata.
# ==========================================================================


def test_spec_5_1_required_fields_are_all_present():
    """Spec section 5.1 enumerates what a contract must specify."""
    for contract in all_contracts():
        assert contract.relation
        assert contract.program_type is not None          # program type
        assert contract.output_type is not None           # answer type
        assert contract.cardinality is not None           # cardinality regime
        assert contract.answer_type                       # answer type label
        assert contract.positive_rules                    # exact positive semantics
        assert contract.hard_negative_rules               # hard negatives / near misses
        assert contract.mandatory_views                   # mandatory views
        assert contract.normalization is not None         # normalizer
        assert contract.verification is not None          # verification policy
        assert contract.stopping is not None              # stopping policy
        assert contract.selection is not None             # final-selection policy


def test_answer_type_reaches_the_verifier_prompt():
    """`answer_type` is executable, not a label nobody reads."""
    for contract in all_contracts():
        assert contract.answer_type in contract.verifier_definition()


def test_near_miss_classes_reach_the_adversarial_template():
    """Spec section 10.5: the adversarial tier uses contract-specified near misses."""
    from cover_kbc.types import Query
    from cover_kbc.verification import TEMPLATE_ADVERSARIAL, build_verifier_prompt

    contract = get_contract("hasCapacity")
    assert contract.verification.adversarial_classes
    prompt = build_verifier_prompt(
        Query("Testvenue", contract.relation), contract, "52000", TEMPLATE_ADVERSARIAL
    )
    for near_miss in contract.verification.adversarial_classes:
        assert near_miss in prompt


def test_a_contract_without_near_misses_degrades_cleanly():
    contract = get_contract("hasArea")
    assert not contract.verification.adversarial_classes
    assert contract.near_miss_block() == ""


def test_verification_thresholds_reach_the_decision():
    """The contract's acceptance threshold is the one applied."""
    from cover_kbc.scoring import ScoringConfig, decide_status
    from cover_kbc.types import (
        Candidate,
        CandidateStatus,
        VerificationLabel,
        VerificationResult,
        VerificationTier,
    )

    contract = get_contract("companyTradesAtStockExchange")   # accept_valid_prob 0.6
    assert contract.verification.accept_valid_prob > 0.4

    candidate = Candidate(key="alpha", display_value="Alpha", relation=contract.relation)
    candidate.tier = VerificationTier.AUTO_ACCEPT
    candidate.score = 1.0
    candidate.verifications.append(
        VerificationResult(
            candidate_key="alpha", label=VerificationLabel.VALID,
            valid_prob=0.5, invalid_prob=0.3, unknown_prob=0.2,
        )
    )
    # 0.5 clears the global default (0.40) but not this contract's 0.6.
    lenient = ScoringConfig(min_valid_prob=0.40)
    assert decide_status(candidate, contract, lenient) is CandidateStatus.UNRESOLVED


def test_stopping_policy_reaches_the_controller():
    """Spec section 12.3: stopping is relation-typed, owned by the contract."""
    from cover_kbc.controller import resolve_stopping

    for contract in all_contracts():
        effective = resolve_stopping(contract)
        assert effective.source == f"contract:{contract.relation}"
        assert effective.saturation_patience == contract.stopping.saturation_patience
        assert effective.residual_stop == contract.stopping.residual_stop_threshold


def test_stopping_policy_bounds_the_query_budget():
    from cover_kbc.pipeline import PipelineConfig

    config = PipelineConfig(max_calls_per_query=99, max_generated_tokens_per_query=99999)
    for contract in all_contracts():
        budget = config.budget(contract)
        assert budget.max_calls == contract.stopping.max_calls
        assert budget.max_generated_tokens == contract.stopping.max_generated_tokens


def test_the_global_ceiling_still_caps_a_generous_contract():
    """A contract may spend less than the global ceiling, never more."""
    from cover_kbc.pipeline import PipelineConfig

    config = PipelineConfig(max_calls_per_query=2, max_generated_tokens_per_query=100)
    budget = config.budget(get_contract("awardWonBy"))
    assert budget.max_calls == 2
    assert budget.max_generated_tokens == 100


def test_selection_policy_reaches_parser_graph_and_selector():
    """`numeric_target_unit` drives unit conversion at parse time."""
    from cover_kbc.elicitation.parsing import parse_numeric_values

    area = get_contract("hasArea")
    capacity = get_contract("hasCapacity")
    assert area.selection.numeric_target_unit == "km2"
    assert capacity.selection.numeric_target_unit == "persons"
    # Square miles convert for area; an area unit is a type error for capacity.
    assert parse_numeric_values("100 square miles", area)[0] == pytest.approx(258.999, rel=1e-3)
    assert parse_numeric_values("100 square miles", capacity) == []


def test_normalization_policy_reaches_the_identity_key():
    for contract in all_contracts():
        merged = contract.normalization.merge_leading_article_variants
        same = contract.key("The Alpha") == contract.key("Alpha")
        assert same is merged


def test_program_type_selects_the_final_selector():
    """Module 0 routes Module 8; no if/elif chain leaks (invariant 8)."""
    from cover_kbc.selection import _BY_PROGRAM, _BY_RELATION

    for contract in all_contracts():
        selector = _BY_RELATION.get(contract.relation) or _BY_PROGRAM[contract.program_type]
        assert callable(selector)
    # Capacity and area are numeric but need different selectors.
    assert _BY_RELATION["hasCapacity"] is not _BY_RELATION["hasArea"]


def test_cardinality_bounds_the_emitted_set():
    assert get_contract("personHasCityOfDeath").max_objects == 1
    assert get_contract("hasArea").max_objects == 1
    assert get_contract("countryLandBordersCountry").max_objects == 0   # unbounded
    assert get_contract("awardWonBy").max_objects == 0


def test_validate_rejects_an_incoherent_contract():
    """The invariants are enforced, not decorative."""
    import dataclasses

    from cover_kbc.contracts.base import StoppingPolicy, VerificationPolicy

    good = get_contract("personHasCityOfDeath")

    # hasArea is EXACTLY_ONE, so a multi-object cap is incoherent for it.
    numeric = get_contract("hasArea")
    assert not numeric.allows_empty
    bad_cap = dataclasses.replace(
        numeric, selection=dataclasses.replace(numeric.selection, max_objects=5)
    )
    with pytest.raises(ValueError, match="exactly one object"):
        bad_cap.validate()

    bad_prob = dataclasses.replace(
        good, verification=VerificationPolicy(accept_valid_prob=1.7)
    )
    with pytest.raises(ValueError, match="probability"):
        bad_prob.validate()

    bad_budget = dataclasses.replace(good, stopping=StoppingPolicy(max_calls=0))
    with pytest.raises(ValueError, match="max_calls"):
        bad_budget.validate()


# --- official semantics, relation by relation ------------------------------


def _rules(relation: str) -> str:
    contract = get_contract(relation)
    return " ".join(
        (contract.definition, *contract.positive_rules, *contract.hard_negative_rules)
    ).lower()


def test_borders_semantics_match_the_official_definition():
    text = _rules("countryLandBordersCountry")
    assert "land" in text
    assert "maritime" in text                    # excluded
    assert "integral" in text                    # integral overseas territories count
    assert "enclave" in text or "enclaved" in text
    assert "recognised" in text                  # currently-recognised states only
    assert "disputed" in text or "deprecated" in text


def test_death_city_semantics_match_the_official_definition():
    text = _rules("personHasCityOfDeath")
    assert "died" in text or "death" in text
    assert "locality" in text
    assert "country" in text and "region" in text  # wrong granularity excluded
    assert "birth" in text                          # birthplace excluded
    assert "living" in text or "alive" in text      # living -> empty


def test_capacity_semantics_match_the_official_definition():
    contract = get_contract("hasCapacity")
    text = _rules("hasCapacity")
    assert "maximum" in text and "spectator" in text
    assert "highest published" in text
    assert "record" in text                      # record attendance excluded
    assert "average" in text                     # average attendance excluded
    assert "seated" in text                      # seated-only excluded
    assert contract.selection.numeric_integer_only     # integer number of people


def test_award_semantics_match_the_official_definition():
    text = _rules("awardWonBy")
    assert "recipient" in text
    assert "work" in text                        # winning work excluded
    assert "nominee" in text                     # nominee excluded
    assert "predecessor" in text and "successor" in text
    assert "rescinded" in text


def test_stock_exchange_semantics_match_the_official_definition():
    text = _rules("companyTradesAtStockExchange")
    assert "itself" in text                      # the company itself must be traded
    assert "parent" in text and "subsidiary" in text
    assert "empty answer set" in text or "privately held" in text


def test_area_semantics_match_the_official_definition():
    text = _rules("hasArea")
    assert "square kilometres" in text
    assert "total area" in text                  # land + inland water for countries
    assert "land-only" in text or "land only" in text
    assert "hectares" in text or "square miles" in text   # conversion required


# --- policy precedence: contract authoritative, global fallback ------------


def _verified_candidate(contract, *, p_valid, label=None, support=1):
    from cover_kbc.types import (
        Candidate,
        EdgeType,
        Evidence,
        IndependenceGroup,
        VerificationLabel,
        VerificationResult,
        VerificationTier,
    )

    candidate = Candidate(key="alpha", display_value="Alpha", relation=contract.relation)
    groups = [
        IndependenceGroup.DIRECT_RECALL,
        IndependenceGroup.STRUCTURAL_DECOMPOSITION,
        IndependenceGroup.CONTRASTIVE_SEPARATION,
    ]
    for i in range(support):
        candidate.add_evidence(Evidence("alpha", EdgeType.SUPPORT, groups[i], "v", "m", 0, f"r{i}"))
    candidate.tier = VerificationTier.AUTO_ACCEPT
    candidate.score = 1.0
    candidate.verifications.append(
        VerificationResult(
            candidate_key="alpha",
            label=label or VerificationLabel.VALID,
            valid_prob=p_valid,
            invalid_prob=(1.0 - p_valid) / 2,
            unknown_prob=(1.0 - p_valid) / 2,
        )
    )
    return candidate


def test_a_relation_may_choose_a_lower_acceptance_bar_than_the_global_default():
    """Recall-first operating points must be reachable (no clamping to global)."""
    import dataclasses

    from cover_kbc.contracts.base import VerificationPolicy
    from cover_kbc.scoring import ScoringConfig, decide_status, resolve_verification
    from cover_kbc.types import CandidateStatus

    base = get_contract("awardWonBy")
    recall_first = dataclasses.replace(
        base, verification=VerificationPolicy(accept_valid_prob=0.25)
    )
    config = ScoringConfig(min_valid_prob=0.40)   # global default is stricter

    assert resolve_verification(recall_first, config).min_valid_prob == pytest.approx(0.25)
    candidate = _verified_candidate(recall_first, p_valid=0.30)
    # 0.30 is below the global 0.40 but above this relation's own 0.25.
    assert decide_status(candidate, recall_first, config) is CandidateStatus.ACCEPTED


def test_an_undeclared_field_falls_back_to_the_global_default():
    import dataclasses

    from cover_kbc.contracts.base import VerificationPolicy
    from cover_kbc.scoring import ScoringConfig, resolve_verification

    silent = dataclasses.replace(get_contract("hasArea"), verification=VerificationPolicy())
    config = ScoringConfig(min_valid_prob=0.37, auto_accept_support=5, drop_on_unknown=False)
    effective = resolve_verification(silent, config)

    assert effective.min_valid_prob == pytest.approx(0.37)
    assert effective.auto_accept_support == 5
    assert effective.drop_on_unknown is False
    assert effective.source == "scoring_config"


def test_thresholds_are_never_combined_arithmetically():
    """No max()/min()/AND blending of contract and global policy."""
    import dataclasses

    from cover_kbc.contracts.base import VerificationPolicy
    from cover_kbc.scoring import ScoringConfig, resolve_verification

    contract = dataclasses.replace(
        get_contract("hasArea"),
        verification=VerificationPolicy(
            auto_accept_independent_support=1, accept_valid_prob=0.1, drop_on_unknown=False
        ),
    )
    strict = ScoringConfig(min_valid_prob=0.9, auto_accept_support=9, drop_on_unknown=True)
    effective = resolve_verification(contract, strict)

    # Every value is exactly the contract's, not a blend with the stricter global.
    assert effective.min_valid_prob == pytest.approx(0.1)
    assert effective.auto_accept_support == 1
    assert effective.drop_on_unknown is False


def test_drop_on_unknown_is_not_an_and_of_two_booleans():
    import dataclasses

    from cover_kbc.contracts.base import VerificationPolicy
    from cover_kbc.scoring import ScoringConfig, resolve_verification

    keeps = dataclasses.replace(
        get_contract("awardWonBy"), verification=VerificationPolicy(drop_on_unknown=False)
    )
    # Global says drop; the relation says keep. The relation wins.
    assert not resolve_verification(keeps, ScoringConfig(drop_on_unknown=True)).drop_on_unknown


def test_the_named_emergency_override_restores_global_policy():
    from cover_kbc.scoring import ScoringConfig, resolve_verification

    contract = get_contract("personHasCityOfDeath")
    forced = ScoringConfig(
        force_global_verification_policy=True, min_valid_prob=0.11, auto_accept_support=7
    )
    effective = resolve_verification(contract, forced)
    assert effective.min_valid_prob == pytest.approx(0.11)
    assert effective.auto_accept_support == 7
    assert effective.source == "scoring_config(forced)"


def test_resolution_source_is_reported_for_the_audit_trail():
    from cover_kbc.scoring import resolve_verification

    for contract in all_contracts():
        assert resolve_verification(contract).source.startswith("contract:")


def test_resource_ceilings_are_still_clamped_not_authoritative():
    """Calls/tokens are a safety limit, so `min` remains correct there."""
    import dataclasses

    from cover_kbc.contracts.base import StoppingPolicy
    from cover_kbc.pipeline import PipelineConfig

    greedy = dataclasses.replace(
        get_contract("awardWonBy"),
        stopping=StoppingPolicy(max_calls=9999, max_generated_tokens=999999),
    )
    budget = PipelineConfig(max_calls_per_query=5, max_generated_tokens_per_query=500).budget(greedy)
    assert budget.max_calls == 5
    assert budget.max_generated_tokens == 500
