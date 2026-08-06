"""Post-architecture real-model runtime smoke.

Proves that the architecture which passed under `ScriptedRuntime` also executes
against the two **real frozen models** and their native tokenizer paths. It
answers runtime questions only — can the weights load, do the tokenizer and
chat-template paths work, does `score_labels` produce usable logits, does
Module 17's live call plan cost what Module 20 says it costs, are physical calls
accounted, does the shadow stack leave production output untouched.

It answers **no** factual question. There is no accuracy, no precision, no
recall, no F1 and no leaderboard number here, and a semantically wrong answer is
still a runtime PASS as long as every contract executed correctly.

**No benchmark data is read.** The smoke manifest below is declared by hand for
runtime compatibility only; no split is loaded, no gold is stored, and nothing
is scored.

    python scripts/real_model_smoke.py --config configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml  # noqa: E402

from cover_kbc.contracts.registry import CONTRACTS  # noqa: E402
from cover_kbc.control.action_catalog import m17_call_plan  # noqa: E402
from cover_kbc.models.base import (  # noqa: E402
    DecodeProfile,
    GenerationRequest,
    LabelScoreRequest,
)
from cover_kbc.models.registry import build_runtime, model_blocks  # noqa: E402
from cover_kbc.types import Query  # noqa: E402

#: Manually declared for runtime compatibility. **Not** selected from any
#: benchmark file, and no gold is stored or compared. One query per specialist
#: family: borders and area share M15/M12 with stock and capacity and already
#: have full scripted coverage, so they do not need an extra expensive
#: real-weight query.
SMOKE_MANIFEST = [
    # NUMERIC -> M12
    ("hasCapacity", "Wembley Stadium"),
    # LARGE_OPEN_SET -> M13
    ("awardWonBy", "Nobel Prize in Literature"),
    # NULL_SINGLE -> M14
    ("personHasCityOfDeath", "Ada Lovelace"),
    # SMALL_SET / temporal -> M15
    ("companyTradesAtStockExchange", "Siemens AG"),
]

SPECIALIST_FAMILY = {
    "hasCapacity": "M12_NUMERIC", "awardWonBy": "M13_LARGE_OPEN_SET",
    "personHasCityOfDeath": "M14_NULL_TEMPORAL",
    "companyTradesAtStockExchange": "M15_SMALL_SET",
}


class SmokeFailure(RuntimeError):
    """A runtime contract failed. Never masked, never continued past."""


class StagedRuntimes:
    """Holds **at most one** real checkpoint on the device at a time.

    The composed smoke needs both roles, but not simultaneously: Module 2's
    acquisition is enumerator-only, Module 4/17/18's verification is
    verifier-only, and Module 8's finalisation needs no model at all. That is
    the repository's own staged contract (`enumerate` / `verify` / `decide`),
    so the smoke follows it rather than holding 24B and 4B resident together.

    Releasing is not `del runtime`. A runtime is reachable from the pipeline,
    from the specialists and verifiers that were handed it, and from any closure
    that captured it, so every owner has to be dropped before the allocator can
    reclaim anything — which is why callers pass their pipeline in.

    Staged residency is the **default on every GPU**. One implementation, no
    per-device branch.
    """

    ENUMERATOR = "enumerator"
    VERIFIER = "verifier"

    def __init__(self, enumerator_spec: dict, verifier_spec: dict) -> None:
        self.enumerator_spec = enumerator_spec
        self.verifier_spec = verifier_spec
        self.runtime: Any = None
        self.role: str | None = None
        self.model_id: str | None = None
        #: Roles loaded, in order, for the summary.
        self.history: list[str] = []

    @property
    def shared_profile(self) -> bool:
        """One checkpoint named for both roles may legitimately load once."""
        return self.enumerator_spec == self.verifier_spec

    def load(self, role: str) -> Any:
        if role not in (self.ENUMERATOR, self.VERIFIER):
            raise SmokeFailure(f"unknown model role {role!r}")
        if self.runtime is not None and self.role != role:
            raise SmokeFailure(
                f"role {self.role!r} is still resident; release it before "
                f"loading {role!r} — two distinct checkpoints must never be "
                "resident together"
            )
        if self.runtime is None:
            spec = (self.enumerator_spec if role == self.ENUMERATOR
                    else self.verifier_spec)
            self.runtime = build_runtime(spec)
            self.model_id = spec["model_id"]
            self.history.append(role)
        self.role = role
        return self.runtime

    def release(self, *owners: Any) -> None:
        """Drop the runtime and every owner that can still reach it."""
        for owner in owners:
            for attribute in (
                "runtime", "verifier_runtime", "numeric_specialist",
                "large_set_specialist", "null_temporal_specialist",
                "small_set_specialist", "retriever", "specialist_verifier",
                "bidirectional_verifier", "consensus_engine",
            ):
                if hasattr(owner, attribute):
                    try:
                        setattr(owner, attribute, None)
                    except Exception:                  # pragma: no cover
                        pass
        self.runtime = None
        self.role = None
        self.model_id = None

        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
        except ImportError:                            # pragma: no cover
            pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def repo_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True).stdout.strip()
    except Exception:                                  # pragma: no cover
        return "unknown"


# --------------------------------------------------------------------------
# Phase 1/2 - primitive runtime smokes
# --------------------------------------------------------------------------


def primitive_generate(runtime: Any, spec: dict) -> dict:
    """One real `LMRuntime.generate` through the production Mistral runtime."""
    prompt = (
        "Name one country that shares a land border with Portugal. "
        "Answer with the country name only."
    )
    request = GenerationRequest(
        prompt=prompt,
        decode=DecodeProfile(temperature=0.0, max_new_tokens=16),
        metadata={"smoke": "primitive_generate"},
    )
    result = runtime.generate(request)
    if result.error:
        raise SmokeFailure(f"enumerator generate failed: {result.error}")
    if not result.text:
        raise SmokeFailure("enumerator generate returned no text")
    return {
        "ok": True,
        "model_id": result.model_id,
        "expected_model_id": spec["model_id"],
        "revision": spec.get("revision"),
        "tokenizer_backend": spec.get("tokenizer_backend"),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
        "prompt_tokens": result.prompt_tokens,
        "generated_tokens": result.generated_tokens,
        "finish_reason": result.finish_reason,
        "decoded_non_empty": bool(result.text.strip()),
    }


def primitive_score_labels(runtime: Any, spec: dict) -> dict:
    """One real `LMRuntime.score_labels` through the production Qwen runtime."""
    from cover_kbc.verification.blind import LABEL_TOKENS, VERIFIER_SYSTEM_PROMPT

    # `LabelScoreRequest.labels` is a **mapping** from label name to the exact
    # continuation string whose first token is scored. Every production call
    # site passes `dict(LABEL_TOKENS)`; flattening it to its values produced a
    # sequence that `dict(request.labels)` cannot unpack, which is what the
    # first real Qwen run caught. The runtime was right to refuse it.
    labels = dict(LABEL_TOKENS)
    prompt = (
        "Statement: Lisbon is the capital of Portugal.\n"
        "A) VALID  B) INVALID  C) UNKNOWN\nAnswer:"
    )
    request = LabelScoreRequest(
        prompt=prompt, labels=labels,
        system_prompt=VERIFIER_SYSTEM_PROMPT,
        metadata={"smoke": "primitive_score_labels"},
    )
    result = runtime.score_labels(request)
    if result.error:
        raise SmokeFailure(f"verifier score_labels failed: {result.error}")

    import math

    # `LabelScoreResult.logits` is a **mapping** from label *name* to its
    # uncalibrated logit - `{"VALID": .., "INVALID": .., "UNKNOWN": ..}` - not a
    # sequence. Iterating it yields the names, which is how the first real Qwen
    # read-out produced `must be real number, not str`. Every numeric check
    # below therefore reads `.values()`, and every report keeps the names.
    logits = dict(result.logits)
    if set(logits) != set(labels):
        raise SmokeFailure(
            f"score_labels returned logits for {sorted(logits)} but the request "
            f"asked for {sorted(labels)}")
    bad = {name: value for name, value in logits.items()
           if not isinstance(value, (int, float)) or not math.isfinite(value)}
    if bad:
        raise SmokeFailure(f"score_labels returned non-finite logits: {bad}")

    # The canonical softmax is the result's own, so the smoke cannot become a
    # second, numerically different verifier implementation.
    probabilities = result.probabilities()
    if set(probabilities) != set(logits):
        raise SmokeFailure(
            f"probability labels {sorted(probabilities)} do not match logit "
            f"labels {sorted(logits)}")
    bad = {name: value for name, value in probabilities.items()
           if not math.isfinite(value)}
    if bad:
        raise SmokeFailure(f"non-finite label probabilities: {bad}")
    total = sum(probabilities.values())
    if abs(total - 1.0) > 1e-6:
        raise SmokeFailure(f"label probabilities sum to {total}, not 1.0")
    # How the *real* tokenizer encoded the labels, and therefore which scoring
    # path ran. Never assumed to be single-token: the runtime inspects it and
    # falls back to sequence log-likelihood when it is not.
    encoding = getattr(runtime, "label_encoding", None)
    return {
        "ok": True,
        "model_id": result.model_id,
        "expected_model_id": spec["model_id"],
        "revision": spec.get("revision"),
        "tokenizer_backend": spec.get("tokenizer_backend"),
        "labels": dict(labels),
        "label_single_token": (
            bool(encoding.single_token) if encoding is not None else None),
        "scoring_strategy": (
            encoding.strategy if encoding is not None else None),
        "label_token_ids": (
            {name: list(ids) for name, ids in encoding.token_ids.items()}
            if encoding is not None else None),
        # Per-label evidence, keyed by name, so the real read-out is
        # inspectable rather than an anonymous numeric list.
        "logits": logits,
        "probabilities": probabilities,
        "logits_finite": True,
        "probabilities_normalise": True,
        "probability_sum": total,
        "generated_tokens": 0,          # scoring generates nothing (Audit 0010)
        "prompt_tokens": result.prompt_tokens,
    }


# --------------------------------------------------------------------------
# Phase 3 - Module 17's live call plan, the Audit 0033 §16A regression
# --------------------------------------------------------------------------


def m17_plan_regression(config: dict, runtime: Any) -> dict:
    """Observed physical calls must equal `m17_call_plan(live config)`.

    Cold: every contextual control is uncached. Warm: the same controls are
    already measured, so only the factual readings cost anything. The expected
    numbers are **derived**, never written down here.
    """
    from cover_kbc.verification.specialist_verifier import (
        SpecialistVerifier, SpecialistVerifierConfig,
    )

    block = dict(config.get("specialist_verifier") or {})
    block["enabled"] = True
    verifier_config = SpecialistVerifierConfig.from_mapping(block)
    readings, controls = m17_call_plan(verifier_config)
    expected_cold, expected_warm = readings + controls, readings

    from cover_kbc.verification.specialist_contracts import specialist_contract
    from cover_kbc.verification.specialist_types import (
        SpecialistVerificationRequest, VerificationTarget, VerificationTargetKind,
    )

    verifier = SpecialistVerifier(verifier_config)
    relation = "hasCapacity"
    contract = CONTRACTS[relation]
    spec_contract = specialist_contract(relation)

    def request_for(target_id: str) -> SpecialistVerificationRequest:
        return SpecialistVerificationRequest(
            target=VerificationTarget(
                relation=relation, subject="Wembley Stadium", row_index=0,
                kind=VerificationTargetKind.NUMERIC_CLUSTER,
                target_id=target_id, display=target_id,
                family=spec_contract.family, canonical_unit="persons",
            ),
            family=spec_contract.family,
            contract_version=spec_contract.contract_version,
            template_ids=verifier_config.template_ids,
            label_orders=verifier_config.label_orders,
        )

    # Cold: no contextual control has been measured for these templates yet.
    before = runtime.calls
    verifier.verify(request_for("90000"), contract, runtime)
    observed_cold = runtime.calls - before

    # Warm: the same control identities are now cached, so only the factual
    # readings cost anything.
    before = runtime.calls
    verifier.verify(request_for("86000"), contract, runtime)
    observed_warm = runtime.calls - before

    ok = observed_cold == expected_cold and observed_warm == expected_warm
    if not ok:
        raise SmokeFailure(
            f"Module 17 runtime disagrees with its planner: expected cold "
            f"{expected_cold} / warm {expected_warm}, observed cold "
            f"{observed_cold} / warm {observed_warm}"
        )
    return {
        "ok": ok,
        "template_ids": list(verifier_config.template_ids),
        "label_orders": [o.value for o in verifier_config.label_orders],
        "use_calibration": verifier_config.use_calibration,
        "factual_readings": readings,
        "controls": controls,
        "expected_cold_calls": expected_cold,
        "observed_cold_calls": observed_cold,
        "expected_warm_calls": expected_warm,
        "observed_warm_calls": observed_warm,
    }


def m20_consistency(m17: dict) -> dict:
    """Non-executing check: M20's safe cost equals the observed requirement.

    No production Module 20 ledger is reserved and no calibrated package is
    created — Module 20 remains uncalibrated and disabled.
    """
    from cover_kbc.control.budget_accounting import specialist_verification_plan

    cold = specialist_verification_plan(
        readings=m17["factual_readings"],
        control_calls_needed=m17["controls"], controls_total=m17["controls"])
    warm = specialist_verification_plan(
        readings=m17["factual_readings"],
        control_calls_needed=0, controls_total=m17["controls"])
    cold_calls = sum(c.calls for c in cold)
    warm_calls = sum(c.calls for c in warm)
    if cold_calls != m17["observed_cold_calls"] or warm_calls != m17["observed_warm_calls"]:
        raise SmokeFailure(
            f"Module 20 safe cost ({cold_calls}/{warm_calls}) disagrees with the "
            f"observed Module 17 requirement "
            f"({m17['observed_cold_calls']}/{m17['observed_warm_calls']})")
    return {"ok": True, "safe_cold_calls": cold_calls, "safe_warm_calls": warm_calls,
            "module_20_activated": False, "ledger_reserved": False}


# --------------------------------------------------------------------------
# Phase 5 - composed real-weight shadow smoke
# --------------------------------------------------------------------------


def build_pipeline(config: dict, runtime: Any, *, upgraded: bool):
    """One pipeline bound to whichever single role is currently resident.

    ``verifier_runtime`` is deliberately not passed: in staged mode the verifier
    is a *later* phase with its own pipeline, so handing this one a second
    runtime would defeat the whole point.
    """
    from cover_kbc.coverage_gap.missingness import CoverageGapEstimator
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler)
    from cover_kbc.specialists import (
        LargeSetSpecialist, NullTemporalSpecialist, NumericSpecialist,
        SmallSetSpecialist)
    from cover_kbc.verification.bidirectional_verifier import BidirectionalVerifier
    from cover_kbc.verification.specialist_verifier import (
        SpecialistVerifier, SpecialistVerifierConfig)

    pipeline_config = PipelineConfig()
    if not upgraded:
        return CoverPipeline(runtime, pipeline_config)

    verifier_block = dict(config.get("specialist_verifier") or {})
    verifier_block["enabled"] = True
    return CoverPipeline(
        runtime, pipeline_config,
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), numeric_specialist=NumericSpecialist(),
        large_set_specialist=LargeSetSpecialist(),
        null_temporal_specialist=NullTemporalSpecialist(),
        small_set_specialist=SmallSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        specialist_verifier=SpecialistVerifier(
            SpecialistVerifierConfig.from_mapping(verifier_block)),
        bidirectional_verifier=BidirectionalVerifier(),
        layer4_integrator=Layer4EvidenceIntegrator(),
        coverage_gap_estimator=CoverageGapEstimator(),
        # Module 20, Module 21 and Layer 6 stay OFF: no TRAIN calibration
        # exists, and a synthetic package would not prove production readiness.
    )


#: The Phase-A shadow artefacts, and the typed loader each one round-trips
#: through. Exactly the set `run_staged.py` restores between phases.
_SHADOW_ARTEFACTS = (
    ("query_profiles", "cover_kbc.query_intelligence.types", "QueryRiskProfile"),
    ("numeric_results", "cover_kbc.specialists.numeric_types",
     "NumericSpecialistResult"),
    ("large_set_results", "cover_kbc.specialists.large_set_types",
     "LargeSetSpecialistResult"),
    ("null_temporal_results", "cover_kbc.specialists.null_temporal_types",
     "NullTemporalSpecialistResult"),
    ("small_set_results", "cover_kbc.specialists.small_set_types",
     "SmallSetSpecialistResult"),
)


def _write_shadow_state(pipeline: Any, path: Path) -> None:
    """Persist the Phase-A shadow half through its own typed serialisation."""
    payload = {
        attribute: [item.to_json() for item in getattr(pipeline, attribute, [])]
        for attribute, _, _ in _SHADOW_ARTEFACTS
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _restore_shadow_state(pipeline: Any, path: Path) -> None:
    """Reload it into the next phase's pipeline. No new identity system."""
    import importlib

    if not path.exists():
        return
    payload = json.loads(path.read_text())
    for attribute, module_name, class_name in _SHADOW_ARTEFACTS:
        target = getattr(pipeline, attribute, None)
        if target is None:
            continue
        loader = getattr(importlib.import_module(module_name), class_name)
        for row in payload.get(attribute, []):
            target.append(loader.from_json(row))


