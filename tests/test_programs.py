"""Module 1 conformance: the typed programme router and the four regimes.

Deterministic and synthetic throughout. No model is loaded anywhere.
"""

from __future__ import annotations

import dataclasses

import pytest

from cover_kbc.contracts.programs import (
    PROGRAMS,
    TypedProgramSpec,
    all_programs,
    check_program_compatibility,
    get_program,
)
from cover_kbc.contracts.registry import CONTRACTS, UnknownRelationError, all_contracts, get_contract
from cover_kbc.contracts.router import (
    PROGRAM_BY_RELATION,
    check_router_consistency,
    route,
    route_program,
)
from cover_kbc.types import Cardinality, OutputType, ProgramType, ViewFamily

#: Spec section 6, Table 4 - transcribed here independently of the source.
SPEC_TABLE_4 = {
    "countryLandBordersCountry": ProgramType.SMALL_SET,
    "companyTradesAtStockExchange": ProgramType.SMALL_SET,
    "personHasCityOfDeath": ProgramType.NULL_SINGLE,
    "hasArea": ProgramType.NUMERIC,
    "hasCapacity": ProgramType.NUMERIC,
    "awardWonBy": ProgramType.LARGE_OPEN_SET,
}


# --- routing correctness ---------------------------------------------------


def test_exactly_six_relations_are_routed():
    assert set(PROGRAM_BY_RELATION) == set(SPEC_TABLE_4)
    assert len(CONTRACTS) == 6


@pytest.mark.parametrize("relation,expected", sorted(SPEC_TABLE_4.items()))
def test_each_relation_routes_to_its_spec_programme(relation, expected):
    assert route(relation) is expected
    assert route_program(relation).program_type is expected


def test_routing_agrees_with_the_contract():
    for contract in all_contracts():
        assert route(contract.relation) is contract.program_type


def test_router_consistency_check_passes():
    check_router_consistency()


def test_unknown_relation_fails_loudly():
    for unknown in ("seriesHasNumberOfEpisodes", "", "hasarea", "totally made up"):
        with pytest.raises(UnknownRelationError):
            route(unknown)
        with pytest.raises(UnknownRelationError):
            route_program(unknown)


def test_there_is_no_default_fallback_programme():
    """An unrouted relation must never silently inherit a programme."""
    with pytest.raises(UnknownRelationError):
        route("countryLandBordersCountryX")
    # An unknown programme fails closed rather than defaulting to one.
    for unknown in ("BIG_SET", "", "small_set", "awardWonBy"):
        with pytest.raises(KeyError):
            get_program(unknown)


def test_program_lookup_accepts_the_serialised_enum_value():
    """`ProgramType` is a str-Enum, so a round-tripped JSON value still resolves.

    Deliberate: stage files persist `program_type` as a string, and rehydrating
    one must not need a manual enum conversion. Unknown strings still raise.
    """
    assert get_program(ProgramType.NUMERIC.value) is get_program(ProgramType.NUMERIC)


def test_routing_is_deterministic_and_stateless():
    first = [route(r) for r in sorted(SPEC_TABLE_4)]
    second = [route(r) for r in sorted(SPEC_TABLE_4)]
    third = [route(r) for r in sorted(SPEC_TABLE_4, reverse=True)]
    assert first == second
    assert sorted(r.value for r in first) == sorted(r.value for r in third)


def test_routing_involves_no_model_call():
    """The router is non-neural: it touches no runtime, no tokenizer, no logits."""
    import inspect

    from cover_kbc.contracts import programs, router

    for module in (router, programs):
        source = inspect.getsource(module)
        for forbidden in ("generate(", "score_labels", "LMRuntime", "tokenizer", "torch"):
            assert forbidden not in source, f"{module.__name__} references {forbidden}"


def test_router_holds_no_factual_object_knowledge():
    import inspect

    from cover_kbc.contracts import programs, router

    for module in (router, programs):
        source = inspect.getsource(module)
        # Relation ids are structure; digits would suggest smuggled facts.
        body = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        for factual in ("Denmark", "Poland", "NYSE", "Nobel", "Wikidata"):
            assert factual not in body


# --- the four programme regimes -------------------------------------------


def test_every_program_type_has_exactly_one_definition():
    assert set(PROGRAMS) == set(ProgramType)
    assert len(all_programs()) == 4


