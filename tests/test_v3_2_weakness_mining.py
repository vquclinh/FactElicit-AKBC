"""Audit 0079: the Claude-diagnostic firewall, and the analysis contracts.

The load-bearing test in this file is the firewall. A directory of
language-model guesses now exists inside `outputs/`, and the one thing that must
never happen is for it to become an input to anything that produces, calibrates
or evaluates a prediction.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
QUARANTINE_TOKEN = "claude_diagnostic_only"

#: Everything that produces, calibrates, evaluates or submits a prediction.
PRODUCTION_ENTRY_POINTS = (
    "scripts/run_cover.py",
    "scripts/run_staged.py",
    "scripts/run_train_calibration_collection.py",
    "scripts/derive_v3_calibration.py",
    "scripts/derive_train_calibration.py",
    "scripts/validate_v3_calibration.py",
    "scripts/check_v3_test_readiness.py",
    "scripts/evaluate_local.py",
    "scripts/package_submission.py",
    "scripts/merge_test_recovery.py",
    "scripts/build_test_recovery_manifest.py",
)


# --------------------------------------------------------------------------
# 1. The firewall
# --------------------------------------------------------------------------


def test_no_production_module_references_the_quarantine():
    """Nothing under src/cover_kbc may know the quarantine exists."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src" / "cover_kbc").rglob("*.py")
        if QUARANTINE_TOKEN in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"production source references the quarantine: {offenders}"


@pytest.mark.parametrize("script", PRODUCTION_ENTRY_POINTS)
def test_no_production_entry_point_references_the_quarantine(script):
    path = REPO_ROOT / script
    if not path.exists():
        pytest.skip(f"{script} not present in this checkout")
    assert QUARANTINE_TOKEN not in path.read_text(encoding="utf-8")


def test_no_config_references_the_quarantine():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "configs").rglob("*.yaml")
        if QUARANTINE_TOKEN in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"a config references the quarantine: {offenders}"


def test_no_tracked_script_names_the_quarantine():
    """The old diagnostic patch generator was retired; no script writes there."""
    naming = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "scripts").glob("*.py")
        if QUARANTINE_TOKEN in path.read_text(encoding="utf-8")
    )
    assert naming == [], naming