def staged_pass(config: dict, staged: StagedRuntimes, work_dir: Path, *,
                upgraded: bool, tag: str) -> dict:
    """One full ENUMERATOR -> release -> VERIFIER -> release -> decide pass.

    State crosses each role boundary through the repository's own typed staging
    contract (`write_stage` / `read_stage`, i.e. `graph_to_json`), so query,
    candidate, origin, independence-group, call and operation identities are
    carried by the same serialisation the staged CLI uses. No parallel
    representation is invented for the smoke.
    """
    from cover_kbc.staging import read_stage, write_stage

    stage_path = work_dir / f"{tag}_stage_a.jsonl"
    shadow_path = work_dir / f"{tag}_shadow_state.json"

    # -- PHASE E1: enumerator only ------------------------------------------
    runtime = staged.load(StagedRuntimes.ENUMERATOR)
    pipeline = build_pipeline(config, runtime, upgraded=upgraded)
    graphs = [
        pipeline.enumerate_query(Query(subject, relation, index))
        for index, (relation, subject) in enumerate(SMOKE_MANIFEST)
    ]
    write_stage(graphs, stage_path, keep_prompts=False)
    # The Phase-A shadow half - Module 9's profiles and the Module 12-15
    # specialist results - does not live on the graph, so it crosses the role
    # boundary through the same typed `to_json` contract `run_staged.py` uses
    # when it restores these artefacts between phases.
    _write_shadow_state(pipeline, shadow_path)
    enumerate_calls = runtime.calls
    profiles = [
        {
            "Relation": p.relation, "SubjectEntity": p.subject,
            "novelty_risk": p.novelty_risk.value if p.novelty_risk else None,
            "novelty_basis": p.novelty_basis,
            "secondary_hints": [h.value for h in p.secondary_hints],
            "refined": p.novelty_basis != "no early graph has been observed",
        }
        for p in pipeline.query_profiles
    ]
    staged.release(pipeline)
    del graphs, pipeline

    # -- PHASE V: verifier only ---------------------------------------------
    runtime = staged.load(StagedRuntimes.VERIFIER)
    pipeline = build_pipeline(config, runtime, upgraded=upgraded)
    _restore_shadow_state(pipeline, shadow_path)
    verified = [pipeline.verify_graph(graph) for graph in read_stage(stage_path)]
    verify_calls = runtime.calls

    # -- PHASE DECIDE: no model at all --------------------------------------
    # Module 16, Layer 4 and Module 19 run at the Phase-C seam inside
    # `decide_graph`, so their state is read *after* this loop, not before it.
    predictions = [pipeline.decide_graph(graph) for graph in verified]
    upgraded_state = {
        "consensus": len(pipeline.consensus_results),
        "layer4": len(pipeline.layer4_results),
        "coverage_gap": len(pipeline.coverage_gap_results),
        "m18_mechanisms": sorted({
            check.check_kind
            for state in pipeline.layer4_results
            for overlay in state.candidates
            for check in overlay.structural_checks
        }),
    } if upgraded else {}
    staged.release(pipeline)
    del verified, pipeline

    return {
        "tag": tag,
        "upgraded": upgraded,
        "enumerate_calls": enumerate_calls,
        "verify_calls": verify_calls,
        "total_calls": enumerate_calls + verify_calls,
        "profiles": profiles,
        "upgraded_state": upgraded_state,
        "objects": [list(p.object_entities) for p in predictions],
        "stop_reasons": [p.stopped_reason for p in predictions],
        "calls_used": [p.calls_used for p in predictions],
        "generated_tokens_used": [p.generated_tokens_used for p in predictions],
    }


