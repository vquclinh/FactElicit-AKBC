#!/usr/bin/env python3
"""Derive V3 production calibration artifacts from the merged TRAIN corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

from cover_kbc.controller_calibration.derivation import DerivationError
from cover_kbc.controller_calibration.gold_join import GoldJoinError
from cover_kbc.controller_calibration.v3_derivation import (
    EXPECTED_MERGED_CORPUS_SHA256,
    V3CalibrationDerivationError,
    derive_v3_calibration,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "CPU-only V3 calibration derivation. Reads the merged TRAIN action "
            "corpus and writes V3 artifacts under a separate calibration namespace."
        )
    )
    parser.add_argument("--merged-corpus", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--train-gold", type=Path,
        default=Path("benchmark/data/train.jsonl"),
    )
    parser.add_argument(
        "--inherited-m20", type=Path,
        default=Path("configs/calibration/m20_relation_budget.json"),
    )
    parser.add_argument(
        "--collection-config", type=Path,
        default=Path("configs/experiments/cover_kbc_v3_train_collection.yaml"),
    )
    parser.add_argument(
        "--expected-merged-corpus-sha256",
        default=EXPECTED_MERGED_CORPUS_SHA256,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = derive_v3_calibration(
            merged_corpus=args.merged_corpus,
            output_dir=args.output_dir,
            train_gold=args.train_gold,
            inherited_m20=args.inherited_m20,
            collection_config=args.collection_config,
            expected_merged_corpus_sha256=args.expected_merged_corpus_sha256,
        )
    except (
        DerivationError,
        GoldJoinError,
        V3CalibrationDerivationError,
    ) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
