"""Audit 0078: recovery manifest, merge invariants, submission readiness.

The property every test here defends is the same one: the tooling decides
*which* rows to replace and never *what* to put in them.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from cover_kbc.run_accounting import (
    INVARIANT_ERROR_NAMES,
    NOT_READY,
    READY,
    run_accounting,
    submission_verdict,
)
from cover_kbc.types import EmptyReason

REPO_ROOT = Path(__file__).resolve().parents[1]
STOCK = "companyTradesAtStockExchange"


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


merge_tool = _load_script("merge_test_recovery.py")


# --------------------------------------------------------------------------
# Fixtures: a miniature failed run
# --------------------------------------------------------------------------


def _canonical(n: int = 5) -> list[dict]:
    return [{"SubjectEntity": f"S{i}", "Relation": STOCK, "ObjectEntities": []}
            for i in range(n)]


def _base(failed: set[int], n: int = 5) -> list[dict]:
    return [
        {"SubjectEntity": f"S{i}", "Relation": STOCK,
         "ObjectEntities": [] if i in failed else [f"Exchange {i}"]}
        for i in range(n)
    ]


def _errors(failed: set[int]) -> list[dict]:
    return [
        {"SubjectEntity": f"S{i}", "Relation": STOCK,
         "error": f"PendingActionNotConsumed: S{i}/{STOCK}: the controller selected "
                  "RUN_FACET needing the enumerator role and 1 calls remain"}
        for i in sorted(failed)
    ]


def _recovered(failed: set[int], values=None) -> list[dict]:
    values = values or {}
    return [
        {"SubjectEntity": f"S{i}", "Relation": STOCK,
         "ObjectEntities": values.get(i, [f"Recovered Exchange {i}"])}
        for i in sorted(failed)
    ]


# --------------------------------------------------------------------------
# Merge invariants
# --------------------------------------------------------------------------


def test_merge_replaces_only_failed_rows():
    failed = {1, 3}
    merged, provenance = merge_tool.merge(
        _base(failed), _errors(failed), _recovered(failed), _canonical())
    assert provenance["rows_replaced"] == 2
    assert provenance["rows_preserved"] == 3
    assert merged[1]["ObjectEntities"] == ["Recovered Exchange 1"]
    assert merged[3]["ObjectEntities"] == ["Recovered Exchange 3"]


def test_merge_preserves_every_non_failed_row_verbatim():
    failed = {2}
    base = _base(failed)
    merged, _ = merge_tool.merge(base, _errors(failed), _recovered(failed), _canonical())
    for index, row in enumerate(merged):
        if index in failed:
            continue
        assert row == {k: base[index][k] for k in merge_tool.OFFICIAL_FIELDS}


def test_merge_output_is_canonical_order_and_row_count():
    failed = {0, 4}
    canonical = _canonical()
    merged, provenance = merge_tool.merge(
        _base(failed), _errors(failed), _recovered(failed), canonical)
    assert len(merged) == len(canonical) == provenance["merged_rows"]
    assert [(r["SubjectEntity"], r["Relation"]) for r in merged] == [
        (r["SubjectEntity"], r["Relation"]) for r in canonical]


def test_merge_output_carries_exactly_the_official_schema():
    failed = {1}
    merged, _ = merge_tool.merge(
        _base(failed), _errors(failed), _recovered(failed), _canonical())
    for row in merged:
        assert set(row) == set(merge_tool.OFFICIAL_FIELDS)


def test_merge_rejects_a_missing_recovered_identity():
    failed = {1, 2}
    with pytest.raises(merge_tool.MergeError, match="not recovered"):
        merge_tool.merge(_base(failed), _errors(failed), _recovered({1}), _canonical())


def test_merge_rejects_an_extra_recovered_identity():
    failed = {1}
    with pytest.raises(merge_tool.MergeError, match="did not fail"):
        merge_tool.merge(_base(failed), _errors(failed), _recovered({1, 2}), _canonical())


def test_merge_rejects_a_duplicate_recovered_identity():
    failed = {1}
    recovered = _recovered(failed) * 2
    with pytest.raises(merge_tool.MergeError, match="duplicate identity"):
        merge_tool.merge(_base(failed), _errors(failed), recovered, _canonical())


def test_merge_rejects_a_base_that_is_not_the_canonical_split():
    failed = {1}
    with pytest.raises(merge_tool.MergeError, match="canonical split has"):
        merge_tool.merge(
            _base(failed)[:4], _errors(failed), _recovered(failed), _canonical())


def test_merge_refuses_to_overwrite_a_non_empty_base_row():
    """A failed row must be a hole. Overwriting an answer is out of scope."""
    failed = {1}
    base = _base(failed)
    base[1]["ObjectEntities"] = ["An Existing Answer"]
    with pytest.raises(merge_tool.MergeError, match="refusing to overwrite an answer"):
        merge_tool.merge(base, _errors(failed), _recovered(failed), _canonical())


def test_merge_rejects_an_error_identity_outside_the_canonical_split():
    failed = {1}
    errors = _errors(failed) + [
        {"SubjectEntity": "Not In Split", "Relation": STOCK, "error": "X: y"}]
    with pytest.raises(merge_tool.MergeError, match="absent from the canonical split"):
        merge_tool.merge(_base(failed), errors, _recovered(failed), _canonical())


def test_merge_records_a_still_empty_recovery_honestly():
    """Recovery may legitimately still produce nothing; it must be reported."""
    failed = {1}
    merged, provenance = merge_tool.merge(
        _base(failed), _errors(failed), _recovered(failed, {1: []}), _canonical())
    assert merged[1]["ObjectEntities"] == []
    assert provenance["recovered_still_empty"] == 1
    assert provenance["recovered_now_non_empty"] == 0


def test_merge_provenance_states_the_answer_source():
    failed = {1}
    _, provenance = merge_tool.merge(
        _base(failed), _errors(failed), _recovered(failed), _canonical())
    statement = provenance["answer_provenance"]
    assert "COVER-KBC inference" in statement
    assert "verbatim" in statement


def test_merge_copies_values_and_never_authors_them():
    """Every emitted value must be traceable to an input file."""
    failed = {1, 3}
    recovered = _recovered(failed, {1: ["Alpha Exchange"], 3: ["Beta Exchange", "Gamma"]})
    base = _base(failed)
    merged, _ = merge_tool.merge(base, _errors(failed), recovered, _canonical())
    allowed = {tuple(r["ObjectEntities"]) for r in recovered}
    allowed |= {tuple(r["ObjectEntities"]) for r in base}
    for row in merged:
        assert tuple(row["ObjectEntities"]) in allowed


# --------------------------------------------------------------------------
# The real incident artifacts
# --------------------------------------------------------------------------


INCIDENT = REPO_ROOT / "outputs" / "v3_test_16f60fb1_20260810T160048Z" / "run"


@pytest.mark.skipif(not (INCIDENT / "errors.json").exists(),
                    reason="incident artifacts not present in this checkout")
def test_recovery_manifest_matches_the_real_failed_identity_set():
    builder = _load_script("build_test_recovery_manifest.py")
    errors = json.loads((INCIDENT / "errors.json").read_text())
    canonical = builder.read_jsonl(REPO_ROOT / "benchmark" / "data" / "test.jsonl")
    canonical_ids = {(r["SubjectEntity"], r["Relation"]) for r in canonical}
    failed = {(e["SubjectEntity"], e["Relation"]) for e in errors}
    assert len(failed) == 39
    assert failed <= canonical_ids
    assert {relation for _, relation in failed} == {STOCK}


@pytest.mark.skipif(not (INCIDENT / "errors.json").exists(),
                    reason="incident artifacts not present in this checkout")
def test_merge_over_the_real_base_replaces_exactly_thirty_nine_rows():
    base = merge_tool.read_jsonl(INCIDENT / "predictions.jsonl")
    errors = json.loads((INCIDENT / "errors.json").read_text())
    canonical = merge_tool.read_jsonl(REPO_ROOT / "benchmark" / "data" / "test.jsonl")
    # A synthetic recovery: identities only, values are placeholders supplied by
    # this test, never by the tool.
    recovered = [
        {"SubjectEntity": e["SubjectEntity"], "Relation": e["Relation"],
         "ObjectEntities": ["Placeholder Exchange"]}
        for e in errors
    ]
    merged, provenance = merge_tool.merge(base, errors, recovered, canonical)
    assert provenance["merged_rows"] == 475
    assert provenance["rows_replaced"] == 39
    assert provenance["rows_preserved"] == 436
    base_by_id = {(r["SubjectEntity"], r["Relation"]): r for r in base}
    failed = {(e["SubjectEntity"], e["Relation"]) for e in errors}
    for row in merged:
        key = (row["SubjectEntity"], row["Relation"])
        if key not in failed:
            assert row["ObjectEntities"] == base_by_id[key]["ObjectEntities"]


@pytest.mark.skipif(not (INCIDENT / "predictions.jsonl").exists(),
                    reason="incident artifacts not present in this checkout")
def test_the_merge_refuses_to_overwrite_the_run_it_reads(tmp_path):
    """The failed run is evidence: it may be an input, never an output.

    Asserted on the guard's behaviour rather than on whether the path appears in
    a docstring - it appears there precisely because it is the documented
    *input*.
    """
    import subprocess

    base = tmp_path / "predictions.jsonl"
    base.write_text(json.dumps(
        {"SubjectEntity": "S0", "Relation": STOCK, "ObjectEntities": []}) + "\n")
    errors = tmp_path / "errors.json"
    errors.write_text(json.dumps(_errors({0})))
    canonical = tmp_path / "canonical.jsonl"
    canonical.write_text(json.dumps(
        {"SubjectEntity": "S0", "Relation": STOCK, "ObjectEntities": []}) + "\n")

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "merge_test_recovery.py"),
         "--base-predictions", str(base), "--base-errors", str(errors),
         "--recovered-predictions", str(base), "--canonical-test", str(canonical),
         "--output", str(base)],
        capture_output=True, text=True, check=False)
    assert completed.returncode != 0
    assert "immutable evidence" in (completed.stderr + completed.stdout)


def test_no_recovery_tool_writes_into_a_source_run_directory():
    """Every write in the tools goes to an explicit --output path."""
    for name in ("merge_test_recovery.py", "build_test_recovery_manifest.py",
                 "analyze_pending_action_incident.py"):
        source = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
        # The only write targets are args.output / args.output_dir.
        writes = [
            line for line in source.splitlines()
            if (".write_text(" in line or ".open(\"w\"" in line)
        ]
        for line in writes:
            assert "run_dir" not in line, f"{name} writes into a source run: {line}"


# --------------------------------------------------------------------------
# Submission readiness
# --------------------------------------------------------------------------


class _Prediction:
    def __init__(self, failed: bool) -> None:
        self.empty_reason = (
            EmptyReason.PIPELINE_ERROR if failed else EmptyReason.NOT_EMPTY)


class _Result:
    def __init__(self, errors, predictions) -> None:
        self.errors = errors
        self.predictions = predictions


def test_a_run_with_unresolved_invariant_errors_is_not_submission_ready():
    errors = _errors({1, 2, 3})
    result = _Result(errors, [_Prediction(True)] * 3 + [_Prediction(False)] * 2)
    accounting = run_accounting(list(range(5)), result)
    assert accounting["unresolved_invariant_errors"] == 3
    verdict = submission_verdict("test", accounting)
    assert verdict["state"] == NOT_READY
    assert any("PendingActionNotConsumed" in blocker for blocker in verdict["blockers"])


def test_a_clean_run_is_submission_ready():
    result = _Result([], [_Prediction(False)] * 5)
    accounting = run_accounting(list(range(5)), result)
    assert accounting["failed_queries"] == 0
    assert submission_verdict("test", accounting)["state"] == READY


def test_a_full_row_count_alone_does_not_make_a_run_ready():
    """The exact failure mode of the incident: 475 rows, 39 holes, exit 0."""
    errors = _errors(set(range(39)))
    result = _Result(errors, [_Prediction(True)] * 39 + [_Prediction(False)] * 436)
    accounting = run_accounting(list(range(475)), result)
    assert accounting["prediction_rows"] == accounting["total_queries"] == 475
    assert submission_verdict("test", accounting)["state"] == NOT_READY


def test_pending_action_not_consumed_is_classified_as_an_invariant_error():
    assert "PendingActionNotConsumed" in INVARIANT_ERROR_NAMES


def test_accounting_reports_every_required_field():
    result = _Result([], [_Prediction(False)])
    accounting = run_accounting([object()], result)
    for field in ("total_queries", "successful_queries", "failed_queries",
                  "query_errors", "unresolved_invariant_errors", "prediction_rows"):
        assert field in accounting


def test_a_mismatch_between_errors_and_fallback_rows_is_reported():
    """A fallback row without a recorded error, or the reverse, is a blocker."""
    result = _Result(_errors({1}), [_Prediction(True)] * 2)
    accounting = run_accounting(list(range(2)), result)
    assert accounting["error_rows_match_pipeline_error_rows"] is False
    assert any("PIPELINE_ERROR" in b for b in submission_verdict("test", accounting)["blockers"])


def test_collection_style_runs_are_not_broken_by_the_gate():
    """Only blind submission splits are refused; TRAIN keeps its behaviour.

    The verdict is still computed and reported for every split - what differs is
    that the runner only turns it into a non-zero exit for a blind split.
    """
    errors = _errors({1})
    result = _Result(errors, [_Prediction(True)] + [_Prediction(False)] * 4)
    accounting = run_accounting(list(range(5)), result)
    assert submission_verdict("train", accounting)["state"] == NOT_READY
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text(encoding="utf-8")
    assert "if split in BLIND_SPLITS:" in source


def test_no_gold_or_world_knowledge_path_exists_in_the_recovery_tools():
    """The hard requirement: recovery cannot import an answer from anywhere."""
    banned = ("requests", "urllib", "httpx", "wikipedia", "wikidata", "openai")
    for name in ("merge_test_recovery.py", "build_test_recovery_manifest.py"):
        source = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
        for token in banned:
            assert f"import {token}" not in source
        # No literal exchange or company names may be embedded.
        assert "Stock Exchange" not in source
        assert "Nasdaq" not in source
        assert "NYSE" not in source
