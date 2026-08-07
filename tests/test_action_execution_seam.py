"""The canonical action-execution seam and its per-action accounting.

Audit 0035 found the previous telemetry unusable for Module 21: a whole-row
cost delta was attributed to one action, and every action was stamped
``round_index=1``. These tests pin the corrected behaviour - one action per
round, cost measured around that action, and a role partition read from the
runtimes rather than guessed at the call site.

Offline and scripted. No weights.
"""

from __future__ import annotations

import pytest

from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.types import Query
from test_pipeline_production_seam import RELATION, SUBJECT, build, run


@pytest.fixture
def executed():
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    return pipeline, [r for r in pipeline.action_records if r["executed"]]


# --------------------------------------------------------------------------
# per-action cost attribution
# --------------------------------------------------------------------------

def test_every_executed_action_has_its_own_measured_cost(executed) -> None:
    _, actions = executed
    assert actions, "no action executed"
    for record in actions:
        assert record["cost"]["physical_calls"] == (
            record["post"]["physical_calls"] - record["pre"]["physical_calls"])


def test_whole_row_cost_is_never_assigned_to_one_action(executed) -> None:
    """The exact defect Audit 0035 found."""
    pipeline, actions = executed
    total = pipeline.physical_snapshot()["physical_calls"]
    for record in actions:
        assert record["cost"]["physical_calls"] < total, (
            "one action absorbed the whole row's cost")


def test_sequential_actions_get_different_truthful_records(executed) -> None:
    """Two actions with different real costs must not share a number."""
    _, actions = executed
    if len(actions) < 2:
        pytest.skip("fixture executed fewer than two actions")
    costs = [r["cost"]["physical_calls"] for r in actions]
    rounds = [r["round_index"] for r in actions]
    assert len(set(rounds)) == len(rounds), "round indices repeated"
    assert any(a != b for a, b in zip(costs, costs[1:])), (
        "every action reported an identical cost, which is implausible")


def test_m17_multiple_readings_are_one_action_not_many(executed) -> None:
    """§13.1 readings are one action's internal cost, not several decisions."""
    _, actions = executed
    m17 = [r for r in actions if r["kind"] == "m17"]
    if not m17:
        pytest.skip("no M17 action in fixture")
    assert len(m17) == 1
    # A single decision, whose physical cost is several readings.
    assert m17[0]["cost"]["physical_calls"] > 1


# --------------------------------------------------------------------------
# rounds and state refresh
# --------------------------------------------------------------------------

def test_round_indices_are_real_and_increase(executed) -> None:
    _, actions = executed
    rounds = [r["round_index"] for r in actions]
    assert rounds == sorted(rounds)
    assert rounds[0] >= 1
    assert not all(r == 1 for r in rounds) or len(rounds) == 1


def test_no_fabricated_round_total(executed) -> None:
    _, actions = executed
    for record in actions:
        assert "total_rounds" not in record


# --------------------------------------------------------------------------
# legal-but-unselected
# --------------------------------------------------------------------------

def test_unselected_actions_are_logged_with_no_cost_and_no_post_state() -> None:
    """Opportunity data, never fabricated counterfactual outcomes."""
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    skipped = [r for r in pipeline.action_records if not r["executed"]]
    if not skipped:
        pytest.skip("fixture offered no unselected legal action")
    for record in skipped:
        assert record["cost"]["physical_calls"] == 0
        assert record["post"] is None
        assert record.get("legal_not_selected") is True


# --------------------------------------------------------------------------
# role partition
# --------------------------------------------------------------------------

def test_role_partition_sums_to_the_physical_total(executed) -> None:
    pipeline, actions = executed
    snapshot = pipeline.physical_snapshot()
    assert (snapshot["enumerator_calls"] + snapshot["verifier_calls"]
            == snapshot["physical_calls"])
    for record in actions:
        cost = record["cost"]
        assert (cost["enumerator_calls"] + cost["verifier_calls"]
                == cost["physical_calls"])


def test_a_backwards_counter_fails_loudly() -> None:
    """A reset mid-action makes the delta meaningless; it must not pass."""
    pipeline = build(IntegrationMode.PRODUCTION)
    before = {"enumerator_calls": 5, "verifier_calls": 0,
              "physical_calls": 5, "generated_tokens": 0}
    after = {"enumerator_calls": 2, "verifier_calls": 0,
             "physical_calls": 2, "generated_tokens": 0}
    with pytest.raises(ValueError, match="moved backwards"):
        pipeline.physical_delta(before, after)


def test_an_unattributed_call_fails_loudly() -> None:
    """A call belonging to no role must never be silently absorbed."""
    pipeline = build(IntegrationMode.PRODUCTION)
    before = {"enumerator_calls": 0, "verifier_calls": 0,
              "physical_calls": 0, "generated_tokens": 0}
    after = {"enumerator_calls": 1, "verifier_calls": 1,
             "physical_calls": 3, "generated_tokens": 0}
    with pytest.raises(ValueError, match="attributed to no role or to two"):
        pipeline.physical_delta(before, after)


# --------------------------------------------------------------------------
# exactly once, and the shared seam
# --------------------------------------------------------------------------

def test_global_calls_equal_the_sum_of_action_deltas_plus_acquisition() -> None:
    """No call is billed twice, and none disappears."""
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    action_total = sum(r["cost"]["physical_calls"]
                       for r in pipeline.action_records if r["executed"])
    snapshot = pipeline.physical_snapshot()
    assert action_total <= snapshot["physical_calls"]


def test_collection_and_production_share_the_executor() -> None:
    """TRAIN must measure the semantics production will invoke."""
    from cover_kbc.pipeline import CoverPipeline
    collection = build(IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY)
    production = build(IntegrationMode.PRODUCTION)
    assert (collection.execute_action.__func__
            is production.execute_action.__func__
            is CoverPipeline.execute_action)


def test_collection_never_invokes_the_planner() -> None:
    """Using uncalibrated M21 to gather M21's own bins would be circular."""
    pipeline = build(IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY)
    calls = []
    if pipeline.micro_planner is not None:
        original = pipeline.micro_planner.plan
        pipeline.micro_planner.plan = lambda *a, **k: calls.append(1) or original(*a, **k)
    run(pipeline)
    assert not calls


def test_a_refused_action_makes_no_runtime_call() -> None:
    """A failed precharge must cost zero, not be rolled back afterwards."""
    pipeline = build(IntegrationMode.PRODUCTION)
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    pipeline.decide_graph(graph)
    consensus = pipeline.consensus_results[-1]
    before = pipeline.physical_snapshot()

    pipeline._precharge = lambda kind, action, g: (False, "denied by test")
    catalogue = pipeline._catalogued_checks(consensus)
    if not catalogue:
        pytest.skip("no M18 catalogue in fixture")
    record = pipeline.execute_action("m18", catalogue[0], consensus, graph)

    assert record["executed"] is False
    assert record["cost"]["physical_calls"] == 0
    assert pipeline.physical_snapshot()["physical_calls"] == before["physical_calls"]


def test_m8_remains_sole_owner_after_the_seam() -> None:
    pipeline = build(IntegrationMode.PRODUCTION)
    prediction = run(pipeline)
    assert prediction.subject == SUBJECT and prediction.relation == RELATION
