"""Module 3 conformance: atomic normalization + Candidate-Facet Evidence Graph.

Deterministic and synthetic throughout. No model is loaded anywhere.
"""

from __future__ import annotations

import pytest

from cover_kbc.contracts.registry import get_contract
from cover_kbc.elicitation.parsing import parse_numeric_observations
from cover_kbc.evidence.graph import apply_hard_contract_rules, build_graph
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.normalization.strings import alias_hint_key, strict_key
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.staging import graph_from_json, graph_to_json
from cover_kbc.types import (
    CandidateStatus,
    DecodeProfile,
    EdgeType,
    EvidenceMode,
    GenerationRecord,
    IndependenceGroup,
    ModelRole,
    Query,
    VerificationLabel,
    VerificationResult,
    ViewFamily,
)


def record(
    query,
    view_id="v",
    family=ViewFamily.DIRECT,
    group=IndependenceGroup.DIRECT_RECALL,
    run_id=0,
    **kw,
):
    return GenerationRecord(
        record_id=f"{view_id}:{run_id}:{kw.get('stage','')}",
        query=query,
        view_id=view_id,
        view_family=family,
        independence_group=group,
        run_id=run_id,
        model_id="offline/mistral",
        model_family="mistral",
        model_role=ModelRole.ENUMERATOR,
        prompt="p",
        prompt_hash="h",
        raw_output="o",
        decode_profile=DecodeProfile(),
        generated_tokens=7,
        prompt_tokens=13,
        **kw,
    )


def borders_graph():
    contract = get_contract("countryLandBordersCountry")
    query = Query("Testland", contract.relation, 0)
    return query, contract, build_graph(query, contract)


# --- 1. atomic candidates (spec §9.1) --------------------------------------


def test_one_generation_of_many_objects_becomes_many_atomic_nodes():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Alpha", "Beta", "Gamma"])
    assert len(graph.candidates) == 3
    assert {c.display_value for c in graph.candidates.values()} == {"Alpha", "Beta", "Gamma"}


def test_a_whole_list_never_becomes_one_candidate():
    """The parser splits; the graph must receive atoms, not the raw string."""
    from cover_kbc.elicitation.parsing import parse_entities

    contract = get_contract("countryLandBordersCountry")
    assert parse_entities("Alpha; Beta; Gamma", contract) == ["Alpha", "Beta", "Gamma"]