@pytest.mark.parametrize(
    "program_type,cardinality,output,bounded,missingness",
    [
        (ProgramType.SMALL_SET, Cardinality.ZERO_OR_MANY_SMALL, OutputType.ENTITY, False, True),
        (ProgramType.NULL_SINGLE, Cardinality.ZERO_OR_ONE, OutputType.ENTITY, True, False),
        (ProgramType.NUMERIC, Cardinality.EXACTLY_ONE, OutputType.NUMBER, True, False),
        (ProgramType.LARGE_OPEN_SET, Cardinality.ZERO_OR_MANY_LARGE, OutputType.ENTITY, False, True),
    ],
)
def test_program_regimes_match_the_specification(
    program_type, cardinality, output, bounded, missingness
):
    spec = get_program(program_type)
    assert cardinality in spec.allowed_cardinalities
    assert spec.required_output_type is output
    assert spec.bounded is bounded
    assert spec.supports_missingness is missingness


def test_null_single_and_numeric_are_the_bounded_regimes():
    bounded = {s.program_type for s in all_programs() if s.bounded}
    assert bounded == {ProgramType.NULL_SINGLE, ProgramType.NUMERIC}
    for program_type in bounded:
        assert get_program(program_type).max_objects == 1


def test_only_open_regimes_support_missingness():
    supporting = {s.program_type for s in all_programs() if s.supports_missingness}
    assert supporting == {ProgramType.SMALL_SET, ProgramType.LARGE_OPEN_SET}


def test_numeric_is_the_only_numeric_regime():
    numeric = {
        s.program_type for s in all_programs() if s.required_output_type is OutputType.NUMBER
    }
    assert numeric == {ProgramType.NUMERIC}


# --- programme / contract compatibility invariants (spec section 6) --------


def test_every_shipped_contract_is_programme_compatible():
    for contract in all_contracts():
        assert check_program_compatibility(contract) == []


def test_null_single_rejects_a_many_cardinality():
    bad = dataclasses.replace(
        get_contract("personHasCityOfDeath"), cardinality=Cardinality.ZERO_OR_MANY_LARGE
    )
    assert any("cardinality" in p for p in check_program_compatibility(bad))
    with pytest.raises(ValueError, match="cardinality"):
        bad.validate()


def test_numeric_rejects_entity_output():
    bad = dataclasses.replace(
        get_contract("hasArea"), output_type=OutputType.ENTITY
    )
    assert any("output type" in p for p in check_program_compatibility(bad))
    with pytest.raises(ValueError, match="output type"):
        bad.validate()


def test_large_open_set_rejects_scalar_numeric_output():
    bad = dataclasses.replace(
        get_contract("hasArea"),
        program_type=ProgramType.LARGE_OPEN_SET,
        cardinality=Cardinality.ZERO_OR_MANY_LARGE,
    )
    assert any("output type" in p for p in check_program_compatibility(bad))


def test_a_bounded_regime_rejects_an_oversized_selection_cap():
    contract = get_contract("personHasCityOfDeath")
    bad = dataclasses.replace(
        contract, selection=dataclasses.replace(contract.selection, max_objects=5)
    )
    assert any("cap" in problem for problem in check_program_compatibility(bad))
    with pytest.raises(ValueError, match="cap"):
        bad.validate()


def test_a_non_missingness_regime_rejects_a_missingness_view_family():
    borders = get_contract("countryLandBordersCountry")
    bad = dataclasses.replace(get_contract("hasArea"), view_families=borders.view_families)
    assert any("missingness" in p for p in check_program_compatibility(bad))
    with pytest.raises(ValueError, match="missingness"):
        bad.validate()


def test_the_router_reports_programme_incompatibility(monkeypatch):
    """`check_router_consistency` surfaces regime violations, not just mappings."""
    broken = dataclasses.replace(
        get_contract("personHasCityOfDeath"), cardinality=Cardinality.ZERO_OR_MANY_LARGE
    )
    monkeypatch.setitem(CONTRACTS, "personHasCityOfDeath", broken)
    with pytest.raises(ValueError, match="cardinality"):
        check_router_consistency()


# --- downstream modules consume the authoritative definition ---------------


def test_contract_cap_is_derived_from_the_programme():
    """`max_objects` is a programme fact, not a locally re-derived rule."""
    for contract in all_contracts():
        spec = contract.program
        if spec.bounded:
            assert contract.max_objects == spec.max_objects
        else:
            assert contract.max_objects == contract.selection.max_objects


def test_null_single_cannot_finalize_more_than_one_object():
    """Module 8 reads the cap from Module 1 rather than hard-coding it."""
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.selection import finalize
    from cover_kbc.types import (
        DecodeProfile,
        GenerationRecord,
        IndependenceGroup,
        Query,
    )

    contract = get_contract("personHasCityOfDeath")
    query = Query("Testperson", contract.relation, 0)
    graph = build_graph(query, contract)
    record = GenerationRecord(
        record_id="r1", query=query, view_id="death_city_direct",
        view_family=ViewFamily.DIRECT, independence_group=IndependenceGroup.DIRECT_RECALL,
        run_id=0, model_id="stub", prompt="p", prompt_hash="h", raw_output="o",
        decode_profile=DecodeProfile(), generated_tokens=5,
    )
    graph.add_entity_mentions(record, ["Testville", "Otherville", "Thirdville"])
    assert len(graph.candidates) == 3
    assert len(finalize(graph).object_entities) <= contract.program.max_objects == 1


