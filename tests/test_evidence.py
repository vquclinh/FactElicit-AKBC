"""The evidence graph, independence accounting, and the final selector."""

from __future__ import annotations

import pytest

from cover_kbc.evidence.graph import apply_hard_contract_rules, build_graph
from cover_kbc.selection import finalize
from cover_kbc.types import (
    DecodeProfile,
    EdgeType,
    GenerationRecord,
    IndependenceGroup,
    Query,
    VerificationLabel,
    VerificationResult,
    ViewFamily,
)


def make_record(
    query: Query,
    view_id: str,
    family: ViewFamily,
    group: IndependenceGroup,
    run_id: int = 0,
    tokens: int = 10,
) -> GenerationRecord:
    return GenerationRecord(
        record_id=f"{view_id}:{run_id}",
        query=query,
        view_id=view_id,
        view_family=family,
        independence_group=group,
        run_id=run_id,
        model_id="test/stub",
        prompt="p",
        prompt_hash="h",
        raw_output="o",
        decode_profile=DecodeProfile(),
        generated_tokens=tokens,
        prompt_tokens=5,
    )


# --- the central independence invariant ------------------------------------


def test_repeated_runs_of_one_view_are_a_single_independent_support(
    borders_query, borders_contract
):
    """direct_run_1..3 are one evidence mechanism, not three."""
    graph = build_graph(borders_query, borders_contract)
    for run_id in range(3):
        record = make_record(
            borders_query, "borders_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL, run_id
        )
        graph.add_entity_mentions(record, ["Alpha"])

    candidate = graph.candidates["alpha"]
    assert candidate.independent_support == 1
    assert candidate.raw_support_count == 3
    assert candidate.supporting_groups == [IndependenceGroup.DIRECT_RECALL]


def test_structurally_different_views_are_independent_supports(borders_query, borders_contract):
    graph = build_graph(borders_query, borders_contract)
    for view_id, family, group in [
        ("borders_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL),
        ("borders_compass", ViewFamily.STRUCTURAL, IndependenceGroup.STRUCTURAL_DECOMPOSITION),
        ("borders_land_vs_maritime", ViewFamily.CONTRASTIVE, IndependenceGroup.CONTRASTIVE_SEPARATION),
    ]:
        graph.add_entity_mentions(make_record(borders_query, view_id, family, group), ["Alpha"])

    candidate = graph.candidates["alpha"]
    assert candidate.independent_support == 3
    assert candidate.raw_support_count == 3
    assert candidate.coverage(borders_contract.eligible_independence_groups) == pytest.approx(0.75)


def test_repeated_mention_inside_one_generation_counts_once(borders_query, borders_contract):
    graph = build_graph(borders_query, borders_contract)
    record = make_record(
        borders_query, "borders_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL
    )
    graph.add_entity_mentions(record, ["Alpha", "Alpha", "alpha"])
    assert graph.candidates["alpha"].raw_support_count == 1


# --- deduplication ---------------------------------------------------------


def test_alias_like_surface_forms_collapse_to_one_candidate(borders_query, stock_contract):
    query = Query("Testcorp", "companyTradesAtStockExchange", 0)
    graph = build_graph(query, stock_contract)
    record = make_record(
        query, "stock_exchange_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL
    )
    graph.add_entity_mentions(record, ["The Alpha Stock Exchange", "Alpha Stock Exchange"])

    assert len(graph.candidates) == 1
    candidate = next(iter(graph.candidates.values()))
    assert len(candidate.surface_forms) == 2
    # Exactly one surface form is emitted, because the evaluator penalises two.
    assert candidate.display_value in candidate.surface_forms


# --- verification edges ----------------------------------------------------


def test_verifier_verdict_becomes_a_signed_edge(borders_query, borders_contract):
    graph = build_graph(borders_query, borders_contract)
    graph.add_entity_mentions(
        make_record(borders_query, "borders_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL),
        ["Alpha"],
    )
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha",
            label=VerificationLabel.INVALID,
            valid_prob=0.1,
            invalid_prob=0.8,
            unknown_prob=0.1,
            model_id="test/stub",
            record_id="v1",
        )
    )
    candidate = graph.candidates["alpha"]
    assert candidate.contradiction_count == 1
    assert IndependenceGroup.BLIND_VERIFIER in candidate.groups