def test_a_gate_record_creates_no_candidate():
    contract = get_contract("personHasCityOfDeath")
    query = Query("Testperson", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(
        record(query, "death_status_gate", ViewFamily.GATE, IndependenceGroup.EXISTENCE_GATE), []
    )
    assert graph.candidates == {}
    assert len(graph.records) == 1          # provenance kept
    assert IndependenceGroup.EXISTENCE_GATE not in graph.candidate_producing_groups()


def test_description_prose_creates_no_candidate_but_extraction_does():
    contract = get_contract("countryLandBordersCountry")
    query = Query("S", contract.relation, 0)
    graph = build_graph(query, contract)

    prose = record(
        query, "borders_description", ViewFamily.DESCRIPTION,
        IndependenceGroup.RELATION_FOCUSED_DESCRIPTION, stage="description",
    )
    graph.add_entity_mentions(prose, [])          # stage 1 yields nothing
    assert graph.candidates == {}

    extraction = record(
        query, "borders_description", ViewFamily.DESCRIPTION,
        IndependenceGroup.RELATION_FOCUSED_DESCRIPTION, stage="extraction",
        source_record_id=prose.record_id,
    )
    graph.add_entity_mentions(extraction, ["Alpha"])
    assert list(graph.candidates) == ["alpha"]


def test_description_and_extraction_are_one_independent_support():
    contract = get_contract("countryLandBordersCountry")
    query = Query("S", contract.relation, 0)
    graph = build_graph(query, contract)
    prose = record(
        query, "borders_description", ViewFamily.DESCRIPTION,
        IndependenceGroup.RELATION_FOCUSED_DESCRIPTION, stage="description",
    )
    extraction = record(
        query, "borders_description", ViewFamily.DESCRIPTION,
        IndependenceGroup.RELATION_FOCUSED_DESCRIPTION, stage="extraction",
        source_record_id=prose.record_id,
    )
    graph.add_entity_mentions(prose, [])
    graph.add_entity_mentions(extraction, ["Alpha"])

    candidate = graph.candidates["alpha"]
    assert candidate.independent_support == 1       # two calls, one mechanism
    assert candidate.raw_support_count == 1         # only extraction produced evidence


def test_extraction_evidence_traces_back_to_its_description():
    contract = get_contract("countryLandBordersCountry")
    query = Query("S", contract.relation, 0)
    graph = build_graph(query, contract)
    prose = record(
        query, "borders_description", ViewFamily.DESCRIPTION,
        IndependenceGroup.RELATION_FOCUSED_DESCRIPTION, stage="description",
    )
    graph.register_record(prose)
    extraction = record(
        query, "borders_description", ViewFamily.DESCRIPTION,
        IndependenceGroup.RELATION_FOCUSED_DESCRIPTION, stage="extraction",
        source_record_id=prose.record_id,
    )
    graph.add_entity_mentions(extraction, ["Alpha"])

    edge = graph.candidates["alpha"].all_evidence()[0]
    traced = graph.records[edge.record_id]
    assert traced.stage == "extraction"
    assert graph.records[traced.source_record_id].stage == "description"


# --- 2. string identity: conservative merge only ---------------------------


def test_identity_is_the_strict_evaluator_key():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Alpha Land"])
    key = next(iter(graph.candidates))
    assert key == strict_key("Alpha Land")


def test_case_and_punctuation_variants_merge_losslessly():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Alpha-Land", "alpha land", "ALPHA LAND"])
    assert len(graph.candidates) == 1
    assert len(next(iter(graph.candidates.values())).surface_forms) == 3


def test_parenthetical_qualifiers_never_hard_merge():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(
        record(query), ["Springfield (Illinois)", "Springfield (Missouri)", "Springfield"]
    )
    assert len(graph.candidates) == 3


@pytest.mark.parametrize(
    "a,b",
    [
        ("Le Havre", "Havre"),        # Havre, Montana is a different place
        ("Los Angeles", "Angeles"),   # Angeles, Philippines
        ("La Paz", "Paz"),
        ("El Paso", "Paso"),
    ],
)
def test_non_english_article_prefixes_are_not_folded(a, b):
    """These are parts of proper names, not detachable articles."""
    assert alias_hint_key(a) != alias_hint_key(b)


def test_an_alias_hint_never_becomes_hard_identity():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["The Alpha Land", "Alpha Land"])

    assert len(graph.candidates) == 2                     # not merged
    hints = {c.alias_hint for c in graph.candidates.values()}
    assert len(hints) == 1                                # but grouped softly
    assert len(graph.alias_groups()) == 1


def test_alias_grouping_is_advisory_and_reported():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["The Alpha Land", "Alpha Land", "Beta"])
    groups = graph.alias_groups()
    assert list(groups) == ["alpha land"]
    assert len(groups["alpha land"]) == 2


# --- 3. surface preservation (spec §9.1) -----------------------------------


def test_every_observed_surface_is_preserved():
    query, contract, graph = borders_graph()
    for run_id, surface in enumerate(["Alpha Land", "alpha land", "ALPHA-LAND"]):
        graph.add_entity_mentions(record(query, run_id=run_id), [surface])
    candidate = next(iter(graph.candidates.values()))
    assert set(candidate.surface_forms) == {"Alpha Land", "alpha land", "ALPHA-LAND"}


def test_normalization_never_rewrites_the_emitted_string():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Côte d'Ivoire"])
    candidate = next(iter(graph.candidates.values()))
    assert candidate.display_value == "Côte d'Ivoire"      # diacritics intact
    assert candidate.key != candidate.display_value        # key is folded, output is not


def test_distinct_names_are_not_claimed_to_be_aliases():
    """The graph must not invent an alias relation it has no evidence for."""
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Czech Republic", "Czechia"])
    assert len(graph.candidates) == 2
    assert graph.alias_groups() == {}      # no fabricated grouping


# --- 4. numeric candidates (brief §7) --------------------------------------


def test_numeric_candidate_keeps_raw_text_and_source_unit():
    contract = get_contract("hasArea")
    query = Query("Isl", contract.relation, 0)
    graph = build_graph(query, contract)
    observations = parse_numeric_observations("2145 square miles", contract)
    graph.add_numeric_mentions(record(query, "area_alternate_unit"), observations)

    candidate = next(iter(graph.candidates.values()))
    assert candidate.raw_text == "2145"
    assert candidate.source_unit == "mi2"
    assert candidate.unit == "km2"
    assert candidate.numeric_value == pytest.approx(5555.52, rel=1e-4)


