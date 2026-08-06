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
import sys
import time
from collections import Counter
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
from cover_kbc.models.registry import build_runtime, model_blocks, spec_from_config
from cover_kbc.paths import OUTPUTS_DIR
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.query_intelligence import build_profiler
from cover_kbc.runtime.manifest import RunManifest, new_run_id
from cover_kbc.runtime.tracing import RunTracer
from cover_kbc.staging import StageWriter, read_stage, stage_summary
from cover_kbc.types import ModelRole, Query

ENUMERATED = "stage_a_enumerated.jsonl"
VERIFIED = "stage_b_verified.jsonl"
#: One file per role-swap cycle, so every intermediate state stays inspectable.
RESUMED = "stage_r{cycle}_{role}.jsonl"
#: The query set this invocation actually selected. Written in Phase A and
#: compared against at output time, because phases may run separately and
#: predictions must never be validated against themselves.
QUERY_MANIFEST = "query_manifest.json"
#: Module 9 observability artefact, written in Phase A. Deliberately its own
#: file: folding risk profiles into an existing stage would change artefacts
#: that must stay comparable across the M9 rollout.
QUERY_PROFILES = "query_profiles.jsonl"
#: Bug detector, not a stopping rule: the call/token budget is what actually
#: bounds the loop. Exceeding this means the orchestration is cycling, which
#: must fail loudly rather than quietly return a half-finished row.
MAX_ROLE_SWAPS = 12


def load_config(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text()) or {}


# --------------------------------------------------------------------------
# Progress reporting
#
# Observability only. Every function below reads counters and finished objects
# and prints; none writes to a graph, spends a neural call, or touches the RNG.
# The pipeline is unchanged apart from ``decide``'s optional observer, so with
# reporting stripped out the run produces byte-identical artefacts.
# --------------------------------------------------------------------------


def line_buffer_stdout() -> None:
    """Flush stdout per line, so a Colab cell shows progress as it happens.

    ``python -u`` and ``PYTHONUNBUFFERED=1`` already do this; this makes the
    plain invocation behave the same rather than buffering minutes of output.
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):  # pragma: no cover - exotic streams
        pass


def _short(text: object, width: int = 48) -> str:
    """One-line, length-capped rendering of a subject."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= width:
        return collapsed
    return collapsed[: width - 1] + "…"


def _runtime_calls(pipeline) -> int:
    """Neural calls spent so far, counted once per distinct runtime object.

    In Phase A ``verifier_runtime`` aliases ``runtime``, so identity - not
    equality - decides whether a counter is added twice.
    """
    seen: set[int] = set()
    total = 0
    for runtime in (pipeline.runtime, getattr(pipeline, "verifier_runtime", None)):
        if runtime is None or id(runtime) in seen:
            continue
        seen.add(id(runtime))
        total += int(getattr(runtime, "calls", 0))
    return total


def _emit(tag: str, index: int, total: int | None, body: str, calls: int, elapsed: float) -> None:
    position = f"[{index}/{total}]" if total else f"[{index}/?]"
    print(f"{tag} {position} {body} calls={calls} elapsed={elapsed:.1f}s", flush=True)


def _with_progress(graphs, tag: str, total: int | None, pipeline, describe):
    """Pass graphs through untouched, printing one line as each is produced.

    Timing brackets only the pipeline's work for an item: the clock restarts
    after the consumer has written the graph, so persistence cost is not
    charged to the next query.
    """
    mark = time.perf_counter()
    before = _runtime_calls(pipeline)
    for index, graph in enumerate(graphs, start=1):
        after = _runtime_calls(pipeline)
        _emit(tag, index, total, describe(graph), after - before, time.perf_counter() - mark)
        yield graph
        mark = time.perf_counter()
        before = after


def _describe_enumerate(graph) -> str:
    return (
        f"relation={graph.query.relation} "
        f'subject="{_short(graph.query.subject)}" '
        f"candidates={len(graph.candidates)}"
    )


def _describe_verify(graph) -> str:
    candidates = list(graph.candidates.values())
    verified = sum(1 for c in candidates if c.verifications)
    body = (
        f"relation={graph.query.relation} "
        f"candidates={len(candidates)} verified={verified}"
    )
    # Labels are already on the candidates; tallying them computes nothing new.
    labels = Counter(v.label.value for c in candidates for v in c.verifications)
    if labels:
        body += " labels=" + ",".join(f"{k}:{n}" for k, n in sorted(labels.items()))
    return body


def _manifest_total(run_dir: Path) -> int | None:
    """Query count for phases that stream, so ``[i/N]`` still has an ``N``."""
    path = run_dir / QUERY_MANIFEST
    if not path.is_file():
        return None
    try:
        return len(json.loads(path.read_text()).get("queries", []))
    except (OSError, ValueError):
        return None


def _decide_reporter(total: int | None):
    """A ``decide`` observer that prints one line per finished query."""
    state = {"index": 0, "mark": time.perf_counter()}

    def report(prediction, _graph) -> None:
        now = time.perf_counter()
        state["index"] += 1
        position = f"[{state['index']}/{total}]" if total else f"[{state['index']}/?]"
        print(
            f"[PHASE C] {position} predictions={len(prediction.object_entities)} "
            f'stop="{_short(prediction.stopped_reason or "none", 32)}" '
            f"elapsed={now - state['mark']:.2f}s",
            flush=True,
        )
        state["mark"] = time.perf_counter()

    return report


