"""The real-weight smoke harness itself, checked without loading weights.

The harness is the thing that will decide whether the architecture survives
contact with real models, so its own contracts are worth pinning: it must read
no benchmark data, compute no accuracy, derive Module 17's expected cost rather
than hard-code it, keep Module 20/21 disabled, and fail loudly instead of
masking an exception.

Nothing here loads a model.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HARNESS = Path("scripts/real_model_smoke.py")
NOTEBOOK = Path("notebooks/COVER_KBC_PostArchitecture_RealModel_Smoke.ipynb")


@pytest.fixture(scope="module")
def harness():
    sys.path.insert(0, str(Path("src").resolve()))
    spec = importlib.util.spec_from_file_location("real_model_smoke", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_harness_reads_no_benchmark_data(harness):
    """No split loader, no gold, no benchmark path."""
    tree = ast.parse(HARNESS.read_text())
    for node in ast.walk(tree):
        imported = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom)
            else []
        )
        for module in imported:
            assert not module.startswith("cover_kbc.data"), module
    called = {
        getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    for loader in ("load_dataset", "load_jsonl_rows", "load_all_splits",
                   "gold_lookup"):
        assert loader not in called, loader
    source = HARNESS.read_text()
    for forbidden in ("--split", "benchmark/", "ObjectEntities\"]", "f1_score"):
        assert forbidden not in source, forbidden


def test_the_manifest_is_manually_declared_and_covers_four_families(harness):
    assert len(harness.SMOKE_MANIFEST) == 4
    families = {harness.SPECIALIST_FAMILY[r] for r, _ in harness.SMOKE_MANIFEST}
    assert families == {
        "M12_NUMERIC", "M13_LARGE_OPEN_SET", "M14_NULL_TEMPORAL",
        "M15_SMALL_SET"}
    # Every subject is a literal in this file, not a value read from anywhere.
    source = HARNESS.read_text()
    for _, subject in harness.SMOKE_MANIFEST:
        assert f'"{subject}"' in source


def test_the_harness_computes_no_factual_score(harness):
    """Scanned on executable code: the docstring *denies* these words."""
    tree = ast.parse(HARNESS.read_text())
    prose = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                prose.add(doc)
        if isinstance(node, ast.Raise):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    prose.add(inner.value)
    code = "\n".join(
        line for line in HARNESS.read_text().splitlines()
        if not line.strip().startswith("#")
        and not any(line.strip().strip('"') in doc for doc in prose if doc)
    )
    for forbidden in ("def precision", "def recall", "f1_score", "accuracy_score",
                      "leaderboard", "gold_lookup"):
        assert forbidden not in code.casefold(), forbidden
    # And no scoring is claimed in the summary schema.
    assert '"factual_scoring_performed": False' in HARNESS.read_text()


def test_module_17_expected_cost_is_derived_not_hard_coded(harness):
    """Audit 0033 §16A: the number must come from the live configuration."""
    source = HARNESS.read_text()
    assert "m17_call_plan(verifier_config)" in source
    # No literal 8/4 expectation is written down as the answer.
    assert "expected_cold, expected_warm = readings + controls, readings" in source
    for forbidden in ("expected_cold = 8", "expected_warm = 4",
                      "== 8", "== 4"):
        assert forbidden not in source, forbidden


def test_module_20_and_21_stay_disabled(harness):
    source = HARNESS.read_text()
    # No synthetic package is constructed to fake production readiness.
    for forbidden in ("SYNTHETIC_TEST", "HistoricalActionBin",
                      "PlannerCalibration", "RelationBudgetCalibration",
                      "MicroPlanner(", "Layer6Integrator("):
        assert forbidden not in source, forbidden
    # And activation without calibration is asserted to fail loudly.
    assert harness.uncalibrated_activation_fails() == {
        "m20_refuses_without_calibration": True,
        "m21_refuses_without_packages": True,
    }


def test_the_harness_fails_loudly_rather_than_masking(harness):
    assert issubclass(harness.SmokeFailure, RuntimeError)
    source = HARNESS.read_text()
    # The only broad except records the failure and marks the run FAIL.
    assert source.count("except Exception") == 2      # driver + repo_sha fallback
    assert '"result"] = "FAIL"' in source or 'summary["result"] = "FAIL"' in source
    assert "pass  # ignore" not in source


def test_the_harness_uses_the_existing_runtime_and_adds_no_loader(harness):
    source = HARNESS.read_text()
    assert "from cover_kbc.models.registry import build_runtime" in source
    # It must not construct a second loader; `build_pipeline` is the
    # repository's own pipeline helper and is not one.
    for forbidden in ("AutoModelForCausalLM", "from_pretrained(",
                      "transformers.pipeline(", "torch.load("):
        assert forbidden not in source, forbidden


def test_the_summary_schema_carries_the_required_fields(harness):
    """§19's machine-readable result."""
    source = HARNESS.read_text()
    for field in ("repo_sha", "config_sha256", "primitive_generate",
                  "primitive_score_labels", "expected_cold_calls",
                  "observed_cold_calls", "expected_warm_calls",
                  "observed_warm_calls", "specialist_families",
                  "m18_mechanisms_executed", "production_core_calls",
                  "upgraded_shadow_calls", "shadow_only_calls",
                  "m7_budget_unchanged", "production_output_unchanged",
                  "errors"):
        assert field in source, field


def test_the_notebook_is_valid_and_declares_no_split_switch():
    notebook = json.loads(NOTEBOOK.read_text())
    assert notebook["nbformat"] == 4
    code = [c for c in notebook["cells"] if c["cell_type"] == "code"]
    assert len(code) == 2                       # compact, per the brief
    source = "\n".join("".join(c["source"]) for c in notebook["cells"])
    for required in ("REPO_SHA", "REPO_ROOT", "CACHE_ROOT", "OUTPUT_ROOT",
                     "audit_model_budget.py", "real_model_smoke.py",
                     # HEAD is verified against the requested SHA, as a list-form
                     # subprocess call rather than a shell string.
                     '"rev-parse"', "benchmark integrity"):
        assert required in source, required
    for forbidden in ("SPLIT =", '"val"', '"test"', "run_staged.py"):
        assert forbidden not in source, forbidden