def test_numeric_regime_never_enters_the_entity_list_parser():
    from cover_kbc.elicitation.parsing import parse_entities

    for contract in all_contracts():
        if contract.program.required_output_type is not OutputType.NUMBER:
            continue
        with pytest.raises(TypeError, match="numeric relation"):
            parse_entities("35,000", contract)


def test_large_open_set_exposes_facet_and_missingness_capability():
    contract = get_contract("awardWonBy")
    assert contract.program.supports_missingness
    assert ViewFamily.MISSINGNESS in contract.view_families
    # Several structural facets exist under one mechanism.
    facets = {
        v for v in contract.all_views() if v.startswith("award_facet")
    }
    assert len(facets) >= 2


def test_small_set_does_not_inherit_unrestricted_open_set_expansion():
    """The action space is bounded by the contract's declared views."""
    from cover_kbc.controller import legal_actions
    from cover_kbc.coverage import RCSEState
    from cover_kbc.types import Budget

    for contract in all_contracts():
        actions = legal_actions(contract, [], RCSEState(), Budget(max_calls=999))
        offered = {a.view_id for a in actions if a.view_id}
        assert offered <= set(contract.all_views())

    small = get_contract("countryLandBordersCountry")
    large = get_contract("awardWonBy")
    small_actions = legal_actions(small, [], RCSEState(), Budget(max_calls=999))
    # Candidate-conditioned views are not subject-only actions, so the offered
    # set is the declared views minus those.
    from cover_kbc.elicitation.library import get_view as _get_view

    subject_only = {v for v in small.all_views() if not _get_view(small.relation, v).is_reverse}
    assert {a.view_id for a in small_actions if a.view_id} == subject_only
    # The open-set regime genuinely has more to explore than the small one.
    assert len(large.all_views()) > len(small.all_views())


def test_program_type_reaches_every_regime_dispatcher():
    """Modules 6, 7 and 8 all dispatch on the routed programme."""
    from cover_kbc.controller import should_stop
    from cover_kbc.coverage import RCSEState, estimate_residual
    from cover_kbc.selection import _BY_PROGRAM
    from cover_kbc.types import Budget

    for program_type in ProgramType:
        assert program_type in _BY_PROGRAM        # Module 8 selector family

    rationales = set()
    for contract in all_contracts():
        estimate = estimate_residual(contract, [], RCSEState())
        assert estimate.program_type == contract.program_type.value
        rationales.add(estimate.rationale)
        # Module 7 stopping accepts the regime without raising.
        should_stop(contract, [], RCSEState(), Budget(), estimate)
    # Each regime has its own residual interpretation.
    assert len(rationales) == 4


# --- programme vs relation-contract boundary -------------------------------


def test_two_small_set_relations_share_the_regime_but_no_semantics():
    borders = get_contract("countryLandBordersCountry")
    stock = get_contract("companyTradesAtStockExchange")

    assert borders.program is stock.program              # same regime object
    assert borders.program_type is stock.program_type

    # ...and nothing factual or definitional is shared.
    assert borders.definition != stock.definition
    assert set(borders.positive_rules).isdisjoint(stock.positive_rules)
    assert set(borders.hard_negative_rules).isdisjoint(stock.hard_negative_rules)
    assert set(borders.all_views()).isdisjoint(stock.all_views())


def test_two_numeric_relations_share_the_regime_but_not_their_units():
    area = get_contract("hasArea")
    capacity = get_contract("hasCapacity")
    assert area.program is capacity.program
    assert area.selection.numeric_target_unit != capacity.selection.numeric_target_unit
    assert area.selection.numeric_integer_only != capacity.selection.numeric_integer_only


def test_the_programme_registry_holds_no_relation_semantics():
    """Module 1 must not absorb Module 0's definitions."""
    import inspect

    from cover_kbc.contracts import programs

    source = inspect.getsource(programs)
    for relation in SPEC_TABLE_4:
        # Relation ids may appear in prose comments, never in the data table.
        table = source[source.index("PROGRAMS: dict") : source.index("def get_program")]
        assert relation not in table


def test_program_spec_is_immutable():
    spec = get_program(ProgramType.NUMERIC)
    assert isinstance(spec, TypedProgramSpec)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.max_objects = 99  # type: ignore[misc]
