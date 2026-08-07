"""Would this telemetry force a second TRAIN session?

The validator under test answers that question structurally, before the run
exits. Every case here is a way Audit 0041's failure could recur: a field that
exists in the schema, is never populated, and reads as a legitimate zero.

The distinction that has to hold throughout: **a measured zero is valid, an
absent measurement is not.** A collection where every action genuinely produced
no new candidate is a real observation; one where the candidate-effect field was
never instrumented is not, and no test here may confuse them.
"""

from __future__ import annotations

import dataclasses

import pytest

from cover_kbc.controller_calibration.sufficiency import (
    M20_REQUIREMENTS,
    M21_BIN_KEY,
    M21_REQUIREMENTS,
    evaluate_sufficiency,
)
from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionOutcome,
    ActionTelemetryRecord,
    ControlStateFeatures,
    RedundancyStatus,
)


def _state(residual: float, entropy: float = 0.5, **overrides) -> ControlStateFeatures:
    base = dict(residual=residual, entropy=entropy, novelty_rate=0.2,
                singleton_ratio=0.1, facet_gap=0.3, disagreement=0.0,
                unresolved_mass=0.4, measured=True, calls_used=4,
                prompt_tokens=120, generated_tokens=8)
    base.update(overrides)
    return ControlStateFeatures(**base)


def _record(round_index: int, *, family="SPECIALIST_VERIFY", pre=None, post=None,
            **overrides) -> ActionTelemetryRecord:
    outcome = overrides.pop("outcome", None) or ActionOutcome(
        physical_calls=4, verifier_calls=4, prompt_tokens=300,
        generated_tokens=0, candidates_supported=("spain",),
        candidates_touched=("spain",), candidate_effect_measured=True,
        redundancy=0.5, redundancy_status=RedundancyStatus.MEASURED,
        verifier_outcome="VALID")
    base = dict(
        schema_version=TELEMETRY_SCHEMA_VERSION, run_id="r1", row_index=0,
        subject="France", relation="countryLandBordersCountry",
        program_type="SMALL_SET", round_index=round_index,
        operation_id=f"0:{round_index}:M17:SPECIALIST_VERIFY:t{round_index}",
        action_family=family, action_id=f"M17:SPECIALIST_VERIFY:t{round_index}",
        target_class=f"t{round_index}", spend_class="VERIFICATION",
        selected=True, executed=True,
        pre_state=pre if pre is not None else _state(0.8),
        post_state=post if post is not None else _state(0.5),
        outcome=outcome,
    )
    base.update(overrides)
    return ActionTelemetryRecord(**base)


def _chain() -> list[ActionTelemetryRecord]:
    """Two actions forming one real successor transition."""
    middle = _state(0.5)
    return [
        _record(1, pre=_state(0.8), post=middle),
        _record(2, family="REVERSE_CHECK", pre=middle, post=_state(0.2),
                action_id="M18:REVERSE:t2",
                operation_id="0:2:M18:REVERSE:t2", spend_class="VERIFICATION",
                outcome=ActionOutcome(
                    physical_calls=1, enumerator_calls=1, prompt_tokens=90,
                    generated_tokens=12, candidates_named=("andorra",),
                    candidate_effect_measured=True,
                    redundancy=0.0, redundancy_status=RedundancyStatus.MEASURED,
                    structural_outcome="SUPPORT")),
    ]


# --------------------------------------------------------------------------
# the accepting case
# --------------------------------------------------------------------------

def test_a_structurally_complete_run_passes() -> None:
    report = evaluate_sufficiency(_chain())
    assert report.ok, report.blockers
    assert report.details["successor_transitions"] == 1
    assert report.details["distinct_bin_keys"] == 2


def _as_kwargs(record: ActionTelemetryRecord, **overrides) -> dict:

    data = {f.name: getattr(record, f.name)
            for f in dataclasses.fields(record)}
    data.update(overrides)
    return data


