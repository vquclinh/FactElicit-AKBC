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