def test_thousands_separator_survives_into_the_graph():
    contract = get_contract("hasCapacity")
    query = Query("Venue", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_numeric_mentions(
        record(query), parse_numeric_observations("35,000", contract)
    )
    candidate = next(iter(graph.candidates.values()))
    assert candidate.numeric_value == 35000.0
    assert candidate.raw_text == "35,000"
    assert set(graph.candidates) == {"35000"}      # not 35 and 000


def test_unit_conversion_is_deterministic():
    contract = get_contract("hasArea")
    for _ in range(3):
        values = [o.value for o in parse_numeric_observations("500 hectares", contract)]
        assert values == pytest.approx([5.0])


def test_two_units_for_one_value_share_a_candidate_but_keep_provenance():
    contract = get_contract("hasArea")
    query = Query("Isl", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_numeric_mentions(
        record(query, "area_direct_km2"), parse_numeric_observations("5556 km2", contract)
    )
    graph.add_numeric_mentions(
        record(query, "area_alternate_unit", group=IndependenceGroup.STRUCTURAL_DECOMPOSITION),
        parse_numeric_observations("2145 square miles", contract),
    )
    # Same normalised key, two mechanisms, both recorded.
    assert len(graph.candidates) == 1
    candidate = next(iter(graph.candidates.values()))
    assert candidate.independent_support == 2
    assert candidate.source_unit == "km2"          # first observation's provenance


def test_bare_floats_are_still_accepted():
    contract = get_contract("hasArea")
    query = Query("Isl", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_numeric_mentions(record(query), [5000.0])
    assert set(graph.candidates) == {"5000"}


# --- 5. candidate node vs evidence edge ------------------------------------


def test_many_edges_do_not_create_many_nodes():
    query, contract, graph = borders_graph()
    for run_id, (view, group) in enumerate(
        [
            ("borders_direct", IndependenceGroup.DIRECT_RECALL),
            ("borders_compass", IndependenceGroup.STRUCTURAL_DECOMPOSITION),
            ("borders_land_vs_maritime", IndependenceGroup.CONTRASTIVE_SEPARATION),
        ]
    ):
        graph.add_entity_mentions(record(query, view, group=group, run_id=run_id), ["Alpha"])

    assert len(graph.candidates) == 1
    candidate = graph.candidates["alpha"]
    assert len(candidate.all_evidence()) == 3
    assert candidate.independent_support == 3


def test_repeated_runs_are_repetition_not_independence():
    query, contract, graph = borders_graph()
    for run_id in range(3):
        graph.add_entity_mentions(record(query, "borders_direct", run_id=run_id), ["Alpha"])
    candidate = graph.candidates["alpha"]
    assert candidate.raw_support_count == 3
    assert candidate.independent_support == 1


def test_facet_identity_and_independence_group_stay_separate():
    contract = get_contract("awardWonBy")
    query = Query("Prize", contract.relation, 0)
    graph = build_graph(query, contract)
    for run_id, facet in enumerate(["award_temporal", "award_recipient_type", "award_category"]):
        graph.add_entity_mentions(
            record(
                query,
                f"award_facet_{run_id}",
                ViewFamily.STRUCTURAL,
                IndependenceGroup.STRUCTURAL_DECOMPOSITION,
                run_id=run_id,
                facet_id=facet,
            ),
            ["Alpha"],
        )
    candidate = graph.candidates["alpha"]
    assert candidate.num_facets == 3                 # facets survive
    assert candidate.independent_support == 1        # one mechanism
    assert len(graph.facet_summary()) == 3


# --- 6. evidence edge provenance -------------------------------------------


def test_no_edge_is_anonymous():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query, "borders_direct"), ["Alpha"])
    edge = graph.candidates["alpha"].all_evidence()[0]
    for field in ("candidate_key", "record_id", "view_id", "model_id", "edge_id"):
        assert getattr(edge, field)
    assert edge.record_id in graph.records
    assert edge.model_family == "mistral"
    assert edge.independence_group is IndependenceGroup.DIRECT_RECALL


def test_edge_ids_are_deterministic():
    def build():
        query, contract, graph = borders_graph()
        graph.add_entity_mentions(record(query, "borders_direct"), ["Alpha"])
        return [e.edge_id for e in graph.candidates["alpha"].all_evidence()]

    assert build() == build()


def test_a_duplicate_edge_fails_loudly():
    query, contract, graph = borders_graph()
    same = record(query, "borders_direct")
    graph.add_entity_mentions(same, ["Alpha"])
    with pytest.raises(ValueError, match="duplicate evidence edge"):
        graph.add_entity_mentions(same, ["Alpha"])


# --- 7. signed edge semantics ----------------------------------------------


def test_absence_from_a_generation_is_not_a_contradiction():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query, "borders_direct", run_id=0), ["Alpha", "Beta"])
    graph.add_entity_mentions(record(query, "borders_compass", run_id=1), ["Alpha"])
    # Beta was simply not mentioned the second time. That is not evidence against it.
    assert graph.candidates["beta"].contradiction_count == 0


