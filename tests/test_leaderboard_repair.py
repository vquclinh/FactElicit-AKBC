"""V3.2 leaderboard repair stack tests.

These tests use only synthetic/scripted model outputs.  They prove the stack is
feature-flagged, bounded, relation-isolated and compliant with the no-web /
no-TEST-gold / no subject-answer-table constraints.
"""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
)
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack, build_repair_stack
from cover_kbc.leaderboard_repair.util import (
    AWARD,
    BORDERS,
    CAPACITY,
    CITY,
    STOCK,
    AREA,
    normalize_award_metadata,
)
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.types import EmptyReason, Prediction, Query
from cover_kbc.v3_core.hypothesis import (
    Hypothesis,
    QueryHypothesisGraph,
    SemanticType,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
PACKAGE = REPO_ROOT / "src" / "cover_kbc" / "leaderboard_repair"


def _prediction(subject: str, relation: str, values=None, row_index=0, **kw):
    return Prediction(
        subject=subject,
        relation=relation,
        object_entities=list(values or []),
        row_index=row_index,
        **kw,
    )


def _query(prediction: Prediction) -> Query:
    return Query(prediction.subject, prediction.relation, prediction.row_index)


def _graph(prediction: Prediction, values: list[tuple[str, int]]) -> QueryHypothesisGraph:
    hypotheses = tuple(
        Hypothesis(
            hypothesis_id=f"h{i}",
            normalized_value=value.lower(),
            display=value,
            semantic_type=SemanticType.STOCK_EXCHANGE,
            independent_support_count=support,
        )
        for i, (value, support) in enumerate(values)
    )
    return QueryHypothesisGraph(
        schema_version="test",
        relation=prediction.relation,
        subject=prediction.subject,
        row_index=prediction.row_index,
        relation_profile={},
        hypotheses=hypotheses,
    )


def _stack(config: LeaderboardRepairConfig, enum=None, ver=None) -> LeaderboardRepairStack:
    return LeaderboardRepairStack(
        config=config,
        enumerator=enum or ScriptedRuntime(model_id="mistral", role="enumerator"),
        verifier=ver or ScriptedRuntime(model_id="qwen", role="verifier"),
    )


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


def test_stock_guard_dedupe_and_rescue_preserves_multi_listing():
    prediction = _prediction("Example Holdings", STOCK, ["NYSE", "Example Holdings"])
    verifier = ScriptedRuntime(
        {
            ("l7_stock_entity_guard", "Example Holdings", STOCK): [
                "KEEP: NYSE\nDROP: Example Holdings | company name"
            ],
            ("l7_stock_multi_listing_rescue", "Example Holdings", STOCK): [
                "RESTORE: NYSE; London Stock Exchange confidence=0.92"
            ],
        },
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {
            "stock_entity_guard": True,
            "stock_alias_dedupe": True,
            "stock_multi_listing_rescue": True,
        },
        "max_calls_by_relation": {STOCK: 2},
    })
    graph = _graph(prediction, [("London Stock Exchange", 2)])
    result = _stack(config, ver=verifier).apply(
        [prediction], queries=[_query(prediction)], hypothesis_graphs=[graph])
    assert result.predictions[0].object_entities == ["NYSE", "London Stock Exchange"]
    record = result.records[0].to_json()
    assert record["calls_used"] == 2
    assert any(d["feature"] == "StockMultiListingRescue" for d in record["decisions"])


def test_stock_rescue_requires_pre_final_support():
    prediction = _prediction("Example Holdings", STOCK, ["NYSE"])
    verifier = ScriptedRuntime(
        {("l7_stock_multi_listing_rescue", "Example Holdings", STOCK): [
            "RESTORE: NYSE; London Stock Exchange confidence=0.95"
        ]},
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"stock_multi_listing_rescue": True},
        "max_calls_by_relation": {STOCK: 2},
    })
    graph = _graph(prediction, [("London Stock Exchange", 0)])
    result = _stack(config, ver=verifier).apply(
        [prediction], queries=[_query(prediction)], hypothesis_graphs=[graph])
    assert result.predictions[0].object_entities == ["NYSE"]


def test_border_reciprocity_does_not_blindly_union_unknown():
    a = _prediction("Aland", BORDERS, ["Borduria"], row_index=0)
    b = _prediction("Borduria", BORDERS, [], row_index=1)
    verifier = ScriptedRuntime(
        {("l8_border_reciprocity", "Aland", BORDERS): ["UNKNOWN: disputed"]},
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"border_reciprocity": True},
        "max_calls_by_relation": {BORDERS: 1},
    })
    result = _stack(config, ver=verifier).apply([a, b], queries=[_query(a), _query(b)])
    assert result.predictions[0].object_entities == ["Borduria"]
    assert result.predictions[1].object_entities == []
    assert any("unknown_preserved_existing_state" == d["decision"]
               for d in result.records[0].decisions)


