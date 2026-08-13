#!/usr/bin/env python3
"""Run the active Capacity Multi-View final layer on hasCapacity rows only."""

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

import yaml

from cover_kbc.data.loader import load_dataset
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.preflight import require_huggingface_runtime
from cover_kbc.models.registry import build_runtime, model_blocks, spec_from_config
from cover_kbc.types import Prediction, Query


CAPACITY = "hasCapacity"
MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
MISTRAL_PARAMETERS = 24_011_361_280
PARAMETER_LIMIT = 32_000_000_000


class CapacityRunError(RuntimeError):
    """A targeted Capacity Multi-View invariant failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


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


def validate_capacity_model_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate that Capacity Multi-View resolves to one shared Mistral model."""
    enumerator, verifier = model_blocks(config)
    if enumerator != verifier:
        raise CapacityRunError("Capacity Multi-View requires one shared Mistral runtime")
    if enumerator.get("model_id") != MISTRAL_ID:
        raise CapacityRunError(f"unexpected model_id {enumerator.get('model_id')!r}")
    if enumerator.get("revision") != MISTRAL_REVISION:
        raise CapacityRunError(f"unexpected revision {enumerator.get('revision')!r}")
    serialized = json.dumps(config.get("model_profile") or {}, sort_keys=True)
    if "Qwen/Qwen3.5-4B" in serialized:
        raise CapacityRunError("Capacity Multi-View model portfolio must not contain Qwen")
    audit = audit_parameter_budget([spec_from_config(enumerator)])
    if not audit.passed:
        raise CapacityRunError(audit.summary())
    if audit.total_parameters != MISTRAL_PARAMETERS or audit.budget != PARAMETER_LIMIT:
        raise CapacityRunError(
            f"unexpected parameter accounting {audit.total_parameters}/{audit.budget}"
        )
    return enumerator


def capacity_rows(split: str) -> list[Any]:
    rows = load_dataset(split).filter_relation(CAPACITY)
    if split == "test" and len(rows) != 98:
        raise CapacityRunError(f"TEST has {len(rows)} hasCapacity rows; expected 98")
    if not rows:
        raise CapacityRunError(f"{split} has no hasCapacity rows")
    return rows


def _prediction(row: Any) -> Prediction:
    return Prediction(
        subject=row.subject,
        relation=row.relation,
        object_entities=[],
        row_index=row.row_index,
    )


def _query(row: Any) -> Query:
    return Query(row.subject, row.relation, row.row_index)


def run_capacity_multiview(
    *,
    config: Mapping[str, Any],
    split: str,
    output_dir: Path,
) -> dict[str, Path]:
    if split not in {"val", "test"}:
        raise CapacityRunError("Capacity Multi-View runner supports only val/test")
    enumerator_cfg = validate_capacity_model_config(config)
    require_huggingface_runtime(enumerator_cfg, enumerator_cfg)
    rows = capacity_rows(split)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime = build_runtime(enumerator_cfg)
    repair_config = LeaderboardRepairConfig.from_mapping(config.get("leaderboard_repair"))
    if not repair_config.features.mistral_capacity_multiview:
        raise CapacityRunError("config does not enable MistralCapacityMultiView")
    stack = LeaderboardRepairStack(
        config=repair_config,
        enumerator=runtime,
        verifier=runtime,
    )
    predictions = [_prediction(row) for row in rows]
    result = stack.apply(predictions, queries=[_query(row) for row in rows])
    result_rows = [prediction.to_official_row() for prediction in result.predictions]

    results_path = write_jsonl(output_dir / "capacity_multiview_results.jsonl", result_rows)
    records_path = write_jsonl(
        output_dir / "capacity_multiview_records.jsonl",
        [record.to_json() for record in result.records],
    )
    calls = [
        call.to_json()
        for record in result.records
        for call in record.calls
    ]
    calls_path = write_jsonl(output_dir / "calls.jsonl", calls)
    accounting = {
        "schema_version": "capacity-multiview-accounting-v1",
        "split": split,
        "source_git_revision": head_sha(),
        "model_id": MISTRAL_ID,
        "model_revision": MISTRAL_REVISION,
        "eligible_hasCapacity_rows": len(rows),
        "capacity_multiview_rows": len(result_rows),
        "total_capacity_multiview_calls": len(calls),
        "max_calls_per_hasCapacity_row": repair_config.cap_for(CAPACITY),
        "repair_accounting": result.accounting,
        "results_sha256": sha256_file(results_path),
        "records_sha256": sha256_file(records_path),
        "calls_sha256": sha256_file(calls_path),
    }
    accounting_path = output_dir / "capacity_multiview_accounting.json"
    accounting_path.write_text(
        json.dumps(accounting, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "capacity_results": results_path,
        "records": records_path,
        "calls": calls_path,
        "accounting": accounting_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--split", required=True, choices=("val", "test"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    try:
        paths = run_capacity_multiview(
            config=config,
            split=args.split,
            output_dir=args.output_dir,
        )
    except CapacityRunError as error:
        print(f"CAPACITY MULTIVIEW RUN REFUSED: {error}", file=sys.stderr)
        return 2
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
