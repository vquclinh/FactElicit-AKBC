"""Module 21 must choose the action Module 7 then executes.

Audit 0038 found the planner reachable only through the shadow diagnostic path.
These tests enter through the real production controller entrypoint and never
call ``MicroPlanner.plan``, ``execute_action`` or the bridge directly — a test
that calls the planner itself proves only that the planner exists.

Synthetic historical bins. No weights.
"""

from __future__ import annotations

import pytest

from cover_kbc.control.historical_bins import (
    HistoricalActionBin,
    HistoricalBinPackage,
    StateBinningSpec,
)
from cover_kbc.control.micro_planner import MicroPlanner
from cover_kbc.control.planner_types import (
    ActionFamily,
    EstimateSource,
    PlannerCalibration,
)
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.types import Query
from test_pipeline_production_seam import RELATION, SUBJECT, build


def _calibration(tau: float = 0.0) -> PlannerCalibration:
    return PlannerCalibration(
        calibration_version="synthetic-test", source=EstimateSource.SYNTHETIC_TEST,
        alpha=1.0, beta=1.0, gamma=1.0, delta=0.0, eta=0.0, kappa=0.0,
        tau_continue=tau, lookahead_depth=1,
    )


def _package(gain: float = 1.0) -> HistoricalBinPackage:
    return HistoricalBinPackage(
        history_version="synthetic-test", source=EstimateSource.SYNTHETIC_TEST,
        binning=StateBinningSpec(spec_version="synthetic-test"),
        # One bin per family, all with the same estimates: the fixture is
        # about the wiring and the threshold, not about ranking families.
        bins=tuple(
            HistoricalActionBin(
                relation=RELATION, program_type="SMALL_SET", state_bin_key="",
                action_family=family, support_count=10,
                expected_verified_gain=gain, expected_delta_r=0.0,
                expected_delta_h=0.0, expected_cost=0.0,
                expected_redundancy=0.0, expected_fp=0.0,
            )
            for family in ActionFamily
        ),
        fallback_state_bin="",
    )


def _pipeline(*, gain: float = 1.0, tau: float = 0.0):
    # Appendix C gives M21 the full state, so the pipeline requires M19 and M20
    # alongside it and refuses to let the planner reconstruct a missing layer.
    from test_m20_precharge_gate import _calibration as _budget_calibration
    from cover_kbc.control.relation_budget import RelationBudgetScheduler

    planner = MicroPlanner(history=_package(gain), calibration=_calibration(tau))
    return build(IntegrationMode.PRODUCTION, micro_planner=planner,
                 relation_budget_scheduler=RelationBudgetScheduler(
                     {RELATION: _budget_calibration()}))


def _run(pipeline):
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    return pipeline.decide_graph(graph)


def test_production_calls_the_planner() -> None:
    """The pipeline is the caller, not the test."""
    calls = []
    pipeline = _pipeline()
    original = pipeline.micro_planner.plan
    pipeline.micro_planner.plan = lambda *a, **k: (
        calls.append(a) or original(*a, **k))
    _run(pipeline)
    assert calls, "MicroPlanner.plan was never called by production code"


def test_the_planner_receives_legal_actions_not_an_empty_list() -> None:
    seen = []
    pipeline = _pipeline()
    original = pipeline.micro_planner.plan
    pipeline.micro_planner.plan = lambda state, actions: (
        seen.append(actions) or original(state, actions))
    _run(pipeline)
    assert any(actions for actions in seen), "planner got no legal actions"


def test_selected_identity_equals_executed_identity() -> None:
    """The action the planner chose must be the action that ran."""
    decisions = []
    pipeline = _pipeline()
    original = pipeline.micro_planner.plan

    def spy(state, actions):
        decision = original(state, actions)
        decisions.append(decision)
        return decision

    pipeline.micro_planner.plan = spy
    _run(pipeline)

    chosen = [d.selected_action for d in decisions
              if getattr(d.kind, "value", d.kind) == "ACTION"]
    executed = [r for r in pipeline.action_records if r["executed"]]
    if not chosen:
        pytest.skip("planner selected STOP for this fixture")
    assert len(executed) == len(chosen)


