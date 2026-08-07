"""The collection runner's real persistence path, driven through ``main()``.

Two failures are targeted, and both cost a full TRAIN session when they happen:

**Losing committed work.** Audit 0038 found a resume truncating the telemetry it
was meant to append to. Audit 0041 then found something worse and quieter: the
*second* resume minted a fresh ``run_id``, wrote it back into the checkpoint,
and landed in a directory that had never existed - so rows 1-2 sat in one
directory, rows 3-4 in another, and the manifest in the second claimed all four.
The old test could not see it, because it resumed exactly once and asserted
there was exactly one run directory.

**Declaring success anyway.** A run that reaches the end is not a run that
collected anything, so the exit gate is asserted here too.

Nothing about persistence is mocked. The **committed** TRAIN collection config
is loaded unmodified; only the runtimes are replaced, because the test may not
load 28.67B of weights. Mocking the writer's open mode is exactly what would
have hidden the truncation bug, so the failure is injected into action
execution and every artefact is read back off disk.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

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

#: The repository's canonical TRAIN collection profile. Deliberately **not**
#: rebuilt here: a test that synthesises its own config proves nothing about
#: the file an operator will actually run (Audit 0041 F-01).
CONFIG = ROOT / "configs" / "experiments" / "cover_kbc_v2_train_collection.yaml"

#: Scripted answers, one per relation, so candidates genuinely land and the
#: Layer-4 seam has something to verify. No factual claim is made or needed.
ANSWERS = {
    "hasArea": "45000",
    "hasCapacity": "45000",
    "personHasCityOfDeath": "Paris",
    "companyTradesAtStockExchange": "NASDAQ; New York Stock Exchange",
    "awardWonBy": "Marie Curie; Albert Einstein",
    "countryLandBordersCountry": "Spain; Andorra; Monaco",
}


class ForcedFatal(MemoryError):
    """A deterministic stand-in for a process-fatal error such as CUDA OOM.

    ``MemoryError`` because that is what the runner's exception boundary
    actually classifies as fatal - a contained row failure would not exercise
    the resume path at all, which is the point of this fixture.
    """


class ForcedRowFailure(RuntimeError):
    """A row-local error: the kind that must **not** destroy 477 rows."""


def _scripted_runtime(config):
    from cover_kbc.models.offline import ScriptedRuntime

    role = str(config.get("role", "enumerator"))
    return ScriptedRuntime(
        model_id=f"offline/{role}", role=role,
        family=str(config.get("family", "offline")),
        fallback=lambda request: ANSWERS.get(
            str(request.metadata.get("relation", "")), "NONE"),
    )


def _argv(out: Path, *, resume: bool, limit: int) -> list[str]:
    argv = ["run_train_calibration_collection.py", "--config", str(CONFIG),
            "--output-dir", str(out), "--limit", str(limit)]
    if resume:
        argv.append("--resume")
    return argv


def _run(monkeypatch, out: Path, *, resume: bool = False, limit: int = 4) -> int:
    monkeypatch.setattr("sys.argv", _argv(out, resume=resume, limit=limit))
    monkeypatch.setattr(runner, "build_runtime", _scripted_runtime)
    try:
        return runner.main()
    except SystemExit as exit_code:          # argparse/refusal paths
        return int(exit_code.code or 0)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _run_dirs(out: Path) -> list[Path]:
    return sorted(p for p in out.iterdir() if p.is_dir())


def _identities(records: list[dict]) -> list[tuple]:
    return [(r["row_index"], r["round_index"], r["operation_id"]) for r in records]


def _complete_small_collection(monkeypatch, tmp_path, name: str):
    out = tmp_path / name
    assert _run(monkeypatch, out, limit=3) == 0
    run_dir = _run_dirs(out)[0]
    return out, run_dir


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n"
                for record in records),
        encoding="utf-8",
    )


def _checkpoint_payload(out: Path) -> dict:
    return json.loads((out / "checkpoint.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def multi_resume(tmp_path_factory):
    """fail -> resume + fail -> resume, through the real runner each time."""
    monkeypatch = pytest.MonkeyPatch()
    out = tmp_path_factory.mktemp("run")
    original = runner.CoverPipeline.execute_action

    def failing_on(row_index: int):
        def failing(self, kind, action, consensus, graph, *, round_index=1):
            """Execute the action for real, *then* die.

            Executing first is what makes this a mid-row failure rather than a
            failure at a row boundary: the action's graph effects and physical
            calls genuinely happened before the fatal error.
            """
            record = original(self, kind, action, consensus, graph,
                              round_index=round_index)
            if graph.query.row_index == row_index and record.get("executed"):
                raise ForcedFatal(f"forced fatal error inside row {row_index}")
            return record
        return failing

    monkeypatch.setattr(runner.CoverPipeline, "execute_action", failing_on(1))
    first_code = _run(monkeypatch, out)
    after_first = {
        "code": first_code,
        "dirs": [p.name for p in _run_dirs(out)],
        "telemetry": _read_jsonl(_run_dirs(out)[0] / "train_telemetry.jsonl"),
        "checkpoint": json.loads((out / "checkpoint.json").read_text()),
    }

    monkeypatch.setattr(runner.CoverPipeline, "execute_action", failing_on(2))
    second_code = _run(monkeypatch, out, resume=True)
    after_second = {
        "code": second_code,
        "dirs": [p.name for p in _run_dirs(out)],
        "telemetry": _read_jsonl(_run_dirs(out)[0] / "train_telemetry.jsonl"),
        "checkpoint": json.loads((out / "checkpoint.json").read_text()),
    }

    monkeypatch.setattr(runner.CoverPipeline, "execute_action", original)
    third_code = _run(monkeypatch, out, resume=True)
    run_dir = _run_dirs(out)[0]
    state = {
        "after_first": after_first, "after_second": after_second,
        "code": third_code, "out": out, "run_dir": run_dir,
        "dirs": [p.name for p in _run_dirs(out)],
        "telemetry": _read_jsonl(run_dir / "train_telemetry.jsonl"),
        "predictions": _read_jsonl(run_dir / "predictions.jsonl"),
        "checkpoint": json.loads((out / "checkpoint.json").read_text()),
        "accounting": json.loads((run_dir / "accounting.json").read_text()),
        "coverage": json.loads((run_dir / "action_coverage.json").read_text()),
        "manifest": json.loads((run_dir / "manifest.json").read_text()),
    }
    monkeypatch.undo()
    return state


# --------------------------------------------------------------------------
# the committed config really is what runs
# --------------------------------------------------------------------------

def test_the_committed_config_passes_the_readiness_gate() -> None:
    import yaml

    from cover_kbc.controller_calibration.readiness import (
        evaluate_collection_readiness,
    )

    report = evaluate_collection_readiness(
        yaml.safe_load(CONFIG.read_text()), base_dir=CONFIG.parent)
    assert report.may_run_collection, report.blockers


def test_the_committed_config_selects_train_and_builds_m11_to_m19(
        multi_resume) -> None:
    manifest = multi_resume["manifest"]
    assert manifest["identity"]["total_rows"] == 4
    assert manifest["integration_mode"] == "train_calibration_collection_only"
    families = {e["action_family"] for e in manifest["coverage"]["families"]}
    # Module 17 and Module 18 both surfaced actions, which they can only do
    # when M11-M16 and Layer-4 built the state they read.
    assert "SPECIALIST_VERIFY" in families
    assert families & {"CANDIDATE_FREE_RECALL", "COUNTERFACTUAL_VERIFY"}


def test_the_run_produces_non_empty_meaningful_telemetry(multi_resume) -> None:
    executed = [r for r in multi_resume["telemetry"] if r["executed"]]
    assert executed, "the committed config executed no action"
    assert all(r["pre_state"]["measured"] for r in executed)
    assert any(r["pre_state"]["residual"] != r["post_state"]["residual"]
               for r in executed), "no action changed Module 19's residual"


def test_program_type_is_canonical_in_every_record(multi_resume) -> None:
    from cover_kbc.contracts.base import ProgramType

    values = {r["program_type"] for r in multi_resume["telemetry"]}
    assert values <= {member.value for member in ProgramType}
    assert not any("ProgramType." in value for value in values)


def test_action_identity_is_deterministic_not_a_memory_address(
        multi_resume) -> None:
    for record in multi_resume["telemetry"]:
        if record["executed"]:
            assert record["action_id"].startswith(("M17:", "M18:"))
            assert record["action_id"] in record["operation_id"]


# --------------------------------------------------------------------------
# state after each forced failure
# --------------------------------------------------------------------------

def test_each_forced_failure_exits_non_zero(multi_resume) -> None:
    assert multi_resume["after_first"]["code"] != 0
    assert multi_resume["after_second"]["code"] != 0


def test_the_failed_row_leaves_nothing_durable(multi_resume) -> None:
    """The row transaction must abort whole, not partially."""
    first = multi_resume["after_first"]
    assert [r for r in first["telemetry"] if r["row_index"] == 0]
    assert not [r for r in first["telemetry"] if r["row_index"] == 1]
    assert first["checkpoint"]["completed_rows"] == [0]


def test_the_second_resume_made_progress_before_failing(multi_resume) -> None:
    second = multi_resume["after_second"]
    assert second["checkpoint"]["completed_rows"] == [0, 1]


# --------------------------------------------------------------------------
# the F-05 invariant: one directory, one identity, across every resume
# --------------------------------------------------------------------------

def test_every_resume_stays_in_one_run_directory(multi_resume) -> None:
    """Audit 0041 F-05: the second resume used to land in a fresh directory."""
    assert len(multi_resume["dirs"]) == 1, multi_resume["dirs"]
    assert multi_resume["after_first"]["dirs"] == multi_resume["dirs"]
    assert multi_resume["after_second"]["dirs"] == multi_resume["dirs"]


def test_one_stable_run_id_across_every_resume(multi_resume) -> None:
    run_id = multi_resume["run_dir"].name
    assert multi_resume["checkpoint"]["counters"]["run_id"] == run_id
    assert {r["run_id"] for r in multi_resume["telemetry"]} == {run_id}
    assert multi_resume["manifest"]["run_id"] == run_id


def test_resume_preserves_every_previously_committed_record(multi_resume) -> None:
    """The Audit-0038 truncation bug: old records must survive the append."""
    for stage in ("after_first", "after_second"):
        before = _identities(multi_resume[stage]["telemetry"])
        assert before, f"nothing was committed before {stage}"
        after = _identities(multi_resume["telemetry"])
        for identity in before:
            assert identity in after, f"{stage}: a committed record was destroyed"


def test_no_duplicate_action_identity_after_two_resumes(multi_resume) -> None:
    identities = _identities(multi_resume["telemetry"])
    assert len(identities) == len(set(identities))


def test_predictions_are_exactly_once_per_row(multi_resume) -> None:
    keys = [(p["SubjectEntity"], p["Relation"])
            for p in multi_resume["predictions"]]
    assert len(keys) == 4, keys
    assert len(keys) == len(set(keys))


def test_accounting_stays_cumulative_across_both_resumes(multi_resume) -> None:
    accounting = multi_resume["accounting"]
    assert accounting["rows_completed"] == 4
    assert (accounting["enumerator_calls"] + accounting["verifier_calls"]
            == accounting["physical_model_calls"])
    assert accounting["prompt_tokens"] > 0, "prompt tokens were never charged"


def test_coverage_stays_cumulative_across_both_resumes(multi_resume) -> None:
    executed = len([r for r in multi_resume["telemetry"] if r["executed"]])
    committed = sum(entry["executed"]
                    for entry in multi_resume["coverage"]["families"])
    assert committed == executed


def test_checkpoint_references_the_existing_run_directory(multi_resume) -> None:
    checkpoint = multi_resume["checkpoint"]
    assert sorted(checkpoint["completed_rows"]) == [0, 1, 2, 3]
    assert (multi_resume["out"] / checkpoint["counters"]["run_id"]).is_dir()
    assert len(multi_resume["predictions"]) == len(checkpoint["completed_rows"])


def test_the_manifest_row_count_matches_the_artifacts(multi_resume) -> None:
    manifest = multi_resume["manifest"]
    assert manifest["rows_completed"] == len(multi_resume["predictions"])
    assert manifest["prediction_rows"] == len(multi_resume["predictions"])
    rows = {r["row_index"] for r in multi_resume["telemetry"]}
    assert rows <= set(manifest["identity"] and
                       multi_resume["checkpoint"]["completed_rows"])


def test_the_completed_run_passes_its_own_exit_gate(multi_resume) -> None:
    assert multi_resume["code"] == 0, multi_resume["manifest"]["gate_blockers"]
    assert multi_resume["manifest"]["sufficiency"]["ok"]
    assert multi_resume["manifest"]["status"] == "complete"


# --------------------------------------------------------------------------
# F-07: a row-local failure is contained, counted, and fails the gate
# --------------------------------------------------------------------------

def test_a_row_local_failure_is_contained_and_reported(monkeypatch, tmp_path) -> None:
    """One bad row must not destroy the other 476 - and must not read as success."""
    original = runner.CoverPipeline.decide_graph

    def failing(self, graph):
        if graph.query.row_index == 1:
            raise ForcedRowFailure("scripted row-local failure")
        return original(self, graph)

    monkeypatch.setattr(runner.CoverPipeline, "decide_graph", failing)
    out = tmp_path / "contained"
    code = _run(monkeypatch, out, limit=3)

    manifest = json.loads((_run_dirs(out)[0] / "manifest.json").read_text())
    predictions = _read_jsonl(_run_dirs(out)[0] / "predictions.jsonl")
    telemetry = _read_jsonl(_run_dirs(out)[0] / "train_telemetry.jsonl")

    # It continued past the bad row...
    assert manifest["rows_completed"] == 2
    assert len(predictions) == 2
    # ...recorded the failure truthfully, rather than printing failed=0...
    assert manifest["unresolved_failed_row_count"] == 1
    assert manifest["unresolved_failed_rows"] == [1]
    assert manifest["failed_attempts"] == 1
    assert manifest["failure_history"][0]["error"].startswith("ForcedRowFailure")
    assert manifest["failure_history"][0]["resolved"] is False
    assert manifest["failed_attempt_calls"] > 0
    # ...kept the wasted work out of the committed calibration accounting...
    assert manifest["accounting"]["failed_attempt_calls"] > 0
    # ...emitted no telemetry or prediction for it...
    assert not [r for r in telemetry if r["row_index"] == 1]
    # ...and refused to call the run a success.
    assert code != 0
    assert any("failed" in blocker for blocker in manifest["gate_blockers"])


def test_a_process_fatal_error_still_aborts(monkeypatch, tmp_path) -> None:
    """Containment must not swallow the failures that make the run untrustworthy."""
    original = runner.CoverPipeline.decide_graph

    def failing(self, graph):
        if graph.query.row_index == 1:
            raise ForcedFatal("CUDA out of memory")
        return original(self, graph)

    monkeypatch.setattr(runner.CoverPipeline, "decide_graph", failing)
    out = tmp_path / "fatal"
    assert _run(monkeypatch, out, limit=3) != 0
    manifest = json.loads((_run_dirs(out)[0] / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
    assert manifest["aborted"]["cuda_oom"] is True
    assert manifest["rows_completed"] == 1


@pytest.mark.parametrize("error,fatal", [
    (MemoryError("oom"), True),
    (OSError("no space left on device"), True),
    (KeyboardInterrupt(), True),
    (RuntimeError("CUDA out of memory"), True),
    (ValueError("could not parse '42abc'"), False),
    (RuntimeError("model said something odd"), False),
])
def test_the_exception_boundary_is_explicit(error, fatal) -> None:
    assert runner.is_fatal(error) is fatal


def test_a_resume_without_a_recorded_run_id_is_refused(tmp_path) -> None:
    """A checkpoint that cannot name its directory must not invent one."""
    from cover_kbc.controller_calibration.checkpoint import (
        CollectionCheckpoint,
        ResumeRefused,
        RunIdentity,
        resume_from,
    )

    identity = RunIdentity(
        train_sha256="a", repo_sha="b", config_sha256="c",
        enumerator_model_id="e", enumerator_revision="1",
        verifier_model_id="v", verifier_revision="2",
        collection_policy_version="collect-v1",
        telemetry_schema_version="train-telemetry-v2", total_rows=4)
    path = tmp_path / "checkpoint.json"
    CollectionCheckpoint(identity, [0], [], {"rows_completed": 1}).save(path)
    restored = resume_from(path, identity)
    assert not restored.counters.get("run_id")
    with pytest.raises(ResumeRefused, match="records no run_id"):
        raise ResumeRefused(f"{path} records no run_id")


# --------------------------------------------------------------------------
# Audit 0043 C-03: an unresolved failure and a failure that happened are
# different questions, and only the first may block the gate
# --------------------------------------------------------------------------

def test_a_retried_row_stops_being_an_unresolved_failure(monkeypatch, tmp_path):
    """A row that fails and later succeeds must not block the run forever.

    Audit 0043 C-03: the failed set was persisted and never cleared, so after a
    successful retry the run still reported "row(s) failed and were omitted
    from telemetry" - which was by then untrue - and exit 0 became unreachable
    for the rest of a 477-row session. Containment exists to preserve progress;
    a feature that permanently poisons the verdict does the opposite.
    """
    original = runner.CoverPipeline.decide_graph
    fail_row = {"index": 1}

    def failing(self, graph):
        if graph.query.row_index == fail_row["index"]:
            raise ForcedRowFailure("scripted row-local failure")
        return original(self, graph)

    monkeypatch.setattr(runner.CoverPipeline, "decide_graph", failing)
    out = tmp_path / "retry"
    assert _run(monkeypatch, out, limit=3) != 0

    first = json.loads((_run_dirs(out)[0] / "manifest.json").read_text())
    assert first["unresolved_failed_rows"] == [1]
    assert first["failure_history"][0]["resolved"] is False
    burned = first["failed_attempt_calls"]
    committed_calls = first["accounting"]["physical_model_calls"]
    assert burned > 0

    # The retry succeeds.
    fail_row["index"] = -1
    assert _run(monkeypatch, out, resume=True, limit=3) == 0

    run_dir = _run_dirs(out)[0]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    telemetry = _read_jsonl(run_dir / "train_telemetry.jsonl")
    predictions = _read_jsonl(run_dir / "predictions.jsonl")

    # Complete, and it says so.
    assert manifest["status"] == "complete"
    assert manifest["rows_completed"] == 3
    assert manifest["unresolved_failed_rows"] == []
    assert manifest["gate_blockers"] == []
    assert manifest["sufficiency"]["ok"]

    # The history is *kept*, not cleared to make the gate pass.
    assert manifest["failed_attempts"] == 1
    assert manifest["failure_history"][0]["row_index"] == 1
    assert manifest["failure_history"][0]["resolved"] is True
    assert manifest["failed_attempt_calls"] == burned

    # Exactly once, everywhere, and the wasted attempt is not in the committed
    # accounting the telemetry is backed by.
    assert len(predictions) == 3
    assert sorted({r["row_index"] for r in telemetry}) == [0, 1, 2]
    assert len(_identities(telemetry)) == len(set(_identities(telemetry)))
    assert manifest["accounting"]["physical_model_calls"] > committed_calls
    assert len(_run_dirs(out)) == 1


# --------------------------------------------------------------------------
# Audit 0043 C-04: a kill inside the row commit must recover without a human
# --------------------------------------------------------------------------

class _HardKill(BaseException):
    """Stands in for SIGKILL: it escapes `except Exception` like a real one."""


@pytest.mark.parametrize("window", ["telemetry", "coverage", "prediction"])
def test_a_kill_inside_the_row_commit_recovers_automatically(
    monkeypatch, tmp_path, window,
) -> None:
    """Roll the interrupted row back to the checkpoint, then replay it.

    The row commit is several file writes. A process killed between the first
    and the last leaves that row's telemetry on disk while the checkpoint has
    not accepted it, and the duplicate guard then refused the replay - so every
    later resume aborted until someone hand-edited a JSONL file (Audit 0043
    C-04). The checkpoint is the commit boundary; uncommitted material is
    discarded on resume, and the row is simply re-run.

    Three windows, one per durable write in the transaction. A real SIGKILL
    also skips the ``finally`` block; that variant is covered by the audit's
    ``os._exit`` probes and produces the same on-disk shape as these.
    """
    from cover_kbc.controller_calibration.collection_policy import CoverageLedger
    from cover_kbc.controller_calibration.progress import RunCounters
    from cover_kbc.controller_calibration.telemetry import TelemetryWriter

    out = tmp_path / "crash"
    state = {"row": None, "written": 0, "armed": True}

    real_write = TelemetryWriter.write

    def write(self, record):
        result = real_write(self, record)
        state["row"] = record.row_index
        state["written"] += 1
        if (window == "telemetry" and state["armed"]
                and record.row_index == 1 and state["written"] >= 2):
            state["armed"] = False
            raise _HardKill("killed after a telemetry flush")
        return result

    real_note = CoverageLedger.note_executed

    def note(self, family, *, succeeded):
        real_note(self, family, succeeded=succeeded)
        if window == "coverage" and state["armed"] and state["row"] == 1:
            state["armed"] = False
            raise _HardKill("killed after coverage, before the prediction")

    # The prediction is written and flushed immediately before the counters are
    # charged, so dying here is "prediction on disk, row not yet committed".
    real_charge = RunCounters.charge

    def charge(self, **kwargs):
        if window == "prediction" and state["armed"] and state["row"] == 1:
            state["armed"] = False
            raise _HardKill("killed after the prediction, before the checkpoint")
        return real_charge(self, **kwargs)

    monkeypatch.setattr(TelemetryWriter, "write", write)
    monkeypatch.setattr(CoverageLedger, "note_executed", note)
    monkeypatch.setattr(RunCounters, "charge", charge)

    assert _run(monkeypatch, out, limit=3) != 0
    monkeypatch.undo()

    run_dir = _run_dirs(out)[0]
    checkpoint = json.loads((out / "checkpoint.json").read_text())
    stranded = _read_jsonl(run_dir / "train_telemetry.jsonl")
    # The interrupted row really did leave material behind.
    assert 1 not in checkpoint["completed_rows"]
    assert [r for r in stranded if r["row_index"] == 1]

    # Resume: no manual intervention, no duplicate, no abort.
    assert _run(monkeypatch, out, resume=True, limit=3) == 0

    manifest = json.loads((run_dir / "manifest.json").read_text())
    telemetry = _read_jsonl(run_dir / "train_telemetry.jsonl")
    predictions = _read_jsonl(run_dir / "predictions.jsonl")

    assert manifest["resume_reconciliation"]["clean"] is False
    assert 1 in manifest["resume_reconciliation"]["telemetry_rows_dropped"]
    if window == "prediction":
        # The extra prediction line for the uncommitted row was rolled back too.
        assert manifest["resume_reconciliation"]["prediction_lines_dropped"] == 1
    assert manifest["status"] == "complete"
    assert manifest["rows_completed"] == 3
    assert manifest["gate_blockers"] == []
    assert manifest["sufficiency"]["ok"]
    assert len(predictions) == 3
    assert sorted({r["row_index"] for r in telemetry}) == [0, 1, 2]
    assert len(_identities(telemetry)) == len(set(_identities(telemetry)))
    assert len(_run_dirs(out)) == 1


def test_reconciliation_leaves_a_clean_resume_untouched(multi_resume) -> None:
    """Rollback must only ever remove uncommitted material."""
    assert multi_resume["manifest"]["resume_reconciliation"]["clean"] is True
    assert multi_resume["manifest"]["resume_reconciliation"][
        "telemetry_records_dropped"] == 0


# --------------------------------------------------------------------------
# Audit 0045 C-16: committed telemetry integrity is a checkpoint boundary
# --------------------------------------------------------------------------

def test_resume_refuses_when_a_committed_rows_telemetry_is_deleted(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "missing-row")
    telemetry_path = run_dir / "train_telemetry.jsonl"
    records = _read_jsonl(telemetry_path)
    _write_jsonl(telemetry_path, [r for r in records if r["row_index"] != 1])

    with pytest.raises(runner.ResumeRefused, match="committed telemetry"):
        _run(monkeypatch, out, resume=True, limit=3)


def test_resume_refuses_when_one_committed_telemetry_record_is_deleted(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "missing-record")
    telemetry_path = run_dir / "train_telemetry.jsonl"
    records = _read_jsonl(telemetry_path)
    assert records
    _write_jsonl(telemetry_path, records[1:])

    with pytest.raises(runner.ResumeRefused, match="committed telemetry"):
        _run(monkeypatch, out, resume=True, limit=3)


def test_resume_refuses_when_a_committed_telemetry_byte_changes(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "hash-mismatch")
    telemetry_path = run_dir / "train_telemetry.jsonl"
    payload = bytearray(telemetry_path.read_bytes())
    marker = b'"row_index": 0'
    offset = payload.index(marker) + len(marker) - 1
    payload[offset] = ord("9")
    telemetry_path.write_bytes(bytes(payload))

    with pytest.raises(runner.ResumeRefused, match="SHA-256 mismatch"):
        _run(monkeypatch, out, resume=True, limit=3)


def test_resume_refuses_when_committed_telemetry_file_is_truncated(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "truncated")
    telemetry_path = run_dir / "train_telemetry.jsonl"
    telemetry_path.write_bytes(telemetry_path.read_bytes()[:-1])

    with pytest.raises(runner.ResumeRefused, match="committed telemetry bytes"):
        _run(monkeypatch, out, resume=True, limit=3)


def test_resume_refuses_torn_final_line_inside_committed_prefix(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "torn-prefix")
    telemetry_path = run_dir / "train_telemetry.jsonl"
    raw = telemetry_path.read_text(encoding="utf-8").rstrip("\n")
    last_break = raw.rfind("\n")
    final_line = raw[last_break + 1:]
    telemetry_path.write_text(
        raw[:last_break + 1] + final_line[: max(1, len(final_line) // 3)],
        encoding="utf-8",
    )

    with pytest.raises(runner.ResumeRefused, match="committed telemetry bytes"):
        _run(monkeypatch, out, resume=True, limit=3)


def test_resume_discards_complete_uncommitted_telemetry_after_prefix(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "valid-tail")
    telemetry_path = run_dir / "train_telemetry.jsonl"
    checkpoint = _checkpoint_payload(out)
    before = telemetry_path.read_bytes()
    tail = _read_jsonl(telemetry_path)[0]
    tail["row_index"] = 999
    tail["round_index"] = 999
    tail["operation_id"] = "uncommitted-tail"
    with telemetry_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(tail, ensure_ascii=False) + "\n")

    assert _run(monkeypatch, out, resume=True, limit=3) == 0
    assert telemetry_path.read_bytes() == before
    manifest = json.loads((run_dir / "manifest.json").read_text())
    report = manifest["resume_reconciliation"]
    assert report["telemetry_bytes_truncated"] > 0
    assert report["telemetry_records_dropped"] == 1
    assert report["telemetry_rows_dropped"] == [999]
    assert _checkpoint_payload(out)["telemetry_committed_bytes"] == (
        checkpoint["telemetry_committed_bytes"])


def test_resume_discards_torn_uncommitted_telemetry_after_prefix(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "torn-tail")
    telemetry_path = run_dir / "train_telemetry.jsonl"
    before = telemetry_path.read_bytes()
    with telemetry_path.open("ab") as handle:
        handle.write(b'{"schema_version":"train-telemetry-v3",')

    assert _run(monkeypatch, out, resume=True, limit=3) == 0
    assert telemetry_path.read_bytes() == before
    manifest = json.loads((run_dir / "manifest.json").read_text())
    report = manifest["resume_reconciliation"]
    assert report["telemetry_bytes_truncated"] > 0
    assert report["torn_lines_dropped"] == 1


def test_clean_resume_with_exact_committed_telemetry_is_untouched(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "clean-resume")
    telemetry_path = run_dir / "train_telemetry.jsonl"
    before = telemetry_path.read_bytes()

    assert _run(monkeypatch, out, resume=True, limit=3) == 0

    after = telemetry_path.read_bytes()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert after == before
    assert manifest["resume_reconciliation"]["clean"] is True


def test_resume_after_all_rows_completed_adds_no_new_telemetry_records(
    monkeypatch, tmp_path,
) -> None:
    out, run_dir = _complete_small_collection(monkeypatch, tmp_path, "all-done")
    before = _identities(_read_jsonl(run_dir / "train_telemetry.jsonl"))

    assert _run(monkeypatch, out, resume=True, limit=3) == 0

    after = _identities(_read_jsonl(run_dir / "train_telemetry.jsonl"))
    assert after == before


def test_zero_action_committed_row_can_reuse_the_previous_telemetry_prefix(
    tmp_path,
) -> None:
    """A completed row need not imply a per-row telemetry record."""
    from cover_kbc.controller_calibration.checkpoint import (
        CollectionCheckpoint,
        RunIdentity,
        TelemetryCommitBoundary,
        resume_from,
    )
    from cover_kbc.controller_calibration.recovery import reconcile_to_checkpoint

    identity = RunIdentity(
        train_sha256="a", repo_sha="b", config_sha256="c",
        enumerator_model_id="e", enumerator_revision="1",
        verifier_model_id="v", verifier_revision="2",
        collection_policy_version="collect-v1",
        telemetry_schema_version="train-telemetry-v3", total_rows=1)
    out = tmp_path / "zero-action"
    run_dir = out / "run"
    run_dir.mkdir(parents=True)
    telemetry_path = run_dir / "train_telemetry.jsonl"
    telemetry_path.write_text("", encoding="utf-8")
    predictions_path = run_dir / "predictions.jsonl"
    predictions_path.write_text(
        json.dumps({"SubjectEntity": "s", "Relation": "r",
                    "ObjectEntities": []}) + "\n",
        encoding="utf-8",
    )
    checkpoint_path = out / "checkpoint.json"
    CollectionCheckpoint(
        identity, [0], [], {"rows_completed": 1, "run_id": "run"},
        telemetry_committed=TelemetryCommitBoundary.empty(),
    ).save(checkpoint_path)

    restored = resume_from(checkpoint_path, identity)
    report = reconcile_to_checkpoint(
        telemetry_path=telemetry_path,
        predictions_path=predictions_path,
        committed_rows=restored.completed_rows,
        committed_queries={0: ("s", "r")},
        telemetry_boundary=restored.telemetry_committed,
    )
    assert report.clean


def test_old_checkpoint_format_is_refused_without_guessing_telemetry_prefix(
    tmp_path,
) -> None:
    from cover_kbc.controller_calibration.checkpoint import (
        CHECKPOINT_VERSION,
        CollectionCheckpoint,
        ResumeRefused,
        RunIdentity,
        resume_from,
    )

    identity = RunIdentity(
        train_sha256="a", repo_sha="b", config_sha256="c",
        enumerator_model_id="e", enumerator_revision="1",
        verifier_model_id="v", verifier_revision="2",
        collection_policy_version="collect-v1",
        telemetry_schema_version="train-telemetry-v3", total_rows=1)

    old = tmp_path / "old.json"
    payload = CollectionCheckpoint(
        identity, [], [], {"rows_completed": 0, "run_id": "run"}
    ).to_json()
    payload["checkpoint_version"] = "collection-checkpoint-v2"
    old.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ResumeRefused, match="checkpoint format"):
        resume_from(old, identity)

    missing = tmp_path / "missing-prefix.json"
    payload["checkpoint_version"] = CHECKPOINT_VERSION
    payload.pop("telemetry_committed_bytes", None)
    missing.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ResumeRefused, match="missing committed telemetry"):
        resume_from(missing, identity)


def test_final_gate_refuses_telemetry_that_exceeds_checkpoint_prefix(
    monkeypatch, tmp_path,
) -> None:
    real_capture = runner.capture_telemetry_commit_boundary
    state = {"last": None, "calls": 0}

    def stale_after_first_row(path):
        boundary = real_capture(path)
        state["calls"] += 1
        if state["last"] is None:
            state["last"] = boundary
            return boundary
        return state["last"]

    monkeypatch.setattr(runner, "capture_telemetry_commit_boundary",
                        stale_after_first_row)
    out = tmp_path / "final-gate"

    assert _run(monkeypatch, out, limit=3) != 0
    manifest = json.loads((_run_dirs(out)[0] / "manifest.json").read_text())
    assert any("checkpoint commit boundary" in blocker
               for blocker in manifest["gate_blockers"])
