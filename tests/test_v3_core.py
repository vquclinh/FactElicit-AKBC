"""V3 relation-conditioned hypothesis-search core tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from cover_kbc.contracts.relation_profile import (
    RELATION_PROFILES,
    all_relation_profiles,
    get_relation_profile,
)
from cover_kbc.controller_calibration.readiness import (
    FROZEN_ENUMERATOR_ID,
    FROZEN_ENUMERATOR_REVISION,
    FROZEN_PARAMETER_TOTAL,
    FROZEN_VERIFIER_ID,
    FROZEN_VERIFIER_REVISION,
    ReadinessState,
    evaluate_v3_core_readiness,
    ordered_identity_digest,
)
from cover_kbc.diagnostics.stages import FailureSearchState
from cover_kbc.evidence.consensus import AtomicConsensusEngine
from cover_kbc.evidence.consensus_types import (
    CONSENSUS_VERSION,
    CandidateConsensusState,
    EvidencePlane,
    EvidenceRole,
    GroupSupport,
    NumericClusterConsensus,
    QueryConsensusResult,
    RiskFlag,
    SemanticDisagreement,
)
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.normalization.numeric import cluster_values, parse_numbers, relative_distance
from cover_kbc.paths import BENCHMARK_EVALUATOR, SPLIT_FILES
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.query_intelligence import (
    ParametricRetriever,
    PromptProgramCompiler,
    QueryProfiler,
)
from cover_kbc.specialists import (
    LargeSetSpecialist,
    NullTemporalSpecialist,
    NumericSpecialist,
    SmallSetSpecialist,
)
from cover_kbc.specialists.null_temporal_types import LocalityMentionKind
from cover_kbc.specialists.small_set_types import (
    ListingTemporalStatus,
    StockMentionKind,
)
from cover_kbc.types import Prediction, Query
from cover_kbc.v3_core import (
    ActionHistory,
    Hypothesis,
    HypothesisStatus,
    NoveltyChange,
    PromptFamily,
    PromptSupportUnit,
    V3ActionFamily,
    V3CoreConfig,
    award_promote_suppress_round,
    build_hypothesis_graph,
    capacity_qualifier_from_text,
    classify_death_slot,
    classify_listing_disambiguation,
    independent_prompt_support,
    legal_action_families,
    prompt_family_for_group,
    render_seen_set,
)
from cover_kbc.v3_core.hypothesis import CapacityQualifier
from cover_kbc.v3_core.relation_programs import (
    DeathAttributeSlot,
    ListingDisambiguation,
    relation_train_collection_actions,
)
from cover_kbc.verification.v3_modes import (
    ContrastOutcome,
    V3VerificationMode,
    V3VerificationRequest,
    render_v3_verification_prompt,
    select_contrast_pair,
    stock_rejection_first_request,
)

ROOT = Path(__file__).resolve().parents[1]


def _group(
    group_key: str, *, total_events: int = 1,
    role: EvidenceRole = EvidenceRole.CORE_ACQUISITION,
    facets: tuple[str, ...] = (),
) -> GroupSupport:
    return GroupSupport(
        group_key=group_key,
        plane=EvidencePlane.CORE,
        role=role,
        q_g=1,
        total_events=total_events,
        origin_event_ids=tuple(f"{group_key}:{i}" for i in range(total_events)),
        facets=facets,
    )


def _candidate(
    key: str,
    *,
    relation: str = "hasArea",
    display: str | None = None,
    groups: tuple[GroupSupport, ...] | None = None,
    total: int | None = None,
    disagreements: tuple[SemanticDisagreement, ...] = (),
    risk_flags: tuple[RiskFlag, ...] = (),
    verifier_label: str | None = None,
    hard: bool = False,
    cluster: int | None = None,
    annotations: tuple[str, ...] = (),
) -> CandidateConsensusState:
    groups = groups or (_group("core:DIRECT_RECALL"),)
    return CandidateConsensusState(
        relation=relation,
        subject="Subject Alpha",
        row_index=0,
        candidate_key=key,
        display=display or key,
        candidate_kind="NUMBER" if relation in {"hasArea", "hasCapacity"} else "ENTITY",
        group_supports=groups,
        origin_event_ids=tuple(
            origin for group in groups for origin in group.origin_event_ids
        ),
        event_ids=tuple(f"event:{group.group_key}" for group in groups),
        i_independent_support=len(groups),
        total_support_events=total if total is not None else sum(
            group.total_events for group in groups
        ),
        disagreement_details=disagreements,
        d_semantic=1.0 if disagreements else 0.0,
        risk_flags=risk_flags,
        annotations=annotations,
        verifier_label=verifier_label,
        hard_contract_violation=hard,
        numeric_cluster_index=cluster,
    )


def _consensus(
    relation: str, candidates: tuple[CandidateConsensusState, ...],
    numeric_clusters: tuple[NumericClusterConsensus, ...] = (),
) -> QueryConsensusResult:
    return QueryConsensusResult(
        consensus_version=CONSENSUS_VERSION,
        relation=relation,
        subject="Subject Alpha",
        row_index=0,
        applicable_specialist="M12",
        candidates=candidates,
        numeric_clusters=numeric_clusters,
    )


def test_relation_profiles_still_six_and_v3_actions_fail_closed() -> None:
    assert len(all_relation_profiles()) == 6
    assert set(RELATION_PROFILES) == {
        "hasArea",
        "hasCapacity",
        "companyTradesAtStockExchange",
        "personHasCityOfDeath",
        "awardWonBy",
        "countryLandBordersCountry",
    }
    with pytest.raises(KeyError):
        legal_action_families("unknownRelation", FailureSearchState.NO_CANDIDATE)


def test_prompt_family_independence_does_not_count_same_family_repeats() -> None:
    units = (
        PromptSupportUnit(PromptFamily.DIRECT, "core:DIRECT_RECALL", "enumerator"),
        PromptSupportUnit(PromptFamily.DIRECT, "core:DIRECT_RECALL", "enumerator"),
        PromptSupportUnit(PromptFamily.DEFINITION, "core:CONTRASTIVE_DEFINITION",
                          "enumerator"),
    )
    assert independent_prompt_support(units) == 2
    assert prompt_family_for_group("specialist:contrastive_definition") is (
        PromptFamily.CONTRAST
    )


def test_hypothesis_graph_raw_majority_is_not_independent_support() -> None:
    candidate = _candidate(
        "296", groups=(_group("core:DIRECT_RECALL", total_events=3),), total=3
    )
    graph = build_hypothesis_graph(
        _consensus("hasArea", (candidate,)), get_relation_profile("hasArea")
    )
    hypothesis = graph.hypotheses[0]
    assert hypothesis.raw_support_count == 3
    assert hypothesis.independent_support_count == 1
    assert HypothesisStatus.SUPPORTED.value not in [
        t.to_status.value for t in hypothesis.transitions
    ]
    assert hypothesis.status is HypothesisStatus.SEMANTICALLY_ALIGNED


def test_hypothesis_graph_supports_different_families_and_serializes_stably() -> None:
    candidate = _candidate(
        "296",
        groups=(
            _group("core:DIRECT_RECALL"),
            _group("specialist:contrastive_definition", facets=("definition",)),
        ),
    )
    graph = build_hypothesis_graph(
        _consensus("hasArea", (candidate,)), get_relation_profile("hasArea")
    )
    hypothesis = graph.hypotheses[0]
    assert hypothesis.independent_support_count == 2
    assert HypothesisStatus.SUPPORTED.value in [
        t.to_status.value for t in hypothesis.transitions
    ]
    assert graph.to_json() == graph.to_json()


def test_terminal_dropped_hypothesis_cannot_be_resurrected() -> None:
    hypothesis = Hypothesis(
        hypothesis_id="h1",
        normalized_value="x",
        display="x",
        semantic_type=__import__(
            "cover_kbc.v3_core.hypothesis", fromlist=["SemanticType"]
        ).SemanticType.ENTITY,
    )
    dropped = hypothesis.transition(
        HypothesisStatus.CONTRADICTED, "bad slot"
    ).transition(HypothesisStatus.DROPPED, "terminal")
    with pytest.raises(ValueError):
        dropped.transition(HypothesisStatus.VERIFIED, "later support")


def test_numeric_reconstruction_uses_authoritative_normalization_and_clusters() -> None:
    parsed = parse_numbers("296 km2 and 250 km2")
    assert [v.value for v in parsed] == [296.0, 250.0]
    assert relative_distance(296.0, 300.0) < 0.05
    clusters = cluster_values([296.0, 300.0, 250.0], threshold=0.05)
    assert len(clusters) == 2
    numeric = NumericClusterConsensus(
        cluster_index=0,
        representative=298.0,
        dispersion=0.01,
        canonical_unit="km2",
        values=(296.0, 300.0),
        total_support=2,
        independent_support=2,
        independence_groups=("core:DIRECT_RECALL", "specialist:definition"),
        candidate_keys=("296", "300"),
        competing_clusters=1,
    )
    graph = build_hypothesis_graph(
        _consensus("hasArea", (), (numeric,)), get_relation_profile("hasArea")
    )
    assert graph.numeric_clusters[0].competing_cluster_ids == ()
    assert graph.numeric_clusters[0].representative == 298.0


def test_capacity_qualifiers_keep_current_and_historical_distinct() -> None:
    assert capacity_qualifier_from_text("52,000 current maximum spectators") is (
        CapacityQualifier.CURRENT_MAXIMUM
    )
    assert capacity_qualifier_from_text("48,000 historical pre-renovation") is (
        CapacityQualifier.HISTORICAL
    )
    candidate = _candidate("48000", relation="hasCapacity",
                           display="48000 historical capacity")
    graph = build_hypothesis_graph(
        _consensus("hasCapacity", (candidate,)),
        get_relation_profile("hasCapacity"),
    )
    assert graph.hypotheses[0].semantic_qualifier == "HISTORICAL"
    assert "HISTORICAL_CAPACITY" in graph.hypotheses[0].ambiguity_flags


def test_award_promote_suppress_iterate_tracks_novelty_and_bounds_seen_text() -> None:
    seen = tuple(f"Recipient {i:02d}" for i in range(30))
    text = render_seen_set(seen, max_items=5, max_chars=80)
    assert "(+25 more)" in text
    assert len(text) <= 80
    round_two = award_promote_suppress_round(
        round_index=2,
        seen=("A", "B", "C"),
        proposed=("B", "D", "D", "E"),
        prompt_family="ALTERNATIVE",
        facet_id="missingness",
    )
    assert round_two.new_candidates == ("D", "E")
    assert round_two.novelty.new_set_members == 2
    duplicate = award_promote_suppress_round(
        round_index=3, seen=("A", "B"), proposed=("A", "B"),
        prompt_family="ALTERNATIVE", facet_id="missingness",
    )
    assert duplicate.should_stop_for_zero_novelty


def test_stock_listing_disambiguator_and_rejection_first_mode() -> None:
    assert classify_listing_disambiguation(
        StockMentionKind.TARGET_EXCHANGE) is ListingDisambiguation.DIRECT_LISTING
    assert classify_listing_disambiguation(
        StockMentionKind.PARENT_COMPANY_LISTING) is (
            ListingDisambiguation.PARENT_COMPANY_ONLY
        )
    assert classify_listing_disambiguation(
        StockMentionKind.SUBSIDIARY_LISTING) is ListingDisambiguation.SUBSIDIARY_ONLY
    assert classify_listing_disambiguation(
        StockMentionKind.TARGET_EXCHANGE,
        temporal_status=ListingTemporalStatus.FORMER_OR_DELISTED,
    ) is ListingDisambiguation.HISTORICAL_ONLY
    assert classify_listing_disambiguation(
        StockMentionKind.TARGET_EXCHANGE,
        text="ADR depositary instrument",
    ) is ListingDisambiguation.ADR_OR_DEPOSITARY
    assert classify_listing_disambiguation(
        StockMentionKind.TARGET_EXCHANGE,
        text="OTC market segment",
    ) is ListingDisambiguation.OTC_OR_MARKET_CONFUSION
    request = stock_rejection_first_request(
        relation="companyTradesAtStockExchange",
        subject="Example Co",
        target_id="nasdaq",
        target_text="NASDAQ",
        relation_definition="the subject company itself trades on the exchange",
    )
    prompt = render_v3_verification_prompt(request)
    assert request.mode is V3VerificationMode.SEMANTIC
    assert request.rejection_first
    assert "should NOT be a direct answer" in prompt
    assert "strongly believes" not in prompt
    parent = _candidate(
        "nasdaq",
        relation="companyTradesAtStockExchange",
        display="NASDAQ",
        annotations=("mention_kind=PARENT_COMPANY_LISTING",),
    )
    graph = build_hypothesis_graph(
        _consensus("companyTradesAtStockExchange", (parent,)),
        get_relation_profile("companyTradesAtStockExchange"),
    )
    assert graph.hypotheses[0].listing_disambiguation is (
        ListingDisambiguation.PARENT_COMPANY_ONLY
    )


def test_death_attribute_contrast_slots_do_not_promote_nearby_locations() -> None:
    assert classify_death_slot(LocalityMentionKind.TARGET_CITY) is (
        DeathAttributeSlot.DEATH_CITY
    )
    assert classify_death_slot(LocalityMentionKind.BIRTHPLACE).is_target is False
    assert classify_death_slot(LocalityMentionKind.BURIAL_PLACE).is_target is False
    assert classify_death_slot(LocalityMentionKind.COUNTRY_OR_REGION).is_target is False


def test_relation_specific_action_legality_and_borders_freeze() -> None:
    assert legal_action_families(
        "awardWonBy", FailureSearchState.SET_GROWING
    ) == (V3ActionFamily.SET_EXPANSION,)
    assert V3ActionFamily.LISTING_ELIMINATION in legal_action_families(
        "companyTradesAtStockExchange", FailureSearchState.HIGH_FP_RISK
    )
    assert V3ActionFamily.SET_EXPANSION not in relation_train_collection_actions(
        "companyTradesAtStockExchange"
    )
    assert legal_action_families(
        "countryLandBordersCountry", FailureSearchState.NO_CANDIDATE
    ) == ()
    assert V3ActionFamily.SET_EXPANSION not in relation_train_collection_actions(
        "countryLandBordersCountry"
    )
    assert V3ActionFamily.LISTING_ELIMINATION not in relation_train_collection_actions(
        "hasArea"
    )


def test_repeated_zero_gain_action_becomes_inadmissible() -> None:
    history = ActionHistory()
    history.record(FailureSearchState.NO_CANDIDATE, V3ActionFamily.MULTI_VIEW_RECALL)
    assert not history.admissible(
        FailureSearchState.NO_CANDIDATE, V3ActionFamily.MULTI_VIEW_RECALL
    )
    history.record(
        FailureSearchState.SINGLE_LOW_SUPPORT,
        V3ActionFamily.INDEPENDENT_RECALL,
        NoveltyChange(new_prompt_families=1),
    )
    assert history.admissible(
        FailureSearchState.SINGLE_LOW_SUPPORT, V3ActionFamily.INDEPENDENT_RECALL
    )


def test_m17_v3_modes_use_typed_score_label_surfaces() -> None:
    unary = V3VerificationRequest(
        relation="awardWonBy", subject="Prize", mode=V3VerificationMode.UNARY,
        target_id="a", target_text="Recipient A",
        relation_definition="recipient of the exact award",
    )
    contrast = V3VerificationRequest(
        relation="hasArea", subject="Region", mode=V3VerificationMode.CONTRAST,
        target_id="h1", target_text="296 km2",
        comparison_id="h2", comparison_text="250 km2",
        relation_definition="total area",
    )
    assert unary.label_schema == ("VALID", "INVALID", "UNKNOWN")
    assert contrast.label_schema == (
        ContrastOutcome.H1.value, ContrastOutcome.H2.value, ContrastOutcome.UNKNOWN.value
    )
    prompt = render_v3_verification_prompt(contrast)
    assert "H1: 296 km2" in prompt and "H2: 250 km2" in prompt
    assert "confidence" not in prompt.lower()
    pair = select_contrast_pair((
        {"hypothesis_id": "h1", "normalized_value": "296",
         "independent_support_count": 2, "raw_support_count": 3},
        {"hypothesis_id": "h2", "normalized_value": "250",
         "independent_support_count": 1, "raw_support_count": 10},
        {"hypothesis_id": "h3", "normalized_value": "296",
         "independent_support_count": 1, "raw_support_count": 1},
    ))
    assert pair == ("h1", "h2")


def _pipeline_with_v3(enabled: bool) -> tuple[Prediction, int, int]:
    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    pipeline = CoverPipeline(
        runtime,
        PipelineConfig(v3_core=V3CoreConfig(enabled=enabled)),
        profiler=QueryProfiler(),
        prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
        numeric_specialist=NumericSpecialist(),
        large_set_specialist=LargeSetSpecialist(),
        null_temporal_specialist=NullTemporalSpecialist(),
        small_set_specialist=SmallSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
    )
    result = pipeline.run([Query("Country Alpha", "countryLandBordersCountry", 0)])
    assert len(pipeline.v3_core_results) == (1 if enabled else 0)
    return result.predictions[0], runtime.calls, runtime.generated_tokens


def test_v3_shadow_observation_does_not_change_predictions_or_accounting() -> None:
    off, off_calls, off_tokens = _pipeline_with_v3(False)
    on, on_calls, on_tokens = _pipeline_with_v3(True)
    assert off.to_official_row() == on.to_official_row()
    assert off.empty_reason == on.empty_reason
    assert off.stopped_reason == on.stopped_reason
    assert off.calls_used == on.calls_used
    assert off.generated_tokens_used == on.generated_tokens_used
    assert off_calls == on_calls
    assert off_tokens == on_tokens


def test_v3_readiness_distinguishes_core_from_production_calibration() -> None:
    config = yaml.safe_load(
        (ROOT / "configs/experiments/cover_kbc_v3_train_collection.yaml").read_text()
    )
    report = evaluate_v3_core_readiness(config, base_dir=ROOT, split="train")
    payload = report.to_json()
    assert report.state is ReadinessState.V3_TRAIN_COLLECTION_READY
    assert report.may_run_v3_train_collection
    assert payload["details"]["v2_calibrated_baseline"] == "READY"
    assert payload["details"]["v3_core_source"] == "IMPLEMENTED"
    assert payload["details"]["v3_production_calibration"] == "NOT_READY"
    assert payload["details"]["v3_official_test"] == "NOT_READY"


def test_v3_config_refuses_test_ready_semantics() -> None:
    config = yaml.safe_load(
        (ROOT / "configs/experiments/cover_kbc_v3_train_collection.yaml").read_text()
    )
    report = evaluate_v3_core_readiness(config, base_dir=ROOT, split="test")
    assert report.state is ReadinessState.NOT_READY
    assert not report.may_run_test
    assert any("train" in blocker.lower() for blocker in report.blockers)


def test_model_pair_revisions_and_benchmark_hashes_remain_unchanged() -> None:
    config = yaml.safe_load(
        (ROOT / "configs/experiments/cover_kbc_v3_train_collection.yaml").read_text()
    )
    assert config["model_profile"]["enumerator"]["model_id"] == FROZEN_ENUMERATOR_ID
    assert config["model_profile"]["enumerator"]["revision"] == (
        FROZEN_ENUMERATOR_REVISION
    )
    assert config["model_profile"]["verifier"]["model_id"] == FROZEN_VERIFIER_ID
    assert config["model_profile"]["verifier"]["revision"] == FROZEN_VERIFIER_REVISION
    assert config["budget_assertion"]["total_published_parameters"] == (
        FROZEN_PARAMETER_TOTAL
    )
    assert hashlib.sha256(BENCHMARK_EVALUATOR.read_bytes()).hexdigest() == (
        "2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22"
    )
    expected = {
        "train": (477, "ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e"),
        "val": (475, "ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be"),
        "test": (475, "67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1"),
    }
    for split, (rows, digest) in expected.items():
        raw = SPLIT_FILES[split].read_bytes()
        assert len([line for line in raw.splitlines() if line.strip()]) == rows
        assert hashlib.sha256(raw).hexdigest() == digest
    test_rows = [
        yaml.safe_load(line)
        for line in SPLIT_FILES["test"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert ordered_identity_digest(
        (row["SubjectEntity"], row["Relation"]) for row in test_rows
    ) == "69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640"
