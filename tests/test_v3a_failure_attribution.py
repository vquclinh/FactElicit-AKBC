"""V3A failure attribution and relation profile invariants."""

from __future__ import annotations

import csv
import hashlib
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from cover_kbc.contracts import (
    RELATION_PROFILES,
    UnknownRelationProfileError,
    all_relation_profiles,
    check_profile_consistency,
    get_relation_profile,
)
from cover_kbc.controller_calibration.gold_join import load_gold
from cover_kbc.data.writer import write_predictions
from cover_kbc.diagnostics import (
    CandidateObservation,
    DiagnosticRecorder,
    FailureCategory,
    FalsePositiveCategory,
    GoldLeakageError,
    PipelineStage,
    QueryInferenceRecord,
    STAGE_ORDER,
    TELEMETRY_VERSION,
    TrainGoldAttribution,
    build_report,
)
from cover_kbc.diagnostics.failure_state import derive_failure_state
from cover_kbc.diagnostics.stages import FailureSearchState
from cover_kbc.elicitation.parsing import acquisition_fragments
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.staging import read_stage, write_stage
from cover_kbc.types import CandidateStatus, Query

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cover_kbc"
EXPERIMENTS = ROOT / "configs" / "experiments"
CALIBRATION = ROOT / "configs" / "calibration"

TRAIN_SHA256 = "ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e"
VAL_SHA256 = "ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be"
TEST_SHA256 = "67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1"
EVALUATOR_SHA256 = "2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


def _gold(tmp_path: Path, rows: list[dict]):
    path = _write_jsonl(tmp_path / "gold.jsonl", rows)
    return path, load_gold(path, expected_rows=len(rows))


def _record(
    subject: str,
    relation: str,
    *,
    row_index: int = 0,
    stages: dict[PipelineStage, tuple[str, ...]] | None = None,
    split: str = "train",
    observable: bool = True,
    candidates: tuple[CandidateObservation, ...] = (),
    calls: int = 0,
    generated_tokens: int = 0,
    verification_calls: int = 0,
    failure_state: str = "",
) -> QueryInferenceRecord:
    return QueryInferenceRecord(
        telemetry_version=TELEMETRY_VERSION,
        run_id="test",
        split=split,
        subject=subject,
        relation=relation,
        row_index=row_index,
        program_type="",
        relation_profile={},
        gate_negative=False,
        gate_reason="",
        empty_reason="NOT_EMPTY",
        stopped_reason="test",
        calls_used=calls,
        generated_tokens_used=generated_tokens,
        prompt_tokens_used=0,
        verification_calls=verification_calls,
        stage_values={stage.value: values for stage, values in (stages or {}).items()},
        candidates=candidates,
        generations=(),
        actions=(),
        failure_state=failure_state,
        observable=observable,
    )


def _candidate(
    value: str,
    *,
    status: CandidateStatus = CandidateStatus.ACCEPTED,
    label: str = "",
    verification_count: int = 0,
    hard_rejected: bool = False,
    support: int = 1,
    emitted: bool = False,
) -> CandidateObservation:
    return CandidateObservation(
        candidate_key=value.casefold(),
        display_value=value,
        output_value=value,
        numeric_value=None,
        surface_forms=(value,),
        acquisition_groups=("DIRECT",),
        independent_support=support,
        raw_support_count=support,
        facet_ids=("direct",),
        record_ids=("r1",),
        hard_rejected=hard_rejected,
        rejection_reason=("hard" if hard_rejected else ""),
        verification_count=verification_count,
        verifier_label=label,
        verifier_valid_prob=(0.9 if label else None),
        final_status=status.value,
        tier="",
        score=1.0,
        emitted=emitted,
    )


def _attributor(gold_index):
    return TrainGoldAttribution(gold_index)