def write_query_profiles(run_dir: Path, profiles) -> Path | None:
    """Persist the Module 9 profiles produced by this phase.

    Nothing downstream reads this file in this milestone - it exists so the
    profiler can be inspected without touching any artefact that predates it.
    Skipped entirely when profiling is off, so a disabled run leaves no trace.
    """
    if not profiles:
        return None
    path = run_dir / QUERY_PROFILES
    with path.open("w", encoding="utf-8") as handle:
        for profile in profiles:
            handle.write(json.dumps(profile.to_json(), ensure_ascii=False) + "\n")
    print(f"[M9] query profiles: {path}  ({len(profiles)} queries)", flush=True)
    return path


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
    # Module 9 profiles at the M1 seam, which only Phase A crosses.
    profiler = (
        build_profiler(config.get("query_intelligence")) if phase == "enumerate" else None
    )

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

    # Declare the *logical* role assignment, so capability never depends on
    # which runtime objects happen to be resident in this phase.
    pipeline_cfg.enumerator_model_id = enumerator_cfg.get("model_id", "")
    pipeline_cfg.verifier_model_id = verifier_cfg.get("model_id", "")
    return CoverPipeline(
        runtime, pipeline_cfg, tracer=tracer, verifier_runtime=verifier, profiler=profiler
    )


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

    # Persist the selected query identity set, honouring this invocation's
    # split, relation filter and limit exactly.
    (run_dir / QUERY_MANIFEST).write_text(json.dumps({
        "split": split,
        "relation": getattr(args, "relation", None),
        "limit": args.limit or 0,
        "queries": [
            {"SubjectEntity": q.subject, "Relation": q.relation, "row_index": q.row_index}
            for q in queries
        ],
    }, indent=2))

    print(f"\n[PHASE A] enumerate  split={split}  queries={len(queries)}  dir={run_dir}")
    with RunTracer(run_dir / "calls_enumerate.jsonl") as tracer:
        pipeline = build_pipeline(config, phase="enumerate", tracer=tracer)
        manifest.add_model(pipeline.runtime.spec)
        with StageWriter(run_dir / ENUMERATED) as writer:
            # The per-query line below supersedes the pipeline's coarse
            # every-25 counter, so that one is left off to keep output clean.
            stream = _with_progress(
                pipeline.enumerate(queries),
                "[PHASE A]", len(queries), pipeline, _describe_enumerate,
            )
            for graph in stream:
                writer.write(graph)

    write_query_profiles(run_dir, pipeline.query_profiles)
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

    total = _manifest_total(run_dir)
    print(f"\n[PHASE B] verify  dir={run_dir}  queries={total if total else '?'}")
    with RunTracer(run_dir / "calls_verify.jsonl") as tracer:
        pipeline = build_pipeline(config, phase="verify", tracer=tracer)
        with StageWriter(run_dir / VERIFIED) as writer:
            stream = _with_progress(
                pipeline.verify(read_stage(source)),
                "[PHASE B]", total, pipeline, _describe_verify,
            )
            for graph in stream:
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
            describe = (
                _describe_enumerate if role is ModelRole.ENUMERATOR else _describe_verify
            )
            with StageWriter(target) as writer:
                stream = _with_progress(
                    driver(iter(graphs)),
                    f"[RESUME {cycle}]", len(graphs), pipeline, describe,
                )
                for graph in stream:
                    writer.write(graph)
        source = target
        print(json.dumps(stage_summary(source), indent=2))

    raise SystemExit(
        f"orchestration exceeded {MAX_ROLE_SWAPS} role swaps without settling; "
        "this is a control-loop bug, not a stopping condition"
    )


def _expected_queries(run_dir: Path, predictions) -> list[Query]:
    """The queries this run was asked to answer, from the persisted manifest.

    Falls back to the predictions only when no manifest exists - an older run
    directory - and says so, rather than silently self-validating.
    """
    path = run_dir / QUERY_MANIFEST
    if not path.is_file():
        print(f"warning: no {QUERY_MANIFEST}; cannot verify row completeness")
        return [Query(p.subject, p.relation, p.row_index) for p in predictions]

    payload = json.loads(path.read_text())
    expected = [
        Query(row["SubjectEntity"], row["Relation"], int(row.get("row_index", -1)))
        for row in payload.get("queries", [])
    ]
    wanted = [(q.subject, q.relation) for q in expected]
    produced = [(p.subject, p.relation) for p in predictions]

    missing = [k for k in wanted if k not in produced]
    extra = [k for k in produced if k not in wanted]
    duplicated = sorted({k for k in produced if produced.count(k) > 1})
    if missing or extra or duplicated:
        raise SystemExit(
            "prediction set does not match the queries this run selected:\n"
            f"  missing   : {missing[:5]}{' ...' if len(missing) > 5 else ''}\n"
            f"  unexpected: {extra[:5]}{' ...' if len(extra) > 5 else ''}\n"
            f"  duplicated: {duplicated[:5]}{' ...' if len(duplicated) > 5 else ''}"
        )
    return expected


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

    total = _manifest_total(run_dir)
    print(
        f"\n[PHASE C] decide  dir={run_dir}  "
        f"queries={total if total else '?'}  (no model loaded)"
    )
    pipeline = build_pipeline(config, phase="decide", tracer=None)
    result = pipeline.decide(read_stage(source), on_result=_decide_reporter(total))

    # Completeness is checked against the *intended* query set, not against the
    # predictions themselves - comparing output to itself could never catch an
    # omitted row.
    expected = _expected_queries(run_dir, result.predictions)
    predictions_path = write_predictions(
        result.predictions, run_dir / "predictions.jsonl", expected_queries=expected
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
    line_buffer_stdout()
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
