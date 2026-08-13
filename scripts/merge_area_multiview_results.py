#!/usr/bin/env python3
"""Merge Profile E3 Area Multi-View results into a 475-row submission."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from merge_targeted_relation_results import (
    TargetedMergeError,
    merge_targeted_relation_results,
    read_jsonl,
    sha256_file,
    write_merged,
)


AREA = "hasArea"
AREA_ROWS = 100


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-predictions", required=True, type=Path)
    parser.add_argument("--area-results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.output.resolve() == args.baseline_predictions.resolve():
        parser.error("refusing to overwrite the baseline prediction artifact")
    for path in (args.baseline_predictions, args.area_results):
        if not path.exists():
            parser.error(f"missing required input: {path}")

    baseline = read_jsonl(args.baseline_predictions)
    area_results = read_jsonl(args.area_results)
    try:
        merged, provenance = merge_targeted_relation_results(
            baseline,
            area_results,
            relation=AREA,
            expected_targeted_rows=AREA_ROWS,
        )
    except TargetedMergeError as error:
        print(f"AREA MULTIVIEW MERGE REFUSED: {error}", file=sys.stderr)
        return 2

    output = write_merged(args.output, merged)
    provenance.update({
        "schema_version": "area-multiview-targeted-merge-v1",
        "baseline_predictions": str(args.baseline_predictions),
        "baseline_sha256": sha256_file(args.baseline_predictions),
        "area_results": str(args.area_results),
        "area_results_sha256": sha256_file(args.area_results),
        "output": str(output),
        "output_sha256": sha256_file(output),
    })
    provenance_path = output.with_name(output.stem + "_provenance.json")
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("relation        : hasArea")
    print(f"merged rows     : {provenance['merged_rows']}")
    print(f"changed rows    : {provenance['changed_rows']}")
    print(f"changed relation: {provenance['changed_relation_set']}")
    print(f"output          : {output}")
    print(f"output sha256   : {provenance['output_sha256']}")
    print(f"provenance      : {provenance_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