def test_relation_profiles_are_exactly_the_six_official_profiles() -> None:
    expected = {
        "hasArea": ("NUMERIC_SINGLE", "MISSING_RECALL", "AREA_DEFINITION",
                    "SINGLE", "MULTI_VIEW", "NUMERIC_CONTRAST", "NEUTRAL", False),
        "hasCapacity": ("NUMERIC_SINGLE", "MEMORY_AMBIGUITY", "CAPACITY_VARIANT",
                        "SINGLE", "MULTI_VIEW_DEFINITION_AWARE",
                        "DEFINITION_CONTRAST", "NEUTRAL", False),
        "companyTradesAtStockExchange": (
            "ENTITY_SET", "FALSE_POSITIVE", "OWNERSHIP_LISTING", "SMALL_SET",
            "BASELINE", "REJECTION_FIRST", "ELIMINATION", False),
        "personHasCityOfDeath": (
            "ENTITY_SINGLE", "ATTRIBUTE_CONFUSION", "RELATED_LOCATION",
            "ZERO_OR_ONE", "ATTRIBUTE_CONTRAST", "SEMANTIC", "NEUTRAL", False),
        "awardWonBy": ("ENTITY_SET", "INCOMPLETE_SET", "SET_COMPLETENESS",
                       "OPEN_SET", "PROMOTE_SUPPRESS_ITERATE", "UNARY",
                       "EXPANSION", False),
        "countryLandBordersCountry": (
            "STRUCTURAL_SET", "LOW", "LAND_BORDER_SCOPE", "SMALL_SET",
            "BASELINE_CONSERVATIVE", "BASELINE", "NEUTRAL", True),
    }
    assert set(RELATION_PROFILES) == set(expected)
    for relation, values in expected.items():
        profile = get_relation_profile(relation)
        assert (
            profile.family.value,
            profile.primary_failure.value,
            profile.semantic_risk.value,
            profile.set_behavior.value,
            profile.recall_policy.value,
            profile.verification_policy.value,
            profile.search_bias.value,
            profile.frozen_conservative,
        ) == values


def test_relation_profiles_are_deterministic_and_fail_closed() -> None:
    first = [p.to_json() for p in all_relation_profiles()]
    second = [p.to_json() for p in all_relation_profiles()]
    assert first == second
    with pytest.raises(UnknownRelationProfileError):
        get_relation_profile("unknownRelation")


def test_relation_profiles_are_consistent_with_m0_and_zero_neural_calls() -> None:
    runtime = ScriptedRuntime({})
    check_profile_consistency()
    _ = [get_relation_profile(name) for name in RELATION_PROFILES]
    assert runtime.calls == 0


def test_relation_profiles_are_not_consumed_by_v2_scoring_or_selection() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        if "contracts" in path.parts or "diagnostics" in path.parts:
            continue
        if "v3_core" in path.parts:
            continue
        if path.name == "pipeline.py":
            # V3 core is opt-in and observed after M8. Baseline V2 scoring and
            # selection must remain profile-free.
            continue
        text = path.read_text(encoding="utf-8")
        if "get_relation_profile" in text or "route_profile" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_failure_state_is_deterministic_and_shadow_only() -> None:
    record = _record(
        "S", "hasCapacity",
        stages={PipelineStage.NORMALIZED: ("100",)},
        candidates=(_candidate("100", status=CandidateStatus.UNRESOLVED),),
    )
    profile = get_relation_profile("hasCapacity")
    assert derive_failure_state(record, profile) is derive_failure_state(record, profile)
    control_source = "\n".join(p.read_text(encoding="utf-8")
                               for p in (SRC / "control").rglob("*.py"))
    assert "FailureSearchState" not in control_source
    assert "derive_failure_state" not in control_source


def test_inference_telemetry_has_no_gold_field_or_api() -> None:
    for cls in (QueryInferenceRecord, CandidateObservation):
        assert not any("gold" in name.lower() for name in cls.__dataclass_fields__)
    signature = inspect.signature(DiagnosticRecorder.observe)
    assert "gold" not in str(signature).lower()
    assert "ObjectEntities" not in QueryInferenceRecord.__dataclass_fields__


