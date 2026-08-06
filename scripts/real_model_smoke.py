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
    from cover_kbc.verification.blind import LABEL_TOKENS

    # The audited A/B/C surface tokens, read from Module 4 rather than retyped.
    labels = tuple(LABEL_TOKENS[name] for name in ("VALID", "INVALID", "UNKNOWN"))
    prompt = (
        "Statement: Lisbon is the capital of Portugal.\n"
        "A) VALID  B) INVALID  C) UNKNOWN\nAnswer:"
    )
    request = LabelScoreRequest(
        prompt=prompt, labels=labels,
        metadata={"smoke": "primitive_score_labels"},
    )
    result = runtime.score_labels(request)
    if result.error:
        raise SmokeFailure(f"verifier score_labels failed: {result.error}")
    logits = list(result.logits)
    if len(logits) != len(labels):
        raise SmokeFailure(
            f"score_labels returned {len(logits)} logits for {len(labels)} labels")
    import math

    if not all(math.isfinite(v) for v in logits):
        raise SmokeFailure(f"score_labels returned non-finite logits: {logits}")
    top = max(logits)
    exps = [math.exp(v - top) for v in logits]
    total = sum(exps)
    probabilities = [v / total for v in exps]
    if abs(sum(probabilities) - 1.0) > 1e-6:
        raise SmokeFailure("label probabilities do not normalise")
    return {
        "ok": True,
        "model_id": result.model_id,
        "expected_model_id": spec["model_id"],
        "revision": spec.get("revision"),
        "tokenizer_backend": spec.get("tokenizer_backend"),
        "labels": list(labels),
        "logits_finite": True,
        "probabilities_normalise": True,
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


def build_pipeline(config: dict, runtime: Any, verifier_runtime: Any, *,
                   upgraded: bool):
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
        return CoverPipeline(runtime, pipeline_config,
                             verifier_runtime=verifier_runtime)

    verifier_block = dict(config.get("specialist_verifier") or {})
    verifier_block["enabled"] = True
    return CoverPipeline(
        runtime, pipeline_config, verifier_runtime=verifier_runtime,
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
        # Module 20, Module 21 and Layer 6 stay OFF: they have no TRAIN
        # calibration and a synthetic package would not prove production
        # readiness.
    )


def composed_smoke(config: dict, runtime: Any, verifier_runtime: Any) -> dict:
    """Run the manifest in production-core and upgraded-shadow mode."""
    results: dict[str, Any] = {"queries": []}

    core_before = runtime.calls + verifier_runtime.calls
    core = build_pipeline(config, runtime, verifier_runtime, upgraded=False)
    core_predictions = [
        core.decide_graph(core.enumerate_query(Query(subject, relation, index)))
        for index, (relation, subject) in enumerate(SMOKE_MANIFEST)
    ]
    core_calls = runtime.calls + verifier_runtime.calls - core_before

    upgraded_before = runtime.calls + verifier_runtime.calls
    upgraded = build_pipeline(config, runtime, verifier_runtime, upgraded=True)
    upgraded_predictions = [
        upgraded.decide_graph(
            upgraded.enumerate_query(Query(subject, relation, index)))
        for index, (relation, subject) in enumerate(SMOKE_MANIFEST)
    ]
    upgraded_calls = runtime.calls + verifier_runtime.calls - upgraded_before

    for index, (relation, subject) in enumerate(SMOKE_MANIFEST):
        profile = next(
            (p for p in upgraded.query_profiles
             if p.relation == relation and p.subject == subject), None)
        results["queries"].append({
            "Relation": relation,
            "SubjectEntity": subject,
            "specialist_family": SPECIALIST_FAMILY[relation],
            "program_type": CONTRACTS[relation].program_type.value,
            # Audit 0033 §10A: the persisted profile must be the refined one.
            "m9_refined": bool(profile and profile.novelty_basis
                               != "no early graph has been observed"),
            "m9_novelty_risk": (
                profile.novelty_risk.value
                if profile and profile.novelty_risk else None),
            "m9_secondary_hints": (
                [h.value for h in profile.secondary_hints] if profile else []),
            "m16_consensus": any(
                c.relation == relation for c in upgraded.consensus_results),
            "layer4": any(
                s.relation == relation for s in upgraded.layer4_results),
            "m19_residual": next(
                (g.residual.residual for g in upgraded.coverage_gap_results
                 if g.relation == relation), None),
            "object_count": len(upgraded_predictions[index].object_entities),
        })

    core_objects = [tuple(p.object_entities) for p in core_predictions]
    upgraded_objects = [tuple(p.object_entities) for p in upgraded_predictions]
    results["production_core_calls"] = core_calls
    results["upgraded_shadow_calls"] = upgraded_calls
    results["shadow_only_calls"] = upgraded_calls - core_calls
    results["production_output_unchanged"] = core_objects == upgraded_objects
    results["m7_budget_unchanged"] = all(
        left.calls_used == right.calls_used
        and left.generated_tokens_used == right.generated_tokens_used
        for left, right in zip(core_predictions, upgraded_predictions))
    results["production_stop_reasons_unchanged"] = all(
        left.stopped_reason == right.stopped_reason
        for left, right in zip(core_predictions, upgraded_predictions))
    results["m18_mechanisms_executed"] = sorted({
        check.check_kind
        for state in upgraded.layer4_results
        for overlay in state.candidates
        for check in overlay.structural_checks
    })
    results["m21_executed"] = False
    results["specialist_families"] = sorted(
        {SPECIALIST_FAMILY[relation] for relation, _ in SMOKE_MANIFEST})

    if not results["production_output_unchanged"]:
        raise SmokeFailure(
            "the upgraded shadow run changed a production prediction; shadow "
            "calls must not perturb production output")
    if not results["m7_budget_unchanged"]:
        raise SmokeFailure("the upgraded run changed Module 7's production budget")
    return results


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
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text())
    enumerator_spec, verifier_spec = model_blocks(config)

    summary: dict[str, Any] = {
        "smoke": "real-model-architecture-smoke-v1",
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

    try:
        print("[1/6] loading enumerator runtime ...", flush=True)
        runtime = build_runtime(config)
        print("[1/6] primitive generate ...", flush=True)
        summary["primitive_generate"] = primitive_generate(runtime, enumerator_spec)

        print("[2/6] loading verifier runtime ...", flush=True)
        verifier_runtime = build_runtime(
            {**config, "model_profile": {
                **config["model_profile"],
                "enumerator": config["model_profile"]["verifier"]}})
        print("[2/6] primitive score_labels ...", flush=True)
        summary["primitive_score_labels"] = primitive_score_labels(
            verifier_runtime, verifier_spec)

        print("[3/6] Module 17 live call-plan regression ...", flush=True)
        summary["m17_call_plan"] = m17_plan_regression(config, verifier_runtime)

        print("[4/6] Module 20 safe-cost consistency (non-executing) ...", flush=True)
        summary["m20_consistency"] = m20_consistency(summary["m17_call_plan"])

        print("[5/6] composed shadow smoke ...", flush=True)
        summary["composed"] = composed_smoke(config, runtime, verifier_runtime)

        print("[6/6] uncalibrated activation must fail ...", flush=True)
        summary["uncalibrated_activation"] = uncalibrated_activation_fails()

        summary["result"] = "PASS"
    except Exception as error:                          # noqa: BLE001
        summary["result"] = "FAIL"
        summary["errors"].append(
            {"type": type(error).__name__, "message": str(error),
             "traceback": traceback.format_exc()})
        print(f"\nSMOKE FAILED: {type(error).__name__}: {error}", flush=True)

    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nresult: {summary['result']}")
    print(f"summary written to {args.out}")
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
