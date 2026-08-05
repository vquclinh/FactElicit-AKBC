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
    expected = 3 / borders_contract.coverage_denominator()
    assert candidate.coverage(borders_contract.eligible_independence_groups) == pytest.approx(expected)


def test_repeated_mention_inside_one_generation_counts_once(borders_query, borders_contract):
    graph = build_graph(borders_query, borders_contract)
    record = make_record(
        borders_query, "borders_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL
    )
    graph.add_entity_mentions(record, ["Alpha", "Alpha", "alpha"])
    assert graph.candidates["alpha"].raw_support_count == 1


# --- deduplication ---------------------------------------------------------


def test_article_variants_stay_separate_nodes_but_group_softly(borders_query, stock_contract):
    """Identity is strict; the alias hint is advisory, never a hard merge.

    Merging on a hint would be irreversible, and article folding is not always
    safe ("Le Havre" vs "Havre"). The graph keeps both nodes; the output writer
    still submits only one surface form.
    """
    from cover_kbc.data.writer import dedupe_object_entities

    query = Query("Testcorp", "companyTradesAtStockExchange", 0)
    graph = build_graph(query, stock_contract)
    record = make_record(
        query, "stock_exchange_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL
    )
    graph.add_entity_mentions(record, ["The Alpha Stock Exchange", "Alpha Stock Exchange"])

    assert len(graph.candidates) == 2                       # strict identity
    assert len(graph.alias_groups()) == 1                   # grouped softly
    assert {c.alias_hint for c in graph.candidates.values()} == {"alpha stock exchange"}
    # Both reach the evaluator: they are two distinct strict candidates, and
    # the writer only removes what the evaluator itself would collapse. Folding
    # the article here would promote a soft hint to hard identity - the exact
    # thing `alias_hint` exists to avoid (audit 0006).
    emitted = dedupe_object_entities([c.display_value for c in graph.candidates.values()])
    assert len(emitted) == 2


def test_exact_duplicate_surfaces_do_merge(stock_contract):
    """Strict-identical forms are provably one prediction, so they merge."""
    query = Query("Testcorp", "companyTradesAtStockExchange", 0)
    graph = build_graph(query, stock_contract)
    record = make_record(
        query, "stock_exchange_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL
    )
    graph.add_entity_mentions(record, ["Alpha Exchange", "alpha exchange", "ALPHA EXCHANGE"])

    assert len(graph.candidates) == 1
    candidate = next(iter(graph.candidates.values()))
    assert len(candidate.surface_forms) == 3        # every surface preserved
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
    """Two mechanisms clear the acceptance bar for every candidate they name."""
    graph = build_graph(borders_query, borders_contract)
    for view_id, family, group in (
        ("borders_direct", ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL),
        ("borders_compass", ViewFamily.STRUCTURAL, IndependenceGroup.STRUCTURAL_DECOMPOSITION),
    ):
        graph.add_entity_mentions(
            make_record(borders_query, view_id, family, group), ["Alpha", "Beta", "Gamma"]
        )
    assert sorted(finalize(graph).object_entities) == ["Alpha", "Beta", "Gamma"]


def test_single_mechanism_support_is_scored_against_all_declared_mechanisms():
    """F(o) normalises by *declared* mechanisms, not by those actually run.

    Recorded as a Module-5 review item in audit 0005: a relation that declares
    many optional mechanisms scores a one-mechanism candidate lower than a
    relation that declares few, even when both ran the same number of views.
    """
    from cover_kbc.contracts.registry import get_contract
    from cover_kbc.scoring import DEFAULT_SCORING, score_candidate

    borders = get_contract("countryLandBordersCountry")
    area = get_contract("hasArea")
    assert borders.coverage_denominator() > area.coverage_denominator()

    def one_mechanism(contract, relation_query):
        graph = build_graph(relation_query, contract)
        graph.add_entity_mentions(
            make_record(relation_query, contract.mandatory_views[0],
                        ViewFamily.DIRECT, IndependenceGroup.DIRECT_RECALL),
            ["Alpha"],
        )
        candidate = graph.candidates["alpha"]
        return score_candidate(candidate, contract).support

    borders_f = one_mechanism(borders, Query("S", borders.relation, 0))
    assert borders_f == pytest.approx(1 / borders.coverage_denominator())
    assert borders_f < DEFAULT_SCORING.accept_score


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


# --- Milestone 2: facets, capacity selection, relation-specific behaviour ---


