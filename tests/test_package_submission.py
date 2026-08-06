"""The packager's job is to refuse a submission that would score wrongly.

Each failure below is silent at scoring time - a dropped row scores as an empty
prediction, a reordered file scores against the wrong subject - so the tests are
written from the mistake, not from the code path.
"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "package_submission",
    Path(__file__).resolve().parents[1] / "scripts" / "package_submission.py",
)
package_submission = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(package_submission)

SubmissionError = package_submission.SubmissionError
validate = package_submission.validate


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def split(tmp_path: Path) -> Path:
    return _write(tmp_path / "val.jsonl", [
        {"SubjectEntity": "France", "Relation": "countryLandBordersCountry",
         "ObjectEntities": ["Spain"]},
        {"SubjectEntity": "Wembley", "Relation": "hasCapacity",
         "ObjectEntities": ["90000"]},
    ])


@pytest.fixture
def good(tmp_path: Path) -> Path:
    return _write(tmp_path / "predictions.jsonl", [
        {"SubjectEntity": "France", "Relation": "countryLandBordersCountry",
         "ObjectEntities": ["Spain", "Belgium"]},
        {"SubjectEntity": "Wembley", "Relation": "hasCapacity",
         "ObjectEntities": []},
    ])


def test_accepts_a_well_formed_submission(good: Path, split: Path) -> None:
    summary = validate(good, split)
    assert summary["rows"] == 2
    assert summary["empty_rows"] == 1
    assert summary["relations"] == {
        "countryLandBordersCountry": 1, "hasCapacity": 1}


def test_gold_objects_in_the_input_are_never_required_to_match(
        good: Path, split: Path) -> None:
    """Validation reads identity columns only; it must not compare answers."""
    summary = validate(good, split)
    assert summary["rows_with_objects"] == 1


def test_refuses_a_missing_row(good: Path, split: Path, tmp_path: Path) -> None:
    rows = [json.loads(line) for line in good.read_text().splitlines()]
    short = _write(tmp_path / "short.jsonl", rows[:1])
    with pytest.raises(SubmissionError, match="row count mismatch"):
        validate(short, split)


def test_refuses_reordered_rows(good: Path, split: Path, tmp_path: Path) -> None:
    rows = [json.loads(line) for line in good.read_text().splitlines()]
    flipped = _write(tmp_path / "flipped.jsonl", list(reversed(rows)))
    with pytest.raises(SubmissionError, match="must keep the input's order"):
        validate(flipped, split)


def test_refuses_a_diagnostic_field(good: Path, split: Path, tmp_path: Path) -> None:
    rows = [{**json.loads(line), "R_t": 0.4} for line in good.read_text().splitlines()]
    noisy = _write(tmp_path / "noisy.jsonl", rows)
    with pytest.raises(SubmissionError, match="non-official field"):
        validate(noisy, split)


def test_refuses_duplicate_objects(good: Path, split: Path, tmp_path: Path) -> None:
    rows = [json.loads(line) for line in good.read_text().splitlines()]
    rows[0]["ObjectEntities"] = ["Spain", "Spain"]
    repeated = _write(tmp_path / "dup.jsonl", rows)
    with pytest.raises(SubmissionError, match="repeats"):
        validate(repeated, split)


def test_refuses_a_non_list_object_field(good: Path, split: Path, tmp_path: Path) -> None:
    rows = [json.loads(line) for line in good.read_text().splitlines()]
    rows[0]["ObjectEntities"] = "Spain"
    scalar = _write(tmp_path / "scalar.jsonl", rows)
    with pytest.raises(SubmissionError, match="must be a list"):
        validate(scalar, split)


def test_refuses_the_test_split(good: Path, tmp_path: Path) -> None:
    blind = _write(tmp_path / "test.jsonl", [
        {"SubjectEntity": "France", "Relation": "countryLandBordersCountry"}])
    with pytest.raises(SubmissionError, match="TEST is out of scope"):
        validate(good, blind)


def test_archive_holds_exactly_the_official_member(
        good: Path, split: Path, tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "submission.zip"
    monkeypatch.setattr("sys.argv", [
        "package_submission.py", "--predictions", str(good),
        "--input", str(split), "--out", str(archive)])
    assert package_submission.main() == 0
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == ["predictions.jsonl"]
    manifest = json.loads(archive.with_suffix(".manifest.json").read_text())
    assert manifest["archive_members"] == ["predictions.jsonl"]
    assert manifest["rows"] == 2