def test_acquired_entity_fragments_do_not_split_arbitrary_prose(borders_contract) -> None:
    text = "I think the answer is probably Poland because the long explanation mentions Germany too."
    assert acquisition_fragments(text, borders_contract) == []
    assert acquisition_fragments("Poland; Germany; UNKNOWN", borders_contract) == [
        "Poland", "Germany"]


def test_diagnostics_on_off_produces_identical_predictions_and_accounting(tmp_path) -> None:
    query = Query("Testland", "countryLandBordersCountry", 0)
    script = {
        ("borders_direct", "Testland", "countryLandBordersCountry"):
            ["Germany; Poland; Unknown"],
        ("borders_compass", "Testland", "countryLandBordersCountry"):
            ["Poland; Germany"],
    }

    def run(enabled: bool):
        runtime = ScriptedRuntime(script)
        recorder = DiagnosticRecorder(run_id="r", split="train") if enabled else None
        pipeline = CoverPipeline(runtime, PipelineConfig(), diagnostics=recorder)
        result = pipeline.run([query])
        return result, recorder

    off, off_recorder = run(False)
    on, on_recorder = run(True)
    assert off_recorder is None
    assert on_recorder is not None and len(on_recorder.records) == 1
    assert [p.to_official_row() for p in off.predictions] == [
        p.to_official_row() for p in on.predictions]
    assert [p.stopped_reason for p in off.predictions] == [
        p.stopped_reason for p in on.predictions]
    assert [p.empty_reason for p in off.predictions] == [
        p.empty_reason for p in on.predictions]
    assert off.total_calls == on.total_calls
    assert off.total_generated_tokens == on.total_generated_tokens
    assert off.total_prompt_tokens == on.total_prompt_tokens

    off_path = write_predictions(off.predictions, tmp_path / "off.jsonl",
                                 expected_queries=[query])
    on_path = write_predictions(on.predictions, tmp_path / "on.jsonl",
                                expected_queries=[query])
    assert off_path.read_bytes() == on_path.read_bytes()

    stages = on_recorder.records[0].stage_values
    assert stages[PipelineStage.ACQUIRED.value] == ("Germany", "Poland")
    assert stages[PipelineStage.VERIFIER_REACHED.value] == ()
    assert stages[PipelineStage.CONTROL_SURVIVED.value] == ("Germany", "Poland")


def test_run_query_records_exactly_one_diagnostic_row() -> None:
    query = Query("Testland", "countryLandBordersCountry", 0)
    runtime = ScriptedRuntime({
        ("borders_direct", "Testland", "countryLandBordersCountry"):
            ["Germany; Poland"],
        ("borders_compass", "Testland", "countryLandBordersCountry"):
            ["Poland; Germany"],
    })
    recorder = DiagnosticRecorder(run_id="single", split="train")
    prediction = CoverPipeline(
        runtime, PipelineConfig(), diagnostics=recorder).run_query(query)

    assert len(recorder.records) == 1
    assert recorder.records[0].emitted == tuple(prediction.object_entities)


def test_staged_decide_records_once_and_enumerate_records_nothing(tmp_path) -> None:
    query = Query("Testland", "countryLandBordersCountry", 0)
    script = {
        ("borders_direct", "Testland", "countryLandBordersCountry"):
            ["Germany; Poland"],
        ("borders_compass", "Testland", "countryLandBordersCountry"):
            ["Poland; Germany"],
    }
    enumerate_recorder = DiagnosticRecorder(run_id="stage-a", split="train")
    stage_a = write_stage(
        CoverPipeline(
            ScriptedRuntime(script), PipelineConfig(),
            diagnostics=enumerate_recorder).enumerate([query]),
        tmp_path / "stage_a.jsonl",
    )
    assert enumerate_recorder.records == []

    decide_recorder = DiagnosticRecorder(run_id="stage-c", split="train")
    result = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(),
        diagnostics=decide_recorder).decide(read_stage(stage_a))

    assert len(result.predictions) == 1
    assert len(decide_recorder.records) == 1
    assert decide_recorder.records[0].emitted == tuple(
        result.predictions[0].object_entities)


