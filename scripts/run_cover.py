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
from typing import Any, Mapping

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

import yaml

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.contracts.router import check_router_consistency
from cover_kbc.data.loader import BLIND_SPLITS, load_dataset
from cover_kbc.data.writer import write_predictions, write_trace
from cover_kbc.elicitation.library import check_library_covers_contracts
from cover_kbc.evaluation.harness import evaluate_predictions, write_report
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.registry import build_runtime, model_blocks
from cover_kbc.models.preflight import require_huggingface_runtime
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
    evaluate_test_readiness,
    evaluate_train_diagnostic_readiness,
    evaluate_validation_readiness,
)
from cover_kbc.diagnostics import DiagnosticRecorder, InferenceTelemetryWriter
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.leaderboard_repair import build_repair_stack
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
from cover_kbc.run_accounting import run_accounting, submission_verdict
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


#: Which leaderboard readiness gate governs which split, and the one state that
#: clears it.
#: A split absent from this table has no production path at all - stated as a
#: table rather than an if/else so adding one is a deliberate edit and
#: `--split test` cannot quietly inherit the validation gate.
PRODUCTION_GATES = {
    "val": (evaluate_validation_readiness, ReadinessState.FULL_VALIDATION_READY),
    "test": (evaluate_test_readiness, ReadinessState.FULL_TEST_READY),
}

#: TRAIN diagnostics deliberately do not live in ``PRODUCTION_GATES``. That
#: table is a leaderboard contract, and existing tests enforce that train does
#: not become a production submission path. V3A's labelled TRAIN run is the same
#: calibrated inference stack plus post-hoc telemetry, selected only when the
#: config explicitly enables diagnostics.
TRAIN_DIAGNOSTIC_GATE = (
    evaluate_train_diagnostic_readiness,
    ReadinessState.TRAIN_DIAGNOSTIC_READY,
)


def build_diagnostic_recorder(
    config: dict, *, run_id: str, split: str,
) -> "DiagnosticRecorder | None":
    """The V3A failure recorder, if this experiment asks for one.

    ``None`` - the default for every committed leaderboard config - is the
    pre-V3A path exactly. The recorder itself is gold-free and would be safe on
    any split; what is *not* safe is joining it to labels, and that is refused
    by :class:`~cover_kbc.diagnostics.TrainGoldAttribution` at analysis time
    rather than guessed at here.
    """
    block = dict(config.get("diagnostics") or {})
    if not block.get("enabled", False):
        return None
    return DiagnosticRecorder(run_id=run_id, split=split)


def diagnostic_telemetry_path(config: dict, *, out_dir: Path) -> "Path | None":
    """Where the opt-in V3A telemetry artifact is written."""
    block = dict(config.get("diagnostics") or {})
    if not block.get("enabled", False):
        return None
    raw = str(block.get("telemetry_file") or "")
    if not raw:
        raise SystemExit(
            "diagnostics.enabled is true but diagnostics.telemetry_file is not "
            "declared")
    path = Path(raw)
    return path if path.is_absolute() else out_dir / path


def _diagnostics_enabled(config: dict) -> bool:
    return bool((config.get("diagnostics") or {}).get("enabled", False))


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


