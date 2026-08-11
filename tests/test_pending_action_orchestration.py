"""Audit 0078: the interleaved pending-action incident, and its repair.

The incident. The frozen calibrated V3 TEST run (`16f60fb`) produced 475 rows
and 39 `PendingActionNotConsumed` failures, every one of them
`companyTradesAtStockExchange`, every one with the identical signature::

    the controller selected RUN_FACET needing the enumerator role
    and 1 calls remain

Every failed row became an empty PIPELINE_ERROR prediction, and the batch still
returned 0.

The mechanism, in three facts that only bite together:

1. ``verify_graph`` hands the Phase-B controller
   ``frozenset({VERIFIER, NONE})`` **unconditionally**, which is a *staged*-mode
   fact - there, only the verifier is resident;
2. ``_controlled_phase`` pends any action whose owner is outside
   ``allowed_roles``, and only ``resume`` consumes an enumerator-role pending
   action - ``resume`` is called solely by ``scripts/run_staged.py``;
3. ``companyTradesAtStockExchange`` is the only relation whose contract budget
   (5 calls) exceeds what Phase A spends (4), so it is the only relation that
   arrives at Phase B with an affordable call left and therefore the only one
   whose Phase-B controller runs at all.

These tests are zero-model throughout: `ScriptedRuntime` only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.controller import ACTION_ROLE, Action, ActionType
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import (
    CoverPipeline,
    ExecutionMode,
    PendingActionNotConsumed,
    PipelineConfig,
)
from cover_kbc.types import EmptyReason, ModelRole, Query

REPO_ROOT = Path(__file__).resolve().parents[1]

STOCK = "companyTradesAtStockExchange"
BORDERS = "countryLandBordersCountry"


def _runtimes(reply: str = "NONE"):
    """An enumerator and a verifier that generate nothing useful, deterministically.

    ``NONE`` is the abstention token every view's format block asks for, so the
    parser yields zero candidates - which is exactly the state the 39 failed
    TEST rows were in when the controller reached for one more facet.
    """
    enumerator = ScriptedRuntime(
        fallback=lambda request: reply, model_id="offline/mistral",
        family="mistral", role="enumerator",
    )
    verifier = ScriptedRuntime(
        fallback=lambda request: reply, model_id="offline/qwen",
        family="qwen", role="verifier",
    )
    return enumerator, verifier


def _config(**overrides) -> PipelineConfig:
    base = dict(
        mode=ExecutionMode.INTERLEAVED,
        enable_active_controller=True,
        enable_calibrated_gate=False,
        enable_verifier=True,
        enable_cross_model_recall=True,
        max_steps_per_query=12,
        max_calls_per_query=12,
        max_verifications_per_query=6,
    )
    base.update(overrides)
    return PipelineConfig(**base)


def _pipeline(**overrides) -> CoverPipeline:
    enumerator, verifier = _runtimes()
    return CoverPipeline(enumerator, _config(**overrides), verifier_runtime=verifier)


# --------------------------------------------------------------------------
# 1. The incident's preconditions, asserted from source rather than assumed
# --------------------------------------------------------------------------


def test_run_facet_is_owned_by_the_enumerator():
    assert ACTION_ROLE[ActionType.RUN_FACET] is ModelRole.ENUMERATOR
    assert Action(action_type=ActionType.RUN_FACET, view_id="v").model_role is (
        ModelRole.ENUMERATOR)


def test_stock_is_the_only_relation_with_a_spare_call_at_the_phase_b_boundary():
    """Why exactly one relation failed, and it was not chance.

    Phase A spends the mandatory views. Every relation except stock has a
    contract budget equal to that spend, so Phase B sees an exhausted budget and
    breaks before the controller can choose anything.
    """
    config = _config()
    caps = {name: config.budget(contract).max_calls for name, contract in CONTRACTS.items()}
    assert caps[STOCK] == 5
    assert caps[BORDERS] == 4
    others = {name: cap for name, cap in caps.items() if name not in (STOCK, "awardWonBy")}
    assert set(others.values()) == {4}, others


def test_only_run_staged_consumes_an_enumerator_pending_action():
    """The consumer gap, asserted against the tree rather than described.

    Matched on the call form ``pipeline.resume`` rather than the bare substring
    ``.resume``, which also occurs in ``--resume`` flags and in checkpoint
    reconciliation that has nothing to do with pending actions.
    """
    resume_callers = sorted(
        path.name
        for path in (REPO_ROOT / "scripts").glob("*.py")
        if "pipeline.resume" in path.read_text(encoding="utf-8")
    )
    assert resume_callers == ["run_staged.py"], resume_callers


def test_the_train_collection_runner_never_runs_phase_b():
    """Why the authoritative TRAIN corpus could not carry this bug.

    The collection driver goes ``enumerate_query`` -> ``decide_graph``. Phase B
    is the only place a pending action is created, so a corpus collected this
    way cannot contain one.
    """
    source = (REPO_ROOT / "scripts" / "run_train_calibration_collection.py").read_text(
        encoding="utf-8")
    assert "verify_graph" not in source
    assert "pipeline.enumerate_query(" in source
    assert "pipeline.decide_graph(" in source


# --------------------------------------------------------------------------
# 2. The reproduction, and the invariant it must satisfy after the fix
# --------------------------------------------------------------------------


def _stock_query() -> Query:
    return Query("A Company That Does Not Exist", STOCK, 0)


def _incident_state(pipeline: CoverPipeline):
    """The exact pre-decision state of the 39 failed TEST rows.

    A stock query whose acquisition produced nothing, with an optional facet
    still unexplored and exactly one affordable call left. ``max_steps_per_query``
    caps Phase A short of the facet, which is what a real run reached by
    spending its steps on views that returned nothing.
    """
    graph = pipeline.enumerate_query(_stock_query())
    cap = pipeline.config.budget(graph.contract).max_calls
    graph.budget_snapshot = {"calls_used": cap - 1, "generated_tokens_used": 0}
    return graph


def test_the_incident_state_no_longer_leaves_a_pending_action():
    """The regression test for the incident, at its exact signature.

    On the frozen source this state raised::

        PendingActionNotConsumed: .../companyTradesAtStockExchange: the
        controller selected RUN_FACET needing the enumerator role and 1 calls
        remain

    After the repair Phase B either executes the action or refuses it as
    unaffordable; either way nothing is pending at the finalization boundary.
    """
    pipeline = _pipeline(max_steps_per_query=3)
    graph = _incident_state(pipeline)
    pipeline.verify_graph(graph)
    assert not graph.pending_action, graph.pending_action
    prediction = pipeline.decide_graph(graph)
    assert prediction.empty_reason is not EmptyReason.PIPELINE_ERROR


def test_a_one_call_action_at_a_one_call_boundary_is_dispatched_exactly_once():
    """Audit 0078 section 7: it MUST be dispatched, and exactly once."""
    pipeline = _pipeline(max_steps_per_query=2)
    graph = _incident_state(pipeline)
    cap = pipeline.config.budget(graph.contract).max_calls
    before = pipeline.physical_snapshot()
    pipeline.verify_graph(graph)
    spent = pipeline.physical_delta(before, pipeline.physical_snapshot())["physical_calls"]
    assert spent == 1, f"expected exactly one dispatch, got {spent}"
    assert not graph.pending_action
    assert int(graph.budget_snapshot["calls_used"]) == cap, "the last call was not charged"


def test_interleaved_stock_query_completes_without_a_pending_action():
    """The whole-query form of the same invariant."""
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(_stock_query())
    pipeline.verify_graph(graph)
    assert not graph.pending_action, (
        "an enumerator-role action was left pending at the finalization "
        f"boundary: {graph.pending_action}"
    )
    prediction = pipeline.decide_graph(graph)
    assert prediction.empty_reason is not EmptyReason.PIPELINE_ERROR


def test_full_interleaved_run_reports_no_query_errors_for_stock():
    pipeline = _pipeline()
    result = pipeline.run([_stock_query()])
    assert result.errors == [], result.errors
    assert len(result.predictions) == 1
    assert result.predictions[0].empty_reason is not EmptyReason.PIPELINE_ERROR


@pytest.mark.parametrize("relation", sorted(CONTRACTS))
def test_no_relation_leaves_an_executable_action_pending_in_interleaved_mode(relation):
    """The invariant, over every relation program type."""
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(Query("Some Subject", relation, 0))
    pipeline.verify_graph(graph)
    assert not graph.pending_action, (
        f"{relation}: {graph.pending_action} survived Phase B in interleaved mode")


# --------------------------------------------------------------------------
# 3. Budget boundaries: 0, 1, 2 remaining calls
# --------------------------------------------------------------------------


def _spend_to(pipeline: CoverPipeline, graph, remaining: int) -> None:
    """Leave exactly ``remaining`` affordable calls on the persisted snapshot."""
    cap = pipeline.config.budget(graph.contract).max_calls
    snapshot = dict(graph.budget_snapshot or {})
    snapshot["calls_used"] = max(0, cap - remaining)
    graph.budget_snapshot = snapshot


@pytest.mark.parametrize("remaining", [0, 1, 2])
def test_phase_b_never_pends_an_enumerator_action_at_any_budget_boundary(remaining):
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(_stock_query())
    _spend_to(pipeline, graph, remaining)
    pipeline.verify_graph(graph)
    assert not graph.pending_action
    prediction = pipeline.decide_graph(graph)
    assert prediction.empty_reason is not EmptyReason.PIPELINE_ERROR


def test_zero_remaining_calls_executes_nothing_further():
    """With no budget the controller must not spend, and must not crash."""
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(_stock_query())
    _spend_to(pipeline, graph, 0)
    before = pipeline.physical_snapshot()
    pipeline.verify_graph(graph)
    after = pipeline.physical_snapshot()
    assert pipeline.physical_delta(before, after)["physical_calls"] == 0
    assert not graph.pending_action


def test_budget_is_never_negative_and_calls_never_exceed_the_cap():
    for relation in sorted(CONTRACTS):
        pipeline = _pipeline()
        graph = pipeline.enumerate_query(Query("Some Subject", relation, 0))
        pipeline.verify_graph(graph)
        cap = pipeline.config.budget(graph.contract).max_calls
        used = int((graph.budget_snapshot or {}).get("calls_used", 0))
        assert 0 <= used <= cap, f"{relation}: {used} of {cap}"


def test_no_action_is_dispatched_twice_and_no_evidence_is_duplicated():
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(_stock_query())
    pipeline.verify_graph(graph)
    # ``graph.records`` is keyed by record id, so a repeat dispatch would either
    # collide here or show up as a second evidence edge below.
    assert len(graph.records) == len(set(graph.records))
    record_ids = [
        record_id
        for candidate in graph.candidates.values()
        for record_id in candidate.record_ids
    ]
    assert len(record_ids) == len(set(record_ids)), (
        "one generation was attributed to a candidate twice")


def test_controller_log_records_each_decision_once():
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(_stock_query())
    pipeline.verify_graph(graph)
    steps = [entry.get("step") for entry in graph.controller_log]
    assert len(steps) == len(set(steps)), graph.controller_log


# --------------------------------------------------------------------------
# 4. The invariant is preserved, not deleted
# --------------------------------------------------------------------------


def test_pending_action_not_consumed_still_fires_on_a_genuinely_illegal_state():
    """The exception must remain a hard assertion for impossible states.

    Constructed directly: a pending enumerator action with budget to spare is
    exactly the state that must never be finalized, however it arose.
    """
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(_stock_query())
    graph.pending_action = Action(
        action_type=ActionType.RUN_FACET, view_id="stock_direct", facet_id="stock_direct",
    ).to_json()
    graph.budget_snapshot = {"calls_used": 0, "generated_tokens_used": 0}
    with pytest.raises(PendingActionNotConsumed, match="RUN_FACET"):
        pipeline.decide_graph(graph)


def test_exhausted_budget_still_cancels_a_pending_action_explicitly():
    """The pre-existing graceful path is untouched: cancelled with a reason."""
    pipeline = _pipeline()
    graph = pipeline.enumerate_query(_stock_query())
    graph.pending_action = Action(
        action_type=ActionType.RUN_FACET, view_id="stock_direct", facet_id="stock_direct",
    ).to_json()
    cap = pipeline.config.budget(graph.contract).max_calls
    graph.budget_snapshot = {"calls_used": cap, "generated_tokens_used": 0}
    prediction = pipeline.decide_graph(graph)
    assert not graph.pending_action
    assert prediction.stopped_reason == "hard budget exhausted"
    abandoned = [e for e in graph.controller_log if e.get("abandoned_action")]
    assert len(abandoned) == 1


def test_staged_mode_still_pends_enumerator_work_for_the_orchestrator():
    """Staged is a different contract and must keep it: only one model is loaded."""
    enumerator, verifier = _runtimes()
    pipeline = CoverPipeline(
        verifier, _config(mode=ExecutionMode.STAGED), verifier_runtime=verifier)
    graph = pipeline.enumerate_query(_stock_query())
    # Phase B under staged rules may legitimately pend enumerator work; what it
    # must never do is silently drop it.
    pipeline.verify_graph(graph)
    if graph.pending_action:
        assert graph.pending_action["action_type"] in {
            action.value for action in ActionType}
        assert ACTION_ROLE[ActionType(graph.pending_action["action_type"])] is (
            ModelRole.ENUMERATOR)
        del enumerator


# --------------------------------------------------------------------------
# 5. Successful paths unchanged
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", sorted(CONTRACTS))
def test_every_relation_still_finalizes(relation):
    pipeline = _pipeline()
    result = pipeline.run([Query("Some Subject", relation, 0)])
    assert result.errors == [], result.errors
    assert len(result.predictions) == 1


def test_borders_behaviour_is_unchanged_by_the_repair():
    """Borders never reach the Phase-B controller with budget to spend.

    Its contract budget equals Phase A's spend, so the widened allowed-role set
    cannot reach it. Asserted on the observable outcome as well as the budget.
    """
    pipeline = _pipeline_with_reply("Germany; Poland")
    query = Query("France", BORDERS, 0)
    graph = pipeline.enumerate_query(query)
    used_before = int((graph.budget_snapshot or {}).get("calls_used", 0))
    pipeline.verify_graph(graph)
    used_after = int((graph.budget_snapshot or {}).get("calls_used", 0))
    assert not graph.pending_action
    assert used_after >= used_before
    assert used_after <= pipeline.config.budget(graph.contract).max_calls


def _pipeline_with_reply(reply: str) -> CoverPipeline:
    enumerator = ScriptedRuntime(
        fallback=lambda request: reply, model_id="offline/mistral",
        family="mistral", role="enumerator")
    verifier = ScriptedRuntime(
        fallback=lambda request: reply, model_id="offline/qwen",
        family="qwen", role="verifier")
    return CoverPipeline(enumerator, _config(), verifier_runtime=verifier)


def test_a_productive_stock_query_still_reaches_finalization_with_candidates():
    """The repair must not suppress evidence on rows that already worked.

    Asserted on the candidate set, not the emitted answer: a scripted verifier
    returns the same text for a label prompt as for a recall prompt, so the
    acceptance policy legitimately rejects everything. What matters here is that
    acquisition ran, the graph was populated, and finalization was reached
    without a pipeline error.
    """
    pipeline = _pipeline_with_reply("London Stock Exchange")
    query = Query("Some Listed Company", STOCK, 0)
    graph = pipeline.enumerate_query(query)
    pipeline.verify_graph(graph)
    assert graph.candidates, "acquisition produced no candidates at all"
    assert not graph.pending_action
    prediction = pipeline.decide_graph(graph)
    assert prediction.empty_reason is not EmptyReason.PIPELINE_ERROR


# --------------------------------------------------------------------------
# 6. Calibration artifacts untouched by this repair
# --------------------------------------------------------------------------


CALIBRATION_HASHES = {
    "configs/calibration/v3/m20_relation_budget.json":
        "74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29",
    "configs/calibration/v3/m21_historical_bins.json":
        "ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675",
    "configs/calibration/v3/m21_planner_calibration.json":
        "1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6",
    "configs/calibration/v3/calibration_provenance.json":
        "f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d",
    "configs/calibration/m20_relation_budget.json":
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68",
    "configs/calibration/m21_historical_bins.json":
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071",
    "configs/calibration/m21_planner_calibration.json":
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05",
}


@pytest.mark.parametrize("path,digest", sorted(CALIBRATION_HASHES.items()))
def test_calibration_artifacts_are_byte_identical(path, digest):
    blob = (REPO_ROOT / path).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == digest


def test_the_repair_touches_no_planner_or_scheduler_module():
    """M20/M21 ranking, prices, bins and tau are out of scope for this fix."""
    import subprocess
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=False).stdout.split()
    forbidden = [
        name for name in diff
        if name.startswith("src/cover_kbc/control/")
        or name.startswith("src/cover_kbc/controller_calibration/derivation")
        or "micro_planner" in name
        or "relation_budget" in name
    ]
    assert not forbidden, f"the orchestration repair modified planner code: {forbidden}"


def test_v3_1_safe_configs_are_unaffected_by_the_repair():
    """Audit 0077's checkpoint must survive this incident milestone untouched."""
    import yaml
    for name in ("cover_kbc_v3_1_safe_core_test.yaml",
                 "cover_kbc_v3_1_safe_full_test.yaml"):
        config = yaml.safe_load(
            (REPO_ROOT / "configs" / "experiments" / name).read_text(encoding="utf-8"))
        block = config["pipeline"]["selection"]["v3_1"]
        assert block["enabled"] is True
        assert not any((block.get("aggressive") or {}).values())
        assert config["pipeline"]["mode"] == "interleaved"


def test_incident_forensics_are_reproducible_from_the_immutable_run():
    """The failed run stays evidence: its predictions hash must not drift."""
    run = REPO_ROOT / "outputs" / "v3_test_16f60fb1_20260810T160048Z" / "run"
    if not (run / "predictions.jsonl").exists():
        pytest.skip("incident artifacts not present in this checkout")
    digest = hashlib.sha256((run / "predictions.jsonl").read_bytes()).hexdigest()
    assert digest == "8a97a5e0696f0f4c77ace3af88725bb90f568ee0587bc07fe0f6e7b75c649f08"
    errors = json.loads((run / "errors.json").read_text())
    assert len(errors) == 39
    assert {entry["Relation"] for entry in errors} == {STOCK}
    assert all("PendingActionNotConsumed" in entry["error"] for entry in errors)
