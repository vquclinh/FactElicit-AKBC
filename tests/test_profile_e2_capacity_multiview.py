"""Profile E2 Capacity Multi-View baseline guards."""

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
    CAPACITY_MULTIVIEW_MODE,
    INVALID,
    JUDGE_VIEW_ID,
    UNKNOWN,
    VALID_CAPACITY,
    VIEW_IDS,
    capacity_judge_prompt,
    capacity_view_prompt,
    cluster_capacity_values,
    parse_capacity_judge_output,
    parse_capacity_output,
    top_capacity_cluster,
    CapacityObservation,
)
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack
from cover_kbc.leaderboard_repair.util import AREA, AWARD, BORDERS, CAPACITY, CITY, STOCK
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.base import GenerationRequest
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.models.registry import model_blocks, spec_from_config
from cover_kbc.types import Prediction, Query


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
E1_PATH = CONFIG_DIR / "cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml"
E2_PATH = CONFIG_DIR / "cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml"
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


def _stack(config: LeaderboardRepairConfig, runtime: ScriptedRuntime) -> LeaderboardRepairStack:
    return LeaderboardRepairStack(config=config, enumerator=runtime, verifier=runtime)


def _capacity_config(max_calls: int = 5) -> LeaderboardRepairConfig:
    return LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "capacity_multiview_mode": CAPACITY_MULTIVIEW_MODE,
        "features": {"mistral_capacity_multiview": True},
        "max_calls_by_relation": {
            CAPACITY: max_calls,
            AREA: 0,
            CITY: 0,
            AWARD: 0,
            STOCK: 0,
            BORDERS: 0,
        },
    })


def _capacity_script(subject: str, outputs: Mapping[str, str]) -> dict[tuple[str, str, str], list[str]]:
    return {
        (view_id, subject, CAPACITY): [text]
        for view_id, text in outputs.items()
    }


def _run_capacity(subject: str, outputs: Mapping[str, str], *, before=None, max_calls: int = 5):
    runtime = CapturingScriptedRuntime(
        _capacity_script(subject, outputs),
        model_id=MISTRAL_ID,
        role="verifier",
    )
    prediction = _prediction(subject, CAPACITY, before or [], row_index=0)
    result = _stack(_capacity_config(max_calls=max_calls), runtime).apply(
        [prediction],
        queries=[_query(prediction)],
    )
    return result, runtime


def _prediction_affecting_sections(config: dict) -> dict:
    repair = LeaderboardRepairConfig.from_mapping(config["leaderboard_repair"])
    return {
        "pipeline": config["pipeline"],
        "leaderboard_repair_enabled": repair.enabled,
        "leaderboard_repair_features": asdict(repair.features),
        "leaderboard_repair_caps": dict(repair.max_calls_by_relation),
        "leaderboard_repair_direct_area_mode": repair.direct_area_mode,
        "leaderboard_repair_capacity_multiview_mode": repair.capacity_multiview_mode,
        "query_intelligence": config["query_intelligence"],
        "specialists": config["specialists"],
        "consensus": config["consensus"],
        "specialist_verifier": config["specialist_verifier"],
        "bidirectional_verification": config["bidirectional_verification"],
        "layer4_integration": config["layer4_integration"],
        "coverage_gap": config["coverage_gap"],
        "relation_budget_scheduler": config["relation_budget_scheduler"],
        "micro_planner": config["micro_planner"],
        "layer6_integration": config["layer6_integration"],
        "test_dataset": config["test_dataset"],
        "calibration_provenance": config["calibration_provenance"],
        "model_profile": config["model_profile"],
        "budget_assertion": config["budget_assertion"],
    }


def _semantic_diff(before_config: dict, after_config: dict) -> dict:
    before = _prediction_affecting_sections(before_config)
    after = _prediction_affecting_sections(after_config)
    return {
        key: [before[key], after[key]]
        for key in sorted(before)
        if before[key] != after[key]
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
            "ObjectEntities": [str(20000 + i)],
        }
        for i in range(98)
    ]


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / name
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_capacity_parser_is_strict_and_canonicalizes_valid_integers():
    assert parse_capacity_output("CAPACITY: 40000").status == VALID_CAPACITY
    assert parse_capacity_output("CAPACITY: 40000").value == 40000
    assert parse_capacity_output("CAPACITY: 40,000").value == 40000
    assert parse_capacity_output("CAPACITY: 40000.0").value == 40000
    assert parse_capacity_output("UNKNOWN").status == UNKNOWN

    for text in (
        "40000",
        "approximately 40000",
        "CAPACITY: 40000 people",
        "CAPACITY: 40000\nNo explanation",
        "CAPACITY: 100-120",
        "CAPACITY: 114 or 116",
        "CAPACITY:",
        "CAPACITY: NaN",
        "CAPACITY: Infinity",
        "CAPACITY: -1",
        "CAPACITY: 4,00",
    ):
        assert parse_capacity_output(text).status == INVALID, text


