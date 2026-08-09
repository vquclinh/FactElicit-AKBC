"""V3D source-activation tests for relation-conditioned TRAIN collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from cover_kbc.contracts.relation_profile import get_relation_profile
from cover_kbc.contracts.router import compile_query
from cover_kbc.control import (
    BudgetLedger,
    CalibrationSource,
    DecisionKind,
    EstimateSource,
    HistoricalActionBin,
    HistoricalBinPackage,
    MicroPlanner,
    PlannerCalibration,
    PlannerStateSnapshot,
    RelationBudgetCalibration,
    StateBinningSpec,
    build_plan,
    load_history,
    relation_policy,
)
from cover_kbc.control.budget_types import CoreBudgetSnapshot
from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
    evaluate_v3_core_readiness,
    evaluate_validation_readiness,
)
from cover_kbc.diagnostics.stages import FailureSearchState
from cover_kbc.elicitation.engine import ElicitationEngine
from cover_kbc.evidence.consensus_types import (
    CONSENSUS_VERSION,
    CandidateConsensusState,
    DisagreementKind,
    EvidencePlane,
    EvidenceRole,
    GroupSupport,
    QueryConsensusResult,
    RiskFlag,
    SemanticDisagreement,
)
from cover_kbc.evidence.graph import build_graph
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.types import (
    Budget,
    CandidateStatus,
    ModelRole,
    OutputType,
    Prediction,
    Query,
    VerificationLabel,
)
from cover_kbc.v3_core import (
    PromptFamily,
    V3ActionFamily,
    V3CoreConfig,
    V3CoreMode,
    build_hypothesis_graph,
    build_v3_action_catalog,
    execute_v3_action,
)
from cover_kbc.v3_core.hypothesis import CapacityQualifier

ROOT = Path(__file__).resolve().parents[1]


class _Residual:
    def __init__(self, value: float) -> None:
        self.residual = value
        self.availability = type("A", (), {"value": "AVAILABLE"})()
        self.components = ()


class _FakeGap:
    def __init__(self, subject: str, relation: str, row_index: int) -> None:
        self.subject = subject
        self.relation = relation
        self.row_index = row_index
        self.residual = _Residual(0.9)
        self.novelty = type("N", (), {"novelty_rate": None})()
        self.disagreement = type("D", (), {"value": None})()
        self.unresolved = type("U", (), {"value": None})()
        self.null_state = None


class _FakeLayer4:
    def __init__(self, subject: str, relation: str, row_index: int) -> None:
        self.subject = subject
        self.relation = relation
        self.row_index = row_index
        self.candidates = ()
        self.numeric_targets = ()


def _consensus_from_graph(
    graph, *, annotations_by_key: dict[str, tuple[str, ...]] | None = None,
) -> QueryConsensusResult:
    annotations_by_key = annotations_by_key or {}
    candidates: list[CandidateConsensusState] = []
    for candidate in sorted(graph.candidates.values(), key=lambda c: c.key):
        groups = []
        for group, evidence_group in sorted(
            candidate.groups.items(), key=lambda item: item[0].value
        ):
            support = bool(evidence_group.supports)
            group_key = f"core:{group.value}"
            groups.append(
                GroupSupport(
                    group_key=group_key,
                    plane=EvidencePlane.CORE,
                    role=EvidenceRole.CORE_ACQUISITION,
                    q_g=1 if support else 0,
                    total_events=len(evidence_group.all_evidence()),
                    origin_event_ids=tuple(
                        edge.record_id for edge in evidence_group.all_evidence()
                    ),
                    facets=tuple(candidate.facet_ids),
                )
            )
        verifications = [
            v for v in candidate.verifications if v.valid_prob is not None
        ]
        latest = verifications[-1] if verifications else None
        risk = []
        if candidate.status is CandidateStatus.REJECTED:
            risk.append(RiskFlag.HARD_CONTRACT_VIOLATION)
        disagreements = []
        for evidence_group in candidate.groups.values():
            for edge in evidence_group.contradictions:
                disagreements.append(
                    SemanticDisagreement(
                        kind=DisagreementKind.TARGET_VERSUS_NEAR_MISS,
                        detail=f"contradiction from {edge.view_id}",
                        origin_event_ids=(edge.record_id,),
                        group_keys=(f"core:{edge.independence_group.value}",),
                    )
                )
        candidates.append(
            CandidateConsensusState(
                relation=graph.query.relation,
                subject=graph.query.subject,
                row_index=graph.query.row_index,
                candidate_key=candidate.key,
                display=candidate.display_value,
                candidate_kind=(
                    "NUMBER"
                    if graph.contract.output_type is OutputType.NUMBER
                    else "ENTITY"
                ),
                group_supports=tuple(groups),
                origin_event_ids=tuple(candidate.record_ids),
                event_ids=tuple(
                    f"{candidate.key}:{index}" for index, _ in enumerate(groups)
                ),
                i_independent_support=sum(1 for g in groups if g.supports),
                total_support_events=candidate.raw_support_count,
                risk_flags=tuple(risk),
                disagreement_details=tuple(disagreements),
                annotations=annotations_by_key.get(candidate.key, ()),
                d_semantic=1.0 if disagreements else 0.0,
                hard_contract_violation=candidate.status is CandidateStatus.REJECTED,
                rejection_reason=candidate.rejection_reason,
                verifier_label=latest.label.value if latest else None,
            )
        )
    return QueryConsensusResult(
        consensus_version=CONSENSUS_VERSION,
        relation=graph.query.relation,
        subject=graph.query.subject,
        row_index=graph.query.row_index,
        applicable_specialist="M12",
        candidates=tuple(candidates),
    )


def _empty_hgraph(relation: str = "hasArea"):
    query, contract = compile_query("Subject Alpha", relation, 0)
    graph = build_graph(query, contract)
    return graph, build_hypothesis_graph(
        _consensus_from_graph(graph), get_relation_profile(relation)
    )


def _first_action(hgraph, graph, family: V3ActionFamily):
    actions = build_v3_action_catalog(hgraph, graph, graph.contract)
    for action in actions:
        if action.family is family:
            return action
    raise AssertionError(f"{family.value} not in {[a.family.value for a in actions]}")


def test_v3_multiview_recall_executes_and_accounts_enumerator() -> None:
    graph, hgraph = _empty_hgraph("hasArea")
    action = _first_action(hgraph, graph, V3ActionFamily.MULTI_VIEW_RECALL)
    enumerator = ScriptedRuntime(
        {(action.view_id, graph.query.subject, graph.query.relation): ["296 km2"]},
        model_id="offline/mistral24",
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    verifier = ScriptedRuntime(
        {}, model_id="offline/qwen4", family="qwen", role=ModelRole.VERIFIER.value
    )

    outcome = execute_v3_action(
        action,
        graph,
        graph.contract,
        enumerator_engine=ElicitationEngine(enumerator),
        verifier_runtime=verifier,
        run_id=1,
    )

    assert enumerator.calls == 1
    assert verifier.calls == 0
    assert outcome.structural_outcome == PromptFamily.DIRECT.value
    assert graph.candidates["296"].numeric_value == 296.0
    record = next(iter(graph.records.values()))
    assert record.model_role is ModelRole.ENUMERATOR
    assert record.view_id == action.view_id
    assert action.budget_descriptor.cost().neural_calls == 1


def test_v3_has_area_action_creates_competing_numeric_hypotheses() -> None:
    graph, hgraph = _empty_hgraph("hasArea")
    action = _first_action(hgraph, graph, V3ActionFamily.MULTI_VIEW_RECALL)
    runtime = ScriptedRuntime(
        {
            (action.view_id, graph.query.subject, graph.query.relation): [
                "296 km2; 300 km2; 250 km2"
            ]
        },
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )

    execute_v3_action(
        action,
        graph,
        graph.contract,
        enumerator_engine=ElicitationEngine(runtime),
        verifier_runtime=runtime,
        run_id=1,
    )
    updated = build_hypothesis_graph(
        _consensus_from_graph(graph), get_relation_profile("hasArea")
    )

    assert {"296", "300", "250"} <= set(graph.candidates)
    assert updated.failure_state is FailureSearchState.MULTIPLE_CONFLICTING
    assert V3ActionFamily.CONTRAST_VERIFY in updated.legal_action_families


def test_v3_capacity_definition_qualifier_survives_into_m16_m17_request() -> None:
    graph, hgraph = _empty_hgraph("hasCapacity")
    action = _first_action(hgraph, graph, V3ActionFamily.DEFINITION_RECALL)
    runtime = ScriptedRuntime(
        {
            (action.view_id, graph.query.subject, graph.query.relation): [
                "52,000 current maximum spectators"
            ]
        },
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )

    execute_v3_action(
        action,
        graph,
        graph.contract,
        enumerator_engine=ElicitationEngine(runtime),
        verifier_runtime=runtime,
        run_id=1,
    )
    first = build_hypothesis_graph(
        _consensus_from_graph(graph), get_relation_profile("hasCapacity")
    )
    alternative = _first_action(first, graph, V3ActionFamily.ALTERNATIVE_RECALL)
    alt_runtime = ScriptedRuntime(
        {
            (alternative.view_id, graph.query.subject, graph.query.relation): [
                "48,000 historical capacity"
            ]
        },
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    execute_v3_action(
        alternative,
        graph,
        graph.contract,
        enumerator_engine=ElicitationEngine(alt_runtime),
        verifier_runtime=alt_runtime,
        run_id=2,
    )
    updated = build_hypothesis_graph(
        _consensus_from_graph(graph), get_relation_profile("hasCapacity")
    )

    qualifiers = {h.semantic_qualifier for h in updated.hypotheses}
    assert CapacityQualifier.CURRENT_MAXIMUM.value in qualifiers
    assert CapacityQualifier.HISTORICAL.value in qualifiers
    verify = next(
        action for action in build_v3_action_catalog(updated, graph, graph.contract)
        if action.family is V3ActionFamily.CONTRAST_VERIFY
    )
    assert verify.metadata["semantic_qualifier"] in qualifiers


def test_v3_award_suppression_changes_prompt_and_zero_novelty_is_bounded() -> None:
    query, contract = compile_query("Prize Alpha", "awardWonBy", 0)
    graph = build_graph(query, contract)
    seed_runtime = ScriptedRuntime(
        {("award_direct", query.subject, query.relation): ["Alice; Bob"]},
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    from cover_kbc.elicitation.library import get_view

    engine = ElicitationEngine(seed_runtime)
    seed = engine.run_view(query, contract, get_view("awardWonBy", "award_direct"))
    graph.add_entity_mentions(seed.record, seed.entities)
    hgraph = build_hypothesis_graph(
        _consensus_from_graph(graph), get_relation_profile("awardWonBy")
    )
    action = _first_action(hgraph, graph, V3ActionFamily.SET_EXPANSION)
    assert tuple(action.metadata["seen"]) == ("Alice", "Bob")

    runtime = ScriptedRuntime(
        {(action.view_id, query.subject, query.relation): ["Bob; Cara"]},
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    outcome = execute_v3_action(
        action,
        graph,
        contract,
        enumerator_engine=ElicitationEngine(runtime),
        verifier_runtime=runtime,
        run_id=2,
    )

    assert "Recipients already identified: Alice; Bob" in runtime.seen_prompts[-1]
    assert set(outcome.candidates_named) == {"Bob", "Cara"}
    assert "Cara" in {candidate.display_value for candidate in graph.candidates.values()}
    history = __import__(
        "cover_kbc.v3_core.relation_programs", fromlist=["ActionHistory", "NoveltyChange"]
    ).ActionHistory()
    history.record(FailureSearchState.SET_GROWING, V3ActionFamily.SET_EXPANSION)
    assert not history.admissible(
        FailureSearchState.SET_GROWING, V3ActionFamily.SET_EXPANSION
    )


def test_v3_stock_elimination_executes_qwen_and_rejects_candidate() -> None:
    query, contract = compile_query("Company Alpha", "companyTradesAtStockExchange", 0)
    graph = build_graph(query, contract)
    runtime = ScriptedRuntime(
        {("stock_exchange_direct", query.subject, query.relation): ["NASDAQ"]},
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    from cover_kbc.elicitation.library import get_view

    outcome = ElicitationEngine(runtime).run_view(
        query, contract, get_view("companyTradesAtStockExchange", "stock_exchange_direct")
    )
    graph.add_entity_mentions(outcome.record, outcome.entities)
    key = contract.strict_key("NASDAQ")
    hgraph = build_hypothesis_graph(
        _consensus_from_graph(
            graph,
            annotations_by_key={key: ("mention_kind=PARENT_COMPANY_LISTING",)},
        ),
        get_relation_profile("companyTradesAtStockExchange"),
    )
    action = _first_action(hgraph, graph, V3ActionFamily.LISTING_ELIMINATION)
    verifier = ScriptedRuntime(
        label_scores={
            (action.view_id, query.subject, query.relation): {
                VerificationLabel.VALID.value: -1.0,
                VerificationLabel.INVALID.value: 4.0,
                VerificationLabel.UNKNOWN.value: 0.0,
            }
        },
        model_id="offline/qwen4",
        family="qwen",
        role=ModelRole.VERIFIER.value,
    )

    result = execute_v3_action(
        action,
        graph,
        contract,
        enumerator_engine=ElicitationEngine(runtime),
        verifier_runtime=verifier,
    )

    assert verifier.calls == 1
    assert runtime.calls == 1
    assert result.verifier_outcome == VerificationLabel.INVALID.value
    assert graph.candidates[key].status is CandidateStatus.REJECTED
    assert "LISTING_ELIMINATION" in (graph.candidates[key].rejection_reason or "")
    assert graph.candidates[key].verifications[-1].model_family == "qwen"


def test_v3_death_decomposition_promotes_only_death_city_and_contradicts_near_slot() -> None:
    query, contract = compile_query("Person Alpha", "personHasCityOfDeath", 0)
    graph = build_graph(query, contract)
    seed_runtime = ScriptedRuntime(
        {("death_city_direct", query.subject, query.relation): ["Paris"]},
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    from cover_kbc.elicitation.library import get_view

    seed = ElicitationEngine(seed_runtime).run_view(
        query, contract, get_view("personHasCityOfDeath", "death_city_direct")
    )
    graph.add_entity_mentions(seed.record, seed.entities)
    hgraph = build_hypothesis_graph(
        _consensus_from_graph(graph), get_relation_profile("personHasCityOfDeath")
    )
    action = _first_action(hgraph, graph, V3ActionFamily.ATTRIBUTE_DECOMPOSITION)
    runtime = ScriptedRuntime(
        {
            (action.view_id, query.subject, query.relation): [
                "birth location: Paris\n"
                "death city: Geneva\n"
                "burial location: Lausanne\n"
                "hospital location: Geneva"
            ]
        },
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )

    result = execute_v3_action(
        action,
        graph,
        contract,
        enumerator_engine=ElicitationEngine(runtime),
        verifier_runtime=runtime,
        run_id=2,
    )

    assert set(result.candidates_named) == {"Paris", "Geneva", "Lausanne"}
    assert "Geneva" in {c.display_value for c in graph.candidates.values()}
    assert "Lausanne" not in {c.display_value for c in graph.candidates.values()}
    paris = graph.candidates[contract.strict_key("Paris")]
    assert paris.status is CandidateStatus.REJECTED
    assert paris.contradiction_count == 1
    assert graph.candidates[contract.strict_key("Geneva")].status is not (
        CandidateStatus.REJECTED
    )


def test_v3_m17_unary_semantic_and_contrast_execute_through_qwen_owner() -> None:
    query, contract = compile_query("Subject Alpha", "hasArea", 0)
    graph = build_graph(query, contract)
    runtime = ScriptedRuntime(
        {("area_direct_km2", query.subject, query.relation): ["296 km2; 250 km2"]},
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    from cover_kbc.elicitation.library import get_view

    outcome = ElicitationEngine(runtime).run_view(
        query, contract, get_view("hasArea", "area_direct_km2")
    )
    graph.add_numeric_mentions(outcome.record, outcome.observations)
    hgraph = build_hypothesis_graph(
        _consensus_from_graph(graph), get_relation_profile("hasArea")
    )
    semantic = hgraph.hypotheses[0]
    actions = build_v3_action_catalog(hgraph, graph, contract)
    contrast = next(a for a in actions if a.family is V3ActionFamily.CONTRAST_VERIFY)
    unary = __import__(
        "cover_kbc.v3_core.execution", fromlist=["V3ActionCandidate"]
    ).V3ActionCandidate(
        subject=query.subject,
        relation=query.relation,
        row_index=query.row_index,
        action_id="manual-unary",
        owner=contrast.owner,
        family=V3ActionFamily.UNARY_VERIFY,
        budget_descriptor=contrast.budget_descriptor,
        prompt_family=PromptFamily.CONTRAST,
        view_id="v3_unary_verify",
        model_role=ModelRole.VERIFIER.value,
        target=semantic.display,
        target_id=semantic.normalized_value,
        metadata={"verification_mode": "UNARY"},
    )
    semantic_action = __import__(
        "cover_kbc.v3_core.execution", fromlist=["V3ActionCandidate"]
    ).V3ActionCandidate(
        subject=query.subject,
        relation=query.relation,
        row_index=query.row_index,
        action_id="manual-semantic",
        owner=contrast.owner,
        family=V3ActionFamily.SEMANTIC_VERIFY,
        budget_descriptor=contrast.budget_descriptor,
        prompt_family=PromptFamily.CONTRAST,
        view_id="v3_semantic_verify",
        model_role=ModelRole.VERIFIER.value,
        target=semantic.display,
        target_id=semantic.normalized_value,
        metadata={"verification_mode": "SEMANTIC"},
    )
    verifier = ScriptedRuntime(
        label_scores={
            ("v3_unary_verify", query.subject, query.relation): {
                "VALID": 3.0,
                "INVALID": 0.0,
                "UNKNOWN": -1.0,
            },
            ("v3_semantic_verify", query.subject, query.relation): {
                "VALID": 3.0,
                "INVALID": 0.0,
                "UNKNOWN": -1.0,
            },
            ("v3_contrast_verify", query.subject, query.relation): {
                "H1": 3.0,
                "H2": 0.0,
                "UNKNOWN": -1.0,
            },
        },
        model_id="offline/qwen4",
        family="qwen",
        role=ModelRole.VERIFIER.value,
    )

    for action in (unary, semantic_action, contrast):
        result = execute_v3_action(
            action,
            graph,
            contract,
            enumerator_engine=ElicitationEngine(runtime),
            verifier_runtime=verifier,
        )
        assert result.verifier_outcome in {"VALID", "H1"}

    assert verifier.calls == 3
    assert all(v.model_family == "qwen" for c in graph.candidates.values() for v in c.verifications)


def test_v3_borders_catalogue_freezes_default_expansion() -> None:
    graph, hgraph = _empty_hgraph("countryLandBordersCountry")
    assert hgraph.legal_action_families == ()
    assert build_v3_action_catalog(hgraph, graph, graph.contract) == ()


def test_v3_control_loop_runs_before_m8_and_can_change_final_prediction() -> None:
    runtime = ScriptedRuntime(
        {
            ("area_direct_km2", "Subject Alpha", "hasArea"): ["UNKNOWN"],
            ("area_total_vs_land", "Subject Alpha", "hasArea"): ["UNKNOWN"],
            ("v3_hasArea_multi_view_recall", "Subject Alpha", "hasArea"): ["296 km2"],
        },
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    finalized: list[bool] = []
    pipeline = CoverPipeline(
        runtime,
        PipelineConfig(
            v3_core=V3CoreConfig(enabled=True, mode=V3CoreMode.TRAIN_COLLECTION),
            max_control_rounds_per_catalogue=1,
            max_steps_per_query=4,
        ),
        consensus_engine=object(),
        integration_mode=IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY,
        action_selector=lambda kind, catalogue: tuple(
            action
            for action in catalogue
            if kind == "v3" and action.family is V3ActionFamily.MULTI_VIEW_RECALL
        )[:1],
    )
    pipeline._build_consensus_snapshot = lambda graph: _consensus_from_graph(graph)  # type: ignore[method-assign]
    original_finalize = __import__(
        "cover_kbc.pipeline", fromlist=["finalize"]
    ).finalize

    def observing_finalize(*args: Any, **kwargs: Any) -> Prediction:
        finalized.append(bool(pipeline.v3_pre_m8_results))
        return original_finalize(*args, **kwargs)

    import cover_kbc.pipeline as pipeline_module

    previous = pipeline_module.finalize
    pipeline_module.finalize = observing_finalize
    try:
        prediction = pipeline.run_query(Query("Subject Alpha", "hasArea", 0))
    finally:
        pipeline_module.finalize = previous

    assert finalized == [True]
    assert prediction.object_entities == ["296"]
    assert [r["kind"] for r in pipeline.action_records if r["executed"]] == ["v3"]
    assert pipeline.v3_pre_m8_results[0].action_family_mapping["pre_m8"] is True
    assert runtime.calls == prediction.calls_used


def test_v3_control_loop_refuses_generated_token_overrun_before_execution() -> None:
    graph, hgraph = _empty_hgraph("hasArea")
    runtime = ScriptedRuntime(
        {(hgraph.legal_action_families[0].value, graph.query.subject, graph.query.relation): [
            "296 km2"
        ]},
        family="mistral",
        role=ModelRole.ENUMERATOR.value,
    )
    pipeline = CoverPipeline(
        runtime,
        PipelineConfig(
            v3_core=V3CoreConfig(enabled=True, mode=V3CoreMode.TRAIN_COLLECTION),
            max_generated_tokens_per_query=1,
            max_control_rounds_per_catalogue=1,
        ),
        consensus_engine=object(),
        integration_mode=IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY,
        action_selector=lambda kind, catalogue: catalogue[:1],
    )
    pipeline.consensus_results.append(_consensus_from_graph(graph))

    pipeline._run_v3_control_loop(graph)

    assert runtime.calls == 0
    assert pipeline.action_records == []


def test_v3_actions_project_to_m21_and_can_be_ranked_by_v3_bins() -> None:
    graph, hgraph = _empty_hgraph("hasArea")
    actions = {
        action.family: action
        for action in build_v3_action_catalog(hgraph, graph, graph.contract)
    }
    recall = actions[V3ActionFamily.MULTI_VIEW_RECALL]
    definition = actions[V3ActionFamily.DEFINITION_RECALL]
    program_type = graph.contract.program_type.value
    state_bin = f"program_type={program_type}"
    history = HistoricalBinPackage(
        history_version="synthetic-v3-history",
        source=EstimateSource.SYNTHETIC_TEST,
        binning=StateBinningSpec(
            spec_version="synthetic-v3-binning",
            categorical_features=("program_type",),
        ),
        bins=(
            HistoricalActionBin(
                relation=graph.query.relation,
                program_type=program_type,
                state_bin_key=state_bin,
                action_family=V3ActionFamily.MULTI_VIEW_RECALL,
                support_count=5,
                expected_verified_gain=1.0,
                expected_delta_r=0.5,
                expected_delta_h=0.0,
                expected_cost=1.0,
                expected_redundancy=0.0,
                expected_fp=0.0,
            ),
            HistoricalActionBin(
                relation=graph.query.relation,
                program_type=program_type,
                state_bin_key=state_bin,
                action_family=V3ActionFamily.DEFINITION_RECALL,
                support_count=5,
                expected_verified_gain=0.0,
                expected_delta_r=0.1,
                expected_delta_h=0.0,
                expected_cost=1.0,
                expected_redundancy=0.0,
                expected_fp=0.0,
            ),
        ),
    )
    history = load_history(history.to_json(), allow_synthetic=True)
    calibration = PlannerCalibration(
        calibration_version="synthetic-v3-planner",
        source=EstimateSource.SYNTHETIC_TEST,
        alpha=1.0,
        beta=1.0,
        gamma=0.0,
        delta=0.0,
        eta=0.0,
        kappa=0.0,
        tau_continue=0.0,
    )
    budget_calibration = RelationBudgetCalibration(
        relation=graph.query.relation,
        calibration_version="synthetic-v3-m20",
        calibration_source=CalibrationSource.SYNTHETIC_TEST,
        hard_calls=10,
        hard_generated_tokens=2000,
        discovery_cap=10,
        verification_cap=10,
        verification_reserve=0,
        special_reserves=tuple(
            (purpose, 1)
            for purpose in relation_policy(graph.query.relation).special_reserve_purposes
        ),
    )
    from cover_kbc.query_intelligence import QueryProfiler

    profile = QueryProfiler().profile(graph.query, graph.contract)
    plan = build_plan(
        subject=graph.query.subject,
        relation=graph.query.relation,
        row_index=graph.query.row_index,
        program_type=program_type,
        profile=profile,
        core_budget=CoreBudgetSnapshot.of(Budget(max_calls=10, max_generated_tokens=2000)),
        calibration=budget_calibration,
    )
    state = PlannerStateSnapshot(
        subject=graph.query.subject,
        relation=graph.query.relation,
        row_index=graph.query.row_index,
        program_type=program_type,
        risk_profile=profile,
        layer4=_FakeLayer4(graph.query.subject, graph.query.relation, graph.query.row_index),
        coverage_gap=_FakeGap(graph.query.subject, graph.query.relation, graph.query.row_index),
        budget_plan=plan,
        budget_ledger=BudgetLedger(plan),
    )

    decision = MicroPlanner(history, calibration).plan(
        state, (recall.to_planner_action(), definition.to_planner_action())
    )

    assert decision.kind is DecisionKind.ACTION
    assert decision.selected_action == recall.action_id
    assert {u.bin_key for u in decision.utilities} == {state_bin}


def test_v3_readiness_fails_closed_for_validation_and_test_without_v3_artifacts() -> None:
    config = yaml.safe_load(
        (ROOT / "configs/experiments/cover_kbc_v3_train_collection.yaml").read_text()
    )

    train = evaluate_v3_core_readiness(config, base_dir=ROOT, split="train")
    validation = evaluate_validation_readiness(config, base_dir=ROOT, split="val")
    test = evaluate_test_readiness(config, base_dir=ROOT, split="test")

    assert train.state is ReadinessState.V3_TRAIN_COLLECTION_READY
    assert train.may_run_v3_train_collection
    assert validation.state is ReadinessState.NOT_READY
    assert not validation.may_run_validation
    assert any("calibration" in blocker.lower() for blocker in validation.blockers)
    assert test.state is ReadinessState.NOT_READY
    assert not test.may_run_test


def test_v3_production_config_cannot_reuse_v2_calibration_artifacts() -> None:
    config = yaml.safe_load(
        (ROOT / "configs/experiments/cover_kbc_v2_validation.yaml").read_text()
    )
    config["pipeline"]["v3_core"] = {
        "enabled": True,
        "mode": "production",
        "schema_version": "v3-core-v1",
        "production_calibration_ready": True,
    }

    report = evaluate_validation_readiness(
        config, base_dir=ROOT / "configs" / "experiments", split="val"
    )

    assert report.state is ReadinessState.NOT_READY
    assert any("may not reuse the historical V2" in b for b in report.blockers)


def test_v3_collection_artifact_schema_paths_are_not_fake_calibration(tmp_path: Path) -> None:
    config = yaml.safe_load(
        (ROOT / "configs/experiments/cover_kbc_v3_train_collection.yaml").read_text()
    )
    outputs = config["v3_calibration_outputs"]
    assert set(outputs) == {
        "m20_relation_budget",
        "m21_historical_bins",
        "m21_planner_calibration",
    }
    for path in outputs.values():
        assert not (ROOT / path).exists()

    payload = {"schema_version": "v3-action-effect-v1", "ok": True}
    target = tmp_path / "v3_action_effects.jsonl"
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == (
        "v3-action-effect-v1"
    )
