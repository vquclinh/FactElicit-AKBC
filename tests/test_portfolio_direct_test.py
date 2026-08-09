"""The direct uncalibrated portfolio TEST path, and the three it must not become.

Submission #2 asks one question - *does the model stack help on its own?* - so
the whole upgraded architecture runs while Modules 20 and 21 govern nothing.
There is no calibration for these checkpoints, and the two ways of pretending
otherwise are both refused: borrowing the baseline's packages would measure
Mistral's action economics driving Gemma's actions, and inventing one from the
split being answered would measure nothing.

Three experiments must stay distinguishable, and most of this file exists to
keep them apart:

* **A** baseline, calibrated, `FULL_TEST_READY`, official 0.4499;
* **B** portfolio direct, uncalibrated, `FULL_TEST_DIRECT_READY`;
* **C** portfolio calibrated, `NOT_READY` until real artifacts exist.

No weights are downloaded and no official split is answered here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys

import pytest
import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_direct_test_readiness,
    evaluate_test_readiness,
    evaluate_validation_readiness,
    ordered_identity_digest,
)
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.models.strategy import (
    PORTFOLIO_V2,
    PORTFOLIO_V2_PARAMETERS,
    ModelStrategy,
    resolve_strategy,
)
from cover_kbc.paths import REPO_ROOT
from cover_kbc.types import ModelRole

EXPERIMENTS = REPO_ROOT / "configs" / "experiments"
DIRECT = EXPERIMENTS / "cover_kbc_v2_portfolio_test_direct.yaml"
CALIBRATED = EXPERIMENTS / "cover_kbc_v2_portfolio_test.yaml"
BASELINE_TEST = EXPERIMENTS / "cover_kbc_v2_test.yaml"
BASELINE_VAL = EXPERIMENTS / "cover_kbc_v2_validation.yaml"
TEST_DATA = REPO_ROOT / "benchmark" / "data" / "test.jsonl"

TEST_ROWS = 477
TEST_SHA256 = "849f565d6fcf53f60b74e53503d1ac119933e823f191030b34befe0df044fc1f"
TEST_IDENTITY = "1bce6d40f843f7c743af6d896f2a390c4e210eac32d95f64d2887e5373fc2609"


def _load(path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def direct() -> dict:
    return _load(DIRECT)


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _runner():
    path = REPO_ROOT / "scripts" / "run_cover.py"
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("run_cover_direct", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


# ==========================================================================
# 1-4, 30 — the other two experiments are untouched
# ==========================================================================


def test_baseline_no_flag_is_still_baseline() -> None:
    config = _load(BASELINE_TEST)
    assert "model_strategy" not in config
    assert resolve_strategy(config).strategy is ModelStrategy.BASELINE


def test_baseline_explicit_flag_is_still_baseline() -> None:
    assert resolve_strategy(_load(BASELINE_TEST), "baseline").strategy is (
        ModelStrategy.BASELINE)


@pytest.mark.parametrize("path", [BASELINE_TEST, BASELINE_VAL])
def test_baseline_readiness_is_unchanged(path) -> None:
    config = _load(path)
    provenance = config["calibration_provenance"]
    gate = (evaluate_test_readiness if config["experiment"]["split"] == "test"
            else evaluate_validation_readiness)
    report = gate(
        config, base_dir=EXPERIMENTS,
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert report.state in (ReadinessState.FULL_TEST_READY,
                            ReadinessState.FULL_VALIDATION_READY), report.blockers
    assert list(report.blockers) == []
    # ...and a calibrated gate never returns the direct state.
    assert report.may_run_test_direct is False


def test_the_calibrated_portfolio_path_is_still_not_ready() -> None:
    """Experiment C stays blocked until real portfolio artifacts exist."""
    report = evaluate_test_readiness(_load(CALIBRATED), base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert report.may_run_test is False
    assert report.blockers


def test_the_calibrated_config_still_demands_production_layer_six() -> None:
    config = _load(CALIBRATED)
    assert config["relation_budget_scheduler"]["mode"] == "production"
    assert config["micro_planner"]["mode"] == "production"
    assert "/portfolio/" in config["relation_budget_scheduler"]["calibration_file"]


# ==========================================================================
# 5-8 — the direct config
# ==========================================================================


def test_the_direct_config_resolves_portfolio(direct) -> None:
    profile = resolve_strategy(direct, "portfolio")
    assert profile.strategy is ModelStrategy.PORTFOLIO
    assert set(profile.blocks) == set(PORTFOLIO_V2)


def test_the_direct_config_uses_the_exact_audit_0062_models(direct) -> None:
    profile = resolve_strategy(direct, "portfolio")
    for role, expected in PORTFOLIO_V2.items():
        block = profile.block(role)
        assert block["model_id"] == expected["model_id"], role
        assert block["revision"] == expected["revision"], role
        assert block["published_total_parameters"] == expected["parameters"], role
        assert block["parameter_source_verified"] is True, role


def test_the_direct_config_total_is_unchanged(direct) -> None:
    assert direct["budget_assertion"]["total_published_parameters"] == (
        PORTFOLIO_V2_PARAMETERS) == 30_728_656_736
    assert PORTFOLIO_V2_PARAMETERS <= 32_000_000_000


def test_the_direct_config_matches_the_calibrated_one_on_models(direct) -> None:
    """B and C must differ only in the controller, never in the models."""
    calibrated = _load(CALIBRATED)
    assert direct["model_portfolio"] == calibrated["model_portfolio"]
    assert direct["budget_assertion"] == calibrated["budget_assertion"]
    for block in ("pipeline", "query_intelligence", "specialists", "consensus",
                  "specialist_verifier", "bidirectional_verification",
                  "layer4_integration", "coverage_gap"):
        assert direct[block] == calibrated[block], block


def test_the_direct_config_declares_a_non_production_controller(direct) -> None:
    assert direct["controller_mode"] == "direct_uncalibrated"
    assert direct["experiment_variant"] == "portfolio_direct"
    for block in ("relation_budget_scheduler", "micro_planner",
                  "layer6_integration"):
        assert direct[block]["enabled"] is False, block
        assert direct[block]["mode"] != "production", block


# ==========================================================================
# 9-11 — it cannot load, or need, any calibration
# ==========================================================================


def test_the_direct_config_names_no_calibration_artifact(direct) -> None:
    """Not the baseline's, not the portfolio's, not anyone's."""
    assert direct["relation_budget_scheduler"].get("calibration_file") is None
    assert direct["micro_planner"].get("historical_bins") is None
    assert direct["micro_planner"].get("planner_calibration") is None
    assert "calibration_provenance" not in direct
    body = DIRECT.read_text()
    for artifact in ("m20_relation_budget.json", "m21_historical_bins.json",
                     "m21_planner_calibration.json"):
        assert artifact not in body, artifact


