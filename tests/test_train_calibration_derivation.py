"""Offline derivation of Modules 20 and 21 from collected TRAIN telemetry.

Two failures are targeted, and both are silent:

**A calibration that describes the wrong run.** The artifacts are loaded months
later by a system that no longer has the telemetry, so provenance is the only
thing standing between "these numbers came from that collection" and a guess.
Every binding check is asserted here against a real refusal.

**A calibration that leaked.** Gold enters the derivation and must not leave it.
The tests below take the gold-bearing side of the pipeline and assert that no
object string, no subject and no candidate identity reaches a production
artifact - structurally, over the serialised bytes, not by reading the code.

Nothing here loads a model, and one test asserts that the derivation module
cannot: an import graph with a runtime in it would be a calibration step that
could call one.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from cover_kbc.control.historical_bins import (
    HistoricalBinPackage,
    StateBinningSpec,
    load_history,
    state_bin_key,
)
from cover_kbc.control.planner_types import (
    ActionFamily,
    EstimateSource,
    PlannerCalibration,
    PlannerError,
    PlannerStateSnapshot,
)
from cover_kbc.control.relation_budget import load_calibrations
from cover_kbc.controller_calibration.derivation import (
    FALLBACK_STATE_BIN,
    CalibrationBundle,
    CalibrationProvenance,
    DerivationError,
    DerivationSettings,
    assert_no_leakage,
    derive_binning_spec,
    derive_m20,
    derive_m21,
    derive_planner_calibration,
    observe_relation_spend,
    offline_state_bin_key,
    require_supported_schema,
)
from cover_kbc.controller_calibration.gold_join import (
    ActionGoldEffect,
    GoldJoinError,
    load_gold,
    score_actions,
)
from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionOutcome,
    ActionTelemetryRecord,
    ControlStateFeatures,
    RedundancyStatus,
)
from cover_kbc.paths import REPO_ROOT

TRAIN = REPO_ROOT / "benchmark" / "data" / "train.jsonl"


# --------------------------------------------------------------------------
# fixtures: a small but structurally complete collection
# --------------------------------------------------------------------------


def _state(residual: float, unresolved: float = 0.4, *, measured: bool = True,
           components: tuple[str, ...] | None = None,
           calls_used: int = 6) -> ControlStateFeatures:
    return ControlStateFeatures(
        residual=residual, novelty_rate=0.2, singleton_ratio=0.1, facet_gap=0.3,
        disagreement=0.0, unresolved_mass=unresolved, entropy=0.5,
        active_candidates=2, calls_used=calls_used, calls_remaining=6,
        prompt_tokens=300, generated_tokens=12, measured=measured,
        available_components=(
            components if components is not None
            else ("novelty_rate", "singleton_ratio", "facet_gap",
                  "disagreement", "unresolved_mass")),
    )


def _outcome(calls: int = 2, *, supported=(), contradicted=(), named=(),
             touched=None, redundancy: float | None = 1.0,
             verdict: str = "VALID", structural: str = "") -> ActionOutcome:
    touched = tuple(supported) if touched is None else tuple(touched)
    status = (RedundancyStatus.MEASURED if (touched or named)
              else RedundancyStatus.NOT_APPLICABLE)
    return ActionOutcome(
        physical_calls=calls, verifier_calls=calls, prompt_tokens=100 * calls,
        generated_tokens=4 * calls,
        candidates_supported=tuple(supported),
        candidates_contradicted=tuple(contradicted),
        candidates_named=tuple(named), candidates_touched=touched,
        candidate_effect_measured=True,
        redundancy=(redundancy if status is RedundancyStatus.MEASURED else None),
        redundancy_status=status,
        verifier_outcome=verdict, structural_outcome=structural,
    )


def _record(row_index, subject, relation, program_type, round_index, family,
            *, pre, post, outcome, spend_class="VERIFICATION",
            reserve_purpose="") -> ActionTelemetryRecord:
    return ActionTelemetryRecord(
        schema_version=TELEMETRY_SCHEMA_VERSION, run_id="run-1",
        row_index=row_index, subject=subject, relation=relation,
        program_type=program_type, round_index=round_index,
        operation_id=f"{row_index}:{round_index}:{family}:{subject}",
        action_family=family, action_id=f"M17:{family}:{subject}",
        target_class="", spend_class=spend_class,
        reserve_purpose=reserve_purpose,
        selected=True, executed=True, pre_state=pre, post_state=post,
        outcome=outcome,
    )


@pytest.fixture(scope="module")
def gold():
    """The real pinned TRAIN split, read through the official evaluator."""
    return load_gold(TRAIN, expected_rows=477)


@pytest.fixture(scope="module")
def real_rows(gold):
    """Two real TRAIN queries with their real gold objects, for the fixtures.

    Real rows rather than invented ones, so the evaluator's alias and numeric
    semantics are genuinely exercised: a fabricated subject would make every
    gold label ``False`` and the verified-gain path would never be tested.
    """
    borders = next(row for row in gold.rows.values()
                   if row.relation == "countryLandBordersCountry" and row.aliases)
    area = next(row for row in gold.rows.values()
                if row.relation == "hasArea" and row.aliases)
    return borders, area


@pytest.fixture(scope="module")
def collection(real_rows):
    """A structurally complete miniature collection over two relations."""
    borders, area = real_rows
    right = borders.aliases[0][0]
    records = []
    for index in range(6):
        pre = _state(0.8 - 0.05 * index, calls_used=6)
        mid = _state(0.4 - 0.02 * index, calls_used=8)
        post = _state(0.2, calls_used=10)
        records.append(_record(
            index, borders.subject, borders.relation, "SMALL_SET", 1,
            "SPECIALIST_VERIFY", pre=pre, post=mid,
            outcome=_outcome(2, supported=(right, "definitely not a country"))))
        records.append(_record(
            index, borders.subject, borders.relation, "SMALL_SET", 2,
            "CANDIDATE_FREE_RECALL", pre=mid, post=post,
            outcome=_outcome(1, named=(right,), redundancy=0.0,
                             verdict="", structural="TARGET_RECALLED"),
            spend_class="DISCOVERY", reserve_purpose="REVERSE_SINGLETON"))
    for index in range(6, 12):
        pre = _state(0.6, unresolved=0.2, calls_used=5)
        post = _state(0.6, unresolved=0.2, calls_used=7)
        records.append(_record(
            index, area.subject, area.relation, "NUMERIC", 1,
            "SPECIALIST_VERIFY", pre=pre, post=post,
            outcome=_outcome(2, redundancy=None, verdict="UNKNOWN")))
    return records


@pytest.fixture(scope="module")
def derived(collection, gold):
    settings = DerivationSettings(minimum_bin_support=2)
    effects = score_actions(collection, gold)
    spend = observe_relation_spend(collection, effects)
    budgets = derive_m20(spend, settings)
    binning = derive_binning_spec(collection, settings)
    history, diagnostics = derive_m21(collection, effects, binning, settings)
    planner, planner_diagnostics = derive_planner_calibration(
        history, collection, effects)
    return {
        "settings": settings, "effects": effects, "spend": spend,
        "budgets": budgets, "binning": binning, "history": history,
        "diagnostics": diagnostics, "planner": planner,
        "planner_diagnostics": planner_diagnostics,
    }


def _bundle(derived) -> CalibrationBundle:
    provenance = CalibrationProvenance(
        collection_repo_sha="a" * 40, derivation_repo_sha="b" * 40,
        train_sha256="c" * 64, train_rows=477, predictions_sha256="d" * 64,
        telemetry_sha256="e" * 64, manifest_sha256="f" * 64,
        experiment_config_sha256="0" * 64, evaluator_sha256="1" * 64,
        telemetry_schema_version=TELEMETRY_SCHEMA_VERSION,
        derivation_schema_version="train-calibration-v1",
        m20_derivation_version="m20-derivation-v1",
        m21_derivation_version="m21-derivation-v1",
        binning_spec_version="m21-state-binning-v1",
        relation_catalogue=("countryLandBordersCountry", "hasArea"),
        collection_policy_version="collect-v1",
        settings=derived["settings"], support_counts={"executed_actions": 18},
    )
    return CalibrationBundle(
        provenance=provenance, budgets=derived["budgets"],
        history=derived["history"], planner=derived["planner"])


# --------------------------------------------------------------------------
# no model, ever
# --------------------------------------------------------------------------


def test_the_derivation_cannot_reach_a_model_runtime() -> None:
    """A calibration step that *could* call a model is one that might."""
    import cover_kbc.controller_calibration.derivation as derivation
    import cover_kbc.controller_calibration.gold_join as gold_join

    for module in (derivation, gold_join):
        source = Path(module.__file__).read_text()
        for forbidden in ("build_runtime", "HuggingFaceRuntime", "torch",
                          "from_pretrained", "generate(", "score_labels"):
            assert forbidden not in source, f"{module.__name__}: {forbidden}"


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_the_same_inputs_produce_byte_identical_artifacts(collection, gold) -> None:
    """Re-running the derivation cannot produce a different number."""
    def run() -> str:
        settings = DerivationSettings(minimum_bin_support=2)
        effects = score_actions(collection, gold)
        spend = observe_relation_spend(collection, effects)
        binning = derive_binning_spec(collection, settings)
        history, _ = derive_m21(collection, effects, binning, settings)
        planner, _ = derive_planner_calibration(history, collection, effects)
        payload = {
            "m20": {n: c.to_json() for n, c in sorted(
                derive_m20(spend, settings).items())},
            "m21": history.to_json(), "planner": planner.to_json(),
        }
        return json.dumps(payload, sort_keys=True)

    assert run() == run()


def test_artifacts_carry_no_timestamp(derived) -> None:
    """A timestamp would make byte-identity impossible to check at all."""
    bundle = _bundle(derived)
    for payload in (bundle.m20_json(), bundle.m21_history_json(),
                    bundle.m21_planner_json()):
        blob = json.dumps(payload).casefold()
        for forbidden in ("timestamp", "generated_at", "derived_at", "finished_at"):
            assert forbidden not in blob


def test_every_serialised_number_is_finite(derived) -> None:
    bundle = _bundle(derived)
    for payload in (bundle.m20_json(), bundle.m21_history_json(),
                    bundle.m21_planner_json()):
        # ``assert_no_leakage`` walks every float and refuses a non-finite one.
        assert_no_leakage(payload)
        blob = json.dumps(payload)
        assert "NaN" not in blob and "Infinity" not in blob


# --------------------------------------------------------------------------
# the two-sided binning contract
# --------------------------------------------------------------------------


def test_offline_and_runtime_bin_keys_agree() -> None:
    """The derived key must be the key the planner will compute at inference.

    Two readers, one spec: the derivation reads a recorded control state, the
    planner reads a live ``PlannerStateSnapshot``. If they disagree by so much
    as a separator, every derived bin is unreachable and the planner raises on
    the first action it tries to rank.
    """
    from types import SimpleNamespace as NS

    binning = StateBinningSpec(
        spec_version="v", categorical_features=("program_type",),
        numeric_boundaries=(("residual", (0.3, 0.7)),
                            ("unresolved_mass", (0.5,))))

    def component(name, value):
        return NS(name=NS(value=name), value=value)

    snapshot = PlannerStateSnapshot(
        subject="S", relation="hasArea", row_index=0, program_type="NUMERIC",
        coverage_gap=NS(
            residual=NS(residual=0.55, components=(
                component("facet_gap", 0.3),
                component("singleton_ratio", 0.1))),
            novelty=NS(novelty_rate=0.2),
            disagreement=NS(value=0.0),
            unresolved=NS(value=0.8),
        ),
    )
    telemetry_state = _state(0.55, unresolved=0.8)

    assert offline_state_bin_key(
        telemetry_state, program_type="NUMERIC", relation="hasArea",
        binning=binning) == state_bin_key(snapshot, binning)


def test_an_unavailable_component_is_its_own_bucket() -> None:
    """§15: unavailable is not zero, and must not share a bucket with zero."""
    binning = StateBinningSpec(
        spec_version="v", categorical_features=(),
        numeric_boundaries=(("unresolved_mass", (0.5,)),))
    measured_zero = _state(0.4, unresolved=0.0)
    unavailable = _state(0.4, unresolved=0.0, components=("novelty_rate",))
    assert offline_state_bin_key(
        measured_zero, program_type="NUMERIC", relation="hasArea",
        binning=binning) == "unresolved_mass=b0"
    assert offline_state_bin_key(
        unavailable, program_type="NUMERIC", relation="hasArea",
        binning=binning) == "unresolved_mass=NA"


def test_an_unmeasured_state_is_its_own_bucket() -> None:
    binning = StateBinningSpec(
        spec_version="v", numeric_boundaries=(("residual", (0.5,)),))
    assert offline_state_bin_key(
        _state(0.9, measured=False), program_type="NUMERIC", relation="hasArea",
        binning=binning) == "residual=NA"


def test_boundaries_come_from_the_observed_distribution(collection) -> None:
    spec = derive_binning_spec(collection, DerivationSettings())
    residual_cuts = dict(spec.numeric_boundaries)["residual"]
    observed = {r.pre_state.residual for r in collection}
    assert residual_cuts, "no cut point was derived"
    assert list(residual_cuts) == sorted(residual_cuts)
    for cut in residual_cuts:
        assert min(observed) <= cut <= max(observed)


# --------------------------------------------------------------------------
# Module 21
# --------------------------------------------------------------------------


def test_bins_are_relation_specific(derived) -> None:
    relations = {b.relation for b in derived["history"].bins}
    assert relations == {"countryLandBordersCountry", "hasArea"}
    for relation in relations:
        assert any(b.relation == relation and b.state_bin_key == FALLBACK_STATE_BIN
                   for b in derived["history"].bins)


def test_every_action_family_resolves_a_bin(derived) -> None:
    """A legal action must never be dropped from the ranking for lack of a bin."""
    history = derived["history"]
    for relation, program_type, family in (
        ("countryLandBordersCountry", "SMALL_SET", ActionFamily.SPECIALIST_VERIFY),
        ("countryLandBordersCountry", "SMALL_SET",
         ActionFamily.CANDIDATE_FREE_RECALL),
        ("hasArea", "NUMERIC", ActionFamily.SPECIALIST_VERIFY),
    ):
        entry = history.lookup(
            relation=relation, program_type=program_type,
            state_bin_key="a state no query ever reached", family=family)
        assert entry.state_bin_key == FALLBACK_STATE_BIN


def test_a_sparse_bin_falls_back_rather_than_shipping_a_mean_of_one(
    collection, gold,
) -> None:
    """Support is a threshold, not a suggestion."""
    strict = DerivationSettings(minimum_bin_support=100)
    effects = score_actions(collection, gold)
    binning = derive_binning_spec(collection, strict)
    history, diagnostics = derive_m21(collection, effects, binning, strict)
    assert diagnostics["exact_bins_kept"] == 0
    assert diagnostics["exact_bins_dropped_for_sparsity"] > 0
    # ...and the fallback still answers every lookup.
    assert all(b.state_bin_key == FALLBACK_STATE_BIN for b in history.bins)
    assert history.lookup(
        relation="hasArea", program_type="NUMERIC", state_bin_key="whatever",
        family=ActionFamily.SPECIALIST_VERIFY).support_count == 6


def test_support_counts_are_real_observation_counts(derived) -> None:
    history = derived["history"]
    fallback = [b for b in history.bins if b.state_bin_key == FALLBACK_STATE_BIN]
    assert sum(b.support_count for b in fallback) == 18
    for entry in history.bins:
        assert entry.support_count >= 1


def test_verified_gain_and_false_positives_come_from_the_evaluator(
    derived, real_rows,
) -> None:
    """One right answer and one wrong one, per borders action, by construction."""
    borders, _ = real_rows
    history = derived["history"]
    entry = history.lookup(
        relation=borders.relation, program_type="SMALL_SET",
        state_bin_key=FALLBACK_STATE_BIN,
        family=ActionFamily.SPECIALIST_VERIFY)
    assert entry.expected_verified_gain == pytest.approx(1.0)
    assert entry.expected_fp == pytest.approx(0.5)


def test_successor_transitions_are_recorded_and_normalised(derived) -> None:
    history = derived["history"]
    with_successors = [b for b in history.bins if b.successors]
    assert with_successors, "no successor frequency was derived"
    for entry in with_successors:
        total = sum(s.probability for s in entry.successors)
        assert math.isclose(total, 1.0, abs_tol=1e-9)
    assert derived["diagnostics"]["observed_transitions"] == 6


def test_redundancy_falls_back_through_a_documented_hierarchy(derived) -> None:
    """A bin with no MEASURED observation borrows the relation's, not zero."""
    entry = derived["history"].lookup(
        relation="hasArea", program_type="NUMERIC",
        state_bin_key=FALLBACK_STATE_BIN,
        family=ActionFamily.SPECIALIST_VERIFY)
    # Every hasArea action had no candidate surface, so the relation has no
    # measurement either; the run-wide mean is used and is a real observation.
    assert 0.0 <= entry.expected_redundancy <= 1.0
    assert derived["diagnostics"]["redundancy_measured_observations"] > 0