def test_train_attribution_refuses_val_and_blind_gold(tmp_path) -> None:
    _, gold = _gold(tmp_path, [
        {"SubjectEntity": "S", "Relation": "awardWonBy",
         "ObjectEntities": [["A"]]},
    ])
    with pytest.raises(GoldLeakageError):
        TrainGoldAttribution(gold, split="val")

    _, blind = _gold(tmp_path, [
        {"SubjectEntity": "S", "Relation": "awardWonBy", "ObjectEntities": []},
    ])
    with pytest.raises(GoldLeakageError):
        TrainGoldAttribution(blind)


def test_object_level_set_survival_is_not_collapsed_to_any_gold(tmp_path) -> None:
    _, gold = _gold(tmp_path, [
        {"SubjectEntity": "Prize", "Relation": "awardWonBy",
         "ObjectEntities": [["A"], ["B"], ["C"]]},
    ])
    record = _record(
        "Prize", "awardWonBy",
        stages={PipelineStage.ACQUIRED: ("A", "C", "X")},
    )
    attribution = _attributor(gold).attribute(record)
    survival = attribution.stage(PipelineStage.ACQUIRED)
    assert survival.gold_count == 3
    assert survival.gold_present_count == 2
    assert survival.gold_fraction == pytest.approx(2 / 3)


def test_empty_gold_handling_has_no_recall_division_by_zero(tmp_path) -> None:
    _, gold = _gold(tmp_path, [
        {"SubjectEntity": "Empty", "Relation": "personHasCityOfDeath",
         "ObjectEntities": []},
        {"SubjectEntity": "Full", "Relation": "personHasCityOfDeath",
         "ObjectEntities": [["A"]]},
    ])
    attribution = _attributor(gold).attribute(
        _record("Empty", "personHasCityOfDeath"))
    report = build_report([attribution], split="train").relations[0]
    assert report.total_gold_objects == 0
    assert report.empty_gold.query_count == 1
    assert report.oracle_candidate_recall_micro == 0.0
    assert report.oracle_candidate_recall_macro == 0.0


def test_oracle_candidate_recall_micro_and_macro_are_object_level(tmp_path) -> None:
    _, gold = _gold(tmp_path, [
        {"SubjectEntity": "Prize1", "Relation": "awardWonBy",
         "ObjectEntities": [["A"], ["B"], ["C"]]},
        {"SubjectEntity": "Prize2", "Relation": "awardWonBy",
         "ObjectEntities": [["D"]]},
    ])
    attrs = _attributor(gold).attribute_all([
        _record("Prize1", "awardWonBy",
                stages={PipelineStage.ACQUIRED: ("A", "C", "X")}),
        _record("Prize2", "awardWonBy",
                stages={PipelineStage.NORMALIZED: ("D",)}),
    ])
    relation = build_report(attrs, split="train").relations[0]
    assert relation.oracle_candidate_recall_micro == pytest.approx(3 / 4)
    assert relation.oracle_candidate_recall_macro == pytest.approx(((2 / 3) + 1) / 2)