def test_border_reciprocity_valid_makes_reciprocal_and_invalid_removes_direction():
    a = _prediction("Aland", BORDERS, ["Borduria"], row_index=0)
    b = _prediction("Borduria", BORDERS, [], row_index=1)
    verifier = ScriptedRuntime(
        {("l8_border_reciprocity", "Aland", BORDERS): ["VALID: land boundary"]},
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"border_reciprocity": True},
        "max_calls_by_relation": {BORDERS: 1},
    })
    result = _stack(config, ver=verifier).apply([a, b], queries=[_query(a), _query(b)])
    assert result.predictions[1].object_entities == ["Aland"]

    verifier = ScriptedRuntime(
        {("l8_border_reciprocity", "Aland", BORDERS): ["INVALID: maritime only"]},
        model_id="qwen",
        role="verifier",
    )
    result = _stack(config, ver=verifier).apply([a, b], queries=[_query(a), _query(b)])
    assert result.predictions[0].object_entities == []


def test_city_living_status_preserves_empty_output():
    prediction = _prediction(
        "Living Person", CITY, [], empty_reason=EmptyReason.UNRESOLVED_ABSTENTION)
    enum = ScriptedRuntime(
        {("l7_death_status_mistral", "Living Person", CITY): ["ALIVE confidence=0.91"]},
        model_id="mistral",
        role="enumerator",
    )
    ver = ScriptedRuntime(
        {("l7_death_status_qwen", "Living Person", CITY): ["ALIVE confidence=0.88"]},
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"death_existence_gate": True, "death_city_recall": True},
        "max_calls_by_relation": {CITY: 4},
    })
    result = _stack(config, enum=enum, ver=ver).apply([prediction], queries=[_query(prediction)])
    assert result.predictions[0].object_entities == []
    assert "life_status_evidence" in json.dumps(result.records[0].to_json())


def test_city_deceased_recall_uses_two_views_and_singleton_output():
    prediction = _prediction("Dead Person", CITY, [])
    enum = ScriptedRuntime(
        {
            ("l7_death_status_mistral", "Dead Person", CITY): ["DECEASED confidence=0.9"],
            ("l7_death_city_direct", "Dead Person", CITY): ["CITY: Testville"],
        },
        model_id="mistral",
        role="enumerator",
    )
    ver = ScriptedRuntime(
        {
            ("l7_death_status_qwen", "Dead Person", CITY): ["DECEASED confidence=0.8"],
            ("l7_death_city_final_locality", "Dead Person", CITY): ["CITY: Testville"],
        },
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"death_existence_gate": True, "death_city_recall": True},
        "max_calls_by_relation": {CITY: 4},
    })
    result = _stack(config, enum=enum, ver=ver).apply([prediction], queries=[_query(prediction)])
    assert result.predictions[0].object_entities == ["Testville"]
    assert result.records[0].calls_used == 4


def test_area_and_capacity_numeric_consensus_use_five_percent_tolerance():
    area = _prediction("Test Island", AREA, [])
    enum = ScriptedRuntime(
        {("l7_area_recall_exact_entity", "Test Island", AREA): ["AREA: 100 km2"]},
        model_id="mistral",
        role="enumerator",
    )
    ver = ScriptedRuntime(
        {("l7_area_recall_semantic_contrast", "Test Island", AREA): ["AREA: 104 square kilometres"]},
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"area_empty_rescue": True},
        "max_calls_by_relation": {AREA: 3},
    })
    result = _stack(config, enum=enum, ver=ver).apply([area], queries=[_query(area)])
    assert result.predictions[0].object_entities == ["102"]

    capacity = _prediction("Exact Arena in Test City", CAPACITY, [])
    enum = ScriptedRuntime(
        {("l7_capacity_exact_venue_direct", "Exact Arena in Test City", CAPACITY): ["CAPACITY: 10000"]},
        model_id="mistral",
        role="enumerator",
    )
    ver = ScriptedRuntime(
        {("l7_capacity_exact_venue_variants", "Exact Arena in Test City", CAPACITY): ["CAPACITY: 10450"]},
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"capacity_repair": True},
        "max_calls_by_relation": {CAPACITY: 3},
    })
    result = _stack(config, enum=enum, ver=ver).apply([capacity], queries=[_query(capacity)])
    assert result.predictions[0].object_entities == ["10225"]
    assert "Do not substitute another stadium/arena" in ver.seen_prompts[0]


