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

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.coverage_gap.gap_types import FacetCoverage
from cover_kbc.contracts.router import check_router_consistency
from cover_kbc.data.loader import load_dataset
from cover_kbc.data.writer import write_predictions, write_trace
from cover_kbc.elicitation.library import check_library_covers_contracts
from cover_kbc.evaluation.harness import evaluate_predictions, write_report
from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.registry import build_runtime, model_blocks, spec_from_config
from cover_kbc.paths import OUTPUTS_DIR
from cover_kbc.pipeline import CoverPipeline, PipelineConfig
from cover_kbc.evidence.consensus import build_consensus_engine
from cover_kbc.coverage_gap.missingness import build_coverage_gap_estimator
from cover_kbc.evidence.layer4 import build_layer4_integrator
from cover_kbc.verification.bidirectional_verifier import build_bidirectional_verifier
from cover_kbc.verification.specialist_verifier import build_specialist_verifier
from cover_kbc.query_intelligence import (
    ParametricMemoryRecord,
    QueryRiskProfile,
    ParametricRetrievalPlan,
    ParametricRetrievalResult,
    build_parametric_retriever,
    build_profiler,
    build_prompt_compiler,
)
from cover_kbc.runtime.manifest import RunManifest, new_run_id
from cover_kbc.specialists import (
    LargeSetSpecialistResult,
    NullTemporalSpecialistResult,
    NumericSpecialistResult,
    SmallSetSpecialistResult,
    build_large_set_specialist,
    build_null_temporal_specialist,
    build_numeric_specialist,
    build_small_set_specialist,
)
from cover_kbc.runtime.tracing import RunTracer
from cover_kbc.staging import StageWriter, read_stage, stage_summary
from cover_kbc.types import ModelRole, ProgramType, Query

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
#: Module 10 observability artefact, written in Phase A alongside the profiles.
#: Its own file for the same reason: existing artefacts must stay comparable
#: across the M10 rollout.
PROMPT_PROGRAMS = "prompt_programs.jsonl"
#: Module 11 observability artefact: one record per executed probe. Its own
#: file, so the production artefacts stay byte-comparable across the rollout.
PARAMETRIC_MEMORY = "parametric_memory.jsonl"
#: Module 12 observability artefact: one record per NUMERIC query analysed.
NUMERIC_SPECIALIST = "numeric_specialist.jsonl"
#: Module 13 observability artefact: one record per LARGE_OPEN_SET query.
LARGE_SET_SPECIALIST = "large_open_set_specialist.jsonl"
#: Module 14 observability artefact: one record per NULL_SINGLE query.
NULL_TEMPORAL_SPECIALIST = "null_temporal_specialist.jsonl"
#: Module 15 observability artefact: one record per SMALL_SET query.
SMALL_SET_SPECIALIST = "small_set_specialist.jsonl"
#: Module 16 - one atomic-consensus state per query. Observability only.
ATOMIC_CONSENSUS = "atomic_consensus.jsonl"
#: Module 17 - one specialist-verification record per query. Observability only.
SPECIALIST_VERIFICATION = "specialist_verification.jsonl"
#: Module 18 - one bidirectional-check record per query. Observability only.
BIDIRECTIONAL_VERIFICATION = "bidirectional_verification.jsonl"
#: Layer-4 boundary - one integrated evidence state per query.
LAYER4_EVIDENCE = "layer4_evidence.jsonl"
#: Module 19 - one coverage-gap state per query. Observability only.
COVERAGE_GAP = "coverage_gap.jsonl"
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
    # Module 11's shadow probes are physically real calls on the same runtime,
    # but they are not production spend. Subtracting them keeps this figure
    # comparable with a pre-M11 run; the honest total is reported per-record in
    # parametric_memory.jsonl and summarised at the end of Phase A.
    return total - int(getattr(pipeline, "shadow_calls", 0))


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


def _write_jsonl(path: Path, records) -> Path:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
    return path


def write_query_profiles(run_dir: Path, profiles) -> Path | None:
    """Persist the Module 9 profiles produced by this phase.

    Nothing downstream reads this file in this milestone - it exists so the
    profiler can be inspected without touching any artefact that predates it.
    Skipped entirely when profiling is off, so a disabled run leaves no trace.
    """
    if not profiles:
        return None
    path = _write_jsonl(run_dir / QUERY_PROFILES, profiles)
    print(f"[M9] query profiles: {path}  ({len(profiles)} queries)", flush=True)
    return path