def test_verification_log_odds():
    result = VerificationResult(
        candidate_key="k",
        label=VerificationLabel.VALID,
        valid_prob=0.8,
        invalid_prob=0.1,
        unknown_prob=0.1,
    )
    assert result.log_odds() == pytest.approx(1.3863, rel=1e-3)
    assert result.edge_type is EdgeType.SUPPORT


# --- hard contract rules ---------------------------------------------------


def test_hard_rules_reject_non_positive_numbers(area_contract):
    query = Query("Testisland", "hasArea", 0)
    graph = build_graph(query, area_contract)
    record = make_record(query, "area_direct_km2", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL)
    graph.add_numeric_mentions(record, [5000.0, 0.0])
    apply_hard_contract_rules(graph)
    assert [c.key for c in graph.active_candidates()] == ["5000"]


# --- selection -------------------------------------------------------------


def test_empty_graph_yields_an_empty_prediction(borders_query, borders_contract):
    prediction = finalize(build_graph(borders_query, borders_contract))
    assert prediction.object_entities == []
    assert prediction.to_official_row()["ObjectEntities"] == []


def test_negative_gate_forces_an_empty_prediction(death_contract):
    """The existence gate says NO, so empty is the answer, not a parse failure."""
    query = Query("Testperson", "personHasCityOfDeath", 0)
    graph = build_graph(query, death_contract)
    graph.add_entity_mentions(
        make_record(query, "death_city_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL),
        ["Testville"],
    )
    graph.close_gate("death_status_gate answered NO")
    assert finalize(graph).object_entities == []


def test_single_object_relation_emits_at_most_one(death_contract):
    query = Query("Testperson", "personHasCityOfDeath", 0)
    graph = build_graph(query, death_contract)
    for view_id, family, group in [
        ("death_city_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL),
        ("death_locality_granularity", ViewFamily.CONTRASTIVE, IndependenceGroup.CONTRASTIVE_SEPARATION),
    ]:
        graph.add_entity_mentions(make_record(query, view_id, family, group), ["Testville"])
    graph.add_entity_mentions(
        make_record(query, "death_city_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL, 1),
        ["Otherville"],
    )
    prediction = finalize(graph)
    assert prediction.object_entities == ["Testville"]


def test_multi_object_relation_emits_every_supported_candidate(borders_query, borders_contract):
    graph = build_graph(borders_query, borders_contract)
    graph.add_entity_mentions(
        make_record(borders_query, "borders_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL),
        ["Alpha", "Beta", "Gamma"],
    )
    assert sorted(finalize(graph).object_entities) == ["Alpha", "Beta", "Gamma"]


def test_numeric_relation_emits_the_dominant_cluster_median(area_contract):
    query = Query("Testisland", "hasArea", 0)
    graph = build_graph(query, area_contract)
    for view_id, family, group, value in [
        ("area_direct_km2", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL, 5000.0),
        ("area_total_vs_land", ViewFamily.CONTRASTIVE, IndependenceGroup.CONTRASTIVE_SEPARATION, 5050.0),
        ("area_alternate_unit", ViewFamily.STRUCTURAL, IndependenceGroup.STRUCTURAL_DECOMPOSITION, 12000.0),
    ]:
        graph.add_numeric_mentions(make_record(query, view_id, family, group), [value])

    prediction = finalize(graph)
    assert len(prediction.object_entities) == 1
    assert float(prediction.object_entities[0]) == pytest.approx(5025.0, abs=30)


def test_contradiction_outweighs_equal_support(borders_query, borders_contract):
    graph = build_graph(borders_query, borders_contract)
    graph.add_entity_mentions(
        make_record(borders_query, "borders_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL),
        ["Alpha"],
    )
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha",
            label=VerificationLabel.INVALID,
            valid_prob=0.05,
            invalid_prob=0.9,
            unknown_prob=0.05,
            model_id="test/stub",
            record_id="v1",
        )
    )
    assert finalize(graph).object_entities == []


def test_active_candidate_order_is_independent_of_insertion_order(borders_query, borders_contract):
    def build(order):
        graph = build_graph(borders_query, borders_contract)
        for index, name in enumerate(order):
            graph.add_entity_mentions(
                make_record(
                    borders_query,
                    "borders_direct",
                    ViewFamily.DIRECT,
                    IndependenceGroup.DIRECT_RECALL,
                    index,
                ),
                [name],
            )
        return [c.key for c in graph.active_candidates()]

    assert build(["Alpha", "Beta", "Gamma"]) == build(["Gamma", "Alpha", "Beta"])