def compare_passes(core: dict, upgraded: dict) -> dict:
    """Shadow isolation, compared **after** both sequential passes finished."""
    unchanged = core["objects"] == upgraded["objects"]
    budget_unchanged = (
        core["calls_used"] == upgraded["calls_used"]
        and core["generated_tokens_used"] == upgraded["generated_tokens_used"])
    stops_unchanged = core["stop_reasons"] == upgraded["stop_reasons"]
    result = {
        "production_core_calls": core["total_calls"],
        "upgraded_shadow_calls": upgraded["total_calls"],
        "shadow_only_calls": upgraded["total_calls"] - core["total_calls"],
        "production_output_unchanged": unchanged,
        "m7_budget_unchanged": budget_unchanged,
        "production_stop_reasons_unchanged": stops_unchanged,
        "specialist_families": sorted(SPECIALIST_FAMILY.values()),
        "m18_mechanisms_executed": upgraded["upgraded_state"].get(
            "m18_mechanisms", []),
        "m9_refined": all(p["refined"] for p in upgraded["profiles"]),
        "m21_executed": False,
    }
    if not unchanged:
        raise SmokeFailure(
            "the upgraded shadow pass changed a production prediction")
    if not budget_unchanged:
        raise SmokeFailure("the upgraded pass changed Module 7's production budget")
    return result


