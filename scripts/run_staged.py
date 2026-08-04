#!/usr/bin/env python3
"""Staged COVER-KBC execution: enumerate -> verify -> decide.

Each phase is a separate invocation, so at most one heavyweight model is
resident at a time. That is what lets the 28.67B pairing run on a Colab GPU
that cannot hold both models concurrently. The counted parameter budget is
unchanged by the split.

    python scripts/run_staged.py enumerate --config C --split val --limit 20
    python scripts/run_staged.py verify    --config C --run-dir outputs/<run>
    python scripts/run_staged.py decide    --config C --run-dir outputs/<run>
    python scripts/run_staged.py all       --config C --split val --limit 20

``decide`` is entirely non-neural, so it can be re-run with different
thresholds against one expensive set of generations.
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
from cover_kbc.models.registry import build_runtime, spec_from_config
from cover_kbc.paths import OUTPUTS_DIR
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.runtime.manifest import RunManifest, new_run_id
from cover_kbc.runtime.tracing import RunTracer
from cover_kbc.staging import StageWriter, read_stage, stage_summary
from cover_kbc.types import ModelRole, Query

ENUMERATED = "stage_a_enumerated.jsonl"
VERIFIED = "stage_b_verified.jsonl"
#: One file per role-swap cycle, so every intermediate state stays inspectable.
RESUMED = "stage_r{cycle}_{role}.jsonl"
#: Bug detector, not a stopping rule: the call/token budget is what actually
#: bounds the loop. Exceeding this means the orchestration is cycling, which
#: must fail loudly rather than quietly return a half-finished row.
MAX_ROLE_SWAPS = 12


def load_config(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text()) or {}


def model_blocks(config: dict) -> tuple[dict, dict]:
    """Return ``(enumerator_cfg, verifier_cfg)`` from a config."""
    profile = config.get("model_profile", {}) or {}
    if "enumerator" in profile or "verifier" in profile:
        enumerator = profile.get("enumerator", {})
        verifier = profile.get("verifier", enumerator)
    else:
        enumerator = profile
        verifier = profile
    return enumerator, verifier


def audit_or_die(config: dict) -> dict:
    """Check the 32B budget before any weights load. Fails closed."""
    enumerator, verifier = model_blocks(config)
    specs = [spec_from_config(enumerator)]
    if verifier.get("model_id") != enumerator.get("model_id"):
        specs.append(spec_from_config(verifier))
    audit = audit_parameter_budget(specs)
    print(audit.summary())
    if not audit.passed:
        raise SystemExit(
            "Parameter budget audit FAILED. Fix the profile before running inference."
        )
    asserted = (config.get("budget_assertion") or {}).get("total_published_parameters")
    if asserted is not None and asserted != audit.total_parameters:
        raise SystemExit(
            f"Config asserts {asserted:,} total parameters but the profiles sum to "
            f"{audit.total_parameters:,}. Refusing to run on inconsistent metadata."
        )
    return audit.to_json()


def resolve_run_dir(args, config: dict, split: str) -> Path:
    if args.run_dir:
        path = Path(args.run_dir)
    else:
        name = (config.get("experiment") or {}).get("name", "cover")
        path = OUTPUTS_DIR / new_run_id(name, split)
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_pipeline(config: dict, *, phase: str, tracer: RunTracer | None) -> CoverPipeline:
    """Construct a pipeline loading only the models the phase needs."""
    enumerator_cfg, verifier_cfg = model_blocks(config)
    pipeline_cfg = PipelineConfig.from_mapping(config.get("pipeline"))

    if phase == "enumerate":
        runtime = build_runtime(enumerator_cfg)
        verifier = None
        # Cross-model recall needs the second model; in staged mode it is not
        # resident during Phase A, so it is deferred rather than silently skipped.
        if pipeline_cfg.enable_cross_model_recall:
            print(
                "note: cross-model recall requires the verifier model; in staged mode "
                "it runs during the verify phase."
            )
    elif phase == "verify":
        runtime = build_runtime(verifier_cfg)
        verifier = runtime
    else:  # decide - no model at all
        runtime = build_runtime({"backend": "null", "model_id": "offline/null"})
        verifier = None

    return CoverPipeline(runtime, pipeline_cfg, tracer=tracer, verifier_runtime=verifier)


def phase_enumerate(args, config: dict) -> Path:
    split = args.split or (config.get("experiment") or {}).get("split", "val")
    dataset = load_dataset(split)
    queries = dataset.queries()
    if getattr(args, "relation", None):
        queries = [q for q in queries if q.relation == args.relation]
        if not queries:
            raise SystemExit(f"no {args.relation} queries in split {split}")
    if args.limit:
        queries = queries[: args.limit]

    run_dir = resolve_run_dir(args, config, split)
    audit = audit_or_die(config)

    manifest = RunManifest(
        run_id=run_dir.name,
        experiment=(config.get("experiment") or {}).get("name", "cover"),
        split=split,
        seed=int((config.get("experiment") or {}).get("seed", 42)),
        config=config,
        dataset_sha256=dataset.sha256,
        dataset_path=str(dataset.path),
        num_queries=len(queries),
        budget_audit=audit,
    )
    manifest.start()

    print(f"\n[PHASE A] enumerate  split={split}  queries={len(queries)}  dir={run_dir}")
    with RunTracer(run_dir / "calls_enumerate.jsonl") as tracer:
        pipeline = build_pipeline(config, phase="enumerate", tracer=tracer)
        manifest.add_model(pipeline.runtime.spec)
        with StageWriter(run_dir / ENUMERATED) as writer:
            for graph in pipeline.enumerate(queries, progress=True):
                writer.write(graph)

    manifest.finish()
    manifest.write(run_dir / "manifest_enumerate.json")
    print(json.dumps(stage_summary(run_dir / ENUMERATED), indent=2))
    return run_dir


def phase_verify(args, config: dict) -> Path:
    run_dir = Path(args.run_dir) if args.run_dir else None
    if run_dir is None:
        raise SystemExit("--run-dir is required for the verify phase")
    source = run_dir / ENUMERATED
    audit_or_die(config)

    print(f"\n[PHASE B] verify  dir={run_dir}")
    with RunTracer(run_dir / "calls_verify.jsonl") as tracer:
        pipeline = build_pipeline(config, phase="verify", tracer=tracer)
        with StageWriter(run_dir / VERIFIED) as writer:
            for graph in pipeline.verify(read_stage(source), progress=True):
                writer.write(graph)

    print(json.dumps(stage_summary(run_dir / VERIFIED), indent=2))
    return run_dir


def phase_resolve(args, config: dict) -> Path:
    """Drive the role-swap loop until no query has executable work left.

    Algorithm 1 does not end after one enumerate and one verify: the controller
    keeps choosing until a relation-specific stop or the hard budget. Staged
    execution may swap which model is resident between those choices, but it
    may not drop ``Execute(action)`` from the loop - so this reloads whichever
    role the pending actions need and runs them, repeatedly, until none remain.

    The same code path serves the scripted smoke and a real Mistral/Qwen run;
    only the runtime implementations differ.
    """
    run_dir = Path(args.run_dir)
    source = run_dir / VERIFIED
    if not source.is_file():
        source = run_dir / ENUMERATED

    for cycle in range(1, MAX_ROLE_SWAPS + 1):
        graphs = list(read_stage(source))
        roles = {CoverPipeline.pending_role(g) for g in graphs}
        roles.discard(None)
        if not roles:
            return source
        if not roles <= {ModelRole.ENUMERATOR, ModelRole.VERIFIER}:
            raise SystemExit(f"unsupported pending model role(s): {sorted(r.value for r in roles)}")

        # One role at a time: staged execution exists so the two models need
        # never be co-resident.
        role = ModelRole.ENUMERATOR if ModelRole.ENUMERATOR in roles else ModelRole.VERIFIER
        waiting = sum(1 for g in graphs if CoverPipeline.pending_role(g) is role)
        print(f"\n[RESUME {cycle}] role={role.value}  queries_waiting={waiting}  dir={run_dir}")

        target = run_dir / RESUMED.format(cycle=cycle, role=role.value)
        phase = "enumerate" if role is ModelRole.ENUMERATOR else "verify"
        with RunTracer(run_dir / f"calls_resume_{cycle}.jsonl") as tracer:
            pipeline = build_pipeline(config, phase=phase, tracer=tracer)
            driver = pipeline.resume if role is ModelRole.ENUMERATOR else pipeline.verify
            with StageWriter(target) as writer:
                for graph in driver(iter(graphs), progress=True):
                    writer.write(graph)
        source = target
        print(json.dumps(stage_summary(source), indent=2))

    raise SystemExit(
        f"orchestration exceeded {MAX_ROLE_SWAPS} role swaps without settling; "
        "this is a control-loop bug, not a stopping condition"
    )


def phase_decide(args, config: dict) -> Path:
    run_dir = Path(args.run_dir) if args.run_dir else None
    if run_dir is None:
        raise SystemExit("--run-dir is required for the decide phase")
    source = Path(args.decide_source) if getattr(args, "decide_source", None) else None
    if source is None:
        source = run_dir / VERIFIED
        if not source.is_file():
            source = run_dir / ENUMERATED
            print(f"note: no verified stage found; deciding from {source.name}")

    split = args.split or (config.get("experiment") or {}).get("split", "val")
    dataset = load_dataset(split)

    print(f"\n[PHASE C] decide  dir={run_dir}  (no model loaded)")
    pipeline = build_pipeline(config, phase="decide", tracer=None)
    result = pipeline.decide(read_stage(source))

    queries = [Query(p.subject, p.relation, p.row_index) for p in result.predictions]
    predictions_path = write_predictions(
        result.predictions, run_dir / "predictions.jsonl", expected_queries=queries
    )
    write_trace(result.predictions, run_dir / "trace.jsonl")
    print(f"predictions : {predictions_path}")

    diagnostics = result.diagnostics()
    (run_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2))
    print(json.dumps(diagnostics, indent=2))

    if not dataset.is_blind:
        wanted = {(p.subject, p.relation) for p in result.predictions}
        gold = [r.to_official_row() for r in dataset.rows if (r.subject, r.relation) in wanted]
        report = evaluate_predictions([p.to_official_row() for p in result.predictions], gold)
        write_report(report, run_dir / "metrics.json")
        print()
        print(report.to_table())
    else:
        print("split is blind (no gold objects); skipping evaluation.")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["enumerate", "verify", "resolve", "decide", "all"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--split")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--relation", help="restrict the run to one relation")
    parser.add_argument("--run-dir")
    args = parser.parse_args()

    config = load_config(args.config)
    check_router_consistency()
    check_library_covers_contracts()

    if args.phase == "enumerate":
        phase_enumerate(args, config)
    elif args.phase == "verify":
        phase_verify(args, config)
    elif args.phase == "resolve":
        phase_resolve(args, config)
    elif args.phase == "decide":
        phase_decide(args, config)
    else:
        run_dir = phase_enumerate(args, config)
        args.run_dir = str(run_dir)
        phase_verify(args, config)
        # Keep swapping roles until the controller settles or the budget runs
        # out. Finalizing before this would abandon chosen actions.
        args.decide_source = str(phase_resolve(args, config))
        phase_decide(args, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
