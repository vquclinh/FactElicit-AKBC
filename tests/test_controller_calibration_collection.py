"""Collection policy, live accounting and resume safety.

The failures targeted here are the ones that would silently produce a telemetry
file that looks complete: a family never executed, a call billed twice, a
resume that merges two different systems into one set of bins.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cover_kbc.controller_calibration.checkpoint import (
    CollectionCheckpoint,
    ResumeRefused,
    RunIdentity,
    resume_from,
)
from cover_kbc.controller_calibration.collection_policy import (
    COLLECTION_POLICY_VERSION,
    CollectionPolicyError,
    FamilyStatus,
    TrainCollectionPolicy,
    family_of,
    required_families,
)
from cover_kbc.controller_calibration.progress import (
    MIN_ROWS_FOR_ETA,
    RunCounters,
    format_duration,
    query_line,
    round_line,
    summary_block,
)


@dataclass(frozen=True)
class FakeAction:
    action_id: str
    action_family: str


def _catalogue(*pairs: tuple[str, str]) -> tuple[FakeAction, ...]:
    return tuple(FakeAction(a, f) for a, f in pairs)


# --------------------------------------------------------------------------
# collection policy
# --------------------------------------------------------------------------

def test_takes_from_every_legal_family_before_repeating_one() -> None:
    """A family left unexecuted has no support and cannot be calibrated."""
    policy = TrainCollectionPolicy(per_family_limit=1)
    chosen = policy.select(_catalogue(
        ("a1", "REVERSE"), ("a2", "REVERSE"), ("a3", "REVERSE"),
        ("b1", "COUNTERFACTUAL"), ("c1", "KEY_CONDITION"),
    ))
    assert {family_of(a) for a in chosen} == {
        "REVERSE", "COUNTERFACTUAL", "KEY_CONDITION"}
    assert len(chosen) == 3


def test_selection_is_deterministic() -> None:
    catalogue = _catalogue(("z", "REVERSE"), ("a", "REVERSE"), ("m", "REVERSE"))
    first = TrainCollectionPolicy(per_family_limit=2).select(catalogue)
    second = TrainCollectionPolicy(per_family_limit=2).select(catalogue)
    assert [a.action_id for a in first] == [a.action_id for a in second]


def test_selection_is_bounded_per_family() -> None:
    policy = TrainCollectionPolicy(per_family_limit=2)
    chosen = policy.select(_catalogue(*[(f"a{i}", "REVERSE") for i in range(9)]))
    assert len(chosen) == 2


def test_policy_never_invents_an_action() -> None:
    """The catalogue is the eligibility authority; selection is a subset of it."""
    catalogue = _catalogue(("a1", "REVERSE"))
    chosen = TrainCollectionPolicy().select(catalogue)
    assert set(chosen).issubset(set(catalogue))


def test_empty_catalogue_selects_nothing() -> None:
    assert TrainCollectionPolicy().select(()) == ()


def test_unselected_legal_actions_still_count_as_opportunities() -> None:
    policy = TrainCollectionPolicy(per_family_limit=1)
    policy.select(_catalogue(("a1", "REVERSE"), ("a2", "REVERSE")))
    assert policy.coverage.families["REVERSE"].legal_opportunities == 2


def test_a_legal_family_never_executed_fails_integrity() -> None:
    policy = TrainCollectionPolicy(per_family_limit=1)
    catalogue = _catalogue(("a1", "REVERSE"), ("b1", "COUNTERFACTUAL"))
    chosen = policy.select(catalogue)
    policy.record_outcome(chosen[0], succeeded=True)  # only one of two executed
    assert not policy.coverage.integrity_ok()
    assert policy.coverage.unobserved_families


def test_family_absent_from_train_is_distinct_from_a_coverage_failure() -> None:
    """Zero legal instances is a fact about TRAIN, not an implementation bug.

    "Surfaced by a catalogue and never legal" is the dataset fact. "Never
    surfaced at all" is a wiring failure and is tested separately below.
    """
    policy = TrainCollectionPolicy()
    policy.note_families(["CANDIDATE_FREE_RECALL"])
    policy.coverage.note_surfaced("CANDIDATE_FREE_RECALL")
    assert "CANDIDATE_FREE_RECALL" in policy.coverage.families_absent_from_train
    assert policy.coverage.integrity_ok()


def test_a_required_family_never_offered_fails_integrity() -> None:
    """Audit 0041 F-10: this used to read as PASS because the family was
    simply absent from the ledger."""
    policy = TrainCollectionPolicy()
    policy.note_families(["REVERSE_CHECK", "SPECIALIST_VERIFY"])
    policy.select(_catalogue(("a1", "SPECIALIST_VERIFY")))
    policy.coverage.note_executed("SPECIALIST_VERIFY", succeeded=True)

    assert policy.coverage.never_surfaced_families == ("REVERSE_CHECK",)
    assert not policy.coverage.integrity_ok()
    assert policy.coverage.families["REVERSE_CHECK"].status is (
        FamilyStatus.NEVER_SURFACED)


def test_the_four_coverage_states_are_distinguished() -> None:
    policy = TrainCollectionPolicy()
    policy.note_families(["REVERSE_CHECK"])                      # never surfaced
    policy.coverage.note_surfaced("COUNTERFACTUAL_VERIFY")        # absent from TRAIN
    policy.coverage.note_legal("CANDIDATE_FREE_RECALL")           # legal, unexecuted
    policy.coverage.note_legal("SPECIALIST_VERIFY")
    policy.coverage.note_executed("SPECIALIST_VERIFY", succeeded=True)   # observed

    states = {f: c.status for f, c in policy.coverage.families.items()}
    assert states == {
        "REVERSE_CHECK": FamilyStatus.NEVER_SURFACED,
        "COUNTERFACTUAL_VERIFY": FamilyStatus.ABSENT_FROM_TRAIN,
        "CANDIDATE_FREE_RECALL": FamilyStatus.LEGAL_BUT_UNEXECUTED,
        "SPECIALIST_VERIFY": FamilyStatus.OBSERVED,
    }


def test_the_family_vocabulary_comes_from_layer_6_not_a_string_list() -> None:
    from cover_kbc.control.planner_types import ActionFamily

    families = required_families(("m17", "m18"))
    assert set(families) <= {member.value for member in ActionFamily}
    assert "SPECIALIST_VERIFY" in families and "REVERSE_CHECK" in families


def test_the_round_robin_position_survives_the_controller_re_asking() -> None:
    """One action executes per round, so a restarting rotation starves a family."""
    policy = TrainCollectionPolicy()
    catalogue = _catalogue(
        ("c1", "COUNTERFACTUAL_VERIFY"), ("c2", "COUNTERFACTUAL_VERIFY"),
        ("c3", "COUNTERFACTUAL_VERIFY"), ("r1", "REVERSE_CHECK"),
    )
    policy.begin_query()
    heads = []
    remaining = list(catalogue)
    for _ in range(2):
        chosen = policy.select(tuple(remaining))
        heads.append(family_of(chosen[0]))
        remaining = [a for a in remaining if a is not chosen[0]]
    assert heads == ["COUNTERFACTUAL_VERIFY", "REVERSE_CHECK"]


def test_begin_query_resets_only_the_rotation_not_the_coverage() -> None:
    policy = TrainCollectionPolicy()
    policy.select(_catalogue(("a1", "REVERSE_CHECK")))
    policy.begin_query()
    assert policy.coverage.families["REVERSE_CHECK"].legal_opportunities == 1


def test_outcomes_split_success_and_failure() -> None:
    policy = TrainCollectionPolicy()
    chosen = policy.select(_catalogue(("a1", "REVERSE"), ("a2", "REVERSE")))
    policy.record_outcome(chosen[0], succeeded=True)
    policy.record_outcome(chosen[1], succeeded=False)
    entry = policy.coverage.families["REVERSE"]
    assert (entry.executed, entry.succeeded, entry.failed) == (2, 1, 1)
    assert "REVERSE" in policy.coverage.table()


def test_zero_family_limit_is_refused() -> None:
    with pytest.raises(CollectionPolicyError, match="at least 1"):
        TrainCollectionPolicy(per_family_limit=0)


def test_family_of_reads_enum_values() -> None:
    @dataclass(frozen=True)
    class Enumish:
        value: str

    @dataclass(frozen=True)
    class Check:
        check_kind: Enumish

    assert family_of(Check(Enumish("COUNTERFACTUAL"))) == "COUNTERFACTUAL"


# --------------------------------------------------------------------------
# live accounting
# --------------------------------------------------------------------------

def test_each_call_is_charged_to_exactly_one_role() -> None:
    counters = RunCounters(total_rows=477)
    counters.charge(role="enumerate", calls=3, prompt_tokens=10, generated_tokens=4)
    counters.charge(role="verify", calls=8, prompt_tokens=20)
    assert counters.physical_model_calls == 11
    assert counters.enumerator_calls + counters.verifier_calls == 11
    assert counters.prompt_tokens == 30 and counters.generated_tokens == 4


def test_an_unknown_role_cannot_absorb_calls() -> None:
    counters = RunCounters(total_rows=477)
    with pytest.raises(ValueError, match="exactly one role"):
        counters.charge(role="decide", calls=1)


def test_negative_accounting_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        RunCounters(total_rows=477).charge(role="verify", calls=-1)


def test_eta_stays_blank_until_enough_rows() -> None:
    """A confident wrong ETA is worse than an honest blank."""
    counters = RunCounters(total_rows=477)
    counters.rows_completed = MIN_ROWS_FOR_ETA - 1
    assert counters.eta_seconds is None
    assert format_duration(counters.eta_seconds) == "--"
    counters.rows_completed = MIN_ROWS_FOR_ETA
    assert counters.eta_seconds is not None


def test_percent_reaches_exactly_full() -> None:
    counters = RunCounters(total_rows=477, rows_completed=477)
    assert counters.percent == pytest.approx(100.0)


def test_summary_reports_every_required_field() -> None:
    block = summary_block(RunCounters(total_rows=477, rows_completed=120))
    for field in ("failed", "physical calls", "enumerator calls", "verifier calls",
                  "prompt tokens", "generated tokens", "elapsed", "ETA"):
        assert field in block
    assert "120 / 477" in block


def test_query_line_shows_current_over_total() -> None:
    line = query_line(21, 477, relation="hasArea", subject="Lošinj")
    assert "[TRAIN 21/477]" in line and "hasArea" in line


def test_round_line_has_no_fabricated_denominator() -> None:
    """Adaptive rounds have no known total; inventing one is a fake progress bar."""
    line = round_line(21, 477, round_index=3, detail="M18 COUNTERFACTUAL")
    assert "[round=3]" in line
    assert "round=3/" not in line


def test_restored_counters_do_not_inherit_a_stale_clock() -> None:
    original = RunCounters(total_rows=477, rows_completed=100, verifier_calls=40)
    original.started_at -= 10_000
    restored = RunCounters.restore(original.to_json(), total_rows=477)
    assert restored.rows_completed == 100 and restored.verifier_calls == 40
    assert restored.elapsed_seconds < 100


# --------------------------------------------------------------------------
# checkpoint and resume
# --------------------------------------------------------------------------

def _identity(**overrides) -> RunIdentity:
    base = dict(
        train_sha256="abc", repo_sha="def", config_sha256="ghi",
        enumerator_model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        enumerator_revision="95a6d26c",
        verifier_model_id="Qwen/Qwen3.5-4B", verifier_revision="851bf6e8",
        collection_policy_version=COLLECTION_POLICY_VERSION,
        telemetry_schema_version="train-telemetry-v1", total_rows=477,
    )
    base.update(overrides)
    return RunIdentity(**base)


def test_resume_skips_completed_rows(tmp_path) -> None:
    identity = _identity()
    path = tmp_path / "checkpoint.json"
    CollectionCheckpoint(identity, [0, 1, 2], [], {"rows_completed": 3}).save(path)
    restored = resume_from(path, identity)
    assert restored.completed_rows == [0, 1, 2]


@pytest.mark.parametrize("field,value", [
    ("train_sha256", "other"),
    ("repo_sha", "other"),
    ("config_sha256", "other"),
    ("enumerator_revision", "other"),
    ("verifier_revision", "other"),
    ("collection_policy_version", "collect-v0"),
    ("telemetry_schema_version", "train-telemetry-v0"),
    ("total_rows", 478),
])
def test_resume_refuses_any_identity_drift(tmp_path, field, value) -> None:
    path = tmp_path / "checkpoint.json"
    CollectionCheckpoint(_identity(), [0], [], {}).save(path)
    with pytest.raises(ResumeRefused, match="different run"):
        resume_from(path, _identity(**{field: value}))


def test_resume_refuses_a_missing_checkpoint(tmp_path) -> None:
    with pytest.raises(ResumeRefused, match="no checkpoint"):
        resume_from(tmp_path / "absent.json", _identity())


def test_resume_refuses_a_corrupt_checkpoint(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ResumeRefused, match="unreadable checkpoint"):
        resume_from(path, _identity())


def test_checkpoint_write_is_atomic(tmp_path) -> None:
    """A torn checkpoint is worse than none, so no partial file may survive."""
    path = tmp_path / "checkpoint.json"
    CollectionCheckpoint(_identity(), [0, 1], [], {}).save(path)
    assert path.is_file()
    assert not list(tmp_path.glob("*.partial"))


def test_checkpoint_round_trips_counters(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    counters = RunCounters(total_rows=477, rows_completed=12, verifier_calls=96)
    CollectionCheckpoint(_identity(), [0], [], counters.to_json()).save(path)
    restored = resume_from(path, _identity())
    assert restored.counters["verifier_calls"] == 96
