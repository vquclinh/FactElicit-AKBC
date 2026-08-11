#!/usr/bin/env python3
"""Derive the exact set of queries a failed run must re-run.

The identities come from the failed run's own ``errors.json``. Nothing is
hard-coded: no subject list lives in this file, in the source tree, or in the
manifest's construction logic. The manifest carries identities and provenance
hashes and **never carries an answer**.

Usage::

    python scripts/build_test_recovery_manifest.py \
      --run-dir outputs/v3_test_16f60fb1_20260810T160048Z/run \
      --canonical benchmark/data/test.jsonl \
      --output outputs/v3_test_pending_action_forensics/recovery_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = "v3-test-recovery-manifest-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def head_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):  # pragma: no cover
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--canonical", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    errors_path = args.run_dir / "errors.json"
    predictions_path = args.run_dir / "predictions.jsonl"
    for path in (errors_path, predictions_path, args.canonical):
        if not path.exists():
            parser.error(f"missing required input: {path}")

    errors = json.loads(errors_path.read_text())
    predictions = read_jsonl(predictions_path)
    canonical = read_jsonl(args.canonical)

    canonical_index = {
        (row["SubjectEntity"], row["Relation"]): position
        for position, row in enumerate(canonical)
    }
    predictions_by_identity = {
        (row["SubjectEntity"], row["Relation"]): row for row in predictions
    }

    identities: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in errors:
        key = (entry["SubjectEntity"], entry["Relation"])
        if key in seen:
            parser.error(f"errors.json lists {key} twice; refusing an ambiguous manifest")
        seen.add(key)
        if key not in canonical_index:
            parser.error(
                f"{key} is not in {args.canonical}; the failed run and the canonical "
                "split disagree about which queries exist")
        original = predictions_by_identity.get(key, {})
        identities.append({
            "SubjectEntity": key[0],
            "Relation": key[1],
            "canonical_index": canonical_index[key],
            "error": entry.get("error", ""),
            "error_kind": str(entry.get("error", "")).split(":", 1)[0],
            # Recorded so the merge can prove it replaced a hole, not an answer.
            "original_prediction_was_empty": not original.get("ObjectEntities"),
        })
    identities.sort(key=lambda row: row["canonical_index"])

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "purpose": (
            "Re-run exactly these identities from the start under fixed source. "
            "Carries identities and provenance only: no predicted values of any "
            "kind appear anywhere in this document."
        ),
        "built_from": {
            "run_dir": str(args.run_dir),
            "errors_json": str(errors_path),
            "errors_sha256": sha256_file(errors_path),
            "base_predictions": str(predictions_path),
            "base_predictions_sha256": sha256_file(predictions_path),
            "base_prediction_rows": len(predictions),
            "canonical": str(args.canonical),
            "canonical_sha256": sha256_file(args.canonical),
            "canonical_rows": len(canonical),
        },
        "repair_source_sha": head_sha(),
        "recovery_count": len(identities),
        "relations": dict(Counter(row["Relation"] for row in identities)),
        "error_kinds": dict(Counter(row["error_kind"] for row in identities)),
        "all_original_predictions_empty": all(
            row["original_prediction_was_empty"] for row in identities),
        "identities": identities,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"recovery identities : {len(identities)}")
    print(f"relations           : {manifest['relations']}")
    print(f"error kinds         : {manifest['error_kinds']}")
    print(f"all originally empty: {manifest['all_original_predictions_empty']}")
    print(f"written             : {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
