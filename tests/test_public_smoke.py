"""Public smoke tests for the frozen COVER-KBC F1 release."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from benchmark.evaluate import evaluate_per_sr_pair, RELATION_TYPE
from cover_kbc.leaderboard_repair.stock_empty_rescue import (
    STRONG_CONSENSUS,
    VIEW_IDS,
    decide_stock_empty_rescue,
    parse_stock_exchange_output,
)
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.registry import model_blocks, spec_from_config


ROOT = Path(__file__).resolve().parents[1]
F1_CONFIG = ROOT / "configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml"
MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
MISTRAL_PARAMETERS = 24_011_361_280
PARAMETER_LIMIT = 32_000_000_000


def _load_config() -> dict:
    return yaml.safe_load(F1_CONFIG.read_text(encoding="utf-8")) or {}


def test_f1_config_uses_one_mistral_checkpoint_under_budget():
    config = _load_config()
    enumerator, verifier = model_blocks(config)
    assert enumerator["model_id"] == MISTRAL_ID
    assert enumerator["revision"] == MISTRAL_REVISION
    assert verifier["model_id"] == MISTRAL_ID
    assert verifier["revision"] == MISTRAL_REVISION
    assert "Qwen" not in json.dumps(config.get("model_profile", {}), sort_keys=True)

    report = audit_parameter_budget([spec_from_config(enumerator)], budget=PARAMETER_LIMIT)
    assert report.passed
    assert report.total_parameters == MISTRAL_PARAMETERS


def test_current_config_records_profile_f1_hidden_score():
    config = _load_config()
    experiment = config["experiment"]
    assert experiment["name"] == "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test"
    assert experiment["frozen_baseline"]["status"] == "FROZEN_CURRENT_BASELINE"
    assert experiment["hidden_test_scores"]["all_relations"]["f1"] == 0.5878
    assert experiment["hidden_test_scores"]["companyTradesAtStockExchange"]["f1"] == 0.7385


def test_stock_empty_rescue_requires_three_view_exchange_support():
    parsed = {
        VIEW_IDS[0]: parse_stock_exchange_output("EXCHANGE: NYSE"),
        VIEW_IDS[1]: parse_stock_exchange_output("EXCHANGE: New York Stock Exchange"),
        VIEW_IDS[2]: parse_stock_exchange_output("EXCHANGE: New York Stock Exchange"),
        VIEW_IDS[3]: parse_stock_exchange_output("UNKNOWN"),
    }
    decision = decide_stock_empty_rescue(parsed)
    assert decision.reason == STRONG_CONSENSUS
    assert decision.values == ("New York Stock Exchange",)
    assert decision.clusters[0].support == 3


def test_official_evaluator_numeric_tolerance_and_alias_matching():
    gold_rows = [
        {
            "SubjectEntity": "Synthetic Island",
            "Relation": "hasArea",
            "ObjectEntities": [["1000"]],
        },
        {
            "SubjectEntity": "Synthetic Company",
            "Relation": "companyTradesAtStockExchange",
            "ObjectEntities": [["New York Stock Exchange", "NYSE"]],
        },
    ]
    pred_rows = [
        {
            "SubjectEntity": "Synthetic Island",
            "Relation": "hasArea",
            "ObjectEntities": ["1049"],
        },
        {
            "SubjectEntity": "Synthetic Company",
            "Relation": "companyTradesAtStockExchange",
            "ObjectEntities": ["NYSE"],
        },
    ]
    scores = evaluate_per_sr_pair(pred_rows, gold_rows, RELATION_TYPE, tolerance=0.05)
    assert [row["f1"] for row in scores] == [1.0, 1.0]
