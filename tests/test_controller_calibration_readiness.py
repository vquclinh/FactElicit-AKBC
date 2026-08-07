"""The readiness gate must default to refusal.

The failure it exists to prevent is a VALIDATION run that *succeeds* on an
uncalibrated controller: 478 rows answered by a system nobody intended, with
nothing in the artifacts showing it happened.
"""

from __future__ import annotations

import json

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
