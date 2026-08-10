#!/usr/bin/env python3
"""Zero-model readiness precheck for calibrated V3 TEST production."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
)
from cover_kbc.models.registry import model_blocks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head(repo_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"cannot resolve git HEAD: {error}") from None


def _resolve(raw: str, *, base: Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _artifact_hashes(config: Mapping[str, Any], *, base: Path) -> dict[str, str]:
    budget = dict(config.get("relation_budget_scheduler") or {})
    planner = dict(config.get("micro_planner") or {})
    paths = {
        "m20_relation_budget.json": _resolve(
            str(budget.get("calibration_file", "")), base=base),
        "m21_historical_bins.json": _resolve(
            str(planner.get("historical_bins", "")), base=base),
        "m21_planner_calibration.json": _resolve(
            str(planner.get("planner_calibration", "")), base=base),
    }
    return {name: _sha256(path) for name, path in paths.items()}


def build_report(config_path: Path) -> dict[str, Any]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise ValueError(f"{config_path}: expected mapping config")
    provenance = dict(config.get("calibration_provenance") or {})
    readiness = evaluate_test_readiness(
        config,
        base_dir=config_path.parent,
        split="test",
        expected_collection_repo_sha=provenance.get("collection_repo_sha"),
        expected_derivation_repo_sha=provenance.get("derivation_repo_sha"),
    )
    enumerator, verifier = model_blocks(config)
    assertion = dict(config.get("budget_assertion") or {})
    v3 = dict(((config.get("pipeline") or {}).get("v3_core")) or {})
    details = dict(readiness.details)
    calibration = dict(details.get("calibration") or {})
    calibration_provenance = dict(calibration.get("provenance") or {})
    budget = dict(config.get("relation_budget_scheduler") or {})
    planner = dict(config.get("micro_planner") or {})
    report = {
        "source_commit": _git_head(Path.cwd()),
        "config_path": str(config_path),
        "ready": readiness.state is ReadinessState.FULL_TEST_READY,
        "state": readiness.state.value,
        "blockers": list(readiness.blockers),
        "test_rows": details.get("test_rows"),
        "test_sha256": details.get("test_sha256", ""),
        "test_ordered_identity_sha256": details.get("test_identity_sha256", ""),
        "test_rows_with_objects": details.get("test_rows_with_objects"),
        "calibration_corpus_sha256": calibration_provenance.get(
            "merged_corpus_sha256", ""),
        "calibration_artifact_sha256": _artifact_hashes(
            config, base=config_path.parent),
        "enumerator_model_id": enumerator.get("model_id", ""),
        "enumerator_revision": enumerator.get("revision", ""),
        "verifier_model_id": verifier.get("model_id", ""),
        "verifier_revision": verifier.get("revision", ""),
        "parameter_total": int(assertion.get("total_published_parameters", 0) or 0),
        "parameter_limit": int(assertion.get("limit", 0) or 0),
        "v3_core_mode": v3.get("mode", ""),
        "m20_mode": budget.get("mode", ""),
        "m21_mode": planner.get("mode", ""),
        "collection_disabled": not bool(config.get("train_collection")),
        "missing_calibration_regions": len(
            details.get("v3_unresolved_calibration_regions") or ()),
        "model_calls": 0,
    }
    return report


def render(report: Mapping[str, Any]) -> str:
    status = "READY" if report.get("ready") else "NOT_READY"
    lines = [
        f"V3 TEST PRODUCTION READINESS: {status}",
        f"source commit: {report['source_commit']}",
        f"config: {report['config_path']}",
        f"TEST rows: {report['test_rows']}",
        f"TEST SHA256: {report['test_sha256']}",
        f"TEST ordered identity: {report['test_ordered_identity_sha256']}",
        f"TEST rows with ObjectEntities: {report['test_rows_with_objects']}",
        f"calibration corpus SHA256: {report['calibration_corpus_sha256']}",
        "V3 calibration artifact SHA256:",
    ]
    for name, digest in sorted(report["calibration_artifact_sha256"].items()):
        lines.append(f"  {name}: {digest}")
    lines.extend((
        f"enumerator: {report['enumerator_model_id']} @ "
        f"{report['enumerator_revision']}",
        f"verifier: {report['verifier_model_id']} @ "
        f"{report['verifier_revision']}",
        f"parameter total: {report['parameter_total']} / "
        f"{report['parameter_limit']}",
        f"production mode: v3_core={report['v3_core_mode']} "
        f"m20={report['m20_mode']} m21={report['m21_mode']}",
        f"collection disabled: {report['collection_disabled']}",
        f"missing calibration regions: {report['missing_calibration_regions']}",
        f"model calls: {report['model_calls']}",
    ))
    if report.get("blockers"):
        lines.append("blockers:")
        for blocker in report["blockers"]:
            lines.append(f"  - {blocker}")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/cover_kbc_v3_test.yaml"),
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(args.config)
    except Exception as error:                                  # noqa: BLE001
        print("V3 TEST PRODUCTION READINESS: NOT_READY", file=sys.stderr)
        print(f"precheck error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report), end="")
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