def test_naming_a_calibration_artifact_is_refused(direct) -> None:
    config = _load(DIRECT)
    config["relation_budget_scheduler"]["calibration_file"] = (
        "../calibration/m20_relation_budget.json")
    report = evaluate_direct_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert any("loads none" in b for b in report.blockers)


@pytest.mark.parametrize("block", ["relation_budget_scheduler", "micro_planner"])
def test_a_production_controller_is_refused_for_a_direct_run(block) -> None:
    config = _load(DIRECT)
    config[block].update({"enabled": True, "mode": "production"})
    report = evaluate_direct_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert any("must not be governed" in b for b in report.blockers)


def test_direct_readiness_records_that_it_is_uncalibrated(direct) -> None:
    report = evaluate_direct_test_readiness(direct, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.FULL_TEST_DIRECT_READY
    assert report.details["production_calibrated"] is False
    assert report.details["controller_mode"] == "direct_uncalibrated"
    assert report.details["experiment_variant"] == "portfolio_direct"
    assert report.may_run_test_direct is True
    # ...and it is never mistaken for the calibrated clearance.
    assert report.may_run_test is False
    assert report.may_run_validation is False


def test_the_two_ready_states_are_distinct() -> None:
    assert (ReadinessState.FULL_TEST_DIRECT_READY
            is not ReadinessState.FULL_TEST_READY)
    assert ReadinessState.FULL_TEST_DIRECT_READY.value == "FULL_TEST_DIRECT_READY"


# ==========================================================================
# 12-17 — the direct gate is not a weaker gate
# ==========================================================================


def test_direct_readiness_accepts_the_exact_blind_split(direct) -> None:
    report = evaluate_direct_test_readiness(direct, base_dir=EXPERIMENTS)
    assert list(report.blockers) == [], report.blockers
    assert report.details["test_rows"] == TEST_ROWS
    assert report.details["test_sha256"] == TEST_SHA256
    assert report.details["test_identity_sha256"] == TEST_IDENTITY


@pytest.mark.parametrize("field", ["rows", "sha256", "identity_sha256"])
def test_direct_readiness_rejects_a_wrong_dataset_pin(field) -> None:
    config = _load(DIRECT)
    config["test_dataset"][field] = 0 if field == "rows" else "0" * 64
    report = evaluate_direct_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY


def test_direct_readiness_rejects_a_reordered_split(tmp_path) -> None:
    rows = _rows(TEST_DATA)
    rows[0], rows[1] = rows[1], rows[0]
    body = "\n".join(json.dumps(r) for r in rows) + "\n"
    (tmp_path / "test.jsonl").write_text(body)
    config = _load(DIRECT)
    config["test_dataset"]["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    report = evaluate_direct_test_readiness(
        config, base_dir=EXPERIMENTS, data_dir=tmp_path)
    assert report.state is ReadinessState.NOT_READY
    assert any("ordered identity" in b for b in report.blockers)


def test_direct_readiness_rejects_a_split_carrying_gold(tmp_path) -> None:
    rows = _rows(TEST_DATA)
    rows[0]["ObjectEntities"] = ["a leaked answer"]
    body = "\n".join(json.dumps(r) for r in rows) + "\n"
    (tmp_path / "test.jsonl").write_text(body)
    config = _load(DIRECT)
    config["test_dataset"]["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    config["test_dataset"]["identity_sha256"] = ordered_identity_digest(
        (r["SubjectEntity"], r["Relation"]) for r in rows)
    report = evaluate_direct_test_readiness(
        config, base_dir=EXPERIMENTS, data_dir=tmp_path)
    assert report.state is ReadinessState.NOT_READY
    assert any("carry ObjectEntities" in b for b in report.blockers)


def test_direct_readiness_rejects_an_over_budget_portfolio() -> None:
    config = _load(DIRECT)
    for role in ("factual_enumerator", "structural_reasoner"):
        config["model_portfolio"][role].update({
            "published_total_parameters": 16_000_000_000,
            "budget_count_parameters": 16_000_000_000})
    report = evaluate_direct_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert any("exceeds the" in b for b in report.blockers)


@pytest.mark.parametrize("role", sorted(r.value for r in PORTFOLIO_V2))
def test_direct_readiness_rejects_a_missing_revision(role) -> None:
    config = _load(DIRECT)
    config["model_portfolio"][role]["revision"] = ""
    report = evaluate_direct_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert any("no immutable revision" in b for b in report.blockers)


@pytest.mark.parametrize("role", sorted(r.value for r in PORTFOLIO_V2))
def test_direct_readiness_rejects_an_unverified_parameter_count(role) -> None:
    config = _load(DIRECT)
    config["model_portfolio"][role]["parameter_source_verified"] = False
    report = evaluate_direct_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY


@pytest.mark.parametrize("split", ["train", "val", ""])
def test_direct_readiness_only_reads_test(split) -> None:
    config = _load(DIRECT)
    config["experiment"]["split"] = split
    report = evaluate_direct_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY
    assert any("may only read 'test'" in b for b in report.blockers)


@pytest.mark.parametrize("path", [
    ("query_intelligence", "parametric_retrieval"),
    ("specialists", "numeric"),
    ("consensus",),
    ("specialist_verifier",),
    ("bidirectional_verification",),
    ("coverage_gap",),
])
def test_direct_readiness_still_requires_the_evidence_stack(path) -> None:
    """Uncalibrated is not "reduced": M9-M19 all still run."""
    config = _load(DIRECT)
    node = config
    for key in path[:-1]:
        node = node[key]
    node[path[-1]]["enabled"] = False
    report = evaluate_direct_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is ReadinessState.NOT_READY


# ==========================================================================
# 18-21 — the runner and the routing
# ==========================================================================


def test_the_runner_recognises_the_direct_variant(direct) -> None:
    runner = _runner()
    assert runner._wants_direct(direct) is True
    assert runner._wants_production(direct) is False
    assert runner._wants_direct(_load(BASELINE_TEST)) is False
    assert runner._wants_direct(_load(CALIBRATED)) is False


def test_a_config_cannot_be_both_direct_and_production() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert "if production and direct:" in source
    assert "a run is one or the\n" in source or "one or the " in source


def test_the_direct_run_selects_the_uncalibrated_integration_mode() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert "IntegrationMode.DIRECT_UNCALIBRATED if direct" in source
    assert "TrainCollectionPolicy()" in source, (
        "the direct path must reuse the existing deterministic policy")


def test_portfolio_routing_is_unchanged_from_audit_0062() -> None:
    from cover_kbc.models.strategy import role_for_operation

    for operation in ("acquisition", "parametric_retrieval", "specialist_probe",
                      "candidate_free_recall"):
        assert role_for_operation(ModelStrategy.PORTFOLIO, operation) is (
            ModelRole.FACTUAL_ENUMERATOR)
    for operation in ("reverse_check", "key_condition", "counterfactual"):
        assert role_for_operation(ModelStrategy.PORTFOLIO, operation) is (
            ModelRole.STRUCTURAL_REASONER)
    for operation in ("specialist_verify", "blind_verify"):
        assert role_for_operation(ModelStrategy.PORTFOLIO, operation) is (
            ModelRole.INDEPENDENT_VERIFIER)


# ==========================================================================
# 22-25 — nothing calibrated governs, and the hard caps remain
# ==========================================================================


def test_no_calibrated_budget_can_govern_a_direct_run() -> None:
    """`_precharge` short-circuits: there is no ledger to consult."""
    import inspect

    from cover_kbc.pipeline import CoverPipeline

    source = inspect.getsource(CoverPipeline._precharge)
    assert ("if self.integration_mode.is_collection "
            "or self.integration_mode.is_direct:") in source
    # ...and the short-circuit precedes any ledger construction.
    assert source.index("is_direct") < source.index("_budget_ledger_for")


def test_only_production_consults_module_21() -> None:
    import inspect

    from cover_kbc.pipeline import CoverPipeline

    source = inspect.getsource(CoverPipeline._select_actions)
    assert "self.integration_mode.is_production" in source
    assert "_plan_next_action" in source
    # A direct run is not production, so it falls through to the injected
    # deterministic selector.
    assert IntegrationMode.DIRECT_UNCALIBRATED.is_production is False
    assert IntegrationMode.DIRECT_UNCALIBRATED.uses_calibrated_controller is False


def test_the_direct_mode_still_mutates_production_state() -> None:
    """Otherwise Layer-4 evidence would never reach Module 8."""
    assert IntegrationMode.DIRECT_UNCALIBRATED.may_mutate_production_state
    assert not IntegrationMode.SHADOW.may_mutate_production_state


def test_the_architecture_hard_caps_are_still_declared(direct) -> None:
    """Uncalibrated does not mean unbounded."""
    pipeline = direct["pipeline"]
    assert pipeline["max_calls_per_query"] == 12
    assert pipeline["max_steps_per_query"] == 12
    assert pipeline["max_generated_tokens_per_query"] == 6000
    assert pipeline["max_control_rounds_per_catalogue"] == 3
    # These are Module 7's, not Module 20's: they live in `pipeline:` and are
    # identical to the calibrated config's.
    assert pipeline == _load(CALIBRATED)["pipeline"]


def test_physical_accounting_and_its_fatal_error_are_untouched() -> None:
    from cover_kbc.pipeline import AccountingInvariantError, CoverPipeline

    assert CoverPipeline.PHYSICAL_COUNTERS == (
        "enumerator_calls", "verifier_calls", "physical_calls",
        "prompt_tokens", "generated_tokens")
    source = (REPO_ROOT / "src" / "cover_kbc" / "pipeline.py").read_text()
    assert "except AccountingInvariantError:" in source
    body = source[source.index("def run(self, queries"):]
    assert body.index("except AccountingInvariantError:") < body.index(
        "except Exception as exc:")
    assert issubclass(AccountingInvariantError, ValueError)


# ==========================================================================
# 26-29 — the run's outputs
# ==========================================================================


def test_the_evaluator_is_unreachable_for_a_blind_split() -> None:
    from cover_kbc.data.loader import BLIND_SPLITS

    assert "test" in BLIND_SPLITS
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert "scoreable = split not in BLIND_SPLITS and not dataset.is_blind" in source


def test_module_8_is_still_the_only_prediction_owner() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert source.count("write_predictions(") == 1


def test_the_manifest_carries_the_controller_facts() -> None:
    from cover_kbc.runtime.manifest import RunManifest

    payload = RunManifest(run_id="r", experiment="e", split="test",
                          seed=1).to_json()
    for key in ("controller_mode", "production_calibrated",
                "calibration_owner", "experiment_variant"):
        assert key in payload, key
    assert payload["production_calibrated"] is False
    assert payload["calibration_owner"] is None


def test_the_runner_records_the_controller_facts() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert "manifest.controller_mode = (" in source
    assert "manifest.production_calibrated = bool(production)" in source
    assert "manifest.calibration_owner = (" in source
    assert "manifest.experiment_variant = str(" in source


def test_the_packager_contract_is_unchanged() -> None:
    packager = (REPO_ROOT / "scripts" / "package_submission.py").read_text()
    assert 'ARCHIVE_MEMBER = "predictions.jsonl"' in packager
    assert 'members != [ARCHIVE_MEMBER]' in packager
    for forbidden in ("manifest.json", "calls.jsonl", "telemetry"):
        assert f'zf.write({forbidden}' not in packager


# ==========================================================================
# end to end, through the real CLI, with scripted runtimes
# ==========================================================================


@pytest.fixture
def direct_cli(tmp_path, monkeypatch):
    """The real `run_cover.main()` on a two-row synthetic blind split."""
    from cover_kbc.data.loader import load_dataset
    from cover_kbc.models.offline import ScriptedRuntime

    rows = tmp_path / "test.jsonl"
    identities = [("Testland", "countryLandBordersCountry"),
                  ("Secondland", "countryLandBordersCountry")]
    body = "\n".join(json.dumps(
        {"SubjectEntity": s, "Relation": r, "ObjectEntities": []})
        for s, r in identities) + "\n"
    rows.write_text(body)

    config = _load(DIRECT)
    config["test_dataset"].update({
        "rows": 2,
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
        "identity_sha256": ordered_identity_digest(identities)})
    path = tmp_path / "direct.yaml"
    path.write_text(yaml.safe_dump(config))

    runner = _runner()
    built: list[str] = []

    def stub(block):
        built.append(block["model_id"])
        role = str(block.get("role", ""))
        reply = {"factual_enumerator": "Alphaland, Betaland",
                 "structural_reasoner": "Alphaland"}.get(role, "A")
        return ScriptedRuntime({}, model_id=block["model_id"],
                              family=str(block.get("family", "")), role=role,
                              fallback=lambda request, _r=reply: _r)

    real_gate = evaluate_direct_test_readiness
    monkeypatch.setattr(runner, "build_runtime", stub)
    monkeypatch.setattr(runner, "load_dataset",
                        lambda split: load_dataset(split, path=rows))
    monkeypatch.setattr(runner, "evaluate_direct_test_readiness",
                        lambda c, **kw: real_gate(c, **{**kw, "data_dir": tmp_path}))
    monkeypatch.setattr(runner, "audit_parameter_budget", lambda specs: type(
        "A", (), {"passed": True, "summary": lambda self: "",
                  "to_json": lambda self: {}})())
    out = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", [
        "run_cover.py", "--config", str(path), "--model-strategy", "portfolio",
        "--output-dir", str(out)])
    return runner, built, out


