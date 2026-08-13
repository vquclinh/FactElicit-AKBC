#!/usr/bin/env python3
"""Run Profile F1 Stock Empty Rescue on stock rows from a baseline artifact."""

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
from cover_kbc.data.schema import validate_prediction_row
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack
from cover_kbc.leaderboard_repair.stock_empty_rescue import (
    STOCK_EMPTY_RESCUE_FEATURE,
    STOCK_EMPTY_RESCUE_MODE,
)
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.preflight import require_huggingface_runtime
from cover_kbc.models.registry import build_runtime, model_blocks, spec_from_config
from cover_kbc.types import Prediction, Query


STOCK = "companyTradesAtStockExchange"
TOTAL_ROWS = 475
STOCK_ROWS = 100
MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
MISTRAL_PARAMETERS = 24_011_361_280
PARAMETER_LIMIT = 32_000_000_000


class StockEmptyRescueRunError(RuntimeError):
    """A targeted Stock Empty Rescue invariant failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            row = official_prediction_row(json.loads(line), index=index)
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def official_prediction_row(row: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    out = {
        "SubjectEntity": str(row["SubjectEntity"]),
        "Relation": str(row["Relation"]),
        "ObjectEntities": [str(value) for value in row.get("ObjectEntities") or []],
    }
    validate_prediction_row(out, index=index)
    return out


def identity(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row["SubjectEntity"]), str(row["Relation"]))


def index_unique(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = identity(row)
        if key in out:
            raise StockEmptyRescueRunError(f"{label}: duplicate key {key}")
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


def validate_stock_model_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate that Profile F1 resolves to one shared Mistral model."""
    enumerator, verifier = model_blocks(config)
    if enumerator != verifier:
        raise StockEmptyRescueRunError(
            "Stock Empty Rescue requires one shared Mistral runtime"
        )
    if enumerator.get("model_id") != MISTRAL_ID:
        raise StockEmptyRescueRunError(
            f"unexpected model_id {enumerator.get('model_id')!r}"
        )
    if enumerator.get("revision") != MISTRAL_REVISION:
        raise StockEmptyRescueRunError(
            f"unexpected revision {enumerator.get('revision')!r}"
        )
    serialized = json.dumps(config.get("model_profile") or {}, sort_keys=True)
    if "Qwen/Qwen3.5-4B" in serialized:
        raise StockEmptyRescueRunError("Profile F1 model portfolio must not contain Qwen")
    audit = audit_parameter_budget([spec_from_config(enumerator)])
    if not audit.passed:
        raise StockEmptyRescueRunError(audit.summary())
    if audit.total_parameters != MISTRAL_PARAMETERS or audit.budget != PARAMETER_LIMIT:
        raise StockEmptyRescueRunError(
            f"unexpected parameter accounting {audit.total_parameters}/{audit.budget}"
        )
    return enumerator


def validate_stock_repair_config(config: Mapping[str, Any]) -> LeaderboardRepairConfig:
    repair_config = LeaderboardRepairConfig.from_mapping(config.get("leaderboard_repair"))
    if not repair_config.enabled:
        raise StockEmptyRescueRunError("leaderboard_repair is disabled")
    if not repair_config.features.mistral_stock_empty_rescue:
        raise StockEmptyRescueRunError("config does not enable MistralStockEmptyRescue")
    if repair_config.stock_empty_rescue_mode != STOCK_EMPTY_RESCUE_MODE:
        raise StockEmptyRescueRunError(
            f"unexpected stock mode {repair_config.stock_empty_rescue_mode!r}"
        )
    if repair_config.stock_empty_rescue_min_support != 3:
        raise StockEmptyRescueRunError(
            "Stock Empty Rescue min support must remain 3 for Profile F1"
        )
    if repair_config.cap_for(STOCK) != 4:
        raise StockEmptyRescueRunError(
            f"stock call cap is {repair_config.cap_for(STOCK)}; expected 4"
        )
    return repair_config


def stock_dataset_rows(split: str) -> list[Any]:
    if split != "test":
        raise StockEmptyRescueRunError("Stock Empty Rescue targeted runner supports TEST only")
    rows = load_dataset(split).filter_relation(STOCK)
    if len(rows) != STOCK_ROWS:
        raise StockEmptyRescueRunError(
            f"TEST has {len(rows)} stock rows; expected {STOCK_ROWS}"
        )
    return rows


def baseline_stock_predictions(
    baseline_predictions: Path,
    *,
    split: str,
) -> tuple[list[Prediction], list[Query], int]:
    rows = read_jsonl(baseline_predictions)
    if len(rows) != TOTAL_ROWS:
        raise StockEmptyRescueRunError(
            f"baseline has {len(rows)} rows; expected {TOTAL_ROWS}"
        )
    by_key = index_unique(rows, label="baseline predictions")
    stock_rows = stock_dataset_rows(split)
    stock_keys = [(row.subject, row.relation) for row in stock_rows]
    baseline_stock_keys = [identity(row) for row in rows if row["Relation"] == STOCK]
    if baseline_stock_keys != stock_keys:
        raise StockEmptyRescueRunError("baseline stock key order does not match official TEST")

    predictions: list[Prediction] = []
    queries: list[Query] = []
    empty_rows = 0
    for row in stock_rows:
        payload = by_key[(row.subject, row.relation)]
        values = [str(value) for value in payload.get("ObjectEntities") or []]
        if not values:
            empty_rows += 1
        predictions.append(Prediction(
            subject=row.subject,
            relation=row.relation,
            object_entities=values,
            row_index=row.row_index,
        ))
        queries.append(Query(row.subject, row.relation, row.row_index))
    return predictions, queries, empty_rows


