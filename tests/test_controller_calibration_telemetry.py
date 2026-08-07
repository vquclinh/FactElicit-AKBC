"""Telemetry has to be trustworthy before it can calibrate a controller.

Every test here targets a way a TRAIN run could produce a file that *looks*
complete and yields wrong budgets or wrong bins - the failures that would only
surface as a bad leaderboard score much later.
"""

from __future__ import annotations

import json

import pytest

from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionOutcome,
    ActionTelemetryRecord,
    ControlStateFeatures,
    TelemetryError,
    RedundancyStatus,
    TelemetryWriter,
    executed_families,
    read_telemetry,
)


def _record(**overrides) -> ActionTelemetryRecord:
    base = dict(
        schema_version=TELEMETRY_SCHEMA_VERSION, run_id="r1", row_index=0,
        subject="France", relation="countryLandBordersCountry",
        program_type="SMALL_SET", round_index=0, operation_id="op-1",
        action_family="SPECIALIST_PROBE", action_id="M18:REVERSE:t1",
    )
    base.update(overrides)
    return ActionTelemetryRecord(**base)


def _executed_outcome(physical_calls: int, **overrides) -> ActionOutcome:
    """An outcome whose role partition sums, as a real one always does."""
    base = dict(physical_calls=physical_calls, verifier_calls=physical_calls)
    base.update(overrides)
    return ActionOutcome(**base)


def test_a_legal_unselected_action_is_recordable() -> None:
    """Unselected branches must survive: M21 estimates actions it did not take."""
    record = _record(legal=True, selected=False, executed=False)
    assert record.delta_residual is None
    assert record.outcome.physical_calls == 0


def test_executed_action_derives_signed_deltas() -> None:
    record = _record(
        selected=True, executed=True,
        pre_state=ControlStateFeatures(residual=0.8, entropy=1.5),
        post_state=ControlStateFeatures(residual=0.5, entropy=1.1),
        outcome=_executed_outcome(4),
    )
    # A *reduction* in residual is a positive ΔR, matching §17's sign.
    assert record.delta_residual == pytest.approx(0.3)
    assert record.delta_entropy == pytest.approx(0.4)


def test_role_partition_must_sum_to_the_physical_call_total() -> None:
    """A call attributed to no role makes M20's per-role derivation wrong."""
    with pytest.raises(TelemetryError, match="role partition"):
        ActionOutcome(physical_calls=3, enumerator_calls=1, verifier_calls=1)


def test_redundancy_distinguishes_measured_zero_from_no_measurement() -> None:
    """§20: a legitimate zero must stay valid; absence must stay visible.

    Three states, not two (Audit 0043 C-05): a measured 0.0, an action with no
    candidate surface for redundancy to be about, and no measurement at all.
    ``None`` used to carry the last two at once.
    """
    default = ActionOutcome()
    assert default.redundancy is None
    assert default.redundancy_status is RedundancyStatus.UNMEASURED

    measured = ActionOutcome(redundancy=0.0,
                             redundancy_status=RedundancyStatus.MEASURED)
    assert measured.redundancy == 0.0
    assert measured.redundancy_status is RedundancyStatus.MEASURED

    not_applicable = ActionOutcome(
        redundancy_status=RedundancyStatus.NOT_APPLICABLE)
    assert not_applicable.redundancy is None
    assert not_applicable.redundancy_status is RedundancyStatus.NOT_APPLICABLE


def test_a_redundancy_value_without_a_measurement_is_refused() -> None:
    """Shape invariant: only MEASURED carries a number."""
    with pytest.raises(TelemetryError, match="only MEASURED carries one"):
        ActionOutcome(redundancy=0.5,
                      redundancy_status=RedundancyStatus.NOT_APPLICABLE)
    with pytest.raises(TelemetryError, match="no redundancy value"):
        ActionOutcome(redundancy_status=RedundancyStatus.MEASURED)


