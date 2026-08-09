#!/usr/bin/env python3
"""Offline V3A TRAIN failure attribution.

Consumes gold-free inference telemetry from ``scripts/run_cover.py`` and joins
it to TRAIN labels after the run. This script makes no model/runtime calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

from cover_kbc.controller_calibration.gold_join import load_gold
from cover_kbc.diagnostics import (
    GoldLeakageError,
    TrainGoldAttribution,
    build_report,
    read_inference_telemetry,
    write_report,
)
from cover_kbc.integration_mode import CALIBRATION_SPLIT
from cover_kbc.paths import SPLIT_FILES

EXPECTED_TRAIN_ROWS = 477
EXPECTED_TRAIN_SHA256 = (
    "ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _line_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip())


def _verify_train_file(path: Path, *, expected_rows: int,
                       expected_sha256: str) -> None:
    if not path.is_file():
        raise SystemExit(f"TRAIN gold file does not exist: {path}")
    rows = _line_count(path)
    digest = _sha256(path)
    if rows != expected_rows:
        raise SystemExit(
            f"TRAIN gold file has {rows} rows, expected {expected_rows}")
    if digest != expected_sha256:
        raise SystemExit(
            f"TRAIN gold file sha256 is {digest}, expected {expected_sha256}")


def _load_records(path: Path):
    records = list(read_inference_telemetry(path))
    if not records:
        raise SystemExit(f"telemetry file is empty: {path}")
    bad = sorted({record.split for record in records
                  if record.split != CALIBRATION_SPLIT})
    if bad:
        raise GoldLeakageError(
            f"telemetry contains split(s) {bad}; only "
            f"{CALIBRATION_SPLIT!r} may be attributed")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemetry", required=True, type=Path,
                        help="inference telemetry JSONL from a TRAIN run")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="directory for failure_attribution.{json,md,csv}")
    parser.add_argument("--split", default=CALIBRATION_SPLIT,
                        help="must be 'train'")
    parser.add_argument("--gold-path", type=Path,
                        default=SPLIT_FILES[CALIBRATION_SPLIT],
                        help="TRAIN gold JSONL")
    parser.add_argument("--expected-train-rows", type=int,
                        default=EXPECTED_TRAIN_ROWS)
    parser.add_argument("--expected-train-sha256",
                        default=EXPECTED_TRAIN_SHA256)
    args = parser.parse_args()

    if args.split != CALIBRATION_SPLIT:
        raise GoldLeakageError(
            f"failure attribution accepts {CALIBRATION_SPLIT!r} only; "
            f"refusing split {args.split!r}")
    _verify_train_file(
        args.gold_path, expected_rows=args.expected_train_rows,
        expected_sha256=args.expected_train_sha256)

    records = _load_records(args.telemetry)
    gold = load_gold(args.gold_path, expected_rows=args.expected_train_rows)
    attributions = TrainGoldAttribution(gold).attribute_all(records)
    report = build_report(
        attributions, split=CALIBRATION_SPLIT,
        telemetry_path=str(args.telemetry), gold_path=str(args.gold_path),
        evaluator_sha256=gold.evaluator_sha256, tolerance=gold.tolerance,
    )
    paths = write_report(report, args.output_dir)
    print(json.dumps({name: str(path) for name, path in paths.items()},
                     indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GoldLeakageError as error:
        print(f"gold attribution refused: {error}", file=sys.stderr)
        raise SystemExit(2) from error
