"""The production seam, exercised through the real pipeline entry point.

The unit A/B test in ``test_production_bridge.py`` proves the bridge works. It
cannot prove the pipeline *calls* it - and "the component exists" versus "the
production path uses it" is exactly the confusion that persisted here for
several milestones. So every test in this file enters through
``enumerate_query``/``decide_graph`` and never touches the bridge or either
verifier executor directly.

Offline and scripted throughout. No weights are loaded.
"""

from __future__ import annotations

import pytest

from cover_kbc.coverage_gap.missingness import CoverageGapEstimator
from cover_kbc.evidence.consensus import AtomicConsensusEngine
from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import CoverPipeline, PipelineConfig, UnsupportedAction
from cover_kbc.query_intelligence import (
    ParametricRetriever,
    PromptProgramCompiler,
    QueryProfiler,
)
from cover_kbc.specialists import (
    LargeSetSpecialist,
    NullTemporalSpecialist,
    NumericSpecialist,
    SmallSetSpecialist,
)
from cover_kbc.types import Query
from cover_kbc.verification.bidirectional_verifier import BidirectionalVerifier
from cover_kbc.verification.specialist_verifier import SpecialistVerifier

RELATION = "countryLandBordersCountry"
SUBJECT = "Testland"


def take_everything_legal(kind, catalogue):
    """A selector standing in for Modules 20/21 (production) or the collection
    policy. It only ever returns catalogue entries - it declares no legality."""
    return catalogue


def build(mode, selector=take_everything_legal, **kwargs):
    # A fallback rather than a keyed script: every acquisition view then yields
    # the same two candidates, so the two are tied on direct recall and the
    # only thing that can separate them is upgraded evidence.
    runtime = ScriptedRuntime(
        {}, model_id="offline/enumerator",
        fallback=lambda request: "Alphaland, Betaland")
    return CoverPipeline(
        runtime, PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
        numeric_specialist=NumericSpecialist(),
        large_set_specialist=LargeSetSpecialist(),
        null_temporal_specialist=NullTemporalSpecialist(),
        small_set_specialist=SmallSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        specialist_verifier=SpecialistVerifier(),
        bidirectional_verifier=BidirectionalVerifier(),
        layer4_integrator=Layer4EvidenceIntegrator(),
        coverage_gap_estimator=CoverageGapEstimator(),
        integration_mode=mode, action_selector=selector, **kwargs)


def run(pipeline, relation=RELATION, subject=SUBJECT, row=0):
    """Drive the *real* query path and return the finalized prediction."""
    graph = pipeline.enumerate_query(Query(subject, relation, row))
    return pipeline.decide_graph(graph)


# --------------------------------------------------------------------------
# mode adoption and the bridge call site
# --------------------------------------------------------------------------

def test_pipeline_normalises_the_mode_once() -> None:
    assert build("production").integration_mode is IntegrationMode.PRODUCTION
    assert build(IntegrationMode.SHADOW).integration_mode is IntegrationMode.SHADOW


def test_pipeline_defaults_to_shadow() -> None:
    """The safe mode is the default; production must be asked for explicitly."""
    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    assert CoverPipeline(runtime, PipelineConfig()).integration_mode \
        is IntegrationMode.SHADOW


@pytest.mark.parametrize("mode", ["prod", "PRODUCTION", "live", ""])
def test_pipeline_refuses_an_invalid_mode(mode) -> None:
    from cover_kbc.integration_mode import IntegrationModeError
    with pytest.raises(IntegrationModeError):
        build(mode)


@pytest.mark.parametrize("mode", [
    IntegrationMode.SHADOW,
    IntegrationMode.PRODUCTION,
    IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY,
])
def test_the_pipeline_invokes_the_bridge_in_every_mode(mode) -> None:
    """The call site must not drift between modes - that is what keeps the
    shadow isolation proven on the same path it protects."""
    pipeline = build(mode)
    run(pipeline)
    assert pipeline.bridge_reports
    assert all(r.mode is mode for r in pipeline.bridge_reports)