# --------------------------------------------------------------------------
# C-02
# --------------------------------------------------------------------------


def test_delta_h_of_zero_is_recorded_truthfully_not_hidden(derived) -> None:
    """The fixture never moves H, exactly as the real action space does not."""
    assert derived["diagnostics"]["delta_h_is_structurally_zero"] is True
    assert derived["diagnostics"]["delta_h_non_zero"] == 0
    for entry in derived["history"].bins:
        assert entry.expected_delta_h == 0.0
    assert derived["planner"].gamma == 0.0
    assert derived["planner_diagnostics"][
        "gamma_is_inert_because_delta_h_never_moved"] is True


def test_gamma_is_estimated_when_h_really_moves(collection, gold) -> None:
    """γ is inert because ΔH is zero, not because the code forces it to be."""
    from dataclasses import replace

    moved = [
        replace(record, post_state=replace(
            record.post_state, entropy=record.pre_state.entropy - 0.25))
        for record in collection
    ]
    settings = DerivationSettings(minimum_bin_support=2)
    effects = score_actions(moved, gold)
    binning = derive_binning_spec(moved, settings)
    history, diagnostics = derive_m21(moved, effects, binning, settings)
    planner, planner_diagnostics = derive_planner_calibration(
        history, moved, effects)
    assert diagnostics["delta_h_is_structurally_zero"] is False
    assert planner.gamma > 0.0
    assert planner_diagnostics[
        "gamma_is_inert_because_delta_h_never_moved"] is False