def isolated_m18_smoke(config: dict, staged: StagedRuntimes) -> dict:
    """One real-weight Module 18 mechanism, run in isolation. **Test evidence.**

    The composed passes catalogue Module 18's eligible checks and execute none,
    which is correct: `pipeline._catalogue_bidirectional_checks` spends nothing,
    and choosing which check is worth a call is Module 20/21's job. Both are
    disabled, so `m18_natural_mechanisms_executed` is legitimately empty.

    This runs the production seam - `catalogue` -> `build_request` -> `execute`
    on the real `BidirectionalVerifier` - over a hand-declared synthetic
    consensus, so the mechanism is genuinely exercised against real weights.

    It is **isolated by construction**: it builds its own consensus object,
    never touches a production or shadow graph, and returns without writing to
    any pipeline. Nothing here can move `ObjectEntities`, Module 7's budget or a
    production stop reason.
    """
    from cover_kbc.contracts.router import compile_query
    from cover_kbc.evidence.consensus_adapters import (
        applicable_specialist, candidate_kind,
    )
    from cover_kbc.evidence.consensus_types import (
        CandidateConsensusState, QueryConsensusResult,
    )
    from cover_kbc.verification.bidirectional_verifier import BidirectionalVerifier

    # A hand-declared synthetic state. No benchmark row, no gold. Every derived
    # field comes from the production helpers, so the state Module 18 sees obeys
    # the same contract a real consensus would.
    relation, subject = "countryLandBordersCountry", "Portugal"
    _, contract = compile_query(subject, relation, 0)
    consensus = QueryConsensusResult(
        consensus_version="m16-v1", relation=relation, subject=subject,
        row_index=0, applicable_specialist=applicable_specialist(relation),
        candidates=(CandidateConsensusState(
            relation=relation, subject=subject, row_index=0,
            candidate_key="spain", display="Spain",
            candidate_kind=candidate_kind(contract),
        ),),
    )

    verifier = BidirectionalVerifier()
    catalogue = verifier.catalogue(consensus)
    eligible = [check for check in catalogue if check.eligible]
    if not eligible:
        raise SmokeFailure(
            "the isolated Module 18 state produced no eligible check; the "
            "mechanism cannot be exercised faithfully without weakening its "
            "eligibility guard"
        )

    # One mechanism, chosen deterministically from what the contract declares.
    check = eligible[0]
    request = verifier.build_request(check)

    runtime = staged.load(StagedRuntimes.ENUMERATOR)
    before = runtime.calls
    record = verifier.execute(
        request, contract, runtime,
        primary_model_family=getattr(runtime.spec, "family", ""),
    )
    calls = runtime.calls - before
    staged.release()

    if calls <= 0:
        raise SmokeFailure(
            "the isolated Module 18 check made no physical call; this is not "
            "real-weight evidence")

    # Mechanism identity belongs to the **request**, not the record: an
    # `EligibleCheck` owns `check_kind`, `BidirectionalCheckRequest` exposes it
    # as a property, and `BidirectionalCheckRecord` adds only execution
    # evidence on top of the request it carries. Read from the exact request
    # instance that was executed, so the reported mechanism cannot drift from
    # the one that actually ran.
    executed = record.request
    if executed is not request:
        raise SmokeFailure(
            "the record does not carry the request that was executed")
    return {
        "ok": True,
        "mode": "ISOLATED_CONTRACT_SMOKE",
        "relation": relation,
        "eligible_catalogue": [c.check_kind.value for c in catalogue if c.eligible],
        "mechanism_executed": request.check_kind.value,
        "model_role": request.model_role,
        "template_id": request.template_id,
        "check_version": request.check_version,
        "operation_id": request.operation_id,
        # Execution evidence, from the record's own declared fields.
        "physical_calls": calls,
        "record_calls": record.calls,
        "origin_event_id": record.origin_event_id,
        "prompt_sha256": record.prompt_sha256,
        "model_id": record.model_id,
        "model_revision": record.model_revision,
        "independence_group": record.independence_group,
        "parse_status": record.parse_status.value,
        "candidate_shown": record.candidate_shown,
        "independent_recall": record.independent_recall,
        "cross_model_eligible": record.cross_model_eligible,
        "generated_tokens": record.generated_tokens,
        "recalled_candidates": len(record.recalled_candidates),
        "error": record.error,
        "entered_production_graph": False,
        "entered_shadow_graph": False,
    }


