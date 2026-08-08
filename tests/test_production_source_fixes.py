"""The three source fixes Audit 0051 left open: P2-A, P1-A and execution mode.

Each is a defect that only became visible once Layer 6 was switched on against
the real calibration, and each is fixed in the canonical owner rather than
worked around at the call site:

* **P2-A** - ``lookahead_depth`` is a package-wide property, so it may only be
  2 when every shipped bin can be planned two steps from.
* **P1-A** - Module 20 owns the calibrated envelope for the upgraded action
  space; Module 7's core ceiling governs the core phase and is not applied to
  it a second time.
* **execution mode** - the config declares it and the runner honours it.

The real artifacts are used read-only, to prove the fixes against the bytes a
production run will actually load. Nothing here regenerates or edits them.
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
    BudgetDenialReason,
    BudgetSchedulerError,
    CacheDisposition,
    CoreBudgetSnapshot,
)
from cover_kbc.control.historical_bins import (
    HistoricalActionBin,
    HistoricalBinPackage,
    StateBinningSpec,
    SuccessorStat,
    load_history,
)
from cover_kbc.control.planner_types import ActionFamily, EstimateSource
from cover_kbc.control.relation_budget import (
    build_plan,
    load_calibrations,
    relation_policy,
)
from cover_kbc.controller_calibration.derivation import supports_depth_two
from cover_kbc.paths import REPO_ROOT
from cover_kbc.pipeline import ExecutionMode, PipelineConfig

CALIBRATION = REPO_ROOT / "configs" / "calibration"
VAL_CONFIG = REPO_ROOT / "configs" / "experiments" / "cover_kbc_v2_validation.yaml"
COLLECTION_CONFIG = (
    REPO_ROOT / "configs" / "experiments" / "cover_kbc_v2_train_collection.yaml")

#: The calibrated whole-query envelopes, from the real M20 artifact. These are
#: the numbers the plan must preserve; Module 7's core ceilings are 12/5/4/4/4/4
#: and must not replace them.
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


# ==========================================================================
# P2-A — package-wide lookahead depth
# ==========================================================================


def _bin(relation="hasArea", program_type="NUMERIC", state="__fallback__",
         family=ActionFamily.SPECIALIST_VERIFY, successors=()):
    return HistoricalActionBin(
        relation=relation, program_type=program_type, state_bin_key=state,
        action_family=family, support_count=40,
        expected_verified_gain=0.5, expected_delta_r=0.2, expected_delta_h=0.0,
        expected_cost=2.0, expected_redundancy=0.1, expected_fp=0.05,
        successors=successors)


def _package(bins, fallback="__fallback__"):
    return HistoricalBinPackage(
        history_version="v", source=EstimateSource.TRAIN_CALIBRATED,
        binning=StateBinningSpec(spec_version="s"), bins=tuple(bins),
        minimum_bin_support=None, fallback_state_bin=fallback)


_TO_FALLBACK = (SuccessorStat(probability=1.0,
                              successor_state_bin="__fallback__"),)


def test_no_successors_anywhere_gives_depth_one() -> None:
    ok, reasons = supports_depth_two(_package([_bin(), _bin(state="s1")]))
    assert ok is False
    assert len(reasons) == 2


def test_successors_in_some_but_not_all_bins_gives_depth_one() -> None:
    """The exact rule that was wrong: ``any`` is not enough."""
    package = _package([
        _bin(state="__fallback__", successors=_TO_FALLBACK),
        _bin(state="s1"),                      # no successors
    ])
    ok, reasons = supports_depth_two(package)
    assert ok is False
    assert any("s1" in reason for reason in reasons)


def test_successors_in_every_bin_gives_depth_two() -> None:
    package = _package([
        _bin(state="__fallback__", successors=_TO_FALLBACK),
        _bin(state="s1", successors=_TO_FALLBACK),
    ])
    ok, reasons = supports_depth_two(package)
    assert ok is True and reasons == ()


def test_a_terminal_high_support_bin_blocks_depth_two() -> None:
    """A well-supported bin with no successor is exactly the real case."""
    terminal = HistoricalActionBin(
        relation="countryLandBordersCountry", program_type="SMALL_SET",
        state_bin_key="__fallback__", action_family=ActionFamily.REVERSE_CHECK,
        support_count=67, expected_verified_gain=0.4, expected_delta_r=0.1,
        expected_delta_h=0.0, expected_cost=1.0, expected_redundancy=0.0,
        expected_fp=0.1, successors=())
    ok, reasons = supports_depth_two(_package([
        _bin(relation="countryLandBordersCountry", program_type="SMALL_SET",
             successors=_TO_FALLBACK),
        terminal,
    ]))
    assert ok is False
    assert any("support 67" in reason for reason in reasons)


def test_a_fallback_bin_without_successors_blocks_depth_two() -> None:
    """`lookup` routes any unseen state here, so it is always rankable."""
    ok, reasons = supports_depth_two(_package([
        _bin(state="s1", successors=_TO_FALLBACK),
        _bin(state="__fallback__"),
    ]))
    assert ok is False
    assert any("__fallback__" in reason for reason in reasons)


def test_an_unresolvable_successor_bin_blocks_depth_two() -> None:
    """The lookahead's *second* raise site, not just the first."""
    package = HistoricalBinPackage(
        history_version="v", source=EstimateSource.TRAIN_CALIBRATED,
        binning=StateBinningSpec(spec_version="s"),
        bins=(_bin(state="s1", successors=(SuccessorStat(
            probability=1.0, successor_state_bin="a state nothing ships"),)),),
        minimum_bin_support=None, fallback_state_bin="")   # no fallback either
    ok, reasons = supports_depth_two(package)
    assert ok is False
    assert any("does not resolve" in reason for reason in reasons)