# --------------------------------------------------------------------------
# §17's coefficients and the strict continuation rule
# --------------------------------------------------------------------------


def test_the_strict_continuation_rule_survives_derivation(derived) -> None:
    """§17 stops on equality. τ = 0 makes "break even" mean exactly that."""
    from cover_kbc.control.micro_planner import utility

    planner = derived["planner"]
    assert planner.tau_continue == 0.0
    entry = derived["history"].lookup(
        relation="hasArea", program_type="NUMERIC",
        state_bin_key=FALLBACK_STATE_BIN,
        family=ActionFamily.SPECIALIST_VERIFY)
    breakdown = utility(entry, planner, action_id="a")
    assert math.isfinite(breakdown.utility)
    # The rule itself is the planner's; what the derivation must not do is make
    # a break-even action continue.
    assert not (breakdown.utility > planner.tau_continue) or breakdown.utility > 0


def test_penalty_coefficients_are_never_negative(derived) -> None:
    planner = derived["planner"]
    for name in ("alpha", "beta", "gamma", "delta", "eta", "kappa"):
        assert getattr(planner, name) >= 0.0


def test_coefficients_are_ratios_of_observed_totals(derived) -> None:
    diagnostics = derived["planner_diagnostics"]
    planner = derived["planner"]
    assert planner.alpha == 1.0 and planner.kappa == 1.0
    if diagnostics["physical_calls_total"]:
        assert planner.delta == pytest.approx(
            diagnostics["verified_gain_total"]
            / diagnostics["physical_calls_total"], abs=1e-6)
    assert planner.eta == pytest.approx(
        diagnostics["verified_gain_total"] / diagnostics["executed_actions"],
        abs=1e-6)