def test_award_normalizer_is_idempotent_and_witness_filters_only_invalids():
    assert normalize_award_metadata("1998: Richard Pryor") == "Richard Pryor"
    assert normalize_award_metadata("Groups: NONE") is None
    once = normalize_award_metadata("George Carlin (second time)")
    assert once == "George Carlin"
    assert normalize_award_metadata(once) == once

    prediction = _prediction(
        "Exact Award", AWARD,
        ["1998: Richard Pryor", "Groups: NONE", "Nominee Person"])
    verifier = ScriptedRuntime(
        {("l7_award_recipient_witness", "Exact Award", AWARD): [
            "VALID: Richard Pryor | 1998 event\nINVALID: Nominee Person | nominee"
        ]},
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {
            "award_metadata_cleanup": True,
            "award_recipient_witness": True,
        },
        "max_calls_by_relation": {AWARD: 8},
    })
    result = _stack(config, ver=verifier).apply([prediction], queries=[_query(prediction)])
    assert result.predictions[0].object_entities == ["Richard Pryor"]


def test_repair_budgets_are_bounded_and_accounted_by_relation():
    prediction = _prediction("Dead Person", CITY, [])
    enum = ScriptedRuntime(
        {
            ("l7_death_status_mistral", "Dead Person", CITY): ["DECEASED confidence=0.9"],
            ("l7_death_city_direct", "Dead Person", CITY): ["CITY: Alpha"],
        },
        model_id="mistral",
        role="enumerator",
    )
    ver = ScriptedRuntime(
        {("l7_death_status_qwen", "Dead Person", CITY): ["DECEASED confidence=0.8"]},
        model_id="qwen",
        role="verifier",
    )
    config = LeaderboardRepairConfig.from_mapping({
        "enabled": True,
        "features": {"death_existence_gate": True, "death_city_recall": True},
        "max_calls_by_relation": {CITY: 3},
    })
    result = _stack(config, enum=enum, ver=ver).apply([prediction], queries=[_query(prediction)])
    assert result.records[0].calls_used == 3
    assert result.accounting["by_relation"][CITY]["max_repair_calls"] == 3
    assert any("budget exhausted" in item for item in result.records[0].skipped)


def test_profile_configs_parse_and_have_expected_features():
    profiles = {
        "cover_kbc_v3_2_profile_a_stock_probe_test.yaml": ("A_STOCK_PROBE", False),
        "cover_kbc_v3_2_profile_b_repair_core_test.yaml": ("B_REPAIR_CORE", True),
        "cover_kbc_v3_2_profile_c_aggressive_recall_test.yaml": ("C_AGGRESSIVE_RECALL", True),
    }
    for name, (profile, repair_enabled) in profiles.items():
        config = yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
        assert config["experiment"]["split"] == "test"
        assert config["test_dataset"]["rows"] == 475
        assert config["leaderboard_probe"]["enabled"] is True
        v31 = config["pipeline"]["selection"]["v3_1"]
        assert v31["aggressive"]["stock_listing_entity_prompt"] is True
        assert v31["safe"]["stock_support_dominance"] is False
        for feature in (
            "capacity_definition_prompt",
            "city_of_death_contrast_prompt",
            "award_expansion_and_fp_cap",
            "scientific_notation_acquisition",
        ):
            assert v31["aggressive"][feature] is False
        repair = config["leaderboard_repair"]
        assert repair["profile"] == profile
        assert repair["enabled"] is repair_enabled
    c = yaml.safe_load(
        (CONFIG_DIR / "cover_kbc_v3_2_profile_c_aggressive_recall_test.yaml").read_text())
    assert c["leaderboard_repair"]["max_calls_by_relation"][CITY] == 5
    assert c["leaderboard_repair"]["features"]["border_directional_sweep"] is True
    assert c["leaderboard_repair"]["features"]["award_recipient_witness"] is True