def test_the_depth_rule_is_deterministic() -> None:
    package = _package([_bin(state="s1"), _bin(state="s2")])
    assert supports_depth_two(package) == supports_depth_two(package)


def test_the_depth_rule_only_ever_yields_one_or_two() -> None:
    """§17 permits 1-2 step micro-lookahead and nothing else."""
    from cover_kbc.control.planner_types import PlannerCalibration

    for supported in (True, False):
        depth = 2 if supported else 1
        assert depth in (1, 2)
        PlannerCalibration(
            calibration_version="v", source=EstimateSource.TRAIN_CALIBRATED,
            alpha=1.0, beta=0.0, gamma=0.0, delta=0.0, eta=0.0, kappa=1.0,
            tau_continue=0.0, lookahead_depth=depth)


def test_there_is_no_runtime_silent_downgrade() -> None:
    """The planner must still raise; the depth is decided at derivation."""
    import inspect

    from cover_kbc.control import micro_planner as module

    source = inspect.getsource(module.MicroPlanner._lookahead)
    assert "raise PlannerError" in source
    whole = inspect.getsource(module)
    # No catch-and-retry-at-depth-1 anywhere in the planner.
    assert "except PlannerError" not in whole


@_artifacts
def test_the_real_package_resolves_to_depth_one() -> None:
    """The expected corrected depth for the currently observed 64-bin package."""
    history = load_history(
        json.loads((CALIBRATION / "m21_historical_bins.json").read_text()))
    ok, reasons = supports_depth_two(history)
    assert ok is False
    assert len(reasons) == 5
    assert (2 if ok else 1) == 1


@_artifacts
def test_the_derivation_would_now_choose_depth_one_for_the_real_package() -> None:
    """The rule as the derivation applies it, not a paraphrase of it."""
    import inspect

    from cover_kbc.controller_calibration import derivation

    source = inspect.getsource(derivation.derive_planner_calibration)
    assert "lookahead_depth=2 if depth_two else 1" in source
    assert "any(b.successors" not in source

    history = load_history(
        json.loads((CALIBRATION / "m21_historical_bins.json").read_text()))
    depth_two, _ = supports_depth_two(history)
    assert (2 if depth_two else 1) == 1