def write_prompt_programs(run_dir: Path, programs) -> Path | None:
    """Persist the Module 10 prompt programs produced by this phase.

    Structured programs only. The rendered preview is deliberately not written:
    it is a projection of these fields, so persisting it would duplicate every
    record in prose without adding information.
    """
    if not programs:
        return None
    path = _write_jsonl(run_dir / PROMPT_PROGRAMS, programs)
    print(f"[M10] prompt programs: {path}  ({len(programs)} queries)", flush=True)
    return path


def write_parametric_memory(run_dir: Path, results) -> Path | None:
    """Persist Module 11's recall records, one line per executed probe.

    Every line carries its own provenance - operation, independence group,
    prompt hash, model identity, parse status and cost - so a record can be
    audited without re-running anything, and can never be mistaken for verified
    evidence.
    """
    if not results:
        return None
    path = run_dir / PARAMETRIC_MEMORY
    calls = tokens = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            plan = result.plan
            calls += result.total_calls
            tokens += result.total_generated_tokens
            for record in result.records:
                payload = {
                    "retrieval_version": plan.retrieval_version,
                    "compiler_version": plan.compiler_version,
                    "profile_version": plan.profile_version,
                    "program_sha256": plan.program_sha256,
                    "SubjectEntity": plan.subject,
                    "Relation": plan.relation,
                    "row_index": plan.row_index,
                    "program_type": plan.program_type.value,
                    **record.to_json(),
                }
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(
        f"[M11] parametric memory: {path}  "
        f"({len(results)} queries, {calls} shadow calls, {tokens} generated tokens)",
        flush=True,
    )
    return path


def write_numeric_specialist(run_dir: Path, results) -> Path | None:
    """Persist Module 12's numeric analyses, one record per NUMERIC query."""
    if not results:
        return None
    path = run_dir / NUMERIC_SPECIALIST
    calls = tokens = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            calls += result.calls
            tokens += result.generated_tokens
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    print(
        f"[M12] numeric specialist: {path}  "
        f"({len(results)} queries, {calls} shadow calls, {tokens} generated tokens)",
        flush=True,
    )
    return path


def write_large_set_specialist(run_dir: Path, results) -> Path | None:
    """Persist Module 13's analyses, one record per LARGE_OPEN_SET query."""
    if not results:
        return None
    path = run_dir / LARGE_SET_SPECIALIST
    calls = tokens = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            calls += result.calls
            tokens += result.generated_tokens
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    print(
        f"[M13] large-open-set specialist: {path}  "
        f"({len(results)} queries, {calls} shadow calls, {tokens} generated tokens)",
        flush=True,
    )
    return path


def write_null_temporal_specialist(run_dir: Path, results) -> Path | None:
    """Persist Module 14's analyses, one record per NULL_SINGLE query."""
    if not results:
        return None
    path = run_dir / NULL_TEMPORAL_SPECIALIST
    calls = tokens = 0
    staged_b = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            calls += result.calls
            tokens += result.generated_tokens
            staged_b += int(result.stage_b_executed)
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    print(
        f"[M14] null/temporal specialist: {path}  "
        f"({len(results)} queries, {staged_b} reached stage B, {calls} shadow "
        f"calls, {tokens} generated tokens)",
        flush=True,
    )
    return path


def write_small_set_specialist(run_dir: Path, results) -> Path | None:
    """Persist Module 15's analyses, one record per SMALL_SET query."""
    if not results:
        return None
    path = run_dir / SMALL_SET_SPECIALIST
    calls = tokens = pending = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            calls += result.calls
            tokens += result.generated_tokens
            pending += len(result.pending_checks)
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    print(
        f"[M15] small-set closure: {path}  "
        f"({len(results)} queries, {pending} pending checks, {calls} shadow "
        f"calls, {tokens} generated tokens)",
        flush=True,
    )
    return path


