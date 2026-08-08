"""The precharge cycle has to close: reserve -> execute -> settle or cancel.

§16 requires the whole call plan to be held before the first neural call. A
hold that is never released is only half of that: the conservative part of
every reservation stays withheld for the rest of the query, the ledger's
committed total stops matching what the runtimes actually spent, and Module 21
is priced against capacity the system does have.

Audit 0053 found exactly that - ``_precharge`` reserved and then dropped the
``BudgetReservation``, and no production path called ``settle`` or ``cancel``.
These tests assert the completed lifecycle at both levels:

* directly on ``BudgetLedger``, where the arithmetic is;
* and through the **real** ``CoverPipeline.execute_action`` path with a scripted
  runtime, because "the ledger can settle" and "production settles" are
  different claims and only the second one was missing.

Scripted offline runtimes throughout. No weights, no VAL, no TEST.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
import yaml

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.control.budget_accounting import BudgetLedger
from cover_kbc.control.budget_types import (
    BudgetDenial,
    BudgetSchedulerError,
    CacheDisposition,
    CalibrationSource,
    CoreBudgetSnapshot,
    RelationBudgetCalibration,
    ReservationStatus,
    SpecialReservePurpose,
)
from cover_kbc.control.relation_budget import (
    RelationBudgetScheduler,
    build_plan,
    load_calibrations,
)
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.paths import REPO_ROOT
from cover_kbc.pipeline import AccountingInvariantError, ExecutionMode, PipelineConfig
from cover_kbc.types import Query
from test_pipeline_production_seam import RELATION, SUBJECT, build

CALIBRATION = REPO_ROOT / "configs" / "calibration"
VAL_CONFIG = REPO_ROOT / "configs" / "experiments" / "cover_kbc_v2_validation.yaml"
QUERY_KEY = (SUBJECT, RELATION, 0)

#: The real TRAIN-derived whole-query envelopes. Asserted unchanged here too,
#: because settlement touches the same ledger they govern.
REAL_HARD_CALLS = {
    "awardWonBy": 44,
    "companyTradesAtStockExchange": 30,
    "countryLandBordersCountry": 24,
    "hasArea": 22,
    "hasCapacity": 23,
    "personHasCityOfDeath": 22,
}

_artifacts = pytest.mark.skipif(
    not (CALIBRATION / "m20_relation_budget.json").is_file(),
    reason="the real TRAIN-derived calibration artifacts are not in this checkout",
)


# --------------------------------------------------------------------------
# scaffolding
# --------------------------------------------------------------------------


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


def _pipeline(calibration=None, mode=IntegrationMode.PRODUCTION, **kwargs):
    scheduler = RelationBudgetScheduler(
        {RELATION: calibration or _calibration()})
    return build(mode, relation_budget_scheduler=scheduler, **kwargs)


def _run(pipeline):
    graph = pipeline.enumerate_query(Query(*QUERY_KEY))
    pipeline.decide_graph(graph)
    return graph


def _ledger(pipeline):
    return pipeline._budget_ledgers[QUERY_KEY]


def _outstanding(ledger):
    return [r for r in ledger.reservations
            if r.status is ReservationStatus.OUTSTANDING]


def _first_action(pipeline, graph, kind="m17"):
    """One real catalogue entry the pipeline would legitimately execute."""
    consensus = pipeline.consensus_results[-1]
    source = (pipeline._catalogued_targets if kind == "m17"
              else pipeline._catalogued_checks)
    for entry in source(consensus):
        if pipeline.project_action(kind, entry, graph) is not None:
            return consensus, entry
    pytest.skip(f"no executable {kind} action in the fixture")


def _prepared(pipeline):
    """A pipeline that has completed one real query, ready for one more action.

    ``decide_graph`` drives the whole Layer-4 loop, so the ledger already holds
    that query's settled reservations. The tests below therefore assert on what
    *this* extra action added, not on the ledger's whole history.
    """
    graph = pipeline.enumerate_query(Query(*QUERY_KEY))
    pipeline.decide_graph(graph)
    return graph


# ==========================================================================
# 1-4, 9-12 — the success path
# ==========================================================================


def test_a_production_query_leaves_no_outstanding_reservation() -> None:
    """The headline defect: holds that nobody ever closed."""
    pipeline = _pipeline()
    _run(pipeline)
    ledger = _ledger(pipeline)
    assert ledger.reservations, "nothing was reserved; this proves nothing"
    assert _outstanding(ledger) == []
    for reservation in ledger.reservations:
        assert reservation.status is ReservationStatus.SETTLED


def test_every_reservation_is_settled_exactly_once() -> None:
    pipeline = _pipeline()
    _run(pipeline)
    ledger = _ledger(pipeline)
    settled = [s.reservation_id for s in ledger.settlements]
    assert sorted(settled) == sorted(r.reservation_id for r in ledger.reservations)
    assert len(settled) == len(set(settled)), "a reservation settled twice"


def test_settlement_and_cancellation_are_mutually_exclusive() -> None:
    """The ledger refuses the second close on the same reservation."""
    pipeline = _pipeline()
    _run(pipeline)
    ledger = _ledger(pipeline)
    held = ledger.reservations[0].reservation_id
    with pytest.raises(BudgetSchedulerError, match="already settled"):
        ledger.cancel(held)
    with pytest.raises(BudgetSchedulerError, match="already settled"):
        ledger.settle(held, actual_calls=0)


def test_the_ledger_total_equals_what_the_runtimes_actually_spent() -> None:
    """The one number that proves nothing is lost or counted twice.

    ``committed_calls`` is prior spend plus settled spend; ``query_physical_cost``
    is the runtimes' own counters differenced against this query's baseline.
    They are measured by two independent owners and must agree exactly.
    """
    pipeline = _pipeline()
    graph = _run(pipeline)
    ledger = _ledger(pipeline)
    spent = pipeline.query_physical_cost(graph)
    assert ledger.committed_calls == spent["physical_calls"]
    assert ledger.committed_tokens == spent["generated_tokens"]
    assert ledger.prior_calls > 0, "acquisition spent nothing; weak assertion"


def test_a_conservative_reservation_releases_what_it_did_not_use() -> None:
    """Module 18 holds a 256-token decode bound and spends far less."""
    pipeline = _pipeline()
    _run(pipeline)
    ledger = _ledger(pipeline)
    generous = [s for s in ledger.settlements if s.released_generated_tokens > 0]
    assert generous, "no over-reservation was released; the case is untested"
    for settlement in generous:
        held = next(r for r in ledger.reservations
                    if r.reservation_id == settlement.reservation_id)
        assert settlement.actual_generated_tokens < held.reserved_generated_tokens
        assert (settlement.released_generated_tokens
                == held.reserved_generated_tokens
                - settlement.actual_generated_tokens)


def test_an_exact_reservation_releases_nothing() -> None:
    """Module 17's readings are counted, not estimated: reserve == actual."""
    pipeline = _pipeline()
    _run(pipeline)
    ledger = _ledger(pipeline)
    exact = [s for s in ledger.settlements
             if s.actual_calls and s.released_calls == 0]
    assert exact, "no exactly-priced action ran"


