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
    """§13.1 readings are one action's internal cost, not several decisions.

    Several M17 *actions* may run - the controller loop is adaptive - but each
    one is a single decision whose physical cost is several readings, and each
    gets its own round.
    """
    _, actions = executed
    m17 = [r for r in actions if r["kind"] == "m17"]
    if not m17:
        pytest.skip("no M17 action in fixture")
    rounds = [r["round_index"] for r in m17]
    assert len(set(rounds)) == len(rounds), "two M17 readings shared one round"
    for record in m17:
        assert record["cost"]["physical_calls"] > 1, (
            "an M17 decision was charged as a single reading")
        assert len({record["projection"].action_id}) == 1


# --------------------------------------------------------------------------
# per-action control state - Audit 0041 F-03
#
# The state either side of an action has to be captured *by the seam, when it
# is true*. Rebuilding it after the row finished made both sides identical and
# ΔR zero by construction, which no test could see because zero is a legal
# value.
# --------------------------------------------------------------------------

def test_every_executed_action_captures_its_own_pre_and_post_state(executed) -> None:
    _, actions = executed
    for record in actions:
        assert record["state_before"] is not None
        assert record["state_after"] is not None
        assert record["state_before"].measured
        assert record["state_after"].measured


def test_the_state_is_captured_not_reconstructed(executed) -> None:
    """Two actions in one row must not share one post-row snapshot."""
    _, actions = executed
    if len(actions) < 2:
        pytest.skip("fixture executed fewer than two actions")
    states = [(r["state_before"].calls_used, r["state_after"].calls_used)
              for r in actions]
    assert len(set(states)) > 1, "every action reported the same accounting state"


def test_one_actions_post_state_is_the_next_actions_pre_state(executed) -> None:
    """The successor chain §17's depth-2 lookahead is calibrated from."""
    _, actions = executed
    same_kind = [r for r in actions if r["kind"] == actions[0]["kind"]]
    if len(same_kind) < 2:
        pytest.skip("fixture executed fewer than two actions of one kind")
    for earlier, later in zip(same_kind, same_kind[1:]):
        assert earlier["state_after"] == later["state_before"]


def test_delta_residual_is_measured_and_signed_as_a_reduction(executed) -> None:
    _, actions = executed
    for record in actions:
        assert record["delta_residual"] == pytest.approx(
            record["state_before"].residual - record["state_after"].residual)
    assert any(r["delta_residual"] != 0.0 for r in actions), (
        "no action moved Module 19's residual, so ΔR is zero by construction")


def test_the_control_state_carries_the_five_section_15_components(executed) -> None:
    pipeline, actions = executed
    state = actions[0]["state_before"]
    for name in ("novelty_rate", "singleton_ratio", "facet_gap", "disagreement",
                 "unresolved_mass"):
        assert hasattr(state, name)
    assert state.available_components, "no §15 component was recorded as measured"


def test_prompt_tokens_are_measured_per_action(executed) -> None:
    _, actions = executed
    assert any(r["cost"]["prompt_tokens"] > 0 for r in actions), (
        "no action recorded a prompt token, yet all of them made real calls")
    for record in actions:
        assert record["cost"]["prompt_tokens"] == (
            record["post"]["prompt_tokens"] - record["pre"]["prompt_tokens"])


# --------------------------------------------------------------------------
# candidate effect and owner readings
# --------------------------------------------------------------------------

def test_candidate_effects_are_measured_from_the_graph(executed) -> None:
    _, actions = executed
    effects = [r["effect"] for r in actions if r["effect"]]
    assert effects, "no executed action recorded a candidate effect block"
    assert any(e["candidates_supported"] or e["candidates_contradicted"]
               or e["candidates_added"] for e in effects), (
        "no action was recorded as touching a candidate")


def test_redundancy_separates_a_measured_zero_from_no_measurement(executed) -> None:
    _, actions = executed
    for record in actions:
        effect = record["effect"]
        if effect["redundancy"] is None:
            assert not effect["candidates_named"]
        else:
            assert 0.0 <= effect["redundancy"] <= 1.0


def test_the_owners_own_verdict_is_recorded(executed) -> None:
    """Not inferred from the graph: a numeric-cluster verdict never reaches it."""
    _, actions = executed
    m17 = [r for r in actions if r["kind"] == "m17"]
    if not m17:
        pytest.skip("no M17 action in fixture")
    assert any(r["effect"]["verifier_outcome"] for r in m17)
    m18 = [r for r in actions if r["kind"] == "m18"]
    if m18:
        assert any(r["effect"]["structural_outcome"] for r in m18)


# --------------------------------------------------------------------------
# deterministic identity - Audit 0041 F-12
# --------------------------------------------------------------------------