def test_the_direct_cli_run_succeeds(direct_cli) -> None:
    runner, _, out = direct_cli
    assert runner.main() == 0
    assert (out / "predictions.jsonl").is_file()
    rows = [line for line in (out / "predictions.jsonl").read_text().splitlines()
            if line.strip()]
    assert len(rows) == 2


def test_the_direct_cli_builds_exactly_three_portfolio_runtimes(
    direct_cli,
) -> None:
    runner, built, _ = direct_cli
    assert runner.main() == 0
    assert sorted(built) == sorted(m["model_id"] for m in PORTFOLIO_V2.values())
    assert "mistralai/Mistral-Small-3.2-24B-Instruct-2506" not in built
    assert "Qwen/Qwen3.5-4B" not in built


def test_the_direct_cli_writes_no_metrics(direct_cli) -> None:
    runner, _, out = direct_cli
    assert runner.main() == 0
    assert not (out / "metrics.json").exists()


def test_the_direct_cli_manifest_says_uncalibrated(direct_cli) -> None:
    runner, _, out = direct_cli
    assert runner.main() == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["model_strategy"] == "portfolio"
    assert manifest["model_strategy_requested"] == "portfolio"
    assert manifest["controller_mode"] == "direct_uncalibrated"
    assert manifest["production_calibrated"] is False
    assert manifest["calibration_owner"] is None
    assert manifest["experiment_variant"] == "portfolio_direct"
    roles = manifest["model_strategy_profile"]["roles"]
    for role, expected in PORTFOLIO_V2.items():
        assert roles[role.value]["model_id"] == expected["model_id"]
        assert roles[role.value]["revision"] == expected["revision"]
        assert roles[role.value]["published_total_parameters"] == (
            expected["parameters"])


def test_the_direct_cli_executes_layer_four(direct_cli) -> None:
    """The point of direct over shadow: the upgraded evidence actually runs."""
    runner, _, out = direct_cli
    assert runner.main() == 0
    for artifact in ("specialist_verification.jsonl",
                     "bidirectional_verification.jsonl",
                     "layer4_evidence.jsonl", "coverage_gap.jsonl"):
        assert (out / artifact).is_file(), artifact


def test_the_direct_cli_writes_no_calibration_record(direct_cli) -> None:
    runner, _, out = direct_cli
    assert runner.main() == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert "calibration" not in json.dumps(manifest.get("config", {})).lower() \
        or manifest["calibration_owner"] is None
    assert not (out / "relation_budget.jsonl").exists()
    assert not (out / "micro_planner.jsonl").exists()