def write_atomic_consensus(run_dir: Path, results) -> Path | None:
    """Persist Module 16's consensus, one record per query. Non-neural.

    The line count is the query count, and every candidate row carries its own
    provenance - origin events, group supports, phi - so the state can be
    audited without re-running anything. Nothing here is a prediction.
    """
    if not results:
        return None
    path = run_dir / ATOMIC_CONSENSUS
    candidates = origins = disagreements = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            candidates += len(result.candidates)
            origins += result.cost.unique_origin_events
            disagreements += sum(
                1 for c in result.candidates if c.disagreement_details
            )
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    print(
        f"[M16] atomic consensus: {path}  "
        f"({len(results)} queries, {candidates} candidate states, {origins} "
        f"unique origin events, {disagreements} with semantic disagreement, "
        f"0 neural calls)",
        flush=True,
    )
    return path


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_shadow_results(run_dir: Path, pipeline: CoverPipeline) -> None:
    """Reload Phase-A shadow state so a later phase can fuse it.

    Module 16 runs in Phase C, where no specialist object exists and no model
    is loaded. It needs the *results*, not the modules, so they are read back
    from the artefacts Phase A wrote. Reloading rather than recomputing is also
    what makes the round trip checkable: consensus over persisted evidence must
    equal consensus over the evidence in memory.
    """
    by_query: dict[tuple[str, str, int], list[dict]] = {}
    for row in _read_jsonl(run_dir / PARAMETRIC_MEMORY):
        key = (row["SubjectEntity"], row["Relation"], int(row["row_index"]))
        by_query.setdefault(key, []).append(row)
    for (subject, relation, row_index), rows in by_query.items():
        first = rows[0]
        pipeline.retrieval_results.append(ParametricRetrievalResult(
            plan=ParametricRetrievalPlan(
                retrieval_version=first["retrieval_version"],
                compiler_version=first["compiler_version"],
                profile_version=first["profile_version"],
                program_sha256=first["program_sha256"],
                subject=subject, relation=relation, row_index=row_index,
                program_type=ProgramType(first["program_type"]),
                # Not persisted by the Module 11 artefact, so it is left empty
                # rather than guessed. Module 16 reads the versions and the
                # query identity above and never reads this field; inventing a
                # value would put provenance in the record that no run wrote.
                specialist_hint="",
            ),
            records=tuple(ParametricMemoryRecord.from_json(row) for row in rows),
        ))

    for row in _read_jsonl(run_dir / QUERY_PROFILES):
        pipeline.query_profiles.append(QueryRiskProfile.from_json(row))

    for artefact, loader, target in (
        (NUMERIC_SPECIALIST, NumericSpecialistResult, pipeline.numeric_results),
        (LARGE_SET_SPECIALIST, LargeSetSpecialistResult, pipeline.large_set_results),
        (NULL_TEMPORAL_SPECIALIST, NullTemporalSpecialistResult,
         pipeline.null_temporal_results),
        (SMALL_SET_SPECIALIST, SmallSetSpecialistResult, pipeline.small_set_results),
    ):
        for row in _read_jsonl(run_dir / artefact):
            target.append(loader.from_json(row))


def write_specialist_verification(run_dir: Path, results) -> Path | None:
    """Persist Module 17's specialist verification, one record per query.

    In this milestone the pipeline writes the deterministic *catalogue* of
    verifiable targets and verifies nothing on its own: Module 17 spends real
    verifier calls, and choosing which targets deserve one is Module 20/21's.
    A caller that asks explicitly gets its readings recorded in the same row.
    """
    if not results:
        return None
    path = run_dir / SPECIALIST_VERIFICATION
    eligible = skipped = calls = readings = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            eligible += sum(1 for t in result.catalogue if t.eligible)
            skipped += len(result.skipped_targets)
            calls += result.calls
            readings += sum(len(r.template_results) for r in result.results)
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    print(
        f"[M17] specialist verification: {path}  "
        f"({len(results)} queries, {eligible} verifiable targets, {skipped} "
        f"skipped without a call, {readings} readings, {calls} verifier calls)",
        flush=True,
    )
    return path


def write_bidirectional_verification(run_dir: Path, results) -> Path | None:
    """Persist Module 18's checks, one record per query.

    The pipeline writes the deterministic catalogue of eligible §14 checks and
    executes none: each check spends a real call, and choosing which is worth
    one is Module 20/21's. A caller that asks explicitly gets its records in the
    same row.
    """
    if not results:
        return None
    path = run_dir / BIDIRECTIONAL_VERIFICATION
    eligible = skipped = calls = executed = recalled = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            eligible += sum(1 for c in result.catalogue if c.eligible)
            skipped += len(result.ineligible_checks)
            calls += result.calls
            executed += len(result.records)
            recalled += len(result.newly_recalled_candidates)
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    print(
        f"[M18] bidirectional checks: {path}  "
        f"({len(results)} queries, {eligible} eligible checks, {skipped} "
        f"ineligible, {executed} executed, {recalled} newly recalled "
        f"candidates, {calls} calls)",
        flush=True,
    )
    return path