def test_released_capacity_is_available_to_the_next_action() -> None:
    """Releasing is the point: a later action must be able to spend it."""
    plan = build_plan(
        subject=SUBJECT, relation=RELATION, row_index=0,
        program_type=CONTRACTS[RELATION].program_type.value,
        profile=_profile(RELATION),
        core_budget=CoreBudgetSnapshot(max_calls=4, calls_used=0,
                                       max_generated_tokens=1024,
                                       generated_tokens_used=0),
        calibration=_calibration(hard_calls=10, hard_generated_tokens=1024,
                                 discovery_cap=10, verification_cap=10,
                                 verification_reserve=0, special_reserves=()))
    ledger = BudgetLedger(plan)
    descriptor = _m17_descriptor(RELATION, control_calls_needed=4)   # 8 calls
    first = ledger.reserve(descriptor)
    assert not isinstance(first, BudgetDenial)
    # Only 2 of the 8 held calls were really spent.
    ledger.settle(first.reservation_id, actual_calls=2)
    assert ledger.committed_calls == 2
    second = ledger.reserve(_m17_descriptor(RELATION, control_calls_needed=0,
                                            action_id="second"))
    assert not isinstance(second, BudgetDenial), (
        "released capacity was not available to the next action")


# ==========================================================================
# 5-7 — the failure paths
# ==========================================================================


class _Boom(RuntimeError):
    """A Layer-4 executor failure, raised by the tests below."""