@pytest.mark.parametrize(
    "stages,expected",
    [
        ({}, FailureCategory.NEVER_ACQUIRED),
        ({PipelineStage.ACQUIRED: ("A",)}, FailureCategory.LOST_IN_NORMALIZATION),
        ({
            PipelineStage.ACQUIRED: ("A",),
            PipelineStage.NORMALIZED: ("A",),
        }, FailureCategory.NOT_SENT_TO_VERIFIER),
        ({
            PipelineStage.ACQUIRED: ("A",),
            PipelineStage.NORMALIZED: ("A",),
            PipelineStage.VERIFIER_REACHED: ("A",),
        }, FailureCategory.REJECTED_BY_VERIFIER),
        ({
            PipelineStage.ACQUIRED: ("A",),
            PipelineStage.NORMALIZED: ("A",),
            PipelineStage.VERIFIER_REACHED: ("A",),
            PipelineStage.VERIFIER_ACCEPTED: ("A",),
        }, FailureCategory.DROPPED_AFTER_VERIFICATION),
        ({
            PipelineStage.ACQUIRED: ("A",),
            PipelineStage.NORMALIZED: ("A",),
            PipelineStage.VERIFIER_REACHED: ("A",),
            PipelineStage.VERIFIER_ACCEPTED: ("A",),
            PipelineStage.CONTROL_SURVIVED: ("A",),
        }, FailureCategory.DROPPED_BY_FINAL_SELECTION),
        ({
            PipelineStage.ACQUIRED: ("A",),
            PipelineStage.NORMALIZED: ("A",),
            PipelineStage.VERIFIER_REACHED: ("A",),
            PipelineStage.VERIFIER_ACCEPTED: ("A",),
            PipelineStage.CONTROL_SURVIVED: ("A",),
            PipelineStage.FINAL_EMITTED: ("A",),
        }, FailureCategory.SUCCESSFULLY_EMITTED),
    ],
)
def test_failure_categories_follow_observable_stage_boundaries(
    tmp_path, stages, expected,
) -> None:
    _, gold = _gold(tmp_path, [
        {"SubjectEntity": "S", "Relation": "awardWonBy",
         "ObjectEntities": [["A"]]},
    ])
    attribution = _attributor(gold).attribute(
        _record("S", "awardWonBy", stages=stages))
    assert attribution.failures[0] is expected


def test_false_positive_attribution_is_deterministic(tmp_path) -> None:
    _, gold = _gold(tmp_path, [
        {"SubjectEntity": "S", "Relation": "awardWonBy",
         "ObjectEntities": [["A"]]},
    ])
    record = _record("S", "awardWonBy", stages={
        stage: ("X",) for stage in STAGE_ORDER
    })
    first = _attributor(gold).attribute(record)
    second = _attributor(gold).attribute(record)
    assert first.false_positives == second.false_positives
    assert first.false_positives[FalsePositiveCategory.FINAL_FP.value] == 1


def test_unobservable_error_does_not_fabricate_never_acquired(tmp_path) -> None:
    _, gold = _gold(tmp_path, [
        {"SubjectEntity": "S", "Relation": "awardWonBy",
         "ObjectEntities": [["A"]]},
    ])
    attribution = _attributor(gold).attribute(
        _record("S", "awardWonBy", observable=False))
    assert attribution.failures[0] is FailureCategory.NOT_OBSERVABLE


def test_analyzer_emits_json_markdown_and_csv(tmp_path) -> None:
    rows = [
        {"SubjectEntity": "S", "Relation": "awardWonBy",
         "ObjectEntities": [["A"]]},
    ]
    gold_path = _write_jsonl(tmp_path / "gold.jsonl", rows)
    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text(json.dumps(_record(
        "S", "awardWonBy",
        stages={PipelineStage.FINAL_EMITTED: ("A",)},
        calls=3, generated_tokens=7, verification_calls=1,
        failure_state=FailureSearchState.STABLE_VERIFIED.value,
    ).to_json()) + "\n", encoding="utf-8")
    out = tmp_path / "report"
    subprocess.run([
        sys.executable, str(ROOT / "scripts" / "analyze_failure_attribution.py"),
        "--telemetry", str(telemetry),
        "--output-dir", str(out),
        "--gold-path", str(gold_path),
        "--expected-train-rows", str(len(rows)),
        "--expected-train-sha256", _sha(gold_path),
    ], cwd=ROOT, check=True)
    payload = json.loads((out / "failure_attribution.json").read_text())
    assert payload["split"] == "train"
    assert (out / "failure_attribution.md").read_text().startswith(
        "# V3A failure attribution")
    with (out / "failure_attribution.csv").open(newline="", encoding="utf-8") as handle:
        table = list(csv.DictReader(handle))
    assert table[0]["Relation"] == "awardWonBy"
    assert "observable_query_count" in table[0]