def test_all_three_edge_types_round_trip():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query, "borders_direct"), ["Alpha", "Beta", "Gamma"])
    for key, label in (
        ("alpha", VerificationLabel.VALID),
        ("beta", VerificationLabel.INVALID),
        ("gamma", VerificationLabel.UNKNOWN),
    ):
        graph.add_verification(
            VerificationResult(
                candidate_key=key, label=label,
                valid_prob=0.4, invalid_prob=0.3, unknown_prob=0.3,
                model_id="qwen", model_family="qwen", record_id=f"v:{key}",
            )
        )
    restored = graph_from_json(graph_to_json(graph))
    types = {
        k: {e.edge_type for e in c.all_evidence()} for k, c in restored.candidates.items()
    }
    assert EdgeType.SUPPORT in types["alpha"]
    assert EdgeType.CONTRADICT in types["beta"]
    assert EdgeType.UNKNOWN in types["gamma"]


# --- 8. Module-2 special events --------------------------------------------


def test_reverse_evidence_attaches_to_the_existing_candidate():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query, "borders_direct"), ["Alpha"])
    graph.add_entity_mentions(
        record(
            query, "borders_reverse_check", ViewFamily.REVERSE,
            IndependenceGroup.REVERSE_ALTERNATE, run_id=1, source_candidate_key="alpha",
        ),
        ["Alpha"],
    )
    assert len(graph.candidates) == 1
    candidate = graph.candidates["alpha"]
    assert candidate.independent_support == 2
    assert IndependenceGroup.REVERSE_ALTERNATE in candidate.groups


def test_cross_model_recall_and_blind_verification_stay_distinct():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(
        record(query, "borders_direct", group=IndependenceGroup.CROSS_MODEL_RECALL), ["Alpha"]
    )
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha", label=VerificationLabel.VALID,
            valid_prob=0.8, invalid_prob=0.1, unknown_prob=0.1,
            model_id="qwen", model_family="qwen", record_id="v1",
        )
    )
    candidate = graph.candidates["alpha"]
    modes = {e.independence_group: e.mode for e in candidate.all_evidence()}
    assert modes[IndependenceGroup.CROSS_MODEL_RECALL] is EvidenceMode.INDEPENDENT_RECALL
    assert modes[IndependenceGroup.BLIND_VERIFIER] is EvidenceMode.SHOWN_CANDIDATE


def test_a_verifier_label_can_never_become_a_candidate():
    query, contract, graph = borders_graph()
    for label in ("A", "B", "C", "VALID", "INVALID", "UNKNOWN"):
        graph.add_verification(
            VerificationResult(candidate_key=label, label=VerificationLabel.VALID, valid_prob=0.9)
        )
    assert graph.candidates == {}


# --- 9. hard contract rules (spec §9.3) ------------------------------------