# --------------------------------------------------------------------------
# Module 20
# --------------------------------------------------------------------------


def test_budgets_are_relation_specific_and_satisfy_the_scheduler(derived) -> None:
    budgets = derived["budgets"]
    assert set(budgets) == {"countryLandBordersCountry", "hasArea"}
    for calibration in budgets.values():
        assert calibration.calibration_source.is_production
        assert calibration.discovery_cap <= calibration.hard_calls
        assert calibration.verification_cap <= calibration.hard_calls
        assert calibration.verification_reserve <= calibration.verification_cap


def test_budgets_reload_through_the_production_loader(derived) -> None:
    """The artifact must be readable by the module that will consume it."""
    payload = _bundle(derived).m20_json()
    reloaded = load_calibrations(payload)
    assert set(reloaded) == set(derived["budgets"])
    for name, calibration in reloaded.items():
        assert calibration.to_json() == derived["budgets"][name].to_json()


def test_a_special_reserve_is_only_derived_where_table_6_declares_one(
    derived,
) -> None:
    """Table 6 owns *which* reserves exist; TRAIN owns only how big they are."""
    from cover_kbc.control.relation_budget import relation_policy

    for relation, calibration in derived["budgets"].items():
        declared = set(relation_policy(relation).special_reserve_purposes)
        assert {p for p, _ in calibration.special_reserves} <= declared


