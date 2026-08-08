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
import sys
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
from cover_kbc.control.layer6_integration import Layer6Integrator
from cover_kbc.control.micro_planner import build_micro_planner
from cover_kbc.control.relation_budget import build_relation_budget_scheduler
from cover_kbc.controller_calibration.production import (
    load_production_calibration,
)
from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_validation_readiness,
)
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.coverage_gap.missingness import build_coverage_gap_estimator
from cover_kbc.evidence.layer4 import build_layer4_integrator
from cover_kbc.verification.bidirectional_verifier import build_bidirectional_verifier
from cover_kbc.verification.specialist_verifier import build_specialist_verifier
from cover_kbc.pipeline import (
    AccountingInvariantError,
    CoverPipeline,
    ExecutionMode,
    PipelineConfig,
)
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


def resolve_execution_mode(config: dict) -> ExecutionMode:
    """The execution mode this experiment declares. **Fails closed.**

    The calibration was measured under one execution mode, and a production run
    under another is a run of a different system - so the config declares it,
    this resolves it, and an unrecognised value stops the run rather than
    quietly picking a default.

    Args:
        config: the loaded experiment mapping.

    Returns:
        The declared :class:`ExecutionMode`, defaulting to ``interleaved`` only
        when the config names none at all.

    Raises:
        SystemExit: on a value that is not a supported execution mode.
    """
    declared = (config.get("pipeline") or {}).get("mode")
    if declared is None:
        return ExecutionMode.INTERLEAVED
    try:
        return ExecutionMode(str(declared))
    except ValueError:
        supported = ", ".join(sorted(mode.value for mode in ExecutionMode))
        raise SystemExit(
            f"pipeline.mode {declared!r} is not a supported execution mode; "
            f"this build implements {supported}"
        ) from None


def _wants_production(config: dict) -> bool:
    """Whether this experiment declares the calibrated production path.

    Read from the two Layer-6 modules rather than from a separate switch: a
    config in which Module 20 and Module 21 are in production mode *is* a
    production config, and a second flag could disagree with them.
    """
    return all(
        str((config.get(block) or {}).get("mode", "")) == "production"
        for block in ("relation_budget_scheduler", "micro_planner")
    )


#: Written instead of a manifest when physical accounting breaks. Named so it
#: cannot be mistaken for one, and deliberately not `manifest.json`,
#: `predictions.jsonl` or anything a submission or completion contract reads.
ACCOUNTING_FAILURE_MARKER = "FAILED_ACCOUNTING_INVARIANT.json"