def test_all_four_capacity_views_are_distinct_and_render_required_contract():
    prompts = [capacity_view_prompt("Exact Venue, City", view_id) for view_id in VIEW_IDS]
    assert len(set(prompts)) == 4
    assert "maximum spectator capacity" in prompts[0]
    assert "encyclopedic spectator-capacity fact" in prompts[1]
    assert "seated capacity" in prompts[2]
    assert "Before answering, reason internally" in prompts[3]
    for prompt in prompts:
        assert "Subject: Exact Venue, City" in prompt
        assert "CAPACITY: <integer>" in prompt
        assert "UNKNOWN" in prompt


def test_capacity_row_gets_four_view_calls_and_non_capacity_row_gets_none():
    runtime = CapturingScriptedRuntime(
        _capacity_script(
            "Venue A",
            {
                "capacity_multiview_v1": "CAPACITY: 44990",
                "capacity_multiview_v2": "CAPACITY: 44918",
                "capacity_multiview_v3": "CAPACITY: 45000",
                "capacity_multiview_v4": "CAPACITY: 41800",
            },
        ),
        model_id=MISTRAL_ID,
        role="verifier",
    )
    predictions = [
        _prediction("Venue A", CAPACITY, ["99999"], row_index=0),
        _prediction("Company A", STOCK, ["NYSE"], row_index=1),
    ]
    result = _stack(_capacity_config(), runtime).apply(
        predictions,
        queries=[_query(prediction) for prediction in predictions],
    )
    assert result.predictions[0].object_entities == ["44990"]
    assert result.predictions[1].object_entities == ["NYSE"]
    assert [request.metadata["view_id"] for request in runtime.requests] == list(VIEW_IDS)
    assert all(request.decode.max_new_tokens == 24 for request in runtime.requests)
    assert result.records[0].calls_used == 4
    assert result.records[1].calls_used == 0


def test_capacity_clustering_uses_five_percent_tolerance_and_observed_representative():
    observations = [
        CapacityObservation("capacity_multiview_v1", 44990),
        CapacityObservation("capacity_multiview_v2", 44918),
        CapacityObservation("capacity_multiview_v3", 45000),
        CapacityObservation("capacity_multiview_v4", 41800),
    ]
    clusters = cluster_capacity_values(observations)
    assert [cluster.support for cluster in clusters] == [3, 1]
    assert top_capacity_cluster(clusters).representative == 44990

    separated = cluster_capacity_values([
        CapacityObservation("capacity_multiview_v1", 10000),
        CapacityObservation("capacity_multiview_v2", 10527),
    ])
    assert [cluster.support for cluster in separated] == [1, 1]


def test_strong_support_bypasses_judge_and_ignores_upstream_capacity():
    result, runtime = _run_capacity(
        "Venue A",
        {
            "capacity_multiview_v1": "CAPACITY: 44990",
            "capacity_multiview_v2": "CAPACITY: 44918",
            "capacity_multiview_v3": "CAPACITY: 45000",
            "capacity_multiview_v4": "CAPACITY: 41800",
        },
        before=["99999"],
    )
    assert result.predictions[0].object_entities == ["44990"]
    assert [request.metadata["view_id"] for request in runtime.requests] == list(VIEW_IDS)
    assert result.records[0].decisions[-1]["reason"] == "strong_cluster"
    assert result.accounting["by_relation"][CAPACITY]["capacity_multiview"][
        "strong_consensus_count"
    ] == 1


