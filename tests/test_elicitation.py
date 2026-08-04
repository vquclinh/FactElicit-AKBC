"""Module 2 conformance: the Diverse Elicitation Engine.

Deterministic and synthetic throughout. No model is loaded anywhere.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from cover_kbc.contracts.registry import all_contracts, get_contract
from cover_kbc.elicitation import library as library_module
from cover_kbc.elicitation.engine import ElicitationEngine
from cover_kbc.elicitation.library import (
    VIEW_LIBRARY,
    check_library_covers_contracts,
    get_view,
    views_for,
)
from cover_kbc.elicitation.views import CANDIDATE_FAMILIES, FAMILY_TO_GROUP
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.types import (
    EvidenceMode,
    IndependenceGroup,
    ModelRole,
    OutputType,
    ProgramType,
    Query,
    ViewFamily,
)


def enumerator(script=None, **kw):
    return ScriptedRuntime(
        script or {}, model_id="offline/mistral", family="mistral", role="enumerator", **kw
    )


# --- contract <-> view library consistency (brief §4) ----------------------


def test_library_and_contracts_agree():
    check_library_covers_contracts()


def test_every_declared_view_has_exactly_one_implementation():
    for contract in all_contracts():
        for view_id in contract.all_views():
            assert (contract.relation, view_id) in VIEW_LIBRARY


def test_every_implemented_view_is_declared_by_its_contract():
    for (relation, view_id) in VIEW_LIBRARY:
        assert view_id in get_contract(relation).all_views()


def test_view_ids_are_unique():
    ids = [view_id for _, view_id in VIEW_LIBRARY]
    assert len(ids) == len(set(ids))


def test_unknown_view_fails_loudly_with_no_fallback_prompt():
    with pytest.raises(KeyError):
        get_view("countryLandBordersCountry", "borders_made_up")
    with pytest.raises(KeyError):
        views_for("hasArea", ("area_direct_km2", "nope"))


def test_no_relation_receives_another_relations_views():
    for (relation, view_id) in VIEW_LIBRARY:
        assert VIEW_LIBRARY[(relation, view_id)].relation == relation


def test_an_orphaned_eligible_group_is_rejected():
    """A group no candidate-producing view can reach caps q(o) forever."""
    contract = get_contract("hasArea")
    broken = dataclasses.replace(
        contract,
        eligible_independence_groups=(
            *contract.eligible_independence_groups,
            IndependenceGroup.MISSINGNESS_SEARCH,
        ),
    )
    problems = _library_problems({contract.relation: broken})
    assert any("no candidate-producing view" in p for p in problems)


def _library_problems(contracts):
    """Run the library check against substituted contracts, collecting problems."""
    import cover_kbc.contracts.registry as registry

    original = dict(registry.CONTRACTS)
    try:
        registry.CONTRACTS.update(contracts)
        try:
            check_library_covers_contracts()
        except ValueError as exc:
            return str(exc).splitlines()
        return []
    finally:
        registry.CONTRACTS.clear()
        registry.CONTRACTS.update(original)


# --- view identity is structural (brief §5) --------------------------------


def test_view_id_facet_id_and_independence_group_are_distinct_concepts():
    """Three different questions: which prompt, which subspace, which mechanism."""
    temporal = get_view("awardWonBy", "award_facet_temporal")
    recipient = get_view("awardWonBy", "award_facet_recipient_type")

    assert temporal.view_id != recipient.view_id            # different procedures
    assert temporal.facet != recipient.facet                # different subspaces
    assert temporal.independence_group is recipient.independence_group  # one mechanism


def test_family_determines_the_group_and_views_cannot_override_it():
    for view in VIEW_LIBRARY.values():
        assert view.independence_group is FAMILY_TO_GROUP[view.family]


def test_repeating_one_view_does_not_create_a_new_view_identity():
    """Spec §7.3: ten repeats of one view are one evidence family."""
    contract = get_contract("countryLandBordersCountry")
    view = dataclasses.replace(
        get_view(contract.relation, "borders_direct"),
        runs=3,
        decode=dataclasses.replace(
            get_view(contract.relation, "borders_direct").decode, temperature=0.7
        ),
    )
    runtime = enumerator({("borders_direct", "S", contract.relation): ["Alpha"]})
    outcomes = ElicitationEngine(runtime).run_view_repeats(
        Query("S", contract.relation, 0), contract, view
    )

    assert len(outcomes) == 3
    assert {o.record.run_id for o in outcomes} == {0, 1, 2}          # runs differ
    assert {o.record.view_id for o in outcomes} == {"borders_direct"}  # identity does not
    assert {o.record.independence_group for o in outcomes} == {
        IndependenceGroup.DIRECT_RECALL
    }
    # ...and distinct record ids, so provenance stays separable.
    assert len({o.record.record_id for o in outcomes}) == 3


def test_repeat_runs_amplify_but_never_multiply_independence():
    contract = get_contract("countryLandBordersCountry")
    view = dataclasses.replace(
        get_view(contract.relation, "borders_direct"),
        runs=4,
        decode=dataclasses.replace(
            get_view(contract.relation, "borders_direct").decode, temperature=0.7
        ),
    )
    from cover_kbc.evidence.graph import build_graph

    graph = build_graph(Query("S", contract.relation, 0), contract)
    runtime = enumerator({("borders_direct", "S", contract.relation): ["Alpha"]})
    for outcome in ElicitationEngine(runtime).run_view_repeats(
        Query("S", contract.relation, 0), contract, view
    ):
        graph.add_entity_mentions(outcome.record, outcome.entities)

    candidate = graph.candidates["alpha"]
    assert candidate.raw_support_count == 4        # amplified
    assert candidate.independent_support == 1      # still one mechanism


def test_repeats_are_rejected_when_decoding_is_deterministic():
    """Greedy repeats would be identical, so `runs > 1` must be a config error."""
    view = dataclasses.replace(get_view("hasArea", "area_direct_km2"), runs=3)
    assert view.decode.deterministic
    with pytest.raises(ValueError, match="greedy decoding"):
        view.validate()


def test_shipped_views_all_validate():
    for view in VIEW_LIBRARY.values():
        view.validate()


# --- gates are not acquisition mechanisms ----------------------------------


def test_gate_views_use_the_gate_family():
    gates = [v for v in VIEW_LIBRARY.values() if v.is_gate]
    assert gates
    for gate in gates:
        assert gate.family is ViewFamily.GATE
        assert gate.independence_group is IndependenceGroup.EXISTENCE_GATE


def test_the_gate_family_is_not_a_candidate_family():
    assert ViewFamily.GATE not in CANDIDATE_FAMILIES
    assert set(CANDIDATE_FAMILIES) == {
        ViewFamily.DIRECT,
        ViewFamily.STRUCTURAL,
        ViewFamily.DESCRIPTION,
        ViewFamily.CONTRASTIVE,
        ViewFamily.MISSINGNESS,
        ViewFamily.REVERSE,
    }


def test_a_gate_never_inflates_the_coverage_denominator():
    """Every eligible mechanism must be reachable, so F(o) can reach 1.0."""
    for contract in all_contracts():
        producing = {
            get_view(contract.relation, v).independence_group
            for v in contract.all_views()
            if not get_view(contract.relation, v).is_gate
        }
        assert set(contract.eligible_independence_groups) == producing
        assert IndependenceGroup.EXISTENCE_GATE not in contract.eligible_independence_groups


def test_gated_relations_can_still_reach_full_coverage():
    for relation in ("companyTradesAtStockExchange", "personHasCityOfDeath"):
        contract = get_contract(relation)
        producing = {
            get_view(relation, v).independence_group
            for v in contract.all_views()
            if not get_view(relation, v).is_gate
        }
        assert len(producing) == contract.coverage_denominator()


def test_a_gate_yields_a_verdict_and_no_candidates():
    contract = get_contract("personHasCityOfDeath")
    runtime = enumerator({("death_status_gate", "S", contract.relation): ["NO"]})
    outcome = ElicitationEngine(runtime).run_view(
        Query("S", contract.relation, 0), contract, get_view(contract.relation, "death_status_gate")
    )
    assert outcome.gate is not None and outcome.gate.is_negative
    assert outcome.entities == [] and outcome.numbers == []


# --- mandatory vs optional acquisition (brief §9) --------------------------


def test_mandatory_and_optional_views_are_disjoint_and_declared():
    for contract in all_contracts():
        assert set(contract.mandatory_views).isdisjoint(contract.optional_views)
        assert contract.mandatory_views  # every relation needs an initial regime


def test_default_discovery_runs_only_mandatory_views():
    for contract in all_contracts():
        graph = CoverPipeline(enumerator(), PipelineConfig()).enumerate_query(
            Query("S", contract.relation, 0)
        )
        ran = {r.view_id for r in graph.records.values()}
        assert ran <= set(contract.mandatory_views)
        assert not (ran & set(contract.optional_views))


def test_the_controller_path_does_not_force_every_optional_view():
    contract = get_contract("awardWonBy")
    config = PipelineConfig(enable_active_controller=True, max_steps_per_query=12)
    graph = CoverPipeline(enumerator(), config).enumerate_query(
        Query("S", contract.relation, 0)
    )
    ran = {r.view_id for r in graph.records.values()}
    assert ran != set(contract.all_views())  # not an unconditional sweep


def test_optional_views_remain_selectable_rather_than_unreachable():
    """Subject-only optional views are offered to the controller, not run automatically.

    Candidate-conditioned (reverse) views are excluded on purpose: they need a
    candidate, so they are not subject-only actions. Module 2 exposes them via
    `run_reverse_view`; scheduling them is a Module 7 item.
    """
    from cover_kbc.controller import legal_actions
    from cover_kbc.coverage import RCSEState
    from cover_kbc.types import Budget

    for contract in all_contracts():
        offered = {
            a.view_id
            for a in legal_actions(contract, [], RCSEState(), Budget(max_calls=99))
            if a.view_id
        }
        subject_only = {
            v for v in contract.optional_views if not get_view(contract.relation, v).is_reverse
        }
        assert subject_only <= offered
        reverse = {
            v for v in contract.optional_views if get_view(contract.relation, v).is_reverse
        }
        assert not (reverse & offered)


# --- structural diversity, not paraphrase (brief §7) -----------------------


def test_each_relation_uses_several_distinct_families():
    for contract in all_contracts():
        families = {get_view(contract.relation, v).family for v in contract.all_views()}
        candidate_families = families & set(CANDIDATE_FAMILIES)
        assert len(candidate_families) >= 2, contract.relation


def test_no_two_views_of_one_relation_share_a_family_and_facet():
    """Two views in the same family must at least explore different subspaces."""
    for contract in all_contracts():
        seen = set()
        for view_id in contract.all_views():
            view = get_view(contract.relation, view_id)
            key = (view.family, view.facet)
            assert key not in seen, f"{contract.relation}/{view_id} duplicates {key}"
            seen.add(key)


def test_award_facets_share_one_mechanism_but_differ_by_subspace():
    facets = [
        get_view("awardWonBy", v)
        for v in ("award_facet_temporal", "award_facet_recipient_type", "award_facet_category")
    ]
    assert len({f.independence_group for f in facets}) == 1
    assert len({f.facet for f in facets}) == 3
    assert len({f.template for f in facets}) == 3


def test_null_single_gets_no_open_set_facet_explosion():
    contract = get_contract("personHasCityOfDeath")
    assert contract.program_type is ProgramType.NULL_SINGLE
    assert not contract.program.supports_missingness
    families = {get_view(contract.relation, v).family for v in contract.all_views()}
    assert ViewFamily.MISSINGNESS not in families
    assert len(contract.all_views()) <= 4


def test_large_open_set_exposes_facet_and_missingness_acquisition():
    contract = get_contract("awardWonBy")
    families = {get_view(contract.relation, v).family for v in contract.all_views()}
    assert ViewFamily.MISSINGNESS in families
    assert ViewFamily.STRUCTURAL in families
    assert len(contract.all_views()) > len(get_contract("countryLandBordersCountry").all_views())


# --- prompt / RelationContract boundary (brief §10) ------------------------


DUPLICATION_PROBES = (
    "maritime", "record attendance", "average attendance", "seated-only",
    "parent", "subsidiary", "land-only", "nominee", "rescinded", "predecessor",
)


def test_templates_do_not_restate_contract_hard_negatives():
    """Module-0 exclusions belong to the contract, not copied into Module 2."""
    offenders = []
    for (relation, view_id), view in VIEW_LIBRARY.items():
        contract = get_contract(relation)
        body = view.template.lower()
        for probe in DUPLICATION_PROBES:
            if any(probe in rule.lower() for rule in contract.hard_negative_rules) and probe in body:
                offenders.append(f"{view_id}:{probe}")
    assert offenders == []


def test_views_that_reason_about_exclusions_inject_the_contract_definition():
    for (relation, view_id), view in VIEW_LIBRARY.items():
        if view.family in (ViewFamily.CONTRASTIVE, ViewFamily.GATE):
            assert "{definition}" in view.template, view_id


def test_the_definition_block_reaches_the_rendered_prompt():
    contract = get_contract("hasCapacity")
    prompt = get_view(contract.relation, "capacity_contrast").render(
        subject="Testvenue", definition=contract.verifier_definition()
    )
    for rule in contract.hard_negative_rules:
        assert rule in prompt


def test_view_library_holds_no_benchmark_answers():
    blob = " ".join(v.template for v in VIEW_LIBRARY.values())
    for factual in ("Denmark", "Poland", "Germany", "NYSE", "Nobel", "Wembley", "Wikidata"):
        assert factual not in blob
    assert not any(ch.isdigit() for ch in blob)


def test_no_retrieval_is_reachable_from_the_elicitation_path():
    """No import or call in Module 2 can reach an external factual source."""
    import ast

    from cover_kbc.elicitation import engine, parsing, views

    banned = {"requests", "urllib", "httpx", "aiohttp", "socket", "wikipedia", "wikidata"}
    for module in (library_module, engine, parsing, views):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned, module.__name__
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in banned, module.__name__


def test_the_system_prompt_states_the_closed_book_rule():
    from cover_kbc.elicitation.views import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "internal" in lowered
    assert "no access to search" in lowered


# --- parser routing (brief §11) --------------------------------------------


def test_numeric_relations_are_parsed_numerically():
    for relation in ("hasArea", "hasCapacity"):
        contract = get_contract(relation)
        view = get_view(relation, contract.mandatory_views[0])
        runtime = enumerator({(view.view_id, "S", relation): ["35,000"]})
        outcome = ElicitationEngine(runtime).run_view(Query("S", relation, 0), contract, view)
        assert outcome.numbers == [35000.0]     # not split on the comma
        assert outcome.entities == []


def test_entity_relations_are_parsed_as_entities():
    contract = get_contract("countryLandBordersCountry")
    view = get_view(contract.relation, "borders_direct")
    runtime = enumerator({(view.view_id, "S", contract.relation): ["Alpha; Beta"]})
    outcome = ElicitationEngine(runtime).run_view(Query("S", contract.relation, 0), contract, view)
    assert outcome.entities == ["Alpha", "Beta"]
    assert outcome.numbers == []


def test_the_parser_is_chosen_from_the_contract_output_type():
    for contract in all_contracts():
        view = get_view(contract.relation, contract.mandatory_views[-1])
        runtime = enumerator({(view.view_id, "S", contract.relation): ["Alpha; 12"]})
        outcome = ElicitationEngine(runtime).run_view(
            Query("S", contract.relation, 0), contract, view
        )
        if contract.output_type is OutputType.NUMBER:
            assert outcome.entities == []
        else:
            assert outcome.numbers == []


def test_a_backend_failure_is_captured_not_raised():
    class Exploding(ScriptedRuntime):
        def generate(self, request):
            raise RuntimeError("backend down")

    contract = get_contract("countryLandBordersCountry")
    outcome = ElicitationEngine(Exploding({})).run_view(
        Query("S", contract.relation, 0), contract, get_view(contract.relation, "borders_direct")
    )
    assert outcome.record.error is not None
    assert outcome.entities == []


def test_malformed_output_yields_no_candidates_but_keeps_provenance():
    contract = get_contract("countryLandBordersCountry")
    view = get_view(contract.relation, "borders_direct")
    runtime = enumerator({(view.view_id, "S", contract.relation): ["I'm sorry, I cannot help."]})
    outcome = ElicitationEngine(runtime).run_view(Query("S", contract.relation, 0), contract, view)
    assert outcome.entities == []
    assert outcome.record.raw_output              # the raw generation survives
    assert outcome.record.prompt_hash


# --- provenance (brief §12, spec §7.4) -------------------------------------

REQUIRED_PROVENANCE = (
    "record_id", "query", "view_id", "view_family", "independence_group", "facet_id",
    "run_id", "model_id", "model_family", "model_role", "prompt", "prompt_hash",
    "raw_output", "decode_profile", "parsed_values", "prompt_tokens", "generated_tokens",
)


def test_every_elicitation_call_carries_the_required_provenance():
    contract = get_contract("countryLandBordersCountry")
    view = get_view(contract.relation, "borders_direct")
    runtime = enumerator({(view.view_id, "S", contract.relation): ["Alpha; Beta"]})
    record = ElicitationEngine(runtime).run_view(
        Query("S", contract.relation, 0), contract, view
    ).record

    for field in REQUIRED_PROVENANCE:
        value = getattr(record, field)
        assert value is not None and value != "", field
    assert record.model_role is ModelRole.ENUMERATOR
    assert record.parsed_values == ["Alpha", "Beta"]


def test_provenance_survives_into_the_candidate_record():
    contract = get_contract("countryLandBordersCountry")
    script = {("borders_direct", "S", contract.relation): ["Alpha; Beta"]}
    result = CoverPipeline(enumerator(script), PipelineConfig()).run(
        [Query("S", contract.relation, 0)]
    )
    for candidate in result.predictions[0].candidates:
        assert candidate.record_ids
        assert candidate.facet_ids
        for edge in candidate.all_evidence():
            assert edge.record_id and edge.view_id and edge.model_family


def test_latency_is_nullable_with_a_documented_reason():
    """The scripted stub has no clock; a real runtime populates it."""
    contract = get_contract("countryLandBordersCountry")
    record = ElicitationEngine(enumerator()).run_view(
        Query("S", contract.relation, 0), contract, get_view(contract.relation, "borders_direct")
    ).record
    assert record.latency_ms is None
    from cover_kbc.models.base import GenerationResult

    assert "latency_ms" in GenerationResult.__dataclass_fields__


# --- cross-model acquisition vs verification (brief §14) -------------------


def test_cross_model_recall_is_acquisition_not_verification():
    contract = get_contract("countryLandBordersCountry")
    view = get_view(contract.relation, "borders_direct")
    verifier = ScriptedRuntime(
        {(view.view_id, "S", contract.relation): ["Delta"]},
        model_id="offline/qwen", family="qwen", role="verifier",
    )
    outcome = ElicitationEngine(verifier).run_view(
        Query("S", contract.relation, 0), contract, view,
        independence_group=IndependenceGroup.CROSS_MODEL_RECALL,
    )
    assert outcome.record.independence_group is IndependenceGroup.CROSS_MODEL_RECALL
    assert outcome.record.model_family == "qwen"
    assert outcome.entities == ["Delta"]


def test_independent_recall_and_shown_candidate_cannot_be_conflated():
    from cover_kbc.evidence.graph import build_graph

    contract = get_contract("countryLandBordersCountry")
    view = get_view(contract.relation, "borders_direct")
    verifier = ScriptedRuntime(
        {(view.view_id, "S", contract.relation): ["Delta"]},
        model_id="offline/qwen", family="qwen", role="verifier",
    )
    graph = build_graph(Query("S", contract.relation, 0), contract)
    outcome = ElicitationEngine(verifier).run_view(
        Query("S", contract.relation, 0), contract, view,
        independence_group=IndependenceGroup.CROSS_MODEL_RECALL,
    )
    graph.add_entity_mentions(outcome.record, outcome.entities)

    edges = graph.candidates["delta"].all_evidence()
    assert all(e.mode is EvidenceMode.INDEPENDENT_RECALL for e in edges)
    assert IndependenceGroup.BLIND_VERIFIER not in graph.candidates["delta"].groups


# --- no hidden scheduling (brief §17) --------------------------------------


def test_module_2_contains_no_hidden_repetition_schedule():
    """All shipped views run once; repetition is opt-in per view, not implicit."""
    assert {v.runs for v in VIEW_LIBRARY.values()} == {1}


def test_the_engine_does_not_decide_which_optional_views_to_run():
    source = inspect.getsource(ElicitationEngine)
    for leak in ("optional_views", "residual", "should_stop", "marginal_yield"):
        assert leak not in source


def test_elicitation_never_finalises_an_answer():
    source = inspect.getsource(library_module) + inspect.getsource(ElicitationEngine)
    for leak in ("ACCEPTED", "decide_status", "finalize", "select_"):
        assert leak not in source


# ==========================================================================
# Relation-focused description (spec Table 5) — brief §11
# ==========================================================================

DESCRIPTION_RELATIONS = ("countryLandBordersCountry", "companyTradesAtStockExchange",
                         "personHasCityOfDeath")


def _description_view(relation):
    return next(
        get_view(relation, v)
        for v in get_contract(relation).all_views()
        if get_view(relation, v).is_description
    )


def test_relation_focused_description_exists_where_table_5_motivates_it():
    for relation in DESCRIPTION_RELATIONS:
        view = _description_view(relation)
        assert view.family is ViewFamily.DESCRIPTION
        assert view.independence_group is IndependenceGroup.RELATION_FOCUSED_DESCRIPTION


def test_description_is_genuinely_two_stage_not_a_renamed_direct_prompt():
    """A: stage 1 asks for prose; stage 2 may only read that prose."""
    for relation in DESCRIPTION_RELATIONS:
        view = _description_view(relation)
        assert view.description_template                      # stage 1 exists
        assert "{context}" in view.template                   # stage 2 consumes it
        assert "do not produce a list" in view.description_template.lower()
        # The two stages are different prompts.
        assert view.description_template != view.template


def test_generated_prose_is_never_itself_a_candidate():
    """B: the description stage yields no candidates and no evidence edge."""
    relation = "countryLandBordersCountry"
    contract = get_contract(relation)
    view = _description_view(relation)
    runtime = enumerator({(view.view_id, "S", relation): [
        "The frontier of S meets Alpha along a river and Beta across a range.",
        "Alpha; Beta",
    ]})
    description, extraction = ElicitationEngine(runtime).run_description_view(
        Query("S", relation, 0), contract, view
    )
    assert description.entities == [] and description.numbers == []
    assert description.record.parsed_values == []
    assert description.record.stage == "description"
    assert extraction.entities == ["Alpha", "Beta"]


def test_extracted_candidates_enter_the_ordinary_pipeline():
    """C: they become normal candidates facing the usual scoring path."""
    from cover_kbc.evidence.graph import build_graph

    relation = "countryLandBordersCountry"
    contract = get_contract(relation)
    view = _description_view(relation)
    runtime = enumerator({(view.view_id, "S", relation): ["prose about Alpha", "Alpha"]})
    graph = build_graph(Query("S", relation, 0), contract)

    description, extraction = ElicitationEngine(runtime).run_description_view(
        Query("S", relation, 0), contract, view
    )
    graph.register_record(description.record)
    graph.add_entity_mentions(extraction.record, extraction.entities)

    candidate = graph.candidates["alpha"]
    assert candidate.independent_support == 1     # one mechanism, not two calls
    assert candidate.status.value == "UNRESOLVED"  # nothing auto-accepted


def test_context_and_extraction_provenance_stay_linked():
    """D: the chain description -> extraction is recoverable."""
    relation = "companyTradesAtStockExchange"
    contract = get_contract(relation)
    view = _description_view(relation)
    runtime = enumerator({(view.view_id, "S", relation): ["S is listed on Alpha.", "Alpha"]})
    description, extraction = ElicitationEngine(runtime).run_description_view(
        Query("S", relation, 0), contract, view
    )

    assert extraction.record.source_record_id == description.record.record_id
    assert description.record.record_id != extraction.record.record_id
    assert description.record.view_id == extraction.record.view_id     # one mechanism
    assert description.record.raw_output in extraction.record.prompt   # context flowed
    for record in (description.record, extraction.record):
        assert record.model_id and record.prompt_hash
        assert record.prompt_tokens is not None and record.generated_tokens is not None


def test_repeated_description_runs_remain_one_mechanism():
    """E: repetition amplifies, it does not multiply independence."""
    relation = "countryLandBordersCountry"
    contract = get_contract(relation)
    view = _description_view(relation)
    runtime = enumerator({(view.view_id, "S", relation): ["prose", "Alpha"]})
    engine = ElicitationEngine(runtime)

    groups = set()
    for run_id in (0, 1, 2):
        _, extraction = engine.run_description_view(
            Query("S", relation, 0), contract, view, run_id=run_id
        )
        groups.add(extraction.record.independence_group)
    assert groups == {IndependenceGroup.RELATION_FOCUSED_DESCRIPTION}


def test_description_views_are_optional_not_mandatory():
    """F: never run during mandatory initial discovery."""
    for relation in DESCRIPTION_RELATIONS:
        contract = get_contract(relation)
        view = _description_view(relation)
        assert view.view_id in contract.optional_views
        assert view.view_id not in contract.mandatory_views

        graph = CoverPipeline(enumerator(), PipelineConfig()).enumerate_query(
            Query("S", relation, 0)
        )
        assert view.view_id not in {r.view_id for r in graph.records.values()}


def test_the_pipeline_can_execute_a_description_view_when_selected():
    relation = "countryLandBordersCountry"
    view = _description_view(relation)
    script = {(view.view_id, "S", relation): ["prose mentioning Alpha", "Alpha"]}
    graph = CoverPipeline(
        enumerator(script), PipelineConfig(run_optional_views=True)
    ).enumerate_query(Query("S", relation, 0))

    stages = {r.stage for r in graph.records.values() if r.view_id == view.view_id}
    assert stages == {"description", "extraction"}
    assert "alpha" in graph.candidates


# ==========================================================================
# Reverse / alternate framing (spec Table 5, §7.3) — brief §12
# ==========================================================================

REVERSE_RELATIONS = ("countryLandBordersCountry", "companyTradesAtStockExchange", "awardWonBy")


def _reverse_view(relation):
    return next(
        get_view(relation, v)
        for v in get_contract(relation).all_views()
        if get_view(relation, v).is_reverse
    )


def test_reverse_views_exist_on_the_multi_object_entity_relations():
    for relation in REVERSE_RELATIONS:
        view = _reverse_view(relation)
        assert view.family is ViewFamily.REVERSE
        assert view.independence_group is IndependenceGroup.REVERSE_ALTERNATE


def test_reverse_acquisition_is_candidate_conditioned():
    """A: the candidate is an input, not something the view discovers."""
    relation = "countryLandBordersCountry"
    contract = get_contract(relation)
    view = _reverse_view(relation)
    runtime = enumerator({(view.view_id, "S", relation): ["Alpha"]})
    outcome = ElicitationEngine(runtime).run_reverse_view(
        Query("S", relation, 0), contract, view, "Alpha"
    )
    assert "Alpha" in outcome.record.prompt
    assert outcome.record.source_candidate_key == "Alpha"

    with pytest.raises(ValueError, match="needs a candidate"):
        ElicitationEngine(runtime).run_reverse_view(
            Query("S", relation, 0), contract, view, ""
        )


def test_reverse_is_structurally_different_from_direct_recall():
    """B: different prompt, different mechanism."""
    relation = "countryLandBordersCountry"
    direct = get_view(relation, "borders_direct")
    reverse = _reverse_view(relation)
    assert reverse.template != direct.template
    assert reverse.independence_group is not direct.independence_group


def test_reverse_is_not_the_blind_verifier():
    """C: no fixed A/B/C label set, no logit scoring, different group."""
    relation = "countryLandBordersCountry"
    contract = get_contract(relation)
    view = _reverse_view(relation)
    prompt = view.render(
        subject="S", definition=contract.verifier_definition(), candidate="Alpha"
    )
    assert "A = VALID" not in prompt
    assert "B = INVALID" not in prompt
    assert "Return exactly one label" not in prompt
    assert view.independence_group is not IndependenceGroup.BLIND_VERIFIER


def test_reverse_output_cannot_finalise_a_candidate():
    """D: it produces a mention, which still faces the ordinary decision path."""
    from cover_kbc.evidence.graph import build_graph

    relation = "countryLandBordersCountry"
    contract = get_contract(relation)
    view = _reverse_view(relation)
    runtime = enumerator({(view.view_id, "S", relation): ["Alpha"]})
    graph = build_graph(Query("S", relation, 0), contract)
    outcome = ElicitationEngine(runtime).run_reverse_view(
        Query("S", relation, 0), contract, view, "Alpha"
    )
    graph.add_entity_mentions(outcome.record, outcome.entities)

    candidate = graph.candidates["alpha"]
    assert candidate.status.value == "UNRESOLVED"
    assert not candidate.verifications          # no verdict was manufactured


def test_reverse_provenance_records_the_full_chain():
    """E: source candidate, view, run, model, prompt."""
    relation = "awardWonBy"
    contract = get_contract(relation)
    view = _reverse_view(relation)
    runtime = enumerator({(view.view_id, "S", relation): ["Alpha"]})
    record = ElicitationEngine(runtime).run_reverse_view(
        Query("S", relation, 0), contract, view, "Alpha", run_id=2
    ).record

    assert record.source_candidate_key == "Alpha"
    assert record.view_id == view.view_id
    assert record.run_id == 2
    assert record.model_id and record.model_family
    assert record.prompt and record.prompt_hash
    assert record.stage == "reverse"


def test_repeating_a_reverse_check_stays_one_mechanism():
    """F: repeats do not multiply independence."""
    from cover_kbc.evidence.graph import build_graph

    relation = "countryLandBordersCountry"
    contract = get_contract(relation)
    view = _reverse_view(relation)
    runtime = enumerator({(view.view_id, "S", relation): ["Alpha"]})
    engine = ElicitationEngine(runtime)
    graph = build_graph(Query("S", relation, 0), contract)

    for run_id in (0, 1, 2):
        outcome = engine.run_reverse_view(
            Query("S", relation, 0), contract, view, "Alpha", run_id=run_id
        )
        graph.add_entity_mentions(outcome.record, outcome.entities)

    candidate = graph.candidates["alpha"]
    assert candidate.raw_support_count == 3
    assert candidate.independent_support == 1


def test_reverse_is_not_forced_for_every_candidate_or_query():
    """G: never executed during ordinary discovery."""
    for relation in REVERSE_RELATIONS:
        view = _reverse_view(relation)
        for config in (PipelineConfig(), PipelineConfig(run_optional_views=True),
                       PipelineConfig(enable_active_controller=True)):
            graph = CoverPipeline(enumerator(), config).enumerate_query(
                Query("S", relation, 0)
            )
            assert view.view_id not in {r.view_id for r in graph.records.values()}


def test_numeric_and_null_single_have_no_reverse_view():
    """Reverse on a scalar collapses into verification, so it is excluded."""
    for relation in ("hasArea", "hasCapacity", "personHasCityOfDeath"):
        contract = get_contract(relation)
        assert not any(get_view(relation, v).is_reverse for v in contract.all_views())