# ==========================================================================
# P1-A — Module 7 / Module 20 budget ownership
# ==========================================================================


def _profile(relation: str, program_type: str):
    grade = NS(value="LOW")
    return NS(relation=relation, subject="S", row_index=0,
              program_type=program_type, profiler_version="m9-v1",
              search_breadth=grade, verification_priority=grade,
              temporal_sensitivity=grade, near_miss_risk=grade,
              open_set_risk=grade)


@pytest.fixture(scope="module")
def real_budgets():
    if not (CALIBRATION / "m20_relation_budget.json").is_file():
        pytest.skip("real artifacts absent")
    return load_calibrations(
        json.loads((CALIBRATION / "m20_relation_budget.json").read_text()))


@pytest.fixture(scope="module")
def core_budgets():
    config = yaml.safe_load(VAL_CONFIG.read_text())
    block = dict(config["pipeline"])
    block["mode"] = ExecutionMode.INTERLEAVED.value
    parsed = PipelineConfig.from_mapping(block)
    return {name: CoreBudgetSnapshot.of(parsed.budget(contract))
            for name, contract in CONTRACTS.items()}


def _plan(relation, real_budgets, core_budgets):
    contract = CONTRACTS[relation]
    return build_plan(
        subject="S", relation=relation, row_index=0,
        program_type=contract.program_type.value,
        profile=_profile(relation, contract.program_type.value),
        core_budget=core_budgets[relation], calibration=real_budgets[relation])


@_artifacts
@pytest.mark.parametrize("relation", sorted(REAL_HARD_CALLS))
def test_the_plan_preserves_the_exact_calibrated_envelope(
    relation, real_budgets, core_budgets,
) -> None:
    """No ``min(core_max_calls, m20_hard_calls)`` anywhere."""
    plan = _plan(relation, real_budgets, core_budgets)
    assert plan.hard_calls == REAL_HARD_CALLS[relation]
    assert plan.hard_calls == real_budgets[relation].hard_calls
    assert plan.hard_generated_tokens == (
        real_budgets[relation].hard_generated_tokens)


@_artifacts
@pytest.mark.parametrize("relation", sorted(REAL_HARD_CALLS))
def test_the_core_ceiling_does_not_replace_the_calibrated_one(
    relation, real_budgets, core_budgets,
) -> None:
    plan = _plan(relation, real_budgets, core_budgets)
    core = core_budgets[relation].max_calls
    assert core in (12, 5, 4)
    if REAL_HARD_CALLS[relation] > core:
        assert plan.hard_calls != core, (
            "the core ceiling replaced the calibrated envelope again")


@_artifacts
def test_the_award_protected_floor_now_fits_inside_its_ceiling(
    real_budgets, core_budgets,
) -> None:
    """Was floor 14 inside ceiling 12 - an envelope nothing could satisfy."""
    plan = _plan("awardWonBy", real_budgets, core_budgets)
    verification = next(e for e in plan.envelopes if e.name == "verification")
    assert verification.protected_floor == 14
    assert plan.hard_calls == 44
    assert verification.protected_floor <= plan.hard_calls
    for envelope in plan.envelopes:
        assert envelope.protected_floor <= plan.hard_calls
        assert envelope.cap <= plan.hard_calls


@_artifacts
def test_the_core_phase_still_obeys_its_own_ceiling(core_budgets) -> None:
    """Module 7's budget is untouched by any of this."""
    assert core_budgets["countryLandBordersCountry"].max_calls == 4
    assert core_budgets["awardWonBy"].max_calls == 12
    config = yaml.safe_load(VAL_CONFIG.read_text())
    assert config["pipeline"]["max_calls_per_query"] == 12