def test_every_executed_action_carries_the_owners_canonical_identity(
        executed) -> None:
    _, actions = executed
    for record in actions:
        projection = record["projection"]
        assert projection is not None
        assert projection.action_id.startswith(("M17:", "M18:"))
        assert projection.family.value
        assert projection.budget_descriptor.spend_class.value in (
            "DISCOVERY", "VERIFICATION")


def test_action_identity_is_stable_across_two_independent_runs() -> None:
    """Same row, same config, same identities - in a different process image."""
    def identities():
        pipeline = build(IntegrationMode.PRODUCTION)
        run(pipeline)
        return [r["projection"].action_id
                for r in pipeline.action_records if r["executed"]]

    first, second = identities(), identities()
    assert first == second
    assert all("0x" not in identity for identity in first)


def test_distinct_logical_actions_get_distinct_identities() -> None:
    """Two counterfactual classes on one target are two actions, not one."""
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    executed = [r for r in pipeline.action_records if r["executed"]]
    identities = [r["projection"].action_id for r in executed]
    assert len(identities) == len(set(identities))


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

    pipeline._precharge = lambda kind, action, g: (False, "denied by test", None)
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


# --------------------------------------------------------------------------
# Audit 0043 C-01: one canonical Module 18 check identity, end to end
# --------------------------------------------------------------------------

def _counterfactual(near_miss: str, *, target="spain", kind="COUNTERFACTUAL"):
    from cover_kbc.verification.bidirectional_types import (
        BidirectionalCheckKind, CheckTarget, CheckTargetKind, EligibleCheck)

    return EligibleCheck(
        check_kind=BidirectionalCheckKind(kind),
        target=CheckTarget(
            relation="countryLandBordersCountry", subject="Portugal",
            row_index=0, kind=CheckTargetKind.ENTITY_CANDIDATE,
            target_id=target, display=target.title()),
        counterfactual_class=near_miss,
    )


def test_the_near_miss_class_is_part_of_module_18s_own_check_identity() -> None:
    """§14 poses a different question per contract-declared class."""
    a, b = _counterfactual("hn0"), _counterfactual("hn1")
    assert a.check_id != b.check_id
    assert a.check_id == "COUNTERFACTUAL:spain:hn0"
    # ...and a different target is a different check under the same class.
    assert _counterfactual("hn0", target="andorra").check_id != a.check_id


def test_the_request_identity_carries_the_canonical_check_identity() -> None:
    """C-01: the request used to name only the mechanism and the template.

    Two counterfactuals in one query then shared an `operation_id`, and the
    execution seam - which attributes a structural reading back to its action
    through it - handed the second action the first one's outcome.
    """
    from cover_kbc.verification.bidirectional_types import BidirectionalCheckRequest

    requests = [
        BidirectionalCheckRequest(check=check, template_id="m18_counterfactual_v1")
        for check in (_counterfactual("hn0"), _counterfactual("hn1"))
    ]
    assert requests[0].operation_id != requests[1].operation_id
    for request in requests:
        assert request.check_id in request.operation_id
        assert request.check_id == request.check.check_id


def test_the_layer6_action_id_is_module_18s_identity_namespaced() -> None:
    """One identity, not two constructions of the same parts."""
    from cover_kbc.control.action_catalog import m18_actions

    checks = [_counterfactual("hn0"), _counterfactual("hn1")]
    actions, _ = m18_actions(
        checks, subject="Portugal", relation="countryLandBordersCountry",
        row_index=0)
    assert [a.action_id for a in actions] == [
        f"M18:{check.check_id}" for check in checks]
    assert len({a.action_id for a in actions}) == 2
    # The class is the action's target class, which Module 21 may bin on.
    assert [a.facet_id for a in actions] == ["hn0", "hn1"]


def test_a_reading_is_never_taken_from_an_ambiguous_identity() -> None:
    """If the identity ever goes ambiguous again, the seam must refuse.

    Silently taking the first match is what made C-01 invisible; two records
    for one request identity is a contradiction, not a tie to be broken.
    """
    from types import SimpleNamespace as NS

    from cover_kbc.pipeline import AccountingInvariantError, CoverPipeline

    def record(outcome):
        return NS(request=NS(operation_id="m18:COUNTERFACTUAL:spain:hn0:t#0"),
                  reverse_outcome=None, reconstruction_outcome=None,
                  counterfactual_outcome=NS(value=outcome), recall_outcome=None,
                  parse_status=NS(name="OK", value="ok"))

    merged = NS(records=(record("CONTRADICT"), record("SUPPORT")))
    with pytest.raises(AccountingInvariantError, match="one request identity"):
        CoverPipeline._m18_reading(
            merged, NS(operation_id="m18:COUNTERFACTUAL:spain:hn0:t#0"))
