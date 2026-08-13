"""Profile E3 Area Multi-View baseline guards."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Mapping

import pytest
import yaml

from cover_kbc.leaderboard_repair.area_multiview import (
    AREA_MULTIVIEW_FEATURE,
    AREA_MULTIVIEW_MODE,
    DIRECT_VIEW_ID,
    FALLBACK_TOP_CLUSTER,
    FALLBACK_V1_DIRECT_AREA,
    INVALID,
    JUDGE,
    JUDGE_VIEW_ID,
    MULTIVIEW_SUPPORT_GE_3,
    NO_VALUE,
    UNKNOWN,
    VALID_AREA,
    VIEW_IDS,
    AreaObservation,
    area_judge_prompt,
    area_view_prompt,
    cluster_area_values,
    decide_area_multiview,
    parse_area_judge_output,
    parse_area_output,
    top_area_cluster,
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
E2_PATH = CONFIG_DIR / "cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml"
E3_PATH = CONFIG_DIR / "cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml"
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


def _area_config(max_calls: int = 5) -> LeaderboardRepairConfig:
    return LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "direct_area_mode": "DIRECT_ALL",
        "area_multiview_mode": AREA_MULTIVIEW_MODE,
        "capacity_multiview_mode": "DIRECT_ALL",
        "features": {
            "mistral_direct_area": True,
            "mistral_area_multiview": True,
            "mistral_capacity_multiview": True,
            "mistral_city_empty_rescue": True,
            "award_metadata_cleanup": True,
        },
        "max_calls_by_relation": {
            AREA: max_calls,
            CAPACITY: 5,
            CITY: 2,
            AWARD: 1,
            STOCK: 0,
            BORDERS: 0,
        },
    })


def _area_script(subject: str, outputs: Mapping[str, str]) -> dict[tuple[str, str, str], list[str]]:
    return {
        (view_id, subject, AREA): [text]
        for view_id, text in outputs.items()
    }


def _run_area(subject: str, outputs: Mapping[str, str], *, before=None, max_calls: int = 5):
    runtime = CapturingScriptedRuntime(
        _area_script(subject, outputs),
        model_id=MISTRAL_ID,
        role="verifier",
    )
    prediction = _prediction(subject, AREA, before or [], row_index=0)
    result = _stack(_area_config(max_calls=max_calls), runtime).apply(
        [prediction],
        queries=[_query(prediction)],
    )
    return result, runtime


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


def test_area_parser_accepts_strict_numbers_and_unknown():
    assert parse_area_output("AREA: 596").status == VALID_AREA
    assert parse_area_output("AREA: 596").value == "596"
    assert parse_area_output("AREA: 596.0").value == "596"
    assert parse_area_output("AREA: 18.105").value == "18.105"
    assert parse_area_output("AREA: 1,267,000").value == "1267000"
    assert parse_area_output("UNKNOWN").status == UNKNOWN


def test_area_parser_rejects_non_strict_outputs():
    for text in (
        "596",
        "AREA: 596 km2",
        "AREA: 596-600",
        "AREA: 596 or 600",
        "AREA: 596\nAREA: 600",
        "The area is 596.",
        "AREA: NaN",
        "AREA: Infinity",
        "AREA: 0",
        "AREA: -1",
        "AREA: 1,26",
    ):
        assert parse_area_output(text).status == INVALID, text


def test_area_clustering_uses_five_percent_tolerance_and_observed_representative():
    observations = [
        AreaObservation("area_multiview_v1_direct", "100", Decimal("100")),
        AreaObservation("area_multiview_v2_entity_type", "102", Decimal("102")),
        AreaObservation("area_multiview_v3_infobox", "98", Decimal("98")),
        AreaObservation("area_multiview_v4_attribute_contrast", "130", Decimal("130")),
    ]
    clusters = cluster_area_values(observations)
    assert [cluster.support for cluster in clusters] == [3, 1]
    top = top_area_cluster(clusters)
    assert top.representative == "100"
    assert top.representative in top.values

    separated = cluster_area_values([
        AreaObservation("area_multiview_v1_direct", "100", Decimal("100")),
        AreaObservation("area_multiview_v2_entity_type", "106", Decimal("106")),
    ])
    assert [cluster.support for cluster in separated] == [1, 1]

    parsed = [parse_area_output(text) for text in ("AREA: 100", "UNKNOWN", "bad")]
    filtered = [
        AreaObservation(str(index), item.value, item.number)
        for index, item in enumerate(parsed)
        if item.valid
    ]
    assert [item.value for item in filtered] == ["100"]


def test_area_row_gets_four_view_calls_and_strong_support_skips_judge():
    result, runtime = _run_area(
        "Exact Island",
        {
            "area_multiview_v1_direct": "AREA: 100",
            "area_multiview_v2_entity_type": "AREA: 101",
            "area_multiview_v3_infobox": "AREA: 99",
            "area_multiview_v4_attribute_contrast": "AREA: 300",
        },
        before=["999"],
    )
    assert result.predictions[0].object_entities == ["100"]
    assert [request.metadata["view_id"] for request in runtime.requests] == list(VIEW_IDS)
    assert all(request.decode.max_new_tokens == 24 for request in runtime.requests)
    assert result.records[0].calls_used == 4
    assert result.records[0].decisions[-1]["reason"] == MULTIVIEW_SUPPORT_GE_3


def test_area_ambiguity_runs_one_source_blind_judge_and_valid_judge_wins():
    prompt, label_to_value, unknown_label = area_judge_prompt("Exact Lake", ["100", "140"])
    assert "area_multiview_v1" not in prompt
    assert "support" not in prompt.lower()
    assert parse_area_judge_output("B", label_to_value, unknown_label) == (
        VALID_AREA,
        "140",
    )

    result, runtime = _run_area(
        "Exact Lake",
        {
            "area_multiview_v1_direct": "AREA: 100",
            "area_multiview_v2_entity_type": "AREA: 140",
            "area_multiview_v3_infobox": "UNKNOWN",
            "area_multiview_v4_attribute_contrast": "not strict",
            JUDGE_VIEW_ID: "B",
        },
    )
    assert result.predictions[0].object_entities == ["140"]
    assert [request.metadata["view_id"] for request in runtime.requests] == [
        *VIEW_IDS,
        JUDGE_VIEW_ID,
    ]
    assert result.records[0].calls_used == 5
    assert result.records[0].decisions[-1]["reason"] == JUDGE


def test_area_invalid_or_unknown_judge_falls_back_to_v1_direct_area():
    result, _runtime = _run_area(
        "Exact Country",
        {
            "area_multiview_v1_direct": "AREA: 100",
            "area_multiview_v2_entity_type": "AREA: 140",
            "area_multiview_v3_infobox": "UNKNOWN",
            "area_multiview_v4_attribute_contrast": "UNKNOWN",
            JUDGE_VIEW_ID: "C",
        },
    )
    assert result.predictions[0].object_entities == ["100"]
    assert result.records[0].decisions[-1]["reason"] == FALLBACK_V1_DIRECT_AREA


def test_area_absent_v1_falls_back_to_top_cluster_and_no_numeric_outputs_empty():
    result, _runtime = _run_area(
        "Exact Feature",
        {
            "area_multiview_v1_direct": "UNKNOWN",
            "area_multiview_v2_entity_type": "AREA: 200",
            "area_multiview_v3_infobox": "AREA: 300",
            "area_multiview_v4_attribute_contrast": "UNKNOWN",
            JUDGE_VIEW_ID: "Z",
        },
    )
    assert result.predictions[0].object_entities == ["200"]
    assert result.records[0].decisions[-1]["reason"] == FALLBACK_TOP_CLUSTER

    empty, runtime = _run_area(
        "Unknown Feature",
        {
            "area_multiview_v1_direct": "UNKNOWN",
            "area_multiview_v2_entity_type": "UNKNOWN",
            "area_multiview_v3_infobox": "not strict",
            "area_multiview_v4_attribute_contrast": "AREA: 0",
        },
    )
    assert empty.predictions[0].object_entities == []
    assert [request.metadata["view_id"] for request in runtime.requests] == list(VIEW_IDS)
    assert empty.records[0].calls_used == 4
    assert empty.records[0].decisions[-1]["reason"] == NO_VALUE


def test_area_multiview_makes_no_calls_on_non_area_rows():
    runtime = CapturingScriptedRuntime(model_id=MISTRAL_ID, role="verifier")
    prediction = _prediction("Company", STOCK, ["NYSE"], row_index=0)
    result = _stack(_area_config(), runtime).apply(
        [prediction],
        queries=[_query(prediction)],
    )
    assert result.predictions[0].object_entities == ["NYSE"]
    assert runtime.requests == []
    assert result.records[0].calls_used == 0


def test_area_view_prompts_match_the_tested_four_view_method():
    prompts = [area_view_prompt("Exact Subject", view_id) for view_id in VIEW_IDS]
    assert len(set(prompts)) == 4
    assert "Identify the exact entity named by the subject" in prompts[0]
    assert "Silently classify the exact subject" in prompts[1]
    assert "canonical encyclopedic or infobox-style" in prompts[2]
    assert "Reason silently through" in prompts[3]
    for prompt in prompts:
        assert "Subject: Exact Subject" in prompt
        assert "AREA: <number>" in prompt
        assert "UNKNOWN" in prompt
        assert "Do not explain" in prompt or "Do not expose the reasoning" in prompt


def test_decision_helper_documents_fallback_order():
    parsed = {
        DIRECT_VIEW_ID: parse_area_output("AREA: 10"),
        "area_multiview_v2_entity_type": parse_area_output("AREA: 20"),
    }
    obs = [
        AreaObservation(DIRECT_VIEW_ID, "10", Decimal("10")),
        AreaObservation("area_multiview_v2_entity_type", "20", Decimal("20")),
    ]
    clusters = cluster_area_values(obs)
    assert decide_area_multiview(
        observations=obs,
        parsed_by_view=parsed,
        clusters=clusters,
        judge_status=VALID_AREA,
        judge_value="20",
    ).reason == JUDGE
    assert decide_area_multiview(
        observations=obs,
        parsed_by_view=parsed,
        clusters=clusters,
        judge_status=INVALID,
    ).reason == FALLBACK_V1_DIRECT_AREA


def test_profile_e3_diff_from_e2_is_area_only_and_model_portfolio_unchanged():
    e2 = _load(E2_PATH)
    e3 = _load(E3_PATH)
    repair_e2 = LeaderboardRepairConfig.from_mapping(e2["leaderboard_repair"])
    repair_e3 = LeaderboardRepairConfig.from_mapping(e3["leaderboard_repair"])

    flags_e2 = asdict(repair_e2.features)
    flags_e3 = asdict(repair_e3.features)
    feature_delta = {
        key: [flags_e2[key], flags_e3[key]]
        for key in flags_e2
        if flags_e2[key] != flags_e3[key]
    }
    assert feature_delta == {"mistral_area_multiview": [False, True]}

    cap_delta = {
        key: [repair_e2.max_calls_by_relation[key], repair_e3.max_calls_by_relation[key]]
        for key in repair_e2.max_calls_by_relation
        if repair_e2.max_calls_by_relation[key] != repair_e3.max_calls_by_relation[key]
    }
    assert cap_delta == {AREA: [1, 5]}
    assert repair_e2.area_multiview_mode == "OFF"
    assert repair_e3.area_multiview_mode == AREA_MULTIVIEW_MODE
    assert repair_e2.direct_area_mode == repair_e3.direct_area_mode == "DIRECT_ALL"
    assert repair_e2.capacity_multiview_mode == repair_e3.capacity_multiview_mode == "DIRECT_ALL"

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
        assert e2[section] == e3[section]

    enumerator, verifier = model_blocks(e3)
    assert enumerator == verifier
    assert enumerator["model_id"] == MISTRAL_ID
    assert enumerator["revision"] == MISTRAL_REVISION
    assert "Qwen/Qwen3.5-4B" not in json.dumps(e3["model_profile"], sort_keys=True)
    audit = audit_parameter_budget([spec_from_config(enumerator), spec_from_config(verifier)])
    assert audit.passed
    assert audit.total_parameters == MISTRAL_PARAMETERS
    assert audit.budget == PARAMETER_LIMIT
    assert [component.model_id for component in audit.counted_specs] == [MISTRAL_ID]


def test_profile_e3_hidden_scores_are_user_provided_previous_baseline_metadata():
    e3 = _load(E3_PATH)
    assert e3["experiment"]["frozen_baseline"]["status"] == "PREVIOUS_FROZEN_BASELINE"
    assert e3["experiment"]["frozen_baseline"]["superseded_by"] == (
        "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test"
    )
    assert e3["experiment"]["frozen_baseline"]["hidden_test_overall_f1"] == 0.5857
    assert e3["experiment"]["hidden_test_scores"][AREA]["f1"] == 0.6700
    assert e3["experiment"]["hidden_test_scores"][CAPACITY]["f1"] == 0.1633
    assert e3["experiment"]["score_deltas_vs_profile_e2"] == {
        "all_relations": 0.0021,
        "hasCapacity": 0.0000,
        "hasArea": 0.0100,
        "personHasCityOfDeath": 0.0000,
        "awardWonBy": 0.0000,
        "companyTradesAtStockExchange": 0.0000,
        "countryLandBordersCountry": 0.0000,
    }
    assert e3["experiment"]["frozen_baseline"]["artifact_status"] == (
        "WINNING_PROFILE_E3_ARTIFACT_NOT_AVAILABLE_LOCALLY"
    )


def test_area_multiview_targeted_merge_invariants_fail_closed():
    module = _script_module("merge_targeted_relation_results.py")
    baseline = []
    baseline.extend({"SubjectEntity": f"Award {i}", "Relation": AWARD, "ObjectEntities": ["X"]} for i in range(10))
    baseline.extend({"SubjectEntity": f"Stock {i}", "Relation": STOCK, "ObjectEntities": ["Exchange"]} for i in range(100))
    baseline.extend({"SubjectEntity": f"Border {i}", "Relation": BORDERS, "ObjectEntities": ["Neighbor"]} for i in range(67))
    baseline.extend({"SubjectEntity": f"Area {i}", "Relation": AREA, "ObjectEntities": [str(i)]} for i in range(100))
    baseline.extend({"SubjectEntity": f"Capacity {i}", "Relation": CAPACITY, "ObjectEntities": ["10000"]} for i in range(98))
    baseline.extend({"SubjectEntity": f"City {i}", "Relation": CITY, "ObjectEntities": []} for i in range(100))
    assert len(baseline) == 475
    area_results = [
        {"SubjectEntity": f"Area {i}", "Relation": AREA, "ObjectEntities": [str(1000 + i)]}
        for i in range(100)
    ]

    merged, provenance = module.merge_targeted_relation_results(
        baseline,
        area_results,
        relation=AREA,
        expected_targeted_rows=100,
    )
    assert len(merged) == 475
    assert [row["SubjectEntity"] for row in merged] == [row["SubjectEntity"] for row in baseline]
    assert provenance["changed_relation_set"] == [AREA]
    assert provenance["non_target_rows_preserved"] == 375
    for before, after in zip(baseline, merged):
        if before["Relation"] != AREA:
            assert before == after

    with pytest.raises(module.TargetedMergeError):
        module.merge_targeted_relation_results(
            baseline,
            area_results[:-1],
            relation=AREA,
            expected_targeted_rows=100,
        )
    duplicate = list(area_results)
    duplicate[-1] = dict(area_results[0])
    with pytest.raises(module.TargetedMergeError):
        module.merge_targeted_relation_results(
            baseline,
            duplicate,
            relation=AREA,
            expected_targeted_rows=100,
        )
    extra = list(area_results)
    extra[-1] = {"SubjectEntity": "Extra Area", "Relation": AREA, "ObjectEntities": ["1"]}
    with pytest.raises(module.TargetedMergeError):
        module.merge_targeted_relation_results(
            baseline,
            extra,
            relation=AREA,
            expected_targeted_rows=100,
        )


def test_area_multiview_runner_dry_run_validates_profile_without_model_load(tmp_path):
    module = _script_module("run_area_multiview.py")
    paths = module.dry_run(config=_load(E3_PATH), split="test", output_dir=tmp_path)
    payload = json.loads(paths["dry_run"].read_text(encoding="utf-8"))
    assert payload["eligible_hasArea_rows"] == 100
    assert payload["feature"] == AREA_MULTIVIEW_FEATURE
    assert payload["mode"] == AREA_MULTIVIEW_MODE
    assert payload["max_calls_per_hasArea_row"] == 5
    assert payload["model_id"] == MISTRAL_ID
