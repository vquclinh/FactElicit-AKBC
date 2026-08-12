"""Profile E1 conservative Mistral City rescue guards."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
)
from cover_kbc.data.loader import load_dataset
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.relations import (
    _parse_e1_city_output,
    _parse_e1_life_status,
)
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack
from cover_kbc.leaderboard_repair.util import (
    AREA,
    AWARD,
    BORDERS,
    CAPACITY,
    CITY,
    STOCK,
)
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.models.registry import model_blocks, spec_from_config
from cover_kbc.types import Prediction, Query


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
D_PATH = CONFIG_DIR / "cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml"
E1_PATH = CONFIG_DIR / "cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml"
C2_PATH = CONFIG_DIR / "cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml"

MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
MISTRAL_PARAMETERS = 24_011_361_280
PARAMETER_LIMIT = 32_000_000_000
TEST_SHA256 = "67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1"


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


def _semantic_diff(profile_d: dict, profile_e1: dict) -> dict:
    before = _prediction_affecting_sections(profile_d)
    after = _prediction_affecting_sections(profile_e1)
    diff = {}
    for key in sorted(before):
        if before[key] != after[key]:
            diff[key] = [before[key], after[key]]
    return diff


def test_e1_config_diff_from_profile_d_is_only_city_rescue_and_metadata():
    profile_d = _load(D_PATH)
    profile_e1 = _load(E1_PATH)

    diff = _semantic_diff(profile_d, profile_e1)
    assert set(diff) == {"leaderboard_repair_caps", "leaderboard_repair_features"}

    feature_before, feature_after = diff["leaderboard_repair_features"]
    feature_delta = {
        key: [feature_before[key], feature_after[key]]
        for key in feature_before
        if feature_before[key] != feature_after[key]
    }
    assert feature_delta == {"mistral_city_empty_rescue": [False, True]}

    caps_before, caps_after = diff["leaderboard_repair_caps"]
    cap_delta = {
        key: [caps_before[key], caps_after[key]]
        for key in caps_before
        if caps_before[key] != caps_after[key]
    }
    assert cap_delta == {CITY: [0, 2]}

    assert profile_e1["experiment"]["name"] == (
        "cover_kbc_v3_4_profile_e1_mistral_city_rescue_test"
    )
    assert profile_e1["experiment"]["baseline_profile"]["name"] == profile_d["experiment"]["name"]
    assert profile_d["leaderboard_repair"]["profile"] == "D_MISTRAL_ONLY_ROLE_SWAP"
    assert profile_e1["leaderboard_repair"]["profile"] == "E1_MISTRAL_CITY_RESCUE"


def test_e1_model_portfolio_is_profile_d_mistral_only_and_budget_legal():
    profile_e1 = _load(E1_PATH)
    enumerator, verifier = model_blocks(profile_e1)
    assert enumerator == verifier
    assert enumerator["model_id"] == MISTRAL_ID
    assert enumerator["revision"] == MISTRAL_REVISION
    assert "Qwen/Qwen3.5-4B" not in json.dumps(profile_e1["model_profile"], sort_keys=True)

    audit = audit_parameter_budget([
        spec_from_config(enumerator),
        spec_from_config(verifier),
    ])
    assert audit.passed
    assert audit.total_parameters == MISTRAL_PARAMETERS
    assert audit.budget == PARAMETER_LIMIT
    assert [component.model_id for component in audit.counted_specs] == [MISTRAL_ID]


def test_e1_official_test_integrity_and_probe_readiness():
    profile_e1 = _load(E1_PATH)
    dataset = load_dataset("test")
    assert profile_e1["experiment"]["split"] == "test"
    assert len(dataset) == 475
    assert dataset.sha256 == TEST_SHA256
    assert all(row.is_empty for row in dataset)
    assert profile_e1["test_dataset"]["rows"] == 475
    assert profile_e1["test_dataset"]["sha256"] == TEST_SHA256

    readiness = evaluate_test_readiness(profile_e1, base_dir=CONFIG_DIR, split="test")
    assert readiness.state is ReadinessState.NOT_READY
    assert any("CALIBRATION_REVIEW_REQUIRED" in blocker for blocker in readiness.blockers)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "run_cover_profile_e1_probe_test", REPO_ROOT / "scripts" / "run_cover.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module._allow_leaderboard_probe(profile_e1, "test", readiness)


def test_e1_keeps_profile_d_non_empty_city_without_calling_model():
    prediction = _prediction("Known Nonempty Person", CITY, ["Existing City"])
    runtime = ScriptedRuntime(
        {("e1_city_life_status", "Known Nonempty Person", CITY): ["DECEASED"]},
        model_id=MISTRAL_ID,
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"mistral_city_empty_rescue": True},
        "max_calls_by_relation": {CITY: 2},
    })
    result = _stack(config, runtime).apply([prediction], queries=[_query(prediction)])
    assert result.predictions[0].object_entities == ["Existing City"]
    assert result.records[0].calls_used == 0
    assert runtime.calls == 0
    assert result.accounting["by_relation"][CITY]["e1_city_rescue"][
        "bypassed_non_empty_rows"
    ] == 1


def test_e1_empty_city_living_unknown_and_invalid_preserve_empty():
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"mistral_city_empty_rescue": True},
        "max_calls_by_relation": {CITY: 2},
    })
    rows = [
        _prediction("Living Person", CITY, [], row_index=0),
        _prediction("Unknown Status Person", CITY, [], row_index=1),
        _prediction("Invalid Status Person", CITY, [], row_index=2),
    ]
    runtime = ScriptedRuntime(
        {
            ("e1_city_life_status", "Living Person", CITY): ["LIVING"],
            ("e1_city_life_status", "Unknown Status Person", CITY): ["UNKNOWN"],
            ("e1_city_life_status", "Invalid Status Person", CITY): [
                "DECEASED confidence=0.9"
            ],
        },
        model_id=MISTRAL_ID,
        role="verifier",
    )
    result = _stack(config, runtime).apply(rows, queries=[_query(row) for row in rows])
    assert [prediction.object_entities for prediction in result.predictions] == [[], [], []]
    assert [record.calls_used for record in result.records] == [1, 1, 1]
    summary = result.accounting["by_relation"][CITY]["e1_city_rescue"]
    assert summary["eligible_empty_rows"] == 3
    assert summary["life_status_calls"] == 3
    assert summary["living_count"] == 1
    assert summary["unknown_count"] == 1
    assert summary["invalid_life_outputs"] == 1
    assert summary["city_calls"] == 0
    assert summary["changed_rows"] == 0


def test_e1_deceased_unknown_city_preserves_empty():
    prediction = _prediction("Deceased Unknown City Person", CITY, [])
    runtime = ScriptedRuntime(
        {
            ("e1_city_life_status", "Deceased Unknown City Person", CITY): ["DECEASED"],
            ("e1_city_of_death_recall", "Deceased Unknown City Person", CITY): ["UNKNOWN"],
        },
        model_id=MISTRAL_ID,
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"mistral_city_empty_rescue": True},
        "max_calls_by_relation": {CITY: 2},
    })
    result = _stack(config, runtime).apply([prediction], queries=[_query(prediction)])
    assert result.predictions[0].object_entities == []
    assert result.records[0].calls_used == 2
    summary = result.accounting["by_relation"][CITY]["e1_city_rescue"]
    assert summary["deceased_count"] == 1
    assert summary["city_calls"] == 1
    assert summary["city_unknown_count"] == 1
    assert summary["changed_rows"] == 0


def test_e1_deceased_city_sets_singleton_and_uses_same_mistral_runtime():
    prediction = _prediction("Deceased City Person", CITY, [])
    runtime = CapturingScriptedRuntime(
        {
            ("e1_city_life_status", "Deceased City Person", CITY): ["DECEASED"],
            ("e1_city_of_death_recall", "Deceased City Person", CITY): ["CITY: São Paulo"],
        },
        model_id=MISTRAL_ID,
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"mistral_city_empty_rescue": True},
        "max_calls_by_relation": {CITY: 2},
    })
    result = _stack(config, runtime).apply([prediction], queries=[_query(prediction)])
    assert result.predictions[0].object_entities == ["São Paulo"]
    assert result.records[0].calls_used == 2
    assert runtime.calls == 2
    assert [request.decode.temperature for request in runtime.requests] == [0.0, 0.0]
    assert [request.decode.top_p for request in runtime.requests] == [1.0, 1.0]
    assert [request.decode.max_new_tokens for request in runtime.requests] == [8, 20]
    assert runtime.requests[0].prompt == (
        "Subject: Deceased City Person\n\n"
        "Question:\n"
        "Is this exact person deceased?\n\n"
        "Return exactly one of:\n"
        "DECEASED\n"
        "LIVING\n"
        "UNKNOWN\n\n"
        "Use UNKNOWN if you are not sufficiently confident.\n\n"
        "Do not explain your answer."
    )
    assert runtime.requests[1].prompt == (
        "Subject: Deceased City Person\n\n"
        "Relation: personHasCityOfDeath\n\n"
        "The person has already been classified as DECEASED.\n\n"
        "Question:\n"
        "In which city did this exact person die?\n\n"
        "Return exactly one of:\n\n"
        "CITY: <city name>\n\n"
        "or:\n\n"
        "UNKNOWN\n\n"
        "The answer must be the city/locality of death.\n\n"
        "Do not return:\n"
        "- hospital or institution name\n"
        "- country\n"
        "- state or province\n"
        "- birthplace\n"
        "- main residence\n"
        "- burial place\n\n"
        "If you cannot confidently identify the city of death, return UNKNOWN.\n\n"
        "Do not explain your answer."
    )
    assert {call.model_id for call in result.records[0].calls} == {MISTRAL_ID}
    assert {call.model_role for call in result.records[0].calls} == {"verifier"}
    assert [call.generated_tokens for call in result.records[0].calls]
    summary = result.accounting["by_relation"][CITY]["e1_city_rescue"]
    assert summary["city_accepted_count"] == 1
    assert summary["changed_rows"] == 1


def test_e1_city_parser_rejects_arbitrary_prose_and_packed_outputs():
    assert _parse_e1_life_status("deceased") == "DECEASED"
    assert _parse_e1_life_status("DECEASED\nbecause") == "INVALID"
    assert _parse_e1_life_status("ALIVE") == "INVALID"
    assert _parse_e1_city_output("CITY: Los Angeles") == ("CITY", "Los Angeles")
    assert _parse_e1_city_output("CITY: Łódź") == ("CITY", "Łódź")
    assert _parse_e1_city_output("UNKNOWN") == ("UNKNOWN", None)
    assert _parse_e1_city_output("The person died in Paris.") == ("INVALID", None)
    assert _parse_e1_city_output("CITY: Paris; London") == ("INVALID", None)
    assert _parse_e1_city_output("CITY: Paris\nNo explanation") == ("INVALID", None)


def test_e1_does_not_mutate_non_city_relations_or_enable_c2_features():
    profile_e1 = _load(E1_PATH)
    repair = LeaderboardRepairConfig.from_mapping(profile_e1["leaderboard_repair"])
    features = asdict(repair.features)
    for feature in (
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
    ):
        assert features[feature] is False, feature
    assert features["award_metadata_cleanup"] is True
    assert features["mistral_city_empty_rescue"] is True

    runtime = ScriptedRuntime(
        {},
        model_id=MISTRAL_ID,
        role="verifier",
    )
    predictions = [
        _prediction("Award", AWARD, ["1998: Recipient"], row_index=0),
        _prediction("Company", STOCK, ["NYSE"], row_index=1),
        _prediction("Country", BORDERS, ["Neighbor"], row_index=2),
        _prediction("Area", AREA, ["123"], row_index=3),
        _prediction("Venue", CAPACITY, ["10000"], row_index=4),
    ]
    result = _stack(repair, runtime).apply(
        predictions,
        queries=[_query(prediction) for prediction in predictions],
    )
    assert [p.object_entities for p in result.predictions] == [
        ["Recipient"],
        ["NYSE"],
        ["Neighbor"],
        ["123"],
        ["10000"],
    ]
    assert result.records[0].features == ["AwardMetadataNormalizer"]
    assert all(record.calls_used == 0 for record in result.records)


def test_e1_runtime_source_reuses_profile_d_single_runtime_alias():
    profile_e1 = _load(E1_PATH)
    enumerator, verifier = model_blocks(profile_e1)
    assert enumerator == verifier
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text(encoding="utf-8")
    assert "runtime if verifier_cfg == enumerator_cfg else build_runtime(verifier_cfg)" in source
    assert "Qwen/Qwen3.5-4B" not in json.dumps(profile_e1["model_profile"], sort_keys=True)


def test_e1_no_profile_e_extra_direct_city_or_external_data_config():
    profile_e1 = _load(E1_PATH)
    serialized = json.dumps(profile_e1, sort_keys=True).lower()
    assert "qwen/qwen3.5-4b" not in serialized
    for forbidden in (
        "profile_e2",
        "failure_signature",
        "evidencebundle",
        "direct_city_qa",
    ):
        assert forbidden not in serialized

    forbidden_keys = {
        "rag",
        "retriever_url",
        "external_corpus",
        "test_gold",
        "subject_to_answer",
        "answer_lookup",
        "gold_lookup",
    }

    def walk(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                assert key not in forbidden_keys
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(profile_e1)

    profile_d = _load(D_PATH)
    d_copy = replace(
        LeaderboardRepairConfig.from_mapping(profile_d["leaderboard_repair"]).features
    )
    assert d_copy.mistral_city_empty_rescue is False
