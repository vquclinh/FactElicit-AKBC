"""A broken accounting invariant stops the run. It is not a row failure.

``BudgetLedger.settle`` refuses to record a settlement larger than the hold,
because that means a neural call happened outside the precharge - the one thing
§16's precharge exists to prevent. ``_release_hold`` reports that refusal as
``AccountingInvariantError``, and the reservation is deliberately left
``OUTSTANDING``: the ledger will not fabricate an impossible close.

Audit 0055 found the consequence unhandled. ``CoverPipeline.run`` caught it in
its generic per-row handler, wrote a ``PIPELINE_ERROR`` prediction, and carried
on - so later rows ran against a ledger holding an unclosable reservation and
the CLI still wrote a manifest and returned 0. Leaving the reservation
outstanding is only safe if nothing can ever observe that ledger again, which
means the run has to be fail-stop.

These tests assert that at both levels, and the CLI probe drives the **real**
``run_cover.main()`` - readiness gate, production Layer 6, artifact writing and
exit path included. Only two things are substituted, because a test machine has
neither: the model (a scripted non-neural runtime) and the rows (a synthetic
two-row file). No official split is read, no weights are loaded.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys

import pytest
import yaml

from cover_kbc.control.budget_types import (
    BudgetSchedulerError,
    CalibrationSource,
    RelationBudgetCalibration,
    ReservationStatus,
    SpecialReservePurpose,
)
from cover_kbc.control.relation_budget import RelationBudgetScheduler
from cover_kbc.data.loader import load_dataset
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.paths import REPO_ROOT
from cover_kbc.pipeline import AccountingInvariantError, CoverPipeline
from cover_kbc.types import EmptyReason, Query
from test_pipeline_production_seam import RELATION, SUBJECT, build
from test_production_activation import _write_artifacts

VAL_CONFIG = REPO_ROOT / "configs" / "experiments" / "cover_kbc_v2_validation.yaml"
SECOND_SUBJECT = "Secondland"


class _Boom(RuntimeError):
    """An ordinary Layer-4 executor failure, raised by the tests below."""


# --------------------------------------------------------------------------
# pipeline-level scaffolding
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


class _Underreserving(CoverPipeline):
    """The pre-Audit-0054 defect, reinstated as a fault injector.

    Claims every Module 17 contextual control is already cached. On a cold
    cache the hold is then 4 calls while the action really spends 8 - a genuine
    precharge violation produced by real code, not a hand-built exception.
    """

    entered: list[tuple[str, int]]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.entered = []

    def enumerate_query(self, query):
        self.entered.append((query.subject, query.row_index))
        return super().enumerate_query(query)

    def _m17_control_calls_needed(self, target, graph) -> int:
        return 0


def _pipeline(cls=_Underreserving, mode=IntegrationMode.PRODUCTION):
    scheduler = RelationBudgetScheduler({RELATION: _calibration()})
    return build(mode, relation_budget_scheduler=scheduler, pipeline_cls=cls)


def _two_queries() -> list[Query]:
    return [Query(SUBJECT, RELATION, 0), Query(SECOND_SUBJECT, RELATION, 1)]


# ==========================================================================
# 1-4, 10 — the pipeline
# ==========================================================================


def test_a_forced_under_reservation_raises_the_accounting_invariant() -> None:
    pipeline = _pipeline()
    with pytest.raises(AccountingInvariantError, match="could not settle"):
        pipeline.run([Query(SUBJECT, RELATION, 0)])


def test_run_re_raises_instead_of_recording_a_pipeline_error() -> None:
    """The exact Audit 0055 defect: it became a row failure and the run went on."""
    pipeline = _pipeline()
    with pytest.raises(AccountingInvariantError):
        pipeline.run([Query(SUBJECT, RELATION, 0)])
    # `run` returns nothing on this path, so there is no PipelineResult that
    # could carry a PIPELINE_ERROR row for the caller to write out.
    records = [r for r in pipeline.action_records if not r["executed"]]
    assert all(r.get("empty_reason") is None for r in records)


def test_a_two_row_run_stops_after_the_failing_row() -> None:
    pipeline = _pipeline()
    with pytest.raises(AccountingInvariantError):
        pipeline.run(_two_queries())
    assert pipeline.entered == [(SUBJECT, 0)], (
        f"row 2 was entered after the invariant broke: {pipeline.entered}")


def test_no_ledger_is_created_for_any_later_query() -> None:
    pipeline = _pipeline()
    with pytest.raises(AccountingInvariantError):
        pipeline.run(_two_queries())
    keys = sorted(pipeline._budget_ledgers)
    assert keys == [(SUBJECT, RELATION, 0)], keys
    # The failing ledger keeps its refused hold. That is correct - nothing may
    # fabricate a settlement of 8 against a hold of 4 - and it is safe only
    # because no later row can reach it.
    ledger = pipeline._budget_ledgers[(SUBJECT, RELATION, 0)]
    outstanding = [r for r in ledger.reservations
                   if r.status is ReservationStatus.OUTSTANDING]
    assert outstanding, "the refused hold was mutated to look closed"
    assert ledger.committed_calls <= ledger.plan.hard_calls
    assert ledger.plan.hard_calls == 64, "the hard cap was widened"


def test_the_exception_chain_keeps_both_failures() -> None:
    """An action can fail after spending, and *then* fail to settle.

    Both matter: the accounting invariant is what stops the run, the original
    failure is what needs debugging. Neither may erase the other.
    """
    from dataclasses import replace

    from cover_kbc.control.budget_accounting import specialist_verification_plan

    pipeline = _pipeline(cls=CoverPipeline)
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    pipeline.decide_graph(graph)                  # a healthy query first
    consensus = pipeline.consensus_results[-1]
    action = next(
        entry for entry in pipeline._catalogued_targets(consensus)
        if pipeline.project_action("m17", entry, graph) is not None)

    # Hold one call for an action that will really spend four, so settlement
    # is impossible; and make the action itself throw after it has spent.
    # Both failures happen, and both have to survive.
    thin = replace(
        pipeline.project_action("m17", action, graph).budget_descriptor,
        sub_calls=specialist_verification_plan(
            readings=1, control_calls_needed=0, controls_total=0))
    pipeline._action_descriptor = lambda kind, entry, g: thin
    original = pipeline.verify_specialist_targets

    def spend_then_explode(*args, **kwargs):
        original(*args, **kwargs)                 # real readings, real counters
        raise _Boom("original action failure after spend")

    pipeline.verify_specialist_targets = spend_then_explode
    with pytest.raises(AccountingInvariantError) as caught:
        pipeline.execute_action("m17", action, consensus, graph)

    chain = []
    error: BaseException | None = caught.value
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        chain.append(type(error))
        error = error.__cause__ or error.__context__
    assert chain[0] is AccountingInvariantError
    assert BudgetSchedulerError in chain
    assert _Boom in chain, f"the original action failure was lost: {chain}"


# ==========================================================================
# 11-12 — ordinary failures keep their existing behaviour
# ==========================================================================


def test_an_ordinary_row_failure_is_still_contained() -> None:
    """One malformed row must not kill 478. Only the invariant is fatal."""
    pipeline = _pipeline(cls=CoverPipeline)
    failed: list[str] = []
    original = pipeline.decide_graph

    def explode_once(graph):
        if graph.query.subject == SUBJECT and not failed:
            failed.append(SUBJECT)
            raise _Boom("an ordinary row problem")
        return original(graph)

    pipeline.decide_graph = explode_once
    result = pipeline.run(_two_queries())
    assert len(result.predictions) == 2
    assert result.predictions[0].empty_reason is EmptyReason.PIPELINE_ERROR
    assert result.predictions[1].empty_reason is not EmptyReason.PIPELINE_ERROR
    assert len(result.errors) == 1
    assert "_Boom" in result.errors[0]["error"]


def test_a_healthy_two_row_run_is_unaffected() -> None:
    pipeline = _pipeline(cls=CoverPipeline)
    result = pipeline.run(_two_queries())
    assert len(result.predictions) == 2
    assert result.errors == []
    for key, ledger in pipeline._budget_ledgers.items():
        assert [r for r in ledger.reservations
                if r.status is ReservationStatus.OUTSTANDING] == [], key


def test_ledger_and_runtime_still_agree_on_a_healthy_run() -> None:
    """Audit 0055's exactly-once invariant, re-checked after this change."""
    pipeline = _pipeline(cls=CoverPipeline)
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    pipeline.decide_graph(graph)
    ledger = pipeline._budget_ledgers[(SUBJECT, RELATION, 0)]
    spent = pipeline.query_physical_cost(graph)
    assert ledger.committed_calls == spent["physical_calls"]
    assert ledger.committed_tokens == spent["generated_tokens"]


