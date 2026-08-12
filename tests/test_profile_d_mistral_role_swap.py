"""Profile D Mistral-only role-swap config guards.

All checks are CPU-only.  They validate the exact leaderboard-probe question
without loading model weights or reading TEST gold.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
)
from cover_kbc.data.loader import load_dataset
from cover_kbc.models.base import LabelScoreRequest
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.models.registry import model_blocks, spec_from_config


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
A_PLUS_PATH = CONFIG_DIR / "cover_kbc_v3_2_profile_a_plus_award_test.yaml"
C2_PATH = CONFIG_DIR / "cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml"
D_PATH = CONFIG_DIR / "cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml"

MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
QWEN_ID = "Qwen/Qwen3.5-4B"
QWEN_REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
MISTRAL_PARAMETERS = 24_011_361_280
DUAL_PARAMETERS = 28_671_226_368
PARAMETER_LIMIT = 32_000_000_000
TEST_SHA256 = "67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1"
A_PLUS_PREDICTION_SHA256 = "bf113ce4fb87f5ac7a54c5d78dbf14e691b07a562f78c9b9ee61ec5763982a88"
PROFILE_D_SOURCE_COMMIT = "170c48756660a34d611b3a563ac26cd4564434ef"
PROFILE_D_PREDICTION_SHA256 = "7a01382de3e95530ecdfabd7cee049712ce7e326f7d4d299e28c5b5ba981320c"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _run_cover_module():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "run_cover_profile_d_probe_test", REPO_ROOT / "scripts" / "run_cover.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _prediction_affecting_sections(config: dict) -> dict:
    repair = dict(config["leaderboard_repair"])
    return {
        "pipeline": config["pipeline"],
        "leaderboard_repair_enabled": repair["enabled"],
        "leaderboard_repair_features": repair["features"],
        "leaderboard_repair_caps": repair["max_calls_by_relation"],
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
    }


def _semantic_diff(a_plus: dict, profile_d: dict) -> dict:
    a_enum, a_ver = model_blocks(a_plus)
    d_enum, d_ver = model_blocks(profile_d)
    return {
        "verifier.model_id": [a_ver["model_id"], d_ver["model_id"]],
        "verifier.revision": [a_ver["revision"], d_ver["revision"]],
        "unique_model_portfolio": [
            [a_enum["model_id"], a_ver["model_id"]],
            [d_enum["model_id"]],
        ],
        "parameter_total": [
            a_plus["budget_assertion"]["total_published_parameters"],
            profile_d["budget_assertion"]["total_published_parameters"],
        ],
    }


def test_a_plus_award_is_historical_and_unchanged():
    config = _load(A_PLUS_PATH)
    frozen = config["experiment"]["frozen_baseline"]
    assert frozen["status"] == "HISTORICAL_BASELINE_SUPERSEDED_BY_PROFILE_D"
    assert frozen["hidden_test_overall_f1"] == 0.4910
    assert frozen["prediction_sha256"] == A_PLUS_PREDICTION_SHA256
    assert frozen["superseded_by"] == "cover_kbc_v3_3_profile_d_mistral_only_role_swap_test"
    assert frozen["superseded_by_hidden_test_overall_f1"] == 0.4952
    assert frozen["superseded_by_prediction_sha256"] == PROFILE_D_PREDICTION_SHA256
    v31 = config["pipeline"]["selection"]["v3_1"]
    assert v31["aggressive"]["stock_listing_entity_prompt"] is True
    assert v31["safe"]["stock_support_dominance"] is False
    features = config["leaderboard_repair"]["features"]
    assert features["award_metadata_cleanup"] is True
    assert all(
        features[name] is False
        for name in (
            "stock_entity_guard",
            "stock_alias_dedupe",
            "stock_multi_listing_rescue",
            "border_alias_dedupe",
            "border_reciprocity",
            "border_directional_sweep",
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
        )
    )


def test_profile_d_is_marked_as_previous_frozen_baseline_with_hidden_test_metadata():
    config = _load(D_PATH)
    frozen = config["experiment"]["frozen_baseline"]
    assert frozen == {
        "status": "PREVIOUS_FROZEN_BASELINE",
        "hidden_test_overall_f1": 0.4952,
        "superseded_by": (
            "cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test"
        ),
        "superseded_by_hidden_test_overall_f1": 0.5752,
        "source_commit": PROFILE_D_SOURCE_COMMIT,
        "prediction_sha256": PROFILE_D_PREDICTION_SHA256,
        "standalone_submission_name":
            "SUBMIT_PROFILE_D_MISTRAL_ONLY_170c48756660_20260812T104512Z.jsonl",
        "full_run_name": "profile_d_mistral_only_role_swap_170c48756660_20260812T104512Z",
        "predecessor_profile": "A_PLUS_AWARD",
        "predecessor_hidden_test_overall_f1": 0.4910,
        "delta_vs_predecessor": 0.0042,
        "qwen_runtime_calls": 0,
        "unique_neural_parameters": MISTRAL_PARAMETERS,
        "calibration_status": "CALIBRATION_REVIEW_LEADERBOARD_PROBE",
    }
    assert config["experiment"]["hidden_test_scores"]["all_relations"] == {
        "precision": 0.7289,
        "recall": 0.5087,
        "f1": 0.4952,
    }
    assert config["experiment"]["hidden_test_scores"]["personHasCityOfDeath"] == {
        "precision": 0.9900,
        "recall": 0.4900,
        "f1": 0.4900,
    }
    assert config["experiment"]["repair_accounting"] == {
        "changed_rows": 1,
        "total_repair_calls": 0,
        "only_changed_feature": "AwardMetadataNormalizer",
    }
    assert config["experiment"]["call_accounting"]["qwen_call_records_with_model_id"] == 0
    assert config["experiment"]["execution_accounting"] == {
        "total_queries": 475,
        "successful_queries": 475,
        "failed_queries": 0,
        "pipeline_error_rows": 0,
        "unresolved_invariant_errors": 0,
    }


def test_profile_d_feature_flags_match_a_plus_award_and_exclude_c2():
    a_plus = _load(A_PLUS_PATH)
    profile_d = _load(D_PATH)
    c2 = _load(C2_PATH)

    assert _prediction_affecting_sections(profile_d) == _prediction_affecting_sections(a_plus)
    assert profile_d["leaderboard_repair"]["profile"] == "D_MISTRAL_ONLY_ROLE_SWAP"
    assert c2["experiment"]["retired"]["status"] == "RETIRED_NEGATIVE_HIDDEN_TEST_PROBE"

    d_features = profile_d["leaderboard_repair"]["features"]
    c2_features = c2["leaderboard_repair"]["features"]
    for name, enabled in c2_features.items():
        if name == "award_metadata_cleanup":
            assert d_features[name] is True
        elif enabled:
            assert d_features[name] is False, name
    assert profile_d["leaderboard_repair"]["features"].get(
        "mistral_city_empty_rescue", False
    ) is False
    assert profile_d["leaderboard_repair"]["features"].get(
        "mistral_direct_area", False
    ) is False
    assert "direct_city" not in json.dumps(profile_d, sort_keys=True).lower()


def test_profile_d_is_exact_mistral_only_model_portfolio_with_unique_budget():
    profile_d = _load(D_PATH)
    enumerator, verifier = model_blocks(profile_d)
    assert enumerator == verifier
    assert enumerator["model_id"] == MISTRAL_ID
    assert enumerator["revision"] == MISTRAL_REVISION
    assert verifier["model_id"] == MISTRAL_ID
    assert verifier["revision"] == MISTRAL_REVISION
    assert QWEN_ID not in json.dumps(profile_d["model_profile"], sort_keys=True)

    enum_spec = spec_from_config(enumerator)
    verifier_spec = spec_from_config(verifier)
    audit = audit_parameter_budget([enum_spec, verifier_spec])
    assert audit.passed
    assert audit.total_parameters == MISTRAL_PARAMETERS
    assert audit.budget == PARAMETER_LIMIT
    assert [component.model_id for component in audit.counted_specs] == [MISTRAL_ID]
    assert profile_d["budget_assertion"]["total_published_parameters"] == MISTRAL_PARAMETERS
    assert profile_d["budget_assertion"]["limit"] == PARAMETER_LIMIT


def test_run_cover_reuses_one_mistral_runtime_for_profile_d_by_config_identity():
    profile_d = _load(D_PATH)
    enumerator, verifier = model_blocks(profile_d)
    assert enumerator == verifier
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text(encoding="utf-8")
    assert "runtime if verifier_cfg == enumerator_cfg else build_runtime(verifier_cfg)" in source


def test_profile_d_verifier_contract_still_supports_three_way_label_scoring():
    runtime = ScriptedRuntime(model_id=MISTRAL_ID, role="enumerator")
    result = runtime.score_labels(
        LabelScoreRequest(
            prompt="Does the candidate satisfy the relation?",
            labels={"YES": "A", "NO": "B", "UNKNOWN": "C"},
        )
    )
    assert set(result.logits) == {"YES", "NO", "UNKNOWN"}
    assert result.model_id == MISTRAL_ID


def test_profile_d_diff_from_a_plus_award_is_only_role_swap_and_budget():
    a_plus = _load(A_PLUS_PATH)
    profile_d = _load(D_PATH)
    assert _semantic_diff(a_plus, profile_d) == {
        "verifier.model_id": [QWEN_ID, MISTRAL_ID],
        "verifier.revision": [QWEN_REVISION, MISTRAL_REVISION],
        "unique_model_portfolio": [
            [MISTRAL_ID, QWEN_ID],
            [MISTRAL_ID],
        ],
        "parameter_total": [DUAL_PARAMETERS, MISTRAL_PARAMETERS],
    }
    assert _prediction_affecting_sections(profile_d) == _prediction_affecting_sections(a_plus)


def test_profile_d_official_test_integrity_and_no_gold_objects():
    profile_d = _load(D_PATH)
    dataset = load_dataset("test")
    assert profile_d["experiment"]["split"] == "test"
    assert len(dataset) == 475
    assert dataset.sha256 == TEST_SHA256
    assert all(row.is_empty for row in dataset)
    assert profile_d["test_dataset"]["rows"] == 475
    assert profile_d["test_dataset"]["sha256"] == TEST_SHA256
    assert dataset.relation_counts() == {
        "awardWonBy": 10,
        "companyTradesAtStockExchange": 100,
        "countryLandBordersCountry": 67,
        "hasArea": 100,
        "hasCapacity": 98,
        "personHasCityOfDeath": 100,
    }


def test_profile_d_has_no_web_rag_external_corpus_or_test_gold_machinery():
    profile_d = _load(D_PATH)
    forbidden_keys = {
        "rag",
        "retriever_url",
        "external_corpus",
        "test_gold",
        "subject_to_answer",
        "answer_lookup",
        "gold_lookup",
        "train_collection",
    }

    def walk(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                assert key not in forbidden_keys
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(profile_d)


def test_profile_d_is_leaderboard_probe_allowed_with_only_expected_blockers():
    run_cover = _run_cover_module()
    profile_d = _load(D_PATH)
    readiness = evaluate_test_readiness(profile_d, base_dir=CONFIG_DIR, split="test")
    assert readiness.state is ReadinessState.NOT_READY
    assert sorted(readiness.blockers) == sorted((
        (
            "V3 model profile: verifier model_id is "
            "'mistralai/Mistral-Small-3.2-24B-Instruct-2506', "
            "expected 'Qwen/Qwen3.5-4B'"
        ),
        (
            "V3 model profile: verifier revision is "
            "'95a6d26c4bfb886c58daf9d3f7332c857cb27b43', "
            "expected '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a'"
        ),
        (
            "V3 model budget: 24011361280 / 32000000000, "
            "expected 28671226368 / 32000000000"
        ),
        (
            "selection.v3_1: CALIBRATION_REVIEW_REQUIRED - calibration-shifting "
            "feature(s) ['stock_listing_entity_prompt'] are enabled. These "
            "change recall, prompt or action semantics, so the Audit 0073 "
            "M20/M21 calibration no longer describes this run and must be "
            "re-derived from a TRAIN collection made with them."
        ),
    ))
    assert run_cover._allow_leaderboard_probe(profile_d, "test", readiness)