def test_hotfix_profiles_resolve_profile_a_stock_background_and_repair_flags():
    a_plus = yaml.safe_load(
        (CONFIG_DIR / "cover_kbc_v3_2_profile_a_plus_award_test.yaml").read_text())
    c2 = yaml.safe_load(
        (CONFIG_DIR / "cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml").read_text())

    for config in (a_plus, c2):
        assert config["experiment"]["split"] == "test"
        assert config["test_dataset"]["rows"] == 475
        assert config["leaderboard_probe"]["status"] == "CALIBRATION_REVIEW_LEADERBOARD_PROBE"
        v31 = config["pipeline"]["selection"]["v3_1"]
        assert v31["aggressive"]["stock_listing_entity_prompt"] is True
        assert v31["safe"]["stock_support_dominance"] is False
        for feature in (
            "capacity_definition_prompt",
            "city_of_death_contrast_prompt",
            "award_expansion_and_fp_cap",
            "scientific_notation_acquisition",
        ):
            assert v31["aggressive"][feature] is False

    a_features = a_plus["leaderboard_repair"]["features"]
    assert a_plus["leaderboard_repair"]["profile"] == "A_PLUS_AWARD"
    assert a_features["award_metadata_cleanup"] is True
    assert a_features["l8_consistency"] is False
    assert a_features["l9_final_risk_guard"] is False
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
    ):
        assert a_features[feature] is False
    assert a_plus["leaderboard_repair"]["max_calls_by_relation"][STOCK] == 0

    c2_features = c2["leaderboard_repair"]["features"]
    assert c2["leaderboard_repair"]["profile"] == "C2_AGGRESSIVE_NONSTOCK"
    for feature in (
        "stock_entity_guard",
        "stock_alias_dedupe",
        "stock_multi_listing_rescue",
        "l8_stock_consistency",
        "l9_stock_guard",
    ):
        assert c2_features[feature] is False
    for feature in (
        "award_metadata_cleanup",
        "award_recipient_witness",
        "award_time_sliced_recall",
        "border_alias_dedupe",
        "border_reciprocity",
        "border_directional_sweep",
        "death_existence_gate",
        "death_city_recall",
        "area_empty_rescue",
        "capacity_repair",
        "l8_consistency",
        "l9_final_risk_guard",
    ):
        assert c2_features[feature] is True
    caps = c2["leaderboard_repair"]["max_calls_by_relation"]
    assert caps[STOCK] == 0
    assert caps[CITY] == 5
    assert caps[AWARD] == 8


def test_c2_stock_repair_and_stock_l8_l9_are_bypassed():
    config = yaml.safe_load(
        (CONFIG_DIR / "cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml").read_text())
    repair_config = LeaderboardRepairConfig.from_mapping(config["leaderboard_repair"])
    stock = _prediction(
        "Example Co",
        STOCK,
        ["Nasdaq", "NYSE", "New York Stock Exchange", "B3"],
        row_index=0,
    )
    area = _prediction("Example Area", AREA, ["123", "not a number"], row_index=1)
    result = _stack(repair_config).apply(
        [stock, area],
        queries=[_query(stock), _query(area)],
    )

    assert result.predictions[0].object_entities == stock.object_entities
    assert result.records[0].calls_used == 0
    assert result.records[0].skipped == ["relation repair disabled by zero cap"]
    assert not result.records[0].decisions
    # Non-Stock L9 remains enabled in C2: numeric outputs are still guarded.
    assert result.predictions[1].object_entities == ["123"]


def test_profile_readiness_is_calibration_review_not_full_test_ready():
    for name in (
        "cover_kbc_v3_2_profile_a_stock_probe_test.yaml",
        "cover_kbc_v3_2_profile_b_repair_core_test.yaml",
        "cover_kbc_v3_2_profile_c_aggressive_recall_test.yaml",
        "cover_kbc_v3_2_profile_a_plus_award_test.yaml",
        "cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml",
    ):
        config = yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
        report = evaluate_test_readiness(config, base_dir=CONFIG_DIR, split="test")
        assert report.state is not ReadinessState.FULL_TEST_READY
        assert any("CALIBRATION_REVIEW_REQUIRED" in b for b in report.blockers)


def test_run_cover_leaderboard_probe_gate_allows_only_explicit_profiles():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "run_cover_leaderboard_probe_test", REPO_ROOT / "scripts" / "run_cover.py")
    run_cover = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = run_cover
    spec.loader.exec_module(run_cover)

    profile = yaml.safe_load(
        (CONFIG_DIR / "cover_kbc_v3_2_profile_a_stock_probe_test.yaml").read_text())
    readiness = evaluate_test_readiness(profile, base_dir=CONFIG_DIR, split="test")
    assert run_cover._allow_leaderboard_probe(profile, "test", readiness)

    for name in (
        "cover_kbc_v3_2_profile_a_plus_award_test.yaml",
        "cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml",
    ):
        profile = yaml.safe_load((CONFIG_DIR / name).read_text())
        readiness = evaluate_test_readiness(profile, base_dir=CONFIG_DIR, split="test")
        assert run_cover._allow_leaderboard_probe(profile, "test", readiness)

    aggressive = yaml.safe_load(
        (CONFIG_DIR / "cover_kbc_v3_1_aggressive_test.yaml").read_text())
    readiness = evaluate_test_readiness(aggressive, base_dir=CONFIG_DIR, split="test")
    assert not run_cover._allow_leaderboard_probe(aggressive, "test", readiness)


def test_run_cover_help_does_not_load_models():
    completed = subprocess.run(
        [sys.executable, "scripts/run_cover.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--config" in completed.stdout