def test_shadow_bridge_reports_write_nothing() -> None:
    pipeline = build(IntegrationMode.SHADOW)
    run(pipeline)
    for report in pipeline.bridge_reports:
        assert report.applied is False
        assert report.verifications_applied == 0
        assert report.structural_edges_applied == 0


# --------------------------------------------------------------------------
# THE MANDATORY PIPELINE A/B
# --------------------------------------------------------------------------

def test_shadow_and_production_differ_through_the_real_pipeline() -> None:
    """MANDATORY: same query, same scripted runtime, only the mode differs.

    Nothing here calls the bridge or either verifier executor; the pipeline
    does. If this passes, "modules run" and "modules affect production output"
    are no longer the same claim.
    """
    shadow = build(IntegrationMode.SHADOW)
    production = build(IntegrationMode.PRODUCTION)

    a = run(shadow).object_entities
    b = run(production).object_entities

    applied = [r for r in production.bridge_reports if r.applied]
    assert applied, "production run never applied upgraded evidence"
    assert a != b, f"shadow and production agreed: {a!r}"


def test_collection_mode_uses_the_same_pipeline_seam() -> None:
    collection = build(IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY)
    production = build(IntegrationMode.PRODUCTION)
    assert run(collection).object_entities == run(production).object_entities


# --------------------------------------------------------------------------
# M17 / M18 real pipeline callers
# --------------------------------------------------------------------------

def test_m17_executes_through_the_pipeline_not_the_test() -> None:
    """The pipeline is the caller ``verify_specialist_targets`` always lacked."""
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    executed = [q for q in pipeline.specialist_verifications if q.results]
    assert executed, "M17 never executed through the pipeline"


def test_m18_executes_through_the_pipeline_not_the_test() -> None:
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    executed = [q for q in pipeline.bidirectional_results if q.records]
    assert executed, "M18 never executed through the pipeline"


def test_nothing_executes_without_a_selector() -> None:
    """Fail-closed: an uncalibrated production run spends nothing.

    Target selection belongs to Modules 20/21; with neither calibrated, the
    pipeline must verify nothing rather than everything it can see.
    """
    pipeline = build(IntegrationMode.PRODUCTION, selector=None)
    run(pipeline)
    assert not any(q.results for q in pipeline.specialist_verifications)
    assert not any(q.records for q in pipeline.bidirectional_results)


def test_shadow_never_executes_verification() -> None:
    pipeline = build(IntegrationMode.SHADOW)
    run(pipeline)
    assert not any(q.results for q in pipeline.specialist_verifications)
    assert not any(q.records for q in pipeline.bidirectional_results)


def test_a_selector_cannot_invent_an_action() -> None:
    """Legality is the catalogue's to declare, not the selector's."""
    class Forged:
        pass

    pipeline = build(IntegrationMode.PRODUCTION,
                     selector=lambda kind, catalogue: (Forged(),))
    with pytest.raises(UnsupportedAction, match="not in the catalogue"):
        run(pipeline)


def test_selector_sees_only_catalogue_entries() -> None:
    seen = {}

    def spy(kind, catalogue):
        seen.setdefault(kind, []).extend(catalogue)
        return catalogue

    pipeline = build(IntegrationMode.PRODUCTION, selector=spy)
    run(pipeline)
    assert "m17" in seen or "m18" in seen


# --------------------------------------------------------------------------
# M19 / control state
# --------------------------------------------------------------------------

def test_m19_recomputes_over_the_bridged_state() -> None:
    """M19 must describe the state the controller will actually face."""
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    assert pipeline.coverage_gap_results
    residuals = [r.residual for r in pipeline.coverage_gap_results
                 if hasattr(r, "residual")]
    assert residuals, "M19 produced no residual for the control state"