def write_layer4_evidence(run_dir: Path, results) -> Path | None:
    """Persist the Layer-4 evidence state, one record per query.

    Non-neural: the call counts in each row are the Module 17 and Module 18
    calls the row *represents*, each counted once. Integration itself spends
    nothing.
    """
    if not results:
        return None
    path = run_dir / LAYER4_EVIDENCE
    candidates = verified = checked = discovered = credited = calls = 0
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            candidates += len(result.candidates)
            verified += sum(
                1 for c in result.candidates if c.specialist_verifier.available
            )
            checked += sum(len(c.structural_checks) for c in result.candidates)
            discovered += len(result.discovered_candidates)
            credited += len(result.cross_model_credited)
            calls += result.cost.total_calls
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    print(
        f"[L4] verification evidence: {path}  "
        f"({len(results)} queries, {candidates} candidate overlays, {verified} "
        f"with verifier evidence, {checked} structural checks, {discovered} "
        f"discovered, {credited} cross-model credited, {calls} represented "
        f"calls, 0 integration calls)",
        flush=True,
    )
    return path


def write_coverage_gap(run_dir: Path, results) -> Path | None:
    """Persist Module 19's coverage-gap state, one record per query.

    Non-neural: R_t is arithmetic over recorded evidence. It is a heuristic
    residual search-need index, never a probability and never a stop signal.
    """
    if not results:
        return None
    path = run_dir / COVERAGE_GAP
    weak = unexplored = exhausted = 0
    measured = 0
    total = 0.0
    for result in results:
        weak += len(result.facets_in(FacetCoverage.WEAK))
        unexplored += len(result.facets_in(FacetCoverage.UNEXPLORED))
        exhausted += len(result.facets_in(FacetCoverage.EXHAUSTED))
        if result.residual.residual is not None:
            measured += 1
            total += result.residual.residual
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
    mean = f"{total / measured:.3f}" if measured else "n/a"
    print(
        f"[M19] coverage gap: {path}  "
        f"({len(results)} queries, {weak} weak / {unexplored} unexplored / "
        f"{exhausted} exhausted facets, mean R_t={mean} over {measured} "
        f"measured, 0 neural calls)",
        flush=True,
    )
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


def _block_enabled(config: dict, section: str, key: str) -> bool:
    """Whether a configured shadow module is enabled, by configuration alone."""
    block = (config.get(section) or {}).get(key) or {}
    return bool(block.get("enabled", False))