def test_the_quarantine_is_gitignored():
    """The guesses must never be committable."""
    import subprocess
    target = (REPO_ROOT / "outputs" / "v3_test_16f60fb1_20260810T160048Z"
              / QUARANTINE_TOKEN / "claude_guesses.csv")
    result = subprocess.run(
        ["git", "check-ignore", str(target)], cwd=REPO_ROOT,
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, "the quarantine directory is not gitignored"


# --------------------------------------------------------------------------
# 2. The quarantined artifact itself, when present
# --------------------------------------------------------------------------


QUARANTINE = (REPO_ROOT / "outputs" / "v3_test_16f60fb1_20260810T160048Z"
              / QUARANTINE_TOKEN)
INCIDENT = REPO_ROOT / "outputs" / "v3_test_16f60fb1_20260810T160048Z" / "run"
present = pytest.mark.skipif(
    not (QUARANTINE / "provenance.json").exists(),
    reason="quarantined diagnostic not present in this checkout")


@present
def test_the_readme_leads_with_the_warning():
    text = (QUARANTINE / "README_DO_NOT_SUBMIT.md").read_text(encoding="utf-8")
    head = text[:400].upper()
    for phrase in ("DO NOT SUBMIT", "NOT COVER-KBC OUTPUT",
                   "NOT GROUND TRUTH", "NOT VALID FOR CALIBRATION"):
        assert phrase in head, f"{phrase!r} is not in the opening warning"


@present
def test_provenance_declares_what_it_is_invalid_for():
    provenance = json.loads((QUARANTINE / "provenance.json").read_text())
    assert provenance["knowledge_source"] == "claude-internal-parametric-memory-only"
    assert provenance["external_sources_used"] == []
    for banned in ("submission", "ground truth", "calibration", "benchmark evaluation"):
        assert banned in provenance["invalid_for"]
    assert provenance["valid_for"] == ["human qualitative inspection"]


@present
def test_the_patch_touches_only_the_failed_rows():
    base = [json.loads(l) for l in
            (INCIDENT / "predictions.jsonl").read_text().splitlines() if l.strip()]
    patched = [json.loads(l) for l in
               (QUARANTINE / "predictions_claude_diagnostic.jsonl").read_text().splitlines()
               if l.strip()]
    errors = json.loads((INCIDENT / "errors.json").read_text())
    failed = {(e["SubjectEntity"], e["Relation"]) for e in errors}
    assert len(patched) == len(base) == 475
    changed = set()
    for before, after in zip(base, patched):
        key = (before["SubjectEntity"], before["Relation"])
        assert key == (after["SubjectEntity"], after["Relation"]), "row order changed"
        if before["ObjectEntities"] != after["ObjectEntities"]:
            changed.add(key)
    assert changed <= failed, "a non-failed row was modified"


@present
def test_the_original_failed_run_is_unmodified():
    digest = hashlib.sha256((INCIDENT / "predictions.jsonl").read_bytes()).hexdigest()
    assert digest == "8a97a5e0696f0f4c77ace3af88725bb90f568ee0587bc07fe0f6e7b75c649f08"


@present
def test_unknown_rows_were_left_empty_rather_than_invented():
    """Honesty check: an UNKNOWN row must not carry a confident-looking answer."""
    import csv
    with (QUARANTINE / "claude_guesses.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["confidence"].strip().upper() == "UNKNOWN":
                assert json.loads(row["claude_diagnostic_ObjectEntities"]) == [], (
                    f"{row['SubjectEntity']}: marked UNKNOWN but carries an answer")


# --------------------------------------------------------------------------
# 3. Analysis scripts: contracts, not conclusions
# --------------------------------------------------------------------------


def _load(name: str):
    path = REPO_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_weakness_analysis_never_labels_a_test_row_correct():
    """Blind TEST may be analysed for structure only."""
    source = (REPO_ROOT / "scripts" / "analyze_v3_2_weaknesses.py").read_text(
        encoding="utf-8")
    structural = source[source.index("def _test_structural"):]
    for token in ("gold", "correct", "accuracy", "f1"):
        assert not re.search(rf"\b{token}\b", structural, re.IGNORECASE), (
            f"the TEST structural analyser references {token!r}")


def test_gold_like_recalled_separates_recall_from_selection():
    """The distinction the whole audit rests on must be computed, not assumed."""
    analysis = _load("analyze_v3_2_weaknesses.py")

    class _Ev:
        RELATION_TYPE = {"hasArea": "numeric", "companyTradesAtStockExchange": "string"}

        @staticmethod
        def normalize_string(value):
            return str(value).strip().casefold()

        @staticmethod
        def try_parse_number(value):
            try:
                return float(str(value).replace(",", ""))
            except ValueError:
                return None

    gold = {"ObjectEntities": [["100"]]}
    assert analysis._gold_like_recalled(_Ev, "hasArea", gold, []) is False
    assert analysis._gold_like_recalled(
        _Ev, "hasArea", gold, [{"numeric_value": 100.0}]) is True
    assert analysis._gold_like_recalled(
        _Ev, "hasArea", gold, [{"numeric_value": 5000.0}]) is False

    gold_entity = {"ObjectEntities": [["Alpha Exchange"]]}
    assert analysis._gold_like_recalled(
        _Ev, "companyTradesAtStockExchange", gold_entity,
        [{"output_value": "Alpha Exchange"}]) is True
    assert analysis._gold_like_recalled(
        _Ev, "companyTradesAtStockExchange", gold_entity,
        [{"output_value": "Beta Exchange"}]) is False


def test_oracle_scenarios_are_labelled_as_upper_bounds():
    mining = REPO_ROOT / "outputs" / "v3_2_weakness_mining" / "oracle_upper_bounds.csv"
    if not mining.exists():
        pytest.skip("mining outputs not present")
    import csv
    with mining.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["scenario"].startswith("ORACLE"):
                assert row["label"] == "TRAIN ORACLE UPPER BOUND ONLY"


def test_no_oracle_logic_reaches_production():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src" / "cover_kbc").rglob("*.py")
        if "ORACLE" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"oracle logic in production: {offenders}"


# --------------------------------------------------------------------------
# 4. Nothing from audits 0076-0078 regressed
# --------------------------------------------------------------------------


CALIBRATION_HASHES = {
    "configs/calibration/v3/m20_relation_budget.json":
        "74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29",
    "configs/calibration/v3/m21_historical_bins.json":
        "ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675",
    "configs/calibration/v3/m21_planner_calibration.json":
        "1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6",
    "configs/calibration/v3/calibration_provenance.json":
        "f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d",
    "configs/calibration/m20_relation_budget.json":
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68",
    "configs/calibration/m21_historical_bins.json":
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071",
    "configs/calibration/m21_planner_calibration.json":
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05",
}


@pytest.mark.parametrize("path,digest", sorted(CALIBRATION_HASHES.items()))
def test_calibration_artifacts_are_byte_identical(path, digest):
    blob = (REPO_ROOT / path).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == digest


def test_this_audit_implemented_no_production_behaviour_change():
    """Audit 0079 is analysis; every measured rule was rejected.

    Asserted so that a later reader can tell the difference between "we found
    nothing worth shipping" and "we forgot to ship it".
    """
    import csv
    plan = REPO_ROOT / "outputs" / "v3_2_weakness_mining" / "cheap_rule_candidates.csv"
    if not plan.exists():
        pytest.skip("plan outputs not present")
    with plan.open(encoding="utf-8") as handle:
        verdicts = {row["verdict"] for row in csv.DictReader(handle)}
    assert "PROMOTE" not in verdicts, (
        "a rule was marked PROMOTE but no production change was made in this audit")