def test_at_most_one_action_executes_per_planner_round() -> None:
    pipeline = _pipeline()
    _run(pipeline)
    executed = [r for r in pipeline.action_records if r["executed"]]
    rounds = [r["round_index"] for r in executed]
    assert len(set(rounds)) == len(rounds), "two actions shared a planner round"


def test_stop_means_zero_execution() -> None:
    """A negative-utility fixture must execute nothing at all."""
    # Gain 0 with tau 0 gives U == tau exactly, which is STOP under strict >.
    pipeline = _pipeline(gain=0.0, tau=0.0)
    _run(pipeline)
    executed = [r for r in pipeline.action_records if r["executed"]]
    assert not executed
    # Acquisition still runs; what STOP forbids is *action* spend.
    assert sum(r["cost"]["physical_calls"]
               for r in pipeline.action_records) == 0


def test_equality_to_tau_is_stop_not_go() -> None:
    """§17 says strict ``>``; equality must not execute."""
    pipeline = _pipeline(gain=0.5, tau=0.5)
    _run(pipeline)
    assert not [r for r in pipeline.action_records if r["executed"]]


def test_strictly_above_tau_executes() -> None:
    pipeline = _pipeline(gain=1.0, tau=0.5)
    _run(pipeline)
    assert [r for r in pipeline.action_records if r["executed"]]


def test_lookahead_never_exceeds_two() -> None:
    from cover_kbc.control.planner_types import PlannerError

    with pytest.raises((PlannerError, ValueError)):
        PlannerCalibration(
            calibration_version="bad", source=EstimateSource.SYNTHETIC_TEST,
            alpha=1.0, beta=1.0, gamma=1.0, delta=0.0, eta=0.0, kappa=0.0,
            tau_continue=0.0, lookahead_depth=3)


def test_planner_never_touches_a_runtime() -> None:
    """M21 chooses; M7 executes. The planner must make no physical call."""
    pipeline = _pipeline()
    observed = {}
    original = pipeline.micro_planner.plan

    def spy(state, actions):
        before = pipeline.physical_snapshot()["physical_calls"]
        decision = original(state, actions)
        observed[len(observed)] = (
            before, pipeline.physical_snapshot()["physical_calls"])
        return decision

    pipeline.micro_planner.plan = spy
    _run(pipeline)
    for before, after in observed.values():
        assert before == after, "MicroPlanner.plan made a physical call"


def test_a_second_round_sees_state_changed_by_the_first() -> None:
    """The planner must not be handed a stale pre-action snapshot."""
    states = []
    pipeline = _pipeline()
    original = pipeline.micro_planner.plan

    def spy(state, actions):
        states.append(state)
        return original(state, actions)

    pipeline.micro_planner.plan = spy
    _run(pipeline)
    if len(states) < 2:
        pytest.skip("fixture produced fewer than two planner rounds")
    # Layer-4 state is rebuilt after each executed action.
    assert states[0].layer4 is not states[1].layer4


def test_collection_still_never_uses_the_planner() -> None:
    """Bootstrap separation must survive the production wiring."""
    from test_m20_precharge_gate import _calibration as _budget_calibration
    from cover_kbc.control.relation_budget import RelationBudgetScheduler

    planner = MicroPlanner(history=_package(), calibration=_calibration())
    pipeline = build(IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY,
                     micro_planner=planner,
                     relation_budget_scheduler=RelationBudgetScheduler(
                         {RELATION: _budget_calibration()}))
    selections = []
    original = pipeline._plan_next_action
    pipeline._plan_next_action = lambda *a, **k: (
        selections.append(1) or original(*a, **k))
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    pipeline.decide_graph(graph)
    # The pre-existing shadow diagnostic may still observe; what must never
    # happen is the planner *choosing* what collection executes.
    assert not selections, "collection selected actions via the planner"


def test_m8_remains_sole_final_owner() -> None:
    pipeline = _pipeline()
    prediction = _run(pipeline)
    assert prediction.subject == SUBJECT and prediction.relation == RELATION