def test_a_failure_before_any_physical_call_cancels_the_hold() -> None:
    pipeline = _pipeline()
    graph = _prepared(pipeline)
    consensus, action = _first_action(pipeline, graph, "m17")
    ledger = _ledger(pipeline)
    before = pipeline.physical_snapshot()["physical_calls"]
    settlements, committed = len(ledger.settlements), ledger.committed_calls

    def explode(*_a, **_k):
        raise _Boom("failed before touching a runtime")

    pipeline.verify_specialist_targets = explode
    with pytest.raises(_Boom):
        pipeline.execute_action("m17", action, consensus, graph)

    assert pipeline.physical_snapshot()["physical_calls"] == before
    assert _outstanding(ledger) == []
    assert ledger.reservations[-1].status is ReservationStatus.CANCELLED
    assert len(ledger.settlements) == settlements, (
        "a hold that bought nothing was settled")
    assert ledger.committed_calls == committed, "a cancelled hold cost budget"


def test_a_failure_after_physical_calls_settles_the_real_spend() -> None:
    """Calls that happened are never refunded because the action then threw."""
    pipeline = _pipeline()
    graph = _prepared(pipeline)
    consensus, action = _first_action(pipeline, graph, "m17")
    original = pipeline.verify_specialist_targets

    def spend_then_explode(*args, **kwargs):
        original(*args, **kwargs)          # real readings, real counters
        raise _Boom("failed after spending")

    pipeline.verify_specialist_targets = spend_then_explode
    ledger = _ledger(pipeline)
    before = pipeline.physical_snapshot()["physical_calls"]
    committed = ledger.committed_calls
    with pytest.raises(_Boom):
        pipeline.execute_action("m17", action, consensus, graph)
    spent = pipeline.physical_snapshot()["physical_calls"] - before

    assert spent > 0, "nothing was spent; this is the wrong case"
    assert _outstanding(ledger) == []
    assert ledger.reservations[-1].status is ReservationStatus.SETTLED
    assert ledger.settlements[-1].actual_calls == spent
    assert ledger.committed_calls == committed + spent
    assert all(r.status is not ReservationStatus.CANCELLED
               for r in ledger.reservations)


def test_the_original_failure_is_what_propagates() -> None:
    """Settlement is bookkeeping. It must not replace the action's error."""
    pipeline = _pipeline()
    graph = _prepared(pipeline)
    consensus, action = _first_action(pipeline, graph, "m17")

    def explode(*_a, **_k):
        raise _Boom("the caller must see this")

    pipeline.verify_specialist_targets = explode
    with pytest.raises(_Boom, match="the caller must see this"):
        pipeline.execute_action("m17", action, consensus, graph)


def test_an_unknown_action_kind_still_closes_its_hold() -> None:
    """`UnsupportedAction` is raised inside the guarded block, not before it."""
    from cover_kbc.pipeline import UnsupportedAction

    pipeline = _pipeline()
    graph = _prepared(pipeline)
    consensus, action = _first_action(pipeline, graph, "m17")
    # Precharge and project as m17, execute as an unknown kind.
    pipeline._action_descriptor = (
        lambda kind, a, g: pipeline.project_action("m17", a, g).budget_descriptor)
    pipeline.project_action = (
        lambda kind, a, g, _real=pipeline.project_action: _real("m17", a, g))
    with pytest.raises(UnsupportedAction):
        pipeline.execute_action("nonsense", action, consensus, graph)
    assert _outstanding(_ledger(pipeline)) == []


def test_a_settlement_overrun_fails_closed_without_widening_the_cap() -> None:
    """A call outside the precharge is what §16 exists to prevent.

    Forced by settling against a spend larger than the hold. The ledger refuses,
    the pipeline reports it as an accounting invariant violation, and nothing
    borrows capacity to make it fit.
    """
    pipeline = _pipeline()
    graph = _prepared(pipeline)
    ledger = _ledger(pipeline)
    _, action = _first_action(pipeline, graph, "m17")
    descriptor = pipeline._action_descriptor("m17", action, graph)
    reservation = ledger.reserve(descriptor)
    assert not isinstance(reservation, BudgetDenial)

    held = reservation.reserved_calls
    fake_before = {k: 0 for k in pipeline.PHYSICAL_COUNTERS}
    fake_after = dict(fake_before)
    fake_after["physical_calls"] = held + 1
    fake_after["enumerator_calls"] = held + 1
    with pytest.raises(AccountingInvariantError, match="could not settle"):
        pipeline._release_hold((ledger, reservation), fake_before, fake_after,
                               executed=True)
    # Fail-closed means fail-closed: the hold stays as it was, the envelope is
    # not widened, and nothing was silently absorbed.
    assert ledger.reservations[-1].status is ReservationStatus.OUTSTANDING
    assert ledger.plan.hard_calls == 64
    assert ledger.committed_calls <= ledger.plan.hard_calls