def test_the_fatal_handler_precedes_the_generic_one() -> None:
    """Ordering is the whole fix; a later edit must not reverse it."""
    import inspect

    source = inspect.getsource(CoverPipeline.run)
    assert (source.index("except AccountingInvariantError:")
            < source.index("except Exception as exc:"))


# ==========================================================================
# 5-9 — the real CLI
# ==========================================================================


def _cli_harness(tmp_path, *, pipeline_cls):
    """The real ``run_cover.main()``, with only the model and the rows stubbed.

    Returns ``(runner, argv, out_dir)``. The config is the committed VAL config
    with its Layer-6 artifacts repointed at the activation fixture, so the
    readiness gate, the production calibration loader, Module 20 and Module 21
    are all the real ones and the run genuinely reaches
    ``FULL_VALIDATION_READY``.
    """
    artifacts = _write_artifacts(tmp_path / "calibration")
    config = yaml.safe_load(VAL_CONFIG.read_text())
    config["relation_budget_scheduler"].update({
        "calibration_file": str(artifacts["m20_relation_budget.json"]),
        "calibration_sha256": hashlib.sha256(
            artifacts["m20_relation_budget.json"].read_bytes()).hexdigest()})
    config["micro_planner"].update({
        "historical_bins": str(artifacts["m21_historical_bins.json"]),
        "historical_bins_sha256": hashlib.sha256(
            artifacts["m21_historical_bins.json"].read_bytes()).hexdigest(),
        "planner_calibration": str(artifacts["m21_planner_calibration.json"]),
        "planner_calibration_sha256": hashlib.sha256(
            artifacts["m21_planner_calibration.json"].read_bytes()).hexdigest()})
    # No weights on a test machine, and the parameter audit has nothing to
    # audit for a non-neural stub.
    config["model_profile"] = {
        "enumerator": {"backend": "scripted", "family": "offline-mistral",
                       "model_id": "offline/scripted-enumerator",
                       "role": "enumerator"},
        "verifier": {"backend": "scripted", "family": "offline-qwen",
                     "model_id": "offline/scripted-verifier",
                     "role": "verifier"}}
    config.pop("budget_assertion", None)
    config_path = tmp_path / "probe.yaml"
    config_path.write_text(yaml.safe_dump(config))

    # Two synthetic rows. No official split is read anywhere in this test.
    rows = tmp_path / "two_rows.jsonl"
    rows.write_text("\n".join(json.dumps(row) for row in (
        {"SubjectEntity": SUBJECT, "Relation": RELATION,
         "ObjectEntities": ["Alphaland"]},
        {"SubjectEntity": SECOND_SUBJECT, "Relation": RELATION,
         "ObjectEntities": ["Betaland"]},
    )) + "\n")

    spec = importlib.util.spec_from_file_location(
        f"run_cover_failstop_{tmp_path.name}", REPO_ROOT / "scripts" / "run_cover.py")
    runner = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec.loader.exec_module(runner)
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))

    runner.load_dataset = lambda split: load_dataset(split, path=rows)
    runner.build_runtime = lambda block: ScriptedRuntime(
        {}, model_id=block.get("model_id", "offline/scripted"),
        fallback=lambda request: "Alphaland, Betaland")
    runner.CoverPipeline = pipeline_cls

    out_dir = tmp_path / "run"
    argv = ["run_cover.py", "--config", str(config_path), "--no-eval",
            "--output-dir", str(out_dir)]
    return runner, argv, out_dir