def test_a_non_numeric_candidate_is_rejected_with_a_reason():
    contract = get_contract("hasArea")
    query = Query("Isl", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(record(query), ["Alpha"])
    apply_hard_contract_rules(graph)

    candidate = graph.candidates["alpha"]
    assert candidate.status is CandidateStatus.REJECTED
    assert candidate.rejection_reason                     # never a silent drop
    assert graph.active_candidates() == []


def test_hard_rules_encode_no_factual_knowledge():
    """Type/format checks only - never a lookup table (spec §9.3)."""
    import ast
    import inspect
    import textwrap

    from cover_kbc.evidence import graph as graph_module

    source = inspect.getsource(graph_module.apply_hard_contract_rules)
    for factual in ("Denmark", "Poland", "NYSE", "Nobel", "France", "Germany", "Wikidata"):
        assert factual not in source

    # No literal collection of names to test membership against.
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
            constants = [
                e for e in getattr(node, "elts", []) if isinstance(e, ast.Constant)
            ]
            words = [e.value for e in constants if isinstance(e.value, str)]
            assert not words, f"literal name set in a hard rule: {words}"


def test_hard_rules_are_only_type_or_format_checks():
    """Each rejection reason names a type/format violation, not a fact."""
    contract = get_contract("hasArea")
    query = Query("Isl", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(record(query), ["Alpha"])
    graph.add_numeric_mentions(record(query, "n", run_id=9), [-5.0])
    apply_hard_contract_rules(graph)

    reasons = {c.rejection_reason for c in graph.candidates.values() if c.rejection_reason}
    assert reasons
    for reason in reasons:
        assert any(word in reason for word in ("numeric", "positive", "letters", "type"))


def test_a_rejected_candidate_keeps_its_evidence():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Alpha"])
    graph.reject("alpha", "type violation")
    candidate = graph.candidates["alpha"]
    assert candidate.status is CandidateStatus.REJECTED
    assert candidate.all_evidence()                       # dedup/reject never deletes evidence


# --- 10. abstention and error handling (brief §22) -------------------------


@pytest.mark.parametrize("junk", ["NONE", "none", "UNKNOWN", "", "   ", "N/A", "nil"])
def test_abstention_tokens_never_become_candidates(junk):
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), [junk])
    assert graph.candidates == {}


def test_a_failed_generation_keeps_provenance_without_a_candidate():
    query, contract, graph = borders_graph()
    failed = record(query, "borders_direct")
    failed.error = "RuntimeError: backend down"
    failed.raw_output = ""
    graph.add_entity_mentions(failed, [])
    assert graph.candidates == {}
    assert graph.records[failed.record_id].error


# --- 11. graph invariants ---------------------------------------------------


def test_every_edge_references_an_existing_candidate():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Alpha", "Beta"])
    for candidate in graph.candidates.values():
        for edge in candidate.all_evidence():
            assert edge.candidate_key in graph.candidates


def test_every_candidate_has_at_least_one_provenance_source():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Alpha", "Beta"])
    for candidate in graph.candidates.values():
        assert candidate.record_ids
        assert all(r in graph.records for r in candidate.record_ids)


def test_candidate_relation_matches_the_graph_relation():
    query, contract, graph = borders_graph()
    graph.add_entity_mentions(record(query), ["Alpha"])
    for candidate in graph.candidates.values():
        assert candidate.relation == contract.relation
        assert candidate.output_type is contract.output_type


def test_an_entity_cannot_survive_in_a_numeric_graph():
    contract = get_contract("hasCapacity")
    query = Query("Venue", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(record(query), ["Alpha"])
    apply_hard_contract_rules(graph)
    assert graph.active_candidates() == []


# --- 12. staged serialization (brief §17) -----------------------------------


def _rich_graph():
    query, contract, graph = borders_graph()
    prose = record(
        query, "borders_description", ViewFamily.DESCRIPTION,
        IndependenceGroup.RELATION_FOCUSED_DESCRIPTION, stage="description",
    )
    graph.register_record(prose)
    graph.add_entity_mentions(
        record(
            query, "borders_description", ViewFamily.DESCRIPTION,
            IndependenceGroup.RELATION_FOCUSED_DESCRIPTION, stage="extraction",
            source_record_id=prose.record_id,
        ),
        ["Alpha", "The Alpha"],
    )
    graph.add_entity_mentions(
        record(query, "borders_compass", ViewFamily.STRUCTURAL,
               IndependenceGroup.STRUCTURAL_DECOMPOSITION, run_id=1, facet_id="compass"),
        ["Alpha"],
    )
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha", label=VerificationLabel.VALID,
            valid_prob=0.8, invalid_prob=0.1, unknown_prob=0.1,
            raw_logits={"VALID": 2.0}, calibrated_logits={"VALID": 1.5},
            calibrated=True, prompt_disagreement=0.2,
            model_id="qwen", model_family="qwen", record_id="v1",
        )
    )
    graph.reject("the alpha", "duplicate surface form")
    return graph


