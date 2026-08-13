"""Profile F1 Capacity exactness repair probe guards."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

import pytest
import yaml

from cover_kbc.leaderboard_repair.capacity import (
    CAPACITY_MULTIVIEW_FEATURE,
    VIEW_IDS as CAPACITY_VIEW_IDS,
)
from cover_kbc.leaderboard_repair.capacity_exactness import (
    CAPACITY_EMPTY,
    CAPACITY_EXACTNESS_FEATURE,
    CAPACITY_EXACTNESS_MODE,
    CAPACITY_FALLBACK_CLUSTER,
    CAPACITY_FALLBACK_V1,
    CAPACITY_HOMOGENEOUS_ROUND_CONSENSUS,
    CAPACITY_ORIGINAL_JUDGE,
    CAPACITY_REPAIR_CORROBORATES_E3,
    CAPACITY_REPAIR_EMPTY_STRONG,
    CAPACITY_REPAIR_NOT_SUSPICIOUS,
    CAPACITY_REPAIR_REJECTED,
    CAPACITY_REPAIR_STRONG_OVERRIDE,
    CAPACITY_REPEATED_VALUE_PRIOR,
    CAPACITY_ROUND_10K,
    CAPACITY_ROUND_1K,
    CAPACITY_ROUND_5K,
    EXACTNESS_VIEW_IDS,
    INVALID,
    UNKNOWN,
    VALID_RECALL,
    CapacityExactnessRecall,
    CapacitySuspicionScorer,
    capacity_exactness_view_prompt,
    capacity_round_flags,
    capacity_roundness_metadata,
    cluster_capacity_exactness_recalls,
    decide_capacity_exactness_repair,
    parse_capacity_exactness_output,
    top_capacity_exactness_cluster,
)
from cover_kbc.leaderboard_repair.config import (
    CapacityExactnessAcceptanceConfig,
    LeaderboardRepairConfig,
)
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack
from cover_kbc.leaderboard_repair.types import RowRepairRecord
from cover_kbc.leaderboard_repair.util import AREA, AWARD, BORDERS, CAPACITY, CITY, STOCK
from cover_kbc.models.base import GenerationRequest
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.models.registry import model_blocks, spec_from_config
from cover_kbc.types import Prediction, Query


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
E3_PATH = CONFIG_DIR / "cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml"
F1_PATH = CONFIG_DIR / "cover_kbc_v3_8_profile_f1_capacity_exactness_repair_test.yaml"
MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
MISTRAL_PARAMETERS = 24_011_361_280
PARAMETER_LIMIT = 32_000_000_000


class CapturingScriptedRuntime(ScriptedRuntime):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests: list[GenerationRequest] = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _prediction(subject: str, relation: str, values=None, row_index=0) -> Prediction:
    return Prediction(
        subject=subject,
        relation=relation,
        object_entities=list(values or []),
        row_index=row_index,
    )


def _query(prediction: Prediction) -> Query:
    return Query(prediction.subject, prediction.relation, prediction.row_index)


def _record(
    subject: str,
    values=None,
    *,
    reason: str = "strong_cluster",
    view_values: tuple[int, ...] = (),
) -> RowRepairRecord:
    output = list(values or [])
    record = RowRepairRecord(
        subject=subject,
        relation=CAPACITY,
        row_index=0,
        before=[],
        after=list(output),
        budget_cap=9,
    )
    for index, value in enumerate(view_values):
        record.add_decision(
            CAPACITY_MULTIVIEW_FEATURE,
            "view_output",
            view_id=CAPACITY_VIEW_IDS[index],
            status="VALID_CAPACITY",
            value=value,
        )
    record.add_decision(
        CAPACITY_MULTIVIEW_FEATURE,
        "final_decision",
        reason=reason,
        values=list(output),
    )
    return record


def _recall(
    view_id: str,
    value: int,
    *,
    config: str = "SEATED",
    exactness: str = "EXACT",
) -> CapacityExactnessRecall:
    return CapacityExactnessRecall(
        value=value,
        config=config,
        exactness=exactness,
        view_id=view_id,
    )


def _cluster(
    value: int,
    *,
    support: int,
    exact: int,
    known: int,
) -> list:
    recalls = []
    for index in range(support):
        recalls.append(
            _recall(
                EXACTNESS_VIEW_IDS[index],
                value,
                config="SEATED" if index < known else "UNKNOWN",
                exactness="EXACT" if index < exact else "APPROXIMATE",
            )
        )
    return cluster_capacity_exactness_recalls(recalls)


def _f1_config() -> LeaderboardRepairConfig:
    return LeaderboardRepairConfig.from_mapping(_load(F1_PATH)["leaderboard_repair"])


def _script_module(name: str):
    path = REPO_ROOT / "scripts" / name
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))


def _capacity_script(subject: str, outputs: Mapping[str, str]) -> dict[tuple[str, str, str], list[str]]:
    return {
        (view_id, subject, CAPACITY): [text]
        for view_id, text in outputs.items()
    }


def _baseline_rows() -> list[dict]:
    rows: list[dict] = []
    rows.extend({"SubjectEntity": f"Award {i}", "Relation": AWARD, "ObjectEntities": ["X"]} for i in range(10))
    rows.extend({"SubjectEntity": f"Stock {i}", "Relation": STOCK, "ObjectEntities": ["Exchange"]} for i in range(100))
    rows.extend({"SubjectEntity": f"Border {i}", "Relation": BORDERS, "ObjectEntities": ["Neighbor"]} for i in range(67))
    rows.extend({"SubjectEntity": f"Area {i}", "Relation": AREA, "ObjectEntities": [str(i + 1)]} for i in range(100))
    rows.extend({"SubjectEntity": f"Capacity {i}", "Relation": CAPACITY, "ObjectEntities": ["10000"]} for i in range(98))
    rows.extend({"SubjectEntity": f"City {i}", "Relation": CITY, "ObjectEntities": []} for i in range(100))
    assert len(rows) == 475
    return rows


def _capacity_result_rows() -> list[dict]:
    return [
        {
            "SubjectEntity": f"Capacity {i}",
            "Relation": CAPACITY,
            "ObjectEntities": [str(12000 + i)],
        }
        for i in range(98)
    ]


def test_suspicion_scorer_flags_empty_ambiguous_round_repeated_and_homogeneous_rows():
    predictions = [
        _prediction("Empty Venue", CAPACITY, []),
        _prediction("Judge Venue", CAPACITY, ["12347"]),
        _prediction("Fallback V1 Venue", CAPACITY, ["8000"]),
        _prediction("Fallback Cluster Venue", CAPACITY, ["9000"]),
        _prediction("Round Venue A", CAPACITY, ["10000"]),
        _prediction("Round Venue B", CAPACITY, ["10000"]),
        _prediction("Round Venue C", CAPACITY, ["10000"]),
        _prediction("Distinctive Venue", CAPACITY, ["42317"]),
        _prediction("Company", STOCK, ["NYSE"]),
    ]
    records = {
        ("Empty Venue", CAPACITY): _record("Empty Venue", [], reason="no_numeric_values"),
        ("Judge Venue", CAPACITY): _record("Judge Venue", ["12347"], reason="judge_selected"),
        ("Fallback V1 Venue", CAPACITY): _record("Fallback V1 Venue", ["8000"], reason="fallback_v1"),
        ("Fallback Cluster Venue", CAPACITY): _record("Fallback Cluster Venue", ["9000"], reason="fallback_top_cluster"),
        ("Round Venue A", CAPACITY): _record("Round Venue A", ["10000"], view_values=(10000, 10000, 10000, 10000)),
        ("Round Venue B", CAPACITY): _record("Round Venue B", ["10000"], view_values=(10000, 10000, 10000, 10000)),
        ("Round Venue C", CAPACITY): _record("Round Venue C", ["10000"], view_values=(10000, 10000, 10000, 10000)),
        ("Distinctive Venue", CAPACITY): _record("Distinctive Venue", ["42317"], view_values=(42317, 42317, 42317)),
    }
    scored = CapacitySuspicionScorer().score_batch(predictions, records)

    assert scored[("Empty Venue", CAPACITY)].risk_flags == (CAPACITY_EMPTY,)
    assert CAPACITY_ORIGINAL_JUDGE in scored[("Judge Venue", CAPACITY)].risk_flags
    assert CAPACITY_FALLBACK_V1 in scored[("Fallback V1 Venue", CAPACITY)].risk_flags
    assert CAPACITY_FALLBACK_CLUSTER in scored[("Fallback Cluster Venue", CAPACITY)].risk_flags

    round_flags = scored[("Round Venue A", CAPACITY)].risk_flags
    assert CAPACITY_ROUND_1K in round_flags
    assert CAPACITY_ROUND_5K in round_flags
    assert CAPACITY_ROUND_10K in round_flags
    assert CAPACITY_REPEATED_VALUE_PRIOR in round_flags
    assert CAPACITY_HOMOGENEOUS_ROUND_CONSENSUS in round_flags
    assert scored[("Round Venue A", CAPACITY)].repeated_value_subject_count == 3

    distinctive = scored[("Distinctive Venue", CAPACITY)]
    assert not set(distinctive.risk_flags) & {
        CAPACITY_ROUND_1K,
        CAPACITY_ROUND_5K,
        CAPACITY_ROUND_10K,
    }
    assert ("Company", STOCK) not in scored


def test_repetition_policy_counts_distinct_subjects_not_subject_identity_patterns():
    predictions = [
        _prediction("Venue Alpha", CAPACITY, ["7000"]),
        _prediction("Venue Beta", CAPACITY, ["7000"]),
        _prediction("Venue Gamma", CAPACITY, ["7000"]),
        _prediction("Venue Alpha", CAPACITY, ["7000"]),
    ]
    records = {
        (prediction.subject, prediction.relation): _record(
            prediction.subject,
            prediction.object_entities,
        )
        for prediction in predictions
    }
    scored = CapacitySuspicionScorer().score_batch(predictions, records)
    assert scored[("Venue Alpha", CAPACITY)].repeated_value_subject_count == 3
    assert CAPACITY_REPEATED_VALUE_PRIOR in scored[("Venue Alpha", CAPACITY)].risk_flags

    distinctive_predictions = [
        _prediction("Venue With Pattern 1", CAPACITY, ["7001"]),
        _prediction("Venue With Pattern 2", CAPACITY, ["7002"]),
        _prediction("Venue With Pattern 3", CAPACITY, ["7003"]),
    ]
    distinctive_records = {
        (prediction.subject, prediction.relation): _record(
            prediction.subject,
            prediction.object_entities,
        )
        for prediction in distinctive_predictions
    }
    distinctive = CapacitySuspicionScorer().score_batch(
        distinctive_predictions,
        distinctive_records,
    )
    assert all(
        CAPACITY_REPEATED_VALUE_PRIOR not in result.risk_flags
        for result in distinctive.values()
    )


def test_suspicion_scorer_uses_zero_neural_calls():
    runtime = ScriptedRuntime()
    predictions = [_prediction("Venue", CAPACITY, ["10000"])]
    records = {("Venue", CAPACITY): _record("Venue", ["10000"])}
    before = runtime.calls
    CapacitySuspicionScorer().score_batch(predictions, records)
    assert runtime.calls == before == 0


def test_exactness_parser_accepts_only_strict_structured_outputs():
    first = parse_capacity_exactness_output(
        "CAPACITY: 42300\nCONFIG: SEATED\nEXACTNESS: EXACT",
        view_id="capacity_exactness_x1",
    )
    assert first.status == VALID_RECALL
    assert first.recall.value == 42300
    assert first.recall.config == "SEATED"
    assert first.recall.exactness == "EXACT"

    second = parse_capacity_exactness_output(
        "CAPACITY: 42,300\nCONFIG: TOTAL\nEXACTNESS: EXACT",
        view_id="capacity_exactness_x2",
    )
    assert second.status == VALID_RECALL
    assert second.recall.value == 42300
    assert parse_capacity_exactness_output("UNKNOWN").status == UNKNOWN

    for text in (
        "The capacity is 42300.",
        "CAPACITY: 42000-43000\nCONFIG: SEATED\nEXACTNESS: APPROXIMATE",
        "CAPACITY: 42300\nCONFIG: BOX\nEXACTNESS: EXACT",
        "CAPACITY: 42300\nCONFIG: TOTAL\nEXACTNESS: CERTAIN",
        "CAPACITY: 42300\nCONFIG: TOTAL\nEXACTNESS: EXACT\nCAPACITY: 42400",
        "CAPACITY: 0\nCONFIG: TOTAL\nEXACTNESS: EXACT",
        "CAPACITY: -1\nCONFIG: TOTAL\nEXACTNESS: EXACT",
        "CAPACITY: NaN\nCONFIG: TOTAL\nEXACTNESS: EXACT",
        "CAPACITY: Infinity\nCONFIG: TOTAL\nEXACTNESS: EXACT",
        "42300",
    ):
        assert parse_capacity_exactness_output(text).status == INVALID, text


def test_exactness_view_prompts_are_four_candidate_blind_deterministic_contracts():
    prompts = [capacity_exactness_view_prompt("Exact Venue", view_id) for view_id in EXACTNESS_VIEW_IDS]
    assert len(set(prompts)) == 4
    assert "published spectator-capacity fact" in prompts[0]
    assert "distinctive, non-generic capacity number" in prompts[1]
    assert "configuration memory" in prompts[2]
    assert "generic plausible" in prompts[3]
    for prompt in prompts:
        assert "Subject: Exact Venue" in prompt
        assert "CAPACITY: <integer>" in prompt
        assert "CONFIG: <label>" in prompt
        assert "EXACTNESS: <label>" in prompt
        assert "UNKNOWN" in prompt
        assert "Candidate =" not in prompt


def test_exactness_clustering_counts_support_exact_config_and_roundness():
    recalls = [
        _recall("capacity_exactness_x1", 10000, config="SEATED", exactness="EXACT"),
        _recall("capacity_exactness_x2", 10200, config="TOTAL", exactness="APPROXIMATE"),
        _recall("capacity_exactness_x3", 9800, config="UNKNOWN", exactness="ESTIMATE"),
        _recall("capacity_exactness_x4", 13000, config="SPORT", exactness="EXACT"),
    ]
    clusters = cluster_capacity_exactness_recalls(recalls)
    assert [cluster.support for cluster in clusters] == [3, 1]
    top = top_capacity_exactness_cluster(clusters)
    assert top.representative == 10000
    assert top.representative in top.values
    assert top.exact_count == 1
    assert top.approximate_count == 1
    assert top.estimate_count == 1
    assert top.known_config_count == 2
    assert top.to_json()["roundness"] == {
        "divisible_by_1000": True,
        "divisible_by_5000": True,
        "divisible_by_10000": True,
        "non_round_distinctive": False,
    }

    separated = cluster_capacity_exactness_recalls([
        _recall("capacity_exactness_x1", 10000),
        _recall("capacity_exactness_x2", 10527),
    ])
    assert [cluster.support for cluster in separated] == [1, 1]
    assert capacity_roundness_metadata(42317)["non_round_distinctive"] is True
    assert capacity_round_flags(42317) == []


def test_acceptance_policy_empty_rescue_and_keep_empty_cases():
    rescue = decide_capacity_exactness_repair(
        e3_values=[],
        clusters=_cluster(12347, support=3, exact=2, known=2),
    )
    assert rescue.values == ("12347",)
    assert rescue.reason == CAPACITY_REPAIR_EMPTY_STRONG
    assert rescue.action == "REPLACE"

    keep_empty = decide_capacity_exactness_repair(
        e3_values=[],
        clusters=_cluster(12347, support=2, exact=2, known=2),
    )
    assert keep_empty.values == ()
    assert keep_empty.reason == CAPACITY_REPAIR_REJECTED
    assert keep_empty.action == "KEEP_E3"


def test_acceptance_policy_corrobates_overrides_and_rejects_conservatively():
    corroborates = decide_capacity_exactness_repair(
        e3_values=["10000"],
        clusters=_cluster(10020, support=3, exact=2, known=2),
    )
    assert corroborates.values == ("10000",)
    assert corroborates.reason == CAPACITY_REPAIR_CORROBORATES_E3

    override = decide_capacity_exactness_repair(
        e3_values=["10000"],
        clusters=_cluster(12347, support=3, exact=2, known=2),
    )
    assert override.values == ("12347",)
    assert override.reason == CAPACITY_REPAIR_STRONG_OVERRIDE

    rejected_round = decide_capacity_exactness_repair(
        e3_values=["10000"],
        clusters=_cluster(15000, support=3, exact=2, known=2),
    )
    assert rejected_round.values == ("10000",)
    assert rejected_round.reason == CAPACITY_REPAIR_REJECTED

    strong_round = decide_capacity_exactness_repair(
        e3_values=["10000"],
        clusters=_cluster(15000, support=4, exact=3, known=2),
    )
    assert strong_round.values == ("15000",)
    assert strong_round.reason == CAPACITY_REPAIR_STRONG_OVERRIDE

    weak = decide_capacity_exactness_repair(
        e3_values=["10000"],
        clusters=_cluster(12347, support=3, exact=1, known=2),
    )
    assert weak.values == ("10000",)
    assert weak.reason == CAPACITY_REPAIR_REJECTED

    unknown = decide_capacity_exactness_repair(e3_values=["10000"], clusters=[])
    assert unknown.values == ("10000",)
    assert unknown.reason == CAPACITY_REPAIR_REJECTED


def test_stack_runs_exactly_four_f1_calls_only_for_suspicious_capacity_rows():
    subject = "Suspicious Venue"
    runtime = CapturingScriptedRuntime(
        _capacity_script(
            subject,
            {
                "capacity_multiview_v1": "CAPACITY: 10000",
                "capacity_multiview_v2": "CAPACITY: 10000",
                "capacity_multiview_v3": "CAPACITY: 10000",
                "capacity_multiview_v4": "CAPACITY: 10000",
                "capacity_exactness_x1": "CAPACITY: 12347\nCONFIG: SEATED\nEXACTNESS: EXACT",
                "capacity_exactness_x2": "CAPACITY: 12347\nCONFIG: TOTAL\nEXACTNESS: EXACT",
                "capacity_exactness_x3": "CAPACITY: 12347\nCONFIG: SPORT\nEXACTNESS: APPROXIMATE",
                "capacity_exactness_x4": "UNKNOWN",
            },
        ),
        model_id=MISTRAL_ID,
        role="verifier",
    )
    prediction = _prediction(subject, CAPACITY, [], row_index=0)
    stock = _prediction("Company", STOCK, ["NYSE"], row_index=1)
    result = LeaderboardRepairStack(
        config=_f1_config(),
        enumerator=runtime,
        verifier=runtime,
    ).apply([prediction, stock], queries=[_query(prediction), _query(stock)])

    assert result.predictions[0].object_entities == ["12347"]
    assert result.predictions[1].object_entities == ["NYSE"]
    assert [request.metadata["view_id"] for request in runtime.requests] == [
        *CAPACITY_VIEW_IDS,
        *EXACTNESS_VIEW_IDS,
    ]
    exactness_requests = runtime.requests[4:]
    assert all(request.decode.temperature == 0.0 for request in exactness_requests)
    assert all(request.decode.top_p == 1.0 for request in exactness_requests)
    assert all(request.decode.max_new_tokens == 48 for request in exactness_requests)
    assert result.records[0].calls_used == 8
    assert [call.view_id for call in result.records[0].calls[-4:]] == list(EXACTNESS_VIEW_IDS)

    summary = result.accounting["by_relation"][CAPACITY]["capacity_exactness_repair"]
    assert summary["total_capacity_rows"] == 1
    assert summary["suspicious_rows"] == 1
    assert summary["untouched_rows"] == 0
    assert summary["overridden_rows"] == 1
    assert summary["total_capacity_exactness_repair_calls"] == 4
    assert summary["capacity_exactness_x1_calls"] == 1
    assert summary["capacity_exactness_x2_calls"] == 1
    assert summary["capacity_exactness_x3_calls"] == 1
    assert summary["capacity_exactness_x4_calls"] == 1


def test_stack_keeps_non_suspicious_capacity_without_f1_calls():
    subject = "Distinctive Venue"
    runtime = CapturingScriptedRuntime(
        _capacity_script(
            subject,
            {
                "capacity_multiview_v1": "CAPACITY: 12347",
                "capacity_multiview_v2": "CAPACITY: 12347",
                "capacity_multiview_v3": "CAPACITY: 12347",
                "capacity_multiview_v4": "CAPACITY: 12347",
            },
        ),
        model_id=MISTRAL_ID,
        role="verifier",
    )
    prediction = _prediction(subject, CAPACITY, [], row_index=0)
    result = LeaderboardRepairStack(
        config=_f1_config(),
        enumerator=runtime,
        verifier=runtime,
    ).apply([prediction], queries=[_query(prediction)])

    assert result.predictions[0].object_entities == ["12347"]
    assert [request.metadata["view_id"] for request in runtime.requests] == list(CAPACITY_VIEW_IDS)
    assert result.records[0].calls_used == 4
    final = result.records[0].decisions[-1]
    assert final["feature"] == CAPACITY_EXACTNESS_FEATURE
    assert final["reason"] == CAPACITY_REPAIR_NOT_SUSPICIOUS
    summary = result.accounting["by_relation"][CAPACITY]["capacity_exactness_repair"]
    assert summary["suspicious_rows"] == 0
    assert summary["untouched_rows"] == 1
    assert summary["total_capacity_exactness_repair_calls"] == 0


def test_profile_f1_diff_from_e3_is_capacity_exactness_only():
    e3 = _load(E3_PATH)
    f1 = _load(F1_PATH)
    assert f1["experiment"]["profile_f1_probe"]["status"] == "UNSCORED_LEADERBOARD_PROBE"
    assert f1["experiment"]["hidden_test_scores_source"] == (
        "FROZEN_PROFILE_E3_BASELINE_NOT_F1"
    )
    repair_e3 = LeaderboardRepairConfig.from_mapping(e3["leaderboard_repair"])
    repair_f1 = LeaderboardRepairConfig.from_mapping(f1["leaderboard_repair"])

    flags_e3 = asdict(repair_e3.features)
    flags_f1 = asdict(repair_f1.features)
    feature_delta = {
        key: [flags_e3[key], flags_f1[key]]
        for key in flags_e3
        if flags_e3[key] != flags_f1[key]
    }
    assert feature_delta == {"mistral_capacity_exactness_repair": [False, True]}

    cap_delta = {
        key: [repair_e3.max_calls_by_relation[key], repair_f1.max_calls_by_relation[key]]
        for key in repair_e3.max_calls_by_relation
        if repair_e3.max_calls_by_relation[key] != repair_f1.max_calls_by_relation[key]
    }
    assert cap_delta == {CAPACITY: [5, 9]}
    assert repair_e3.direct_area_mode == repair_f1.direct_area_mode == "DIRECT_ALL"
    assert repair_e3.area_multiview_mode == repair_f1.area_multiview_mode == "DIRECT_ALL"
    assert repair_e3.capacity_multiview_mode == repair_f1.capacity_multiview_mode == "DIRECT_ALL"
    assert repair_f1.capacity_exactness_repair.enabled is True
    assert repair_f1.capacity_exactness_repair.mode == CAPACITY_EXACTNESS_MODE
    assert repair_f1.capacity_exactness_repair.acceptance == (
        CapacityExactnessAcceptanceConfig()
    )

    for section in (
        "pipeline",
        "query_intelligence",
        "specialists",
        "consensus",
        "specialist_verifier",
        "bidirectional_verification",
        "layer4_integration",
        "coverage_gap",
        "relation_budget_scheduler",
        "micro_planner",
        "layer6_integration",
        "test_dataset",
        "calibration_provenance",
        "model_profile",
        "budget_assertion",
    ):
        assert e3[section] == f1[section]

    assert flags_f1["mistral_area_multiview"] == flags_e3["mistral_area_multiview"] is True
    assert flags_f1["mistral_capacity_multiview"] == flags_e3["mistral_capacity_multiview"] is True
    assert flags_f1["mistral_city_empty_rescue"] == flags_e3["mistral_city_empty_rescue"] is True
    assert flags_f1["award_metadata_cleanup"] == flags_e3["award_metadata_cleanup"] is True
    for flag in (
        "stock_entity_guard",
        "stock_alias_dedupe",
        "stock_multi_listing_rescue",
        "border_alias_dedupe",
        "border_directional_sweep",
        "border_reciprocity",
        "death_existence_gate",
        "death_city_recall",
        "area_empty_rescue",
        "capacity_repair",
        "award_recipient_witness",
        "award_time_sliced_recall",
        "l8_consistency",
        "l8_stock_consistency",
        "l9_final_risk_guard",
        "l9_stock_guard",
    ):
        assert flags_f1[flag] is False


def test_profile_f1_model_portfolio_is_one_mistral_no_qwen_chiv_or_nsmv():
    f1 = _load(F1_PATH)
    enumerator, verifier = model_blocks(f1)
    assert enumerator == verifier
    assert enumerator["model_id"] == MISTRAL_ID
    assert enumerator["revision"] == MISTRAL_REVISION
    assert "Qwen" not in json.dumps(f1["model_profile"], sort_keys=True)

    audit = audit_parameter_budget([spec_from_config(enumerator), spec_from_config(verifier)])
    assert audit.passed
    assert audit.total_parameters == MISTRAL_PARAMETERS
    assert audit.budget == PARAMETER_LIMIT
    assert [component.model_id for component in audit.counted_specs] == [MISTRAL_ID]

    assert f1["experiment"]["profile_f1_probe"]["chiv_active"] is False
    assert f1["experiment"]["profile_f1_probe"]["nsmv_active"] is False
    assert "chiv" not in f1["leaderboard_repair"]
    assert "nsmv" not in json.dumps(f1["leaderboard_repair"], sort_keys=True).lower()


def test_capacity_exactness_targeted_runner_dry_run_and_diagnostics(tmp_path):
    module = _script_module("run_capacity_exactness_repair.py")
    config = _load(F1_PATH)
    paths = module.dry_run(config=config, split="test", output_dir=tmp_path)
    payload = json.loads(paths["dry_run"].read_text(encoding="utf-8"))
    assert payload["total_capacity_rows"] == 98
    assert payload["expected_result_rows"] == 98
    assert payload["feature"] == CAPACITY_EXACTNESS_FEATURE
    assert payload["mode"] == CAPACITY_EXACTNESS_MODE
    assert payload["repair_calls_per_suspicious_row"] == 4
    assert payload["max_total_capacity_calls_per_suspicious_row"] == 9
    assert payload["model_id"] == MISTRAL_ID

    model = module.validate_f1_model_config(config)
    assert model["model_id"] == MISTRAL_ID
    assert model["revision"] == MISTRAL_REVISION
    rows = module.capacity_rows("test")
    assert len(rows) == 98
    assert all(row.relation == CAPACITY for row in rows)


def test_capacity_exactness_targeted_diagnostics_schema_with_mocked_record():
    subject = "Diagnostic Venue"
    runtime = CapturingScriptedRuntime(
        _capacity_script(
            subject,
            {
                "capacity_multiview_v1": "CAPACITY: 10000",
                "capacity_multiview_v2": "CAPACITY: 10000",
                "capacity_multiview_v3": "CAPACITY: 10000",
                "capacity_multiview_v4": "CAPACITY: 10000",
                "capacity_exactness_x1": "CAPACITY: 12347\nCONFIG: SEATED\nEXACTNESS: EXACT",
                "capacity_exactness_x2": "CAPACITY: 12347\nCONFIG: TOTAL\nEXACTNESS: EXACT",
                "capacity_exactness_x3": "CAPACITY: 12347\nCONFIG: SPORT\nEXACTNESS: APPROXIMATE",
                "capacity_exactness_x4": "UNKNOWN",
            },
        ),
        model_id=MISTRAL_ID,
        role="verifier",
    )
    prediction = _prediction(subject, CAPACITY, [], row_index=0)
    result = LeaderboardRepairStack(
        config=_f1_config(),
        enumerator=runtime,
        verifier=runtime,
    ).apply([prediction], queries=[_query(prediction)])
    module = _script_module("run_capacity_exactness_repair.py")
    diagnostics = module.diagnostics_rows(result.records)
    assert len(diagnostics) == 1
    row = diagnostics[0]
    assert row["original_e3_answer"] == ["10000"]
    assert row["suspicious"] is True
    assert row["suspicion_score"] > 0
    assert set(row["capacity_exactness_outputs"]) == set(EXACTNESS_VIEW_IDS)
    assert row["repair_clusters"]
    assert row["acceptance_decision"]["reason"] == CAPACITY_REPAIR_STRONG_OVERRIDE
    assert row["final_answer"] == ["12347"]
    assert row["f1_repair_call_count"] == 4


def test_capacity_f1_merge_preserves_377_non_capacity_rows_and_fails_closed():
    module = _script_module("merge_targeted_relation_results.py")
    baseline = _baseline_rows()
    targeted = _capacity_result_rows()
    merged, provenance = module.merge_targeted_relation_results(
        baseline,
        targeted,
        relation=CAPACITY,
        expected_targeted_rows=98,
    )
    assert len(merged) == 475
    assert provenance["baseline_rows"] == 475
    assert provenance["targeted_result_rows"] == 98
    assert provenance["non_target_rows_preserved"] == 377
    assert provenance["changed_relation_set"] == [CAPACITY]
    for before, after in zip(baseline, merged):
        if before["Relation"] != CAPACITY:
            assert after == before

    with pytest.raises(module.TargetedMergeError, match="expected 98"):
        module.merge_targeted_relation_results(
            baseline,
            targeted[:-1],
            relation=CAPACITY,
            expected_targeted_rows=98,
        )
    duplicate = list(targeted)
    duplicate[-1] = dict(targeted[0])
    with pytest.raises(module.TargetedMergeError, match="duplicate identity"):
        module.merge_targeted_relation_results(
            baseline,
            duplicate,
            relation=CAPACITY,
            expected_targeted_rows=98,
        )
