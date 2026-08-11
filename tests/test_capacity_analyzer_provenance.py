"""Audit 0080 phase B: source provenance is git_revision, never a version string.

The first analysis of the real capacity artifact refused a perfectly valid run,
because the analyzer compared the expected commit SHA against
``manifest["cover_kbc_version"]`` — the *package* version, which is ``0.1.0``
for every commit this project has ever made. That comparison cannot succeed, so
the guard rejected 100% of valid runs while looking like a working check.

`RunManifest` carries both fields deliberately and they answer different
questions. These tests pin which one means "what source produced this run".
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

RUN_SHA = "a7dfb5d68a55b83fab267a0b5e490972df10a275"
PACKAGE_VERSION = "0.1.0"


def _analyzer():
    path = REPO_ROOT / "scripts" / "analyze_capacity_diagnostic.py"
    spec = importlib.util.spec_from_file_location("capacity_analyzer_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


analyzer = _analyzer()


def _run_dir(tmp_path: Path, *, git_revision: str = RUN_SHA,
             cover_kbc_version: str = PACKAGE_VERSION,
             rows: int = 100, relation: str = "hasCapacity",
             errors: list | None = None,
             accounting: dict | None = None) -> Path:
    """A minimal but structurally faithful capacity run directory."""
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)

    manifest = {"cover_kbc_version": cover_kbc_version, "run_id": "probe"}
    if git_revision is not None:
        manifest["git_revision"] = git_revision
    (run / "manifest.json").write_text(json.dumps(manifest))

    with (run / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(rows):
            handle.write(json.dumps({
                "SubjectEntity": f"Venue {index}",
                "Relation": relation,
                "ObjectEntities": ["1000"],
            }) + "\n")

    if errors:
        (run / "errors.json").write_text(json.dumps(errors))

    (run / "run_accounting.json").write_text(json.dumps(accounting or {
        "total_queries": rows, "prediction_rows": rows,
        "successful_queries": rows, "failed_queries": 0,
        "unresolved_invariant_errors": 0, "pipeline_error_rows": 0,
    }))
    return run


# --------------------------------------------------------------------------
# The fix
# --------------------------------------------------------------------------


def test_a_matching_git_revision_passes(tmp_path):
    record = analyzer.check_provenance(_run_dir(tmp_path), RUN_SHA, strict=False)
    assert record["valid"], record["problems"]
    assert record["manifest_git_revision"] == RUN_SHA


def test_a_mismatched_git_revision_fails(tmp_path):
    run = _run_dir(tmp_path, git_revision="0" * 40)
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert not record["valid"]
    assert any("git_revision" in problem for problem in record["problems"])


def test_the_package_version_is_never_read_as_a_source_sha(tmp_path):
    """The exact bug: a valid run must not be refused because 0.1.0 != a SHA."""
    run = _run_dir(tmp_path, git_revision=RUN_SHA, cover_kbc_version=PACKAGE_VERSION)
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert record["valid"], record["problems"]
    assert record["manifest_package_version"] == PACKAGE_VERSION
    assert record["manifest_git_revision"] != record["manifest_package_version"]


def test_a_changed_package_version_does_not_affect_provenance(tmp_path):
    """Bumping the package version must not invalidate a correct run."""
    run = _run_dir(tmp_path, git_revision=RUN_SHA, cover_kbc_version="9.9.9")
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert record["valid"], record["problems"]


def test_a_package_version_cannot_masquerade_as_the_expected_sha(tmp_path):
    """Even if someone puts a SHA in the version field, it is not provenance."""
    run = _run_dir(tmp_path, git_revision="0" * 40, cover_kbc_version=RUN_SHA)
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert not record["valid"], "the version field was accepted as source provenance"


def test_a_missing_git_revision_is_refused(tmp_path):
    run = _run_dir(tmp_path, git_revision=None)
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert not record["valid"]
    assert any("no git_revision" in problem for problem in record["problems"])


def test_a_short_expected_sha_still_matches(tmp_path):
    """Callers may pass an abbreviated SHA; the prefix comparison must hold."""
    record = analyzer.check_provenance(_run_dir(tmp_path), RUN_SHA[:12], strict=False)
    assert record["valid"], record["problems"]


def test_no_expected_sha_skips_the_comparison_but_still_records_it(tmp_path):
    record = analyzer.check_provenance(_run_dir(tmp_path), None, strict=False)
    assert record["valid"], record["problems"]
    assert record["manifest_git_revision"] == RUN_SHA


# --------------------------------------------------------------------------
# The other provenance gates still bite
# --------------------------------------------------------------------------


def test_a_wrong_row_count_is_refused(tmp_path):
    record = analyzer.check_provenance(_run_dir(tmp_path, rows=99), RUN_SHA, strict=False)
    assert not record["valid"]
    assert any("prediction rows" in problem for problem in record["problems"])


def test_a_foreign_relation_is_refused(tmp_path):
    run = _run_dir(tmp_path, relation="hasArea")
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert not record["valid"]
    assert any("expected only hasCapacity" in problem for problem in record["problems"])


def test_a_contaminated_run_is_refused(tmp_path):
    run = _run_dir(tmp_path, errors=[
        {"SubjectEntity": "Venue 0", "Relation": "hasCapacity",
         "error": "PendingActionNotConsumed: ..."}])
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert not record["valid"]
    assert any("query error" in problem for problem in record["problems"])


def test_failed_queries_in_accounting_are_refused(tmp_path):
    run = _run_dir(tmp_path, accounting={
        "total_queries": 100, "prediction_rows": 100, "successful_queries": 99,
        "failed_queries": 1, "unresolved_invariant_errors": 0,
        "pipeline_error_rows": 1})
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert not record["valid"]
    assert any("failed_queries" in problem for problem in record["problems"])


def test_missing_run_accounting_is_refused(tmp_path):
    run = _run_dir(tmp_path)
    (run / "run_accounting.json").unlink()
    record = analyzer.check_provenance(run, RUN_SHA, strict=False)
    assert not record["valid"]
    assert any("run_accounting.json missing" in problem for problem in record["problems"])


# --------------------------------------------------------------------------
# The manifest really does carry both fields
# --------------------------------------------------------------------------


def test_run_manifest_declares_both_fields_and_they_differ():
    from cover_kbc.runtime.manifest import RunManifest

    fields = RunManifest.__dataclass_fields__
    assert "git_revision" in fields
    assert "cover_kbc_version" in fields


def test_package_version_is_not_a_git_sha():
    """Why the buggy comparison could never pass."""
    import re

    from cover_kbc import __version__

    assert not re.fullmatch(r"[0-9a-f]{40}", __version__), (
        "the package version looks like a SHA; the two fields would be "
        "indistinguishable and the provenance check meaningless")


def test_the_analyzer_never_reads_the_version_field_as_provenance():
    """Guard the fix at the source level, so a refactor cannot reintroduce it."""
    source = (REPO_ROOT / "scripts" / "analyze_capacity_diagnostic.py").read_text(
        encoding="utf-8")
    assert 'observed_sha = str(manifest.get("git_revision")' in source
    assert 'observed_sha = str(manifest.get("cover_kbc_version")' not in source


def test_no_temporary_hotfix_copy_was_committed():
    """The Colab-side one-line copy must not enter the repository."""
    stray = list((REPO_ROOT / "scripts").glob("*hotfix*"))
    assert not stray, f"temporary analysis copies in scripts/: {stray}"
