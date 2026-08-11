"""Audit 0081 phase A: the stock diagnostic is isolated, executable and analysable.

Two lessons are encoded here.

From audit 0080's *first* attempt: static config assertions are not enough, so
the executability checks call the runner's own gate functions.

From audit 0080's *result*: a restrictive prompt can raise precision purely by
answering less. Stock is a suppression experiment, so the analyzer must be able
to tell a removed false exchange from a removed true one - and these tests drive
that classifier over constructed rows rather than trusting it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"
STOCK = "companyTradesAtStockExchange"
CONFIG = CONFIG_DIR / "v3_1_diag_stock.yaml"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "0081-stock-diagnostic-colab.md"

#: Measured from source in audit 0081 phase A, then pinned.
STOCK_BODY_SHA = "39af1c5be480c85350535f75ddd2466b497d1b124bb4e3b97c63b21bda1c39f8"
SYSTEM_PROMPT_OFF = "2fb9188dbeda44f3"
SYSTEM_PROMPT_ON = "849a83f0c40440d1"
BASELINE_PREDICTIONS_SHA = (
    "36d2b079e0a732fcce7e1f2655f96dc3d7606382d54825918dbedc8098472816")


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / name
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


analyzer = _load_script("analyze_stock_diagnostic.py")
runner = _load_script("run_cover.py")


def _evaluator():
    path = REPO_ROOT / "benchmark" / "evaluate.py"
    spec = importlib.util.spec_from_file_location("official_evaluate_stock_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EV = _evaluator()


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1. Exactly one causal intervention, on a SAFE_CORE background
# --------------------------------------------------------------------------


def test_only_the_stock_class_b_feature_is_enabled():
    from cover_kbc.v3_1.config import V31Config

    block = V31Config.from_mapping(_config()["pipeline"]["selection"]["v3_1"])
    assert block.aggressive.enabled_features == ("stock_listing_entity_prompt",)
    assert block.calibration_status == "CALIBRATION_REVIEW_REQUIRED"


@pytest.mark.parametrize("feature", [
    "capacity_definition_prompt", "city_of_death_contrast_prompt",
    "award_expansion_and_fp_cap", "scientific_notation_acquisition",
])
def test_every_other_class_b_feature_is_off(feature):
    from cover_kbc.v3_1.config import V31Config

    block = V31Config.from_mapping(_config()["pipeline"]["selection"]["v3_1"])
    assert getattr(block.aggressive, feature) is False


def test_stock_support_dominance_is_off():
    """Audit 0081 section 6: the gate is written against the SAFE_CORE baseline.

    Unlike the capacity diagnostic, where both stock features were unreachable,
    support dominance is live here - audit 0076 measured it changing 22 stock
    rows. Leaving it on would compare unlike systems.
    """
    from cover_kbc.v3_1.config import V31Config

    block = V31Config.from_mapping(_config()["pipeline"]["selection"]["v3_1"])
    assert block.safe.stock_support_dominance is False


def test_the_background_is_exactly_safe_core():
    from cover_kbc.v3_1.config import V31Config

    diag = V31Config.from_mapping(_config()["pipeline"]["selection"]["v3_1"])
    core = V31Config.from_mapping(
        yaml.safe_load((CONFIG_DIR / "cover_kbc_v3_1_safe_core_test.yaml").read_text())
        ["pipeline"]["selection"]["v3_1"])
    assert set(diag.safe.enabled_features) == set(core.safe.enabled_features)


# --------------------------------------------------------------------------
# 2. TRAIN-only, executable, no TEST
# --------------------------------------------------------------------------


def test_config_is_a_train_only_targeted_diagnostic():
    config = _config()
    assert config["experiment"]["split"] == "train"
    assert config["experiment"]["relation_filter"] == [STOCK]
    assert config["experiment"]["expected_rows"] == 100
    assert config["experiment"]["diagnostic"] is True
    assert (config.get("diagnostics") or {}).get("enabled") is True
    assert "train_dataset" in config
    assert "test_dataset" not in config


def test_real_runner_routes_it_to_the_train_diagnostic_gate():
    gate, required = runner.resolve_production_gate(_config(), "train", CONFIG)
    assert gate is runner.TRAIN_DIAGNOSTIC_GATE[0]
    assert required is runner.TRAIN_DIAGNOSTIC_GATE[1]


def test_real_runner_readiness_passes_without_models():
    from cover_kbc.controller_calibration.readiness import ReadinessState

    readiness, required = runner.evaluate_production_readiness(
        _config(), "train", CONFIG)
    assert readiness.state is required, readiness.blockers
    assert readiness.state is ReadinessState.TRAIN_DIAGNOSTIC_READY


def test_relation_filter_resolves_exactly_one_hundred_stock_rows():
    from cover_kbc.data.loader import load_dataset

    wanted = runner._resolve_relation_filter(None, _config()["experiment"], "train")
    assert wanted == frozenset({STOCK})
    rows = [q for q in load_dataset("train").queries() if q.relation in wanted]
    assert len(rows) == 100


def test_test_readiness_remains_not_ready():
    from cover_kbc.controller_calibration.readiness import (
        ReadinessState, evaluate_test_readiness)

    report = evaluate_test_readiness(_config(), base_dir=CONFIG_DIR, split="test")
    assert report.state is not ReadinessState.FULL_TEST_READY


def test_production_execution_semantics_are_preserved():
    config = _config()
    assert config["pipeline"]["mode"] == "interleaved"
    assert config["pipeline"]["v3_core"]["mode"] == "production"
    assert config["relation_budget_scheduler"]["mode"] == "production"
    assert config["micro_planner"]["mode"] == "production"


# --------------------------------------------------------------------------
# 2b. Colab runbook operator gates
# --------------------------------------------------------------------------


def test_runbook_chronology_inherits_audit_0080_preflight_without_stock_attempt():
    source = _runbook()
    flattened = " ".join(source.split())
    assert "The first attempt at this experiment loaded 28.7B parameters" not in source
    assert "first analysis of this experiment" not in source
    assert (
        "Audit 0080's first real diagnostic attempt exposed the TRAIN-diagnostic "
        "execution-gate problem only after the 28.7B weights had loaded"
    ) in flattened
    assert (
        "Audit 0081 inherits the fix: CELL 9b runs the runner's own preflight "
        "before any Stock weights are downloaded"
    ) in flattened


def test_runbook_cell_10_preserves_process_return_code_and_relation_filter_gate():
    source = _runbook()
    assert "subprocess.Popen(" in source
    assert "stderr=subprocess.STDOUT" in source
    assert "return_code = proc.wait()" in source
    assert "assert return_code == 0" in source
    assert "relation_filter_rows is not None" in source
    assert "relation_filter_rows == 100" in source
    assert "PROCESS-LEVEL GATE: PASS" in source
    assert "'--output-dir', OUT" in source
    assert "2>&1 | tee" not in source
    assert "!python scripts/run_cover.py" not in source


def test_runbook_cell_12b_resolves_the_authoritative_baseline_first():
    source = _runbook()
    authoritative = (
        "/content/drive/MyDrive/AKBC/Submissions-AKBC/"
        "v3_train_collect_v2_coverage_a11d75d2_20260809T232041Z/")
    fallback = "Path(DRIVE_ROOT) / 'baseline' / BASELINE_RUN_NAME"
    assert authoritative in source
    assert source.index(authoritative) < source.index(fallback)
    assert BASELINE_PREDICTIONS_SHA in source
    assert "matches = [path for path, digest in observed if digest == BASELINE_SHA256]" in source
    assert "assert len(matches) == 1" in source
    assert "Do not rerun the baseline" in source
    assert "BASELINE ABSENT" not in source


# --------------------------------------------------------------------------
# 3. Prompt binding
# --------------------------------------------------------------------------


def _render(block):
    from cover_kbc.contracts.registry import CONTRACTS
    from cover_kbc.elicitation.engine import ElicitationEngine
    from cover_kbc.elicitation.library import views_for
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.v3_1.live_prompts import RelationInstructions

    stub = ScriptedRuntime(fallback=lambda request: "NONE", model_id="offline",
                           family="m", role="enumerator")
    engine = ElicitationEngine(
        stub, relation_instructions=RelationInstructions.from_config(block))
    contract = CONTRACTS[STOCK]
    view = views_for(STOCK, contract.mandatory_views)[0]
    return engine.system_prompt_for(view), view.render(
        subject="Example Holdings Limited", definition=contract.verifier_definition())


def test_stock_instruction_body_hash_is_unchanged():
    from cover_kbc.v3_1.prompts import STOCK_LISTING_ENTITY

    assert STOCK_LISTING_ENTITY.sha256 == STOCK_BODY_SHA


def test_rendered_system_prompt_differs_off_versus_on():
    from cover_kbc.elicitation.engine import prompt_hash
    from cover_kbc.v3_1.config import V31Config

    block = V31Config.from_mapping(_config()["pipeline"]["selection"]["v3_1"])
    off_system, _ = _render(None)
    on_system, _ = _render(block)
    assert off_system != on_system, "INVALID EXPERIMENT: rendered prompt identical"
    assert prompt_hash(off_system) == SYSTEM_PROMPT_OFF
    assert prompt_hash(on_system) == SYSTEM_PROMPT_ON


def test_user_prompt_is_unchanged_because_the_binding_is_system_side():
    from cover_kbc.v3_1.config import V31Config

    block = V31Config.from_mapping(_config()["pipeline"]["selection"]["v3_1"])
    _, off_user = _render(None)
    _, on_user = _render(block)
    assert off_user == on_user


def test_no_cross_relation_prompt_leakage():
    from cover_kbc.contracts.registry import CONTRACTS
    from cover_kbc.v3_1.config import V31Config
    from cover_kbc.v3_1.live_prompts import RelationInstructions

    block = V31Config.from_mapping(_config()["pipeline"]["selection"]["v3_1"])
    instructions = RelationInstructions.from_config(block)
    for relation in CONTRACTS:
        changed = bool(instructions.enumerator_instruction(relation))
        assert changed is (relation == STOCK), relation
        assert not instructions.verifier_boundary(relation)


def test_the_instruction_carries_no_benchmark_answer():
    import re

    from cover_kbc.v3_1.prompts import STOCK_LISTING_ENTITY

    body = STOCK_LISTING_ENTITY.body
    assert not re.search(r"\d{4,}", body)
    for named in ("Nasdaq", "NYSE", "Euronext", "Tokyo Stock", "London Stock"):
        assert named not in body


# --------------------------------------------------------------------------
# 4. Analyzer provenance (audit 0080's corrected rule)
# --------------------------------------------------------------------------


def _run_dir(tmp_path: Path, *, git_revision="a" * 40, rows=100, relation=STOCK,
             errors=None, accounting=None) -> Path:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    manifest = {"cover_kbc_version": "0.1.0", "run_id": "probe"}
    if git_revision is not None:
        manifest["git_revision"] = git_revision
    (run / "manifest.json").write_text(json.dumps(manifest))
    with (run / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(rows):
            handle.write(json.dumps({
                "SubjectEntity": f"Company {index}", "Relation": relation,
                "ObjectEntities": ["Alpha Exchange"]}) + "\n")
    if errors:
        (run / "errors.json").write_text(json.dumps(errors))
    (run / "run_accounting.json").write_text(json.dumps(accounting or {
        "total_queries": rows, "prediction_rows": rows, "successful_queries": rows,
        "failed_queries": 0, "unresolved_invariant_errors": 0,
        "pipeline_error_rows": 0}))
    return run


def test_analyzer_uses_git_revision_for_provenance(tmp_path):
    record = analyzer.check_provenance(_run_dir(tmp_path), "a" * 40, strict=False)
    assert record["valid"], record["problems"]
    assert record["manifest_git_revision"] == "a" * 40


def test_analyzer_rejects_a_missing_git_revision(tmp_path):
    record = analyzer.check_provenance(
        _run_dir(tmp_path, git_revision=None), "a" * 40, strict=False)
    assert not record["valid"]
    assert any("no git_revision" in p for p in record["problems"])


def test_analyzer_rejects_a_wrong_git_revision(tmp_path):
    record = analyzer.check_provenance(
        _run_dir(tmp_path, git_revision="b" * 40), "a" * 40, strict=False)
    assert not record["valid"]


def test_analyzer_never_reads_the_package_version_as_provenance():
    source = (REPO_ROOT / "scripts" / "analyze_stock_diagnostic.py").read_text(
        encoding="utf-8")
    assert 'observed_sha = str(manifest.get("git_revision")' in source
    assert 'observed_sha = str(manifest.get("cover_kbc_version")' not in source


def test_analyzer_requires_exactly_one_hundred_rows(tmp_path):
    record = analyzer.check_provenance(_run_dir(tmp_path, rows=99), "a" * 40,
                                       strict=False)
    assert not record["valid"]
    assert any("prediction rows" in p for p in record["problems"])


def test_analyzer_rejects_a_foreign_relation(tmp_path):
    record = analyzer.check_provenance(
        _run_dir(tmp_path, relation="hasArea"), "a" * 40, strict=False)
    assert not record["valid"]
    assert any(f"expected only {STOCK}" in p for p in record["problems"])


def test_analyzer_rejects_contaminated_accounting(tmp_path):
    record = analyzer.check_provenance(_run_dir(tmp_path, accounting={
        "total_queries": 100, "prediction_rows": 100, "successful_queries": 99,
        "failed_queries": 1, "unresolved_invariant_errors": 1,
        "pipeline_error_rows": 1}), "a" * 40, strict=False)
    assert not record["valid"]
    assert any("failed_queries" in p for p in record["problems"])


# --------------------------------------------------------------------------
# 5. The classifier - the load-bearing part
# --------------------------------------------------------------------------


def _score(predicted, gold_objects):
    gold_row = {"ObjectEntities": [[g] for g in gold_objects]}
    return analyzer.score_row(EV, predicted, gold_row)


def _classify(predicted_before, predicted_after, gold_objects,
              gold_lost=0, gold_added=0, false_removed=0, false_added=0):
    before = _score(predicted_before, gold_objects)
    after = _score(predicted_after, gold_objects)
    return analyzer.classify(before, after, len(gold_objects),
                             gold_lost, gold_added, false_removed, false_added)


def test_official_matching_semantics_are_reused():
    """Alias groups are matched the evaluator's way, not by a second matcher."""
    gold_row = {"ObjectEntities": [["London Stock Exchange", "LSE"]]}
    assert analyzer.score_row(EV, ["LSE"], gold_row)["tp"] == 1
    assert analyzer.score_row(EV, ["London Stock Exchange"], gold_row)["tp"] == 1
    # one gold entity absorbs at most one prediction
    both = analyzer.score_row(EV, ["LSE", "London Stock Exchange"], gold_row)
    assert both["tp"] == 1 and both["fp"] == 1