def test_candidate_effect_measurement_presence_is_explicit() -> None:
    """An unmeasured diff must never look like an action that changed nothing."""
    assert ActionOutcome().candidate_effect_measured is False
    measured = ActionOutcome(candidate_effect_measured=True)
    assert measured.candidate_effect_measured is True
    assert measured.candidates_added == ()   # a real, empty observation


def test_an_unknown_redundancy_status_is_refused() -> None:
    with pytest.raises(TelemetryError, match="not a RedundancyStatus"):
        ActionOutcome.from_json({"redundancy_status": "PROBABLY"})


def test_refuses_an_executed_action_without_a_canonical_action_id() -> None:
    with pytest.raises(TelemetryError, match="canonical action_id"):
        _record(selected=True, executed=True, action_id="",
                post_state=ControlStateFeatures(),
                outcome=_executed_outcome(1))


def test_refuses_an_executed_action_against_an_unmeasured_state() -> None:
    """The Audit-0041 F-02 failure: a zeroed state reads as a real observation."""
    with pytest.raises(TelemetryError, match="unmeasured control state"):
        _record(selected=True, executed=True,
                pre_state=ControlStateFeatures(measured=False),
                post_state=ControlStateFeatures(),
                outcome=_executed_outcome(1))


def test_refuses_a_candidate_effect_on_an_unexecuted_action() -> None:
    with pytest.raises(TelemetryError, match="claims a candidate effect"):
        _record(outcome=ActionOutcome(candidates_added=("Spain",)))


def test_refuses_a_foreign_schema_version() -> None:
    with pytest.raises(TelemetryError, match="telemetry schema"):
        _record(schema_version="train-telemetry-v0")


def test_refuses_execution_without_selection() -> None:
    with pytest.raises(TelemetryError, match="executed without being selected"):
        _record(executed=True, selected=False,
                post_state=ControlStateFeatures())


def test_refuses_selecting_an_illegal_action() -> None:
    with pytest.raises(TelemetryError, match="selected but marked illegal"):
        _record(legal=False, selected=True)


def test_refuses_execution_without_a_post_state() -> None:
    """Without a post-state the action's ΔR and ΔH are unrecoverable."""
    with pytest.raises(TelemetryError, match="recorded no post-state"):
        _record(selected=True, executed=True)


def test_refuses_charging_calls_to_an_unexecuted_action() -> None:
    with pytest.raises(TelemetryError, match="not executed but charged"):
        _record(outcome=_executed_outcome(2))


def test_refuses_a_record_without_identity() -> None:
    with pytest.raises(TelemetryError, match="needs an operation_id"):
        _record(operation_id="")
    with pytest.raises(TelemetryError, match="needs an action_family"):
        _record(action_family="")


def test_refuses_non_finite_state() -> None:
    with pytest.raises(TelemetryError, match="not finite"):
        ControlStateFeatures(residual=float("nan"))
    with pytest.raises(TelemetryError, match="not finite"):
        ControlStateFeatures(entropy=float("inf"))


def test_writer_refuses_a_duplicate_operation_identity(tmp_path) -> None:
    """A physical call written twice would inflate every derived cost."""
    with TelemetryWriter(tmp_path / "t.jsonl", run_id="run-a", resume=False) as writer:
        writer.write(_record())
        with pytest.raises(TelemetryError, match="duplicate telemetry identity"):
            writer.write(_record())


def test_same_operation_id_in_a_later_round_is_not_a_duplicate(tmp_path) -> None:
    with TelemetryWriter(tmp_path / "t.jsonl", run_id="run-a", resume=False) as writer:
        writer.write(_record(round_index=0))
        writer.write(_record(round_index=1))


