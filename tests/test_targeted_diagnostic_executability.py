"""Audit 0080 hotfix: the targeted TRAIN diagnostics must actually be runnable.

Audit 0077 prepared five real-model TRAIN diagnostics and tested them
statically - feature sets, relation filters, TEST inaccessibility. All five
passed, and none of them could execute: `run_cover.py` refused a TRAIN config in
production mode, and the refusal fired *after* `build_runtime` had downloaded
28.7B parameters.

Static config assertions cannot catch that, because the failure lives in the
runner's dispatch rather than in the config. These tests therefore call the
runner's **own** gate resolution rather than restating its rules.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"

DIAGNOSTICS = ("capacity", "city", "stock", "award", "area_parser")
RELATION_OF = {
    "capacity": ("hasCapacity", 100),
    "city": ("personHasCityOfDeath", 100),
    "stock": ("companyTradesAtStockExchange", 100),
    "award": ("awardWonBy", 10),
    "area_parser": ("hasArea", 100),
}


def _runner():
    """Import scripts/run_cover.py without executing a run."""
    path = REPO_ROOT / "scripts" / "run_cover.py"
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("run_cover_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _runner()


def _load(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


def _diag(name: str) -> dict:
    return _load(f"v3_1_diag_{name}.yaml")


def _path(name: str) -> Path:
    return CONFIG_DIR / f"v3_1_diag_{name}.yaml"


# --------------------------------------------------------------------------
# 1. The five diagnostics are executable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", DIAGNOSTICS)
def test_diagnostic_resolves_a_gate_instead_of_exiting(name):
    """The exact failure of the first Colab attempt, as a test."""
    gate, required = runner.resolve_production_gate(_diag(name), "train", _path(name))
    assert gate is not None
    assert required is runner.TRAIN_DIAGNOSTIC_GATE[1]


@pytest.mark.parametrize("name", DIAGNOSTICS)
def test_diagnostic_passes_the_readiness_gate_without_models(name):
    from cover_kbc.controller_calibration.readiness import ReadinessState

    readiness, required = runner.evaluate_production_readiness(
        _diag(name), "train", _path(name))
    assert readiness.state is required, readiness.blockers
    assert readiness.state is ReadinessState.TRAIN_DIAGNOSTIC_READY


@pytest.mark.parametrize("name", DIAGNOSTICS)
def test_diagnostic_relation_filter_resolves_the_declared_row_count(name):
    """Uses the runner's own filter resolution, over the real TRAIN split."""
    from cover_kbc.data.loader import load_dataset

    relation, expected_rows = RELATION_OF[name]
    config = _diag(name)
    wanted = runner._resolve_relation_filter(None, config["experiment"], "train")
    assert wanted == frozenset({relation})
    queries = [q for q in load_dataset("train").queries() if q.relation in wanted]
    assert len(queries) == expected_rows == config["experiment"]["expected_rows"]


@pytest.mark.parametrize("name", DIAGNOSTICS)
def test_diagnostic_still_cannot_read_test(name):
    from cover_kbc.controller_calibration.readiness import (
        ReadinessState, evaluate_test_readiness)

    config = _diag(name)
    assert "test_dataset" not in config
    assert config["experiment"]["split"] == "train"
    report = evaluate_test_readiness(config, base_dir=CONFIG_DIR, split="test")
    assert report.state is not ReadinessState.FULL_TEST_READY


@pytest.mark.parametrize("name", DIAGNOSTICS)
def test_diagnostic_keeps_production_execution_semantics(name):
    """The point of the diagnostic is the production path, not a lighter one."""
    config = _diag(name)
    assert config["pipeline"]["mode"] == "interleaved"
    assert config["pipeline"]["v3_core"]["mode"] == "production"
    assert config["relation_budget_scheduler"]["mode"] == "production"
    assert config["micro_planner"]["mode"] == "production"
    assert runner._wants_production(config) is True
    assert "train_collection" not in config


