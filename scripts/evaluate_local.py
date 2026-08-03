#!/usr/bin/env python3
"""Score a prediction file with the official evaluator.

Examples:
    python scripts/evaluate_local.py -p outputs/preds.jsonl -s val
    python scripts/evaluate_local.py -p outputs/preds.jsonl -g benchmark/data/val.jsonl --cli
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401

from cover_kbc.evaluation.harness import evaluate_files, evaluate_via_cli, write_report
from cover_kbc.paths import SPLIT_FILES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--predictions", required=True, help="prediction JSONL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-g", "--ground-truth", help="ground-truth JSONL")
    group.add_argument("-s", "--split", choices=sorted(SPLIT_FILES), help="official split name")
    parser.add_argument("-o", "--report", help="write the structured report here")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="also run benchmark/evaluate.py as a subprocess and print its table",
    )
    args = parser.parse_args()

    ground_truth = args.ground_truth or SPLIT_FILES[args.split]

    if args.split == "test":
        print(
            "note: test.jsonl is the blind split and carries no gold objects; "
            "scoring it locally is meaningless.",
            file=sys.stderr,
        )

    report = evaluate_files(args.predictions, ground_truth)
    print(report.to_table())
    print()
    print(f"evaluator sha256 : {report.evaluator_sha256}")
    print(f"gt rows          : {report.num_gt_rows}")
    print(f"pred rows        : {report.num_pred_rows}")
    if report.missing_pred_rows:
        print(f"MISSING rows     : {len(report.missing_pred_rows)} (scored as empty predictions)")
    if report.extra_pred_rows:
        print(f"EXTRA rows       : {len(report.extra_pred_rows)} (ignored by the evaluator)")

    if args.report:
        path = write_report(report, args.report)
        print(f"report written   : {path}")

    if args.cli:
        print("\n--- official CLI (benchmark/evaluate.py) ---")
        print(evaluate_via_cli(args.predictions, ground_truth))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