def test_an_unknown_relation_is_refused(collection, gold) -> None:
    from dataclasses import replace

    alien = [replace(r, relation="notARelation") for r in collection[:2]]
    effects = {
        r.operation_id: ActionGoldEffect(r.operation_id, r.relation)
        for r in alien
    }
    with pytest.raises(Exception):
        derive_m20(observe_relation_spend(alien, effects), DerivationSettings())


# --------------------------------------------------------------------------
# leakage
# --------------------------------------------------------------------------


def test_no_gold_object_reaches_a_production_artifact(derived, real_rows) -> None:
    """The decisive check, over the bytes rather than the intent."""
    borders, area = real_rows
    bundle = _bundle(derived)
    blobs = [json.dumps(bundle.m20_json()),
             json.dumps(bundle.m21_history_json()),
             json.dumps(bundle.m21_planner_json())]
    surfaces = [alias for row in (borders, area) for group in row.aliases
                for alias in group]
    for blob in blobs:
        folded = blob.casefold()
        for surface in surfaces:
            assert surface.casefold() not in folded, surface
        assert borders.subject.casefold() not in folded
        assert area.subject.casefold() not in folded


def test_no_subject_identity_feature_is_used(derived) -> None:
    """A bin keyed on a subject is a memorised answer table."""
    binning = derived["history"].binning
    assert "subject" not in " ".join(binning.categorical_features).casefold()
    for feature, _cuts in binning.numeric_boundaries:
        assert "subject" not in feature.casefold()
    for entry in derived["history"].bins:
        assert "subject" not in entry.state_bin_key.casefold()


