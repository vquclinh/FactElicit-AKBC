"""Module 20 must actually stop a neural call, not merely observe one.

Audit 0036 found the precharge hook probing for a method that does not exist,
so it silently admitted everything. These tests go through the real
``RelationBudgetScheduler.schedule`` → ``BudgetLedger.reserve`` contract and
assert on physical counters, because a hook that returns the right value while
the model still runs is exactly the failure that got missed.

Synthetic calibration only. No weights.
"""

from __future__ import annotations

import pytest

from cover_kbc.control.budget_types import (
    CalibrationSource,
    RelationBudgetCalibration,
    SpecialReservePurpose,
)
from cover_kbc.control.relation_budget import RelationBudgetScheduler
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.types import Query
from test_pipeline_production_seam import RELATION, SUBJECT, build


def _calibration(**overrides) -> RelationBudgetCalibration:
    base = dict(
        relation=RELATION, calibration_version="synthetic-test",
        calibration_source=CalibrationSource.SYNTHETIC_TEST,
        hard_calls=64, hard_generated_tokens=8192,
        discovery_cap=32, verification_cap=32, verification_reserve=4,
        special_reserves=((SpecialReservePurpose.REVERSE_SINGLETON, 1),),
    )
    base.update(overrides)
    return RelationBudgetCalibration(**base)


def _pipeline(calibration, mode=IntegrationMode.PRODUCTION):
    scheduler = RelationBudgetScheduler({RELATION: calibration})
    return build(mode, relation_budget_scheduler=scheduler)


def _run(pipeline):
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    return pipeline.decide_graph(graph)


def test_the_pipeline_calls_the_real_scheduler() -> None:
    """Not a stub: the canonical ``schedule`` method must be the one invoked."""
    calls = []
    pipeline = _pipeline(_calibration())
    original = pipeline.relation_budget_scheduler.schedule
    pipeline.relation_budget_scheduler.schedule = (
        lambda **kw: calls.append(kw) or original(**kw))
    _run(pipeline)
    assert calls, "RelationBudgetScheduler.schedule was never called"
    assert calls[0]["relation"] == RELATION


def test_a_starved_budget_prevents_every_neural_call() -> None:
    """Refusal must cost zero physical calls, not be undone afterwards."""
    starved = _calibration(hard_calls=0, discovery_cap=0, verification_cap=0,
                           verification_reserve=0, special_reserves=())
    pipeline = _pipeline(starved)
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    before = pipeline.physical_snapshot()["physical_calls"]
    pipeline.decide_graph(graph)

    executed = [r for r in pipeline.action_records if r["executed"]]
    assert not executed, "an action executed on a zero budget"
    assert pipeline.physical_snapshot()["physical_calls"] == before


def test_refusals_are_recorded_with_module_20_as_the_reason() -> None:
    starved = _calibration(hard_calls=0, discovery_cap=0, verification_cap=0,
                           verification_reserve=0, special_reserves=())
    pipeline = _pipeline(starved)
    _run(pipeline)
    refusals = [r for r in pipeline.action_records
                if not r["executed"] and r.get("refusal")]
    assert refusals, "no refusal was recorded"
    assert any("Module 20" in r["refusal"] for r in refusals)


def test_a_sufficient_budget_admits_the_selected_action() -> None:
    pipeline = _pipeline(_calibration())
    _run(pipeline)
    executed = [r for r in pipeline.action_records if r["executed"]]
    assert executed, "a funded action was refused"
    assert all(r["admitted"] for r in executed)


def test_precharge_precedes_execution() -> None:
    """Ordering guard: the reserve must happen before any runtime is touched."""
    order = []
    pipeline = _pipeline(_calibration())
    original_precharge = pipeline._precharge

    def spy_precharge(kind, action, graph):
        order.append("precharge")
        return original_precharge(kind, action, graph)

    original_verify = pipeline.verify_specialist_targets

    def spy_verify(*args, **kwargs):
        order.append("execute")
        return original_verify(*args, **kwargs)

    pipeline._precharge = spy_precharge
    pipeline.verify_specialist_targets = spy_verify
    _run(pipeline)

    if "execute" in order:
        assert order.index("precharge") < order.index("execute")


def test_one_ledger_per_query_so_caps_bind() -> None:
    """A fresh ledger per action would make every reserve succeed."""
    pipeline = _pipeline(_calibration())
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    first = pipeline._budget_ledger_for(graph)
    second = pipeline._budget_ledger_for(graph)
    assert first is second


def test_collection_does_not_pretend_to_have_calibration() -> None:
    """Collection precedes calibration; it must not consult a budget it lacks."""
    pipeline = _pipeline(_calibration(),
                         mode=IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY)
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    admitted, reason, hold = pipeline._precharge("m18", object(), graph)
    assert admitted and reason == "" and hold is None


def test_module_20_never_touches_object_entities() -> None:
    starved = _calibration(hard_calls=0, discovery_cap=0, verification_cap=0,
                           verification_reserve=0, special_reserves=())
    rich = _pipeline(_calibration())
    poor = _pipeline(starved)
    # Both still produce a prediction; only M8 decides what is in it.
    assert _run(rich).relation == RELATION
    assert _run(poor).relation == RELATION


def test_synthetic_calibration_is_refused_by_shipped_configuration() -> None:
    """A fixture must never masquerade as a production budget."""
    from cover_kbc.control.budget_types import BudgetSchedulerError
    from cover_kbc.control.relation_budget import load_calibrations

    payload = {"relations": [_calibration().to_json()]}
    # ``allow_synthetic`` defaults to False, which is what shipped config uses.
    with pytest.raises(BudgetSchedulerError, match="SYNTHETIC_TEST"):
        load_calibrations(payload)
    # Tests may opt in explicitly; nothing else may.
    assert load_calibrations(payload, allow_synthetic=True)[RELATION]
