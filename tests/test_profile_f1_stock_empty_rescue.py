"""Profile F1 Stock Empty Rescue baseline guards."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

from cover_kbc.data.loader import load_dataset
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack
from cover_kbc.leaderboard_repair.stock_empty_rescue import (
    INVALID,
    REJECTED,
    STOCK_EMPTY_RESCUE_FEATURE,
    STOCK_EMPTY_RESCUE_MODE,
    STRONG_CONSENSUS,
    UNKNOWN,
    VALID_EXCHANGE_SET,
    VIEW_IDS,
    cluster_stock_exchange_outputs,
    decide_stock_empty_rescue,
    parse_stock_exchange_output,
    stock_exchange_view_prompt,
)
from cover_kbc.leaderboard_repair.util import AREA, AWARD, BORDERS, CAPACITY, CITY, STOCK
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.base import GenerationRequest
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.models.registry import model_blocks, spec_from_config
from cover_kbc.types import Prediction, Query


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
E3_PATH = CONFIG_DIR / "cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml"
F1_PATH = CONFIG_DIR / "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml"
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


def _stock_config(max_calls: int = 4) -> LeaderboardRepairConfig:
    return LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "stock_empty_rescue_mode": STOCK_EMPTY_RESCUE_MODE,
        "stock_empty_rescue_min_support": 3,
        "features": {"mistral_stock_empty_rescue": True},
        "max_calls_by_relation": {
            STOCK: max_calls,
            AREA: 0,
            CITY: 0,
            AWARD: 0,
            CAPACITY: 0,
            BORDERS: 0,
        },
    })


def _stock_script(subject: str, outputs: dict[str, str]) -> dict[tuple[str, str, str], list[str]]:
    return {
        (view_id, subject, STOCK): [text]
        for view_id, text in outputs.items()
    }


def _run_stock(subject: str, outputs: dict[str, str], *, before=None, max_calls: int = 4):
    runtime = CapturingScriptedRuntime(
        _stock_script(subject, outputs),
        model_id=MISTRAL_ID,
        role="verifier",
    )
    prediction = _prediction(subject, STOCK, before or [], row_index=0)
    result = _stack(_stock_config(max_calls=max_calls), runtime).apply(
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
        "leaderboard_repair_area_multiview_mode": repair.area_multiview_mode,
        "leaderboard_repair_capacity_multiview_mode": repair.capacity_multiview_mode,
        "leaderboard_repair_stock_empty_rescue_mode": repair.stock_empty_rescue_mode,
        "leaderboard_repair_stock_empty_rescue_min_support": (
            repair.stock_empty_rescue_min_support
        ),
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


def test_stock_parser_accepts_strict_exchange_lines_and_unknown():
    parsed = parse_stock_exchange_output("EXCHANGE: New York Stock Exchange")
    assert parsed.status == VALID_EXCHANGE_SET
    assert parsed.values == ("New York Stock Exchange",)

    parsed = parse_stock_exchange_output(
        "EXCHANGE: London Stock Exchange\nEXCHANGE: LSE"
    )
    assert parsed.status == VALID_EXCHANGE_SET
    assert parsed.values == ("London Stock Exchange",)

    assert parse_stock_exchange_output("UNKNOWN").status == UNKNOWN


def test_stock_parser_rejects_non_strict_outputs():
    for text in (
        "New York Stock Exchange",
        "EXCHANGE: AAPL",
        "EXCHANGE: Apple Inc.",
        "EXCHANGE: NYSE\nbecause it is listed there",
        "EXCHANGE: NYSE\nEXCHANGE: NASDAQ\nEXCHANGE: LSE\nEXCHANGE: TSX",
        "MARKET: New York Stock Exchange",
        "EXCHANGE:",
        "The exchange is NASDAQ.",
    ):
        assert parse_stock_exchange_output(text).status == INVALID, text


def test_stock_prompts_are_four_candidate_blind_views():
    prompts = [stock_exchange_view_prompt("Exact Company", view_id) for view_id in VIEW_IDS]
    assert len(set(prompts)) == 4
    assert "Which stock exchange" in prompts[0]
    assert "primary public stock exchange listing venue" in prompts[1]
    assert "ordinary/common shares" in prompts[2]
    assert "plausible market prior" in prompts[3]
    for prompt in prompts:
        assert "Subject: Exact Company" in prompt
        assert "EXCHANGE: <exchange name>" in prompt
        assert "UNKNOWN" in prompt


def test_stock_decision_accepts_only_strong_cross_view_consensus():
    parsed = {
        VIEW_IDS[0]: parse_stock_exchange_output("EXCHANGE: NYSE"),
        VIEW_IDS[1]: parse_stock_exchange_output("EXCHANGE: New York Stock Exchange"),
        VIEW_IDS[2]: parse_stock_exchange_output("EXCHANGE: New York Stock Exchange"),
        VIEW_IDS[3]: parse_stock_exchange_output("UNKNOWN"),
    }
    decision = decide_stock_empty_rescue(parsed)
    assert decision.values == ("New York Stock Exchange",)
    assert decision.reason == STRONG_CONSENSUS
    assert decision.clusters[0].support == 3

    weak = {
        VIEW_IDS[0]: parse_stock_exchange_output("EXCHANGE: NASDAQ"),
        VIEW_IDS[1]: parse_stock_exchange_output("EXCHANGE: NASDAQ"),
        VIEW_IDS[2]: parse_stock_exchange_output("UNKNOWN"),
        VIEW_IDS[3]: parse_stock_exchange_output("EXCHANGE: New York Stock Exchange"),
    }
    decision = decide_stock_empty_rescue(weak)
    assert decision.values == ()
    assert decision.reason == REJECTED

    clusters = cluster_stock_exchange_outputs(parsed)
    assert clusters[0].representative in clusters[0].surfaces


def test_stock_empty_rescue_fills_only_empty_rows_with_three_of_four_support():
    result, runtime = _run_stock(
        "Exact Listed Company",
        {
            VIEW_IDS[0]: "EXCHANGE: NYSE",
            VIEW_IDS[1]: "EXCHANGE: New York Stock Exchange",
            VIEW_IDS[2]: "EXCHANGE: New York Stock Exchange",
            VIEW_IDS[3]: "UNKNOWN",
        },
    )
    assert result.predictions[0].object_entities == ["New York Stock Exchange"]
    assert [request.metadata["view_id"] for request in runtime.requests] == list(VIEW_IDS)
    assert all(request.decode.temperature == 0.0 for request in runtime.requests)
    assert all(request.decode.max_new_tokens == 96 for request in runtime.requests)
    assert result.records[0].calls_used == 4
    assert result.records[0].decisions[-1]["reason"] == STRONG_CONSENSUS
    summary = result.accounting["by_relation"][STOCK]["stock_empty_rescue"]
    assert summary["eligible_empty_rows"] == 1
    assert summary["accepted_count"] == 1
    assert summary["changed_rows"] == 1


def test_stock_empty_rescue_keeps_non_empty_and_rejects_weak_empty_rows():
    non_empty, runtime = _run_stock(
        "Already Listed",
        {},
        before=["NASDAQ"],
    )
    assert non_empty.predictions[0].object_entities == ["NASDAQ"]
    assert runtime.requests == []
    assert non_empty.records[0].calls_used == 0

    weak, runtime = _run_stock(
        "Weak Company",
        {
            VIEW_IDS[0]: "EXCHANGE: NASDAQ",
            VIEW_IDS[1]: "EXCHANGE: NASDAQ",
            VIEW_IDS[2]: "UNKNOWN",
            VIEW_IDS[3]: "UNKNOWN",
        },
    )
    assert weak.predictions[0].object_entities == []
    assert [request.metadata["view_id"] for request in runtime.requests] == list(VIEW_IDS)
    assert weak.records[0].calls_used == 4
    assert weak.records[0].decisions[-1]["reason"] == REJECTED


def test_stock_empty_rescue_makes_no_calls_on_non_stock_rows():
    runtime = CapturingScriptedRuntime(model_id=MISTRAL_ID, role="verifier")
    prediction = _prediction("Exact Venue", CAPACITY, ["10000"], row_index=0)
    result = _stack(_stock_config(), runtime).apply(
        [prediction],
        queries=[_query(prediction)],
    )
    assert result.predictions[0].object_entities == ["10000"]
    assert runtime.requests == []
    assert result.records[0].calls_used == 0


def test_profile_f1_diff_from_e3_is_stock_empty_rescue_only():
    e3 = _load(E3_PATH)
    f1 = _load(F1_PATH)
    before = _prediction_affecting_sections(e3)
    after = _prediction_affecting_sections(f1)
    diff = {
        key: [before[key], after[key]]
        for key in sorted(before)
        if before[key] != after[key]
    }

    flags_before = before["leaderboard_repair_features"]
    flags_after = after["leaderboard_repair_features"]
    feature_delta = {
        key: [flags_before[key], flags_after[key]]
        for key in flags_before
        if flags_before[key] != flags_after[key]
    }
    caps_before = before["leaderboard_repair_caps"]
    caps_after = after["leaderboard_repair_caps"]
    cap_delta = {
        key: [caps_before[key], caps_after[key]]
        for key in caps_before
        if caps_before[key] != caps_after[key]
    }

    assert set(diff) == {
        "leaderboard_repair_caps",
        "leaderboard_repair_features",
        "leaderboard_repair_stock_empty_rescue_mode",
    }
    assert feature_delta == {"mistral_stock_empty_rescue": [False, True]}
    assert cap_delta == {STOCK: [0, 4]}
    assert before["leaderboard_repair_stock_empty_rescue_mode"] == "OFF"
    assert after["leaderboard_repair_stock_empty_rescue_mode"] == (
        STOCK_EMPTY_RESCUE_MODE
    )

    enumerator, verifier = model_blocks(f1)
    assert enumerator == verifier
    assert enumerator["model_id"] == MISTRAL_ID
    assert enumerator["revision"] == MISTRAL_REVISION
    assert "Qwen/Qwen3.5-4B" not in json.dumps(f1["model_profile"], sort_keys=True)
    audit = audit_parameter_budget([spec_from_config(enumerator), spec_from_config(verifier)])
    assert audit.passed
    assert audit.total_parameters == MISTRAL_PARAMETERS
    assert audit.budget == PARAMETER_LIMIT
    assert [component.model_id for component in audit.counted_specs] == [MISTRAL_ID]


def test_profile_f1_hidden_scores_are_user_provided_current_baseline_metadata():
    e3 = _load(E3_PATH)
    f1 = _load(F1_PATH)
    assert e3["experiment"]["frozen_baseline"]["status"] == "PREVIOUS_FROZEN_BASELINE"
    assert e3["experiment"]["frozen_baseline"]["superseded_by"] == (
        "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test"
    )
    assert f1["experiment"]["frozen_baseline"]["status"] == "FROZEN_CURRENT_BASELINE"
    assert f1["experiment"]["frozen_baseline"]["hidden_test_overall_f1"] == 0.5878
    assert f1["experiment"]["hidden_test_scores"][STOCK]["f1"] == 0.7385
    assert f1["experiment"]["hidden_test_scores"]["all_relations"]["f1"] == 0.5878
    assert f1["experiment"]["score_deltas_vs_profile_e3"] == {
        "all_relations": 0.0021,
        "hasCapacity": 0.0000,
        "hasArea": 0.0000,
        "personHasCityOfDeath": 0.0000,
        "awardWonBy": 0.0000,
        "companyTradesAtStockExchange": 0.0100,
        "countryLandBordersCountry": 0.0000,
    }


def test_stock_empty_rescue_runner_dry_run_validates_profile_without_model_load(tmp_path):
    module = _script_module("run_stock_empty_rescue.py")
    baseline_path = tmp_path / "profile_e3_baseline.jsonl"
    rows = [row.to_official_row() for row in load_dataset("test")]
    with baseline_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    paths = module.dry_run(
        config=_load(F1_PATH),
        baseline_predictions=baseline_path,
        split="test",
        output_dir=tmp_path,
    )
    payload = json.loads(paths["dry_run"].read_text(encoding="utf-8"))
    assert payload["total_stock_rows"] == 100
    assert payload["empty_stock_rows_to_repair"] == 100
    assert payload["feature"] == STOCK_EMPTY_RESCUE_FEATURE
    assert payload["mode"] == STOCK_EMPTY_RESCUE_MODE
    assert payload["max_calls_per_empty_stock_row"] == 4
    assert payload["expected_stock_empty_rescue_calls"] == 400
    assert payload["model_id"] == MISTRAL_ID