def test_writer_stamps_the_run_id(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    with TelemetryWriter(path, run_id="run-b", resume=False) as writer:
        writer.write(_record(run_id="ignored"))
    assert json.loads(path.read_text())["run_id"] == "run-b"


def test_round_trips_through_disk(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    original = _record(
        selected=True, executed=True, target_class="reverse",
        pre_state=ControlStateFeatures(residual=0.9, entropy=2.0),
        post_state=ControlStateFeatures(residual=0.4, entropy=1.0),
        spend_class="VERIFICATION", reserve_purpose="REVERSE",
        outcome=_executed_outcome(8, prompt_tokens=120, generated_tokens=40,
                                  candidates_added=("Spain",), redundancy=0.5,
                                  redundancy_status=RedundancyStatus.MEASURED,
                                  candidates_named=("Andorra",),
                                  candidates_touched=("Spain",),
                                  candidate_effect_measured=True,
                                  verifier_outcome="VALID"),
    )
    with TelemetryWriter(path, run_id="run-c", resume=False) as writer:
        writer.write(original)
    (restored,) = list(read_telemetry(path))
    assert restored.outcome.candidates_added == ("Spain",)
    assert restored.outcome.candidates_named == ("Andorra",)
    assert restored.outcome.physical_calls == 8
    assert restored.outcome.verifier_calls == 8
    assert restored.outcome.prompt_tokens == 120
    assert restored.outcome.redundancy == pytest.approx(0.5)
    assert restored.outcome.redundancy_status is RedundancyStatus.MEASURED
    assert restored.outcome.candidates_touched == ("Spain",)
    assert restored.outcome.candidate_effect_measured is True
    assert (restored.spend_class, restored.reserve_purpose) == (
        "VERIFICATION", "REVERSE")
    assert restored.delta_residual == pytest.approx(0.5)


def test_reading_rejects_malformed_lines(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(TelemetryError, match="not valid JSON"):
        list(read_telemetry(path))


def test_executed_families_counts_only_executed_actions() -> None:
    records = [
        _record(operation_id="a", action_family="SPECIALIST_PROBE",
                selected=True, executed=True,
                post_state=ControlStateFeatures()),
        _record(operation_id="b", action_family="SPECIALIST_PROBE"),
        _record(operation_id="c", action_family="CANDIDATE_FREE_RECALL",
                selected=True, executed=True,
                post_state=ControlStateFeatures()),
    ]
    assert executed_families(records) == {
        "CANDIDATE_FREE_RECALL": 1, "SPECIALIST_PROBE": 1}


# --------------------------------------------------------------------------
# Module 19 state extraction - Audit 0041 F-02
# --------------------------------------------------------------------------

def _gap_state(**components):
    from cover_kbc.coverage_gap.gap_types import (
        CoverageGapComponents,
        CoverageGapState,
        ResidualComponent,
        ResidualComponentName,
        SignalAvailability,
    )

    parts = tuple(
        ResidualComponent(
            name=ResidualComponentName(name), value=value,
            availability=SignalAvailability.AVAILABLE,
            configured_weight=1.0, effective_weight=0.2,
        )
        for name, value in components.items()
    )
    return CoverageGapState(
        estimator_version="m19-v1", layer4_version="layer4-v1",
        relation="countryLandBordersCountry", subject="France", row_index=0,
        program_type="SMALL_SET",
        residual=CoverageGapComponents(
            components=parts, weight_source="uniform_unfitted",
            effective_weight_mass=1.0, residual=0.42,
            availability=SignalAvailability.AVAILABLE,
        ),
    )


_STATE_KWARGS = dict(
    entropy=0.3, active_candidates=2, calls_used=4, calls_remaining=8,
    prompt_tokens=100, generated_tokens=20,
)


def test_reads_module_19s_real_residual_and_components() -> None:
    """F-02: the runner used to look for `state.coverage_gap`, which is absent."""
    features = ControlStateFeatures.from_coverage_gap(
        _gap_state(novelty_rate=0.7, singleton_ratio=0.25, facet_gap=0.5,
                   disagreement=0.1, unresolved_mass=0.6),
        **_STATE_KWARGS)
    assert features.measured
    assert features.residual == pytest.approx(0.42)
    assert features.novelty_rate == pytest.approx(0.7)
    assert features.singleton_ratio == pytest.approx(0.25)
    assert features.facet_gap == pytest.approx(0.5)
    assert features.disagreement == pytest.approx(0.1)
    assert features.unresolved_mass == pytest.approx(0.6)
    assert (features.prompt_tokens, features.calls_used) == (100, 4)


def test_component_names_are_matched_on_the_canonical_enum_value() -> None:
    """F-02's second half: `str(enum)` is 'ResidualComponentName.NOVELTY_RATE'."""
    features = ControlStateFeatures.from_coverage_gap(
        _gap_state(novelty_rate=0.9), **_STATE_KWARGS)
    assert features.novelty_rate == pytest.approx(0.9)


def test_a_wrong_state_shape_raises_rather_than_zeroing() -> None:
    """A silently zeroed control state calibrates; a crash does not."""

    class NotACoverageGapState:
        coverage_gap = None

    with pytest.raises(TelemetryError, match="does not expose Module 19"):
        ControlStateFeatures.from_coverage_gap(
            NotACoverageGapState(), **_STATE_KWARGS)


def test_absent_module_19_state_is_unmeasured_not_zero() -> None:
    features = ControlStateFeatures.from_coverage_gap(None, **_STATE_KWARGS)
    assert not features.measured and features.residual == 0.0
    assert features.available_components == ()


def test_an_unavailable_component_is_not_reported_as_a_measured_zero() -> None:
    """§15: unavailable is never zero, and the distinction must survive."""
    features = ControlStateFeatures.from_coverage_gap(
        _gap_state(novelty_rate=0.0), **_STATE_KWARGS)
    assert features.novelty_rate == 0.0
    assert "novelty_rate" in features.available_components   # a measured zero
    assert "facet_gap" not in features.available_components  # never measured


# --------------------------------------------------------------------------
# successor transitions - §17's depth-2 lookahead support
# --------------------------------------------------------------------------

def test_consecutive_actions_form_a_successor_transition() -> None:
    from cover_kbc.controller_calibration.telemetry import successor_transitions

    first_post = ControlStateFeatures(residual=0.5, entropy=0.4)
    records = [
        _record(operation_id="a", round_index=1, selected=True, executed=True,
                pre_state=ControlStateFeatures(residual=0.8, entropy=0.6),
                post_state=first_post, outcome=_executed_outcome(1)),
        _record(operation_id="b", round_index=2, selected=True, executed=True,
                pre_state=first_post,
                post_state=ControlStateFeatures(residual=0.3, entropy=0.2),
                outcome=_executed_outcome(1)),
    ]
    (transition,) = successor_transitions(records)
    assert transition[0].operation_id == "a"
    assert transition[1].operation_id == "b"
    assert transition[0].delta_residual == pytest.approx(0.3)


def test_a_broken_state_chain_yields_no_fabricated_transition() -> None:
    from cover_kbc.controller_calibration.telemetry import successor_transitions

    records = [
        _record(operation_id="a", round_index=1, selected=True, executed=True,
                pre_state=ControlStateFeatures(residual=0.8),
                post_state=ControlStateFeatures(residual=0.5),
                outcome=_executed_outcome(1)),
        _record(operation_id="b", round_index=2, selected=True, executed=True,
                pre_state=ControlStateFeatures(residual=0.9),   # not the post
                post_state=ControlStateFeatures(residual=0.1),
                outcome=_executed_outcome(1)),
    ]
    assert successor_transitions(records) == []


def test_telemetry_carries_no_gold_field() -> None:
    """Gold joins offline; a gold column here would leak it into inference."""
    payload = _record().to_json()
    assert "ObjectEntities" not in payload
    assert not any("gold" in key.lower() for key in payload)
