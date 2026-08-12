"""Integrated Profile E1 Direct Area baseline guards."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
)
from cover_kbc.data.loader import load_dataset
from cover_kbc.leaderboard_repair.area import (
    DIRECT_AREA_FEATURE,
    DIRECT_AREA_MODE,
    DIRECT_AREA_SYSTEM_PROMPT,
    INVALID,
    UNKNOWN,
    VALID_AREA,
    direct_area_prompt,
    parse_direct_area_output,
)
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack
from cover_kbc.leaderboard_repair.util import AREA, BORDERS, CAPACITY, CITY, STOCK
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.models.registry import model_blocks, spec_from_config
from cover_kbc.types import Prediction, Query


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
OLD_E1_PATH = CONFIG_DIR / "cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml"
INTEGRATED_E1_PATH = (
    CONFIG_DIR / "cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml"
)
MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
MISTRAL_PARAMETERS = 24_011_361_280
PARAMETER_LIMIT = 32_000_000_000
TEST_SHA256 = "67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1"
INTEGRATED_E1_PREDICTION_SHA256 = (
    "67bd1bc8af01de177520d93f9b5b9fc30839d56f36ceeeb6263813662e52d8a6"
)


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


class CapturingScriptedRuntime(ScriptedRuntime):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


def _prediction_affecting_sections(config: dict) -> dict:
    repair = LeaderboardRepairConfig.from_mapping(config["leaderboard_repair"])
    return {
        "pipeline": config["pipeline"],
        "leaderboard_repair_enabled": repair.enabled,
        "leaderboard_repair_features": asdict(repair.features),
        "leaderboard_repair_caps": dict(repair.max_calls_by_relation),
        "leaderboard_repair_direct_area_mode": repair.direct_area_mode,
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
    rows = [
        {"SubjectEntity": f"Area {i}", "Relation": AREA, "ObjectEntities": []}
        for i in range(100)
    ]
    rows.extend(
        {"SubjectEntity": f"City {i}", "Relation": CITY, "ObjectEntities": ["Existing City"]}
        for i in range(100)
    )
    rows.extend(
        {"SubjectEntity": f"Stock {i}", "Relation": STOCK, "ObjectEntities": ["Exchange"]}
        for i in range(100)
    )
    rows.extend(
        {"SubjectEntity": f"Border {i}", "Relation": BORDERS, "ObjectEntities": ["Neighbor"]}
        for i in range(100)
    )
    rows.extend(
        {"SubjectEntity": f"Capacity {i}", "Relation": CAPACITY, "ObjectEntities": ["10000"]}
        for i in range(75)
    )
    return rows


def _area_result_rows() -> list[dict]:
    return [
        {
            "SubjectEntity": f"Area {i}",
            "Relation": AREA,
            "ObjectEntities": [str(100 + i)],
            "direct_parse_status": VALID_AREA,
            "direct_value": str(100 + i),
            "direct_raw_output": f"AREA: {100 + i}",
        }
        for i in range(100)
    ]


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_integrated_e1_config_diff_from_city_only_e1_is_only_direct_area():
    old_e1 = _load(OLD_E1_PATH)
    integrated_e1 = _load(INTEGRATED_E1_PATH)
    diff = _semantic_diff(old_e1, integrated_e1)
    assert set(diff) == {
        "leaderboard_repair_caps",
        "leaderboard_repair_direct_area_mode",
        "leaderboard_repair_features",
    }

    feature_before, feature_after = diff["leaderboard_repair_features"]
    feature_delta = {
        key: [feature_before[key], feature_after[key]]
        for key in feature_before
        if feature_before[key] != feature_after[key]
    }
    assert feature_delta == {"mistral_direct_area": [False, True]}

    caps_before, caps_after = diff["leaderboard_repair_caps"]
    cap_delta = {
        key: [caps_before[key], caps_after[key]]
        for key in caps_before
        if caps_before[key] != caps_after[key]
    }
    assert cap_delta == {AREA: [0, 1]}
    assert diff["leaderboard_repair_direct_area_mode"] == ["OFF", DIRECT_AREA_MODE]

    assert old_e1["experiment"]["profile_e1_probe"]["status"] == (
        "HISTORICAL_SUPERSEDED_CITY_ONLY_E1"
    )
    assert integrated_e1["experiment"]["frozen_baseline"]["status"] == (
        "FROZEN_CURRENT_BASELINE"
    )
    assert integrated_e1["experiment"]["frozen_baseline"]["prediction_sha256"] == (
        INTEGRATED_E1_PREDICTION_SHA256
    )
    assert integrated_e1["experiment"]["hidden_test_scores"]["all_relations"] == {
        "precision": 0.7563,
        "recall": 0.5929,
        "f1": 0.5752,
    }
    assert integrated_e1["experiment"]["integrated_e1_baseline"][
        "profile_e2_reserved_for_future"
    ] is True


def test_integrated_e1_model_portfolio_remains_one_mistral_checkpoint():
    config = _load(INTEGRATED_E1_PATH)
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


def test_integrated_e1_official_test_integrity_and_probe_readiness():
    config = _load(INTEGRATED_E1_PATH)
    dataset = load_dataset("test")
    assert config["experiment"]["split"] == "test"
    assert len(dataset) == 475
    assert dataset.sha256 == TEST_SHA256
    assert all(row.is_empty for row in dataset)
    assert config["test_dataset"]["rows"] == 475
    assert config["test_dataset"]["sha256"] == TEST_SHA256

    readiness = evaluate_test_readiness(config, base_dir=CONFIG_DIR, split="test")
    assert readiness.state is ReadinessState.NOT_READY
    assert any("CALIBRATION_REVIEW_REQUIRED" in blocker for blocker in readiness.blockers)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location(
            "run_cover_integrated_e1_probe_test", REPO_ROOT / "scripts" / "run_cover.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        assert module._allow_leaderboard_probe(config, "test", readiness)
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


def test_direct_area_prompt_matches_successful_standalone_semantics():
    assert DIRECT_AREA_SYSTEM_PROMPT == (
        "You are a precise factual knowledge-base completion assistant.\n\n"
        "Use only factual knowledge encoded in the model.\n"
        "Follow the requested output format exactly.\n"
        "If you are not sufficiently confident, return UNKNOWN.\n"
        "Do not explain your answer."
    )
    assert direct_area_prompt("Example Island") == (
        "Subject: Example Island\n\n"
        "Relation: hasArea\n\n"
        "Question:\n"
        "What is the canonical surface area of this exact named geographic entity in\n"
        "square kilometres?\n\n"
        "Identify the exact entity named by the subject before answering.\n\n"
        "For an island:\n"
        "return the land area of that exact island itself.\n\n"
        "For a lake:\n"
        "return the surface area of that exact lake.\n\n"
        "For a country:\n"
        "return its total area, including land and inland water.\n\n"
        "Do NOT return the area of:\n"
        "- a containing country\n"
        "- a state, province, county, municipality, or administrative region\n"
        "- an archipelago or island group unless the subject itself is that group\n"
        "- only one part of the named island\n"
        "- a drainage basin or catchment\n"
        "- a lagoon unless the subject itself is the lagoon\n"
        "- a protected area\n"
        "- a nearby geographic feature\n\n"
        "If you know the area in square miles or hectares, convert it to square\n"
        "kilometres before answering.\n\n"
        "Return exactly one of:\n\n"
        "AREA: <number>\n\n"
        "or:\n\n"
        "UNKNOWN\n\n"
        "<number> must be a single positive decimal number in km^2.\n\n"
        "Do not include units after the number.\n"
        "Do not return a range.\n"
        "Do not give multiple candidate values.\n"
        "Do not explain your answer."
    )


def test_direct_area_parser_accepts_only_strict_positive_single_line_area_or_unknown():
    for raw, value in (
        ("AREA: 114", "114"),
        ("AREA: 114.0", "114.0"),
        ("AREA: 1.32", "1.32"),
        ("AREA: 1267000", "1267000"),
    ):
        parsed = parse_direct_area_output(raw)
        assert parsed.status == VALID_AREA
        assert parsed.value == value
        assert parsed.valid

    assert parse_direct_area_output("UNKNOWN").status == UNKNOWN
    for raw in (
        "AREA: 114 km2",
        "approximately 114",
        "114",
        "AREA: 100-120",
        "AREA: 114 or 116",
        "AREA:",
        "AREA: NaN",
        "AREA: Infinity",
        "AREA: -1",
        "AREA: 0",
        "AREA: 114\nbecause",
    ):
        assert parse_direct_area_output(raw).status == INVALID


def test_direct_area_applies_to_every_area_row_and_bypasses_non_area_relations():
    predictions = [
        _prediction("Exact Island", AREA, ["100"], row_index=0),
        _prediction("Unknown Island", AREA, ["200"], row_index=1),
        _prediction("Existing City Person", CITY, ["Old City"], row_index=2),
        _prediction("Stock Row", STOCK, ["Exchange"], row_index=3),
    ]
    runtime = CapturingScriptedRuntime(
        {
            ("mistral_direct_area", "Exact Island", AREA): ["AREA: 105"],
            ("mistral_direct_area", "Unknown Island", AREA): ["UNKNOWN"],
        },
        model_id=MISTRAL_ID,
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "direct_area_mode": DIRECT_AREA_MODE,
        "features": {
            "mistral_direct_area": True,
            "mistral_city_empty_rescue": True,
        },
        "max_calls_by_relation": {AREA: 1, CITY: 2, STOCK: 0},
    })
    result = _stack(config, runtime).apply(
        predictions,
        queries=[_query(prediction) for prediction in predictions],
    )
    assert [prediction.object_entities for prediction in result.predictions] == [
        ["105"],
        [],
        ["Old City"],
        ["Exchange"],
    ]
    assert [record.calls_used for record in result.records] == [1, 1, 0, 0]
    assert [request.system_prompt for request in runtime.requests] == [
        DIRECT_AREA_SYSTEM_PROMPT,
        DIRECT_AREA_SYSTEM_PROMPT,
    ]
    assert [request.decode.temperature for request in runtime.requests] == [0.0, 0.0]
    assert [request.decode.top_p for request in runtime.requests] == [1.0, 1.0]
    assert [request.decode.max_new_tokens for request in runtime.requests] == [24, 24]
    assert runtime.requests[0].prompt == direct_area_prompt("Exact Island")
    assert {call.model_id for record in result.records for call in record.calls} == {MISTRAL_ID}
    assert result.accounting["by_relation"][AREA]["direct_area"] == {
        "eligible_hasArea_rows": 2,
        "direct_area_calls": 2,
        "valid_area_count": 1,
        "unknown_count": 1,
        "invalid_count": 0,
        "error_count": 0,
        "changed_rows": 2,
    }
    assert result.records[0].features == [DIRECT_AREA_FEATURE]


def test_direct_area_result_row_schema_and_no_subject_lookup():
    runner = _load_script("run_direct_area.py")
    row = runner.make_direct_result_row(
        subject="Synthetic Island",
        row_index=4,
        raw_output="AREA: 12.5",
        model_id=MISTRAL_ID,
        prompt_tokens=10,
        generated_tokens=2,
    )
    assert row["SubjectEntity"] == "Synthetic Island"
    assert row["Relation"] == AREA
    assert row["ObjectEntities"] == ["12.5"]
    assert row["direct_parse_status"] == VALID_AREA
    assert row["model_id"] == MISTRAL_ID
    source = (REPO_ROOT / "scripts" / "run_direct_area.py").read_text(encoding="utf-8")
    for forbidden in ("answer_lookup", "subject_to_answer"):
        assert forbidden not in source


def test_targeted_direct_area_merge_preserves_non_area_rows_and_order():
    merge_tool = _load_script("merge_direct_area_results.py")
    baseline = _baseline_rows()
    merged, provenance = merge_tool.merge_area_results(baseline, _area_result_rows())
    assert len(merged) == 475
    assert [merge_tool.identity(row) for row in merged] == [
        merge_tool.identity(row) for row in baseline
    ]
    assert merged[0]["ObjectEntities"] == ["100"]
    assert provenance["direct_area_mode"] == DIRECT_AREA_MODE
    assert provenance["changed_relation_set"] == [AREA]
    assert provenance["changed_rows_by_relation"] == {AREA: 100}
    for before, after in zip(baseline[100:], merged[100:]):
        assert after == before
    assert all(
        row["Relation"] != CITY or row["ObjectEntities"] == ["Existing City"]
        for row in merged
    )


def test_targeted_direct_area_merge_unknown_invalid_and_rejection_paths():
    merge_tool = _load_script("merge_direct_area_results.py")
    baseline = _baseline_rows()
    baseline[0]["ObjectEntities"] = ["50"]
    baseline[1]["ObjectEntities"] = ["60"]
    area = _area_result_rows()
    area[0] = dict(area[0], ObjectEntities=[], direct_parse_status=UNKNOWN, direct_value="")
    area[1] = dict(area[1], ObjectEntities=[], direct_parse_status=INVALID, direct_value="")
    merged, provenance = merge_tool.merge_area_results(baseline, area)
    assert merged[0]["ObjectEntities"] == []
    assert merged[1]["ObjectEntities"] == []
    assert provenance["changed_rows"] == 100

    with pytest.raises(merge_tool.AreaMergeError, match="99"):
        merge_tool.merge_area_results(baseline, area[:99])
    with pytest.raises(merge_tool.AreaMergeError, match="duplicate identity"):
        merge_tool.merge_area_results(baseline, area[:-1] + [area[0]])
    unknown = list(area)
    unknown[0] = dict(unknown[0], SubjectEntity="Not In Baseline")
    with pytest.raises(merge_tool.AreaMergeError, match="mismatch"):
        merge_tool.merge_area_results(baseline, unknown)


def test_integrated_e1_no_retired_c2_area_or_qwen_runtime_is_reintroduced():
    config = _load(INTEGRATED_E1_PATH)
    serialized = json.dumps(config, sort_keys=True)
    assert "Qwen/Qwen3.5-4B" not in serialized
    repair = LeaderboardRepairConfig.from_mapping(config["leaderboard_repair"])
    flags = asdict(repair.features)
    assert flags["mistral_direct_area"] is True
    assert flags["mistral_city_empty_rescue"] is True
    assert flags["award_metadata_cleanup"] is True
    assert flags["area_empty_rescue"] is False
    assert flags["capacity_repair"] is False
    assert flags["death_existence_gate"] is False
    assert flags["death_city_recall"] is False
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "src" / "cover_kbc" / "leaderboard_repair").glob("*.py")
    )
    for forbidden in (
        "AreaEmptyRescue",
        "NumericAttributeResolver",
        "BorderDirectionalSweep",
        "AwardRecipientWitness",
        "AwardTimeSlicedRecall",
    ):
        assert forbidden not in source
