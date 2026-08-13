#!/usr/bin/env python3
"""Run Profile F1 Capacity exactness repair on hasCapacity rows only."""

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
from cover_kbc.leaderboard_repair.capacity import CAPACITY_MULTIVIEW_FEATURE
from cover_kbc.leaderboard_repair.capacity_exactness import (
    CAPACITY_EXACTNESS_FEATURE,
    CAPACITY_EXACTNESS_MODE,
)
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack
from cover_kbc.leaderboard_repair.util import AREA, AWARD, BORDERS, CAPACITY, CITY, STOCK
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.preflight import require_huggingface_runtime
from cover_kbc.models.registry import build_runtime, model_blocks, spec_from_config
from cover_kbc.types import Prediction, Query


MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
MISTRAL_PARAMETERS = 24_011_361_280
PARAMETER_LIMIT = 32_000_000_000
CAPACITY_ROWS = 98


class CapacityExactnessRunError(RuntimeError):
    """A targeted F1 Capacity exactness invariant failed."""


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


def validate_f1_model_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate that F1 resolves to one shared Mistral runtime."""
    enumerator, verifier = model_blocks(config)
    if enumerator != verifier:
        raise CapacityExactnessRunError("F1 requires one shared Mistral runtime")
    if enumerator.get("model_id") != MISTRAL_ID:
        raise CapacityExactnessRunError(f"unexpected model_id {enumerator.get('model_id')!r}")
    if enumerator.get("revision") != MISTRAL_REVISION:
        raise CapacityExactnessRunError(
            f"unexpected revision {enumerator.get('revision')!r}"
        )
    serialized = json.dumps(config.get("model_profile") or {}, sort_keys=True)
    if "Qwen" in serialized:
        raise CapacityExactnessRunError("Profile F1 model portfolio must not contain Qwen")
    audit = audit_parameter_budget([spec_from_config(enumerator)])
    if not audit.passed:
        raise CapacityExactnessRunError(audit.summary())
    if audit.total_parameters != MISTRAL_PARAMETERS or audit.budget != PARAMETER_LIMIT:
        raise CapacityExactnessRunError(
            f"unexpected parameter accounting {audit.total_parameters}/{audit.budget}"
        )
    return enumerator


def validate_f1_repair_config(config: Mapping[str, Any]) -> LeaderboardRepairConfig:
    repair = LeaderboardRepairConfig.from_mapping(config.get("leaderboard_repair"))
    if not repair.enabled:
        raise CapacityExactnessRunError("leaderboard_repair is disabled")
    if repair.profile != "F1_CAPACITY_EXACTNESS_REPAIR":
        raise CapacityExactnessRunError(f"unexpected repair profile {repair.profile!r}")
    features = repair.features
    if not features.mistral_capacity_multiview:
        raise CapacityExactnessRunError("F1 must keep E3 Capacity Multi-View upstream")
    if not features.mistral_area_multiview:
        raise CapacityExactnessRunError("F1 must preserve E3 Area Multi-View")
    if not features.mistral_capacity_exactness_repair:
        raise CapacityExactnessRunError("F1 exactness feature is disabled")
    if not repair.capacity_exactness_repair.enabled:
        raise CapacityExactnessRunError("capacity_exactness_repair is disabled")
    if repair.capacity_exactness_repair.mode != CAPACITY_EXACTNESS_MODE:
        raise CapacityExactnessRunError(
            f"unexpected exactness mode {repair.capacity_exactness_repair.mode!r}"
        )
    if repair.capacity_exactness_repair.max_repair_calls_per_suspicious_row != 4:
        raise CapacityExactnessRunError("F1 must run exactly four exactness views")
    if repair.cap_for(CAPACITY) != 9:
        raise CapacityExactnessRunError(
            f"hasCapacity call cap is {repair.cap_for(CAPACITY)}; expected 9"
        )

    forbidden_enabled = {
        "stock_entity_guard": features.stock_entity_guard,
        "stock_alias_dedupe": features.stock_alias_dedupe,
        "stock_multi_listing_rescue": features.stock_multi_listing_rescue,
        "border_alias_dedupe": features.border_alias_dedupe,
        "border_directional_sweep": features.border_directional_sweep,
        "border_reciprocity": features.border_reciprocity,
        "death_existence_gate": features.death_existence_gate,
        "death_city_recall": features.death_city_recall,
        "area_empty_rescue": features.area_empty_rescue,
        "capacity_repair": features.capacity_repair,
        "award_recipient_witness": features.award_recipient_witness,
        "award_time_sliced_recall": features.award_time_sliced_recall,
        "l8_consistency": features.l8_consistency,
        "l8_stock_consistency": features.l8_stock_consistency,
        "l9_final_risk_guard": features.l9_final_risk_guard,
        "l9_stock_guard": features.l9_stock_guard,
    }
    enabled = [name for name, value in forbidden_enabled.items() if value]
    if enabled:
        raise CapacityExactnessRunError(f"forbidden F1 feature(s) enabled: {enabled}")
    expected_caps = {
        AREA: 5,
        CAPACITY: 9,
        CITY: 2,
        AWARD: 1,
        STOCK: 0,
        BORDERS: 0,
    }
    actual_caps = {relation: repair.cap_for(relation) for relation in expected_caps}
    if actual_caps != expected_caps:
        raise CapacityExactnessRunError(
            f"unexpected relation caps {actual_caps}; expected {expected_caps}"
        )
    return repair


def capacity_rows(split: str) -> list[Any]:
    if split != "test":
        raise CapacityExactnessRunError("F1 Capacity exactness runner supports TEST only")
    rows = load_dataset(split).filter_relation(CAPACITY)
    if len(rows) != CAPACITY_ROWS:
        raise CapacityExactnessRunError(
            f"TEST has {len(rows)} hasCapacity rows; expected {CAPACITY_ROWS}"
        )
    if not rows:
        raise CapacityExactnessRunError("TEST has no hasCapacity rows")
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


def _latest_decision(
    record: Any,
    *,
    feature: str,
    decision: str,
) -> dict[str, Any]:
    for item in reversed(record.decisions):
        if item.get("feature") == feature and item.get("decision") == decision:
            return dict(item)
    return {}


def _feature_decisions(record: Any, feature: str) -> list[dict[str, Any]]:
    return [dict(item) for item in record.decisions if item.get("feature") == feature]


def diagnostics_rows(records: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        original_final = _latest_decision(
            record,
            feature=CAPACITY_MULTIVIEW_FEATURE,
            decision="final_decision",
        )
        suspicion = _latest_decision(
            record,
            feature=CAPACITY_EXACTNESS_FEATURE,
            decision="suspicion_score",
        )
        clusters = _latest_decision(
            record,
            feature=CAPACITY_EXACTNESS_FEATURE,
            decision="clustered_numeric_outputs",
        )
        acceptance = _latest_decision(
            record,
            feature=CAPACITY_EXACTNESS_FEATURE,
            decision="final_decision",
        )
        exactness_outputs = {
            str(item.get("view_id")): dict(item)
            for item in _feature_decisions(record, CAPACITY_EXACTNESS_FEATURE)
            if item.get("decision") == "view_output"
        }
        f1_calls = [
            call.to_json()
            for call in record.calls
            if call.feature == CAPACITY_EXACTNESS_FEATURE
        ]
        rows.append({
            "SubjectEntity": record.subject,
            "Relation": record.relation,
            "original_e3_answer": list(original_final.get("values") or record.before),
            "original_capacity_multiview_metadata": _feature_decisions(
                record,
                CAPACITY_MULTIVIEW_FEATURE,
            ),
            "suspicion_score": suspicion.get("risk_score", 0.0),
            "suspicion_flags": list(suspicion.get("risk_flags") or []),
            "suspicious": bool(suspicion.get("suspicious", False)),
            "capacity_exactness_outputs": exactness_outputs,
            "repair_clusters": list(clusters.get("clusters") or []),
            "acceptance_decision": acceptance,
            "final_answer": list(record.after),
            "final_reason": str(acceptance.get("reason", "")),
            "f1_repair_call_count": len(f1_calls),
            "f1_repair_calls": f1_calls,
        })
    return rows


def dry_run(
    *,
    config: Mapping[str, Any],
    split: str,
    output_dir: Path,
) -> dict[str, Path]:
    validate_f1_model_config(config)
    repair = validate_f1_repair_config(config)
    rows = capacity_rows(split)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": "capacity-exactness-repair-dry-run-v1",
        "split": split,
        "source_git_revision": head_sha(),
        "total_capacity_rows": len(rows),
        "expected_result_rows": CAPACITY_ROWS,
        "feature": CAPACITY_EXACTNESS_FEATURE,
        "mode": repair.capacity_exactness_repair.mode,
        "suspicion_scorer_neural_calls": 0,
        "repair_calls_per_suspicious_row": 4,
        "max_total_capacity_calls_per_suspicious_row": repair.cap_for(CAPACITY),
        "model_id": MISTRAL_ID,
        "model_revision": MISTRAL_REVISION,
        "unique_published_parameters": MISTRAL_PARAMETERS,
    }
    path = output_dir / "capacity_exactness_repair_dry_run.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"dry_run": path}


def run_capacity_exactness_repair(
    *,
    config: Mapping[str, Any],
    split: str,
    output_dir: Path,
) -> dict[str, Path]:
    enumerator_cfg = validate_f1_model_config(config)
    repair = validate_f1_repair_config(config)
    require_huggingface_runtime(enumerator_cfg, enumerator_cfg)
    rows = capacity_rows(split)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime = build_runtime(enumerator_cfg)
    stack = LeaderboardRepairStack(
        config=repair,
        enumerator=runtime,
        verifier=runtime,
    )
    predictions = [_prediction(row) for row in rows]
    result = stack.apply(predictions, queries=[_query(row) for row in rows])
    result_rows = [prediction.to_official_row() for prediction in result.predictions]
    if len(result_rows) != CAPACITY_ROWS:
        raise CapacityExactnessRunError(
            f"F1 produced {len(result_rows)} Capacity rows; expected {CAPACITY_ROWS}"
        )
    if any(row["Relation"] != CAPACITY for row in result_rows):
        raise CapacityExactnessRunError("F1 result contains a non-Capacity row")

    diagnostics = diagnostics_rows(result.records)
    results_path = write_jsonl(
        output_dir / "capacity_exactness_repair_results.jsonl",
        result_rows,
    )
    diagnostics_path = write_jsonl(
        output_dir / "capacity_exactness_repair_diagnostics.jsonl",
        diagnostics,
    )
    records_path = write_jsonl(
        output_dir / "capacity_exactness_repair_records.jsonl",
        [record.to_json() for record in result.records],
    )
    calls = [
        call.to_json()
        for record in result.records
        for call in record.calls
    ]
    calls_path = write_jsonl(output_dir / "calls.jsonl", calls)
    exactness_summary = (
        result.accounting.get("by_relation", {})
        .get(CAPACITY, {})
        .get("capacity_exactness_repair", {})
    )
    accounting = {
        "schema_version": "capacity-exactness-repair-accounting-v1",
        "split": split,
        "source_git_revision": head_sha(),
        "model_id": MISTRAL_ID,
        "model_revision": MISTRAL_REVISION,
        "total_capacity_rows": len(rows),
        "capacity_exactness_rows": len(result_rows),
        "suspicious_rows": exactness_summary.get("suspicious_rows", 0),
        "untouched_rows": exactness_summary.get("untouched_rows", 0),
        "overridden_rows": exactness_summary.get("overridden_rows", 0),
        "corroborated_rows": exactness_summary.get("corroborated_rows", 0),
        "rejected_repair_rows": exactness_summary.get("rejected_repair_rows", 0),
        "empty_rescued_rows": exactness_summary.get("empty_rescued_rows", 0),
        "total_capacity_exactness_repair_calls": exactness_summary.get(
            "total_capacity_exactness_repair_calls",
            0,
        ),
        "capacity_exactness_x1_calls": exactness_summary.get(
            "capacity_exactness_x1_calls",
            0,
        ),
        "capacity_exactness_x2_calls": exactness_summary.get(
            "capacity_exactness_x2_calls",
            0,
        ),
        "capacity_exactness_x3_calls": exactness_summary.get(
            "capacity_exactness_x3_calls",
            0,
        ),
        "capacity_exactness_x4_calls": exactness_summary.get(
            "capacity_exactness_x4_calls",
            0,
        ),
        "repair_accounting": result.accounting,
        "results_sha256": sha256_file(results_path),
        "diagnostics_sha256": sha256_file(diagnostics_path),
        "records_sha256": sha256_file(records_path),
        "calls_sha256": sha256_file(calls_path),
    }
    accounting_path = output_dir / "capacity_exactness_repair_accounting.json"
    accounting_path.write_text(
        json.dumps(accounting, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "capacity_results": results_path,
        "diagnostics": diagnostics_path,
        "records": records_path,
        "calls": calls_path,
        "accounting": accounting_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--split", required=True, choices=("test",))
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate config/data invariants without loading the model",
    )
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    try:
        if args.dry_run:
            paths = dry_run(config=config, split=args.split, output_dir=args.output_dir)
        else:
            paths = run_capacity_exactness_repair(
                config=config,
                split=args.split,
                output_dir=args.output_dir,
            )
    except CapacityExactnessRunError as error:
        print(f"CAPACITY EXACTNESS REPAIR RUN REFUSED: {error}", file=sys.stderr)
        return 2
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