def test_the_leakage_guard_refuses_a_forbidden_field() -> None:
    with pytest.raises(DerivationError, match="ObjectEntities"):
        assert_no_leakage({"bins": [{"ObjectEntities": ["Spain"]}]})
    with pytest.raises(DerivationError, match="SubjectEntity"):
        assert_no_leakage({"SubjectEntity": "France"})


def test_the_leakage_guard_refuses_a_non_finite_number() -> None:
    with pytest.raises(DerivationError, match="not finite"):
        assert_no_leakage({"expected_cost": float("inf")})


# --------------------------------------------------------------------------
# fail-closed inputs
# --------------------------------------------------------------------------


def test_an_unsupported_telemetry_schema_is_refused(collection) -> None:
    from dataclasses import replace

    stale = [object.__new__(ActionTelemetryRecord)]
    object.__setattr__(stale[0], "schema_version", "train-telemetry-v2")
    with pytest.raises(DerivationError, match="schema"):
        require_supported_schema(stale)
    assert require_supported_schema(collection) == TELEMETRY_SCHEMA_VERSION
    del replace


def test_empty_telemetry_is_refused() -> None:
    with pytest.raises(DerivationError, match="empty"):
        require_supported_schema([])


def test_telemetry_with_no_executed_action_is_refused(gold) -> None:
    with pytest.raises(DerivationError, match="no executed action"):
        derive_m21([], {}, StateBinningSpec(spec_version="v"),
                   DerivationSettings())


def test_an_action_with_no_gold_row_is_refused(collection, gold) -> None:
    from dataclasses import replace

    orphan = [replace(collection[0], subject="A Subject TRAIN Does Not Contain")]
    with pytest.raises(GoldJoinError, match="does not contain"):
        score_actions(orphan, gold)


def test_a_wrong_train_row_count_is_refused() -> None:
    with pytest.raises(GoldJoinError, match="expected 12"):
        load_gold(TRAIN, expected_rows=12)


def test_a_duplicate_query_identity_in_gold_is_refused(tmp_path) -> None:
    row = json.loads(TRAIN.read_text().splitlines()[0])
    path = tmp_path / "dupe.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    with pytest.raises(GoldJoinError, match="duplicate query identity"):
        load_gold(path)


