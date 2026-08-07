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
    TelemetryWriter,
    executed_families,
    read_telemetry,
)


def _record(**overrides) -> ActionTelemetryRecord:
    base = dict(
        schema_version=TELEMETRY_SCHEMA_VERSION, run_id="r1", row_index=0,
        subject="France", relation="countryLandBordersCountry",
        program_type="SMALL_SET", round_index=0, operation_id="op-1",
        action_family="SPECIALIST_PROBE",
    )
    base.update(overrides)
    return ActionTelemetryRecord(**base)


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
        outcome=ActionOutcome(physical_calls=4),
    )
    # A *reduction* in residual is a positive ΔR, matching §17's sign.
    assert record.delta_residual == pytest.approx(0.3)
    assert record.delta_entropy == pytest.approx(0.4)


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
        _record(outcome=ActionOutcome(physical_calls=2))


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
        outcome=ActionOutcome(physical_calls=8, prompt_tokens=120,
                              generated_tokens=40, candidates_added=("Spain",),
                              verifier_outcome="VALID"),
    )
    with TelemetryWriter(path, run_id="run-c", resume=False) as writer:
        writer.write(original)
    (restored,) = list(read_telemetry(path))
    assert restored.outcome.candidates_added == ("Spain",)
    assert restored.outcome.physical_calls == 8
    assert restored.delta_residual == pytest.approx(0.5)


def test_reading_rejects_malformed_lines(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(TelemetryError, match="not valid JSON"):
        list(read_telemetry(path))


def test_executed_families_counts_only_executed_actions() -> None:
    records = [
        _record(operation_id="a", action_family="SPECIALIST_PROBE",
                selected=True, executed=True, post_state=ControlStateFeatures()),
        _record(operation_id="b", action_family="SPECIALIST_PROBE"),
        _record(operation_id="c", action_family="CANDIDATE_FREE_RECALL",
                selected=True, executed=True, post_state=ControlStateFeatures()),
    ]
    assert executed_families(records) == {
        "CANDIDATE_FREE_RECALL": 1, "SPECIALIST_PROBE": 1}


def test_telemetry_carries_no_gold_field() -> None:
    """Gold joins offline; a gold column here would leak it into inference."""
    payload = _record().to_json()
    assert "ObjectEntities" not in payload
    assert not any("gold" in key.lower() for key in payload)
