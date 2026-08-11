#!/usr/bin/env python3
"""Offline TRAIN ablation scorer for V3.2 leaderboard repair artifacts.

This script does not run models.  It consumes prediction JSONL files produced by
actual TRAIN runs and scores them against labelled TRAIN gold.  TEST labels are
never an input.

Example:
    python scripts/analyze_v3_2_repair_ablations.py \
      --gold benchmark/data/train.jsonl \
      --baseline outputs/.../predictions.jsonl \
      --variant stock_prompt=outputs/.../predictions.jsonl \
      --output-dir outputs/v3_2_repair_ablations
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

from cover_kbc.evaluation.harness import OVERALL_KEY, evaluate_files, evaluate_predictions


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _by_key(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(str(row["SubjectEntity"]), str(row["Relation"])): row for row in rows}


def _row_f1(per_pair: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], float]:
    out = {}
    for row in per_pair:
        key = (str(row["SubjectEntity"]), str(row["Relation"]))
        out[key] = float(row.get("f1", row.get("F1", 0.0)) or 0.0)
    return out


def _variant_row(
    name: str, path: Path, *, gold: Path, baseline_report, baseline_rows,
) -> dict[str, Any]:
    report = evaluate_files(path, gold)
    rows = _read_jsonl(path)
    before = _by_key(baseline_rows)
    after = _by_key(rows)
    base_f1 = _row_f1(baseline_report.per_pair)
    variant_f1 = _row_f1(report.per_pair)
    improved = harmed = changed = 0
    for key, after_row in after.items():
        if list((before.get(key) or {}).get("ObjectEntities") or []) != list(
            after_row.get("ObjectEntities") or []
        ):
            changed += 1
        delta = variant_f1.get(key, 0.0) - base_f1.get(key, 0.0)
        if delta > 1e-12:
            improved += 1
        elif delta < -1e-12:
            harmed += 1
    total_tp = sum(int(row.get("tp", 0) or 0) for row in report.per_pair)
    total_fp = sum(
        int(row.get("total_pred", 0) or 0) - int(row.get("tp", 0) or 0)
        for row in report.per_pair
    )
    total_fn = sum(
        int(row.get("total_gt", 0) or 0) - int(row.get("tp", 0) or 0)
        for row in report.per_pair
    )
    cardinalities = Counter(len(row.get("ObjectEntities") or []) for row in rows)
    empties = cardinalities.get(0, 0)
    overall = report.macro[OVERALL_KEY]
    base = baseline_report.macro[OVERALL_KEY]
    return {
        "variant": name,
        "predictions": str(path),
        "macro_p": overall["macro-p"],
        "macro_r": overall["macro-r"],
        "macro_f1": overall["macro-f1"],
        "delta_macro_f1": overall["macro-f1"] - base["macro-f1"],
        "micro_f1": report.micro[OVERALL_KEY]["micro-f1"],
        "rows_changed": changed,
        "rows_improved": improved,
        "rows_harmed": harmed,
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "empty_rows": empties,
        "cardinality_distribution": dict(sorted(cardinalities.items())),
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def parse_variant(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--variant must be NAME=predictions.jsonl")
    name, path = value.split("=", 1)
    if not name:
        raise argparse.ArgumentTypeError("variant name is empty")
    return name, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path, help="TRAIN gold JSONL")
    parser.add_argument("--baseline", required=True, type=Path,
                        help="baseline predictions JSONL")
    parser.add_argument("--variant", action="append", type=parse_variant, default=[],
                        help=("NAME=predictions.jsonl; repeat for isolated/cumulative "
                              "ablations"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    gold_rows = _read_jsonl(args.gold)
    baseline_rows = _read_jsonl(args.baseline)
    baseline_report = evaluate_predictions(baseline_rows, gold_rows)
    rows = [_variant_row(
        "baseline", args.baseline, gold=args.gold,
        baseline_report=baseline_report, baseline_rows=baseline_rows)]
    for name, path in args.variant:
        rows.append(_variant_row(
            name, path, gold=args.gold,
            baseline_report=baseline_report, baseline_rows=baseline_rows))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": "v3-2-repair-ablation-summary-v1",
        "gold": str(args.gold),
        "baseline": str(args.baseline),
        "variants": rows,
        "note": (
            "This file is TRAIN-only. TEST predictions may be analysed for output "
            "shape elsewhere, but TEST gold is not an input to this script."
        ),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        args.output_dir / "ablation_table.csv",
        rows,
        (
            "variant", "macro_p", "macro_r", "macro_f1", "delta_macro_f1",
            "micro_f1", "rows_changed", "rows_improved", "rows_harmed",
            "tp", "fp", "fn", "empty_rows", "cardinality_distribution",
            "predictions",
        ),
    )
    print(args.output_dir / "ablation_table.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
