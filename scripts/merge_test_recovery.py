#!/usr/bin/env python3
"""Replace exactly the failed rows of a TEST run with re-run predictions.

Mechanical throughout. The tool decides *which* rows to replace, from the failed
run's own ``errors.json``; it never decides *what* to put in them. Every emitted
value is copied verbatim from a COVER-KBC prediction file.

The failed run is immutable evidence and is opened read-only. The merged file is
written to a new path.

Invariants, all enforced rather than assumed:

* the base has exactly the canonical row count;
* the failed identity set comes from ``errors.json``;
* the recovered identity set equals it exactly - no missing, no extra, no
  duplicates;
* every replaced row was empty in the base (a hole, not an answer);
* no non-failed row changes, compared field by field;
* output is in canonical split order with the official three-field schema.

Usage::

    python scripts/merge_test_recovery.py \
      --base-predictions outputs/v3_test_16f60fb1_20260810T160048Z/run/predictions.jsonl \
      --base-errors      outputs/v3_test_16f60fb1_20260810T160048Z/run/errors.json \
      --recovered-predictions outputs/<recovery run>/predictions.jsonl \
      --canonical-test   benchmark/data/test.jsonl \
      --output           outputs/v3_test_recovered/predictions.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

SCHEMA_VERSION = "v3-test-recovery-merge-v1"
OFFICIAL_FIELDS = ("SubjectEntity", "Relation", "ObjectEntities")


class MergeError(RuntimeError):
    """A merge invariant failed. Nothing is written."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def identity(row: dict[str, Any]) -> tuple[str, str]:
    return (row["SubjectEntity"], row["Relation"])


def index_unique(rows: list[dict[str, Any]], label: str) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = identity(row)
        if key in out:
            raise MergeError(f"{label}: duplicate identity {key}")
        out[key] = row
    return out


def head_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):  # pragma: no cover
        return ""


def merge(
    base: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    recovered: list[dict[str, Any]],
    canonical: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The merge, as a pure function so a test can drive it without files."""
    canonical_order = [identity(row) for row in canonical]
    canonical_set = set(canonical_order)
    if len(canonical_order) != len(canonical_set):
        raise MergeError("canonical split contains duplicate identities")

    base_by_identity = index_unique(base, "base predictions")
    if len(base) != len(canonical_order):
        raise MergeError(
            f"base has {len(base)} row(s); the canonical split has "
            f"{len(canonical_order)}")
    if set(base_by_identity) != canonical_set:
        raise MergeError("base identities do not match the canonical split")

    failed = set()
    for entry in errors:
        key = (entry["SubjectEntity"], entry["Relation"])
        if key in failed:
            raise MergeError(f"errors.json lists {key} twice")
        if key not in canonical_set:
            raise MergeError(f"errors.json names {key}, absent from the canonical split")
        failed.add(key)
    if not failed:
        raise MergeError("errors.json lists no failed rows; there is nothing to merge")

    recovered_by_identity = index_unique(recovered, "recovered predictions")
    recovered_set = set(recovered_by_identity)
    missing = failed - recovered_set
    extra = recovered_set - failed
    if missing:
        raise MergeError(
            f"{len(missing)} failed identity(ies) were not recovered: {sorted(missing)[:5]}")
    if extra:
        raise MergeError(
            f"{len(extra)} recovered identity(ies) did not fail: {sorted(extra)[:5]}")

    replaced: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    for key in canonical_order:
        if key in failed:
            source = recovered_by_identity[key]
            original = base_by_identity[key]
            if original.get("ObjectEntities"):
                raise MergeError(
                    f"{key} failed but its base row is non-empty; refusing to "
                    "overwrite an answer")
            row = {
                "SubjectEntity": key[0],
                "Relation": key[1],
                "ObjectEntities": list(source.get("ObjectEntities") or []),
            }
            replaced.append({
                "SubjectEntity": key[0],
                "Relation": key[1],
                "recovered_object_count": len(row["ObjectEntities"]),
                "still_empty": not row["ObjectEntities"],
            })
        else:
            original = base_by_identity[key]
            row = {field: original[field] for field in OFFICIAL_FIELDS}
        merged.append(row)

    # Field-by-field proof that nothing outside the failed set moved.
    for key in canonical_order:
        if key in failed:
            continue
        before = base_by_identity[key]
        after = next(r for r in merged if identity(r) == key)
        if [before[f] for f in OFFICIAL_FIELDS] != [after[f] for f in OFFICIAL_FIELDS]:
            raise MergeError(f"non-failed row {key} changed during merge")

    if len(merged) != len(canonical_order):
        raise MergeError(f"merged {len(merged)} row(s) for {len(canonical_order)}")
    if [identity(r) for r in merged] != canonical_order:
        raise MergeError("merged output is not in canonical split order")

    provenance = {
        "schema_version": SCHEMA_VERSION,
        "canonical_rows": len(canonical_order),
        "base_rows": len(base),
        "merged_rows": len(merged),
        "failed_rows": len(failed),
        "recovered_rows": len(recovered),
        "rows_replaced": len(replaced),
        "rows_preserved": len(merged) - len(replaced),
        "recovered_now_non_empty": sum(1 for r in replaced if not r["still_empty"]),
        "recovered_still_empty": sum(1 for r in replaced if r["still_empty"]),
        "replaced": replaced,
        "answer_provenance": (
            "Every ObjectEntities value in this file was produced by COVER-KBC "
            "inference. Non-failed rows are copied verbatim from the base run; "
            "failed rows are copied verbatim from the recovery run. No value was "
            "authored, selected, reordered or edited by any other process."
        ),
    }
    return merged, provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-predictions", required=True, type=Path)
    parser.add_argument("--base-errors", required=True, type=Path)
    parser.add_argument("--recovered-predictions", required=True, type=Path)
    parser.add_argument("--canonical-test", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    for path in (args.base_predictions, args.base_errors,
                 args.recovered_predictions, args.canonical_test):
        if not path.exists():
            parser.error(f"missing required input: {path}")
    if args.output.resolve() == args.base_predictions.resolve():
        parser.error("refusing to overwrite the failed run: it is immutable evidence")

    base = read_jsonl(args.base_predictions)
    errors = json.loads(args.base_errors.read_text())
    recovered = read_jsonl(args.recovered_predictions)
    canonical = read_jsonl(args.canonical_test)

    try:
        merged, provenance = merge(base, errors, recovered, canonical)
    except MergeError as error:
        print(f"MERGE REFUSED: {error}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in merged:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    provenance.update({
        "output": str(args.output),
        "output_sha256": sha256_file(args.output),
        "base_predictions": str(args.base_predictions),
        "base_predictions_sha256": sha256_file(args.base_predictions),
        "base_errors_sha256": sha256_file(args.base_errors),
        "recovered_predictions": str(args.recovered_predictions),
        "recovered_predictions_sha256": sha256_file(args.recovered_predictions),
        "canonical_test": str(args.canonical_test),
        "canonical_test_sha256": sha256_file(args.canonical_test),
        "merge_source_sha": head_sha(),
    })
    provenance_path = args.output.with_name(args.output.stem + "_provenance.json")
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"merged rows        : {provenance['merged_rows']}")
    print(f"rows replaced      : {provenance['rows_replaced']}")
    print(f"rows preserved     : {provenance['rows_preserved']}")
    print(f"recovered non-empty: {provenance['recovered_now_non_empty']}")
    print(f"recovered still empty: {provenance['recovered_still_empty']}")
    print(f"output             : {args.output}")
    print(f"output sha256      : {provenance['output_sha256']}")
    print(f"provenance         : {provenance_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
