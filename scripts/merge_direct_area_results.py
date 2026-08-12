#!/usr/bin/env python3
"""Merge Direct Area results into an exact baseline submission.

The tool is mechanical. It decides which rows may change solely from relation
identity (``hasArea``). It never looks up facts and never edits non-Area rows.
Area rows use DIRECT_ALL semantics: a valid Direct Area value replaces the
baseline value; UNKNOWN or invalid output becomes empty.
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
from cover_kbc.leaderboard_repair.area import (
    DIRECT_AREA_MODE,
    UNKNOWN,
    VALID_AREA,
    direct_area_values,
    parse_direct_area_output,
)


AREA = "hasArea"
TOTAL_ROWS = 475
AREA_ROWS = 100
OFFICIAL_FIELDS = ("SubjectEntity", "Relation", "ObjectEntities")


class AreaMergeError(RuntimeError):
    """A targeted Area merge invariant failed."""


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


def index_unique(
    rows: Sequence[Mapping[str, Any]],
    label: str,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = identity(row)
        if key in out:
            raise AreaMergeError(f"{label}: duplicate identity {key}")
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


def parsed_direct_area(row: Mapping[str, Any]):
    status = str(row.get("direct_parse_status", ""))
    if status == VALID_AREA:
        value = str(row.get("direct_value", "")).strip()
        return parse_direct_area_output(f"AREA: {value}")
    if status == UNKNOWN:
        return parse_direct_area_output("UNKNOWN")
    return parse_direct_area_output("INVALID")


def _official_row(row: Mapping[str, Any], values: Sequence[str]) -> dict[str, Any]:
    out = {
        "SubjectEntity": str(row["SubjectEntity"]),
        "Relation": str(row["Relation"]),
        "ObjectEntities": [str(value) for value in values if str(value).strip()],
    }
    validate_prediction_row(out, index=-1)
    return out


def merge_area_results(
    baseline: Sequence[Mapping[str, Any]],
    area_results: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge Direct Area results with fail-closed row and relation invariants."""
    if len(baseline) != TOTAL_ROWS:
        raise AreaMergeError(f"baseline has {len(baseline)} row(s); expected {TOTAL_ROWS}")
    if len(area_results) != AREA_ROWS:
        raise AreaMergeError(
            f"area results have {len(area_results)} row(s); expected {AREA_ROWS}"
        )

    baseline_by_key = index_unique(baseline, "baseline predictions")
    area_by_key = index_unique(area_results, "area results")
    baseline_area_order = [identity(row) for row in baseline if row["Relation"] == AREA]
    baseline_area_keys = set(baseline_area_order)

    if len(baseline_area_order) != AREA_ROWS:
        raise AreaMergeError(
            f"baseline has {len(baseline_area_order)} hasArea row(s); expected {AREA_ROWS}"
        )
    for key, row in area_by_key.items():
        if key[1] != AREA or row.get("Relation") != AREA:
            raise AreaMergeError(f"area result {key} is not relation hasArea")
    if set(area_by_key) != baseline_area_keys:
        missing = baseline_area_keys - set(area_by_key)
        extra = set(area_by_key) - baseline_area_keys
        raise AreaMergeError(
            f"area result keys mismatch; missing={sorted(missing)[:5]} "
            f"extra={sorted(extra)[:5]}"
        )

    merged: list[dict[str, Any]] = []
    changed_rows: list[dict[str, Any]] = []
    for row in baseline:
        key = identity(row)
        before_values = list(row.get("ObjectEntities") or [])
        if key[1] != AREA:
            merged_row = {field: row[field] for field in OFFICIAL_FIELDS}
        else:
            direct = parsed_direct_area(area_by_key[key])
            values = direct_area_values(direct)
            merged_row = _official_row(row, values)
            if values != before_values:
                changed_rows.append({
                    "SubjectEntity": key[0],
                    "Relation": key[1],
                    "before": before_values,
                    "after": list(values),
                    "direct_parse_status": direct.status,
                    "direct_value": direct.value,
                })
        merged.append(merged_row)

    if len(merged) != TOTAL_ROWS:
        raise AreaMergeError(f"merged {len(merged)} row(s); expected {TOTAL_ROWS}")
    if [identity(row) for row in merged] != [identity(row) for row in baseline]:
        raise AreaMergeError("merged output does not preserve baseline row order")
    merged_by_key = index_unique(merged, "merged predictions")
    for key, row in baseline_by_key.items():
        if key[1] == AREA:
            continue
        if merged_by_key[key] != {field: row[field] for field in OFFICIAL_FIELDS}:
            raise AreaMergeError(f"non-Area row changed during merge: {key}")

    changed_relations = sorted({row["Relation"] for row in changed_rows})
    if any(relation != AREA for relation in changed_relations):
        raise AreaMergeError(f"unexpected changed relation(s): {changed_relations}")

    provenance = {
        "schema_version": "direct-area-targeted-merge-v1",
        "source_git_revision": head_sha(),
        "direct_area_mode": DIRECT_AREA_MODE,
        "baseline_rows": len(baseline),
        "area_result_rows": len(area_results),
        "merged_rows": len(merged),
        "baseline_hasArea_rows": len(baseline_area_order),
        "changed_rows": len(changed_rows),
        "changed_rows_by_relation": {AREA: len(changed_rows)} if changed_rows else {},
        "changed_relation_set": changed_relations,
        "non_area_rows_preserved": TOTAL_ROWS - AREA_ROWS,
        "city_rows_preserved": sum(
            1 for row in baseline if row["Relation"] == "personHasCityOfDeath"
        ),
        "changed_rows_detail": changed_rows,
        "answer_provenance": (
            "Non-Area rows are copied verbatim from the baseline predictions. "
            "Area rows are produced by applying Direct Area DIRECT_ALL semantics "
            "to the Mistral direct Area result. No value is manually authored or "
            "looked up by this merge tool."
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
        merged, provenance = merge_area_results(baseline, area_results)
    except AreaMergeError as error:
        print(f"DIRECT AREA MERGE REFUSED: {error}", file=sys.stderr)
        return 2

    output = write_merged(args.output, merged)
    provenance.update({
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

    print(f"direct_area_mode: {DIRECT_AREA_MODE}")
    print(f"merged rows     : {provenance['merged_rows']}")
    print(f"changed rows    : {provenance['changed_rows']}")
    print(f"changed relation: {provenance['changed_relation_set']}")
    print(f"output          : {output}")
    print(f"output sha256   : {provenance['output_sha256']}")
    print(f"provenance      : {provenance_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