def test_a_genuine_zero_is_accepted() -> None:
    """§20: a legitimate zero must remain valid where zero was measured.

    Nothing found, nothing redundant, no structural support - and every field
    populated. That is a real observation and must not be rejected.
    """
    records = _chain()
    records[1] = ActionTelemetryRecord(**_as_kwargs(
        records[1], outcome=ActionOutcome(
            physical_calls=1, enumerator_calls=1, prompt_tokens=90,
            generated_tokens=0, candidates_touched=("spain",),
            candidate_effect_measured=True,
            redundancy=0.0, redundancy_status=RedundancyStatus.MEASURED,
            structural_outcome="UNRESOLVED")))
    report = evaluate_sufficiency(records)
    assert report.ok, report.blockers
    assert records[1].outcome.redundancy == 0.0
    assert records[1].outcome.candidates_supported == ()


def test_an_action_with_no_candidate_surface_is_accepted() -> None:
    """Redundancy is not a question about an action that touched nothing."""
    records = _chain()
    records[1] = ActionTelemetryRecord(**_as_kwargs(
        records[1], outcome=ActionOutcome(
            physical_calls=1, enumerator_calls=1, prompt_tokens=90,
            candidate_effect_measured=True,
            redundancy_status=RedundancyStatus.NOT_APPLICABLE,
            structural_outcome="UNRESOLVED")))
    assert evaluate_sufficiency(records).ok


def test_erased_candidate_effect_instrumentation_is_rejected() -> None:
    """C-05: empty lists are only an observation if something looked."""
    records = [ActionTelemetryRecord(**_as_kwargs(
        r, outcome=ActionOutcome(**{
            **{f.name: getattr(r.outcome, f.name)
               for f in dataclasses.fields(r.outcome)},
            "candidate_effect_measured": False}))) for r in _chain()]
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("candidate-effect measurement" in b for b in report.blockers)


def test_erased_redundancy_instrumentation_is_rejected() -> None:
    """C-05: UNMEASURED is a gap, not an inapplicability."""
    records = [ActionTelemetryRecord(**_as_kwargs(
        r, outcome=ActionOutcome(**{
            **{f.name: getattr(r.outcome, f.name)
               for f in dataclasses.fields(r.outcome)},
            "redundancy": None,
            "redundancy_status": RedundancyStatus.UNMEASURED}))) for r in _chain()]
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("UNMEASURED" in b for b in report.blockers)


def test_a_false_not_applicable_is_rejected() -> None:
    """C-05: claiming no surface while reporting one is a contradiction."""
    records = _chain()
    records[0] = ActionTelemetryRecord(**_as_kwargs(
        records[0], outcome=ActionOutcome(
            physical_calls=4, verifier_calls=4, prompt_tokens=300,
            candidates_touched=("spain",), candidate_effect_measured=True,
            redundancy_status=RedundancyStatus.NOT_APPLICABLE,
            verifier_outcome="VALID")))
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("NOT_APPLICABLE" in b for b in report.blockers)


def test_the_requirements_it_checks_are_declared() -> None:
    """Every §17 estimate and §16 quantity is named with its telemetry source."""
    assert set(M21_REQUIREMENTS) >= {
        "expected_verified_gain", "expected_fp", "expected_delta_r",
        "expected_delta_h", "expected_cost", "expected_redundancy",
        "successor_state_probabilities"}
    assert set(M20_REQUIREMENTS) >= {
        "hard_calls", "hard_generated_tokens", "discovery_cap",
        "verification_cap", "verification_reserve", "special_reserve_sizes"}
    assert M21_BIN_KEY == (
        "relation", "program_type", "state_bin", "family", "target_class")


# --------------------------------------------------------------------------
# the rejecting cases - each one is a way to need a second TRAIN run
# --------------------------------------------------------------------------

def test_empty_telemetry_is_rejected() -> None:
    report = evaluate_sufficiency([])
    assert not report.ok
    assert "telemetry is empty" in report.blockers[0]


def test_a_repr_program_type_is_rejected() -> None:
    """F-13: 'ProgramType.SMALL_SET' can never match a historical bin."""
    records = _chain()
    records[0] = ActionTelemetryRecord(
        **_as_kwargs(records[0], program_type="ProgramType.SMALL_SET"))
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("canonical ProgramType" in b for b in report.blockers)


