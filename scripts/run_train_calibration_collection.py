#!/usr/bin/env python3
"""Run TRAIN calibration collection over the frozen models.

This gathers the action outcomes Modules 20 and 21 will later be calibrated
from. It is **not** a leaderboard run and never becomes one: the mode is
``TRAIN_CALIBRATION_COLLECTION_ONLY``, action choice comes from the fixed
``TrainCollectionPolicy`` rather than from utility, and the readiness gate
refuses anything but a TRAIN profile before a single weight is loaded.

The run is long and expensive, so durability is designed in rather than hoped
for: telemetry, predictions and the checkpoint are flushed after every row, and
a fatal error - CUDA OOM included - preserves everything completed before
exiting non-zero. **No quality is ever reduced to fit memory.** Model,
quantization, prompts, views, specialist coverage and verifier readings are
whatever the config declares, in success and in failure alike.

Nothing here measures anything itself. The control state either side of an
action, its cost, its candidate effect and its identity are all captured by the
canonical execution seam at the moment they are true, and this file transcribes
them. Audit 0041 found the previous version rebuilding that state after the row
had finished, which made every ``ΔR`` zero by construction; state is never
reconstructed here now.

TRAIN gold never reaches inference. The runner reads ``SubjectEntity`` and
``Relation`` to build a query and nothing else; ``ObjectEntities`` is joined
offline, in a later milestone, by the derivation step.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

import yaml

from cover_kbc.contracts.router import check_router_consistency
from cover_kbc.control.micro_planner import build_micro_planner
from cover_kbc.control.relation_budget import build_relation_budget_scheduler
from cover_kbc.controller_calibration.checkpoint import (
    CollectionCheckpoint,
    ResumeRefused,
    RunIdentity,
    TelemetryCommitBoundary,
    resume_from,
)
from cover_kbc.controller_calibration.collection_policy import (
    COLLECTION_POLICY_VERSION,
    DEFAULT_FAMILY_TARGET,
    CoverageLedger,
    CollectionPolicyError,
    TrainCollectionPolicy,
    required_families,
)
from cover_kbc.controller_calibration.progress import (
    RunCounters,
    query_line,
    round_line,
    summary_block,
)
from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_collection_readiness,
    evaluate_v3_core_readiness,
)
from cover_kbc.controller_calibration.recovery import (
    capture_telemetry_commit_boundary,
    reconcile_to_checkpoint,
    validate_committed_telemetry_prefix,
)
from cover_kbc.controller_calibration.sufficiency import evaluate_sufficiency
from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionOutcome,
    ActionTelemetryRecord,
    RedundancyStatus,
    TelemetryError,
    TelemetryWriter,
    read_telemetry,
)
from cover_kbc.coverage_gap.missingness import build_coverage_gap_estimator
from cover_kbc.data.loader import load_dataset
from cover_kbc.diagnostics import DiagnosticRecorder, InferenceTelemetryWriter
from cover_kbc.elicitation.library import check_library_covers_contracts
from cover_kbc.evidence.consensus import build_consensus_engine
from cover_kbc.evidence.layer4 import build_layer4_integrator
from cover_kbc.evidence.production_bridge import ProductionBridgeError
from cover_kbc.integration_mode import (
    CALIBRATION_SPLIT,
    IntegrationMode,
    require_split,
)
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.registry import build_runtime, model_blocks
from cover_kbc.pipeline import (
    AccountingInvariantError,
    CoverPipeline,
    ExecutionMode,
    PipelineConfig,
    program_type_value,
)
from cover_kbc.query_intelligence import (
    build_parametric_retriever,
    build_profiler,
    build_prompt_compiler,
)
from cover_kbc.runtime.manifest import new_run_id
from cover_kbc.specialists import (
    build_large_set_specialist,
    build_null_temporal_specialist,
    build_numeric_specialist,
    build_small_set_specialist,
)
from cover_kbc.verification.bidirectional_verifier import build_bidirectional_verifier
from cover_kbc.verification.specialist_verifier import build_specialist_verifier
from cover_kbc.v3_core.relation_programs import (
    V3ActionFamily,
    relation_train_collection_actions,
)

#: Rows the official TRAIN split must contain. A different number means the
#: benchmark snapshot moved, and calibrating against it would be calibrating
#: against a different dataset than the one audited.
EXPECTED_TRAIN_ROWS = 477

#: How often the cumulative summary is printed.
SUMMARY_EVERY = 20

#: The catalogue kinds this collection executes actions from. Also the source of
#: the action-family vocabulary the coverage gate is declared against.
COLLECTED_CATALOGUES = ("v3",)
LEGACY_COLLECTED_CATALOGUES = ("m17", "m18")

#: Failures that make the *process* untrustworthy rather than the row. Anything
#: here aborts and preserves what was committed; everything else is contained as
#: a row failure so one malformed subject cannot destroy 477 rows of inference.
#:
#: The boundary is drawn at "would the next row still be measured correctly?".
#: An accounting invariant break or a corrupted bridge means no, so they abort.
#: A parse failure or a model-specific error on one subject means yes.
FATAL_ERRORS = (
    MemoryError,
    OSError,                    # disk full / IO: durability itself is at risk
    AccountingInvariantError,   # every later cost estimate would inherit it
    TelemetryError,             # the record stream can no longer be trusted
    ProductionBridgeError,      # evidence was applied to the wrong query
)


class CollectionError(RuntimeError):
    """The collection run could not be started or trusted."""


def is_fatal(error: BaseException) -> bool:
    """Whether ``error`` must abort the run rather than fail one row.

    ``BaseException`` that is not an ``Exception`` - ``KeyboardInterrupt``,
    ``SystemExit`` - is always fatal: it is an instruction, not a data problem.
    CUDA OOM is matched by name because the exception type lives in torch, which
    this module must not import to make a decision about a run that may have no
    GPU at all.
    """
    if not isinstance(error, Exception):
        return True
    if isinstance(error, FATAL_ERRORS):
        return True
    name = type(error).__name__
    return "OutOfMemoryError" in name or "CUDA out of memory" in str(error)


def _repo_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _config_sha(payload: dict) -> str:
    import hashlib
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _v3_enabled(config: dict) -> bool:
    pipeline = dict(config.get("pipeline") or {})
    v3 = dict(pipeline.get("v3_core") or {})
    return bool(v3.get("enabled", False))


def _required_v3_families() -> tuple[str, ...]:
    by_relation = {
        relation: relation_train_collection_actions(relation)
        for relation in sorted(_collection_relations())
    }
    families = {
        family.value
        for actions in by_relation.values()
        for family in actions
        if family is not V3ActionFamily.STOP
    }
    return tuple(sorted(families))


def _coverage_target(config: dict) -> int:
    block = dict(config.get("train_collection") or {})
    return int(block.get("coverage_target_per_family", DEFAULT_FAMILY_TARGET))


def _write_coverage_csv(path: Path, coverage: CoverageLedger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        "relation",
        "action_family",
        "legal_opportunities",
        "executed",
        "successful",
        "failed",
        "target",
        "coverage_ratio",
        "status",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in coverage.csv_rows():
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def _write_coverage_artifacts(
    out_dir: Path, coverage: CoverageLedger, *, v3_enabled: bool,
) -> dict[str, str]:
    payload = json.dumps(coverage.to_json(), indent=2)
    action_path = out_dir / "action_coverage.json"
    action_path.write_text(payload, encoding="utf-8")
    written = {"action_coverage": str(action_path)}
    if v3_enabled:
        v3_json = out_dir / "v3_action_coverage.json"
        v3_csv = out_dir / "v3_action_coverage.csv"
        v3_json.write_text(payload, encoding="utf-8")
        _write_coverage_csv(v3_csv, coverage)
        written["v3_action_coverage"] = str(v3_json)
        written["v3_action_coverage_csv"] = str(v3_csv)
    return written


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            payload = row.to_json() if hasattr(row, "to_json") else row
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _check_output_root_writable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".train_collection_write_check"
    try:
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise CollectionError(
            f"output directory {path} is not writable: {error}"
        ) from error


def _v3_executable_owner_families() -> dict[str, tuple[str, ...]]:
    return {
        V3ActionFamily.MULTI_VIEW_RECALL.value: ("M12", "M13", "M18"),
        V3ActionFamily.INDEPENDENT_RECALL.value: ("M12", "M14", "M18"),
        V3ActionFamily.DEFINITION_RECALL.value: ("M12",),
        V3ActionFamily.ALTERNATIVE_RECALL.value: ("M12", "M13"),
        V3ActionFamily.ATTRIBUTE_DECOMPOSITION.value: ("M14",),
        V3ActionFamily.SET_EXPANSION.value: ("M13",),
        V3ActionFamily.LISTING_ELIMINATION.value: ("M17",),
        V3ActionFamily.UNARY_VERIFY.value: ("M17",),
        V3ActionFamily.SEMANTIC_VERIFY.value: ("M17",),
        V3ActionFamily.CONTRAST_VERIFY.value: ("M17",),
    }


def _run_precheck(
    *, config: dict, config_path: Path, output_dir: Path, split: str,
) -> int:
    """Cheap source/scripted readiness check. Loads no model weights."""
    _check_output_root_writable(output_dir)
    readiness = evaluate_v3_core_readiness(
        config, base_dir=config_path.parent, split=split)
    blockers = list(readiness.blockers)
    dataset = load_dataset(CALIBRATION_SPLIT)
    if len(dataset) != EXPECTED_TRAIN_ROWS:
        blockers.append(
            f"TRAIN has {len(dataset)} rows, expected {EXPECTED_TRAIN_ROWS}")

    expected = set(_required_v3_families())
    owners = _v3_executable_owner_families()
    missing_owners = sorted(expected - set(owners))
    if missing_owners:
        blockers.append(
            f"V3 executable owner registry missing {missing_owners}")

    target = _coverage_target(config)
    try:
        policy = TrainCollectionPolicy(family_target=target)
        policy.note_families(expected)
    except CollectionPolicyError as error:
        blockers.append(f"coverage target configuration invalid: {error}")
    else:
        recognized = set(policy.coverage.families)
        missing = sorted(expected - recognized)
        if missing:
            blockers.append(f"coverage scheduler does not recognize {missing}")

    if readiness.details.get("v3_production_calibration") != "NOT_READY":
        blockers.append("V3 production readiness is not fail-closed")
    test_readiness = evaluate_v3_core_readiness(
        config, base_dir=config_path.parent, split="test")
    if test_readiness.state is not ReadinessState.NOT_READY:
        blockers.append("V3 TEST gate is not blocked")

    print("V3 TRAIN collection precheck")
    print(f"  policy: {COLLECTION_POLICY_VERSION}")
    print(f"  coverage target: {target}")
    print(f"  output root: {output_dir}")
    print(f"  required families: {', '.join(sorted(expected))}")
    if blockers:
        print("  status: FAIL")
        for blocker in blockers:
            print(f"  BLOCKER {blocker}")
        return 1
    print("  status: PASS")
    print("  model calls: 0")
    return 0


def build_pipeline(config: dict, selector):
    """Construct the upgraded pipeline exactly as the canonical runner does."""
    enumerator_cfg, verifier_cfg = model_blocks(config)
    check_router_consistency()
    check_library_covers_contracts()

    runtime = build_runtime(enumerator_cfg)
    verifier_runtime = (
        runtime if verifier_cfg == enumerator_cfg else build_runtime(verifier_cfg))
    specs = [runtime.spec]
    if verifier_runtime is not runtime:
        specs.append(verifier_runtime.spec)
    audit = audit_parameter_budget(specs)
    if not audit.passed:
        print(audit.summary())
        raise CollectionError("parameter budget audit failed")

    block = dict(config.get("pipeline", {}))
    block["mode"] = ExecutionMode.INTERLEAVED.value
    pipeline_config = PipelineConfig.from_mapping(block)
    pipeline_config.enumerator_model_id = enumerator_cfg.get("model_id", "")
    pipeline_config.verifier_model_id = verifier_cfg.get("model_id", "")

    profiler = build_profiler(config.get("query_intelligence"))
    compiler = build_prompt_compiler(
        config.get("query_intelligence"), profiler_enabled=profiler is not None)
    retriever = build_parametric_retriever(
        config.get("query_intelligence"), profiler_enabled=profiler is not None,
        compiler_enabled=compiler is not None)
    kw = dict(profiler_enabled=profiler is not None,
              compiler_enabled=compiler is not None,
              retrieval_enabled=retriever is not None)
    specialists = config.get("specialists")
    numeric = build_numeric_specialist(specialists, **kw)
    large_set = build_large_set_specialist(specialists, **kw)
    null_temporal = build_null_temporal_specialist(specialists, **kw)
    small_set = build_small_set_specialist(specialists, **kw)
    # Module 16 fuses the *owning* specialist's evidence for each relation in
    # play, so it is told which specialists exist and which relations will be
    # asked. Leaving both out made the dependency check vacuous, and the run
    # then failed one row at a time inside `_specialist_result_for`.
    consensus = build_consensus_engine(
        config.get("consensus"), **kw,
        available_specialists={
            "M12": numeric is not None, "M13": large_set is not None,
            "M14": null_temporal is not None, "M15": small_set is not None,
        },
        relations=tuple(sorted(_collection_relations())),
    )
    diagnostics_block = dict(config.get("diagnostics") or {})
    recorder = (
        DiagnosticRecorder(
            run_id="",
            split=str((config.get("experiment") or {}).get("split", "")),
        )
        if diagnostics_block.get("enabled", False) else None
    )

    pipeline = CoverPipeline(
        runtime, pipeline_config, verifier_runtime=verifier_runtime,
        profiler=profiler, prompt_compiler=compiler, retriever=retriever,
        numeric_specialist=numeric,
        large_set_specialist=large_set,
        null_temporal_specialist=null_temporal,
        small_set_specialist=small_set,
        consensus_engine=consensus,
        specialist_verifier=build_specialist_verifier(
            config.get("specialist_verifier"),
            consensus_enabled=consensus is not None,
            verifier_available=verifier_runtime is not None,
        ) if consensus is not None else None,
        bidirectional_verifier=build_bidirectional_verifier(
            config.get("bidirectional_verification"),
            consensus_enabled=consensus is not None,
        ) if consensus is not None else None,
        layer4_integrator=build_layer4_integrator(
            config.get("layer4_integration"),
            consensus_enabled=consensus is not None,
        ) if consensus is not None else None,
        coverage_gap_estimator=build_coverage_gap_estimator(
            config.get("coverage_gap"),
            layer4_enabled=bool(
                (config.get("layer4_integration") or {}).get("enabled", False)),
        ) if consensus is not None else None,
        relation_budget_scheduler=build_relation_budget_scheduler(
            config.get("relation_budget_scheduler")),
        micro_planner=build_micro_planner(config.get("micro_planner")),
        integration_mode=IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY,
        action_selector=selector,
        diagnostics=recorder,
    )
    return pipeline, runtime, verifier_runtime


def _collection_relations() -> set[str]:
    from cover_kbc.contracts.registry import CONTRACTS

    return set(CONTRACTS)


def _telemetry_for(
    record: dict, *, run_id: str, query, program_type: str,
) -> ActionTelemetryRecord:
    """Transcribe one seam record. **Measures nothing, defaults nothing.**

    Every field below already exists on the record the execution seam produced;
    this only renames them into the versioned schema. Anything the seam could
    not observe stays absent rather than becoming a zero.
    """
    cost = record["cost"]
    projection = record.get("projection")
    effect = record.get("effect") or {}
    descriptor = getattr(projection, "budget_descriptor", None)
    purpose = getattr(descriptor, "special_purpose", None)
    return ActionTelemetryRecord(
        schema_version=TELEMETRY_SCHEMA_VERSION, run_id=run_id,
        row_index=query.row_index, subject=query.subject,
        relation=query.relation,
        # The canonical ProgramType *value*. `str(enum)` yields
        # "ProgramType.NUMERIC", which no historical bin could ever match.
        program_type=program_type,
        round_index=record["round_index"],
        # Deterministic and owner-published: same row + same logical action +
        # same config gives the same identity in every process.
        operation_id=(
            f"{query.row_index}:{record['round_index']}:"
            f"{getattr(projection, 'action_id', '') or record['kind']}"),
        action_family=(
            getattr(getattr(projection, "family", None), "value", "")
            or record["kind"].upper()),
        target_class=getattr(projection, "facet_id", "")
        or getattr(projection, "target", ""),
        action_id=getattr(projection, "action_id", ""),
        model_role=getattr(projection, "model_role", ""),
        # No reservation occurred - collection predates the calibrated ledger -
        # so the reserved class stays empty and the owner's *declaration* is
        # carried under its own name instead.
        reserved_class="",
        spend_class=getattr(
            getattr(descriptor, "spend_class", None), "value", ""),
        reserve_purpose=getattr(purpose, "value", "") if purpose else "",
        legal=True, selected=record["executed"], executed=record["executed"],
        selection_reason=record.get("refusal", ""),
        pre_state=record["state_before"],
        post_state=record.get("state_after"),
        outcome=ActionOutcome(
            physical_calls=cost["physical_calls"],
            enumerator_calls=cost["enumerator_calls"],
            verifier_calls=cost["verifier_calls"],
            prompt_tokens=cost["prompt_tokens"],
            generated_tokens=cost["generated_tokens"],
            candidates_added=effect.get("candidates_added", ()),
            candidates_supported=effect.get("candidates_supported", ()),
            candidates_contradicted=effect.get("candidates_contradicted", ()),
            candidates_named=effect.get("candidates_named", ()),
            candidates_touched=effect.get("candidates_touched", ()),
            # Measurement presence, transcribed like everything else. An
            # unexecuted action measured nothing, and the seam says so by
            # publishing no effect at all - the defaults then carry
            # "not measured", which is the truth for an unexplored branch.
            candidate_effect_measured=bool(
                effect.get("candidate_effect_measured", False)),
            redundancy=effect.get("redundancy"),
            redundancy_status=effect.get(
                "redundancy_status", RedundancyStatus.UNMEASURED),
            verifier_outcome=effect.get("verifier_outcome", ""),
            structural_outcome=effect.get("structural_outcome", ""),
            errors=tuple(effect.get("errors", ())),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="experiment YAML")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="root for the run directory")
    parser.add_argument("--limit", type=int, default=0,
                        help="development only; the real run must omit this")
    parser.add_argument("--resume", action="store_true",
                        help="continue a matching interrupted run")
    parser.add_argument("--precheck-only", action="store_true",
                        help="run cheap source/path checks without loading models")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text()) or {}
    experiment = config.get("experiment", {})

    split = experiment.get("split", CALIBRATION_SPLIT)
    # Fails closed on VAL and TEST, from the canonical guard rather than a
    # local string comparison a new entry point could forget.
    require_split(IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY, split)

    if args.precheck_only:
        return _run_precheck(
            config=config, config_path=args.config, output_dir=args.output_dir,
            split=split,
        )

    # The readiness verdict, from its owner. Evaluated **before any model is
    # built**, so a profile with the upgraded stack switched off refuses in a
    # second rather than after an hour of inference that observed nothing.
    readiness = (
        evaluate_v3_core_readiness(config, base_dir=args.config.parent, split=split)
        if _v3_enabled(config)
        else evaluate_collection_readiness(
            config, base_dir=args.config.parent, split=split)
    )
    if not readiness.may_run_collection:
        print("collection readiness: REFUSED", file=sys.stderr)
        for blocker in readiness.blockers:
            print(f"  - {blocker}", file=sys.stderr)
        raise CollectionError(
            f"{args.config} is not ready for calibration collection "
            f"({readiness.state.value})")

    dataset = load_dataset(CALIBRATION_SPLIT)
    queries = dataset.queries()
    if not args.limit and len(queries) != EXPECTED_TRAIN_ROWS:
        raise CollectionError(
            f"TRAIN has {len(queries)} rows, expected {EXPECTED_TRAIN_ROWS}; "
            "the benchmark snapshot has changed"
        )
    if args.limit:
        queries = queries[: args.limit]
    total = len(queries)

    v3_enabled = _v3_enabled(config)
    policy = TrainCollectionPolicy(
        family_target=_coverage_target(config) if v3_enabled else 1)
    # The action-family vocabulary this run expects to be able to surface, read
    # from Layer 6's own adapters. A required family that never appears in any
    # catalogue now fails the integrity gate instead of quietly not existing.
    expected_families = (
        _required_v3_families()
        if v3_enabled else required_families(LEGACY_COLLECTED_CATALOGUES)
    )
    policy.note_families(expected_families)

    def selector(kind: str, catalogue, selectable=None):
        """Bounded family-balanced selection, keyed on the canonical family.

        The coverage ledger and Module 21's bins must speak one vocabulary, so
        the family comes from the same Layer-6 adapter that stamps the telemetry
        rather than from a guess at the raw entry's attributes.
        """
        selectable = tuple(selectable) if selectable is not None else tuple(catalogue)
        if kind == "v3":
            return policy.select(
                catalogue,
                family_key=lambda entry: entry.family.value,
                relation_key=lambda entry: entry.relation,
                selectable=selectable,
            )
        from cover_kbc.control.action_catalog import action_family_for
        return policy.select(
            catalogue,
            family_key=lambda entry: action_family_for(kind, entry).value,
            selectable=selectable,
        )

    pipeline, runtime, verifier_runtime = build_pipeline(config, selector)

    enumerator_cfg, verifier_cfg = model_blocks(config)
    identity = RunIdentity(
        train_sha256=dataset.sha256, repo_sha=_repo_sha(),
        config_sha256=_config_sha(config),
        enumerator_model_id=enumerator_cfg.get("model_id", ""),
        enumerator_revision=str(enumerator_cfg.get("revision", "")),
        verifier_model_id=verifier_cfg.get("model_id", ""),
        verifier_revision=str(verifier_cfg.get("revision", "")),
        collection_policy_version=COLLECTION_POLICY_VERSION,
        telemetry_schema_version=TELEMETRY_SCHEMA_VERSION,
        total_rows=total,
    )

    run_id = new_run_id(experiment.get("name", "cover"), "train-collect")
    completed: set[int] = set()
    # Rows that failed and have **not** since completed. This is what the exit
    # gate reads. It is deliberately not the same thing as `failure_history`:
    # a row that failed once and succeeded on retry is a completed row whose
    # telemetry backs the calibration, not a hole in it (Audit 0043 C-03).
    unresolved_failed: set[int] = set()
    failure_history: list[dict] = []
    counters = RunCounters(total_rows=total)
    telemetry_boundary = TelemetryCommitBoundary.empty()
    reconciliation = None

    checkpoint_path = args.output_dir / "checkpoint.json"
    if args.resume:
        # Identity first, always: reconciling artifacts against a checkpoint
        # that describes a different run would delete real work.
        restored = resume_from(checkpoint_path, identity)
        restored_id = str(restored.counters.get("run_id", ""))
        if not restored_id:
            raise ResumeRefused(
                f"{checkpoint_path} records no run_id, so the run directory it "
                "belongs to cannot be identified; a resume must not invent one"
            )
        # The restored id *becomes* this process's run id. Adopting it - rather
        # than minting a fresh one and pointing the output directory at the old
        # one - is what keeps every resume in a single directory under a single
        # identity (Audit 0041 F-05).
        run_id = restored_id
        completed = set(restored.completed_rows)
        unresolved_failed = set(restored.unresolved_failed_rows)
        failure_history = list(restored.failure_history)
        counters = RunCounters.restore(restored.counters, total_rows=total)
        telemetry_boundary = restored.telemetry_committed

    if pipeline.diagnostics is not None:
        pipeline.diagnostics.run_id = run_id
        pipeline.diagnostics.split = split

    out_dir = args.output_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _check_output_root_writable(args.output_dir)
    predictions_path = out_dir / "predictions.jsonl"
    telemetry_path = out_dir / "train_telemetry.jsonl"
    diagnostics_block = dict(config.get("diagnostics") or {})
    inference_telemetry_path = out_dir / str(
        diagnostics_block.get("telemetry_file", "inference_telemetry.jsonl"))
    v3_pre_m8_path = out_dir / "v3_pre_m8_hypothesis_graphs.jsonl"
    v3_final_graph_path = out_dir / "v3_final_hypothesis_graphs.jsonl"
    v3_action_effect_path = out_dir / "v3_action_effects.jsonl"

    if args.resume:
        # Carry the committed coverage forward; a fresh ledger would report only
        # post-restart rows and understate the support offline derivation checks.
        coverage_path = (
            out_dir / "v3_action_coverage.json"
            if v3_enabled else out_dir / "action_coverage.json"
        )
        if coverage_path.is_file():
            policy.coverage = CoverageLedger.from_json(
                json.loads(coverage_path.read_text(encoding="utf-8")))
        elif completed:
            raise ResumeRefused(
                f"{coverage_path} is missing for a checkpoint with "
                f"{len(completed)} committed row(s); refusing to resume without "
                "the committed coverage state"
            )
        policy.note_families(expected_families)
        # Roll the artifacts back to the checkpoint's commit boundary **before**
        # anything is opened for append. A process killed part-way through a row
        # commit leaves that row's telemetry on disk without the checkpoint
        # accepting it; replaying the row would then hit the duplicate guard and
        # abort every future resume until someone hand-edited a JSONL file
        # (Audit 0043 C-04). The checkpoint is the authority; uncommitted
        # material is an interrupted transaction and goes.
        reconciliation = reconcile_to_checkpoint(
            telemetry_path=telemetry_path,
            predictions_path=predictions_path,
            committed_rows=completed,
            committed_queries={q.row_index: (q.subject, q.relation)
                               for q in queries},
            telemetry_boundary=telemetry_boundary,
            coverage=policy.coverage,
        )
        print(f"resuming run {run_id}: {len(completed)} row(s) already complete, "
              f"{len(unresolved_failed)} unresolved failure(s), "
              f"{len(failure_history)} failed attempt(s) in history; "
              f"directory = {out_dir}")
        print(f"  {reconciliation.summary()}")

    print(f"run_id           : {run_id}")
    print(f"split            : {CALIBRATION_SPLIT} ({total} rows)")
    print(f"integration mode : {pipeline.integration_mode.value}")
    print(f"readiness        : {readiness.state.value}")
    print(f"policy           : {COLLECTION_POLICY_VERSION}")
    print(f"coverage target  : {policy.family_target} successful observation(s)")
    print(f"telemetry schema : {TELEMETRY_SCHEMA_VERSION}")
    print(f"enumerator       : {runtime.spec.model_id}")
    print(f"verifier         : {verifier_runtime.spec.model_id}")
    print(f"action bound     : {pipeline.config.max_control_rounds_per_catalogue}"
          f" round(s) per catalogue per query")
    print(f"outputs          : {out_dir}\n")

    aborted: dict | None = None
    seen_records = 0
    writer = TelemetryWriter(telemetry_path, run_id=run_id,
                             resume=bool(args.resume))
    predictions = predictions_path.open("a", encoding="utf-8")

    def persist() -> None:
        state = counters.to_json()
        state["run_id"] = run_id
        # Saved last in the row transaction, and atomically: this write is what
        # *makes* a row committed, so everything the resume path trusts is
        # decided here.
        CollectionCheckpoint(identity, sorted(completed),
                             sorted(unresolved_failed), state,
                             failure_history=failure_history,
                             telemetry_committed=telemetry_boundary).save(
                                 checkpoint_path)
        (out_dir / "accounting.json").write_text(
            json.dumps(counters.to_json(), indent=2), encoding="utf-8")
        _write_coverage_artifacts(
            out_dir, policy.coverage, v3_enabled=v3_enabled)

    try:
        for position, query in enumerate(queries, 1):
            if query.row_index in completed:
                continue
            print(query_line(position, total, relation=query.relation,
                             subject=query.subject))
            before_physical = pipeline.physical_snapshot()
            # Reset the policy's per-query round-robin position. Run-wide
            # coverage is untouched: only *where in the family rotation this
            # query starts* is reset.
            policy.begin_query()
            try:
                graph = pipeline.enumerate_query(query)
                print(round_line(position, total, round_index=0,
                                 detail="acquisition + specialists"))
                prediction = pipeline.decide_graph(graph)
                pipeline._observe(graph, prediction, seen_records)
                program_type = program_type_value(graph.contract)

                row_records = []
                row_outcomes = []
                for record in pipeline.action_records[seen_records:]:
                    row_records.append(_telemetry_for(
                        record, run_id=run_id, query=query,
                        program_type=program_type))
                    if record["executed"]:
                        print(round_line(
                            position, total, round_index=record["round_index"],
                            detail=f"{record['kind'].upper()} "
                                   f"{row_records[-1].action_family} "
                                   f"calls={record['cost']['physical_calls']}"))
                        row_outcomes.append((
                            row_records[-1].action_family,
                            query.relation,
                            (
                                not bool(row_records[-1].outcome.errors)
                                if v3_enabled else True
                            ),
                        ))
                seen_records = len(pipeline.action_records)
            except BaseException as error:                   # noqa: BLE001
                # The row transaction has committed nothing at this point, so a
                # contained failure leaves no half-written record behind. The
                # seam records for the dead row are skipped rather than replayed
                # into the next row's telemetry.
                seen_records = len(pipeline.action_records)
                if is_fatal(error):
                    raise
                burned = pipeline.physical_delta(
                    before_physical, pipeline.physical_snapshot())
                # Unresolved *now*. A later successful attempt at this row
                # removes it from here and leaves the history entry standing.
                unresolved_failed.add(query.row_index)
                counters.unresolved_failed_rows = len(unresolved_failed)
                counters.failed_attempts += 1
                # Wasted spend, kept out of the committed totals: those describe
                # the work backing durable telemetry, and this attempt committed
                # none. Cumulative, so a retry does not erase what it cost.
                counters.failed_attempt_calls += burned["physical_calls"]
                failure_history.append({
                    "row_index": query.row_index, "relation": query.relation,
                    "subject": query.subject,
                    "error": f"{type(error).__name__}: {error}",
                    "physical_calls_burned": burned["physical_calls"],
                    "resolved": False,
                })
                print(f"  ROW FAILED (contained): {type(error).__name__}: {error}",
                      file=sys.stderr)
                persist()
                continue

            # ---- row commit ------------------------------------------------
            # Nothing above this line is durable. A row is a transaction: its
            # telemetry, prediction, coverage and accounting all become visible
            # together, or none of them do. That is what lets a resume replay an
            # interrupted row without leaving its half-written records behind.
            for telemetry_record in row_records:
                writer.write(telemetry_record)
            for family, relation, succeeded in row_outcomes:
                if v3_enabled:
                    policy.coverage.note_executed(
                        family, succeeded=succeeded, relation=relation)
                else:
                    policy.coverage.note_executed(family, succeeded=succeeded)

            predictions.write(json.dumps({
                "SubjectEntity": prediction.subject,
                "Relation": prediction.relation,
                "ObjectEntities": list(prediction.object_entities),
            }, ensure_ascii=False) + "\n")
            predictions.flush()

            # Role split is read from the runtimes, never inferred: Modules 14
            # and 15 use both models inside one operation, so no call site can
            # attribute their calls by itself.
            #
            # Measured *before* any counter moves, because `physical_delta` is
            # the only fallible step here: everything below it is arithmetic on
            # already-validated non-negative integers, so the accounting and the
            # committed-row set can no longer disagree if this row dies. An
            # earlier version incremented `rows_completed` first and a kill in
            # between left the checkpoint claiming a row it had not committed.
            row_cost = pipeline.physical_delta(before_physical,
                                               pipeline.physical_snapshot())
            counters.charge(role="enumerate", calls=row_cost["enumerator_calls"])
            counters.charge(role="verify", calls=row_cost["verifier_calls"])
            counters.prompt_tokens += row_cost["prompt_tokens"]
            counters.generated_tokens += row_cost["generated_tokens"]
            completed.add(query.row_index)
            # A function of the commit boundary, never an independent tally.
            counters.rows_completed = len(completed)
            # This row is no longer an unresolved failure: it committed. Its
            # earlier attempts stay in the history, marked resolved, so the
            # wasted work is still visible and still separately accounted.
            if query.row_index in unresolved_failed:
                unresolved_failed.discard(query.row_index)
                for entry in failure_history:
                    if entry["row_index"] == query.row_index:
                        entry["resolved"] = True
                counters.unresolved_failed_rows = len(unresolved_failed)
            telemetry_boundary = capture_telemetry_commit_boundary(telemetry_path)
            persist()

            if counters.rows_completed % SUMMARY_EVERY == 0:
                print("\n" + summary_block(counters) + "\n")
                print(f"checkpoint saved  persisted={len(completed)}/{total}  "
                      f"path={checkpoint_path}\n")

    except BaseException as error:                      # noqa: BLE001
        oom = "CUDA out of memory" in str(error) or "OutOfMemoryError" in type(
            error).__name__
        aborted = {
            "error": f"{type(error).__name__}: {error}",
            "cuda_oom": oom,
            "completed": f"{counters.rows_completed}/{total}",
            "failing_row": getattr(locals().get("query", None), "row_index", None),
            "relation": getattr(locals().get("query", None), "relation", ""),
            "subject": getattr(locals().get("query", None), "subject", ""),
            "run_directory": str(out_dir),
            "quality_profile": "unchanged - no automatic downgrade attempted",
        }
        print("\nCOLLECTION ABORTED", file=sys.stderr)
        for key, value in aborted.items():
            print(f"  {key}: {value}", file=sys.stderr)
        if oom:
            print("  note: quality profile unchanged; no model, quantization, "
                  "prompt, view or reading count was reduced to fit memory.",
                  file=sys.stderr)
    finally:
        writer.close()
        predictions.close()
        persist()

    # ---- exit gate ---------------------------------------------------------
    # Reaching the end is not success. Every claim printed below is derived from
    # a typed validator over what is actually on disk.
    telemetry_integrity_error = ""
    try:
        validate_committed_telemetry_prefix(
            telemetry_path, telemetry_boundary, allow_uncommitted_tail=False)
        committed = (
            list(read_telemetry(telemetry_path))
            if telemetry_path.is_file() else [])
    except (ResumeRefused, TelemetryError) as error:
        telemetry_integrity_error = str(error)
        committed = []
    prediction_rows = (
        len([line for line in predictions_path.read_text(
            encoding="utf-8").splitlines() if line.strip()])
        if predictions_path.is_file() else 0)
    sufficiency = evaluate_sufficiency(
        committed, expect_transitions=not aborted and counters.rows_completed > 0)

    if pipeline.diagnostics is not None:
        InferenceTelemetryWriter(inference_telemetry_path).write_all(
            pipeline.diagnostics.records)
    if v3_enabled:
        _write_jsonl(v3_pre_m8_path, pipeline.v3_pre_m8_results)
        _write_jsonl(v3_final_graph_path, pipeline.v3_core_results)
        _write_jsonl(v3_action_effect_path, pipeline.v3_action_effects)

    gate: list[str] = []
    if aborted:
        gate.append(f"run aborted: {aborted['error']}")
    if unresolved_failed:
        # Fail closed: a partial TRAIN is not a calibration corpus unless the
        # calibration contract explicitly permits the omission, and it does not.
        # The test is *unresolved* failures, not whether a failure ever
        # happened: a row that failed and was later retried successfully is
        # committed, present in telemetry, and backs the calibration like any
        # other row. Blocking on its history would make the containment feature
        # self-defeating (Audit 0043 C-03).
        gate.append(
            f"{len(unresolved_failed)} row(s) failed and are still missing from "
            f"telemetry: {sorted(unresolved_failed)}")
    if not aborted and counters.rows_completed != total:
        gate.append(
            f"{counters.rows_completed}/{total} rows completed")
    if prediction_rows != counters.rows_completed:
        gate.append(
            f"{prediction_rows} prediction row(s) for {counters.rows_completed} "
            "committed row(s)")
    if telemetry_integrity_error:
        gate.append(
            "telemetry artifact does not match checkpoint commit boundary: "
            f"{telemetry_integrity_error}")
    for family in policy.coverage.unobserved_families:
        coverage = policy.coverage.families[family]
        gate.append(
            f"action family {family} was legal in TRAIN but under-covered "
            f"({coverage.succeeded}/{coverage.target} successful observations; "
            f"{coverage.legal_opportunities} legal opportunities)")
    gate.extend(sufficiency.blockers)

    manifest = {
        "run_id": run_id, "status": "aborted" if aborted else (
            "complete" if not gate else "incomplete"),
        "identity": identity.to_json(),
        "integration_mode": pipeline.integration_mode.value,
        "readiness": readiness.to_json(),
        "rows_completed": counters.rows_completed,
        # Two different questions, two different fields. The first decides
        # whether the corpus is complete; the second is diagnostics that a
        # successful retry must not erase.
        "unresolved_failed_rows": sorted(unresolved_failed),
        "unresolved_failed_row_count": len(unresolved_failed),
        "failed_attempts": len(failure_history),
        "failure_history": failure_history,
        "failed_attempt_calls": counters.failed_attempt_calls,
        "resume_reconciliation": (
            reconciliation.to_json() if reconciliation is not None else None),
        "prediction_rows": prediction_rows,
        "accounting": counters.to_json(),
        "coverage": policy.coverage.to_json(),
        "action_coverage": str(out_dir / "action_coverage.json"),
        "v3_action_coverage": (
            str(out_dir / "v3_action_coverage.json")
            if v3_enabled else None),
        "v3_action_coverage_csv": (
            str(out_dir / "v3_action_coverage.csv")
            if v3_enabled else None),
        "sufficiency": sufficiency.to_json(),
        "inference_telemetry": (
            str(inference_telemetry_path)
            if pipeline.diagnostics is not None else None),
        "v3_pre_m8_hypothesis_graphs": (
            str(v3_pre_m8_path) if v3_enabled else None),
        "v3_final_hypothesis_graphs": (
            str(v3_final_graph_path) if v3_enabled else None),
        "v3_action_effects": (
            str(v3_action_effect_path) if v3_enabled else None),
        "gate_blockers": gate,
        "action_bound_per_catalogue":
            pipeline.config.max_control_rounds_per_catalogue,
        "train_path": str(dataset.path),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "aborted": aborted,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print("\n" + policy.coverage.table())
    if v3_enabled:
        print("\nrelation x action-family coverage")
        print(policy.coverage.relation_table())
    if policy.coverage.families_absent_from_train:
        print("\nfamilies with zero legal TRAIN opportunities (dataset fact, "
              "not a failure):")
        for family in policy.coverage.families_absent_from_train:
            print(f"  {family}")

    print("\n" + sufficiency.summary())
    print(f"\n  rows completed:     {counters.rows_completed} / {total}")
    print(f"  unresolved failed:  {len(unresolved_failed)}")
    print(f"  failed attempts:    {len(failure_history)} "
          f"({counters.failed_attempt_calls} call(s) burned, not committed)")
    print(f"  predictions:    {predictions_path}")
    print(f"  telemetry:      {telemetry_path}")
    if pipeline.diagnostics is not None:
        print(f"  inference:      {inference_telemetry_path}")
    if v3_enabled:
        print(f"  v3 pre-M8:      {v3_pre_m8_path}")
        print(f"  v3 final:       {v3_final_graph_path}")
        print(f"  v3 actions:     {v3_action_effect_path}")
        print(f"  v3 coverage:    {out_dir / 'v3_action_coverage.json'}")
        print(f"  v3 coverage csv:{out_dir / 'v3_action_coverage.csv'}")
    print(f"  manifest:       {out_dir / 'manifest.json'}")

    if gate:
        print("\nTRAIN CALIBRATION COLLECTION INCOMPLETE\n", file=sys.stderr)
        for blocker in gate:
            print(f"  BLOCKER {blocker}", file=sys.stderr)
        return 1

    print("\nTRAIN CALIBRATION COLLECTION COMPLETE\n")
    print("  M20/M21 calibration NOT derived yet - offline derivation is the "
          "next milestone.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CollectionError, CollectionPolicyError, ResumeRefused) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        sys.exit(2)