def test_ambiguous_clusters_invoke_source_blind_judge_once():
    result, runtime = _run_capacity(
        "Venue B",
        {
            "capacity_multiview_v1": "CAPACITY: 10000",
            "capacity_multiview_v2": "CAPACITY: 20000",
            "capacity_multiview_v3": "UNKNOWN",
            "capacity_multiview_v4": "INVALID",
            JUDGE_VIEW_ID: "B",
        },
    )
    assert result.predictions[0].object_entities == ["20000"]
    assert [request.metadata["view_id"] for request in runtime.requests] == [
        *VIEW_IDS,
        JUDGE_VIEW_ID,
    ]
    judge_prompt = runtime.requests[-1].prompt
    assert "A. 10000" in judge_prompt
    assert "B. 20000" in judge_prompt
    assert "capacity_multiview_v1" not in judge_prompt
    assert "V1" not in judge_prompt
    assert runtime.requests[-1].decode.max_new_tokens == 4


def test_capacity_judge_parser_and_fallback_order():
    prompt, labels, unknown_label = capacity_judge_prompt("Venue", [10000, 20000])
    assert "A. 10000" in prompt
    assert "B. 20000" in prompt
    assert parse_capacity_judge_output("B", labels, unknown_label) == (
        VALID_CAPACITY,
        20000,
    )
    assert parse_capacity_judge_output(unknown_label, labels, unknown_label) == (
        UNKNOWN,
        None,
    )
    assert parse_capacity_judge_output("Z", labels, unknown_label) == (INVALID, None)

    invalid_judge, _ = _run_capacity(
        "Venue C",
        {
            "capacity_multiview_v1": "CAPACITY: 10000",
            "capacity_multiview_v2": "CAPACITY: 20000",
            "capacity_multiview_v3": "UNKNOWN",
            "capacity_multiview_v4": "INVALID",
            JUDGE_VIEW_ID: "Z",
        },
    )
    assert invalid_judge.predictions[0].object_entities == ["10000"]
    assert invalid_judge.records[0].decisions[-1]["reason"] == "fallback_v1"

    no_v1, _ = _run_capacity(
        "Venue D",
        {
            "capacity_multiview_v1": "UNKNOWN",
            "capacity_multiview_v2": "CAPACITY: 20000",
            "capacity_multiview_v3": "CAPACITY: 21000",
            "capacity_multiview_v4": "CAPACITY: 30000",
            JUDGE_VIEW_ID: "Z",
        },
    )
    assert no_v1.predictions[0].object_entities == ["20000"]
    assert no_v1.records[0].decisions[-1]["reason"] == "fallback_top_cluster"

    no_values, _ = _run_capacity(
        "Venue E",
        {
            "capacity_multiview_v1": "UNKNOWN",
            "capacity_multiview_v2": "UNKNOWN",
            "capacity_multiview_v3": "INVALID",
            "capacity_multiview_v4": "CAPACITY: -1",
        },
    )
    assert no_values.predictions[0].object_entities == []
    assert no_values.records[0].calls_used == 4
    assert no_values.records[0].decisions[-1]["reason"] == "no_numeric_values"


def test_e2_config_derives_from_e1_with_only_capacity_prediction_diff():
    e1 = _load(E1_PATH)
    e2 = _load(E2_PATH)
    diff = _semantic_diff(e1, e2)
    assert set(diff) == {
        "leaderboard_repair_caps",
        "leaderboard_repair_capacity_multiview_mode",
        "leaderboard_repair_features",
    }

    features_before, features_after = diff["leaderboard_repair_features"]
    feature_delta = {
        key: [features_before[key], features_after[key]]
        for key in features_before
        if features_before[key] != features_after[key]
    }
    assert feature_delta == {"mistral_capacity_multiview": [False, True]}

    caps_before, caps_after = diff["leaderboard_repair_caps"]
    cap_delta = {
        key: [caps_before[key], caps_after[key]]
        for key in caps_before
        if caps_before[key] != caps_after[key]
    }
    assert cap_delta == {CAPACITY: [0, 5]}
    assert diff["leaderboard_repair_capacity_multiview_mode"] == [
        "OFF",
        CAPACITY_MULTIVIEW_MODE,
    ]

    assert e1["experiment"]["frozen_baseline"]["status"] == "PREVIOUS_FROZEN_BASELINE"
    assert e2["experiment"]["frozen_baseline"]["status"] == "FROZEN_CURRENT_BASELINE"
    assert e2["experiment"]["hidden_test_scores"]["all_relations"]["f1"] == 0.5836
    assert e2["experiment"]["hidden_test_scores"][CAPACITY]["f1"] == 0.1633
    assert e2["experiment"]["capacity_probe_history"]["chiv"]["status"] == (
        "RETIRED_NEGATIVE_HIDDEN_TEST_PROBE"
    )
    assert "chiv" not in e2["leaderboard_repair"]