def test_facets_are_provenance_not_independence(borders_query, borders_contract):
    """Five slices of one mechanism are one independent support, not five."""
    graph = build_graph(borders_query, borders_contract)
    for index, facet in enumerate(["decade_1990", "decade_2000", "decade_2010"]):
        record = make_record(
            borders_query,
            "borders_compass",
            ViewFamily.STRUCTURAL,
            IndependenceGroup.STRUCTURAL_DECOMPOSITION,
            run_id=index,
        )
        record.facet_id = facet
        graph.add_entity_mentions(record, ["Alpha"])

    candidate = graph.candidates["alpha"]
    assert candidate.num_facets == 3          # three facets recorded
    assert candidate.independent_support == 1  # but one mechanism
    assert candidate.raw_support_count == 3
    assert graph.facet_summary() == {"decade_1990": 1, "decade_2000": 1, "decade_2010": 1}


def test_award_facet_views_share_one_independence_group():
    from cover_kbc.elicitation.library import get_view

    groups = {
        get_view("awardWonBy", v).independence_group
        for v in ("award_facet_temporal", "award_facet_recipient_type", "award_facet_category")
    }
    assert groups == {IndependenceGroup.STRUCTURAL_DECOMPOSITION}
    facets = {
        get_view("awardWonBy", v).facet
        for v in ("award_facet_temporal", "award_facet_recipient_type", "award_facet_category")
    }
    assert len(facets) == 3  # separable in traces, identical in independence


def _capacity_graph(capacity_contract, values_with_support):
    query = Query("Testvenue", "hasCapacity", 0)
    graph = build_graph(query, capacity_contract)
    groups = [
        IndependenceGroup.DIRECT_RECALL,
        IndependenceGroup.CONTRASTIVE_SEPARATION,
        IndependenceGroup.STRUCTURAL_DECOMPOSITION,
    ]
    for value, support in values_with_support:
        for i in range(support):
            record = make_record(
                query,
                ["capacity_direct", "capacity_contrast", "capacity_configuration"][i],
                ViewFamily.DIRECT,
                groups[i],
                run_id=i,
            )
            graph.add_numeric_mentions(record, [value])
    return graph


def test_capacity_prefers_the_highest_equally_supported_cluster(capacity_contract):
    """Official target is the highest published capacity, not the modal one."""
    graph = _capacity_graph(capacity_contract, [(40000, 2), (52000, 2)])
    prediction = finalize(graph)
    assert float(prediction.object_entities[0]) == pytest.approx(52000, abs=100)


def test_capacity_ignores_an_unsupported_high_outlier(capacity_contract):
    """A lone hallucinated big number must not win merely for being big."""
    graph = _capacity_graph(capacity_contract, [(40000, 3), (250000, 1)])
    prediction = finalize(graph)
    assert float(prediction.object_entities[0]) == pytest.approx(40000, abs=100)


def test_capacity_excludes_a_cluster_the_verifier_rejected(capacity_contract):
    """Record attendance verified INVALID must not be selected."""
    graph = _capacity_graph(capacity_contract, [(40000, 2), (52000, 2)])
    graph.add_verification(
        VerificationResult(
            candidate_key="52000",
            label=VerificationLabel.INVALID,
            valid_prob=0.05, invalid_prob=0.9, unknown_prob=0.05,
            model_id="qwen", record_id="v1",
        )
    )
    prediction = finalize(graph)
    assert float(prediction.object_entities[0]) == pytest.approx(40000, abs=100)


def test_area_uses_the_robust_dominant_cluster_not_the_highest(area_contract):
    """hasArea wants a central estimate; only capacity takes the maximum."""
    query = Query("Testisland", "hasArea", 0)
    graph = build_graph(query, area_contract)
    for value, group, view in [
        (5000.0, IndependenceGroup.DIRECT_RECALL, "area_direct_km2"),
        (5050.0, IndependenceGroup.CONTRASTIVE_SEPARATION, "area_total_vs_land"),
        (90000.0, IndependenceGroup.STRUCTURAL_DECOMPOSITION, "area_alternate_unit"),
    ]:
        graph.add_numeric_mentions(
            make_record(query, view, ViewFamily.DIRECT, group), [value]
        )
    prediction = finalize(graph)
    assert float(prediction.object_entities[0]) == pytest.approx(5025, abs=60)


def test_empty_reasons_are_not_conflated(death_contract, borders_contract, borders_query):
    """A confident gate and an abstention are different outcomes."""
    from cover_kbc.types import EmptyReason

    query = Query("Testperson", "personHasCityOfDeath", 0)
    gated = build_graph(query, death_contract)
    gated.close_gate("calibrated gate: NO")
    assert finalize(gated).empty_reason is EmptyReason.CONFIDENT_NEGATIVE_GATE

    barren = build_graph(borders_query, borders_contract)
    assert finalize(barren).empty_reason is EmptyReason.NO_CANDIDATE_GENERATED