@pytest.mark.parametrize("name", DIAGNOSTICS)
def test_diagnostic_records_telemetry(name):
    """A diagnostic that records nothing produces nothing to analyse."""
    block = _diag(name).get("diagnostics") or {}
    assert block.get("enabled") is True
    assert block.get("telemetry_file")


# --------------------------------------------------------------------------
# 2. Negative cases - the guard is not weakened
# --------------------------------------------------------------------------


def test_plain_train_production_config_is_still_refused():
    """The rule this hotfix must not break."""
    config = _diag("capacity")
    config.pop("diagnostics")
    with pytest.raises(SystemExit, match="production leaderboard run is defined only"):
        runner.resolve_production_gate(config, "train", _path("capacity"))


def test_train_production_without_telemetry_file_is_refused_by_the_gate():
    from cover_kbc.controller_calibration.readiness import ReadinessState

    config = copy.deepcopy(_diag("capacity"))
    config["diagnostics"] = {"enabled": True}
    readiness, _ = runner.evaluate_production_readiness(
        config, "train", _path("capacity"))
    assert readiness.state is ReadinessState.NOT_READY
    assert any("telemetry_file" in b for b in readiness.blockers)


def test_a_diagnostic_flag_cannot_open_a_test_run():
    """`diagnostics.enabled` must not become a TEST bypass.

    The branch tests ``split == 'train'`` first, so a TEST split takes the
    ordinary leaderboard gate no matter what the diagnostics block says.
    """
    config = copy.deepcopy(_diag("capacity"))
    gate, required = runner.resolve_production_gate(config, "test", _path("capacity"))
    assert gate is runner.PRODUCTION_GATES["test"][0]
    assert required is runner.PRODUCTION_GATES["test"][1]
    assert gate is not runner.TRAIN_DIAGNOSTIC_GATE[0]


def test_a_diagnostic_flag_cannot_open_an_unknown_split():
    config = copy.deepcopy(_diag("capacity"))
    with pytest.raises(SystemExit, match="production leaderboard run is defined only"):
        runner.resolve_production_gate(config, "dev", _path("capacity"))


def test_val_and_test_still_take_their_own_gates():
    from cover_kbc.controller_calibration.readiness import ReadinessState

    for split, expected in (("test", ReadinessState.FULL_TEST_READY),
                            ("val", ReadinessState.FULL_VALIDATION_READY)):
        gate, required = runner.resolve_production_gate(
            _load("cover_kbc_v3_test.yaml"), split, CONFIG_DIR / "cover_kbc_v3_test.yaml")
        assert required is runner.PRODUCTION_GATES[split][1]
        assert required is expected or split != "test"


def test_non_production_config_resolves_no_gate():
    config = copy.deepcopy(_diag("capacity"))
    config["relation_budget_scheduler"]["mode"] = "shadow"
    gate, required = runner.resolve_production_gate(config, "train", _path("capacity"))
    assert gate is None and required is None


def test_relation_filter_still_refuses_a_blind_split():
    config = _diag("capacity")
    with pytest.raises(SystemExit, match="relation filter refused on blind split"):
        runner._resolve_relation_filter(None, config["experiment"], "test")


def test_frozen_test_config_is_unaffected_by_the_hotfix():
    from cover_kbc.controller_calibration.readiness import ReadinessState

    config = _load("cover_kbc_v3_test.yaml")
    readiness, required = runner.evaluate_production_readiness(
        config, "test", CONFIG_DIR / "cover_kbc_v3_test.yaml")
    assert required is ReadinessState.FULL_TEST_READY
    assert readiness.state is ReadinessState.FULL_TEST_READY, readiness.blockers


@pytest.mark.parametrize("name", ("cover_kbc_v3_1_safe_core_test.yaml",
                                  "cover_kbc_v3_1_safe_full_test.yaml"))
def test_safe_submission_configs_are_unaffected(name):
    from cover_kbc.controller_calibration.readiness import ReadinessState

    readiness, required = runner.evaluate_production_readiness(
        _load(name), "test", CONFIG_DIR / name)
    assert required is ReadinessState.FULL_TEST_READY
    assert readiness.state is ReadinessState.FULL_TEST_READY, readiness.blockers


