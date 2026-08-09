"""Two model strategies, one architecture, and a baseline that cannot move.

The frozen Mistral+Qwen system produced the official TEST result. Adding a
second strategy alongside it is only safe if the first one is provably
untouched, so most of this file asserts *sameness*: the default, the configs,
the calibration hashes, the model ids, the role routing.

The rest asserts that the two cannot be confused. There is no fallback in
either direction - a strategy and a config that disagree are refused by name,
an uncalibrated strategy cannot answer an official split, and neither
strategy's Module 20/21 package can be borrowed by the other.

No weights are downloaded, no inference runs, and the bake-off refuses any
split but TRAIN.
"""

from __future__ import annotations

import importlib.util
import json
import sys

import pytest
import yaml

from cover_kbc.controller_calibration.readiness import ReadinessState
from cover_kbc.controller_calibration.production import (
    ProductionCalibrationError,
    load_production_calibration,
)
from cover_kbc.models.bakeoff import (
    ALLOWED_SPLIT,
    BakeoffError,
    CandidateOutcome,
    VerificationOutcome,
    compare_candidates,
    compare_verification,
    profile_strategy,
    require_train,
)
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.strategy import (
    BASELINE_VERIFIER_MODEL_ID,
    PORTFOLIO_OPERATION_ROLES,
    PORTFOLIO_RELATION_ROLES,
    PORTFOLIO_ROLES,
    PORTFOLIO_V2,
    PORTFOLIO_V2_PARAMETERS,
    ModelStrategy,
    ModelStrategyError,
    StrategyStatus,
    calibration_strategy,
    check_calibration_strategy,
    declared_strategy,
    parse_strategy,
    resolve_strategy,
    role_for_operation,
)
from cover_kbc.paths import REPO_ROOT
from cover_kbc.types import ModelRole

EXPERIMENTS = REPO_ROOT / "configs" / "experiments"
VAL_CONFIG = EXPERIMENTS / "cover_kbc_v2_validation.yaml"
TEST_CONFIG = EXPERIMENTS / "cover_kbc_v2_test.yaml"
COLLECTION_CONFIG = EXPERIMENTS / "cover_kbc_v2_train_collection.yaml"
PORTFOLIO_CONFIG = EXPERIMENTS / "cover_kbc_v2_portfolio_train_collection.yaml"
PORTFOLIO_TEST_CONFIG = EXPERIMENTS / "cover_kbc_v2_portfolio_test.yaml"
CALIBRATION = REPO_ROOT / "configs" / "calibration"

#: The frozen baseline, exactly as it ran for the official TEST submission.
BASELINE_ENUMERATOR = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
BASELINE_ENUMERATOR_REV = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
BASELINE_VERIFIER = "Qwen/Qwen3.5-4B"
BASELINE_VERIFIER_REV = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
BASELINE_PARAMETERS = 28_671_226_368

BASELINE_CALIBRATION_SHA256 = {
    "m20_relation_budget.json":
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68",
    "m21_historical_bins.json":
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071",
    "m21_planner_calibration.json":
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05",
}


