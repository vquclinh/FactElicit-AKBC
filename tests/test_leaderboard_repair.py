"""Active leaderboard repair stack tests.

The active frozen line is Profile E3: Profile E2 plus Area Multi-View for
hasArea. Retired B/C/C2 repair experiments remain documented in configs/audits
but are no longer executable runtime branches.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
)
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack, build_repair_stack
from cover_kbc.leaderboard_repair.util import (
    AREA,
    AWARD,
    BORDERS,
    CAPACITY,
    CITY,
    STOCK,
    normalize_award_metadata,
)
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.types import Prediction, Query


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
PACKAGE = REPO_ROOT / "src" / "cover_kbc" / "leaderboard_repair"


def _prediction(subject: str, relation: str, values=None, row_index=0) -> Prediction:
    return Prediction(
        subject=subject,
        relation=relation,
        object_entities=list(values or []),
        row_index=row_index,
    )


def _query(prediction: Prediction) -> Query:
    return Query(prediction.subject, prediction.relation, prediction.row_index)


def _stack(config: LeaderboardRepairConfig, runtime=None) -> LeaderboardRepairStack:
    resolved = runtime or ScriptedRuntime(model_id="mistral", role="verifier")
    return LeaderboardRepairStack(
        config=config,
        enumerator=resolved,
        verifier=resolved,
    )


def _load(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8")) or {}


def test_disabled_stack_is_not_constructed_and_keeps_frozen_behavior():
    assert build_repair_stack({}, enumerator=ScriptedRuntime(), verifier=ScriptedRuntime()) is None
    prediction = _prediction("A", STOCK, ["NYSE"], row_index=7)
    config = LeaderboardRepairConfig.from_mapping({"enabled": False})
    result = _stack(config).apply([prediction], queries=[_query(prediction)])
    assert [p.to_official_row() for p in result.predictions] == [prediction.to_official_row()]
    assert result.records == []


def test_repair_source_has_no_web_rag_test_gold_or_subject_answer_table_patterns():
    forbidden = (
        "requests", "urllib", "wikipedia", "wikidata", "http://", "https://",
        "benchmark/data/test", "test.jsonl", "ObjectEntities\" in entry",
        "subject_to_answer", "answer_lookup", "gold_lookup",
    )
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, (path, marker)


def test_award_normalizer_is_idempotent_and_zero_call():
    assert normalize_award_metadata("1998: Richard Pryor") == "Richard Pryor"
    assert normalize_award_metadata("Groups: NONE") is None
    once = normalize_award_metadata("George Carlin (second time)")
    assert once == "George Carlin"
    assert normalize_award_metadata(once) == once

    prediction = _prediction(
        "Exact Award",
        AWARD,
        ["1998: Richard Pryor", "Groups: NONE", "George Carlin (second time)"],
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"award_metadata_cleanup": True},
        "max_calls_by_relation": {AWARD: 1},
    })
    result = _stack(config).apply([prediction], queries=[_query(prediction)])
    assert result.predictions[0].object_entities == ["Richard Pryor", "George Carlin"]
    assert result.records[0].calls_used == 0
    assert result.accounting["by_relation"][AWARD]["features"] == {
        "AwardMetadataNormalizer": 1
    }


def test_e1_city_rescue_mutates_only_empty_deceased_city_rows():
    predictions = [
        _prediction("Already Known", CITY, ["Existing City"], row_index=0),
        _prediction("Living Person", CITY, [], row_index=1),
        _prediction("Unknown Person", CITY, [], row_index=2),
        _prediction("Invalid Person", CITY, [], row_index=3),
        _prediction("Deceased Unknown City", CITY, [], row_index=4),
        _prediction("Deceased Known City", CITY, [], row_index=5),
        _prediction("Stock Row", STOCK, ["NYSE"], row_index=6),
    ]
    runtime = ScriptedRuntime(
        {
            ("e1_city_life_status", "Living Person", CITY): ["LIVING"],
            ("e1_city_life_status", "Unknown Person", CITY): ["UNKNOWN"],
            ("e1_city_life_status", "Invalid Person", CITY): ["DECEASED confidence=0.9"],
            ("e1_city_life_status", "Deceased Unknown City", CITY): ["DECEASED"],
            ("e1_city_of_death_recall", "Deceased Unknown City", CITY): ["UNKNOWN"],
            ("e1_city_life_status", "Deceased Known City", CITY): ["DECEASED"],
            ("e1_city_of_death_recall", "Deceased Known City", CITY): ["CITY: São Paulo"],
        },
        model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"mistral_city_empty_rescue": True},
        "max_calls_by_relation": {CITY: 2, STOCK: 0},
    })
    result = _stack(config, runtime).apply(
        predictions,
        queries=[_query(prediction) for prediction in predictions],
    )
    assert [prediction.object_entities for prediction in result.predictions] == [
        ["Existing City"],
        [],
        [],
        [],
        [],
        ["São Paulo"],
        ["NYSE"],
    ]
    assert [record.calls_used for record in result.records] == [0, 1, 1, 1, 2, 2, 0]
    summary = result.accounting["by_relation"][CITY]["e1_city_rescue"]
    assert summary == {
        "eligible_empty_rows": 5,
        "bypassed_non_empty_rows": 1,
        "life_status_calls": 5,
        "deceased_count": 2,
        "living_count": 1,
        "unknown_count": 1,
        "invalid_life_outputs": 1,
        "city_calls": 2,
        "city_accepted_count": 1,
        "city_unknown_count": 1,
        "invalid_city_outputs": 0,
        "changed_rows": 1,
    }


def test_profile_configs_make_f1_current_and_retired_profiles_archival():
    old_e1 = _load("cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml")
    previous_e1 = _load(
        "cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml"
    )
    current_e2 = _load(
        "cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml"
    )
    current_e3 = _load(
        "cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml"
    )
    current_f1 = _load(
        "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml"
    )
    d = _load("cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml")
    b = _load("cover_kbc_v3_2_profile_b_repair_core_test.yaml")
    c = _load("cover_kbc_v3_2_profile_c_aggressive_recall_test.yaml")
    c2 = _load("cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml")

    assert current_f1["experiment"]["name"] == (
        "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test"
    )
    assert current_f1["experiment"]["frozen_baseline"]["status"] == (
        "FROZEN_CURRENT_BASELINE"
    )
    assert current_f1["experiment"]["hidden_test_scores"]["all_relations"]["f1"] == 0.5878
    assert current_f1["experiment"]["hidden_test_scores"][STOCK]["f1"] == 0.7385

    assert current_e3["experiment"]["name"] == (
        "cover_kbc_v3_7_profile_e3_mistral_area_multiview_test"
    )
    assert current_e3["experiment"]["frozen_baseline"]["status"] == (
        "PREVIOUS_FROZEN_BASELINE"
    )
    assert current_e3["experiment"]["frozen_baseline"]["superseded_by"] == (
        "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test"
    )
    assert current_e3["experiment"]["hidden_test_scores"]["all_relations"]["f1"] == 0.5857
    assert current_e3["experiment"]["hidden_test_scores"][AREA]["f1"] == 0.6700

    assert current_e2["experiment"]["name"] == (
        "cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test"
    )
    assert current_e2["experiment"]["frozen_baseline"]["status"] == (
        "HISTORICAL_BASELINE_SUPERSEDED_BY_PROFILE_E3"
    )
    assert current_e2["experiment"]["frozen_baseline"]["superseded_by"] == (
        "cover_kbc_v3_7_profile_e3_mistral_area_multiview_test"
    )
    assert current_e2["experiment"]["hidden_test_scores"]["all_relations"]["f1"] == 0.5836
    assert current_e2["experiment"]["hidden_test_scores"][CAPACITY]["f1"] == 0.1633

    assert previous_e1["experiment"]["name"] == (
        "cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test"
    )
    assert previous_e1["experiment"]["frozen_baseline"]["status"] == (
        "PREVIOUS_FROZEN_BASELINE"
    )
    assert previous_e1["experiment"]["frozen_baseline"]["superseded_by"] == (
        "cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test"
    )
    assert old_e1["experiment"]["profile_e1_probe"]["status"] == (
        "HISTORICAL_SUPERSEDED_CITY_ONLY_E1"
    )
    assert old_e1["experiment"]["profile_e1_probe"]["superseded_by"] == (
        "cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test"
    )
    assert d["experiment"]["frozen_baseline"]["status"] == "PREVIOUS_FROZEN_BASELINE"

    for config in (b, c):
        assert config["experiment"]["historical"]["status"] == (
            "HISTORICAL_SUPERSEDED_ARCHIVAL_CONFIG"
        )
        assert config["leaderboard_probe"]["enabled"] is False
        assert config["leaderboard_repair"]["enabled"] is False
        assert config["leaderboard_repair"]["archived_runtime_status"] == (
            "RETIRED_IMPLEMENTATION_REMOVED_AUDIT_0088"
        )

    assert c2["experiment"]["retired"]["status"] == "RETIRED_NEGATIVE_HIDDEN_TEST_PROBE"
    assert c2["leaderboard_probe"]["enabled"] is False
    assert c2["leaderboard_repair"]["enabled"] is False


def test_e2_active_flags_are_only_award_city_area_and_capacity_multiview():
    e2 = _load(
        "cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml"
    )
    repair = LeaderboardRepairConfig.from_mapping(e2["leaderboard_repair"])
    flags = asdict(repair.features)
    assert flags["award_metadata_cleanup"] is True
    assert flags["mistral_city_empty_rescue"] is True
    assert flags["mistral_direct_area"] is True
    assert flags["mistral_capacity_multiview"] is True
    assert all(
        value is False
        for key, value in flags.items()
        if key not in {
            "award_metadata_cleanup",
            "mistral_city_empty_rescue",
            "mistral_direct_area",
            "mistral_capacity_multiview",
        }
    )
    assert repair.direct_area_mode == "DIRECT_ALL"
    assert repair.city_rescue_mode == "DIRECT_ALL_STANDALONE"
    assert repair.area_multiview_mode == "OFF"
    assert repair.capacity_multiview_mode == "DIRECT_ALL"
    assert repair.max_calls_by_relation[AREA] == 1
    assert repair.max_calls_by_relation[CAPACITY] == 5
    assert repair.max_calls_by_relation[CITY] == 2
    assert repair.max_calls_by_relation[AWARD] == 1
    assert repair.max_calls_by_relation[STOCK] == 0
    assert repair.max_calls_by_relation[BORDERS] == 0


def test_e3_active_flags_are_e2_plus_area_multiview_only():
    e3 = _load(
        "cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml"
    )
    repair = LeaderboardRepairConfig.from_mapping(e3["leaderboard_repair"])
    flags = asdict(repair.features)
    assert flags["award_metadata_cleanup"] is True
    assert flags["mistral_city_empty_rescue"] is True
    assert flags["mistral_direct_area"] is True
    assert flags["mistral_capacity_multiview"] is True
    assert flags["mistral_area_multiview"] is True
    assert all(
        value is False
        for key, value in flags.items()
        if key not in {
            "award_metadata_cleanup",
            "mistral_city_empty_rescue",
            "mistral_direct_area",
            "mistral_capacity_multiview",
            "mistral_area_multiview",
        }
    )
    assert repair.direct_area_mode == "DIRECT_ALL"
    assert repair.city_rescue_mode == "DIRECT_ALL_STANDALONE"
    assert repair.area_multiview_mode == "DIRECT_ALL"
    assert repair.capacity_multiview_mode == "DIRECT_ALL"
    assert repair.max_calls_by_relation[AREA] == 5
    assert repair.max_calls_by_relation[CAPACITY] == 5
    assert repair.max_calls_by_relation[CITY] == 2
    assert repair.max_calls_by_relation[AWARD] == 1
    assert repair.max_calls_by_relation[STOCK] == 0
    assert repair.max_calls_by_relation[BORDERS] == 0


def test_f1_active_flags_are_e3_plus_stock_empty_rescue_only():
    f1 = _load(
        "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml"
    )
    repair = LeaderboardRepairConfig.from_mapping(f1["leaderboard_repair"])
    flags = asdict(repair.features)
    assert flags["award_metadata_cleanup"] is True
    assert flags["mistral_city_empty_rescue"] is True
    assert flags["mistral_direct_area"] is True
    assert flags["mistral_capacity_multiview"] is True
    assert flags["mistral_area_multiview"] is True
    assert flags["mistral_stock_empty_rescue"] is True
    assert all(
        value is False
        for key, value in flags.items()
        if key not in {
            "award_metadata_cleanup",
            "mistral_city_empty_rescue",
            "mistral_direct_area",
            "mistral_capacity_multiview",
            "mistral_area_multiview",
            "mistral_stock_empty_rescue",
        }
    )
    assert repair.direct_area_mode == "DIRECT_ALL"
    assert repair.city_rescue_mode == "DIRECT_ALL_STANDALONE"
    assert repair.area_multiview_mode == "DIRECT_ALL"
    assert repair.capacity_multiview_mode == "DIRECT_ALL"
    assert repair.stock_empty_rescue_mode == "EMPTY_ONLY_STRONG_CONSENSUS"
    assert repair.stock_empty_rescue_min_support == 3
    assert repair.max_calls_by_relation[AREA] == 5
    assert repair.max_calls_by_relation[CAPACITY] == 5
    assert repair.max_calls_by_relation[CITY] == 2
    assert repair.max_calls_by_relation[AWARD] == 1
    assert repair.max_calls_by_relation[STOCK] == 4
    assert repair.max_calls_by_relation[BORDERS] == 0


def test_profile_readiness_and_probe_gate_match_active_development_line():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "run_cover_leaderboard_probe_test", REPO_ROOT / "scripts" / "run_cover.py")
    run_cover = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = run_cover
    spec.loader.exec_module(run_cover)

    for name in (
        "cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml",
        "cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml",
        "cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml",
        "cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml",
        "cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml",
        "cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml",
    ):
        profile = _load(name)
        readiness = evaluate_test_readiness(profile, base_dir=CONFIG_DIR, split="test")
        assert readiness.state is ReadinessState.NOT_READY
        assert any("CALIBRATION_REVIEW_REQUIRED" in b for b in readiness.blockers)
        assert run_cover._allow_leaderboard_probe(profile, "test", readiness)

    for name in (
        "cover_kbc_v3_2_profile_b_repair_core_test.yaml",
        "cover_kbc_v3_2_profile_c_aggressive_recall_test.yaml",
        "cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml",
    ):
        profile = _load(name)
        readiness = evaluate_test_readiness(profile, base_dir=CONFIG_DIR, split="test")
        assert not run_cover._allow_leaderboard_probe(profile, "test", readiness)


def test_run_cover_help_does_not_load_models():
    import subprocess

    completed = subprocess.run(
        [sys.executable, "scripts/run_cover.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--config" in completed.stdout


def test_active_repair_package_contains_no_retired_c2_runtime_modules():
    paths = sorted(path.name for path in PACKAGE.glob("*.py"))
    assert "consistency.py" not in paths
    assert "risk_guard.py" not in paths
    assert "parsing.py" not in paths
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    assert "BorderDirectionalSweep" not in source
    assert "DeathCityRecall" not in source
    assert "AreaEmptyRescue" not in source
    assert "CapacityRepair" not in source
    assert "AwardRecipientWitness" not in source
    assert "AwardTimeSlicedRecall" not in source