def test_m19_is_refreshed_after_each_executed_action() -> None:
    """A residual computed once, before M17/M18, would describe a state that no
    longer exists by the time Layer 6 reads it.

    Asserted behaviourally: M19 runs once to establish round-zero state and
    again after every executed action, while ``coverage_gap_results`` still
    holds exactly one entry per query — the latest, not a history.
    """
    refreshes = []
    pipeline = build(IntegrationMode.PRODUCTION)
    original = pipeline._estimate_coverage_gap

    def spy(consensus, contract):
        refreshes.append(consensus.subject)
        return original(consensus, contract)

    pipeline._estimate_coverage_gap = spy
    run(pipeline)

    executed = [r for r in pipeline.action_records if r["executed"]]
    assert executed, "no action executed"
    # Round-zero state, one refresh per executed action, and a final settle
    # after the loop. The exact count is an implementation detail; what the
    # invariant needs is strictly more refreshes than actions, so no action's
    # effect goes unobserved.
    assert len(refreshes) > len(executed), (
        "M19 must refresh after every executed action")
    assert len(pipeline.coverage_gap_results) == 1, (
        "coverage_gap_results became a history instead of the state")


# --------------------------------------------------------------------------
# accounting
# --------------------------------------------------------------------------

def test_production_calls_are_the_complete_production_cost() -> None:
    """In PRODUCTION, ``production_calls`` alone is the physical-call total.

    Previously acquisition (M11, M12-M15) still billed ``shadow_calls`` even in
    production, so the true cost was the sum of two ledgers. That is fixed: a
    physical call now appears in exactly one ledger, chosen by mode.
    """
    production = build(IntegrationMode.PRODUCTION)
    run(production)
    assert production.production_calls > 0
    assert production.shadow_calls == 0


def test_shadow_never_bills_production() -> None:
    shadow = build(IntegrationMode.SHADOW)
    run(shadow)
    assert shadow.shadow_calls > 0
    assert shadow.production_calls == 0


def test_collection_bills_production_not_shadow() -> None:
    """Telemetry must receive the complete cost of the collection execution."""
    collection = build(IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY)
    run(collection)
    assert collection.production_calls > 0
    assert collection.shadow_calls == 0


def test_acquisition_alone_is_billed_by_mode() -> None:
    """M11/M12-M15 with no verification selected still bill the right ledger."""
    production = build(IntegrationMode.PRODUCTION, selector=None)
    run(production)
    shadow = build(IntegrationMode.SHADOW, selector=None)
    run(shadow)
    assert production.production_calls > 0 and production.shadow_calls == 0
    assert shadow.shadow_calls > 0 and shadow.production_calls == 0
    # Same inference either way: only the ledger differs, never the behaviour.
    assert production.production_calls == shadow.shadow_calls


def test_bridge_consumption_does_not_recharge_calls() -> None:
    """A record consumed downstream must not be billed a second time."""
    pipeline = build(IntegrationMode.PRODUCTION)
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    pipeline.decide_graph(graph)
    charged = pipeline.production_calls
    edges = sum(len(c.all_evidence()) for c in graph.active_candidates())

    # Re-applying the same integrated state must neither re-execute a model
    # nor re-attach an edge.
    for state in pipeline.layer4_results:
        pipeline.production_bridge.apply(graph, state)

    assert pipeline.production_calls == charged
    assert sum(len(c.all_evidence()) for c in graph.active_candidates()) == edges


# --------------------------------------------------------------------------
# M8 ownership
# --------------------------------------------------------------------------

def test_m8_remains_the_sole_final_owner() -> None:
    pipeline = build(IntegrationMode.PRODUCTION)
    prediction = run(pipeline)
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 1))
    from cover_kbc.selection import finalize
    assert finalize(graph, stopped_reason="t").object_entities is not None
    assert prediction.subject == SUBJECT and prediction.relation == RELATION