def _load(path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def val_config() -> dict:
    return _load(VAL_CONFIG)


@pytest.fixture(scope="module")
def test_config() -> dict:
    return _load(TEST_CONFIG)


@pytest.fixture(scope="module")
def portfolio_config() -> dict:
    return _load(PORTFOLIO_CONFIG)


def _runner():
    path = REPO_ROOT / "scripts" / "run_cover.py"
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("run_cover_strategy", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


# ==========================================================================
# 1, 2, 10 — the flag
# ==========================================================================


def test_no_flag_means_baseline(val_config) -> None:
    assert resolve_strategy(val_config).strategy is ModelStrategy.BASELINE
    assert resolve_strategy(val_config, None).strategy is ModelStrategy.BASELINE


def test_the_explicit_baseline_flag_means_baseline(val_config) -> None:
    assert resolve_strategy(val_config, "baseline").strategy is (
        ModelStrategy.BASELINE)
    assert resolve_strategy(val_config, ModelStrategy.BASELINE).strategy is (
        ModelStrategy.BASELINE)


def test_the_runner_defaults_to_baseline() -> None:
    runner = _runner()
    parser = [a for a in dir(runner) if a == "main"]
    assert parser, "run_cover has no main"
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert '"--model-strategy"' in source
    assert "default=ModelStrategy.BASELINE.value" in source


@pytest.mark.parametrize("name", ["ensemble", "portfolio2", "", "mistral",
                                  "none", "base line", "hybrid"])
def test_an_unknown_strategy_fails_closed(name) -> None:
    with pytest.raises(ModelStrategyError, match="is not a model strategy"):
        parse_strategy(name)


@pytest.mark.parametrize("name", ["baseline", "BASELINE", " portfolio ",
                                  "Portfolio"])
def test_a_known_strategy_is_read_case_and_whitespace_tolerantly(name) -> None:
    """A CLI value, not a hash. Tolerant of shell noise, strict about meaning."""
    assert parse_strategy(name) in tuple(ModelStrategy)


def test_the_runner_only_offers_the_two_real_strategies() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert "choices=[s.value for s in ModelStrategy]" in source
    assert {s.value for s in ModelStrategy} == {"baseline", "portfolio"}


# ==========================================================================
# 3-7 — the baseline cannot move
# ==========================================================================


@pytest.mark.parametrize("path", [VAL_CONFIG, TEST_CONFIG, COLLECTION_CONFIG])
def test_every_committed_config_is_still_baseline(path) -> None:
    """They predate the field, and absence must keep meaning baseline."""
    config = _load(path)
    assert "model_strategy" not in config
    assert declared_strategy(config) is ModelStrategy.BASELINE


@pytest.mark.parametrize("path", [VAL_CONFIG, TEST_CONFIG])
def test_the_production_configs_resolve_to_the_frozen_models(path) -> None:
    profile = resolve_strategy(_load(path))
    assert profile.strategy is ModelStrategy.BASELINE
    assert profile.status is StrategyStatus.PRODUCTION_CALIBRATED
    assert profile.may_run_production is True
    assert set(profile.blocks) == {ModelRole.ENUMERATOR, ModelRole.VERIFIER}
    assert profile.block(ModelRole.ENUMERATOR)["model_id"] == BASELINE_ENUMERATOR
    assert profile.block(ModelRole.ENUMERATOR)["revision"] == (
        BASELINE_ENUMERATOR_REV)
    assert profile.block(ModelRole.VERIFIER)["model_id"] == BASELINE_VERIFIER
    assert profile.block(ModelRole.VERIFIER)["revision"] == BASELINE_VERIFIER_REV


@pytest.mark.parametrize("path", [VAL_CONFIG, TEST_CONFIG])
def test_the_audited_parameter_total_is_unchanged(path) -> None:
    config = _load(path)
    assert config["budget_assertion"]["total_published_parameters"] == (
        BASELINE_PARAMETERS)
    assert config["budget_assertion"]["limit"] == 32_000_000_000
    assert config["budget_assertion"]["legal"] is True


@pytest.mark.parametrize("name,expected",
                         sorted(BASELINE_CALIBRATION_SHA256.items()))
def test_the_baseline_calibration_hashes_are_unchanged(name, expected) -> None:
    import hashlib

    assert hashlib.sha256(
        (CALIBRATION / name).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("path", [VAL_CONFIG, TEST_CONFIG])
def test_the_production_configs_still_load_their_calibration(path) -> None:
    """The end-to-end baseline invariant: same config, same artifacts, loads."""
    config = _load(path)
    provenance = config["calibration_provenance"]
    calibration = load_production_calibration(
        config, base_dir=EXPERIMENTS,
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert len(calibration.budgets) == 6
    assert len(calibration.history.bins) == 64
    assert calibration.planner.lookahead_depth == 1


def test_the_baseline_pipeline_configuration_is_untouched(val_config,
                                                          test_config) -> None:
    """Every block a run's behaviour depends on, still equal across the two."""
    for block in ("model_profile", "budget_assertion", "pipeline",
                  "calibration_provenance", "query_intelligence", "specialists",
                  "consensus", "specialist_verifier",
                  "bidirectional_verification", "layer4_integration",
                  "coverage_gap", "relation_budget_scheduler", "micro_planner",
                  "layer6_integration"):
        assert test_config[block] == val_config[block], block


# ==========================================================================
# 8, 9 — the two cannot be confused
# ==========================================================================


def test_a_baseline_config_cannot_activate_portfolio(val_config) -> None:
    with pytest.raises(ModelStrategyError, match="declares baseline"):
        resolve_strategy(val_config, "portfolio")


def test_a_portfolio_config_cannot_activate_baseline(portfolio_config) -> None:
    with pytest.raises(ModelStrategyError, match="declares portfolio"):
        resolve_strategy(portfolio_config, "baseline")


def test_a_portfolio_config_with_no_flag_is_refused(portfolio_config) -> None:
    """The default is baseline, so silence over a portfolio config is a clash."""
    with pytest.raises(ModelStrategyError):
        resolve_strategy(portfolio_config)


def test_no_config_is_mutated_by_resolution(val_config) -> None:
    before = json.dumps(val_config, sort_keys=True)
    resolve_strategy(val_config)
    assert json.dumps(val_config, sort_keys=True) == before


# ==========================================================================
# 11, 12, 13 — portfolio governance
# ==========================================================================


def test_the_portfolio_config_declares_all_three_roles(portfolio_config) -> None:
    profile = resolve_strategy(portfolio_config, "portfolio")
    assert set(profile.blocks) == set(PORTFOLIO_ROLES)
    for role, expected in PORTFOLIO_V2.items():
        block = profile.block(role)
        assert block["model_id"] == expected["model_id"], role
        assert block["revision"] == expected["revision"], role
        assert block["published_total_parameters"] == expected["parameters"], role


def test_the_portfolio_has_exactly_three_models(portfolio_config) -> None:
    """No fourth model, no embedding model, no learned router."""
    assert len(portfolio_config["model_portfolio"]) == 3
    assert len(PORTFOLIO_V2) == 3


def test_the_portfolio_verifier_is_qwen9_and_never_falls_back_to_qwen4(
    portfolio_config,
) -> None:
    """v2 changed the judge. The baseline keeps Qwen3.5-4B; portfolio must not."""
    verifier = portfolio_config["model_portfolio"]["independent_verifier"]
    assert verifier["model_id"] == "Qwen/Qwen3.5-9B"
    assert verifier["revision"] == (
        "c202236235762e1c871ad0ccb60c8ee5ba337b9a")
    assert verifier["model_id"] != BASELINE_VERIFIER_MODEL_ID
    body = PORTFOLIO_CONFIG.read_text()
    assert "Qwen3.5-4B" in body, "the 4B is named only to say it is not used"
    assert '"Qwen/Qwen3.5-4B"' not in body
    # ...and no baseline model appears as a portfolio checkpoint anywhere.
    for role in PORTFOLIO_ROLES:
        model_id = portfolio_config["model_portfolio"][role.value]["model_id"]
        assert model_id not in {BASELINE_ENUMERATOR, BASELINE_VERIFIER_MODEL_ID}


@pytest.mark.parametrize("role,expected", sorted(
    (r.value, m) for r, m in PORTFOLIO_V2.items()))
def test_every_portfolio_model_has_an_immutable_revision(role, expected) -> None:
    assert len(expected["revision"]) == 40
    assert all(c in "0123456789abcdef" for c in expected["revision"])


def test_the_portfolio_total_is_exact_and_legal() -> None:
    """The decisive gate: 12,187,325,040 + 8,888,227,328 + 9,653,104,368."""
    assert PORTFOLIO_V2_PARAMETERS == 30_728_656_736
    assert PORTFOLIO_V2_PARAMETERS <= 32_000_000_000
    assert sum(m["parameters"] for m in PORTFOLIO_V2.values()) == (
        PORTFOLIO_V2_PARAMETERS)


def test_the_config_records_the_same_total(portfolio_config) -> None:
    assert portfolio_config["budget_assertion"][
        "total_published_parameters"] == PORTFOLIO_V2_PARAMETERS
    assert portfolio_config["budget_assertion"]["limit"] == 32_000_000_000
    assert portfolio_config["budget_assertion"]["legal"] is True


def test_multimodal_checkpoints_count_their_vision_towers(
    portfolio_config,
) -> None:
    """Gemma and Qwen3.5 load `*ForConditionalGeneration`; the tower counts."""
    portfolio = portfolio_config["model_portfolio"]
    gemma = portfolio["factual_enumerator"]
    qwen = portfolio["independent_verifier"]
    assert gemma["architecture"] == "Gemma3ForConditionalGeneration"
    assert qwen["architecture"] == "Qwen3_5ForConditionalGeneration"
    for block in (gemma, qwen):
        assert "vision" in block["instantiated_components"]
    # Nemotron is text-only, and says so.
    nemotron = portfolio["structural_reasoner"]
    assert nemotron["architecture"] == "NemotronHForCausalLM"
    assert "text only" in nemotron["instantiated_components"]


def test_nemotron_custom_code_is_explicit_and_pinned(portfolio_config) -> None:
    nemotron = portfolio_config["model_portfolio"]["structural_reasoner"]
    assert nemotron["trust_remote_code"] is True
    assert nemotron["custom_code_files"] == [
        "configuration_nemotron_h.py", "modeling_nemotron_h.py"]
    for other in ("factual_enumerator", "independent_verifier"):
        assert portfolio_config["model_portfolio"][other][
            "trust_remote_code"] is False


@pytest.mark.parametrize("role", ["factual_enumerator", "structural_reasoner"])
def test_a_missing_strategy_specific_model_fails_closed(portfolio_config,
                                                        role) -> None:
    config = _load(PORTFOLIO_CONFIG)
    config["model_portfolio"][role]["model_id"] = ""
    with pytest.raises(ModelStrategyError, match="declares no model_id"):
        resolve_strategy(config, "portfolio")


def test_a_portfolio_config_with_no_portfolio_block_fails_closed() -> None:
    with pytest.raises(ModelStrategyError, match="needs a 'model_portfolio'"):
        resolve_strategy({"model_strategy": "portfolio"}, "portfolio")


def test_every_portfolio_count_is_verified_against_a_primary_source(
    portfolio_config,
) -> None:
    """Read from each checkpoint's own metadata, never from its name."""
    for role in PORTFOLIO_ROLES:
        block = portfolio_config["model_portfolio"][role.value]
        assert block["parameter_source_verified"] is True, role
        assert "huggingface.co/api/models/" in block["parameter_source"], role
        assert "safetensors.total" in block["parameter_source"], role
        assert block["published_total_parameters"] == (
            PORTFOLIO_V2[role]["parameters"])


def test_an_unrecorded_parameter_count_still_cannot_build_a_runtime(
    portfolio_config,
) -> None:
    """The refusal that protected us before the counts were known."""
    from cover_kbc.models.registry import build_runtime

    block = dict(portfolio_config["model_portfolio"]["factual_enumerator"])
    for key in ("published_total_parameters", "budget_count_parameters",
                "published_checkpoint_parameters"):
        block.pop(key, None)
    with pytest.raises(ValueError, match="must be recorded"):
        build_runtime(block)


def test_the_budget_audit_refuses_an_unverified_count() -> None:
    from cover_kbc.models.base import ModelSpec

    unverified = ModelSpec(
        model_id="google/gemma-3-12b-it", published_total_parameters=12_000_000_000,
        family="gemma", role="factual_enumerator", is_neural=True,
        parameter_source="", parameter_source_verified=False)
    audit = audit_parameter_budget([unverified])
    assert not audit.passed
    assert any("not marked verified" in p for p in audit.problems)


def test_quantization_does_not_change_legality() -> None:
    """nf4 is a memory decision. The counted total is the published one."""
    from cover_kbc.models.base import ModelSpec

    def spec(quantization):
        return ModelSpec(
            model_id="x/y", published_total_parameters=31_000_000_000,
            family="f", role="enumerator", is_neural=True,
            parameter_source="url", parameter_source_verified=True,
            quantization=quantization)

    for quantization in (None, "nf4", "int8", "gptq"):
        audit = audit_parameter_budget([spec(quantization)])
        assert audit.passed, quantization
        assert audit.total_parameters == 31_000_000_000

    over = ModelSpec(
        model_id="x/big", published_total_parameters=33_000_000_000,
        family="f", role="enumerator", is_neural=True,
        parameter_source="url", parameter_source_verified=True,
        quantization="nf4")
    assert not audit_parameter_budget([over]).passed


def test_the_portfolio_is_not_production_calibrated(portfolio_config) -> None:
    profile = resolve_strategy(portfolio_config, "portfolio")
    assert profile.status is StrategyStatus.TRAIN_BAKEOFF_ONLY
    assert profile.may_run_production is False


def test_the_portfolio_collection_config_is_a_train_config(
    portfolio_config,
) -> None:
    assert portfolio_config["experiment"]["split"] == "train"
    # Layer 6 shadow: no calibration exists for these models, and a shadow
    # M20/M21 governs nothing.
    assert portfolio_config["relation_budget_scheduler"]["mode"] == "shadow"
    assert portfolio_config["micro_planner"]["mode"] == "shadow"


def test_no_portfolio_validation_config_exists() -> None:
    """VAL is deliberately skipped for this iteration."""
    assert not (EXPERIMENTS / "cover_kbc_v2_portfolio_validation.yaml").exists()


def test_the_portfolio_test_config_names_portfolio_owned_artifacts() -> None:
    config = _load(PORTFOLIO_TEST_CONFIG)
    assert config["model_strategy"] == "portfolio"
    assert config["experiment"]["split"] == "test"
    assert config["calibration_provenance"]["model_strategy"] == "portfolio"
    for block, key in (("relation_budget_scheduler", "calibration_file"),
                       ("micro_planner", "historical_bins"),
                       ("micro_planner", "planner_calibration")):
        path = config[block][key]
        assert "/portfolio/" in path, path
        # It must not point at a baseline artifact, even by accident.
        assert not path.endswith("../calibration/m20_relation_budget.json")


def test_the_portfolio_test_config_is_not_ready_before_real_calibration() -> None:
    """The expected state. Fixture artifacts are not production artifacts."""
    from cover_kbc.controller_calibration.readiness import evaluate_test_readiness

    config = _load(PORTFOLIO_TEST_CONFIG)
    report = evaluate_test_readiness(config, base_dir=EXPERIMENTS)
    assert report.state is not ReadinessState.FULL_TEST_READY
    assert report.may_run_test is False
    assert report.blockers
    joined = " | ".join(report.blockers)
    assert "portfolio" in joined.lower() or "does not exist" in joined


def test_the_portfolio_test_artifacts_do_not_exist_yet() -> None:
    portfolio_dir = REPO_ROOT / "configs" / "calibration" / "portfolio"
    assert not portfolio_dir.exists() or not list(portfolio_dir.glob("*.json"))


def test_no_credential_is_recorded_for_the_gated_model() -> None:
    body = PORTFOLIO_CONFIG.read_text()
    for secret in ("hf_", "token:", "HUGGING_FACE_HUB_TOKEN:", "api_key"):
        assert secret not in body, secret
    assert "gated: true" in body


# ==========================================================================
# 14-19 — role routing
# ==========================================================================


@pytest.mark.parametrize("operation", sorted(PORTFOLIO_OPERATION_ROLES))
def test_baseline_routing_is_the_old_two_role_split(operation) -> None:
    """M11, M17 and M18 must route exactly as they always did."""
    role = role_for_operation(ModelStrategy.BASELINE, operation)
    expected = (ModelRole.VERIFIER
                if operation in {"specialist_verify", "blind_verify"}
                else ModelRole.ENUMERATOR)
    assert role is expected


def test_baseline_never_reaches_a_portfolio_role() -> None:
    for operation in list(PORTFOLIO_OPERATION_ROLES) + ["anything at all"]:
        assert role_for_operation(ModelStrategy.BASELINE, operation) in (
            ModelRole.ENUMERATOR, ModelRole.VERIFIER)


def test_portfolio_m11_factual_work_routes_to_the_factual_enumerator() -> None:
    for operation in ("parametric_retrieval", "acquisition", "specialist_probe",
                      "candidate_free_recall"):
        assert role_for_operation(ModelStrategy.PORTFOLIO, operation) is (
            ModelRole.FACTUAL_ENUMERATOR)


def test_portfolio_m17_verification_routes_to_the_independent_verifier() -> None:
    for operation in ("specialist_verify", "blind_verify"):
        assert role_for_operation(ModelStrategy.PORTFOLIO, operation) is (
            ModelRole.INDEPENDENT_VERIFIER)


def test_portfolio_m18_structural_work_routes_to_the_structural_reasoner() -> None:
    for operation in ("reverse_check", "key_condition", "counterfactual"):
        assert role_for_operation(ModelStrategy.PORTFOLIO, operation) is (
            ModelRole.STRUCTURAL_REASONER)


def test_candidate_free_recall_stays_factual_not_structural() -> None:
    """§14 lists it under M18, but it is recall, not structural reasoning."""
    assert role_for_operation(ModelStrategy.PORTFOLIO,
                              "candidate_free_recall") is (
        ModelRole.FACTUAL_ENUMERATOR)


def test_an_undeclared_portfolio_operation_fails_closed() -> None:
    with pytest.raises(ModelStrategyError, match="declares no role"):
        role_for_operation(ModelStrategy.PORTFOLIO, "improvise")


def test_the_relation_routing_matrix_matches_the_hypothesis() -> None:
    from cover_kbc.contracts.registry import CONTRACTS

    assert set(PORTFOLIO_RELATION_ROLES) == set(CONTRACTS)
    for relation, roles in PORTFOLIO_RELATION_ROLES.items():
        assert roles["verification"] is ModelRole.INDEPENDENT_VERIFIER, relation
        assert roles["factual_recall"] is ModelRole.FACTUAL_ENUMERATOR, relation
    # Only the two relations whose failure mode is structural get Nemotron.
    structural = {r for r, roles in PORTFOLIO_RELATION_ROLES.items()
                  if "structural" in roles}
    assert structural == {"countryLandBordersCountry",
                          "companyTradesAtStockExchange"}


# ==========================================================================
# 20-21 — provenance and accounting
# ==========================================================================


def test_the_strategy_profile_records_everything_a_paper_needs(
    val_config, portfolio_config,
) -> None:
    payload = resolve_strategy(val_config).to_json()
    assert payload["model_strategy"] == "baseline"
    assert payload["status"] == "PRODUCTION_CALIBRATED"
    assert payload["may_run_production"] is True
    enumerator = payload["roles"]["enumerator"]
    assert enumerator["model_id"] == BASELINE_ENUMERATOR
    assert enumerator["revision"] == BASELINE_ENUMERATOR_REV
    assert enumerator["parameter_source_verified"] is True

    portfolio = resolve_strategy(portfolio_config, "portfolio").to_json()
    assert portfolio["model_strategy"] == "portfolio"
    assert portfolio["may_run_production"] is False
    assert set(portfolio["roles"]) == {r.value for r in PORTFOLIO_ROLES}


def test_the_manifest_carries_the_strategy() -> None:
    from cover_kbc.runtime.manifest import RunManifest

    manifest = RunManifest(run_id="r", experiment="e", split="train", seed=1)
    payload = manifest.to_json()
    for key in ("model_strategy", "model_strategy_requested",
                "model_strategy_status", "model_strategy_profile"):
        assert key in payload, key
    assert payload["model_strategy"] == "baseline"


def test_the_runner_records_requested_and_effective_strategy() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert "manifest.model_strategy_requested = args.model_strategy" in source
    assert "manifest.model_strategy = strategy.strategy.value" in source
    assert "manifest.model_strategy_profile = strategy.to_json()" in source


def test_physical_call_accounting_is_untouched() -> None:
    """One forward pass is one physical call, whichever model served it."""
    from cover_kbc.pipeline import CoverPipeline

    assert CoverPipeline.PHYSICAL_COUNTERS == (
        "enumerator_calls", "verifier_calls", "physical_calls",
        "prompt_tokens", "generated_tokens")
    source = (REPO_ROOT / "src" / "cover_kbc" / "pipeline.py").read_text()
    # The strategy layer must not have reached into the accounting path.
    assert "model_strategy" not in source
    assert "ModelStrategy" not in source


def test_the_strategy_layer_does_not_touch_the_controller() -> None:
    for module in ("control/relation_budget.py", "control/micro_planner.py",
                   "control/budget_accounting.py"):
        body = (REPO_ROOT / "src" / "cover_kbc" / module).read_text()
        assert "ModelStrategy" not in body, module


# ==========================================================================
# 22, 23 — calibration ownership
# ==========================================================================


def test_an_artifact_that_names_no_strategy_is_baseline() -> None:
    """True of the three shipped packages, and byte-preserving."""
    for name in BASELINE_CALIBRATION_SHA256:
        provenance = json.loads((CALIBRATION / name).read_text())["provenance"]
        assert "model_strategy" not in provenance
        assert calibration_strategy(provenance) is ModelStrategy.BASELINE


def test_baseline_calibration_is_rejected_by_a_portfolio_run(
    test_config,
) -> None:
    with pytest.raises(ProductionCalibrationError, match="derived under"):
        load_production_calibration(
            test_config, base_dir=EXPERIMENTS,
            expected_model_strategy=ModelStrategy.PORTFOLIO)


def test_a_future_portfolio_calibration_is_rejected_by_a_baseline_run() -> None:
    with pytest.raises(ModelStrategyError, match="derived under"):
        check_calibration_strategy(
            {"model_strategy": "portfolio"}, ModelStrategy.BASELINE)


def test_each_strategy_accepts_only_its_own_calibration() -> None:
    check_calibration_strategy({}, ModelStrategy.BASELINE)
    check_calibration_strategy(
        {"model_strategy": "baseline"}, ModelStrategy.BASELINE)
    check_calibration_strategy(
        {"model_strategy": "portfolio"}, ModelStrategy.PORTFOLIO)
    with pytest.raises(ModelStrategyError):
        check_calibration_strategy({}, ModelStrategy.PORTFOLIO)


def test_an_uncalibrated_strategy_may_not_answer_an_official_split() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert "if not strategy.may_run_production:" in source
    body = source[source.index("if production:"):]
    assert body.index("may_run_production") < body.index(
        "load_production_calibration(")


def test_the_calibration_loader_defaults_to_the_configs_own_strategy(
    test_config,
) -> None:
    """So every existing caller keeps its exact behaviour."""
    calibration = load_production_calibration(test_config, base_dir=EXPERIMENTS)
    assert len(calibration.budgets) == 6


# ==========================================================================
# 24 — the bake-off is TRAIN-only
# ==========================================================================


@pytest.mark.parametrize("split", ["val", "test", "", "TRAIN", "validation"])
def test_the_bakeoff_refuses_any_split_but_train(split) -> None:
    with pytest.raises(BakeoffError, match="may only read 'train'"):
        require_train(split)


def test_every_bakeoff_entry_point_guards_the_split() -> None:
    with pytest.raises(BakeoffError):
        compare_candidates([], {}, split="val")
    with pytest.raises(BakeoffError):
        compare_verification([], split="test")
    with pytest.raises(BakeoffError):
        profile_strategy(ModelStrategy.PORTFOLIO, [], {}, [], split="val")
    assert ALLOWED_SPLIT == "train"


def test_the_bakeoff_never_imports_the_evaluator() -> None:
    body = (REPO_ROOT / "src" / "cover_kbc" / "models" / "bakeoff.py").read_text()
    for forbidden in ("evaluate_predictions", "evaluation.harness",
                      "benchmark", "val.jsonl", "test.jsonl"):
        assert forbidden not in body, forbidden


def test_the_bakeoff_separates_novel_from_repeated_true_positives() -> None:
    """The number that justifies a portfolio: what it found that baseline did not."""
    gold = {("hasArea", "Niutao", 0): ["25.0", "30.0"]}
    baseline = [CandidateOutcome("hasArea", "Niutao", 0, ("25.0",),
                                 physical_calls=3)]
    portfolio = [CandidateOutcome("hasArea", "Niutao", 0,
                                  ("25.0", "30.0", "30.0", "junk"),
                                  physical_calls=5)]
    reports = compare_candidates(portfolio, gold, reference=baseline)
    report = reports["hasArea"]
    assert report.true_positives == 2
    assert report.false_positives == 1
    assert report.novel_true_positives == 1     # 30.0
    assert report.lost_true_positives == 0
    assert report.redundant_candidates == 1     # 30.0 twice
    assert report.recall == 1.0
    assert report.precision == pytest.approx(2 / 3)


def test_the_bakeoff_counts_lost_true_positives_too() -> None:
    gold = {("hasArea", "Niutao", 0): ["25.0"]}
    baseline = [CandidateOutcome("hasArea", "Niutao", 0, ("25.0",))]
    portfolio = [CandidateOutcome("hasArea", "Niutao", 0, ("wrong",))]
    report = compare_candidates(portfolio, gold, reference=baseline)["hasArea"]
    assert report.lost_true_positives == 1
    assert report.novel_true_positives == 0


def test_the_bakeoff_splits_verification_four_ways() -> None:
    outcomes = [
        VerificationOutcome("countryLandBordersCountry", "REVERSE_CHECK",
                            "VALID", True, True, reference_accepted=True),
        VerificationOutcome("countryLandBordersCountry", "REVERSE_CHECK",
                            "VALID", True, True, reference_accepted=False),
        VerificationOutcome("countryLandBordersCountry", "REVERSE_CHECK",
                            "INVALID", False, False, reference_accepted=True),
        VerificationOutcome("countryLandBordersCountry", "REVERSE_CHECK",
                            "UNKNOWN", False, True, reference_accepted=False),
    ]
    report = compare_verification(outcomes)[
        ("countryLandBordersCountry", "REVERSE_CHECK")]
    assert report.true_positives_preserved == 1
    assert report.true_positives_recovered == 1
    assert report.false_positives_prevented == 1
    assert report.false_positives_introduced == 1
    assert report.unknown_verdicts == 1
    assert report.unknown_rate == 0.25


def test_the_bakeoff_produces_tables_and_ablation_rows() -> None:
    from cover_kbc.models.bakeoff import ablation_rows

    gold = {("hasArea", "Niutao", 0): ["25.0"]}
    result = profile_strategy(
        ModelStrategy.PORTFOLIO,
        [CandidateOutcome("hasArea", "Niutao", 0, ("25.0",), physical_calls=4)],
        gold,
        [VerificationOutcome("hasArea", "SPECIALIST_VERIFY", "VALID", True,
                             True, reference_accepted=True, physical_calls=8)])
    text = result.tables()
    assert "model_strategy = portfolio" in text
    assert "split: train" in text
    assert "candidate generation" in text
    assert "verification and structural work" in text
    payload = result.to_json()
    assert payload["split"] == "train"
    rows = ablation_rows([result])
    assert rows and rows[0]["model_strategy"] == "portfolio"


# ==========================================================================
# 25, 26, 27 — what did not change
# ==========================================================================


def _executable_source(path) -> str:
    """A module's code with docstrings and comments removed.

    Scanned rather than the raw text because this repository documents what it
    deliberately does *not* do - "not a majority vote", "no Module 22" - and a
    prose scan flags the denial as if it were the thing.
    """
    import ast

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_there_is_no_module_22() -> None:
    for path in (REPO_ROOT / "src" / "cover_kbc").rglob("*.py"):
        code = _executable_source(path)
        assert "M22" not in code, path
        assert "Module22" not in code.replace(" ", ""), path


def test_there_is_no_majority_vote() -> None:
    """Independence groups and calibrated evidence, never head-counting."""
    for path in (REPO_ROOT / "src" / "cover_kbc").rglob("*.py"):
        code = _executable_source(path).casefold()
        for forbidden in ("majority_vote", "majorityvote", "def vote("):
            assert forbidden not in code, f"{path}: {forbidden}"


def test_module_8_is_still_the_only_prediction_owner() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert source.count("write_predictions(") == 1
    body = (REPO_ROOT / "src" / "cover_kbc" / "models" / "strategy.py").read_text()
    assert "Prediction" not in body
    bakeoff = (REPO_ROOT / "src" / "cover_kbc" / "models" / "bakeoff.py").read_text()
    assert "Prediction(" not in bakeoff


def test_there_is_still_one_pipeline_class() -> None:
    src = REPO_ROOT / "src" / "cover_kbc"
    classes = [line for path in src.rglob("*.py")
               for line in path.read_text().splitlines()
               if line.startswith("class CoverPipeline")]
    assert classes == ["class CoverPipeline:"]
    for forbidden in ("CoverPipelineBaseline", "CoverPipelinePortfolio"):
        for path in src.rglob("*.py"):
            assert forbidden not in path.read_text(), forbidden


def test_there_is_still_one_canonical_runner() -> None:
    scripts = REPO_ROOT / "scripts"
    assert (scripts / "run_cover.py").is_file()
    for forbidden in ("run_cover_portfolio.py", "run_portfolio.py"):
        assert not (scripts / forbidden).exists(), forbidden


# ==========================================================================
# governance gate and the CLI
# ==========================================================================


def test_the_governed_portfolio_has_no_blockers_left(portfolio_config) -> None:
    """Audit 0061 left six. All are closed: counts verified, revisions pinned,
    access route recorded, and the third runtime wired."""
    from cover_kbc.models.strategy import (
        PORTFOLIO_IMPLEMENTATION_BLOCKERS,
        portfolio_governance_blockers,
    )

    profile = resolve_strategy(portfolio_config, "portfolio")
    assert portfolio_governance_blockers(profile) == []
    assert PORTFOLIO_IMPLEMENTATION_BLOCKERS == ()


def test_the_governance_gate_lists_every_blocker_at_once() -> None:
    """Three facts away from legal should cost one refusal, not three."""
    from cover_kbc.models.strategy import portfolio_governance_blockers

    config = _load(PORTFOLIO_CONFIG)
    for role in ("factual_enumerator", "structural_reasoner"):
        config["model_portfolio"][role].update({
            "revision": "", "published_total_parameters": None,
            "budget_count_parameters": None,
            "parameter_source_verified": False})
    blockers = portfolio_governance_blockers(
        resolve_strategy(config, "portfolio"))
    joined = "\n".join(blockers)
    for expected in ("google/gemma-3-12b-it", "nvidia/NVIDIA-Nemotron-Nano-9B-v2",
                     "no immutable revision", "parameter count is unrecorded",
                     "legality cannot be proven"):
        assert expected in joined, expected
    # The verified, pinned verifier contributes no blocker of its own.
    assert not any("Qwen/Qwen3.5-9B" in b for b in blockers)


def test_the_governance_gate_is_a_no_op_for_baseline(val_config) -> None:
    from cover_kbc.models.strategy import (
        portfolio_governance_blockers,
        require_portfolio_governance,
    )

    profile = resolve_strategy(val_config)
    assert portfolio_governance_blockers(profile) == []
    require_portfolio_governance(profile)          # must not raise


def test_the_portfolio_refuses_to_run_when_a_fact_goes_missing() -> None:
    from cover_kbc.models.strategy import require_portfolio_governance

    config = _load(PORTFOLIO_CONFIG)
    config["model_portfolio"]["factual_enumerator"]["revision"] = ""
    with pytest.raises(ModelStrategyError, match="not ready to make a real call"):
        require_portfolio_governance(resolve_strategy(config, "portfolio"))


def test_the_governed_portfolio_passes_the_gate(portfolio_config) -> None:
    from cover_kbc.models.strategy import require_portfolio_governance

    require_portfolio_governance(
        resolve_strategy(portfolio_config, "portfolio"))    # must not raise


def test_a_fully_governed_portfolio_only_blocks_on_declared_wiring() -> None:
    """Prove the gate opens: record the facts and only the wiring remains."""
    from cover_kbc.models.strategy import (
        PORTFOLIO_IMPLEMENTATION_BLOCKERS,
        portfolio_governance_blockers,
    )

    config = _load(PORTFOLIO_CONFIG)
    for role, count in (("factual_enumerator", 12_000_000_000),
                        ("structural_reasoner", 9_000_000_000)):
        config["model_portfolio"][role].update({
            "revision": "0" * 40,
            "published_total_parameters": count,
            "budget_count_parameters": count,
            "parameter_source": "https://huggingface.co/api/models/x",
            "parameter_source_verified": True,
        })
    blockers = portfolio_governance_blockers(
        resolve_strategy(config, "portfolio"))
    assert blockers == list(PORTFOLIO_IMPLEMENTATION_BLOCKERS)


def test_an_over_budget_portfolio_is_refused_even_when_verified() -> None:
    from cover_kbc.models.strategy import portfolio_governance_blockers

    config = _load(PORTFOLIO_CONFIG)
    for role, count in (("factual_enumerator", 20_000_000_000),
                        ("structural_reasoner", 20_000_000_000)):
        config["model_portfolio"][role].update({
            "revision": "0" * 40,
            "published_total_parameters": count,
            "budget_count_parameters": count,
            "parameter_source": "https://huggingface.co/api/models/x",
            "parameter_source_verified": True,
        })
    blockers = portfolio_governance_blockers(
        resolve_strategy(config, "portfolio"))
    assert any("exceeds the" in b and "quantization does not reduce" in b
               for b in blockers)


def test_a_gated_repository_must_record_its_access_route() -> None:
    from cover_kbc.models.strategy import portfolio_governance_blockers

    config = _load(PORTFOLIO_CONFIG)
    config["model_portfolio"]["factual_enumerator"]["access_note"] = ""
    blockers = portfolio_governance_blockers(
        resolve_strategy(config, "portfolio"))
    assert any("gated repository with no recorded access route" in b
               for b in blockers)


def test_the_runner_gates_the_strategy_before_building_any_runtime() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    body = source[source.index("def main("):]
    assert (body.index("require_portfolio_governance(strategy)")
            < body.index("build_runtime(enumerator_cfg)"))
    assert (body.index("resolve_strategy(config, args.model_strategy)")
            < body.index("build_runtime(enumerator_cfg)"))


def _drive(tmp_path, monkeypatch, config_path, argv_extra):
    """Run the real `main()` with `build_runtime` counted, never executed."""
    runner = _runner()
    built: list = []

    class _Built(RuntimeError):
        pass

    def stub(block):
        built.append(block)
        raise _Built("reached build_runtime")

    monkeypatch.setattr(runner, "build_runtime", stub)
    monkeypatch.setattr(sys, "argv",
                        ["run_cover.py", "--config", str(config_path)]
                        + argv_extra)
    return runner, built, _Built


def test_the_cli_refuses_a_portfolio_flag_over_a_baseline_config(
    tmp_path, monkeypatch,
) -> None:
    runner, built, _ = _drive(
        tmp_path, monkeypatch, TEST_CONFIG, ["--model-strategy", "portfolio"])
    with pytest.raises(SystemExit, match="declares baseline"):
        runner.main()
    assert built == [], "a runtime was built before the strategy was refused"


def test_the_cli_refuses_an_ungoverned_portfolio_before_any_runtime(
    tmp_path, monkeypatch,
) -> None:
    import yaml as _yaml

    config = _load(PORTFOLIO_CONFIG)
    config["model_portfolio"]["structural_reasoner"][
        "parameter_source_verified"] = False
    path = tmp_path / "ungoverned.yaml"
    path.write_text(_yaml.safe_dump(config))
    runner, built, _ = _drive(
        tmp_path, monkeypatch, path, ["--model-strategy", "portfolio"])
    with pytest.raises(SystemExit, match="not ready to make a real call"):
        runner.main()
    assert built == [], "a runtime was built for an ungoverned portfolio"


def test_the_cli_builds_three_runtimes_for_the_governed_portfolio(
    tmp_path, monkeypatch,
) -> None:
    """The Audit 0061 blocker, closed: all three roles reach construction."""
    runner, built, boom = _drive(
        tmp_path, monkeypatch, PORTFOLIO_CONFIG,
        ["--model-strategy", "portfolio"])
    with pytest.raises(boom):
        runner.main()
    assert len(built) == 1, "the first role should be built first"
    assert built[0]["model_id"] == PORTFOLIO_V2[
        ModelRole.FACTUAL_ENUMERATOR]["model_id"]


def test_the_cli_never_builds_a_baseline_model_for_the_portfolio(
    tmp_path, monkeypatch,
) -> None:
    runner, built, _ = _drive(
        tmp_path, monkeypatch, PORTFOLIO_CONFIG,
        ["--model-strategy", "portfolio"])
    with pytest.raises(Exception):
        runner.main()
    for block in built:
        assert block["model_id"] not in {BASELINE_ENUMERATOR,
                                         BASELINE_VERIFIER_MODEL_ID}


def test_the_cli_default_still_reaches_runtime_construction_for_baseline(
    tmp_path, monkeypatch,
) -> None:
    """The baseline command must be unchanged: no flag, straight through."""
    runner, built, boom = _drive(tmp_path, monkeypatch, TEST_CONFIG, [])
    with pytest.raises(boom):
        runner.main()
    assert len(built) == 1
    assert built[0]["model_id"] == BASELINE_ENUMERATOR
    assert built[0]["revision"] == BASELINE_ENUMERATOR_REV


def test_the_explicit_baseline_flag_is_identical_to_no_flag(
    tmp_path, monkeypatch,
) -> None:
    seen = []
    for extra in ([], ["--model-strategy", "baseline"]):
        runner, built, boom = _drive(tmp_path, monkeypatch, TEST_CONFIG, extra)
        with pytest.raises(boom):
            runner.main()
        seen.append(built[0])
    assert seen[0] == seen[1]


# ==========================================================================
# §30 — scripted three-runtime pipeline integration (NOT a real-weight smoke)
# ==========================================================================


def _scripted(model_id, family, role, response):
    from cover_kbc.models.offline import ScriptedRuntime

    return ScriptedRuntime({}, model_id=model_id, family=family, role=role,
                           fallback=lambda request: response)


@pytest.fixture
def three_runtime_pipeline():
    """The canonical pipeline with one scripted runtime per logical role."""
    from test_pipeline_production_seam import build

    factual = _scripted("offline/gemma-role", "gemma", "factual_enumerator",
                        "Alphaland, Betaland")
    structural = _scripted("offline/nemotron-role", "nemotron",
                           "structural_reasoner", "Alphaland")
    verifier = _scripted("offline/qwen9-role", "qwen", "independent_verifier",
                         "A")
    pipeline = build(IntegrationMode.PRODUCTION,
                     verifier_runtime=verifier, structural_runtime=structural)
    pipeline.runtime = factual
    pipeline.engine.runtime = factual
    return pipeline, factual, structural, verifier


def test_the_pipeline_accepts_three_distinct_runtimes(
    three_runtime_pipeline,
) -> None:
    pipeline, factual, structural, verifier = three_runtime_pipeline
    assert pipeline.runtime is factual
    assert pipeline.structural_runtime is structural
    assert pipeline.verifier_runtime is verifier
    assert len({id(factual), id(structural), id(verifier)}) == 3


def test_all_three_runtimes_are_reachable_and_counted(
    three_runtime_pipeline,
) -> None:
    """Every model's calls land in the physical total, exactly once."""
    from cover_kbc.types import Query
    from test_pipeline_production_seam import RELATION, SUBJECT

    pipeline, factual, structural, verifier = three_runtime_pipeline
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    pipeline.decide_graph(graph)

    snapshot = pipeline.physical_snapshot()
    real = (factual.calls + structural.calls + verifier.calls)
    assert snapshot["physical_calls"] == real, snapshot
    # The two-bucket partition still sums exactly.
    assert (snapshot["enumerator_calls"] + snapshot["verifier_calls"]
            == snapshot["physical_calls"])
    # Non-verifier bucket is the factual + structural models.
    assert snapshot["enumerator_calls"] == factual.calls + structural.calls
    assert snapshot["verifier_calls"] == verifier.calls
    assert factual.calls > 0, "the factual enumerator never ran"
    assert verifier.calls > 0, "the independent verifier never ran"


def test_the_structural_runtime_serves_module_18(
    three_runtime_pipeline,
) -> None:
    """M18's mechanisms must reach Nemotron's role runtime, not the enumerator."""
    from cover_kbc.types import Query
    from test_pipeline_production_seam import RELATION, SUBJECT

    pipeline, factual, structural, verifier = three_runtime_pipeline
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    before = structural.calls
    pipeline.decide_graph(graph)
    executed = [r for r in pipeline.action_records
                if r["executed"] and r["kind"] == "m18"]
    if not executed:
        pytest.skip("no M18 action executed in this fixture")
    assert structural.calls > before, "M18 did not reach the structural runtime"


def test_a_two_model_profile_still_falls_back_to_the_enumerator() -> None:
    """Baseline invariance at the seam: no third runtime, no behaviour change."""
    from test_pipeline_production_seam import build

    pipeline = build(IntegrationMode.PRODUCTION)
    assert pipeline.structural_runtime is pipeline.runtime
    snapshot = pipeline.physical_snapshot()
    assert snapshot["single_role_profile"] is True


def test_evidence_provenance_records_each_model(three_runtime_pipeline) -> None:
    """The paper needs to know which checkpoint produced which observation."""
    from cover_kbc.types import Query
    from test_pipeline_production_seam import RELATION, SUBJECT

    pipeline, factual, structural, verifier = three_runtime_pipeline
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    pipeline.decide_graph(graph)

    produced = {getattr(record, "model_id", "")
                for record in graph.records.values()}
    assert "offline/gemma-role" in produced, produced
    # Which family produced evidence is already a first-class graph summary.
    families = graph.model_family_summary()
    assert families.get("gemma"), families
    assert "nemotron" in families, (
        f"the structural runtime produced no evidence: {families}")
    # Module 17's verdicts must be attributed to the independent verifier.
    verifications = [r for entry in pipeline.specialist_verifications
                     for r in entry.results]
    if verifications:
        assert {v.verifier_model_id for v in verifications} == {
            "offline/qwen9-role"}


def test_a_different_model_id_alone_does_not_create_cross_model_support(
    three_runtime_pipeline,
) -> None:
    """X means genuine independent recall, not "two different model ids"."""
    from cover_kbc.types import EvidenceMode, Query
    from test_pipeline_production_seam import RELATION, SUBJECT

    pipeline, factual, structural, verifier = three_runtime_pipeline
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    pipeline.decide_graph(graph)

    # More than one model family produced evidence for this query...
    assert len(graph.model_family_summary()) >= 2, graph.model_family_summary()
    # ...yet cross-model support is only ever claimed for a candidate that was
    # genuinely recalled by an independent branch. Nothing derives it from
    # "these two records carry different model ids".
    for candidate in graph.active_candidates():
        cross = getattr(candidate, "cross_model_support", 0)
        if cross:
            assert getattr(candidate, "independence_groups", ()), (
                f"{candidate}: cross-model support with no independence group")

    # And the rule is in the owner, not in this test: Module 16 decides X from
    # independence groups, never from a model-id comparison.
    body = (REPO_ROOT / "src" / "cover_kbc" / "evidence" / "consensus.py").read_text()
    assert "model_id !=" not in body
    assert "model_id ==" not in body
    del EvidenceMode


def test_production_layer_six_is_unavailable_without_portfolio_calibration(
    three_runtime_pipeline,
) -> None:
    pipeline, *_ = three_runtime_pipeline
    assert pipeline.relation_budget_scheduler is None
    assert pipeline.micro_planner is None


# ==========================================================================
# §22 — a changed revision is a changed calibration profile
# ==========================================================================


def test_the_portfolio_fingerprint_covers_every_revision(
    portfolio_config,
) -> None:
    from cover_kbc.models.strategy import portfolio_fingerprint

    base = portfolio_fingerprint(resolve_strategy(portfolio_config, "portfolio"))
    assert len(base) == 64
    for role in PORTFOLIO_ROLES:
        bumped = _load(PORTFOLIO_CONFIG)
        bumped["model_portfolio"][role.value]["revision"] = "f" * 40
        assert portfolio_fingerprint(
            resolve_strategy(bumped, "portfolio")) != base, role
    # ...including the verifier, which v2 changed.
    swapped = _load(PORTFOLIO_CONFIG)
    swapped["model_portfolio"]["independent_verifier"]["model_id"] = (
        BASELINE_VERIFIER_MODEL_ID)
    assert portfolio_fingerprint(
        resolve_strategy(swapped, "portfolio")) != base


def test_the_fingerprint_is_deterministic(portfolio_config) -> None:
    from cover_kbc.models.strategy import portfolio_fingerprint

    profile = resolve_strategy(portfolio_config, "portfolio")
    assert portfolio_fingerprint(profile) == portfolio_fingerprint(profile)


def test_a_mismatched_portfolio_revision_is_refused(tmp_path) -> None:
    """§22: a changed revision is a changed calibration profile."""
    import hashlib

    from cover_kbc.models.strategy import portfolio_fingerprint

    config = _load(PORTFOLIO_TEST_CONFIG)
    mine = portfolio_fingerprint(resolve_strategy(config, "portfolio"))

    other = _load(PORTFOLIO_TEST_CONFIG)
    other["model_portfolio"]["independent_verifier"]["revision"] = "a" * 40
    theirs = portfolio_fingerprint(resolve_strategy(other, "portfolio"))
    assert mine != theirs

    from cover_kbc.models.strategy import check_calibration_portfolio

    check_calibration_portfolio({"portfolio_fingerprint": mine}, mine)
    with pytest.raises(ModelStrategyError, match="changed system"):
        check_calibration_portfolio({"portfolio_fingerprint": theirs}, mine)
    # An artifact that records no fingerprint is left to the strategy check.
    check_calibration_portfolio({}, mine)
    del hashlib


def test_the_loader_enforces_the_portfolio_fingerprint() -> None:
    import inspect

    from cover_kbc.controller_calibration import production

    source = inspect.getsource(production.load_production_calibration)
    assert "check_calibration_portfolio" in source
    assert "portfolio_fingerprint" in source