def test_staged_round_trip_is_semantically_lossless():
    original = _rich_graph()
    restored = graph_from_json(graph_to_json(original))

    assert set(restored.candidates) == set(original.candidates)
    assert set(restored.records) == set(original.records)

    for key, candidate in original.candidates.items():
        other = restored.candidates[key]
        for field in (
            "strict_key", "alias_hint", "display_value", "surface_forms", "facet_ids",
            "status", "rejection_reason", "numeric_value", "unit", "raw_text", "source_unit",
        ):
            assert getattr(other, field) == getattr(candidate, field), field
        assert other.independent_support == candidate.independent_support
        assert other.raw_support_count == candidate.raw_support_count
        assert [e.edge_id for e in other.all_evidence()] == [
            e.edge_id for e in candidate.all_evidence()
        ]


def test_record_provenance_survives_staging():
    original = _rich_graph()
    restored = graph_from_json(graph_to_json(original))
    for record_id, source in original.records.items():
        target = restored.records[record_id]
        for field in (
            "view_id", "facet_id", "stage", "source_record_id", "source_candidate_key",
            "model_family", "model_role", "independence_group", "view_family", "run_id",
        ):
            assert getattr(target, field) == getattr(source, field), field


def test_the_description_chain_survives_staging():
    """The context -> extraction link must not break across a staged phase."""
    restored = graph_from_json(graph_to_json(_rich_graph()))
    extraction = next(r for r in restored.records.values() if r.stage == "extraction")
    assert extraction.source_record_id
    assert restored.records[extraction.source_record_id].stage == "description"


def test_verification_fields_survive_staging():
    restored = graph_from_json(graph_to_json(_rich_graph()))
    verification = restored.candidates["alpha"].verifications[0]
    assert verification.calibrated is True
    assert verification.raw_logits == {"VALID": 2.0}
    assert verification.calibrated_logits == {"VALID": 1.5}
    assert verification.prompt_disagreement == pytest.approx(0.2)
    assert verification.model_family == "qwen"


def test_a_reloaded_graph_still_rejects_duplicate_edges():
    restored = graph_from_json(graph_to_json(_rich_graph()))
    candidate = restored.candidates["alpha"]
    existing = candidate.all_evidence()[0]
    with pytest.raises(ValueError, match="duplicate evidence edge"):
        restored._attach(candidate, existing)


def test_candidate_identity_survives_reload():
    original = _rich_graph()
    restored = graph_from_json(graph_to_json(original))
    assert list(restored.candidates) == list(original.candidates)
    for key in original.candidates:
        assert restored.candidates[key].key == key


# --- 13. what Module 5 will need (brief §19) --------------------------------


def test_the_graph_reports_executed_vs_candidate_producing_groups():
    """Module 5 needs both to compute a per-run denominator later."""
    contract = get_contract("personHasCityOfDeath")
    query = Query("P", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(
        record(query, "death_status_gate", ViewFamily.GATE, IndependenceGroup.EXISTENCE_GATE), []
    )
    graph.add_entity_mentions(record(query, "death_city_direct"), ["Testville"])

    assert graph.executed_independence_groups() == {
        IndependenceGroup.EXISTENCE_GATE,
        IndependenceGroup.DIRECT_RECALL,
    }
    assert graph.candidate_producing_groups() == {IndependenceGroup.DIRECT_RECALL}
    # The declared catalogue is larger than what actually ran.
    assert len(contract.eligible_independence_groups) >= len(
        graph.candidate_producing_groups()
    )


def test_module_3_does_not_compute_any_score():
    import inspect

    from cover_kbc.evidence import graph as graph_module

    source = inspect.getsource(graph_module)
    for leak in ("accept_score", "min_valid_prob", "residual", "score_candidate", "S(o)"):
        assert leak not in source


# --- 14. end-to-end through the real pipeline ------------------------------


def test_a_scripted_pipeline_run_produces_a_well_formed_graph():
    contract = get_contract("countryLandBordersCountry")
    script = {
        ("borders_direct", "S", contract.relation): ["Alpha; Beta"],
        ("borders_compass", "S", contract.relation): ["Alpha"],
    }
    runtime = ScriptedRuntime(
        script, model_id="offline/mistral", family="mistral", role="enumerator"
    )
    graph = CoverPipeline(runtime, PipelineConfig()).enumerate_query(
        Query("S", contract.relation, 0)
    )

    assert set(graph.candidates) == {"alpha", "beta"}
    assert graph.candidates["alpha"].independent_support == 2
    assert graph.candidates["beta"].independent_support == 1
    for candidate in graph.candidates.values():
        for edge in candidate.all_evidence():
            assert edge.edge_id and edge.record_id in graph.records