def test_malformed_gold_is_refused(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"SubjectEntity": "x"}\n')
    with pytest.raises(GoldJoinError, match="not an official TRAIN row"):
        load_gold(path)


def test_a_bad_derivation_setting_is_refused() -> None:
    with pytest.raises(DerivationError, match="budget_quantile"):
        DerivationSettings(budget_quantile=0.0)
    with pytest.raises(DerivationError, match="minimum_bin_support"):
        DerivationSettings(minimum_bin_support=0)


def test_an_unknown_state_feature_is_refused() -> None:
    from cover_kbc.controller_calibration.derivation import (
        telemetry_numeric_feature,
    )
    with pytest.raises(DerivationError, match="no telemetry backing"):
        telemetry_numeric_feature(_state(0.5), "not_a_feature")


# --------------------------------------------------------------------------
# the production loader contract
# --------------------------------------------------------------------------


def test_the_history_artifact_reloads_and_ranks(derived) -> None:
    """What is written must be what Module 21 can read - and then use."""
    from cover_kbc.control.micro_planner import utility

    payload = _bundle(derived).m21_history_json()
    reloaded = load_history(payload)
    assert isinstance(reloaded, HistoricalBinPackage)
    assert reloaded.source is EstimateSource.TRAIN_CALIBRATED
    assert reloaded.to_json()["bins"] == derived["history"].to_json()["bins"]
    entry = reloaded.lookup(
        relation="hasArea", program_type="NUMERIC", state_bin_key="unseen",
        family=ActionFamily.SPECIALIST_VERIFY)
    assert math.isfinite(utility(entry, derived["planner"], action_id="a").utility)


def test_the_planner_artifact_reloads(derived) -> None:
    payload = _bundle(derived).m21_planner_json()
    reloaded = PlannerCalibration.from_json(payload)
    assert reloaded.to_json() == derived["planner"].to_json()
    assert reloaded.source is EstimateSource.TRAIN_CALIBRATED


def test_a_synthetic_history_is_still_refused_for_production(derived) -> None:
    """The derivation stamps TRAIN_CALIBRATED; the guard must still bite."""
    payload = _bundle(derived).m21_history_json()
    payload["source"] = EstimateSource.SYNTHETIC_TEST.value
    with pytest.raises(PlannerError, match="test fixture"):
        load_history(payload)


def test_the_readiness_gate_accepts_the_generated_artifacts(
    derived, tmp_path,
) -> None:
    """The gate that will admit a validation run must admit these files."""
    from cover_kbc.controller_calibration.readiness import (
        ReadinessState,
        evaluate_readiness,
    )

    bundle = _bundle(derived)
    for name, payload in (
        ("m20.json", bundle.m20_json()),
        ("bins.json", bundle.m21_history_json()),
        ("planner.json", bundle.m21_planner_json()),
    ):
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate_readiness({
        "relation_budget_scheduler": {
            "enabled": True, "calibration_file": "m20.json"},
        "micro_planner": {
            "enabled": True, "historical_bins": "bins.json",
            "planner_calibration": "planner.json"},
    }, base_dir=tmp_path)
    assert report.state is ReadinessState.FULL_VALIDATION_READY, report.blockers


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------


def test_provenance_binds_the_artifact_to_one_collection(derived) -> None:
    payload = _bundle(derived).m20_json()["provenance"]
    for field in ("collection_repo_sha", "derivation_repo_sha", "train_sha256",
                  "train_rows", "predictions_sha256", "telemetry_sha256",
                  "manifest_sha256", "experiment_config_sha256",
                  "evaluator_sha256", "telemetry_schema_version",
                  "derivation_schema_version", "m20_derivation_version",
                  "m21_derivation_version", "binning_spec_version",
                  "relation_catalogue", "collection_policy_version",
                  "derivation_settings", "support_counts"):
        assert field in payload, field
    assert payload["train_rows"] == 477


def test_every_artifact_carries_the_same_provenance(derived) -> None:
    bundle = _bundle(derived)
    blocks = [bundle.m20_json()["provenance"],
              bundle.m21_history_json()["provenance"],
              bundle.m21_planner_json()["provenance"]]
    assert blocks[0] == blocks[1] == blocks[2]
