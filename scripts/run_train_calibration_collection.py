#!/usr/bin/env python3
"""Run TRAIN calibration collection over the frozen models.

This gathers the action outcomes Modules 20 and 21 will later be calibrated
from. It is **not** a leaderboard run and never becomes one: the mode is
``TRAIN_CALIBRATION_COLLECTION_ONLY``, action choice comes from the fixed
``TrainCollectionPolicy`` rather than from utility, and the split guard refuses
anything but TRAIN.

The run is long and expensive, so durability is designed in rather than hoped
for: telemetry, predictions and the checkpoint are flushed after every row, and
a fatal error - CUDA OOM included - preserves everything completed before
exiting non-zero. **No quality is ever reduced to fit memory.** Model,
quantization, prompts, views, specialist coverage and verifier readings are
whatever the config declares, in success and in failure alike.

TRAIN gold never reaches inference. The runner reads ``SubjectEntity`` and
``Relation`` to build a query and nothing else; ``ObjectEntities`` is joined
offline, in a later milestone, by the derivation step.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import replace
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
    resume_from,
)
from cover_kbc.controller_calibration.collection_policy import (
    COLLECTION_POLICY_VERSION,
    CoverageLedger,
    TrainCollectionPolicy,
    family_of,
)
from cover_kbc.controller_calibration.progress import (
    RunCounters,
    query_line,
    round_line,
    summary_block,
)
from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionOutcome,
    ActionTelemetryRecord,
    ControlStateFeatures,
    TelemetryWriter,
)
from cover_kbc.coverage_gap.missingness import build_coverage_gap_estimator
from cover_kbc.data.loader import load_dataset
from cover_kbc.elicitation.library import check_library_covers_contracts
from cover_kbc.evidence.consensus import build_consensus_engine
from cover_kbc.evidence.layer4 import build_layer4_integrator
from cover_kbc.integration_mode import (
    CALIBRATION_SPLIT,
    IntegrationMode,
    require_split,
)
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.registry import build_runtime, model_blocks
from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
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

#: Rows the official TRAIN split must contain. A different number means the
#: benchmark snapshot moved, and calibrating against it would be calibrating
#: against a different dataset than the one audited.
EXPECTED_TRAIN_ROWS = 477

#: How often the cumulative summary is printed.
SUMMARY_EVERY = 20


class CollectionError(RuntimeError):
    """The collection run could not be started or trusted."""


def _repo_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _sha256_of(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_sha(payload: dict) -> str:
    import hashlib
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _state_features(pipeline, graph) -> ControlStateFeatures:
    """Read the control state from real Module 19 output, never from a guess.

    ``R_t`` and its five weighted components are read straight off M19's own
    result. Nothing is recomputed here: a second implementation of the residual
    would be a second coverage estimator, which the architecture forbids.
    """
    fields = {name: 0.0 for name in (
        "residual", "novelty_rate", "singleton_ratio", "facet_gap",
        "disagreement", "unresolved_mass")}
    for state in reversed(pipeline.coverage_gap_results):
        if (state.relation != graph.query.relation
                or state.subject != graph.query.subject):
            continue
        gap = getattr(state, "coverage_gap", None) or getattr(state, "gap", None)
        if gap is None:
            break
        fields["residual"] = float(getattr(gap, "residual", 0.0) or 0.0)
        for component in getattr(gap, "components", ()):
            name = str(getattr(component, "name", "") or
                       getattr(component, "signal", ""))
            if name in fields:
                fields[name] = float(getattr(component, "value", 0.0) or 0.0)
        break
    return ControlStateFeatures(
        active_candidates=len(graph.active_candidates()),
        calls_used=pipeline.production_calls,
        # Canonical H from its owner. The runner computes no entropy itself.
        entropy=pipeline.control_entropy(graph), **fields)


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
    consensus = build_consensus_engine(config.get("consensus"), **kw)

    pipeline = CoverPipeline(
        runtime, pipeline_config, verifier_runtime=verifier_runtime,
        profiler=profiler, prompt_compiler=compiler, retriever=retriever,
        numeric_specialist=build_numeric_specialist(specialists, **kw),
        large_set_specialist=build_large_set_specialist(specialists, **kw),
        null_temporal_specialist=build_null_temporal_specialist(specialists, **kw),
        small_set_specialist=build_small_set_specialist(specialists, **kw),
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
    )
    return pipeline, runtime, verifier_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="experiment YAML")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="root for the run directory")
    parser.add_argument("--limit", type=int, default=0,
                        help="development only; the real run must omit this")
    parser.add_argument("--resume", action="store_true",
                        help="continue a matching interrupted run")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text()) or {}
    experiment = config.get("experiment", {})

    split = experiment.get("split", CALIBRATION_SPLIT)
    # Fails closed on VAL and TEST, from the canonical guard rather than a
    # local string comparison a new entry point could forget.
    require_split(IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY, split)

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

    policy = TrainCollectionPolicy()
    pipeline, runtime, verifier_runtime = build_pipeline(
        config, lambda kind, catalogue: policy.select(catalogue))

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
    out_dir = args.output_dir / run_id
    checkpoint_path = args.output_dir / "checkpoint.json"
    completed: set[int] = set()
    counters = RunCounters(total_rows=total)

    if args.resume:
        restored = resume_from(checkpoint_path, identity)
        completed = set(restored.completed_rows)
        counters = RunCounters.restore(restored.counters, total_rows=total)
        out_dir = args.output_dir / restored.counters.get("run_id", run_id)
        # Carry the committed coverage forward; a fresh ledger would report
        # only post-restart rows and understate the support offline derivation
        # checks.
        coverage_path = out_dir / "action_coverage.json"
        if coverage_path.is_file():
            policy.coverage = CoverageLedger.from_json(
                json.loads(coverage_path.read_text(encoding="utf-8")))
        print(f"resuming: {len(completed)} row(s) already complete; "
              f"committed rows = {len(completed)} / {total}")

    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "predictions.jsonl"
    telemetry_path = out_dir / "train_telemetry.jsonl"

    print(f"run_id           : {run_id}")
    print(f"split            : {CALIBRATION_SPLIT} ({total} rows)")
    print(f"integration mode : {pipeline.integration_mode.value}")
    print(f"policy           : {COLLECTION_POLICY_VERSION}")
    print(f"telemetry schema : {TELEMETRY_SCHEMA_VERSION}")
    print(f"enumerator       : {runtime.spec.model_id}")
    print(f"verifier         : {verifier_runtime.spec.model_id}")
    print(f"outputs          : {out_dir}\n")

    failed: list[int] = []
    failure: dict | None = None
    seen_records = 0
    writer = TelemetryWriter(telemetry_path, run_id=run_id,
                             resume=bool(args.resume))
    predictions = predictions_path.open("a", encoding="utf-8")

    def persist() -> None:
        state = counters.to_json()
        state["run_id"] = run_id
        CollectionCheckpoint(identity, sorted(completed), sorted(failed),
                             state).save(checkpoint_path)
        (out_dir / "accounting.json").write_text(
            json.dumps(counters.to_json(), indent=2), encoding="utf-8")
        (out_dir / "action_coverage.json").write_text(
            json.dumps(policy.coverage.to_json(), indent=2), encoding="utf-8")

    try:
        for position, query in enumerate(queries, 1):
            if query.row_index in completed:
                continue
            print(query_line(position, total, relation=query.relation,
                             subject=query.subject))
            before_physical = pipeline.physical_snapshot()

            graph = pipeline.enumerate_query(query)
            pre = _state_features(pipeline, graph)
            print(round_line(position, total, round_index=1,
                             detail="acquisition + specialists"))

            prediction = pipeline.decide_graph(graph)
            post = _state_features(pipeline, graph)

            # One telemetry record per action the pipeline actually
            # considered, with the cost and state delta measured around that
            # individual action by the canonical seam. Nothing here estimates.
            row_records = []
            row_outcomes = []
            for record in pipeline.action_records[seen_records:]:
                action = record.get("action")
                cost = record["cost"]
                post = record["post"]
                row_records.append(ActionTelemetryRecord(
                    schema_version=TELEMETRY_SCHEMA_VERSION, run_id=run_id,
                    row_index=query.row_index, subject=query.subject,
                    relation=query.relation,
                    program_type=str(getattr(graph.contract, "program_type", "")),
                    round_index=record["round_index"],
                    operation_id=f"{record['kind']}:{record['round_index']}:"
                                 f"{len(policy.coverage.families)}:{id(action)}",
                    action_family=family_of(action) or record["kind"].upper(),
                    model_role=("verify" if cost["verifier_calls"] else "enumerate"),
                    legal=True, selected=record["executed"],
                    executed=record["executed"],
                    selection_reason=record.get("refusal", ""),
                    pre_state=replace(
                        _state_features(pipeline, graph),
                        entropy=record.get("entropy_before") or 0.0),
                    post_state=(replace(
                        _state_features(pipeline, graph),
                        entropy=record.get("entropy_after") or 0.0)
                        if post else None),
                    outcome=ActionOutcome(
                        physical_calls=cost["physical_calls"],
                        generated_tokens=cost["generated_tokens"],
                    ),
                ))
                if record["executed"] and action is not None:
                    row_outcomes.append(action)
            seen_records = len(pipeline.action_records)

            # ---- row commit ------------------------------------------------
            # Nothing above this line is durable. A row is a transaction: its
            # telemetry, prediction, coverage and accounting all become visible
            # together, or none of them do. That is what lets a resume replay an
            # interrupted row without leaving its half-written records behind.
            for telemetry_record in row_records:
                writer.write(telemetry_record)
            for action in row_outcomes:
                policy.record_outcome(action, succeeded=True)

            predictions.write(json.dumps({
                "SubjectEntity": prediction.subject,
                "Relation": prediction.relation,
                "ObjectEntities": list(prediction.object_entities),
            }, ensure_ascii=False) + "\n")
            predictions.flush()

            counters.rows_completed += 1
            # Role split is read from the runtimes, never inferred: Modules 14
            # and 15 use both models inside one operation, so no call site can
            # attribute their calls by itself.
            row_cost = pipeline.physical_delta(before_physical,
                                               pipeline.physical_snapshot())
            counters.charge(role="enumerate", calls=row_cost["enumerator_calls"])
            counters.charge(role="verify", calls=row_cost["verifier_calls"])
            counters.generated_tokens += row_cost["generated_tokens"]
            completed.add(query.row_index)
            persist()

            if counters.rows_completed % SUMMARY_EVERY == 0:
                print("\n" + summary_block(counters) + "\n")
                print(f"checkpoint saved  persisted={len(completed)}/{total}  "
                      f"path={checkpoint_path}\n")

    except BaseException as error:                      # noqa: BLE001
        oom = "CUDA out of memory" in str(error) or "OutOfMemoryError" in type(
            error).__name__
        failure = {
            "error": f"{type(error).__name__}: {error}",
            "cuda_oom": oom,
            "completed": f"{counters.rows_completed}/{total}",
            "failing_row": getattr(locals().get("query", None), "row_index", None),
            "relation": getattr(locals().get("query", None), "relation", ""),
            "subject": getattr(locals().get("query", None), "subject", ""),
            "run_directory": str(out_dir),
            "quality_profile": "unchanged - no automatic downgrade attempted",
        }
        print("\nCOLLECTION FAILED", file=sys.stderr)
        for key, value in failure.items():
            print(f"  {key}: {value}", file=sys.stderr)
        if oom:
            print("  note: quality profile unchanged; no model, quantization, "
                  "prompt, view or reading count was reduced to fit memory.",
                  file=sys.stderr)
    finally:
        writer.close()
        predictions.close()
        persist()

    manifest = {
        "run_id": run_id, "status": "failed" if failure else "complete",
        "identity": identity.to_json(),
        "integration_mode": pipeline.integration_mode.value,
        "rows_completed": counters.rows_completed, "rows_failed": len(failed),
        "accounting": counters.to_json(),
        "coverage": policy.coverage.to_json(),
        "train_path": str(dataset.path),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "failure": failure,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    if failure:
        return 1

    coverage_ok = policy.coverage.integrity_ok()
    print("\n" + policy.coverage.table())
    if policy.coverage.families_absent_from_train:
        print("\nfamilies with zero legal TRAIN opportunities (dataset fact, "
              "not a failure):")
        for family in policy.coverage.families_absent_from_train:
            print(f"  {family}")

    print("\nTRAIN CALIBRATION COLLECTION COMPLETE\n")
    print(f"  rows completed: {counters.rows_completed} / {total}")
    print(f"  rows failed:    {len(failed)}\n")
    print(f"  telemetry integrity: {'PASS' if telemetry_path.is_file() else 'FAIL'}")
    print(f"  accounting integrity: "
          f"{'PASS' if counters.physical_model_calls >= 0 else 'FAIL'}")
    print(f"  required action-family coverage: {'PASS' if coverage_ok else 'FAIL'}\n")
    print(f"  predictions: {predictions_path}")
    print(f"  telemetry:   {telemetry_path}")
    print(f"  manifest:    {out_dir / 'manifest.json'}")
    print("\n  M20/M21 calibration NOT derived yet - offline derivation is the "
          "next milestone.")
    return 0 if coverage_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CollectionError, ResumeRefused) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        sys.exit(2)
