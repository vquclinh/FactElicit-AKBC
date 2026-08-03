#!/usr/bin/env python3
"""Run the COVER pipeline over an official split and score the result.

One command, one committed config, one reproducible run (spec invariant 10).
Writes predictions, a full per-query trace, a call-level log and a run manifest
under ``outputs/<run_id>/``.

Examples:
    python scripts/run_cover.py --config configs/experiments/smoke_abstain.yaml
    python scripts/run_cover.py --config configs/experiments/smoke_abstain.yaml --limit 30
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

import yaml

from cover_kbc.contracts.router import check_router_consistency
from cover_kbc.data.loader import load_dataset
from cover_kbc.data.writer import write_predictions, write_trace
from cover_kbc.elicitation.library import check_library_covers_contracts
from cover_kbc.evaluation.harness import evaluate_predictions, write_report
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.registry import build_runtime
from cover_kbc.paths import OUTPUTS_DIR
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.runtime.manifest import RunManifest, new_run_id
from cover_kbc.runtime.tracing import RunTracer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="experiment YAML")
    parser.add_argument("--split", help="override the split named in the config")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N queries")
    parser.add_argument("--output-dir", type=Path, help="override outputs/<run_id>")
    parser.add_argument("--no-eval", action="store_true", help="skip scoring")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text()) or {}
    experiment = config.get("experiment", {})
    model_profile = config.get("model_profile", {})
    pipeline_cfg = config.get("pipeline", {})

    # Fail fast on a contract/router/library mismatch rather than mid-run.
    check_router_consistency()
    check_library_covers_contracts()

    split = args.split or experiment.get("split", "val")
    dataset = load_dataset(split)
    queries = dataset.queries()
    if args.limit:
        queries = queries[: args.limit]

    runtime = build_runtime(model_profile)
    audit = audit_parameter_budget([runtime.spec])
    if not audit.passed:
        print(audit.summary())
        raise SystemExit(
            "Parameter budget audit failed. Record the published parameter count, or "
            "choose a compliant profile, before running."
        )

    run_id = new_run_id(experiment.get("name", "cover"), split)
    out_dir = args.output_dir or (OUTPUTS_DIR / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = RunManifest(
        run_id=run_id,
        experiment=experiment.get("name", "cover"),
        split=split,
        seed=int(experiment.get("seed", 42)),
        config=config,
        dataset_sha256=dataset.sha256,
        dataset_path=str(dataset.path),
        num_queries=len(queries),
        budget_audit=audit.to_json(),
        notes=experiment.get("notes", ""),
    )
    manifest.add_model(runtime.spec)
    manifest.start()

    print(f"run_id      : {run_id}")
    print(f"split       : {split} ({len(queries)} queries of {len(dataset)})")
    print(f"model       : {runtime.spec.model_id}")
    print(f"config hash : {manifest.config_hash}")
    print(f"outputs     : {out_dir}")

    with RunTracer(out_dir / "calls.jsonl") as tracer:
        pipeline = CoverPipeline(
            runtime,
            PipelineConfig(
                seed=manifest.seed,
                run_optional_views=bool(pipeline_cfg.get("run_optional_views", False)),
                enable_verifier=bool(pipeline_cfg.get("enable_verifier", False)),
                max_verifications_per_query=int(
                    pipeline_cfg.get("max_verifications_per_query", 0)
                ),
            ),
            tracer=tracer,
        )
        result = pipeline.run(queries, progress=True)

    manifest.finish()
    manifest.total_calls = result.total_calls
    manifest.total_generated_tokens = result.total_generated_tokens
    manifest.total_prompt_tokens = result.total_prompt_tokens

    predictions_path = write_predictions(
        result.predictions, out_dir / "predictions.jsonl", expected_queries=queries
    )
    write_trace(result.predictions, out_dir / "trace.jsonl")
    print(f"\npredictions : {predictions_path}")

    if result.errors:
        (out_dir / "errors.json").write_text(json.dumps(result.errors, indent=2))
        print(f"errors      : {len(result.errors)} (see errors.json)")

    if not args.no_eval and not dataset.is_blind:
        gold = [
            row.to_official_row()
            for row in dataset.rows
            if (row.subject, row.relation) in {(q.subject, q.relation) for q in queries}
        ]
        report = evaluate_predictions(
            [p.to_official_row() for p in result.predictions], gold
        )
        manifest.evaluation = report.to_json()
        write_report(report, out_dir / "metrics.json")
        print()
        print(report.to_table())
    elif dataset.is_blind:
        print("\nsplit is blind (no gold objects); skipping evaluation.")

    manifest.write(out_dir / "manifest.json")
    print(f"\nmanifest    : {out_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
