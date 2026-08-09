"""The official blind TEST path: readiness, config equivalence, packaging.

The system that answers TEST must be the system that was validated. Nothing
here tunes anything; every test asserts sameness, identity, or a refusal.

Three failures are targeted.

**A TEST run of a different system.** The config is validation's with the split
changed, so any divergence in models, prompts, controller, budgets or
calibration is a defect - asserted key by key rather than by eyeballing a diff.

**`--split test` used as a way around the validation gate.** TEST has its own
gate with its own state; the validation gate refuses the test config and the
test gate refuses the validation one.

**A submission built against the wrong file.** Row count, byte hash and the
digest of the ordered SubjectEntity/Relation pairs are pinned in the config and
re-checked by the packager, so VAL predictions cannot be shipped as TEST.

No weights are loaded, no inference runs, and the evaluator is never called.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
    evaluate_validation_readiness,
    ordered_identity_digest,
)
from cover_kbc.paths import REPO_ROOT

EXPERIMENTS = REPO_ROOT / "configs" / "experiments"
VAL_CONFIG = EXPERIMENTS / "cover_kbc_v2_validation.yaml"
TEST_CONFIG = EXPERIMENTS / "cover_kbc_v2_test.yaml"
TEST_DATA = REPO_ROOT / "benchmark" / "data" / "test.jsonl"
VAL_DATA = REPO_ROOT / "benchmark" / "data" / "val.jsonl"

#: The official blind split, as inspected. Pinned so a swapped or regenerated
#: file is a test failure rather than a silent re-target.
TEST_ROWS = 475
TEST_SHA256 = "67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1"
TEST_IDENTITY = "69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640"


@pytest.fixture(scope="module")
def val_config() -> dict:
    return yaml.safe_load(VAL_CONFIG.read_text())


@pytest.fixture(scope="module")
def test_config() -> dict:
    return yaml.safe_load(TEST_CONFIG.read_text())


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


# ==========================================================================
# the official TEST data
# ==========================================================================


def test_the_official_test_split_is_the_one_we_recorded() -> None:
    raw = TEST_DATA.read_bytes()
    rows = _rows(TEST_DATA)
    assert hashlib.sha256(raw).hexdigest() == TEST_SHA256
    assert len(rows) == TEST_ROWS
    assert ordered_identity_digest(
        (r["SubjectEntity"], r["Relation"]) for r in rows) == TEST_IDENTITY


def test_the_official_test_split_is_blind() -> None:
    """Gold is absent. The inference path could not read it if it tried."""
    rows = _rows(TEST_DATA)
    assert {tuple(sorted(r)) for r in rows} == {
        ("ObjectEntities", "Relation", "SubjectEntity")}
    assert all(r["ObjectEntities"] == [] for r in rows)


def test_the_test_split_has_no_duplicate_identities() -> None:
    ids = [(r["SubjectEntity"], r["Relation"]) for r in _rows(TEST_DATA)]
    assert len(ids) == len(set(ids))


# ==========================================================================
# 1, 2, 4, 5 — the two gates
# ==========================================================================


def test_the_val_config_still_reaches_full_validation_ready(val_config) -> None:
    provenance = val_config["calibration_provenance"]
    report = evaluate_validation_readiness(
        val_config, base_dir=EXPERIMENTS, split="val",
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert report.state is ReadinessState.FULL_VALIDATION_READY, report.blockers
    assert report.may_run_validation is True
    assert list(report.blockers) == []


def test_the_test_config_reaches_full_test_ready(test_config) -> None:
    provenance = test_config["calibration_provenance"]
    report = evaluate_test_readiness(
        test_config, base_dir=EXPERIMENTS, split="test",
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert report.state is ReadinessState.FULL_TEST_READY, report.blockers
    assert report.may_run_test is True
    assert list(report.blockers) == []
    satisfied = " | ".join(report.satisfied)
    for expected in ("split: test", "pipeline.mode: interleaved",
                     "parameter budget", f"test dataset: {TEST_ROWS} rows",
                     "test dataset: blind", "all six relations budgeted",
                     "M20 and M21 both declare production mode"):
        assert expected in satisfied, expected


def test_test_readiness_is_not_validation_readiness(test_config) -> None:
    """A test-ready profile must not read as cleared for validation."""
    report = evaluate_test_readiness(test_config, base_dir=EXPERIMENTS)
    assert report.may_run_validation is False
    assert report.to_json()["may_run_test"] is True


def test_the_validation_gate_refuses_the_test_config(test_config) -> None:
    report = evaluate_validation_readiness(test_config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert any("may only read 'val'" in b for b in report.blockers)


def test_the_test_gate_refuses_the_validation_config(val_config) -> None:
    """`--split test` is not a bypass: the val profile is refused by name."""
    report = evaluate_test_readiness(val_config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert report.may_run_test is False
    assert any("may only read 'test'" in b for b in report.blockers)


def test_the_test_gate_refuses_val_data_under_a_test_label(
    test_config, tmp_path,
) -> None:
    """Pointing the test config at the val split must not pass identity."""
    (tmp_path / "test.jsonl").write_bytes(VAL_DATA.read_bytes())
    report = evaluate_test_readiness(
        test_config, base_dir=EXPERIMENTS, data_dir=tmp_path)
    assert report.state is ReadinessState.NOT_READY
    joined = " | ".join(report.blockers)
    # VAL and TEST happen to have the same row count in the current benchmark
    # generation, so the byte hash and the ordered identity are what catch
    # this - which is exactly why identity is pinned three ways and not one.
    assert "sha256" in joined
    assert "ordered identity" in joined or "ObjectEntities" in joined


@pytest.mark.parametrize("field", ["rows", "sha256", "identity_sha256"])
def test_the_test_gate_fails_closed_on_a_wrong_dataset_pin(
    test_config, field,
) -> None:
    config = yaml.safe_load(TEST_CONFIG.read_text())
    config["test_dataset"][field] = (
        0 if field == "rows" else "0" * 64)
    report = evaluate_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY


def test_the_test_gate_refuses_a_dataset_carrying_objects(
    test_config, tmp_path,
) -> None:
    """Blind means blind - a non-empty objects field is a blocker."""
    rows = _rows(TEST_DATA)
    rows[0]["ObjectEntities"] = ["a leaked answer"]
    body = "\n".join(json.dumps(r) for r in rows) + "\n"
    (tmp_path / "test.jsonl").write_text(body)
    config = yaml.safe_load(TEST_CONFIG.read_text())
    config["test_dataset"]["sha256"] = hashlib.sha256(
        body.encode()).hexdigest()
    report = evaluate_test_readiness(
        config, base_dir=EXPERIMENTS, data_dir=tmp_path)
    assert report.state is ReadinessState.NOT_READY
    assert any("carry ObjectEntities" in b for b in report.blockers)


@pytest.mark.parametrize("mode", ["staged", "", "bogus"])
def test_the_test_gate_requires_the_calibrated_execution_mode(mode) -> None:
    config = yaml.safe_load(TEST_CONFIG.read_text())
    config["pipeline"]["mode"] = mode
    report = evaluate_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert any("pipeline.mode" in b for b in report.blockers)


def test_the_test_gate_requires_a_legal_parameter_budget() -> None:
    config = yaml.safe_load(TEST_CONFIG.read_text())
    config["budget_assertion"]["limit"] = 1_000_000
    report = evaluate_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert any("exceeds the" in b for b in report.blockers)


@pytest.mark.parametrize("block", ["relation_budget_scheduler", "micro_planner",
                                   "layer6_integration"])
def test_the_test_gate_requires_the_whole_layer_six_stack(block) -> None:
    config = yaml.safe_load(TEST_CONFIG.read_text())
    config[block]["enabled"] = False
    report = evaluate_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY


# ==========================================================================
# 3, 13 — the test config differs from validation only where it must
# ==========================================================================

#: The only keys allowed to differ. Everything else is the validated system.
ALLOWED_DIFFERENCES = {"experiment", "test_dataset"}


def test_the_test_config_differs_from_validation_only_where_expected(
    val_config, test_config,
) -> None:
    assert set(test_config) - set(val_config) == {"test_dataset"}
    assert set(val_config) - set(test_config) == set()
    differing = {key for key in val_config
                 if val_config[key] != test_config.get(key)}
    assert differing <= ALLOWED_DIFFERENCES, differing


def test_every_semantic_block_is_identical(val_config, test_config) -> None:
    """Models, prompts, controller, budgets, calibration - all of it."""
    for block in ("model_profile", "budget_assertion", "pipeline",
                  "calibration_provenance", "query_intelligence", "specialists",
                  "consensus", "specialist_verifier",
                  "bidirectional_verification", "layer4_integration",
                  "coverage_gap", "relation_budget_scheduler", "micro_planner",
                  "layer6_integration"):
        assert test_config[block] == val_config[block], block


def test_the_frozen_models_are_exact(test_config) -> None:
    profile = test_config["model_profile"]
    assert profile["enumerator"]["model_id"] == (
        "mistralai/Mistral-Small-3.2-24B-Instruct-2506")
    assert profile["enumerator"]["revision"] == (
        "95a6d26c4bfb886c58daf9d3f7332c857cb27b43")
    assert profile["verifier"]["model_id"] == "Qwen/Qwen3.5-4B"
    assert profile["verifier"]["revision"] == (
        "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a")
    assert test_config["budget_assertion"]["total_published_parameters"] == (
        28_671_226_368)


def test_the_test_config_keeps_interleaved_and_the_train_provenance(
    test_config,
) -> None:
    assert test_config["pipeline"]["mode"] == "interleaved"
    provenance = test_config["calibration_provenance"]
    assert provenance["collection_repo_sha"] == (
        "264c980361a513078903526440c72adc6e10edaf")
    assert provenance["derivation_repo_sha"] == (
        "78ad89d3cd8a321f500807b11477fce2f8579e32")
    assert test_config["relation_budget_scheduler"]["calibration_sha256"] == (
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68")
    assert test_config["micro_planner"]["historical_bins_sha256"] == (
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071")
    assert test_config["micro_planner"]["planner_calibration_sha256"] == (
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05")


def test_the_config_declares_the_split_and_the_dataset_it_answers(
    test_config,
) -> None:
    assert test_config["experiment"]["split"] == "test"
    dataset = test_config["test_dataset"]
    assert dataset["rows"] == TEST_ROWS
    assert dataset["sha256"] == TEST_SHA256
    assert dataset["identity_sha256"] == TEST_IDENTITY
    assert dataset["blind"] is True


# ==========================================================================
# 6, 7, 8 — the runner routes by split and never scores TEST
# ==========================================================================


def _runner():
    import importlib.util

    path = REPO_ROOT / "scripts" / "run_cover.py"
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("run_cover_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


def test_the_runner_maps_each_split_to_its_own_gate() -> None:
    runner = _runner()
    assert set(runner.PRODUCTION_GATES) == {"val", "test"}
    assert runner.PRODUCTION_GATES["val"] == (
        evaluate_validation_readiness, ReadinessState.FULL_VALIDATION_READY)
    assert runner.PRODUCTION_GATES["test"] == (
        evaluate_test_readiness, ReadinessState.FULL_TEST_READY)


def test_the_runner_has_no_production_path_for_any_other_split() -> None:
    """train, or a typo, must not inherit a gate."""
    runner = _runner()
    for split in ("train", "TEST", "valid", ""):
        assert split not in runner.PRODUCTION_GATES


def test_the_runner_builds_the_same_production_stack_for_both_splits() -> None:
    """One pipeline construction, not a parallel TEST path."""
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert source.count("pipeline = CoverPipeline(") == 1
    assert source.count("IntegrationMode.PRODUCTION") == 1
    assert source.count("Layer6Integrator(planner)") == 1
    assert source.count("relation_budget_scheduler=build_relation_budget_scheduler") == 1


def test_the_runner_never_scores_a_blind_split() -> None:
    """The evaluator is not reachable for TEST, and no metrics.json is written."""
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    guard = "scoreable = split not in BLIND_SPLITS and not dataset.is_blind"
    assert guard in source
    body = source[source.index(guard):]
    evaluate_at = body.index("evaluate_predictions(")
    metrics_at = body.index('"metrics.json"')
    branch_at = body.index("if not args.no_eval and scoreable:")
    assert branch_at < evaluate_at and branch_at < metrics_at

    from cover_kbc.data.loader import BLIND_SPLITS
    assert "test" in BLIND_SPLITS


def test_the_test_config_reaches_production_mode_in_the_runner(
    test_config,
) -> None:
    runner = _runner()
    assert runner._wants_production(test_config) is True
    assert runner.resolve_execution_mode(test_config).value == "interleaved"


# ==========================================================================
# 9-12 — the submission
# ==========================================================================


def _predictions_for(rows: list[dict]) -> str:
    return "\n".join(json.dumps({
        "SubjectEntity": r["SubjectEntity"], "Relation": r["Relation"],
        "ObjectEntities": [],
    }) for r in rows) + "\n"


@pytest.fixture
def packager():
    import importlib.util

    path = REPO_ROOT / "scripts" / "package_submission.py"
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("package_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


def test_the_packager_pins_the_same_dataset_the_config_does(
    packager, test_config,
) -> None:
    """Two owners, one identity. Drift between them would be silent."""
    assert packager.OFFICIAL_TEST["rows"] == test_config["test_dataset"]["rows"]
    assert packager.OFFICIAL_TEST["sha256"] == test_config["test_dataset"]["sha256"]
    assert packager.OFFICIAL_TEST["identity_sha256"] == (
        test_config["test_dataset"]["identity_sha256"])


def test_the_packager_accepts_a_valid_test_submission(packager, tmp_path) -> None:
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(_predictions_for(_rows(TEST_DATA)))
    summary = packager.validate(predictions, TEST_DATA, split="test")
    assert summary["split"] == "test"
    assert summary["rows"] == TEST_ROWS
    assert summary["identity_sha256"] == TEST_IDENTITY


def test_the_packager_accepts_answers_as_well_as_empties(
    packager, tmp_path,
) -> None:
    rows = _rows(TEST_DATA)
    payload = [{"SubjectEntity": r["SubjectEntity"], "Relation": r["Relation"],
                "ObjectEntities": (["Spain"] if i == 0 else [])}
               for i, r in enumerate(rows)]
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("\n".join(json.dumps(r) for r in payload) + "\n")
    summary = packager.validate(predictions, TEST_DATA, split="test")
    assert summary["rows_with_objects"] == 1


def test_the_packager_refuses_val_predictions_as_test(packager, tmp_path) -> None:
    """The accident that would silently score zero."""
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(_predictions_for(_rows(VAL_DATA)))
    with pytest.raises(packager.SubmissionError, match="different split"):
        packager.validate(predictions, TEST_DATA, split="test")


def test_the_packager_refuses_the_wrong_input_file(packager, tmp_path) -> None:
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(_predictions_for(_rows(TEST_DATA)))
    with pytest.raises(packager.SubmissionError, match="must be validated against"):
        packager.validate(predictions, VAL_DATA, split="test")


def test_the_packager_refuses_a_tampered_test_input(packager, tmp_path) -> None:
    rows = _rows(TEST_DATA)
    rows[0]["SubjectEntity"] = "Somewhere Else"
    forged = tmp_path / "test.jsonl"
    forged.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(_predictions_for(rows))
    with pytest.raises(packager.SubmissionError, match="not the official test split"):
        packager.validate(predictions, forged, split="test")


@pytest.mark.parametrize("mangle", ["missing", "extra", "reordered", "duplicated"])
def test_the_packager_refuses_a_broken_row_set(packager, tmp_path, mangle) -> None:
    rows = _rows(TEST_DATA)
    payload = [{"SubjectEntity": r["SubjectEntity"], "Relation": r["Relation"],
                "ObjectEntities": []} for r in rows]
    if mangle == "missing":
        payload.pop()
    elif mangle == "extra":
        payload.append(dict(payload[0]))
    elif mangle == "reordered":
        payload[0], payload[1] = payload[1], payload[0]
    else:
        payload[1] = dict(payload[0])
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("\n".join(json.dumps(r) for r in payload) + "\n")
    with pytest.raises(packager.SubmissionError):
        packager.validate(predictions, TEST_DATA, split="test")


def test_the_packager_refuses_a_non_list_object_field(packager, tmp_path) -> None:
    rows = _rows(TEST_DATA)
    payload = [{"SubjectEntity": r["SubjectEntity"], "Relation": r["Relation"],
                "ObjectEntities": []} for r in rows]
    payload[0]["ObjectEntities"] = "Spain"
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("\n".join(json.dumps(r) for r in payload) + "\n")
    with pytest.raises(packager.SubmissionError, match="must be a list"):
        packager.validate(predictions, TEST_DATA, split="test")


def test_the_packager_refuses_malformed_jsonl(packager, tmp_path) -> None:
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text('{"SubjectEntity": "x", oops\n')
    with pytest.raises(packager.SubmissionError, match="not valid JSON"):
        packager.validate(predictions, TEST_DATA, split="test")


def test_the_packager_refuses_a_gold_or_diagnostic_field(packager, tmp_path) -> None:
    rows = _rows(TEST_DATA)
    payload = [{"SubjectEntity": r["SubjectEntity"], "Relation": r["Relation"],
                "ObjectEntities": []} for r in rows]
    payload[0]["gold"] = ["leaked"]
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("\n".join(json.dumps(r) for r in payload) + "\n")
    with pytest.raises(packager.SubmissionError, match="non-official field"):
        packager.validate(predictions, TEST_DATA, split="test")


def test_the_test_archive_holds_exactly_predictions_jsonl(
    packager, tmp_path, monkeypatch,
) -> None:
    import zipfile

    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(_predictions_for(_rows(TEST_DATA)))
    archive = tmp_path / "test_submission.zip"
    monkeypatch.setattr(sys, "argv", [
        "package_submission.py", "--predictions", str(predictions),
        "--input", str(TEST_DATA), "--out", str(archive), "--split", "test"])
    assert packager.main() == 0
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == ["predictions.jsonl"]
        assert zf.testzip() is None
    manifest = json.loads(archive.with_suffix(".manifest.json").read_text())
    assert manifest["split"] == "test"
    assert manifest["rows"] == TEST_ROWS
    assert manifest["input_sha256"] == TEST_SHA256
    assert manifest["archive_members"] == ["predictions.jsonl"]


def test_validation_packaging_is_unchanged(packager, tmp_path) -> None:
    """The existing VAL route must behave exactly as before."""
    rows = _rows(VAL_DATA)
    payload = [{"SubjectEntity": r["SubjectEntity"], "Relation": r["Relation"],
                "ObjectEntities": []} for r in rows]
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("\n".join(json.dumps(r) for r in payload) + "\n")
    summary = packager.validate(predictions, VAL_DATA)
    assert summary["split"] == "val"
    # Counted from the file rather than pinned to a constant. VAL is not the
    # submission split and the packager pins no identity for it, so what this
    # guards is the identity contract, not a row count the organizer moves.
    assert summary["rows"] == len(rows)


def test_the_packager_never_reads_gold_from_the_input() -> None:
    """Identity only: the input's ObjectEntities are never consulted."""
    source = (REPO_ROOT / "scripts" / "package_submission.py").read_text()
    body = source[source.index("def validate("):source.index("def main(")]
    # `want` is the input row; only its identity columns may be read.
    reads = {line.strip() for line in body.splitlines() if "want[" in line}
    for line in reads:
        assert "ObjectEntities" not in line, line
