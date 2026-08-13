#!/usr/bin/env python3
"""Merge a targeted relation result artifact into a 475-row submission.

The tool is mechanical. It decides which rows may change solely from relation
identity, validates exact key coverage for the targeted relation, and copies all
non-target rows verbatim from the baseline prediction artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

from cover_kbc.data.schema import validate_prediction_row


TOTAL_ROWS = 475
OFFICIAL_FIELDS = ("SubjectEntity", "Relation", "ObjectEntities")


class TargetedMergeError(RuntimeError):
    """A targeted relation merge invariant failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def identity(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row["SubjectEntity"]), str(row["Relation"]))


def official_row(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {
        "SubjectEntity": str(row["SubjectEntity"]),
        "Relation": str(row["Relation"]),
        "ObjectEntities": [str(value) for value in row.get("ObjectEntities") or []],
    }
    validate_prediction_row(out, index=-1)
    return out


def index_unique(
    rows: Sequence[Mapping[str, Any]],
    label: str,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = identity(row)
        if key in out:
            raise TargetedMergeError(f"{label}: duplicate identity {key}")
        out[key] = row
    return out


def head_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        return ""


def merge_targeted_relation_results(
    baseline: Sequence[Mapping[str, Any]],
    targeted_results: Sequence[Mapping[str, Any]],
    *,
    relation: str,
    expected_targeted_rows: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge targeted rows with fail-closed row and relation invariants."""
    if len(baseline) != TOTAL_ROWS:
        raise TargetedMergeError(
            f"baseline has {len(baseline)} row(s); expected {TOTAL_ROWS}"
        )
    if len(targeted_results) != expected_targeted_rows:
        raise TargetedMergeError(
            f"targeted results have {len(targeted_results)} row(s); "
            f"expected {expected_targeted_rows}"
        )

    baseline_by_key = index_unique(baseline, "baseline predictions")
    targeted_by_key = index_unique(targeted_results, "targeted results")
    baseline_target_order = [
        identity(row) for row in baseline if row["Relation"] == relation
    ]
    baseline_target_keys = set(baseline_target_order)

    if len(baseline_target_order) != expected_targeted_rows:
        raise TargetedMergeError(
            f"baseline has {len(baseline_target_order)} {relation} row(s); "
            f"expected {expected_targeted_rows}"
        )
    for key, row in targeted_by_key.items():
        if key[1] != relation or row.get("Relation") != relation:
            raise TargetedMergeError(f"targeted result {key} is not relation {relation}")
    if set(targeted_by_key) != baseline_target_keys:
        missing = baseline_target_keys - set(targeted_by_key)
        extra = set(targeted_by_key) - baseline_target_keys
        raise TargetedMergeError(
            f"targeted result keys mismatch; missing={sorted(missing)[:5]} "
            f"extra={sorted(extra)[:5]}"
        )

    merged: list[dict[str, Any]] = []
    changed_rows: list[dict[str, Any]] = []
    for row in baseline:
        key = identity(row)
        before = official_row(row)
        if key[1] == relation:
            merged_row = official_row(targeted_by_key[key])
            if merged_row != before:
                changed_rows.append({
                    "SubjectEntity": key[0],
                    "Relation": key[1],
                    "before": before["ObjectEntities"],
                    "after": merged_row["ObjectEntities"],
                })
        else:
            merged_row = before
        merged.append(merged_row)

    if len(merged) != TOTAL_ROWS:
        raise TargetedMergeError(f"merged {len(merged)} row(s); expected {TOTAL_ROWS}")
    if [identity(row) for row in merged] != [identity(row) for row in baseline]:
        raise TargetedMergeError("merged output does not preserve baseline row order")
    merged_by_key = index_unique(merged, "merged predictions")
    for key, row in baseline_by_key.items():
        if key[1] == relation:
            continue
        if merged_by_key[key] != official_row(row):
            raise TargetedMergeError(f"non-target row changed during merge: {key}")

    changed_relations = sorted({row["Relation"] for row in changed_rows})
    if any(item != relation for item in changed_relations):
        raise TargetedMergeError(f"unexpected changed relation(s): {changed_relations}")

    provenance = {
        "schema_version": "targeted-relation-merge-v1",
        "source_git_revision": head_sha(),
        "relation": relation,
        "baseline_rows": len(baseline),
        "targeted_result_rows": len(targeted_results),
        "merged_rows": len(merged),
        "baseline_targeted_rows": len(baseline_target_order),
        "changed_rows": len(changed_rows),
        "changed_rows_by_relation": {relation: len(changed_rows)} if changed_rows else {},
        "changed_relation_set": changed_relations,
        "non_target_rows_preserved": TOTAL_ROWS - expected_targeted_rows,
        "changed_rows_detail": changed_rows,
        "answer_provenance": (
            "Non-target rows are copied verbatim from the baseline predictions. "
            "Targeted rows are copied from the targeted relation result artifact. "
            "No value is manually authored or looked up by this merge tool."
        ),
    }
    return merged, provenance


def write_merged(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(rows):
            validate_prediction_row(row, index=index)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-predictions", required=True, type=Path)
    parser.add_argument("--targeted-results", required=True, type=Path)
    parser.add_argument("--relation", required=True)
    parser.add_argument("--expected-targeted-rows", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.output.resolve() == args.baseline_predictions.resolve():
        parser.error("refusing to overwrite the baseline prediction artifact")
    for path in (args.baseline_predictions, args.targeted_results):
        if not path.exists():
            parser.error(f"missing required input: {path}")

    baseline = read_jsonl(args.baseline_predictions)
    targeted_results = read_jsonl(args.targeted_results)
    try:
        merged, provenance = merge_targeted_relation_results(
            baseline,
            targeted_results,
            relation=args.relation,
            expected_targeted_rows=args.expected_targeted_rows,
        )
    except TargetedMergeError as error:
        print(f"TARGETED MERGE REFUSED: {error}", file=sys.stderr)
        return 2

    output = write_merged(args.output, merged)
    provenance.update({
        "baseline_predictions": str(args.baseline_predictions),
        "baseline_sha256": sha256_file(args.baseline_predictions),
        "targeted_results": str(args.targeted_results),
        "targeted_results_sha256": sha256_file(args.targeted_results),
        "output": str(output),
        "output_sha256": sha256_file(output),
    })
    provenance_path = output.with_name(output.stem + "_provenance.json")
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"relation        : {args.relation}")
    print(f"merged rows     : {provenance['merged_rows']}")
    print(f"changed rows    : {provenance['changed_rows']}")
    print(f"changed relation: {provenance['changed_relation_set']}")
    print(f"output          : {output}")
    print(f"output sha256   : {provenance['output_sha256']}")
    print(f"provenance      : {provenance_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