def _recovery_identities(path: Path) -> set[tuple[str, str]]:
    """The identity set a recovery manifest names. Fails closed.

    Only identities are read. A manifest that carries predicted values is
    rejected outright: recovery must re-derive answers from inference, and a
    manifest is not allowed to become a channel for supplying them.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    identities = manifest.get("identities")
    if not isinstance(identities, list) or not identities:
        raise SystemExit(f"{path}: no identities to recover")
    wanted: set[tuple[str, str]] = set()
    for entry in identities:
        if "ObjectEntities" in entry:
            raise SystemExit(
                f"{path}: a recovery manifest must not carry predictions; "
                "recovered answers come from inference alone")
        key = (str(entry["SubjectEntity"]), str(entry["Relation"]))
        if key in wanted:
            raise SystemExit(f"{path}: duplicate identity {key}")
        wanted.add(key)
    return wanted


def _resolve_relation_filter(
    cli_relations: "list[str] | None", experiment: Mapping[str, Any], split: str,
) -> frozenset[str]:
    """Which relations this run is restricted to, or an empty set for all.

    CLI wins over config so a diagnostic profile can still be pointed at one
    relation ad hoc. Unknown relation names fail closed rather than silently
    selecting nothing, because "0 rows" and "you typo'd the relation" look
    identical in a run log otherwise.
    """
    declared = cli_relations if cli_relations else (
        experiment.get("relation_filter") or []
    )
    if isinstance(declared, str):
        declared = [declared]
    names = frozenset(str(name) for name in declared)
    if not names:
        return frozenset()
    unknown = sorted(names - set(CONTRACTS))
    if unknown:
        raise SystemExit(
            f"unknown relation(s) in relation filter: {unknown}; "
            f"known relations are {sorted(CONTRACTS)}"
        )
    if split in BLIND_SPLITS:
        raise SystemExit(
            f"relation filter refused on blind split {split!r}: a partial "
            "submission is not a submission, and the filter exists for TRAIN "
            "diagnostics only"
        )
    return names


def resolve_production_gate(config: dict, split: str, config_path: Path):
    """Which readiness gate governs a production-mode run of ``split``.

    Extracted so the pre-flight check, the tests and the runbook all interrogate
    **this** function rather than a copy of its rules. Audit 0080's first Colab
    attempt failed here after 28.7B parameters had already loaded, because
    nothing cheaper exercised it.

    Returns ``(gate, required_state)``, or ``(None, None)`` when the config does
    not declare production mode at all.

    Raises:
        SystemExit: when production mode is declared for a split that has no
            gate. TRAIN reaches a gate only through the opt-in V3A diagnostic
            path, which requires ``diagnostics.enabled``; a plain TRAIN
            production config is still refused, and TEST can never arrive here
            through the diagnostic branch because that branch tests
            ``split == "train"`` first.
    """
    if not _wants_production(config):
        return None, None
    if split == "train" and _diagnostics_enabled(config):
        gate, required = TRAIN_DIAGNOSTIC_GATE
    else:
        gate, required = PRODUCTION_GATES.get(split, (None, None))
    if gate is None:
        raise SystemExit(
            f"{config_path} declares production mode for split {split!r}; "
            f"a production leaderboard run is defined only for "
            f"{sorted(PRODUCTION_GATES)}. A TRAIN diagnostic must additionally "
            f"declare diagnostics.enabled and a telemetry_file, which is what "
            f"selects the {TRAIN_DIAGNOSTIC_GATE[1].value} gate.")
    return gate, required


def evaluate_production_readiness(config: dict, split: str, config_path: Path):
    """Resolve the gate and run it. ``None`` when the config is not production.

    Deliberately free of any model dependency, so it can run before weights are
    downloaded. ``scripts/run_cover.py`` calls it there, and the Colab runbook
    calls the same function as its pre-flight cell.
    """
    gate, required = resolve_production_gate(config, split, config_path)
    if gate is None:
        return None
    provenance = dict(config.get("calibration_provenance") or {})
    readiness = gate(
        config, base_dir=config_path.parent, split=split,
        expected_collection_repo_sha=provenance.get("collection_repo_sha"),
        expected_derivation_repo_sha=provenance.get("derivation_repo_sha"),
    )
    return readiness, required


def _allow_leaderboard_probe(config: dict, split: str, readiness) -> bool:
    """Explicit escape hatch for blind leaderboard probes under calibration review.

    This is intentionally narrower than production readiness.  It does not
    report FULL_TEST_READY and it is not inherited by ordinary aggressive
    configs.  The user may choose to spend a submission slot on a calibration
    review probe, but the source must say so.
    """
    if split != "test":
        return False
    probe = dict(config.get("leaderboard_probe") or {})
    if not probe.get("enabled", False):
        return False
    if str(probe.get("status", "")) != "CALIBRATION_REVIEW_LEADERBOARD_PROBE":
        return False
    accepted = probe.get("accepts_readiness_blockers") or []
    if "selection.v3_1: CALIBRATION_REVIEW_REQUIRED" not in accepted:
        return False
    blockers = getattr(readiness, "blockers", ())
    if not any("CALIBRATION_REVIEW_REQUIRED" in blocker for blocker in blockers):
        return False

    enumerator, verifier = model_blocks(config)
    mistral_id = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    mistral_revision = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
    qwen_id = "Qwen/Qwen3.5-4B"
    qwen_revision = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
    frozen_dual_model_probe = (
        enumerator.get("model_id") == mistral_id
        and enumerator.get("revision") == mistral_revision
        and verifier.get("model_id") == qwen_id
        and verifier.get("revision") == qwen_revision
    )
    if frozen_dual_model_probe:
        return True

    role_swap_probe = (
        probe.get("role_swap") == "MISTRAL_ONLY_VERIFIER"
        and enumerator.get("model_id") == mistral_id
        and enumerator.get("revision") == mistral_revision
        and verifier.get("model_id") == mistral_id
        and verifier.get("revision") == mistral_revision
        and int((config.get("budget_assertion") or {}).get(
            "total_published_parameters", 0) or 0) == 24_011_361_280
    )
    if not role_swap_probe:
        return False

    accepted_role_swap_blockers = (
        "selection.v3_1: CALIBRATION_REVIEW_REQUIRED",
        (
            "V3 model profile: verifier model_id is "
            "'mistralai/Mistral-Small-3.2-24B-Instruct-2506', "
            "expected 'Qwen/Qwen3.5-4B'"
        ),
        (
            "V3 model profile: verifier revision is "
            "'95a6d26c4bfb886c58daf9d3f7332c857cb27b43', "
            "expected '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a'"
        ),
        (
            "V3 model budget: 24011361280 / 32000000000, "
            "expected 28671226368 / 32000000000"
        ),
    )
    return all(
        any(str(blocker).startswith(accepted) for accepted in accepted_role_swap_blockers)
        for blocker in blockers
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="experiment YAML")
    parser.add_argument("--split", help="override the split named in the config")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N queries")
    parser.add_argument(
        "--recovery-manifest",
        type=Path,
        default=None,
        help=(
            "re-run exactly the identities listed in a recovery manifest "
            "(scripts/build_test_recovery_manifest.py). Every listed query runs "
            "from the start; no partial state is resumed."
        ),
    )
    parser.add_argument(
        "--relation",
        action="append",
        default=None,
        help=(
            "run only rows of this relation; repeatable. Overrides "
            "experiment.relation_filter in the config. Used by the audit-0077 "
            "targeted Class-B diagnostics so one relation can be measured "
            "without paying for a full split."
        ),
    )
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

    # Can this machine actually load the declared checkpoints? This catches
    # family-specific Hugging Face support requirements before any weight fetch.
    # A stub-backed profile needs no transformers and is not asked for one.
    try:
        require_huggingface_runtime(enumerator_cfg, verifier_cfg)
    except RuntimeError as error:
        raise SystemExit(f"runtime preflight: {error}") from error

    split = args.split or experiment.get("split", "val")
    dataset = load_dataset(split)
    queries = dataset.queries()

    # Relation filter (audit 0077). Deterministic and order-preserving: it is a
    # membership test on the query's own relation name and reads nothing else -
    # in particular no gold value, so a filtered run cannot be a disguised
    # lookup. Applied before --limit so "--relation X --limit 5" means the
    # first five rows *of X*.
    if args.recovery_manifest:
        wanted = _recovery_identities(args.recovery_manifest)
        queries = [q for q in queries if (q.subject, q.relation) in wanted]
        missing = wanted - {(q.subject, q.relation) for q in queries}
        if missing:
            raise SystemExit(
                f"recovery manifest names {len(missing)} identity(ies) absent from "
                f"split {split!r}: {sorted(missing)[:5]}")
        if len(queries) != len(wanted):
            raise SystemExit(
                f"recovery manifest matched {len(queries)} rows for {len(wanted)} "
                "identities; the split contains duplicates")
        print(f"recovery manifest: re-running {len(queries)} identity(ies) from the start")

    relation_filter = _resolve_relation_filter(args.relation, experiment, split)
    if relation_filter:
        queries = [q for q in queries if q.relation in relation_filter]
        if not queries:
            raise SystemExit(
                f"relation filter {sorted(relation_filter)} matched no rows in "
                f"split {split!r}"
            )
        print(f"relation filter {sorted(relation_filter)}: {len(queries)} rows")
    if args.limit:
        queries = queries[: args.limit]

    # ---- production activation, BEFORE any weights load ------------------
    # A config whose Layer-6 modules declare production mode gets the real
    # calibrated path, and gets it only if the readiness gate says so. Evaluated
    # here rather than after `build_runtime` because audit 0080's first Colab
    # attempt spent minutes downloading 28.7B parameters and only then
    # discovered that a TRAIN diagnostic had no gate. A config guard that fires
    # after the expensive step is a guard that costs what it was meant to save.
    production = _wants_production(config)
    if production:
        readiness, required = evaluate_production_readiness(config, split, args.config)
        if readiness.state is not required:
            if _allow_leaderboard_probe(config, split, readiness):
                print(
                    f"{split} readiness: LEADERBOARD_PROBE_ALLOWED "
                    f"(not {required.value}; {readiness.state.value})"
                )
                for blocker in readiness.blockers:
                    print(f"  CALIBRATION-REVIEW BLOCKER ACCEPTED: {blocker}")
            else:
                print(f"{split} readiness: REFUSED")
                for blocker in readiness.blockers:
                    print(f"  - {blocker}")
                raise SystemExit(
                    f"{args.config} declares production mode but is not "
                    f"{required.value} ({readiness.state.value})")
        print(f"readiness   : {readiness.state.value}")

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

    calibration = None
    if production:
        provenance = dict(config.get("calibration_provenance") or {})
        calibration = load_production_calibration(
            config, base_dir=args.config.parent,
            expected_collection_repo_sha=provenance.get("collection_repo_sha"),
            expected_derivation_repo_sha=provenance.get("derivation_repo_sha"),
        )
        print(f"calibration : {len(calibration.budgets)} relation budget(s), "
              f"{len(calibration.history.bins)} bin(s), "
              f"tau={calibration.planner.tau_continue}")

    run_id = new_run_id(experiment.get("name", "cover"), split)
    out_dir = args.output_dir or (OUTPUTS_DIR / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    recorder = build_diagnostic_recorder(config, run_id=run_id, split=split)
    telemetry_path = diagnostic_telemetry_path(config, out_dir=out_dir)

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
    if verifier_runtime is not runtime:
        manifest.add_model(verifier_runtime.spec)
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
    if telemetry_path is not None:
        print(f"telemetry   : {telemetry_path}")

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
            # V3A, observability only: consulted after each query is decided
            # and incapable of changing one. Absent from every leaderboard
            # config, so those runs take the pre-V3A path unchanged.
            diagnostics=recorder,
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

        repair_stack = build_repair_stack(
            config.get("leaderboard_repair"),
            enumerator=runtime,
            verifier=verifier_runtime,
        )
        repair_result = None
        if repair_stack is not None:
            repair_graphs = pipeline.v3_pre_m8_results or pipeline.v3_core_results
            repair_result = repair_stack.apply(
                result.predictions,
                queries=queries,
                hypothesis_graphs=repair_graphs,
            )
            result.predictions = list(repair_result.predictions)
            print(
                "leaderboard repair: "
                f"{repair_result.accounting['changed_rows']} changed row(s), "
                f"{repair_result.accounting['total_repair_calls']} post-call(s), "
                f"profile={repair_result.accounting['profile']}"
            )

    manifest.finish()
    manifest.total_calls = result.total_calls
    manifest.total_generated_tokens = result.total_generated_tokens
    manifest.total_prompt_tokens = result.total_prompt_tokens

    predictions_path = write_predictions(
        result.predictions, out_dir / "predictions.jsonl", expected_queries=queries
    )
    write_trace(result.predictions, out_dir / "trace.jsonl")
    print(f"\npredictions : {predictions_path}")

    if recorder is not None:
        if len(recorder.records) != len(result.predictions):
            raise SystemExit(
                "diagnostic recorder produced "
                f"{len(recorder.records)} row(s) for "
                f"{len(result.predictions)} prediction(s); refusing to write "
                "ambiguous V3A telemetry")
        assert telemetry_path is not None
        InferenceTelemetryWriter(telemetry_path).write_all(recorder.records)
        print(f"telemetry   : {telemetry_path} ({len(recorder.records)} queries)")

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
        ("V3", "v3_hypothesis_graph.jsonl", pipeline.v3_core_results),
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

    if repair_result is not None and repair_stack is not None:
        records_path, accounting_path = repair_stack.write_artifacts(repair_result, out_dir)
        print(f"[L7-L9] {records_path}  ({len(repair_result.records)} queries)")
        print(f"[L7-L9] {accounting_path}")

    if result.errors:
        (out_dir / "errors.json").write_text(json.dumps(result.errors, indent=2))
        print(f"errors      : {len(result.errors)} (see errors.json)")

    # Written as its own artifact rather than folded into the manifest, whose
    # `notes` field is free text: a submission gate must read a typed record.
    accounting = run_accounting(queries, result)
    (out_dir / "run_accounting.json").write_text(
        json.dumps(accounting, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"accounting  : {accounting['successful_queries']}/{accounting['total_queries']} "
        f"succeeded, {accounting['failed_queries']} failed "
        f"({accounting['unresolved_invariant_errors']} unresolved invariant)"
    )

    # `test` never scores, and the reason is named rather than left to follow
    # from the split happening to be blind: the official test answers are not
    # in this repository, so a metrics number for them could only come from
    # gold that leaked. The evaluator is not called and no metrics.json exists.
    scoreable = split not in BLIND_SPLITS and not dataset.is_blind
    if not args.no_eval and scoreable:
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
    elif not scoreable:
        print(f"\nsplit {split!r} is blind; no evaluation and no metrics.json.")

    manifest.write(out_dir / "manifest.json")
    print(f"\nmanifest    : {out_dir / 'manifest.json'}")

    # Submission readiness is decided here, from the accounting, and it is the
    # process exit code. Audit 0078: the frozen TEST run wrote 475 rows with 39
    # orchestration-failure fallback empties and still returned 0, so nothing
    # downstream could tell a finished submission from a broken one.
    verdict = submission_verdict(split, accounting)
    print(f"submission  : {verdict['state']}")
    for blocker in verdict["blockers"]:
        print(f"  BLOCKER: {blocker}", file=sys.stderr)
    if not verdict["ready"]:
        # Non-blind splits keep their previous exit behaviour: a diagnostic or
        # collection workflow is allowed to contain per-row failures and study
        # them. Only a blind submission run is refused.
        if split in BLIND_SPLITS:
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