def test_good_fp_suppression_is_recognised():
    assert _classify(["Alpha", "Beta"], ["Alpha"], ["Alpha"],
                     false_removed=1) == "GOLD_PRESERVED_FP_REDUCED"


def test_empty_gold_suppression_is_recognised():
    assert _classify(["Alpha"], [], [], false_removed=1) == "GOOD_EMPTY_GOLD_SUPPRESSION"


def test_destructive_abstention_is_recognised():
    """A non-empty-gold row emptied: the capacity failure mode."""
    assert _classify(["Alpha"], [], ["Alpha"], gold_lost=1) == "DESTRUCTIVE_ABSTENTION"


def test_multi_listing_collapse_is_recognised():
    assert _classify(["Alpha", "Beta"], ["Alpha"], ["Alpha", "Beta"],
                     gold_lost=1) == "MULTI_LISTING_COLLAPSE"


def test_multi_listing_preserved_is_recognised():
    assert _classify(["Alpha", "Beta", "Gamma"], ["Alpha", "Beta"],
                     ["Alpha", "Beta"], false_removed=1) == "MULTI_LISTING_PRESERVED"


def test_gold_candidate_lost_is_recognised():
    """A gold lost without collapsing the set to one.

    Three gold listings down to two is a recall loss; three down to *one* is a
    collapse, which is the more specific and more damning label and is tested
    separately below. Precedence is deliberate: the classifier names the most
    destructive shape that fits.
    """
    assert _classify(["Alpha", "Beta", "Gamma"], ["Alpha", "Beta"],
                     ["Alpha", "Beta", "Gamma"], gold_lost=1) == "GOLD_CANDIDATE_LOST"


