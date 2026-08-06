"""Consolidated offline architecture smoke for M0-M21.

Exercises every layer that can run without real weights, over all six official
relations, and emits an **architecture trace**: which module produced state for
which query, under which owner, and what it cost.

This is not a benchmark run and not an evaluation. Subjects are fictional, the
runtime is scripted, Module 20 and Module 21 use ``SYNTHETIC_TEST`` packages,
and the action Module 21 selects is **never executed**.

    python scripts/architecture_smoke.py [--json PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cover_kbc.contracts.registry import CONTRACTS  # noqa: E402
from cover_kbc.control import (  # noqa: E402
    ActionFamily,
    CalibrationSource,
    EstimateSource,
    HistoricalActionBin,
    HistoricalBinPackage,
    Layer6Integrator,
    MicroPlanner,
    PlannerCalibration,
    RelationBudgetCalibration,
    RelationBudgetScheduler,
    StateBinningSpec,
    relation_policy,
)
from cover_kbc.coverage_gap.missingness import CoverageGapEstimator  # noqa: E402
from cover_kbc.evidence.consensus import AtomicConsensusEngine  # noqa: E402
from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator  # noqa: E402
from cover_kbc.models.offline import ScriptedRuntime  # noqa: E402
from cover_kbc.pipeline import CoverPipeline, PipelineConfig  # noqa: E402
from cover_kbc.query_intelligence import (  # noqa: E402
    ParametricRetriever,
    PromptProgramCompiler,
    QueryProfiler,
)
from cover_kbc.specialists import (  # noqa: E402
    LargeSetSpecialist,
    NullTemporalSpecialist,
    NumericSpecialist,
    SmallSetSpecialist,
)
from cover_kbc.types import Query  # noqa: E402

#: Fictional subjects. No benchmark row is read and no gold is inspected.
SUBJECTS = {
    "countryLandBordersCountry": "Country Alpha",
    "personHasCityOfDeath": "Person Alpha of Examplestan",
    "hasCapacity": "Example Municipal Stadium",
    "hasArea": "Example Northern Region",
    "awardWonBy": "Aurora Prize for Invention",
    "companyTradesAtStockExchange": "Example Holdings Group",
}

#: Which specialist owns each relation, per Audit 0028 §9A.
SPECIALIST_OWNER = {
    "hasCapacity": "M12", "hasArea": "M12", "awardWonBy": "M13",
    "personHasCityOfDeath": "M14", "countryLandBordersCountry": "M15",
    "companyTradesAtStockExchange": "M15",
}


def synthetic_budget(relation: str) -> RelationBudgetCalibration:
    """A fictional Module 20 calibration. **Not** a production budget."""
    policy = relation_policy(relation)
    return RelationBudgetCalibration(
        relation=relation, calibration_version="smoke-fixture-v1",
        calibration_source=CalibrationSource.SYNTHETIC_TEST,
        hard_calls=12, hard_generated_tokens=40000, discovery_cap=8,
        verification_cap=8, verification_reserve=2,
        special_reserves=tuple((p, 1) for p in policy.special_reserve_purposes),
    )


def synthetic_history() -> HistoricalBinPackage:
    """Fictional Module 21 bins. **Not** TRAIN-calibrated."""
    return HistoricalBinPackage(
        history_version="smoke-fixture-v1", source=EstimateSource.SYNTHETIC_TEST,
        binning=StateBinningSpec(
            spec_version="smoke-binning-v1",
            categorical_features=("program_type",)),
        bins=tuple(
            HistoricalActionBin(
                relation=relation,
                program_type=CONTRACTS[relation].program_type.value,
                state_bin_key=(
                    f"program_type={CONTRACTS[relation].program_type.value}"),
                action_family=family, support_count=5,
                expected_verified_gain=1.0, expected_delta_r=0.1,
                expected_delta_h=0.1, expected_cost=1.0,
                expected_redundancy=0.1, expected_fp=0.1,
            )
            for relation in SUBJECTS for family in ActionFamily
        ),
    )


def synthetic_planner_calibration() -> PlannerCalibration:
    """Fictional §17 coefficients. **Not** TRAIN-calibrated."""
    return PlannerCalibration(
        calibration_version="smoke-fixture-v1", source=EstimateSource.SYNTHETIC_TEST,
        alpha=1.0, beta=1.0, gamma=1.0, delta=1.0, eta=1.0, kappa=1.0,
        tau_continue=0.0, lookahead_depth=1,
    )


def build_pipeline(runtime, *, upgraded: bool):
    """The full stack, or the production core alone."""
    if not upgraded:
        return CoverPipeline(runtime, PipelineConfig())
    planner = MicroPlanner(synthetic_history(), synthetic_planner_calibration())
    return CoverPipeline(
        runtime, PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), numeric_specialist=NumericSpecialist(),
        large_set_specialist=LargeSetSpecialist(),
        null_temporal_specialist=NullTemporalSpecialist(),
        small_set_specialist=SmallSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        layer4_integrator=Layer4EvidenceIntegrator(),
        coverage_gap_estimator=CoverageGapEstimator(),
        relation_budget_scheduler=RelationBudgetScheduler(
            {r: synthetic_budget(r) for r in SUBJECTS}),
        micro_planner=planner, layer6_integrator=Layer6Integrator(planner),
    )


def run(*, upgraded: bool):
    """One scripted pass over all six relations. Returns (pipeline, trace)."""
    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    pipeline = build_pipeline(runtime, upgraded=upgraded)
    predictions = []
    for index, relation in enumerate(SUBJECTS):
        graph = pipeline.enumerate_query(
            Query(SUBJECTS[relation], relation, index))
        predictions.append(pipeline.decide_graph(graph))
    return pipeline, predictions, runtime.calls


def architecture_trace(pipeline, predictions) -> dict:
    """Module presence and ownership, read from what actually ran."""
    per_relation = []
    for index, relation in enumerate(SUBJECTS):
        prediction = predictions[index]
        layer6 = next(
            (s for s in pipeline.layer6_results if s.relation == relation), None)
        gap = next(
            (g for g in pipeline.coverage_gap_results if g.relation == relation),
            None)
        budget = next(
            (b for b in pipeline.relation_budget_results
             if b.relation == relation), None)
        per_relation.append({
            "Relation": relation,
            "SubjectEntity": SUBJECTS[relation],
            "program_type": CONTRACTS[relation].program_type.value,
            "specialist_owner": SPECIALIST_OWNER[relation],
            "M9_profile": any(
                p.relation == relation for p in pipeline.query_profiles),
            "M10_program": any(
                p.relation == relation for p in pipeline.prompt_programs),
            "M11_retrieval": any(
                r.plan.relation == relation for r in pipeline.retrieval_results),
            "M16_consensus": any(
                c.relation == relation for c in pipeline.consensus_results),
            "layer4": any(
                s.relation == relation for s in pipeline.layer4_results),
            "M19_residual": (
                gap.residual.residual if gap and gap.residual else None),
            "M20_numeric_plan": bool(budget and budget.plan.is_numeric),
            "L6_legal_actions": len(layer6.legal_actions) if layer6 else 0,
            "L6_affordable": len(layer6.affordable_actions) if layer6 else 0,
            "L6_denied": len(layer6.denied_actions) if layer6 else 0,
            "M21_decision": (
                layer6.decision.kind.value if layer6 and layer6.decision else None),
            "M21_stop_reason": (
                layer6.decision.stop_reason.value
                if layer6 and layer6.decision and layer6.decision.stop_reason
                else None),
            "M8_objects": len(prediction.object_entities),
            "M8_stopped_reason": prediction.stopped_reason,
        })
    return {
        "smoke": "architecture-smoke-v1",
        "relations": per_relation,
        "modules_present": {
            "M0_M1_contracts": len(CONTRACTS),
            "M9": len(pipeline.query_profiles),
            "M10": len(pipeline.prompt_programs),
            "M11": len(pipeline.retrieval_results),
            "M12": len(pipeline.numeric_results),
            "M13": len(pipeline.large_set_results),
            "M14": len(pipeline.null_temporal_results),
            "M15": len(pipeline.small_set_results),
            "M16": len(pipeline.consensus_results),
            "Layer4": len(pipeline.layer4_results),
            "M19": len(pipeline.coverage_gap_results),
            "M20": len(pipeline.relation_budget_results),
            "M21": len(pipeline.micro_planner_results),
            "Layer6": len(pipeline.layer6_results),
        },
        "no_execution": (
            "No Module 21 decision was executed. Module 7 remains the production "
            "controller and Module 8 the finaliser."
        ),
        "calibration": (
            "Module 20 and Module 21 ran on SYNTHETIC_TEST packages. No TRAIN "
            "calibration exists and none was performed."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None,
                        help="write the architecture trace here")
    args = parser.parse_args()

    upgraded, upgraded_predictions, upgraded_calls = run(upgraded=True)
    core, core_predictions, core_calls = run(upgraded=False)

    trace = architecture_trace(upgraded, upgraded_predictions)
    trace["accounting"] = {
        "production_core_calls": core_calls,
        "full_stack_calls": upgraded_calls,
        "shadow_neural_calls": upgraded_calls - core_calls,
        "note": (
            "The difference is shadow neural spend by M11/M12-M15/M17/M18, not "
            "production Module 7 budget. The non-neural upgraded modules - M16 "
            "integration, M19, M20, M21 and Layer 6 - add zero calls."
        ),
    }
    core_objects = [tuple(p.object_entities) for p in core_predictions]
    upgraded_objects = [tuple(p.object_entities) for p in upgraded_predictions]
    trace["production_invariance"] = {
        "predictions_identical": core_objects == upgraded_objects,
        "core_objects": [list(o) for o in core_objects],
    }

    print("COVER-KBC consolidated architecture smoke (offline, fictional)")
    print("=" * 70)
    for row in trace["relations"]:
        print(
            f"{row['Relation']:30} {row['program_type']:15} "
            f"owner={row['specialist_owner']} "
            f"L6 legal={row['L6_legal_actions']:2} "
            f"afford={row['L6_affordable']:2} "
            f"-> {row['M21_decision']}"
            f"{'/' + row['M21_stop_reason'] if row['M21_stop_reason'] else ''}"
            f"  M8 objects={row['M8_objects']}"
        )
    print("-" * 70)
    print("modules present:", json.dumps(trace["modules_present"]))
    print("accounting:", json.dumps(trace["accounting"]["production_core_calls"]),
          "core calls vs", trace["accounting"]["full_stack_calls"], "full-stack;",
          trace["accounting"]["shadow_neural_calls"], "shadow neural calls")
    print("production predictions identical:",
          trace["production_invariance"]["predictions_identical"])
    print(trace["no_execution"])
    print(trace["calibration"])

    if args.json:
        args.json.write_text(json.dumps(trace, indent=2), encoding="utf-8")
        print(f"\ntrace written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