@_artifacts
def test_spend_classes_and_reserves_survive(real_budgets, core_budgets) -> None:
    for relation in sorted(REAL_HARD_CALLS):
        plan = _plan(relation, real_budgets, core_budgets)
        names = {e.name for e in plan.envelopes}
        assert {"discovery", "verification"} <= names
        declared = set(relation_policy(relation).special_reserve_purposes)
        for envelope in plan.envelopes:
            if envelope.special_purpose is not None:
                assert envelope.special_purpose in declared


# -- prior spend, charged exactly once --------------------------------------


def test_the_calibrated_envelope_survives_any_core_ceiling() -> None:
    """Ownership, stated without the real artifacts: no intersection at all."""
    from cover_kbc.control.budget_types import (
        CalibrationSource,
        RelationBudgetCalibration,
    )

    calibration = RelationBudgetCalibration(
        relation="hasArea", calibration_version="v",
        calibration_source=CalibrationSource.TRAIN_CALIBRATED,
        hard_calls=22, hard_generated_tokens=197,
        discovery_cap=1, verification_cap=10, verification_reserve=0)
    for core_max in (1, 4, 12, 22, 500):
        plan = build_plan(
            subject="S", relation="hasArea", row_index=0,
            program_type=CONTRACTS["hasArea"].program_type.value,
            profile=_profile("hasArea", CONTRACTS["hasArea"].program_type.value),
            core_budget=CoreBudgetSnapshot(
                max_calls=core_max, calls_used=0,
                max_generated_tokens=1024, generated_tokens_used=0),
            calibration=calibration)
        assert plan.hard_calls == 22, core_max
        assert plan.hard_generated_tokens == 197, core_max


def test_a_calibrated_envelope_above_the_core_ceiling_is_recorded_not_clipped(
) -> None:
    """The divergence is auditable: a note, not a silent reduction."""
    from cover_kbc.control.budget_types import (
        CalibrationSource,
        RelationBudgetCalibration,
    )

    plan = build_plan(
        subject="S", relation="hasArea", row_index=0,
        program_type=CONTRACTS["hasArea"].program_type.value,
        profile=_profile("hasArea", CONTRACTS["hasArea"].program_type.value),
        core_budget=CoreBudgetSnapshot(
            max_calls=4, calls_used=0, max_generated_tokens=100,
            generated_tokens_used=0),
        calibration=RelationBudgetCalibration(
            relation="hasArea", calibration_version="v",
            calibration_source=CalibrationSource.TRAIN_CALIBRATED,
            hard_calls=22, hard_generated_tokens=197,
            discovery_cap=1, verification_cap=10, verification_reserve=0))
    joined = " ".join(plan.notes)
    assert "core-phase ceiling 4" in joined
    assert "core-phase ceiling 100" in joined
    assert plan.hard_calls == 22


def test_build_plan_no_longer_intersects_the_two_ceilings() -> None:
    import inspect

    from cover_kbc.control import relation_budget as module

    source = inspect.getsource(module.build_plan)
    assert "min(" not in source.split("envelopes = [")[0], (
        "build_plan intersects a ceiling before building envelopes again")
    assert "_note_envelope(" in source
    assert not hasattr(module, "_intersect_ceiling")


@_artifacts
def test_prior_physical_spend_reduces_availability_exactly_once(
    real_budgets, core_budgets,
) -> None:
    plan = _plan("countryLandBordersCountry", real_budgets, core_budgets)
    fresh = BudgetLedger(plan)
    spent = BudgetLedger(plan, prior_calls=9, prior_tokens=40)
    assert fresh.committed_calls == 0
    assert spent.committed_calls == 9
    assert spent.prior_calls == 9
    assert (plan.hard_calls - spent.committed_calls) == plan.hard_calls - 9


