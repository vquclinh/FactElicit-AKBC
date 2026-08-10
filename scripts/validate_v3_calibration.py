#!/usr/bin/env python3
"""Validate derived V3 calibration artifacts without loading any model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

import yaml

from cover_kbc.controller_calibration.derivation import require_supported_schema
from cover_kbc.controller_calibration.production import load_production_calibration
from cover_kbc.controller_calibration.readiness import (
    evaluate_train_diagnostic_readiness,
)
from cover_kbc.controller_calibration.telemetry import read_telemetry
from cover_kbc.controller_calibration.v3_derivation import (
    PRODUCTION_ARTIFACTS,
    V3CalibrationDerivationError,
    load_merged_v3_corpus,
    read_artifact_hashes,
    render_validation_report,
    validation_report,
    write_validation_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CPU-only V3 calibration artifact validator."
    )
    parser.add_argument("--merged-corpus", required=True, type=Path)
    parser.add_argument("--calibration-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def _load_config(path: Path | None) -> dict:
    if path is None:
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise V3CalibrationDerivationError(f"{path}: expected mapping config")
    return payload


def main() -> int:
    args = parse_args()
    try:
        corpus = load_merged_v3_corpus(args.merged_corpus)
        records = list(read_telemetry(corpus.telemetry_path))
        require_supported_schema(records)
        calibration = load_production_calibration(
            config={
                "relation_budget_scheduler": {
                    "enabled": True,
                    "mode": "production",
                    "calibration_file": str(
                        args.calibration_dir / "m20_relation_budget.json"
                    ),
                    "calibration_sha256": read_artifact_hashes(
                        args.calibration_dir
                    )["m20_relation_budget.json"],
                },
                "micro_planner": {
                    "enabled": True,
                    "mode": "production",
                    "historical_bins": str(
                        args.calibration_dir / "m21_historical_bins.json"
                    ),
                    "historical_bins_sha256": read_artifact_hashes(
                        args.calibration_dir
                    )["m21_historical_bins.json"],
                    "planner_calibration": str(
                        args.calibration_dir / "m21_planner_calibration.json"
                    ),
                    "planner_calibration_sha256": read_artifact_hashes(
                        args.calibration_dir
                    )["m21_planner_calibration.json"],
                },
            },
            base_dir=Path("."),
        )
        report_path = args.calibration_dir / "derivation_report.json"
        prior = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.is_file()
            else {}
        )
        report = validation_report(
            corpus=corpus,
            records=records,
            history=calibration.history,
            planner=calibration.planner,
            m21_diagnostics=dict(prior.get("m21_diagnostics") or {}),
            planner_diagnostics=dict(prior.get("planner_diagnostics") or {}),
            artifact_sha256=read_artifact_hashes(args.calibration_dir),
            m20_status=str(
                calibration.provenance.get(
                    "m20_derivation_method", "UNKNOWN")),
            readiness_status="NOT_CHECKED",
        )
        config = _load_config(args.config)
        if config:
            readiness = evaluate_train_diagnostic_readiness(
                config, base_dir=args.config.parent, split="train")
            report["readiness_status"] = (
                "READY" if readiness.may_run_train_diagnostic else "NOT_READY")
            report["readiness_blockers"] = readiness.blockers
            report["readiness_satisfied"] = readiness.satisfied
        missing = [
            name for name in PRODUCTION_ARTIFACTS
            if not (args.calibration_dir / name).is_file()
        ]
        if missing:
            raise V3CalibrationDerivationError(
                f"missing calibration artifacts: {missing}")
    except Exception as error:                                  # noqa: BLE001
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2

    rendered = render_validation_report(report)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "v3_calibration_validation.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (args.output_dir / "v3_calibration_validation.md").write_text(
            rendered, encoding="utf-8")
        write_validation_csv(
            args.output_dir / "v3_calibration_validation.csv", report)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