# ==========================================================================
# 8 — zero-cost execution
# ==========================================================================


def test_a_zero_cost_execution_settles_rather_than_cancels() -> None:
    """An action that ran and cost nothing is a settlement of zero, not a cancel."""
    plan = build_plan(
        subject=SUBJECT, relation=RELATION, row_index=0,
        program_type=CONTRACTS[RELATION].program_type.value,
        profile=_profile(RELATION),
        core_budget=CoreBudgetSnapshot(max_calls=4, calls_used=0,
                                       max_generated_tokens=1024,
                                       generated_tokens_used=0),
        calibration=_calibration())
    pipeline = _pipeline()
    ledger = BudgetLedger(plan)
    descriptor = _m17_descriptor(RELATION, control_calls_needed=0)
    reservation = ledger.reserve(descriptor)
    zero = {k: 0 for k in pipeline.PHYSICAL_COUNTERS}
    pipeline._release_hold((ledger, reservation), zero, dict(zero), executed=True)
    assert ledger.reservations[-1].status is ReservationStatus.SETTLED
    assert ledger.settlements[-1].actual_calls == 0
    assert ledger.committed_calls == 0


def test_a_fully_cached_control_costs_zero_and_is_reserved_as_zero() -> None:
    """Cache-awareness survives settlement: a hit is not a call."""
    descriptor = _m17_descriptor(RELATION, control_calls_needed=0)
    hits = [s for s in descriptor.sub_calls
            if s.cache is CacheDisposition.CACHE_HIT]
    assert len(hits) == 4
    assert descriptor.cost().neural_calls == 4
    assert descriptor.cost().cache_hits == 4


# ==========================================================================
# the precharge must be a safe upper bound
# ==========================================================================


def test_the_precharge_reserves_the_cold_control_plan_when_it_is_cold() -> None:
    """The first Module 17 action of a run pays for its contextual controls.

    ``project_action`` used to hardcode ``control_calls_needed=0`` - "every
    control is already cached" - which is false until the first action has run.
    The hold was then 4 while 8 calls were spent, and settlement now refuses
    that. The reserve has to be the real number.
    """
    pipeline = _pipeline()
    graph = _prepared(pipeline)
    ledger = _ledger(pipeline)
    first = next(r for r in ledger.reservations
                 if r.descriptor.action_id.startswith("M17:"))
    later = [r for r in ledger.reservations
             if r.descriptor.action_id.startswith("M17:")][1:]
    assert first.reserved_calls == 8, "the cold control plan was not reserved"
    assert all(r.reserved_calls == 4 for r in later), (
        "a warm action was charged for controls it did not make")
    settlement = next(s for s in ledger.settlements
                      if s.reservation_id == first.reservation_id)
    assert settlement.actual_calls == 8
    assert settlement.released_calls == 0
    del graph


def test_an_unknowable_cache_state_is_reserved_as_a_miss() -> None:
    """budget_accounting's own rule, applied where it is decided."""
    pipeline = _pipeline()
    graph = _prepared(pipeline)
    _, action = _first_action(pipeline, graph, "m17")
    pipeline.verifier_runtime = None
    assert pipeline._m17_control_calls_needed(action, graph) == 4


def test_module_17_answers_the_control_question_itself() -> None:
    """The owner's accounting, not a constant chosen by the call site."""
    from cover_kbc.verification.specialist_verifier import SpecialistVerifier

    assert hasattr(SpecialistVerifier, "control_calls_needed")

    from cover_kbc.verification.specialist_types import (
        VerificationTarget,
        VerificationTargetKind,
    )

    cold = _pipeline()
    graph = cold.enumerate_query(Query(*QUERY_KEY))
    request = cold.specialist_verifier.build_request(VerificationTarget(
        relation=RELATION, subject=SUBJECT, row_index=0,
        kind=VerificationTargetKind.ENTITY_CANDIDATE, target_id="alphaland",
        display="Alphaland"))
    before = cold.physical_snapshot()["physical_calls"]
    assert cold.specialist_verifier.control_calls_needed(
        request, graph.contract, cold.verifier_runtime) == 4
    assert cold.physical_snapshot()["physical_calls"] == before, (
        "asking what a control would cost made a neural call")

    # After a real query the four controls are measured and cached.
    cold.decide_graph(graph)
    assert cold.specialist_verifier.control_calls_needed(
        request, graph.contract, cold.verifier_runtime) == 0


