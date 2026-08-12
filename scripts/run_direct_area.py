#!/usr/bin/env python3
"""Run integrated Profile E1 Direct Area on Area rows only.

This targeted runner does not execute the full COVER pipeline. It loads the
configured shared Mistral runtime, selects only ``hasArea`` rows from an
official split, runs one Direct Area prompt per row, and writes a replayable
direct-result artifact for deterministic merging.
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

import yaml

from cover_kbc.data.loader import load_dataset
from cover_kbc.leaderboard_repair.area import (
    DIRECT_AREA_FEATURE,
    DIRECT_AREA_SYSTEM_PROMPT,
    INVALID,
    UNKNOWN,
    VALID_AREA,
    direct_area_prompt,
    direct_area_values,
    parse_direct_area_output,
)
from cover_kbc.models.base import GenerationRequest
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.preflight import require_huggingface_runtime
from cover_kbc.models.registry import build_runtime, model_blocks, spec_from_config
from cover_kbc.types import DecodeProfile


AREA = "hasArea"
MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
MISTRAL_PARAMETERS = 24_011_361_280
PARAMETER_LIMIT = 32_000_000_000


class DirectAreaRunError(RuntimeError):
    """A targeted Direct Area invariant failed."""


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


def validate_direct_area_model_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate that the active baseline resolves to one shared Mistral model."""
    enumerator, verifier = model_blocks(config)
    if enumerator != verifier:
        raise DirectAreaRunError("Direct Area requires one shared Mistral runtime")
    if enumerator.get("model_id") != MISTRAL_ID:
        raise DirectAreaRunError(f"unexpected model_id {enumerator.get('model_id')!r}")
    if enumerator.get("revision") != MISTRAL_REVISION:
        raise DirectAreaRunError(f"unexpected revision {enumerator.get('revision')!r}")
    serialized = json.dumps(config.get("model_profile") or {}, sort_keys=True)
    if "Qwen/Qwen3.5-4B" in serialized:
        raise DirectAreaRunError("active Direct Area model portfolio must not contain Qwen")
    audit = audit_parameter_budget([spec_from_config(enumerator)])
    if not audit.passed:
        raise DirectAreaRunError(audit.summary())
    if audit.total_parameters != MISTRAL_PARAMETERS or audit.budget != PARAMETER_LIMIT:
        raise DirectAreaRunError(
            f"unexpected parameter accounting {audit.total_parameters}/{audit.budget}"
        )
    return enumerator


def area_rows(split: str) -> list[Any]:
    rows = load_dataset(split).filter_relation(AREA)
    if split == "test" and len(rows) != 100:
        raise DirectAreaRunError(f"TEST has {len(rows)} hasArea rows; expected 100")
    if not rows:
        raise DirectAreaRunError(f"{split} has no hasArea rows")
    return rows


def make_direct_result_row(
    *,
    subject: str,
    row_index: int,
    raw_output: str,
    model_id: str,
    prompt_tokens: int = 0,
    generated_tokens: int = 0,
) -> dict[str, Any]:
    parsed = parse_direct_area_output(raw_output)
    return {
        "SubjectEntity": subject,
        "Relation": AREA,
        "row_index": row_index,
        "ObjectEntities": direct_area_values(parsed),
        "direct_raw_output": raw_output,
        "direct_parse_status": parsed.status,
        "direct_value": parsed.value,
        "direct_number": str(parsed.number) if parsed.number is not None else "",
        "model_id": model_id,
        "prompt_tokens": int(prompt_tokens or 0),
        "generated_tokens": int(generated_tokens or 0),
    }


def run_direct_area(
    *,
    config: Mapping[str, Any],
    split: str,
    output_dir: Path,
) -> dict[str, Path]:
    if split not in {"val", "test"}:
        raise DirectAreaRunError("Direct Area runner supports only val/test")
    enumerator_cfg = validate_direct_area_model_config(config)
    require_huggingface_runtime(enumerator_cfg, enumerator_cfg)
    rows = area_rows(split)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime = build_runtime(enumerator_cfg)
    direct_rows: list[dict[str, Any]] = []
    calls_path = output_dir / "calls.jsonl"
    with calls_path.open("w", encoding="utf-8") as calls_handle:
        for row in rows:
            prompt = direct_area_prompt(row.subject)
            result = runtime.generate(
                GenerationRequest(
                    prompt=prompt,
                    system_prompt=DIRECT_AREA_SYSTEM_PROMPT,
                    decode=DecodeProfile(
                        name="mistral_direct_area_greedy",
                        temperature=0.0,
                        top_p=1.0,
                        max_new_tokens=24,
                    ),
                    metadata={
                        "view_id": "mistral_direct_area",
                        "subject": row.subject,
                        "relation": AREA,
                        "repair_feature": DIRECT_AREA_FEATURE,
                    },
                )
            )
            direct = make_direct_result_row(
                subject=row.subject,
                row_index=row.row_index,
                raw_output=result.text,
                model_id=result.model_id,
                prompt_tokens=int(result.prompt_tokens or 0),
                generated_tokens=int(result.generated_tokens or 0),
            )
            direct_rows.append(direct)
            calls_handle.write(json.dumps({
                "kind": "generation",
                "layer": "E1_DIRECT_AREA",
                "feature": DIRECT_AREA_FEATURE,
                "Relation": AREA,
                "SubjectEntity": row.subject,
                "row_index": row.row_index,
                "view_id": "mistral_direct_area",
                "model_role": "verifier",
                "model_id": result.model_id,
                "prompt": prompt,
                "system_prompt": DIRECT_AREA_SYSTEM_PROMPT,
                "raw_output": result.text,
                "decode": {
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "max_new_tokens": 24,
                    "do_sample": False,
                },
                "prompt_tokens": int(result.prompt_tokens or 0),
                "generated_tokens": int(result.generated_tokens or 0),
            }, ensure_ascii=False) + "\n")

    direct_path = write_jsonl(output_dir / "direct_area_results.jsonl", direct_rows)
    accounting = {
        "schema_version": "direct-area-accounting-v1",
        "split": split,
        "source_git_revision": head_sha(),
        "model_id": MISTRAL_ID,
        "model_revision": MISTRAL_REVISION,
        "eligible_hasArea_rows": len(rows),
        "direct_area_calls": len(direct_rows),
        "valid_area_count": sum(1 for row in direct_rows if row["direct_parse_status"] == VALID_AREA),
        "unknown_count": sum(1 for row in direct_rows if row["direct_parse_status"] == UNKNOWN),
        "invalid_count": sum(1 for row in direct_rows if row["direct_parse_status"] == INVALID),
        "error_count": 0,
        "max_calls_per_hasArea_row": 1,
        "direct_results_sha256": sha256_file(direct_path),
        "calls_sha256": sha256_file(calls_path),
    }
    accounting_path = output_dir / "direct_area_accounting.json"
    accounting_path.write_text(
        json.dumps(accounting, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "direct_results": direct_path,
        "accounting": accounting_path,
        "calls": calls_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--split", required=True, choices=("val", "test"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    try:
        paths = run_direct_area(
            config=config,
            split=args.split,
            output_dir=args.output_dir,
        )
    except DirectAreaRunError as error:
        print(f"DIRECT AREA RUN REFUSED: {error}", file=sys.stderr)
        return 2

    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
