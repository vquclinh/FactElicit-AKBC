"""Forced mid-row failure, then resume, through the real persistence path.

This is the test Audits 0038 and 0039 named as the last blocker. It drives the
collection runner's actual ``main()`` — real ``TelemetryWriter`` open modes,
real checkpoint, real predictions/accounting/coverage files — and injects a
fatal exception *after* one action of row 2 has already executed.

Nothing about persistence is mocked. Mocking the writer's open mode is exactly
what would have hidden the Audit-0038 truncation bug, so the failure is injected
into action execution instead and every artefact is read back off disk.

Scripted offline runtimes. No weights.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

# The runner bootstraps ``src`` via ``scripts/_bootstrap.py``, so that
# directory must be importable before the module is executed.
sys.path.insert(0, str(ROOT / "scripts"))

_SPEC = importlib.util.spec_from_file_location(
    "run_train_calibration_collection",
    ROOT / "scripts" / "run_train_calibration_collection.py",
)
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


class ForcedFailure(RuntimeError):
    """A deterministic stand-in for a fatal mid-row runtime error."""


@pytest.fixture
def config(tmp_path) -> Path:
    """The real experiment config, pointed at an offline model profile."""
    real = yaml.safe_load(
        (ROOT / "configs" / "experiments" / "cover_kbc_v2_mistral24_qwen4.yaml").read_text())
    smoke = yaml.safe_load(
        (ROOT / "configs" / "experiments" / "smoke_abstain.yaml").read_text())

    merged = copy.deepcopy(real)
    merged["model_profile"] = smoke.get("model_profile", smoke.get("model"))
    merged.setdefault("experiment", {})["split"] = "train"
    for block in ("consensus", "layer4_integration", "coverage_gap",
                  "specialist_verifier", "bidirectional_verification"):
        merged.setdefault(block, {})["enabled"] = True
    for specialist in merged["specialists"].values():
        if isinstance(specialist, dict):
            specialist["enabled"] = True
    merged["query_intelligence"]["parametric_retrieval"]["enabled"] = True

    path = tmp_path / "collect.yaml"
    path.write_text(yaml.safe_dump(merged), encoding="utf-8")
    return path


def _argv(config: Path, out: Path, *, resume: bool) -> list[str]:
    argv = ["run_train_calibration_collection.py", "--config", str(config),
            "--output-dir", str(out), "--limit", "2"]
    if resume:
        argv.append("--resume")
    return argv


def _run(monkeypatch, config: Path, out: Path, *, resume: bool = False) -> int:
    monkeypatch.setattr("sys.argv", _argv(config, out, resume=resume))
    try:
        return runner.main()
    except SystemExit as exit_code:          # argparse/refusal paths
        return int(exit_code.code or 0)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _run_dir(out: Path) -> Path:
    directories = [p for p in out.iterdir() if p.is_dir()]
    assert len(directories) == 1, f"expected one run directory, found {directories}"
    return directories[0]


def _identities(records: list[dict]) -> list[tuple]:
    return [(r["row_index"], r["round_index"], r["operation_id"]) for r in records]


@pytest.fixture
def failed_then_resumed(monkeypatch, config, tmp_path):
    """Run to a forced mid-row failure, capture disk state, then resume."""
    out = tmp_path / "run"
    original = runner.CoverPipeline.execute_action
    executed = {"count": 0}

    def failing(self, kind, action, consensus, graph, *, round_index=1):
        """Row 0 commits; row 1 executes one real action and then dies.

        The action is executed *first*, so its graph effects and physical calls
        genuinely happened before the fatal error - which is what makes this a
        mid-row failure rather than a failure at a row boundary.
        """
        record = original(self, kind, action, consensus, graph,
                          round_index=round_index)
        if graph.query.row_index == 1 and record.get("executed"):
            executed["count"] += 1
            raise ForcedFailure("forced fatal error after row 2 action A")
        return record

    monkeypatch.setattr(runner.CoverPipeline, "execute_action", failing)
    first_code = _run(monkeypatch, config, out)
    run_dir = _run_dir(out)

    after_failure = {
        "code": first_code,
        "telemetry": _read_jsonl(run_dir / "train_telemetry.jsonl"),
        "predictions": _read_jsonl(run_dir / "predictions.jsonl"),
        "checkpoint": json.loads((out / "checkpoint.json").read_text()),
        "accounting": json.loads((run_dir / "accounting.json").read_text()),
        "coverage": json.loads((run_dir / "action_coverage.json").read_text()),
    }

    # Restore real execution and resume the *same* run directory.
    monkeypatch.setattr(runner.CoverPipeline, "execute_action", original)
    second_code = _run(monkeypatch, config, out, resume=True)

    return {
        "after_failure": after_failure,
        "resume_code": second_code,
        "run_dir": run_dir,
        "telemetry": _read_jsonl(run_dir / "train_telemetry.jsonl"),
        "predictions": _read_jsonl(run_dir / "predictions.jsonl"),
        "checkpoint": json.loads((out / "checkpoint.json").read_text()),
        "accounting": json.loads((run_dir / "accounting.json").read_text()),
        "coverage": json.loads((run_dir / "action_coverage.json").read_text()),
    }


# --------------------------------------------------------------------------
# state immediately after the forced failure
# --------------------------------------------------------------------------

def test_the_forced_failure_exits_non_zero(failed_then_resumed) -> None:
    assert failed_then_resumed["after_failure"]["code"] != 0


def test_row_one_is_durable_after_the_failure(failed_then_resumed) -> None:
    state = failed_then_resumed["after_failure"]
    assert [r for r in state["telemetry"] if r["row_index"] == 0]
    assert len(state["predictions"]) == 1
    assert state["checkpoint"]["completed_rows"] == [0]


def test_the_failed_row_leaves_nothing_durable(failed_then_resumed) -> None:
    """The row transaction must abort whole, not partially."""
    state = failed_then_resumed["after_failure"]
    assert not [r for r in state["telemetry"] if r["row_index"] == 1], (
        "the failed row committed telemetry")
    assert all(p["SubjectEntity"] for p in state["predictions"])
    assert 1 not in state["checkpoint"]["completed_rows"]


def test_failed_row_work_is_not_counted_in_persisted_accounting(
        failed_then_resumed) -> None:
    """Physical calls burned by the dead process must not enter calibration
    accounting; only committed rows may."""
    state = failed_then_resumed["after_failure"]
    assert state["accounting"]["rows_completed"] == 1


def test_failed_row_coverage_is_not_committed(failed_then_resumed) -> None:
    state = failed_then_resumed["after_failure"]
    committed = sum(entry["executed"]
                    for entry in state["coverage"]["families"])
    row_one_actions = len([r for r in state["telemetry"]
                           if r["row_index"] == 0 and r["executed"]])
    assert committed == row_one_actions


# --------------------------------------------------------------------------
# resume
# --------------------------------------------------------------------------

def test_resume_completes_successfully(failed_then_resumed) -> None:
    assert failed_then_resumed["resume_code"] == 0


def test_resume_preserves_the_previously_committed_row(failed_then_resumed) -> None:
    """The Audit-0038 truncation bug: old records must survive the append."""
    before = _identities(
        [r for r in failed_then_resumed["after_failure"]["telemetry"]])
    after = _identities(failed_then_resumed["telemetry"])
    assert before, "nothing was committed before resume"
    for identity in before:
        assert identity in after, "resume destroyed a previously committed record"
    assert len(after) > len(before), "resume appended nothing"


def test_no_duplicate_execution_identities_after_resume(failed_then_resumed) -> None:
    identities = _identities(failed_then_resumed["telemetry"])
    assert len(identities) == len(set(identities)), "duplicate telemetry identity"


def test_no_stale_record_from_the_failed_attempt_survived(
        failed_then_resumed) -> None:
    rows = [r["row_index"] for r in failed_then_resumed["telemetry"]]
    # Row 1 appears only from the successful replay.
    replayed = [r for r in failed_then_resumed["telemetry"] if r["row_index"] == 1]
    assert replayed, "the resumed row committed nothing"
    assert len(rows) == len(set(_identities(failed_then_resumed["telemetry"])))


def test_predictions_are_exactly_once_per_row(failed_then_resumed) -> None:
    predictions = failed_then_resumed["predictions"]
    keys = [(p["SubjectEntity"], p["Relation"]) for p in predictions]
    assert len(keys) == 2, f"expected one prediction per row, got {keys}"
    assert len(keys) == len(set(keys)), "duplicate prediction row"


def test_accounting_counts_each_committed_row_once(failed_then_resumed) -> None:
    assert failed_then_resumed["accounting"]["rows_completed"] == 2


def test_role_partition_survives_the_resume(failed_then_resumed) -> None:
    accounting = failed_then_resumed["accounting"]
    assert (accounting["enumerator_calls"] + accounting["verifier_calls"]
            == accounting["physical_model_calls"])


def test_coverage_matches_committed_telemetry(failed_then_resumed) -> None:
    executed = len([r for r in failed_then_resumed["telemetry"] if r["executed"]])
    committed = sum(entry["executed"]
                    for entry in failed_then_resumed["coverage"]["families"])
    assert committed == executed


def test_checkpoint_agrees_with_the_artifacts(failed_then_resumed) -> None:
    checkpoint = failed_then_resumed["checkpoint"]
    assert sorted(checkpoint["completed_rows"]) == [0, 1]
    rows_in_telemetry = {r["row_index"] for r in failed_then_resumed["telemetry"]}
    assert rows_in_telemetry <= set(checkpoint["completed_rows"])
    assert len(failed_then_resumed["predictions"]) == len(
        checkpoint["completed_rows"])