@pytest.fixture
def fatal_cli(tmp_path, monkeypatch):
    runner, argv, out_dir = _cli_harness(tmp_path, pipeline_cls=_Underreserving)
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as caught:
        runner.main()
    return caught.value, out_dir


def test_the_cli_exits_non_zero(fatal_cli) -> None:
    exit_, _ = fatal_cli
    assert exit_.code not in (0, None), "the CLI reported success"


def test_the_cli_writes_no_manifest(fatal_cli) -> None:
    _, out_dir = fatal_cli
    assert not (out_dir / "manifest.json").exists()
    assert not (out_dir / "metrics.json").exists()


def test_the_cli_writes_no_predictions(fatal_cli) -> None:
    _, out_dir = fatal_cli
    assert not (out_dir / "predictions.jsonl").exists()
    assert not (out_dir / "trace.jsonl").exists()
    # Nor any per-module artifact that could be mistaken for a finished run.
    assert not list(out_dir.glob("*_specialist.jsonl"))
    assert not (out_dir / "relation_budget.jsonl").exists()


def test_the_only_artifacts_are_unmistakably_a_failure(fatal_cli) -> None:
    _, out_dir = fatal_cli
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == ["FAILED_ACCOUNTING_INVARIANT.json", "calls.jsonl"], names