def _m17_descriptor(relation: str):
    """One real Module 17 action's descriptor, from Module 17's own catalogue.

    Built through :func:`m17_actions` with the live verifier configuration, so
    the reading and control counts are the production ones (four readings plus
    four contextual controls) rather than a number this file chose.
    """
    from cover_kbc.control.action_catalog import m17_actions
    from cover_kbc.verification.specialist_types import (
        VerificationTarget,
        VerificationTargetKind,
    )

    target = VerificationTarget(
        relation=relation, subject="S", row_index=0,
        kind=VerificationTargetKind.ENTITY_CANDIDATE, target_id="c1",
        display="Spain")
    actions, _ = m17_actions((target,), subject="S", relation=relation,
                             row_index=0)
    assert actions, "no Module 17 action projected"
    return actions[0].budget_descriptor


@_artifacts
def test_the_m17_descriptor_is_the_live_four_reading_plan() -> None:
    """Guards the fixture: the tests below mean nothing on a two-call action."""
    descriptor = _m17_descriptor("hasArea")
    readings = [s for s in descriptor.sub_calls
                if s.cache is not CacheDisposition.CACHE_HIT]
    assert len(readings) == 4
    assert len(descriptor.sub_calls) == 8


@_artifacts
def test_a_new_layer_four_call_reduces_availability_exactly_once(
    real_budgets, core_budgets,
) -> None:
    plan = _plan("countryLandBordersCountry", real_budgets, core_budgets)
    ledger = BudgetLedger(plan, prior_calls=9)
    descriptor = _m17_descriptor("countryLandBordersCountry")
    before = ledger.committed_calls
    reservation = ledger.reserve(descriptor)
    assert not isinstance(reservation, BudgetDenial), reservation
    charged = ledger.committed_calls - before
    non_cacheable = sum(1 for sub in descriptor.sub_calls
                        if sub.cache is not CacheDisposition.CACHE_HIT)
    assert charged == non_cacheable
    # Settling the reservation at its actual cost must not charge it again.
    ledger.settle(reservation.reservation_id, actual_calls=charged)
    assert ledger.committed_calls == before + charged


@_artifacts
def test_cache_hits_cost_nothing(real_budgets, core_budgets) -> None:
    descriptor = _m17_descriptor("countryLandBordersCountry")
    hits = [s for s in descriptor.sub_calls
            if s.cache is CacheDisposition.CACHE_HIT]
    assert hits, "the fixture should include cached controls"
    plan = _plan("countryLandBordersCountry", real_budgets, core_budgets)
    ledger = BudgetLedger(plan)
    ledger.reserve(descriptor)
    assert ledger.committed_calls == len(descriptor.sub_calls) - len(hits)


@_artifacts
def test_a_four_reading_m17_action_is_affordable_on_a_starved_relation(
    real_budgets, core_budgets,
) -> None:
    """The invariant P1-A exists for - affordability only, never selection.

    Four relations previously answered ``NO_AFFORDABLE_ACTION`` because the
    core ceiling of 4 had replaced a calibrated envelope of 22-24. This asserts
    the action can now be funded; whether Module 21 *wants* it is §17's
    business and is not forced here.

    The query enters Layer 4 having already spent Module 7's **entire** core
    ceiling, which is the worst realistic case and the one the old arithmetic
    reduced to zero remaining capacity.
    """
    for relation in ("countryLandBordersCountry", "hasArea", "hasCapacity",
                     "personHasCityOfDeath"):
        plan = _plan(relation, real_budgets, core_budgets)
        descriptor = _m17_descriptor(relation)
        needed = sum(1 for s in descriptor.sub_calls
                     if s.cache is not CacheDisposition.CACHE_HIT)
        spent = core_budgets[relation].max_calls
        ledger = BudgetLedger(plan, prior_calls=spent)
        # The envelope the old `min(...)` produced is already fully consumed,
        # so this case discriminates: it funded nothing before the fix.
        assert spent >= min(real_budgets[relation].hard_calls, spent)
        assert ledger.available_calls(descriptor) >= needed, relation
        outcome = ledger.reserve(descriptor)
        assert not isinstance(outcome, BudgetDenial), (relation, outcome)