def test_e2_model_portfolio_is_one_mistral_checkpoint_and_no_qwen():
    config = _load(E2_PATH)
    enumerator, verifier = model_blocks(config)
    assert enumerator == verifier
    assert enumerator["model_id"] == MISTRAL_ID
    assert enumerator["revision"] == MISTRAL_REVISION
    assert "Qwen/Qwen3.5-4B" not in json.dumps(config["model_profile"], sort_keys=True)

    audit = audit_parameter_budget([
        spec_from_config(enumerator),
        spec_from_config(verifier),
    ])
    assert audit.passed
    assert audit.total_parameters == MISTRAL_PARAMETERS
    assert audit.budget == PARAMETER_LIMIT
    assert [component.model_id for component in audit.counted_specs] == [MISTRAL_ID]


def test_targeted_capacity_merge_preserves_non_capacity_rows_and_rejects_bad_inputs():
    module = _load_script("merge_targeted_relation_results.py")
    baseline = _baseline_rows()
    targeted = _capacity_result_rows()
    merged, provenance = module.merge_targeted_relation_results(
        baseline,
        targeted,
        relation=CAPACITY,
        expected_targeted_rows=98,
    )
    assert len(merged) == 475
    assert [row["Relation"] for row in merged] == [row["Relation"] for row in baseline]
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
    duplicate[-1] = dict(duplicate[0])
    with pytest.raises(module.TargetedMergeError, match="duplicate identity"):
        module.merge_targeted_relation_results(
            baseline,
            duplicate,
            relation=CAPACITY,
            expected_targeted_rows=98,
        )
    unknown = list(targeted)
    unknown[0] = {
        "SubjectEntity": "Unknown Capacity",
        "Relation": CAPACITY,
        "ObjectEntities": ["1"],
    }
    with pytest.raises(module.TargetedMergeError, match="keys mismatch"):
        module.merge_targeted_relation_results(
            baseline,
            unknown,
            relation=CAPACITY,
            expected_targeted_rows=98,
        )


def test_capacity_targeted_runner_preflight_uses_exact_test_capacity_rows():
    module = _load_script("run_capacity_multiview.py")
    config = _load(E2_PATH)
    model = module.validate_capacity_model_config(config)
    assert model["model_id"] == MISTRAL_ID
    assert model["revision"] == MISTRAL_REVISION

    rows = module.capacity_rows("test")
    assert len(rows) == 98
    assert all(row.relation == CAPACITY for row in rows)
    assert all(row.is_empty for row in rows)


def test_capacity_multiview_repeat_run_is_deterministic_with_mocked_runtime():
    outputs = {
        "capacity_multiview_v1": "CAPACITY: 10000",
        "capacity_multiview_v2": "CAPACITY: 10000",
        "capacity_multiview_v3": "CAPACITY: 10000",
        "capacity_multiview_v4": "UNKNOWN",
    }
    first, _ = _run_capacity("Venue F", outputs)
    second, _ = _run_capacity("Venue F", outputs)
    assert [p.to_official_row() for p in first.predictions] == [
        p.to_official_row() for p in second.predictions
    ]
    assert first.records[0].decisions == second.records[0].decisions


def test_capacity_multiview_feature_name_and_active_config_are_explicit():
    e2 = _load(E2_PATH)
    repair = LeaderboardRepairConfig.from_mapping(e2["leaderboard_repair"])
    assert CAPACITY_MULTIVIEW_FEATURE == "MistralCapacityMultiView"
    assert repair.features.mistral_capacity_multiview is True
    assert repair.features.mistral_city_empty_rescue is True
    assert repair.features.mistral_direct_area is True
    assert repair.features.award_metadata_cleanup is True
    assert repair.features.capacity_repair is False
    assert repair.max_calls_by_relation[CAPACITY] == 5
    assert repair.max_calls_by_relation[AREA] == 1
    assert repair.max_calls_by_relation[CITY] == 2
    assert repair.max_calls_by_relation[AWARD] == 1
    assert repair.max_calls_by_relation[STOCK] == 0
    assert repair.max_calls_by_relation[BORDERS] == 0