def test_a_multi_gold_row_falling_to_one_is_a_collapse_not_a_plain_loss():
    """Collapse outranks the generic loss label, because it is the fake-win shape."""
    assert _classify(["Alpha", "Beta"], ["Beta"], ["Alpha", "Beta"],
                     gold_lost=1) == "MULTI_LISTING_COLLAPSE"


def test_recall_gain_is_recognised():
    assert _classify(["Alpha"], ["Alpha", "Beta"], ["Alpha", "Beta"],
                     gold_added=1) == "RECALL_GAIN"


def test_new_false_candidate_is_recognised():
    assert _classify(["Alpha"], ["Alpha", "Zeta"], ["Alpha"],
                     false_added=1) == "NEW_FALSE_CANDIDATE"


def test_unchanged_is_recognised():
    assert _classify(["Alpha"], ["Alpha"], ["Alpha"]) == "UNCHANGED"


def test_destructive_abstention_outranks_fp_reduction():
    """Emptying a real row must never be reported as a precision win."""
    label = _classify(["Alpha", "Beta"], [], ["Alpha"], gold_lost=1, false_removed=1)
    assert label == "DESTRUCTIVE_ABSTENTION"


def test_the_ledger_is_deterministic():
    for _ in range(3):
        assert _classify(["Alpha", "Beta"], ["Alpha"], ["Alpha"],
                         false_removed=1) == "GOLD_PRESERVED_FP_REDUCED"