@_artifacts
def test_an_action_beyond_the_remaining_envelope_is_denied(
    real_budgets, core_budgets,
) -> None:
    plan = _plan("hasArea", real_budgets, core_budgets)
    # Open the ledger with the whole envelope already spent.
    ledger = BudgetLedger(plan, prior_calls=plan.hard_calls)
    descriptor = _m17_descriptor("hasArea")
    assert ledger.available_calls(descriptor) == 0
    outcome = ledger.reserve(descriptor)
    assert isinstance(outcome, BudgetDenial), "an unaffordable action was admitted"
    assert outcome.reason is BudgetDenialReason.DENIED_BY_HARD_CAP


@_artifacts
def test_prior_spend_cannot_be_released_or_settled_away(
    real_budgets, core_budgets,
) -> None:
    """Prior spend is already spent: no Layer-4 bookkeeping may refund it."""
    plan = _plan("hasArea", real_budgets, core_budgets)
    ledger = BudgetLedger(plan, prior_calls=7)
    descriptor = _m17_descriptor("hasArea")
    reservation = ledger.reserve(descriptor)
    assert not isinstance(reservation, BudgetDenial), reservation
    ledger.cancel(reservation.reservation_id)
    assert ledger.committed_calls == 7
    assert ledger.prior_calls == 7


def test_negative_prior_spend_is_refused() -> None:
    plan = NS(is_numeric=True, calibration=None)
    with pytest.raises(BudgetSchedulerError, match="negative"):
        BudgetLedger(plan, prior_calls=-1)
    with pytest.raises(BudgetSchedulerError, match="negative"):
        BudgetLedger(plan, prior_tokens=-1)


def test_the_pipeline_reuses_its_own_query_scoped_counter() -> None:
    """No parallel call counter: the ledger is fed from `query_physical_cost`."""
    import inspect

    from cover_kbc.pipeline import CoverPipeline

    source = inspect.getsource(CoverPipeline._budget_ledger_for)
    assert "self.query_physical_cost(graph)" in source
    assert "prior_calls=" in source and "prior_tokens=" in source


# ==========================================================================
# execution mode
# ==========================================================================


def test_the_val_config_declares_interleaved() -> None:
    config = yaml.safe_load(VAL_CONFIG.read_text())
    assert config["pipeline"]["mode"] == "interleaved"


def test_validation_matches_the_mode_the_calibration_was_measured_under() -> None:
    """The collection ran interleaved, so validation must too."""
    validation = yaml.safe_load(VAL_CONFIG.read_text())
    collection = yaml.safe_load(COLLECTION_CONFIG.read_text())
    assert validation["pipeline"]["mode"] == collection["pipeline"]["mode"]
    assert validation["pipeline"]["mode"] == ExecutionMode.INTERLEAVED.value


def test_the_runner_resolves_the_configured_mode() -> None:
    runner = _load_runner()
    assert runner.resolve_execution_mode(
        {"pipeline": {"mode": "interleaved"}}) is ExecutionMode.INTERLEAVED
    assert runner.resolve_execution_mode(
        {"pipeline": {"mode": "staged"}}) is ExecutionMode.STAGED
    # No pipeline block at all: the historical default, stated explicitly.
    assert runner.resolve_execution_mode({}) is ExecutionMode.INTERLEAVED


@pytest.mark.parametrize("mode", ["Interleaved", "batch", "", "parallel", "none"])
def test_an_unsupported_mode_fails_closed(mode) -> None:
    runner = _load_runner()
    with pytest.raises(SystemExit, match="not a supported execution mode"):
        runner.resolve_execution_mode({"pipeline": {"mode": mode}})


