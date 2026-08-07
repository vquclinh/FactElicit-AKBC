"""The readiness gate must default to refusal.

The failure it exists to prevent is a VALIDATION run that *succeeds* on an
uncalibrated controller: 478 rows answered by a system nobody intended, with
nothing in the artifacts showing it happened.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cover_kbc.controller_calibration.readiness import ReadinessState, evaluate_readiness


def _write(path, payload) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _m20(source: str = "TRAIN_CALIBRATED") -> dict:
    return {"relations": [
        {"relation": "hasArea", "calibration_source": source, "hard_calls": 8}]}


def _bins() -> dict:
    return {"bins": [{"relation": "hasArea", "support_count": 12}]}


def _planner() -> dict:
    return {"alpha": 1.0, "beta": 1.0, "gamma": 1.0, "delta": 1.0,
            "eta": 1.0, "kappa": 1.0, "tau_continue": 0.1}


def _config(tmp_path, *, m20=None, bins=None, planner=None, enabled=True) -> dict:
    return {
        "relation_budget_scheduler": {"enabled": enabled, "calibration_file": m20},
        "micro_planner": {"enabled": enabled, "historical_bins": bins,
                          "planner_calibration": planner},
    }


def test_empty_profile_is_not_validation_ready(tmp_path) -> None:
    report = evaluate_readiness(_config(tmp_path), base_dir=tmp_path)
    assert not report.may_run_validation


def test_uncalibrated_profile_may_still_collect(tmp_path) -> None:
    """Collecting is precisely how the missing artifacts get made."""
    report = evaluate_readiness(_config(tmp_path), base_dir=tmp_path)
    assert report.state is ReadinessState.CALIBRATION_COLLECTION_READY
    assert report.may_run_collection
    assert not report.may_run_validation


def test_fully_calibrated_profile_is_validation_ready(tmp_path) -> None:
    report = evaluate_readiness(_config(
        tmp_path,
        m20=_write(tmp_path / "m20.json", _m20()),
        bins=_write(tmp_path / "bins.json", _bins()),
        planner=_write(tmp_path / "planner.json", _planner()),
    ), base_dir=tmp_path)
    assert report.state is ReadinessState.FULL_VALIDATION_READY
    assert report.may_run_validation and report.may_run_collection
    assert report.blockers == ()


def test_synthetic_calibration_is_refused_for_validation(tmp_path) -> None:
    report = evaluate_readiness(_config(
        tmp_path,
        m20=_write(tmp_path / "m20.json", _m20("SYNTHETIC_TEST")),
        bins=_write(tmp_path / "bins.json", _bins()),
        planner=_write(tmp_path / "planner.json", _planner()),
    ), base_dir=tmp_path)
    assert not report.may_run_validation
    assert any("not purely TRAIN_CALIBRATED" in b for b in report.blockers)


def test_a_configured_but_absent_path_never_reads_as_calibrated(tmp_path) -> None:
    report = evaluate_readiness(
        _config(tmp_path, m20=str(tmp_path / "missing.json")), base_dir=tmp_path)
    assert not report.may_run_validation
    assert any("does not exist" in b for b in report.blockers)


def test_empty_bin_package_is_refused(tmp_path) -> None:
    report = evaluate_readiness(_config(
        tmp_path,
        m20=_write(tmp_path / "m20.json", _m20()),
        bins=_write(tmp_path / "bins.json", {"bins": []}),
        planner=_write(tmp_path / "planner.json", _planner()),
    ), base_dir=tmp_path)
    assert not report.may_run_validation
    assert any("no bins" in b for b in report.blockers)


def test_missing_coefficients_are_refused(tmp_path) -> None:
    partial = _planner()
    del partial["tau_continue"]
    report = evaluate_readiness(_config(
        tmp_path,
        m20=_write(tmp_path / "m20.json", _m20()),
        bins=_write(tmp_path / "bins.json", _bins()),
        planner=_write(tmp_path / "planner.json", partial),
    ), base_dir=tmp_path)
    assert not report.may_run_validation
    assert any("tau_continue" in b for b in report.blockers)


def test_corrupt_artifact_is_not_ready(tmp_path) -> None:
    bad = tmp_path / "m20.json"
    bad.write_text("{broken", encoding="utf-8")
    report = evaluate_readiness(_config(tmp_path, m20=str(bad)), base_dir=tmp_path)
    assert report.state is ReadinessState.NOT_READY
    assert not report.may_run_validation


def test_report_serialises_its_reasons(tmp_path) -> None:
    payload = evaluate_readiness(_config(tmp_path), base_dir=tmp_path).to_json()
    assert payload["may_run_validation"] is False
    assert payload["blockers"]


# ==========================================================================
# Collection readiness - Audit 0041 F-09
#
# The artifact gate above says nothing about whether a profile can *collect*.
# Audit 0041 found the collection runner starting on a `split: val` profile
# with every upgraded module off, running to completion, and printing PASS over
# an empty telemetry file. These are the checks that now refuse it, and they
# run before a single weight loads.
# ==========================================================================

import yaml

from cover_kbc.controller_calibration.readiness import (
    FORBIDDEN_COLLECTION_MODULES,
    REQUIRED_COLLECTION_MODULES,
    evaluate_collection_readiness,
)

ROOT = Path(__file__).resolve().parents[1]
TRAIN_CONFIG = ROOT / "configs" / "experiments" / "cover_kbc_v2_train_collection.yaml"
TARGET_CONFIG = ROOT / "configs" / "experiments" / "cover_kbc_v2_mistral24_qwen4.yaml"


def _train_config() -> dict:
    return yaml.safe_load(TRAIN_CONFIG.read_text())


def test_the_committed_train_config_may_collect() -> None:
    report = evaluate_collection_readiness(
        _train_config(), base_dir=TRAIN_CONFIG.parent)
    assert report.may_run_collection, report.blockers
    assert report.details["split"] == "train"


def test_the_committed_train_config_is_not_validation_ready() -> None:
    """No TRAIN-derived M20/M21 artifact exists yet, and it must not pretend."""
    report = evaluate_collection_readiness(
        _train_config(), base_dir=TRAIN_CONFIG.parent)
    assert not report.may_run_validation


def test_the_frozen_target_config_may_not_collect() -> None:
    """It declares `split: val` and leaves the upgraded stack off."""
    report = evaluate_collection_readiness(
        yaml.safe_load(TARGET_CONFIG.read_text()), base_dir=TARGET_CONFIG.parent)
    assert not report.may_run_collection
    assert any("may only read 'train'" in b for b in report.blockers)
    assert any("parametric_retrieval" in b for b in report.blockers)


def test_a_val_split_is_refused() -> None:
    config = _train_config()
    config["experiment"]["split"] = "val"
    report = evaluate_collection_readiness(config, base_dir=TRAIN_CONFIG.parent)
    assert not report.may_run_collection


@pytest.mark.parametrize("path,label", REQUIRED_COLLECTION_MODULES)
def test_every_required_upgraded_module_is_enforced(path, label) -> None:
    config = _train_config()
    node = config
    for key in path[:-1]:
        node = node[key]
    node[path[-1]]["enabled"] = False
    report = evaluate_collection_readiness(config, base_dir=TRAIN_CONFIG.parent)
    assert not report.may_run_collection, label
    assert any(label in b for b in report.blockers)


@pytest.mark.parametrize("path,label", FORBIDDEN_COLLECTION_MODULES)
def test_calibrated_modules_may_not_be_on_during_collection(path, label) -> None:
    """M20/M21 are what this run produces; enabling them would be circular."""
    config = _train_config()
    node = config
    for key in path[:-1]:
        node = node[key]
    node.setdefault(path[-1], {})["enabled"] = True
    report = evaluate_collection_readiness(config, base_dir=TRAIN_CONFIG.parent)
    assert not report.may_run_collection, label


def test_an_unusable_model_profile_is_refused() -> None:
    config = _train_config()
    config["model_profile"]["verifier"].pop("model_id")
    report = evaluate_collection_readiness(config, base_dir=TRAIN_CONFIG.parent)
    assert not report.may_run_collection
    assert any("verifier declares no model_id" in b for b in report.blockers)


def test_the_committed_train_config_keeps_the_frozen_model_profile() -> None:
    """Collection must measure the system validation will run, not a cheaper one."""
    train = _train_config()["model_profile"]
    target = yaml.safe_load(TARGET_CONFIG.read_text())["model_profile"]
    assert train == target