def dry_run(
    *,
    config: Mapping[str, Any],
    baseline_predictions: Path,
    split: str,
    output_dir: Path,
) -> dict[str, Path]:
    validate_stock_model_config(config)
    repair_config = validate_stock_repair_config(config)
    predictions, _queries, empty_rows = baseline_stock_predictions(
        baseline_predictions,
        split=split,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": "stock-empty-rescue-dry-run-v1",
        "split": split,
        "source_git_revision": head_sha(),
        "baseline_predictions": str(baseline_predictions),
        "baseline_sha256": sha256_file(baseline_predictions),
        "total_stock_rows": len(predictions),
        "empty_stock_rows_to_repair": empty_rows,
        "non_empty_stock_rows_untouched": len(predictions) - empty_rows,
        "feature": STOCK_EMPTY_RESCUE_FEATURE,
        "mode": STOCK_EMPTY_RESCUE_MODE,
        "min_support": repair_config.stock_empty_rescue_min_support,
        "max_calls_per_empty_stock_row": repair_config.cap_for(STOCK),
        "expected_stock_empty_rescue_calls": empty_rows * 4,
        "model_id": MISTRAL_ID,
        "model_revision": MISTRAL_REVISION,
        "unique_published_parameters": MISTRAL_PARAMETERS,
    }
    path = output_dir / "stock_empty_rescue_dry_run.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"dry_run": path}


def run_stock_empty_rescue(
    *,
    config: Mapping[str, Any],
    baseline_predictions: Path,
    split: str,
    output_dir: Path,
) -> dict[str, Path]:
    enumerator_cfg = validate_stock_model_config(config)
    repair_config = validate_stock_repair_config(config)
    require_huggingface_runtime(enumerator_cfg, enumerator_cfg)
    predictions, queries, empty_rows = baseline_stock_predictions(
        baseline_predictions,
        split=split,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime = build_runtime(enumerator_cfg)
    stack = LeaderboardRepairStack(
        config=repair_config,
        enumerator=runtime,
        verifier=runtime,
    )
    result = stack.apply(predictions, queries=queries)
    result_rows = [prediction.to_official_row() for prediction in result.predictions]

    results_path = write_jsonl(output_dir / "stock_empty_rescue_results.jsonl", result_rows)
    records_path = write_jsonl(
        output_dir / "stock_empty_rescue_records.jsonl",
        [record.to_json() for record in result.records],
    )
    calls = [
        call.to_json()
        for record in result.records
        for call in record.calls
    ]
    calls_path = write_jsonl(output_dir / "calls.jsonl", calls)
    accounting = {
        "schema_version": "stock-empty-rescue-accounting-v1",
        "split": split,
        "source_git_revision": head_sha(),
        "baseline_predictions": str(baseline_predictions),
        "baseline_sha256": sha256_file(baseline_predictions),
        "model_id": MISTRAL_ID,
        "model_revision": MISTRAL_REVISION,
        "total_stock_rows": len(result_rows),
        "empty_stock_rows_attempted": empty_rows,
        "non_empty_stock_rows_untouched": len(result_rows) - empty_rows,
        "rescued_empty_rows": sum(
            1 for record in result.records if not record.before and record.after
        ),
        "kept_empty_rows": sum(
            1 for record in result.records if not record.before and not record.after
        ),
        "total_stock_empty_rescue_calls": len(calls),
        "max_calls_per_empty_stock_row": repair_config.cap_for(STOCK),
        "repair_accounting": result.accounting,
        "results_sha256": sha256_file(results_path),
        "records_sha256": sha256_file(records_path),
        "calls_sha256": sha256_file(calls_path),
    }
    accounting_path = output_dir / "stock_empty_rescue_accounting.json"
    accounting_path.write_text(
        json.dumps(accounting, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "stock_results": results_path,
        "records": records_path,
        "calls": calls_path,
        "accounting": accounting_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--baseline-predictions", required=True, type=Path)
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
            paths = dry_run(
                config=config,
                baseline_predictions=args.baseline_predictions,
                split=args.split,
                output_dir=args.output_dir,
            )
        else:
            paths = run_stock_empty_rescue(
                config=config,
                baseline_predictions=args.baseline_predictions,
                split=args.split,
                output_dir=args.output_dir,
            )
    except StockEmptyRescueRunError as error:
        print(f"STOCK EMPTY RESCUE RUN REFUSED: {error}", file=sys.stderr)
        return 2
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