def _exception_chain(error: BaseException) -> list[dict[str, str]]:
    """Every failure in the chain, outermost first.

    A settlement overrun can sit on top of an ordinary action failure: the
    action threw *after* spending, then the settlement of what it spent was
    itself impossible. Both matter, and only the outermost one appears in a
    one-line error string, so the whole chain is recorded.
    """
    chain: list[dict[str, str]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append({"type": type(current).__name__, "message": str(current)})
        current = current.__cause__ or current.__context__
    return chain


def _abort_on_accounting_invariant(
    out_dir: Path, run_id: str, split: str, expected: int,
    error: AccountingInvariantError,
) -> None:
    """Stop the run. **Never returns.**

    Reached when Module 20's ledger refused a settlement because the runtimes
    spent more than the precharge held. Nothing below the call site may run: a
    manifest, a predictions file or a metrics report written after this point
    would describe a run whose recorded cost is not the cost it incurred, and a
    478-row predictions file missing its failed rows is exactly the artifact
    that must not exist.

    A single diagnostic marker is written so the failure is inspectable. It is
    not a checkpoint and nothing resumes from it - this entry point has no
    resume path - and its name and ``"status": "aborted"`` make it unusable as
    a completion record.

    Raises:
        SystemExit: always, with a non-zero status.
    """
    marker = {
        "status": "aborted",
        "reason": "accounting_invariant",
        "run_id": run_id,
        "split": split,
        "expected_queries": expected,
        "complete": False,
        "submittable": False,
        "predictions_written": False,
        "manifest_written": False,
        "detail": (
            "Physical accounting stopped being representable by the precharged "
            "Module 20 envelope: a neural call happened outside the precharge, "
            "so the ledger refused the settlement and the reservation could not "
            "be closed. The run was stopped; later queries were not attempted. "
            "This file is a diagnostic record of a FAILED run and is not a "
            "manifest, a submission, or a resumable checkpoint."),
        "failures": _exception_chain(error),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ACCOUNTING_FAILURE_MARKER).write_text(
        json.dumps(marker, indent=2), encoding="utf-8")
    print("\nRUN ABORTED - ACCOUNTING INVARIANT", file=sys.stderr)
    for entry in marker["failures"]:
        print(f"  {entry['type']}: {entry['message']}", file=sys.stderr)
    print(f"  no predictions and no manifest were written for {run_id}",
          file=sys.stderr)
    print(f"  diagnostic: {out_dir / ACCOUNTING_FAILURE_MARKER}", file=sys.stderr)
    raise SystemExit(2) from error


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

    # Resolved here, with the other cheap fail-closed config checks, because
    # everything below this line is expensive: `build_runtime` loads real
    # weights. An unsupported execution mode must cost nothing to discover.
    execution_mode = resolve_execution_mode(config)

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

    # ---- production activation -------------------------------------------
    # A config whose Layer-6 modules declare production mode gets the real
    # calibrated path, and gets it only if the readiness gate says so. The gate
    # loads the three artifacts through their canonical owners, so a missing,
    # synthetic, mis-hashed or provenance-mismatched calibration stops the run
    # here rather than at row 1 of 478.
    production = _wants_production(config)
    calibration = None
    if production:
        provenance = dict(config.get("calibration_provenance") or {})
        readiness = evaluate_validation_readiness(
            config, base_dir=args.config.parent, split=split,
            expected_collection_repo_sha=provenance.get("collection_repo_sha"),
            expected_derivation_repo_sha=provenance.get("derivation_repo_sha"),
        )
        if readiness.state is not ReadinessState.FULL_VALIDATION_READY:
            print("validation readiness: REFUSED")
            for blocker in readiness.blockers:
                print(f"  - {blocker}")
            raise SystemExit(
                f"{args.config} declares production mode but is not "
                f"FULL_VALIDATION_READY ({readiness.state.value})")
        calibration = load_production_calibration(
            config, base_dir=args.config.parent,
            expected_collection_repo_sha=provenance.get("collection_repo_sha"),
            expected_derivation_repo_sha=provenance.get("derivation_repo_sha"),
        )
        print(f"readiness   : {readiness.state.value}")
        print(f"calibration : {len(calibration.budgets)} relation budget(s), "
              f"{len(calibration.history.bins)} bin(s), "
              f"tau={calibration.planner.tau_continue}")

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
    if calibration is not None:
        # Which calibration answered these rows is part of the run's identity,
        # not a detail: two runs under different artifacts are different runs.
        manifest.notes = (manifest.notes + " | " if manifest.notes else "") + (
            f"production calibration {calibration.provenance.get('derivation_repo_sha', '')[:12]}")
    manifest.start()

    print(f"run_id      : {run_id}")
    print(f"split       : {split} ({len(queries)} queries of {len(dataset)})")
    print(f"model       : {runtime.spec.model_id}")
    print(f"execution   : {execution_mode.value} (from config)")
    print(f"config hash : {manifest.config_hash}")
    print(f"outputs     : {out_dir}")

    with RunTracer(out_dir / "calls.jsonl") as tracer:
        # The canonical config path, so this runner cannot drift from
        # `run_staged.py` on any factual setting. The execution mode is the
        # config's to declare and is resolved above, not overridden here: a
        # runner that silently ran a mode the experiment did not ask for makes
        # the config a comment (Audit 0051).
        config_block = dict(pipeline_cfg)
        config_block["mode"] = execution_mode.value
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
        planner = build_micro_planner(
            config.get("micro_planner"),
            calibration.history if calibration else None,
            calibration.planner if calibration else None,
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
            # Module 18, shadow: the catalogue costs nothing and no check runs
            # without an explicit request.
            bidirectional_verifier=build_bidirectional_verifier(
                config.get("bidirectional_verification"),
                consensus_enabled=bool(
                    (config.get("consensus") or {}).get("enabled", False)
                ),
            ) if (config.get("consensus") or {}).get("enabled", False) else None,
            # Layer-4 boundary integration, shadow and non-neural.
            layer4_integrator=build_layer4_integrator(
                config.get("layer4_integration"),
                consensus_enabled=bool(
                    (config.get("consensus") or {}).get("enabled", False)
                ),
            ) if (config.get("consensus") or {}).get("enabled", False) else None,
            # Module 19, shadow and non-neural.
            coverage_gap_estimator=build_coverage_gap_estimator(
                config.get("coverage_gap"),
                layer4_enabled=bool(
                    (config.get("layer4_integration") or {}).get("enabled", False)
                ),
            ) if (config.get("consensus") or {}).get("enabled", False) else None,
            # Module 20. In production it holds real reservations against
            # the TRAIN-derived envelope; in shadow it plans and governs
            # nothing. The calibration comes from the loader above, never from
            # a default.
            relation_budget_scheduler=build_relation_budget_scheduler(
                config.get("relation_budget_scheduler"),
                calibration.budgets if calibration else None,
            ),
            # Module 21. §17's selector, built on the real historical bins and
            # the real coefficients.
            micro_planner=planner,
            # Layer 6. Without it Module 21 is handed an empty legal-action
            # list and can only answer STOP/NO_LEGAL_ACTION, which is not a
            # decision - it is the absence of one (F-22).
            layer6_integrator=Layer6Integrator(planner) if planner else None,
            # The mode is what actually turns the upgraded path on: it is what
            # lets the production bridge mutate evidence and what routes action
            # choice to Module 21 (F-24).
            integration_mode=(IntegrationMode.PRODUCTION if production
                              else IntegrationMode.SHADOW),
        )
        try:
            result = pipeline.run(queries, progress=True)
        except AccountingInvariantError as error:
            # Fail-stop, and stopped *here* so that every artifact below - the
            # manifest, predictions, the trace, the module records and the
            # metrics report - is unreachable. A run whose accounting broke has
            # no completion record to write.
            _abort_on_accounting_invariant(
                out_dir, run_id, split, len(queries), error)

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
        ("M18", "bidirectional_verification.jsonl", pipeline.bidirectional_results),
        ("L4", "layer4_evidence.jsonl", pipeline.layer4_results),
        ("M19", "coverage_gap.jsonl", pipeline.coverage_gap_results),
        ("M20", "relation_budget.jsonl", pipeline.relation_budget_results),
        ("M21", "micro_planner.jsonl", pipeline.micro_planner_results),
        ("L6", "layer6_control.jsonl", pipeline.layer6_results),
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