def test_analyzer_refuses_non_train_telemetry(tmp_path) -> None:
    rows = [
        {"SubjectEntity": "S", "Relation": "awardWonBy",
         "ObjectEntities": [["A"]]},
    ]
    gold_path = _write_jsonl(tmp_path / "gold.jsonl", rows)
    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text(json.dumps(_record(
        "S", "awardWonBy", split="val").to_json()) + "\n", encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "analyze_failure_attribution.py"),
        "--telemetry", str(telemetry),
        "--output-dir", str(tmp_path / "report"),
        "--gold-path", str(gold_path),
        "--expected-train-rows", str(len(rows)),
        "--expected-train-sha256", _sha(gold_path),
    ], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 2
    assert "refused" in result.stderr


def test_train_diagnostic_config_is_ready_for_the_current_baseline() -> None:
    from cover_kbc.controller_calibration.readiness import (
        ReadinessState,
        evaluate_train_diagnostic_readiness,
    )

    path = EXPERIMENTS / "cover_kbc_v2_train_diagnostic_v3a.yaml"
    config = yaml.safe_load(path.read_text())
    report = evaluate_train_diagnostic_readiness(config, base_dir=path.parent)
    assert report.state is ReadinessState.TRAIN_DIAGNOSTIC_READY, report.blockers
    assert report.may_run_train_diagnostic


def test_train_diagnostic_readiness_refuses_val_test_and_v3b() -> None:
    from cover_kbc.controller_calibration.readiness import (
        evaluate_train_diagnostic_readiness,
    )

    path = EXPERIMENTS / "cover_kbc_v2_train_diagnostic_v3a.yaml"
    config = yaml.safe_load(path.read_text())
    assert not evaluate_train_diagnostic_readiness(
        config, base_dir=path.parent, split="val").may_run_train_diagnostic
    assert not evaluate_train_diagnostic_readiness(
        config, base_dir=path.parent, split="test").may_run_train_diagnostic
    config["v3b_features"] = {"multi_view_parametric_recall": True}
    report = evaluate_train_diagnostic_readiness(config, base_dir=path.parent)
    assert not report.may_run_train_diagnostic
    assert any("V3B" in blocker for blocker in report.blockers)


def test_model_pair_revisions_and_parameter_total_remain_unchanged() -> None:
    path = EXPERIMENTS / "cover_kbc_v2_train_diagnostic_v3a.yaml"
    config = yaml.safe_load(path.read_text())
    profile = config["model_profile"]
    assert profile["enumerator"]["model_id"] == (
        "mistralai/Mistral-Small-3.2-24B-Instruct-2506")
    assert profile["enumerator"]["revision"] == (
        "95a6d26c4bfb886c58daf9d3f7332c857cb27b43")
    assert profile["verifier"]["model_id"] == "Qwen/Qwen3.5-4B"
    assert profile["verifier"]["revision"] == (
        "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a")
    assert config["budget_assertion"]["total_published_parameters"] == 28_671_226_368


def test_m20_m21_and_benchmark_hashes_remain_unchanged() -> None:
    assert _sha(CALIBRATION / "m20_relation_budget.json") == (
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68")
    assert _sha(CALIBRATION / "m21_historical_bins.json") == (
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071")
    assert _sha(CALIBRATION / "m21_planner_calibration.json") == (
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05")
    assert _sha(ROOT / "benchmark" / "evaluate.py") == EVALUATOR_SHA256
    assert _sha(ROOT / "benchmark" / "data" / "train.jsonl") == TRAIN_SHA256
    assert _sha(ROOT / "benchmark" / "data" / "val.jsonl") == VAL_SHA256
    assert _sha(ROOT / "benchmark" / "data" / "test.jsonl") == TEST_SHA256


def test_test_split_remains_blind_and_portfolio_direct_paths_absent() -> None:
    test_rows = [
        json.loads(line)
        for line in (ROOT / "benchmark" / "data" / "test.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert all(not row.get("ObjectEntities") for row in test_rows)
    assert list(EXPERIMENTS.glob("*portfolio*")) == []
    executable = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (SRC, ROOT / "scripts")
        for path in root.rglob("*.py")
    )
    for token in ("DIRECT_UNCALIBRATED", "ModelStrategy", "model_strategy"):
        assert token not in executable