def test_the_live_m17_descriptor_is_still_four_factual_readings() -> None:
    """Quality is untouched: only the control accounting changed."""
    from cover_kbc.control.action_catalog import m17_call_plan
    from cover_kbc.verification.specialist_verifier import SpecialistVerifierConfig

    readings, controls = m17_call_plan(SpecialistVerifierConfig())
    assert (readings, controls) == (4, 4)
    cold = _m17_descriptor(RELATION, control_calls_needed=4)
    warm = _m17_descriptor(RELATION, control_calls_needed=0)
    factual = [s for s in warm.sub_calls if s.label.startswith("reading#")]
    assert len(factual) == 4
    assert cold.cost().neural_calls == 8
    assert warm.cost().neural_calls == 4


# ==========================================================================
# 13-17 — no double charging, denials, ledger identity
# ==========================================================================


def test_the_ledger_is_never_recreated_within_a_query() -> None:
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(Query(*QUERY_KEY))
    first = pipeline._budget_ledger_for(graph)
    pipeline.decide_graph(graph)
    assert pipeline._budget_ledger_for(graph) is first
    assert first.prior_calls == pipeline._budget_ledger_for(graph).prior_calls


def test_settled_layer_four_spend_is_never_re_imported_as_prior_spend() -> None:
    """The Audit 0052 interaction, asserted directly.

    ``query_physical_cost`` grows as Layer 4 spends. If the ledger re-read it
    the settled calls would be charged a second time - so the ledger is built
    once, before the first reservation, and the priors never move again.
    """
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(Query(*QUERY_KEY))
    ledger = pipeline._budget_ledger_for(graph)
    priors_at_open = ledger.prior_calls
    acquisition = pipeline.query_physical_cost(graph)["physical_calls"]
    assert priors_at_open == acquisition

    pipeline.decide_graph(graph)
    layer4 = pipeline.query_physical_cost(graph)["physical_calls"] - acquisition
    assert layer4 > 0, "no Layer-4 call ran; this proves nothing"
    assert ledger.prior_calls == priors_at_open, "priors were re-read"
    assert ledger.committed_calls == priors_at_open + layer4


def test_prior_spend_survives_every_settlement_and_cancellation() -> None:
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(Query(*QUERY_KEY))
    ledger = pipeline._budget_ledger_for(graph)
    priors = ledger.prior_calls
    pipeline.decide_graph(graph)
    assert ledger.prior_calls == priors
    assert ledger.committed_calls >= priors


def test_repeated_actions_consume_cumulative_capacity() -> None:
    """Settlement releases the unused part - it does not reset the ledger."""
    pipeline = _pipeline()
    _run(pipeline)
    ledger = _ledger(pipeline)
    running = ledger.prior_calls
    for settlement in ledger.settlements:
        running += settlement.actual_calls
    assert ledger.committed_calls == running
    assert len(ledger.settlements) > 1, "only one action ran; not cumulative"


def test_a_denied_action_never_executes_and_leaves_no_hold() -> None:
    starved = _calibration(hard_calls=0, discovery_cap=0, verification_cap=0,
                           verification_reserve=0, special_reserves=())
    pipeline = _pipeline(starved)
    graph = pipeline.enumerate_query(Query(*QUERY_KEY))
    before = pipeline.physical_snapshot()["physical_calls"]
    pipeline.decide_graph(graph)
    ledger = _ledger(pipeline)
    assert [r for r in pipeline.action_records if r["executed"]] == []
    assert pipeline.physical_snapshot()["physical_calls"] == before
    assert ledger.reservations == (), "a denial created a reservation"
    assert ledger.denials, "nothing was denied; the budget was not starved"
    assert _outstanding(ledger) == []


def test_a_denial_returns_no_hold_from_precharge() -> None:
    starved = _calibration(hard_calls=0, discovery_cap=0, verification_cap=0,
                           verification_reserve=0, special_reserves=())
    pipeline = _pipeline(starved)
    graph = _prepared(pipeline)
    consensus, action = _first_action(pipeline, graph, "m17")
    admitted, refusal, hold = pipeline._precharge("m17", action, graph)
    assert admitted is False
    assert "Module 20 denied" in refusal
    assert hold is None
    del consensus