def uncalibrated_activation_fails() -> dict:
    """Module 20/21 must still refuse to activate without TRAIN packages."""
    from cover_kbc.control.micro_planner import MicroPlannerConfig
    from cover_kbc.control.relation_budget import RelationBudgetConfig

    checks = {}
    try:
        RelationBudgetConfig.from_mapping({"enabled": True})
        checks["m20_refuses_without_calibration"] = False
    except ValueError:
        checks["m20_refuses_without_calibration"] = True
    try:
        MicroPlannerConfig.from_mapping({"enabled": True})
        checks["m21_refuses_without_packages"] = False
    except ValueError:
        checks["m21_refuses_without_packages"] = True
    if not all(checks.values()):
        raise SmokeFailure(
            f"uncalibrated Module 20/21 activation did not fail loudly: {checks}")
    return checks


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=Path("configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml"))
    parser.add_argument(
        "--out", type=Path,
        default=Path("real_model_architecture_smoke_summary.json"))
    parser.add_argument(
        "--memory-mode", choices=("staged",), default="staged",
        help="staged is the only mode: one checkpoint resident at a time, on "
             "every GPU size")
    parser.add_argument(
        "--work-dir", type=Path, default=None,
        help="where staged intermediate state is written")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text())
    enumerator_spec, verifier_spec = model_blocks(config)
    work_dir = args.work_dir or args.out.parent / "real_model_smoke_stage"
    work_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "smoke": "real-model-architecture-smoke-v1",
        "memory_mode": args.memory_mode,
        "repo_sha": repo_sha(),
        "config_path": str(args.config),
        "config_sha256": _sha(args.config),
        "models": {
            "enumerator": {
                "model_id": enumerator_spec["model_id"],
                "revision": enumerator_spec.get("revision"),
                "tokenizer_backend": enumerator_spec.get("tokenizer_backend"),
                "quantization": enumerator_spec.get("quantization"),
            },
            "verifier": {
                "model_id": verifier_spec["model_id"],
                "revision": verifier_spec.get("revision"),
                "tokenizer_backend": verifier_spec.get("tokenizer_backend"),
                "quantization": verifier_spec.get("quantization"),
            },
        },
        "benchmark_data_read": False,
        "factual_scoring_performed": False,
        "errors": [],
    }

    staged = StagedRuntimes(enumerator_spec, verifier_spec)
    phase, active_role = "startup", None
    try:
        # -- PHASE E1 primitive ---------------------------------------------
        phase, active_role = "E1_enumerator_primitive", StagedRuntimes.ENUMERATOR
        print("[E1] loading enumerator ...", flush=True)
        runtime = staged.load(StagedRuntimes.ENUMERATOR)
        print("[E1] primitive generate ...", flush=True)
        summary["primitive_generate"] = primitive_generate(runtime, enumerator_spec)
        staged.release()

        # -- PHASE V primitive + M17 ----------------------------------------
        phase, active_role = "V_verifier_primitive", StagedRuntimes.VERIFIER
        print("[V] loading verifier ...", flush=True)
        runtime = staged.load(StagedRuntimes.VERIFIER)
        print("[V] primitive score_labels ...", flush=True)
        summary["primitive_score_labels"] = primitive_score_labels(
            runtime, verifier_spec)

        # Qwen stays resident across cold AND warm: unloading between them
        # would reset the contextual-control cache and destroy the experiment.
        phase = "V_m17_cold_warm"
        print("[V] Module 17 cold/warm regression ...", flush=True)
        summary["m17_call_plan"] = m17_plan_regression(config, runtime)
        summary["m20_consistency"] = m20_consistency(summary["m17_call_plan"])
        staged.release()

        # -- Sequential passes, never simultaneous --------------------------
        phase, active_role = "core_pass", None
        print("[pass 1/2] production core, staged ...", flush=True)
        core = staged_pass(config, staged, work_dir, upgraded=False, tag="core")

        phase = "upgraded_pass"
        print("[pass 2/2] upgraded shadow, staged ...", flush=True)
        upgraded = staged_pass(
            config, staged, work_dir, upgraded=True, tag="upgraded")

        phase = "compare"
        summary["composed"] = compare_passes(core, upgraded)

        # Natural coverage first; the isolated contract smoke only runs when
        # the composed passes genuinely executed no mechanism.
        natural = summary["composed"]["m18_mechanisms_executed"]
        summary["m18_natural_mechanisms_executed"] = natural
        if natural:
            summary["m18_isolated_contract_mechanisms_executed"] = []
            summary["m18_isolated"] = None
        else:
            phase, active_role = "m18_isolated", StagedRuntimes.ENUMERATOR
            print("[M18] no natural mechanism fired; isolated contract smoke ...",
                  flush=True)
            isolated = isolated_m18_smoke(config, staged)
            summary["m18_isolated"] = isolated
            summary["m18_isolated_contract_mechanisms_executed"] = [
                isolated["mechanism_executed"]]
        summary["m18_real_weight_coverage"] = bool(
            summary["m18_natural_mechanisms_executed"]
            or summary["m18_isolated_contract_mechanisms_executed"])
        summary["staged_roles_observed"] = staged.history
        summary["shared_profile"] = staged.shared_profile

        phase = "activation_guard"
        print("[guard] uncalibrated activation must fail ...", flush=True)
        summary["uncalibrated_activation"] = uncalibrated_activation_fails()

        summary["result"] = "PASS"
    except Exception as error:                          # noqa: BLE001
        # Caught only here, at the outermost boundary. Nothing is retried with a
        # smaller model, a different quantization, CPU offload or another
        # architecture: the profile is frozen and the user switches GPU.
        name = type(error).__name__
        message = str(error)
        oom = "OutOfMemoryError" in name or "CUDA out of memory" in message
        summary["result"] = "FAIL"
        summary["errors"].append({
            "type": "CUDA_OOM" if oom else name,
            "message": message,
            "phase": phase,
            "active_role": active_role or staged.role,
            "active_model_id": staged.model_id,
            "traceback": traceback.format_exc(),
        })
        print(f"\nSMOKE FAILED in {phase}: {name}: {message}", flush=True)

    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nresult: {summary['result']}")
    print(f"summary written to {args.out}")
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