# --------------------------------------------------------------------------
# 6. Isolation of the analyzer from production
# --------------------------------------------------------------------------


def test_the_stock_analyzer_is_not_importable_from_production():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src" / "cover_kbc").rglob("*.py")
        if "analyze_stock_diagnostic" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_no_subject_to_answer_mapping_in_the_analyzer():
    source = (REPO_ROOT / "scripts" / "analyze_stock_diagnostic.py").read_text(
        encoding="utf-8")
    for named in ("Nasdaq", "NYSE", "Euronext", "Tokyo Stock Exchange",
                  "London Stock Exchange"):
        assert named not in source


def test_analyzer_reads_gold_only_after_inference():
    """Gold is a CLI input to the offline analyzer, never a runner input."""
    runner_source = (REPO_ROOT / "scripts" / "run_cover.py").read_text(encoding="utf-8")
    assert "analyze_stock_diagnostic" not in runner_source
    analyzer_source = (REPO_ROOT / "scripts" / "analyze_stock_diagnostic.py").read_text(
        encoding="utf-8")
    assert "--gold" in analyzer_source


# --------------------------------------------------------------------------
# 7. Nothing else moved
# --------------------------------------------------------------------------


CALIBRATION_HASHES = {
    "configs/calibration/v3/m20_relation_budget.json":
        "74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29",
    "configs/calibration/v3/m21_historical_bins.json":
        "ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675",
    "configs/calibration/v3/m21_planner_calibration.json":
        "1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6",
    "configs/calibration/v3/calibration_provenance.json":
        "f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d",
    "configs/calibration/m20_relation_budget.json":
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68",
    "configs/calibration/m21_historical_bins.json":
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071",
    "configs/calibration/m21_planner_calibration.json":
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05",
}