def test_an_unmeasured_control_state_is_rejected() -> None:
    """F-02: the zeroed state that reads exactly like a real observation.

    Refused at construction, so such a record cannot reach a file - and refused
    again by the validator, so one that somehow did could not pass the gate.
    """
    from cover_kbc.controller_calibration.telemetry import TelemetryError

    unmeasured = ControlStateFeatures(measured=False, available_components=())
    with pytest.raises(TelemetryError, match="unmeasured control state"):
        ActionTelemetryRecord(
            **_as_kwargs(_chain()[0], pre_state=unmeasured, post_state=unmeasured))

    # The validator's own guard, over a record that never claimed execution.
    legal_only = ActionTelemetryRecord(**_as_kwargs(
        _chain()[0], selected=False, executed=False, pre_state=unmeasured,
        post_state=None, outcome=ActionOutcome()))
    report = evaluate_sufficiency([legal_only], expect_transitions=False)
    assert not report.ok
    assert any("no action was executed" in b for b in report.blockers)


def test_a_state_with_no_available_component_is_rejected() -> None:
    records = _chain()
    bare = _state(0.8, available_components=())
    records[0] = ActionTelemetryRecord(
        **_as_kwargs(records[0], pre_state=bare, post_state=bare))
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("no available §15 component" in b for b in report.blockers)


def test_a_missing_successor_chain_is_rejected() -> None:
    report = evaluate_sufficiency([_chain()[0]])
    assert not report.ok
    assert any("successor" in b for b in report.blockers)


def test_depth_one_can_be_requested_explicitly() -> None:
    report = evaluate_sufficiency([_chain()[0]], expect_transitions=False)
    assert report.ok, report.blockers


def test_zero_prompt_tokens_on_a_charged_action_is_rejected() -> None:
    """F-06: a real call always has a prompt."""
    records = _chain()
    records = [ActionTelemetryRecord(**_as_kwargs(
        r, outcome=ActionOutcome(
            physical_calls=r.outcome.physical_calls,
            enumerator_calls=r.outcome.enumerator_calls,
            verifier_calls=r.outcome.verifier_calls,
            prompt_tokens=0, candidates_touched=("spain",),
            candidate_effect_measured=True,
            redundancy=0.0, redundancy_status=RedundancyStatus.MEASURED,
            verifier_outcome=r.outcome.verifier_outcome,
            structural_outcome=r.outcome.structural_outcome)))
        for r in records]
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("zero prompt tokens" in b for b in report.blockers)


def test_a_missing_spend_class_is_rejected() -> None:
    """Without it, discovery and verification spend cannot be separated."""
    records = _chain()
    records[0] = ActionTelemetryRecord(**_as_kwargs(records[0], spend_class=""))
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("spend_class" in b for b in report.blockers)


def test_claiming_a_module_20_reservation_is_rejected() -> None:
    """Collection reserves nothing; saying otherwise would fake calibration."""
    records = _chain()
    records[0] = ActionTelemetryRecord(
        **_as_kwargs(records[0], reserved_class="VERIFICATION"))
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("reserved_class" in b for b in report.blockers)


def test_a_missing_verifier_verdict_is_rejected() -> None:
    records = _chain()
    records[0] = ActionTelemetryRecord(**_as_kwargs(
        records[0], outcome=ActionOutcome(
            physical_calls=4, verifier_calls=4, prompt_tokens=300,
            candidates_touched=("spain",), candidate_effect_measured=True,
            redundancy=0.5, redundancy_status=RedundancyStatus.MEASURED,
            verifier_outcome="")))
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("verifier verdict" in b for b in report.blockers)


def test_an_action_id_that_looks_like_an_address_is_rejected() -> None:
    records = _chain()
    records[0] = ActionTelemetryRecord(
        **_as_kwargs(records[0], operation_id="m17:1:0:140144940782416"))
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("memory address" in b for b in report.blockers)


def test_a_non_canonical_action_family_is_rejected() -> None:
    records = _chain()
    records[0] = ActionTelemetryRecord(
        **_as_kwargs(records[0], action_family="M17"))
    report = evaluate_sufficiency(records)
    assert not report.ok
    assert any("canonical ActionFamily" in b for b in report.blockers)


def test_the_report_round_trips_and_summarises() -> None:
    report = evaluate_sufficiency(_chain())
    payload = report.to_json()
    assert payload["ok"] and payload["satisfied"]
    assert "PASS" in report.summary()