def test_collection_reserves_nothing_and_therefore_settles_nothing() -> None:
    """Collection precedes calibration; its bound is the policy's, not M20's."""
    pipeline = _pipeline(mode=IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY)
    graph = pipeline.enumerate_query(Query(*QUERY_KEY))
    admitted, refusal, hold = pipeline._precharge("m17", object(), graph)
    assert admitted and refusal == "" and hold is None
    assert QUERY_KEY not in pipeline._budget_ledgers


# ==========================================================================
# 18-20 — the verified Audit 0052/0053 properties must survive
# ==========================================================================


def _profile(relation: str, subject: str = SUBJECT):
    grade = NS(value="LOW")
    return NS(relation=relation, subject=subject, row_index=0,
              program_type=CONTRACTS[relation].program_type.value,
              profiler_version="m9-v1", search_breadth=grade,
              verification_priority=grade, temporal_sensitivity=grade,
              near_miss_risk=grade, open_set_risk=grade)


def _m17_descriptor(relation: str, *, control_calls_needed: int,
                    action_id: str = "c1"):
    from cover_kbc.control.action_catalog import m17_actions
    from cover_kbc.verification.specialist_types import (
        VerificationTarget,
        VerificationTargetKind,
    )

    target = VerificationTarget(
        relation=relation, subject=SUBJECT, row_index=0,
        kind=VerificationTargetKind.ENTITY_CANDIDATE, target_id=action_id,
        display="Alphaland")
    actions, _ = m17_actions((target,), subject=SUBJECT, relation=relation,
                             row_index=0,
                             control_calls_needed=control_calls_needed)
    return actions[0].budget_descriptor


@pytest.fixture(scope="module")
def real_budgets():
    if not (CALIBRATION / "m20_relation_budget.json").is_file():
        pytest.skip("real artifacts absent")
    return load_calibrations(
        json.loads((CALIBRATION / "m20_relation_budget.json").read_text()))


@_artifacts
@pytest.mark.parametrize("relation", sorted(REAL_HARD_CALLS))
def test_the_real_m20_envelopes_are_unchanged(relation, real_budgets) -> None:
    config = yaml.safe_load(VAL_CONFIG.read_text())
    block = dict(config["pipeline"])
    block["mode"] = ExecutionMode.INTERLEAVED.value
    parsed = PipelineConfig.from_mapping(block)
    contract = CONTRACTS[relation]
    plan = build_plan(
        subject="S", relation=relation, row_index=0,
        program_type=contract.program_type.value,
        profile=_profile(relation, "S"),
        core_budget=CoreBudgetSnapshot.of(parsed.budget(contract)),
        calibration=real_budgets[relation])
    assert plan.hard_calls == REAL_HARD_CALLS[relation]
    assert real_budgets[relation].hard_calls == REAL_HARD_CALLS[relation]


@_artifacts
def test_the_award_verification_reserve_still_protects_its_floor(
    real_budgets,
) -> None:
    """§9.3's floor must survive both the envelope fix and settlement."""
    config = yaml.safe_load(VAL_CONFIG.read_text())
    block = dict(config["pipeline"])
    block["mode"] = ExecutionMode.INTERLEAVED.value
    parsed = PipelineConfig.from_mapping(block)
    contract = CONTRACTS["awardWonBy"]
    plan = build_plan(
        subject="S", relation="awardWonBy", row_index=0,
        program_type=contract.program_type.value, profile=_profile("awardWonBy", "S"),
        core_budget=CoreBudgetSnapshot.of(parsed.budget(contract)),
        calibration=real_budgets["awardWonBy"])
    verification = next(e for e in plan.envelopes if e.name == "verification")
    assert verification.protected_floor == 14
    assert plan.hard_calls == 44

    # Spend everything a discovery action could reach; the floor stays.
    ledger = BudgetLedger(plan, prior_calls=plan.hard_calls - 14)
    discovery = _award_discovery_descriptor()
    assert isinstance(ledger.reserve(discovery), BudgetDenial), (
        "discovery spent into the protected verification floor")


def _award_discovery_descriptor():
    from cover_kbc.control.action_catalog import ActionOwner, _descriptor
    from cover_kbc.control.budget_accounting import structural_check_plan
    from cover_kbc.control.budget_types import BudgetSpendClass

    return _descriptor(
        "S", "awardWonBy", 0, action_id="M18:CANDIDATE_FREE_RECALL:probe",
        owner=ActionOwner.M18_STRUCTURAL, action_kind="CANDIDATE_FREE_RECALL",
        spend_class=BudgetSpendClass.DISCOVERY,
        sub_calls=structural_check_plan(256))