def test_the_runner_no_longer_hardcodes_interleaved() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    assert 'config_block["mode"] = ExecutionMode.INTERLEAVED.value' not in source
    assert 'config_block["mode"] = execution_mode.value' in source


def test_the_val_config_resolves_to_the_mode_it_declares() -> None:
    runner = _load_runner()
    config = yaml.safe_load(VAL_CONFIG.read_text())
    resolved = runner.resolve_execution_mode(config)
    assert resolved is ExecutionMode.INTERLEAVED
    assert resolved.value == config["pipeline"]["mode"]


def _load_runner():
    import importlib.util
    import sys

    path = REPO_ROOT / "scripts" / "run_cover.py"
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("run_cover_modes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


# -- the mode must be refused *before* a model is built ---------------------
#
# Static ordering is not the claim; "no weights were loaded" is. These drive
# the real `main()` with `build_runtime` replaced by a counter, so the
# assertion is on what the entry point actually did (Audit 0053 P2).


class _RuntimeBuilt(RuntimeError):
    """Raised by the stub the moment `main` reaches runtime construction."""


def _drive_main(tmp_path, monkeypatch, mode):
    """Run `run_cover.main()` on the real VAL config with `mode` substituted.

    Returns the list of `build_runtime` calls that happened before the run
    stopped. `build_runtime` never returns, so no weight is ever loaded and the
    entry point cannot proceed past it.
    """
    import sys

    runner = _load_runner()
    config = yaml.safe_load(VAL_CONFIG.read_text())
    if mode is None:
        config["pipeline"].pop("mode", None)
    else:
        config["pipeline"]["mode"] = mode
    path = tmp_path / "probe.yaml"
    path.write_text(yaml.safe_dump(config))

    built: list = []

    def stub(block):
        built.append(block)
        raise _RuntimeBuilt("main reached build_runtime")

    monkeypatch.setattr(runner, "build_runtime", stub)
    monkeypatch.setattr(sys, "argv", ["run_cover.py", "--config", str(path)])
    return runner, built, path


@pytest.mark.parametrize("mode", ["bogus", "Interleaved", "batch", "parallel"])
def test_an_unsupported_mode_builds_no_runtime_at_all(
    tmp_path, monkeypatch, mode,
) -> None:
    """The P2 property: refusal costs zero model construction."""
    runner, built, _ = _drive_main(tmp_path, monkeypatch, mode)
    with pytest.raises(SystemExit, match="not a supported execution mode"):
        runner.main()
    assert built == [], (
        f"{len(built)} runtime(s) were built before the mode was refused")


def test_a_valid_mode_still_reaches_runtime_construction(
    tmp_path, monkeypatch,
) -> None:
    """The refusal must be specific, not a blanket early exit."""
    runner, built, _ = _drive_main(tmp_path, monkeypatch, "interleaved")
    with pytest.raises(_RuntimeBuilt):
        runner.main()
    assert len(built) == 1, "the enumerator runtime was not reached"


def test_the_staged_mode_also_reaches_runtime_construction(
    tmp_path, monkeypatch,
) -> None:
    runner, built, _ = _drive_main(tmp_path, monkeypatch, "staged")
    with pytest.raises(_RuntimeBuilt):
        runner.main()
    assert len(built) == 1


def test_an_absent_mode_still_reaches_runtime_construction(
    tmp_path, monkeypatch,
) -> None:
    runner, built, _ = _drive_main(tmp_path, monkeypatch, None)
    with pytest.raises(_RuntimeBuilt):
        runner.main()
    assert len(built) == 1


def test_the_mode_is_resolved_before_build_runtime_in_source() -> None:
    """Ordering, stated once in the file itself so a later edit cannot undo it."""
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    body = source.split("def main(", 1)[1]
    assert (body.index("resolve_execution_mode(config)")
            < body.index("build_runtime(enumerator_cfg)"))