def test_the_diagnostic_marker_cannot_pass_for_a_completion_record(
    fatal_cli,
) -> None:
    _, out_dir = fatal_cli
    marker = json.loads(
        (out_dir / "FAILED_ACCOUNTING_INVARIANT.json").read_text())
    assert marker["status"] == "aborted"
    assert marker["reason"] == "accounting_invariant"
    assert marker["complete"] is False
    assert marker["submittable"] is False
    assert marker["predictions_written"] is False
    assert marker["manifest_written"] is False
    assert marker["expected_queries"] == 2


def test_the_marker_records_the_whole_failure_chain(fatal_cli) -> None:
    _, out_dir = fatal_cli
    marker = json.loads(
        (out_dir / "FAILED_ACCOUNTING_INVARIANT.json").read_text())
    types = [f["type"] for f in marker["failures"]]
    assert types[0] == "AccountingInvariantError"
    assert "BudgetSchedulerError" in types
    assert any("outside the precharge" in f["message"] for f in marker["failures"])


def test_nothing_can_resume_from_the_failed_run(fatal_cli) -> None:
    """This entry point has no resume path, and the marker is not a checkpoint.

    ``run_cover.py`` writes no checkpoint and reads none, so there is no state
    a later invocation could silently continue from. Asserted rather than
    assumed, because "we do not have that feature" is exactly the kind of claim
    that stops being true quietly.
    """
    _, out_dir = fatal_cli
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    for option in ('"--resume"', '"--checkpoint"', '"--continue"'):
        assert f"add_argument({option}" not in source, (
            f"{option} exists; a resume path would need its own refusal")
    marker = json.loads(
        (out_dir / "FAILED_ACCOUNTING_INVARIANT.json").read_text())
    assert "checkpoint" not in marker
    assert "completed_rows" not in marker
    assert "not a manifest, a submission, or a resumable checkpoint" \
        in marker["detail"]


def test_re_running_into_the_failed_directory_restarts_and_fails_again(
    tmp_path, monkeypatch,
) -> None:
    """Behavioural proof, not an absence-of-feature claim.

    A second invocation pointed at the same output directory must not treat
    anything left behind as progress. It starts at row 1 again and fails again.
    """
    seen: list[tuple[str, int]] = []

    class Recording(_Underreserving):
        def enumerate_query(self, query):
            seen.append((query.subject, query.row_index))
            return super().enumerate_query(query)

    runner, argv, out_dir = _cli_harness(tmp_path, pipeline_cls=Recording)
    monkeypatch.setattr(sys, "argv", argv)
    for _ in range(2):
        with pytest.raises(SystemExit) as caught:
            runner.main()
        assert caught.value.code not in (0, None)
    assert seen == [(SUBJECT, 0), (SUBJECT, 0)], seen
    assert not (out_dir / "manifest.json").exists()
    assert not (out_dir / "predictions.jsonl").exists()


def test_the_failing_row_is_the_last_row_entered(tmp_path, monkeypatch) -> None:
    """Row 1 runs, the invariant breaks, row 2 is never entered."""
    seen: list[tuple[str, int]] = []

    class Recording(_Underreserving):
        def enumerate_query(self, query):
            seen.append((query.subject, query.row_index))
            return super().enumerate_query(query)

    runner, argv, _ = _cli_harness(tmp_path, pipeline_cls=Recording)
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        runner.main()
    assert seen == [(SUBJECT, 0)], seen


def test_the_same_cli_run_succeeds_without_the_fault(tmp_path, monkeypatch) -> None:
    """The control. Every absence above is caused by the fault, not the harness."""
    runner, argv, out_dir = _cli_harness(tmp_path, pipeline_cls=CoverPipeline)
    monkeypatch.setattr(sys, "argv", argv)
    assert runner.main() == 0
    assert (out_dir / "manifest.json").is_file()
    assert (out_dir / "predictions.jsonl").is_file()
    assert not (out_dir / "FAILED_ACCOUNTING_INVARIANT.json").exists()
    rows = [line for line in
            (out_dir / "predictions.jsonl").read_text().splitlines() if line.strip()]
    assert len(rows) == 2
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["num_queries"] == 2


def test_the_control_run_really_reaches_production_layer_six(
    tmp_path, monkeypatch, capsys,
) -> None:
    """Otherwise the fatal probe would prove nothing about production."""
    runner, argv, out_dir = _cli_harness(tmp_path, pipeline_cls=CoverPipeline)
    monkeypatch.setattr(sys, "argv", argv)
    assert runner.main() == 0
    printed = capsys.readouterr().out
    assert "readiness   : FULL_VALIDATION_READY" in printed
    assert (out_dir / "relation_budget.jsonl").is_file()
    assert (out_dir / "micro_planner.jsonl").is_file()
    assert (out_dir / "layer6_control.jsonl").is_file()