@pytest.mark.parametrize("path,digest", sorted(CALIBRATION_HASHES.items()))
def test_calibration_artifacts_are_byte_identical(path, digest):
    blob = (REPO_ROOT / path).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == digest


@pytest.mark.parametrize("name", ("cover_kbc_v3_1_safe_core_test.yaml",
                                  "cover_kbc_v3_1_safe_full_test.yaml"))
def test_safe_configs_carry_no_class_b_feature(name):
    from cover_kbc.v3_1.config import V31Config

    config = yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
    block = V31Config.from_mapping(config["pipeline"]["selection"]["v3_1"])
    assert not block.aggressive.any_enabled


def test_safe_full_still_enables_stock_support_dominance():
    """The submission variant is untouched by the diagnostic's isolation."""
    from cover_kbc.v3_1.config import V31Config

    config = yaml.safe_load(
        (CONFIG_DIR / "cover_kbc_v3_1_safe_full_test.yaml").read_text(encoding="utf-8"))
    block = V31Config.from_mapping(config["pipeline"]["selection"]["v3_1"])
    assert block.safe.stock_support_dominance is True


def test_orchestration_repair_remains_active():
    from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig

    pipeline = CoverPipeline.__new__(CoverPipeline)
    pipeline.config = PipelineConfig(mode=ExecutionMode.INTERLEAVED)
    assert {r.value for r in pipeline._phase_b_roles()} == {
        "enumerator", "verifier", "none"}
    pipeline.config = PipelineConfig(mode=ExecutionMode.STAGED)
    assert {r.value for r in pipeline._phase_b_roles()} == {"verifier", "none"}


def test_borders_remain_frozen_from_class_b():
    from cover_kbc.v3_1.live_prompts import FROZEN_RELATIONS
    from cover_kbc.v3_1.prompts import instruction_for

    assert "countryLandBordersCountry" in FROZEN_RELATIONS
    assert instruction_for("countryLandBordersCountry") is None


def test_train_identity_is_the_canonical_split():
    blob = (REPO_ROOT / "benchmark" / "data" / "train.jsonl").read_bytes()
    assert hashlib.sha256(blob).hexdigest() == (
        "ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e")
    rows = [json.loads(line) for line in blob.decode().splitlines() if line.strip()]
    assert len(rows) == 477
    assert sum(1 for r in rows if r["Relation"] == STOCK) == 100


def test_model_contract_is_unchanged():
    profile = _config()["model_profile"]
    assert profile["enumerator"]["model_id"] == (
        "mistralai/Mistral-Small-3.2-24B-Instruct-2506")
    assert profile["enumerator"]["revision"] == (
        "95a6d26c4bfb886c58daf9d3f7332c857cb27b43")
    assert profile["verifier"]["model_id"] == "Qwen/Qwen3.5-4B"
    assert profile["verifier"]["revision"] == (
        "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a")
    total = sum(profile[role]["published_total_parameters"]
                for role in ("enumerator", "verifier"))
    assert total == 28_671_226_368 <= 32_000_000_000