# --------------------------------------------------------------------------
# 3. The experiment itself is unchanged by the hotfix
# --------------------------------------------------------------------------


def test_capacity_prompt_hashes_are_unchanged():
    """The causal intervention must survive an infrastructure fix untouched."""
    from cover_kbc.contracts.registry import CONTRACTS
    from cover_kbc.elicitation.engine import ElicitationEngine, prompt_hash
    from cover_kbc.elicitation.library import views_for
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.v3_1.config import V31Config
    from cover_kbc.v3_1.live_prompts import RelationInstructions
    from cover_kbc.v3_1.prompts import CAPACITY_DEFINITION

    block = V31Config.from_mapping(_diag("capacity")["pipeline"]["selection"]["v3_1"])

    def render(instructions):
        stub = ScriptedRuntime(fallback=lambda request: "UNKNOWN",
                               model_id="offline", family="m", role="enumerator")
        engine = ElicitationEngine(stub, relation_instructions=instructions)
        view = views_for("hasCapacity", CONTRACTS["hasCapacity"].mandatory_views)[0]
        return engine.system_prompt_for(view)

    off = render(RelationInstructions.from_config(None))
    on = render(RelationInstructions.from_config(block))
    assert prompt_hash(off) == "2fb9188dbeda44f3"
    assert prompt_hash(on) == "bcffa96f37770392"
    assert CAPACITY_DEFINITION.sha256 == (
        "27ea6e49bb5547f360effcf133c037fc6f2cd396fddd3f83e2eec12aa8c4ac36")


def test_capacity_remains_the_only_class_b_feature():
    from cover_kbc.v3_1.config import V31Config

    block = V31Config.from_mapping(_diag("capacity")["pipeline"]["selection"]["v3_1"])
    assert block.aggressive.enabled_features == ("capacity_definition_prompt",)
    assert block.calibration_status == "CALIBRATION_REVIEW_REQUIRED"


@pytest.mark.parametrize("name", DIAGNOSTICS)
def test_each_diagnostic_still_enables_exactly_one_class_b_feature(name):
    from cover_kbc.v3_1.config import V31Config

    block = V31Config.from_mapping(_diag(name)["pipeline"]["selection"]["v3_1"])
    assert len(block.aggressive.enabled_features) == 1


def test_model_contract_is_unchanged():
    for name in DIAGNOSTICS:
        profile = _diag(name)["model_profile"]
        assert profile["enumerator"]["model_id"] == (
            "mistralai/Mistral-Small-3.2-24B-Instruct-2506")
        assert profile["enumerator"]["revision"] == (
            "95a6d26c4bfb886c58daf9d3f7332c857cb27b43")
        assert profile["verifier"]["model_id"] == "Qwen/Qwen3.5-4B"
        assert profile["verifier"]["revision"] == (
            "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a")


def test_train_dataset_identity_is_the_canonical_split():
    import hashlib

    blob = (REPO_ROOT / "benchmark" / "data" / "train.jsonl").read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    for name in DIAGNOSTICS:
        block = _diag(name)["train_dataset"]
        assert block["rows"] == 477
        assert block["sha256"] == digest
        assert block["labelled"] is True


# --------------------------------------------------------------------------
# 4. The guard is cheap - it runs before weights
# --------------------------------------------------------------------------


def test_readiness_is_evaluated_before_build_runtime():
    """Ordering is the whole point of the hotfix's runner change."""
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text(encoding="utf-8")
    readiness_at = source.index("evaluate_production_readiness(config, split, args.config)")
    build_at = source.index("runtime = build_runtime(enumerator_cfg)")
    assert readiness_at < build_at, (
        "the production gate must fire before 28.7B parameters download")


def test_gate_resolution_needs_no_model_dependency():
    """A pre-flight cell must be able to call it with no GPU present."""
    import inspect

    source = inspect.getsource(runner.resolve_production_gate)
    for token in ("build_runtime", "torch", "transformers", "cuda"):
        assert token not in source