def build_pipeline(config: dict, *, phase: str, tracer: RunTracer | None) -> CoverPipeline:
    """Construct a pipeline loading only the models the phase needs."""
    enumerator_cfg, verifier_cfg = model_blocks(config)
    pipeline_cfg = PipelineConfig.from_mapping(config.get("pipeline"))
    # Modules 9 and 10 run at the M1 seam, which only Phase A crosses.
    intelligence = config.get("query_intelligence") if phase == "enumerate" else None
    profiler = build_profiler(intelligence)
    prompt_compiler = build_prompt_compiler(intelligence, profiler_enabled=profiler is not None)
    retriever = build_parametric_retriever(
        intelligence,
        profiler_enabled=profiler is not None,
        compiler_enabled=prompt_compiler is not None,
    )
    numeric_specialist = build_numeric_specialist(
        config.get("specialists") if phase == "enumerate" else None,
        profiler_enabled=profiler is not None,
        compiler_enabled=prompt_compiler is not None,
        retrieval_enabled=retriever is not None,
    )
    large_set_specialist = build_large_set_specialist(
        config.get("specialists") if phase == "enumerate" else None,
        profiler_enabled=profiler is not None,
        compiler_enabled=prompt_compiler is not None,
        retrieval_enabled=retriever is not None,
    )
    null_temporal_specialist = build_null_temporal_specialist(
        config.get("specialists") if phase == "enumerate" else None,
        profiler_enabled=profiler is not None,
        compiler_enabled=prompt_compiler is not None,
        retrieval_enabled=retriever is not None,
    )
    small_set_specialist = build_small_set_specialist(
        config.get("specialists") if phase == "enumerate" else None,
        profiler_enabled=profiler is not None,
        compiler_enabled=prompt_compiler is not None,
        retrieval_enabled=retriever is not None,
    )
    # Module 16 runs in Phase C, where no specialist object is resident: it
    # consumes recorded results, not modules. Its dependency check therefore
    # reads the *configuration*, exactly as the cross-family rule reads the
    # configured model ids rather than whichever runtime happens to be loaded.
    consensus_engine = (
        build_consensus_engine(
            config.get("consensus"),
            profiler_enabled=_block_enabled(config, "query_intelligence", "profiler"),
            compiler_enabled=_block_enabled(config, "query_intelligence", "prompt_compiler"),
            retrieval_enabled=_block_enabled(
                config, "query_intelligence", "parametric_retrieval"
            ),
            available_specialists={
                "M12": _block_enabled(config, "specialists", "numeric"),
                "M13": _block_enabled(config, "specialists", "large_open_set"),
                "M14": _block_enabled(config, "specialists", "null_temporal"),
                "M15": _block_enabled(config, "specialists", "small_set_closure"),
            },
            relations=sorted(CONTRACTS),
        )
        if phase == "decide" else None
    )
    # Module 17 is built wherever Module 16 is: it reads M16's state to build
    # the verifiable-target catalogue, which costs nothing. Actual readings
    # require an explicit caller and the verifier model role.
    specialist_verifier = (
        build_specialist_verifier(
            config.get("specialist_verifier"),
            consensus_enabled=bool(
                (config.get("consensus") or {}).get("enabled", False)
            ),
            verifier_available=True,
        )
        if consensus_engine is not None else None
    )
    coverage_gap_estimator = (
        build_coverage_gap_estimator(
            config.get("coverage_gap"),
            layer4_enabled=bool(
                (config.get("layer4_integration") or {}).get("enabled", False)
            ),
        )
        if consensus_engine is not None else None
    )
    layer4_integrator = (
        build_layer4_integrator(
            config.get("layer4_integration"),
            consensus_enabled=bool(
                (config.get("consensus") or {}).get("enabled", False)
            ),
        )
        if consensus_engine is not None else None
    )
    # Module 18 is built wherever Module 16 is, for the same reason: its
    # catalogue is a projection of M16 state and costs nothing.
    bidirectional_verifier = (
        build_bidirectional_verifier(
            config.get("bidirectional_verification"),
            consensus_enabled=bool(
                (config.get("consensus") or {}).get("enabled", False)
            ),
        )
        if consensus_engine is not None else None
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
        runtime, pipeline_cfg, tracer=tracer, verifier_runtime=verifier,
        profiler=profiler, prompt_compiler=prompt_compiler, retriever=retriever,
        numeric_specialist=numeric_specialist,
        large_set_specialist=large_set_specialist,
        null_temporal_specialist=null_temporal_specialist,
        small_set_specialist=small_set_specialist,
        consensus_engine=consensus_engine,
        specialist_verifier=specialist_verifier,
        bidirectional_verifier=bidirectional_verifier,
        layer4_integrator=layer4_integrator,
        coverage_gap_estimator=coverage_gap_estimator,
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
    write_prompt_programs(run_dir, pipeline.prompt_programs)
    write_parametric_memory(run_dir, pipeline.retrieval_results)
    write_numeric_specialist(run_dir, pipeline.numeric_results)
    write_large_set_specialist(run_dir, pipeline.large_set_results)
    write_null_temporal_specialist(run_dir, pipeline.null_temporal_results)
    write_small_set_specialist(run_dir, pipeline.small_set_results)
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
    if pipeline.consensus_engine is not None:
        load_shadow_results(run_dir, pipeline)
    result = pipeline.decide(read_stage(source), on_result=_decide_reporter(total))
    write_atomic_consensus(run_dir, pipeline.consensus_results)
    write_specialist_verification(run_dir, pipeline.specialist_verifications)
    write_bidirectional_verification(run_dir, pipeline.bidirectional_results)
    write_layer4_evidence(run_dir, pipeline.layer4_results)
    write_coverage_gap(run_dir, pipeline.coverage_gap_results)

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
