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

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

import yaml

from cover_kbc.contracts.router import check_router_consistency
from cover_kbc.data.loader import load_dataset
from cover_kbc.data.writer import write_predictions, write_trace
from cover_kbc.elicitation.library import check_library_covers_contracts
from cover_kbc.evaluation.harness import evaluate_predictions, write_report
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.registry import build_runtime, model_blocks
from cover_kbc.paths import OUTPUTS_DIR
from cover_kbc.evidence.consensus import build_consensus_engine
from cover_kbc.verification.specialist_verifier import build_specialist_verifier
from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
from cover_kbc.query_intelligence import (
    build_parametric_retriever,
    build_profiler,
    build_prompt_compiler,
)
from cover_kbc.runtime.manifest import RunManifest, new_run_id
from cover_kbc.specialists import (
    build_large_set_specialist,
    build_null_temporal_specialist,
    build_numeric_specialist,
    build_small_set_specialist,
)
from cover_kbc.runtime.tracing import RunTracer


def _enabled(config: dict, key: str) -> bool:
    """Whether one Layer-2 specialist is enabled, by configuration alone."""
    return bool(((config.get("specialists") or {}).get(key) or {}).get("enabled", False))


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
    enumerator_cfg, verifier_cfg = model_blocks(config)
    pipeline_cfg = config.get("pipeline", {})

    # Fail fast on a contract/router/library mismatch rather than mid-run.
    check_router_consistency()
    check_library_covers_contracts()

    split = args.split or experiment.get("split", "val")
    dataset = load_dataset(split)
    queries = dataset.queries()
    if args.limit:
        queries = queries[: args.limit]

    # Resolve both logical roles through the *canonical* resolver, so this
    # entry point cannot disagree with `run_staged.py` about which models a
    # config declares - and cannot silently fall back to a stub when handed the
    # frozen target's nested profile.
    runtime = build_runtime(enumerator_cfg)
    verifier_runtime = (
        runtime if verifier_cfg == enumerator_cfg else build_runtime(verifier_cfg)
    )
    specs = [runtime.spec]
    if verifier_runtime is not runtime:
        specs.append(verifier_runtime.spec)
    audit = audit_parameter_budget(specs)
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
        # The canonical config path, so this runner cannot drift from
        # `run_staged.py` on any factual setting. Interleaved is forced: the
        # staged seam is the other runner's job, and honouring `mode: staged`
        # here would run half an architecture.
        config_block = dict(pipeline_cfg)
        config_block["mode"] = ExecutionMode.INTERLEAVED.value
        config_block.setdefault("seed", manifest.seed)
        pipeline_config = PipelineConfig.from_mapping(config_block)
        pipeline_config.enumerator_model_id = enumerator_cfg.get("model_id", "")
        pipeline_config.verifier_model_id = verifier_cfg.get("model_id", "")
        profiler = build_profiler(config.get("query_intelligence"))
        prompt_compiler = build_prompt_compiler(
            config.get("query_intelligence"), profiler_enabled=profiler is not None
        )
        retriever = build_parametric_retriever(
            config.get("query_intelligence"),
            profiler_enabled=profiler is not None,
            compiler_enabled=prompt_compiler is not None,
        )
        pipeline = CoverPipeline(
            runtime, pipeline_config, tracer=tracer, verifier_runtime=verifier_runtime,
            # Modules 9 and 10, shadow mode: they profile and compile at the M1
            # seam and feed nothing back into the run.
            profiler=profiler,
            prompt_compiler=prompt_compiler,
            retriever=retriever,
            numeric_specialist=build_numeric_specialist(
                config.get("specialists"),
                profiler_enabled=profiler is not None,
                compiler_enabled=prompt_compiler is not None,
                retrieval_enabled=retriever is not None,
            ),
            large_set_specialist=build_large_set_specialist(
                config.get("specialists"),
                profiler_enabled=profiler is not None,
                compiler_enabled=prompt_compiler is not None,
                retrieval_enabled=retriever is not None,
            ),
            null_temporal_specialist=build_null_temporal_specialist(
                config.get("specialists"),
                profiler_enabled=profiler is not None,
                compiler_enabled=prompt_compiler is not None,
                retrieval_enabled=retriever is not None,
            ),
            small_set_specialist=build_small_set_specialist(
                config.get("specialists"),
                profiler_enabled=profiler is not None,
                compiler_enabled=prompt_compiler is not None,
                retrieval_enabled=retriever is not None,
            ),
            # Module 16, shadow mode and non-neural: it fuses what the modules
            # above recorded and changes no prediction.
            consensus_engine=build_consensus_engine(
                config.get("consensus"),
                profiler_enabled=profiler is not None,
                compiler_enabled=prompt_compiler is not None,
                retrieval_enabled=retriever is not None,
                available_specialists={
                    "M12": _enabled(config, "numeric"),
                    "M13": _enabled(config, "large_open_set"),
                    "M14": _enabled(config, "null_temporal"),
                    "M15": _enabled(config, "small_set_closure"),
                },
                relations=sorted({q.relation for q in queries}),
            ),
            # Module 17, shadow: the catalogue costs nothing and no target is
            # verified without an explicit request.
            specialist_verifier=build_specialist_verifier(
                config.get("specialist_verifier"),
                consensus_enabled=bool(
                    (config.get("consensus") or {}).get("enabled", False)
                ),
                verifier_available=verifier_runtime is not None,
            ) if (config.get("consensus") or {}).get("enabled", False) else None,
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

    for tag, name, records in (
        ("M9", "query_profiles.jsonl", pipeline.query_profiles),
        ("M10", "prompt_programs.jsonl", pipeline.prompt_programs),
        ("M11", "parametric_memory.jsonl", pipeline.retrieval_results),
        ("M12", "numeric_specialist.jsonl", pipeline.numeric_results),
        ("M13", "large_open_set_specialist.jsonl", pipeline.large_set_results),
        ("M14", "null_temporal_specialist.jsonl", pipeline.null_temporal_results),
        ("M15", "small_set_specialist.jsonl", pipeline.small_set_results),
        ("M16", "atomic_consensus.jsonl", pipeline.consensus_results),
        ("M17", "specialist_verification.jsonl", pipeline.specialist_verifications),
    ):
        if not records:
            continue
        path = out_dir / name
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
        print(f"[{tag}] {path}  ({len(records)} queries)")

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
