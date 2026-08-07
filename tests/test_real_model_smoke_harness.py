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
import subprocess
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
    assert 'summary["result"] = "FAIL"' in source
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


def test_the_notebook_is_minimal_and_declares_no_split_switch():
    """Two code cells: setup, then run. No diagnostic sprawl."""
    notebook = json.loads(NOTEBOOK.read_text())
    assert notebook["nbformat"] == 4
    code = [c for c in notebook["cells"] if c["cell_type"] == "code"]
    assert len(code) == 2
    source = "\n".join("".join(c["source"]) for c in notebook["cells"])
    for required in ("REPO_SHA", "REPO_ROOT", "CACHE_ROOT", "OUTPUT_ROOT",
                     "real_model_smoke.py"):
        assert required in source, required
    # No split switch, and none of the preflight sprawl the brief cut.
    for forbidden in ("SPLIT =", '"val"', '"test"', "run_staged.py",
                      "nvidia-smi", "disk_usage", "audit_model_budget"):
        assert forbidden not in source, forbidden


# ==========================================================================
# Corrective pass: model resolution must go through `model_blocks`
#
# The first real-weight run failed before any model loaded, because the harness
# handed the whole experiment YAML to `build_runtime`. The registry's
# fail-closed check caught it; these tests stop it coming back.
# ==========================================================================


def test_the_harness_imports_the_canonical_resolver():
    source = HARNESS.read_text()
    assert "from cover_kbc.models.registry import build_runtime, model_blocks" in source


def test_build_runtime_is_never_handed_the_whole_experiment_config():
    """Every runtime construction receives one resolved model block."""
    tree = ast.parse(HARNESS.read_text())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "build_runtime"
    ]
    assert calls, "the harness builds no runtime"
    for call in calls:
        assert len(call.args) == 1, ast.dump(call)
        argument = call.args[0]
        # A bare name is fine only if it is a resolved block, never `config`.
        if isinstance(argument, ast.Name):
            # `spec` is the resolved block chosen inside StagedRuntimes.load.
            assert argument.id in {
                "enumerator_spec", "verifier_spec", "spec"}, argument.id
        else:
            # No dict literal rebuilding a profile, and no config subscript.
            assert not isinstance(argument, ast.Dict), ast.dump(argument)
            assert not isinstance(argument, ast.Subscript), ast.dump(argument)

    source = HARNESS.read_text()
    for forbidden in ("build_runtime(config)", 'build_runtime(config["model_profile"])',
                      "build_runtime({**config"):
        assert forbidden not in source, forbidden


def test_each_role_is_built_from_its_own_resolved_block():
    """The lifecycle owns both blocks and picks by role."""
    source = HARNESS.read_text()
    assert "enumerator_spec, verifier_spec = model_blocks(config)" in source
    assert "StagedRuntimes(enumerator_spec, verifier_spec)" in source
    assert ("spec = (self.enumerator_spec if role == self.ENUMERATOR\n"
            "                    else self.verifier_spec)") in source
    assert "self.runtime = build_runtime(spec)" in source


def test_the_two_roles_keep_distinct_identities_and_pinned_revisions():
    import yaml

    from cover_kbc.models.registry import model_blocks

    config = yaml.safe_load(
        Path("configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml").read_text())
    enumerator, verifier = model_blocks(config)

    assert enumerator["model_id"] == "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    assert verifier["model_id"] == "Qwen/Qwen3.5-4B"
    assert enumerator["model_id"] != verifier["model_id"]
    assert enumerator["revision"] == "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
    assert verifier["revision"] == "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
    assert enumerator["tokenizer_backend"] == "mistral_common"
    assert verifier["tokenizer_backend"] == "huggingface"
    assert enumerator["quantization"] == verifier["quantization"] == "nf4"
    # Both blocks carry the backend `build_runtime` requires.
    assert enumerator["backend"] == verifier["backend"] == "huggingface"


def test_a_shared_profile_is_not_loaded_twice():
    """One model named for both roles loads once, via `shared_profile`."""
    source = HARNESS.read_text()
    assert "def shared_profile" in source
    assert "return self.enumerator_spec == self.verifier_spec" in source


def test_the_harness_declares_no_second_model_resolver():
    source = HARNESS.read_text()
    for forbidden in ("def model_blocks", "def build_runtime",
                      "def _resolve_model", "def load_model",
                      '["model_profile"]["enumerator"]',
                      '["model_profile"]["verifier"]'):
        assert forbidden not in source, forbidden


def test_the_registry_still_fails_closed_on_an_unresolved_profile():
    """The check that caught this defect must not be weakened."""
    import yaml

    from cover_kbc.models.registry import build_runtime

    config = yaml.safe_load(
        Path("configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml").read_text())
    with pytest.raises(ValueError, match="has no 'backend'"):
        build_runtime(config)
    with pytest.raises(ValueError, match="has no 'backend'"):
        build_runtime(config["model_profile"])
    # And no top-level `backend:` was added to the YAML to work around it.
    assert "backend" not in config
    assert subprocess.run(
        ["git", "status", "--porcelain",
         "src/cover_kbc/models/registry.py",
         "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml"],
        capture_output=True, text=True, check=True).stdout == ""


def test_a_role_mismatch_would_be_caught_at_run_time(harness):
    """One resident runtime may not be reused for the other role."""
    enumerator, verifier = _stub_specs()
    staged = harness.StagedRuntimes(enumerator, verifier)
    staged.load(staged.ENUMERATOR)
    with pytest.raises(harness.SmokeFailure, match="must never be\n *resident|still resident"):
        staged.load(staged.VERIFIER)
    staged.release()


# ==========================================================================
# Memory-safe staged residency
#
# Mistral-Small-24B and Qwen3.5-4B must never sit on the device together: the
# smoke needs both roles, but the repository's own enumerate/verify/decide
# contract needs only one at a time.
# ==========================================================================


def _isolated_code() -> str:
    """`isolated_m18_smoke` with its docstring removed.

    The docstring explains what the function must not touch, so a raw substring
    scan would fire on the very prose that records the prohibition.
    """
    tree = ast.parse(HARNESS.read_text())
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "isolated_m18_smoke")
    body = [n for n in function.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str))]
    return "\n".join(
        ast.get_source_segment(HARNESS.read_text(), n) or "" for n in body)


def _driver_source() -> str:
    """`main()` only — phase ordering is a property of the driver."""
    tree = ast.parse(HARNESS.read_text())
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main")
    return ast.get_source_segment(HARNESS.read_text(), function)


def _code_only() -> str:
    """Harness source with docstrings and comments removed."""
    import io
    import tokenize

    source = HARNESS.read_text()
    tree = ast.parse(source)
    prose = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                prose.add(doc)
    kept = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING:
            try:
                if ast.literal_eval(token.string) in prose:
                    continue
            except (ValueError, SyntaxError):
                pass
        kept.append(token.string)
    return " ".join(kept)


def _stub_specs():
    return ({"backend": "scripted", "model_id": "offline/enumerator"},
            {"backend": "scripted", "model_id": "offline/verifier"})


def test_two_distinct_checkpoints_are_never_resident_together(harness):
    enumerator, verifier = _stub_specs()
    staged = harness.StagedRuntimes(enumerator, verifier)

    staged.load(staged.ENUMERATOR)
    with pytest.raises(harness.SmokeFailure, match="still resident"):
        staged.load(staged.VERIFIER)

    staged.release()
    assert staged.runtime is None and staged.role is None
    staged.load(staged.VERIFIER)
    assert staged.model_id == "offline/verifier"
    staged.release()
    assert staged.history == ["enumerator", "verifier"]


def test_the_enumerator_is_released_before_the_verifier_is_built(harness):
    """Read off the phase order in the source, not just the helper."""
    driver = _driver_source()
    e1 = driver.index("staged.load(StagedRuntimes.ENUMERATOR)")
    release = driver.index("staged.release()", e1)
    v = driver.index("staged.load(StagedRuntimes.VERIFIER)", e1)
    assert e1 < release < v, "the enumerator is not released before the verifier"


def test_each_staged_pass_releases_between_every_role(harness):
    tree = ast.parse(HARNESS.read_text())
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "staged_pass")
    body = ast.dump(function)
    # load ENUMERATOR ... release ... load VERIFIER ... release
    assert body.count("'release'") == 2
    assert "ENUMERATOR" in body and "VERIFIER" in body
    order = [
        m for m in ("ENUMERATOR", "release", "VERIFIER")
        for _ in [0]
    ]
    source = ast.get_source_segment(HARNESS.read_text(), function)
    i_enum = source.index("StagedRuntimes.ENUMERATOR")
    i_rel = source.index("staged.release(pipeline)")
    i_ver = source.index("StagedRuntimes.VERIFIER")
    assert i_enum < i_rel < i_ver, order


def test_qwen_stays_resident_across_the_m17_cold_warm_experiment(harness):
    """Unloading between cold and warm would reset the control cache."""
    driver = _driver_source()
    verifier_load = driver.index("staged.load(StagedRuntimes.VERIFIER)")
    m17 = driver.index('summary["m17_call_plan"] = m17_plan_regression')
    release = driver.index("staged.release()", verifier_load)
    assert verifier_load < m17 < release, (
        "Module 17's cold/warm regression must run while the verifier is still "
        "resident")
    # Both readings happen inside one regression call, so the cache persists.
    assert HARNESS.read_text().count("verifier.verify(request_for(") == 2


def test_state_crosses_phases_through_existing_typed_serialisation(harness):
    source = HARNESS.read_text()
    assert "from cover_kbc.staging import read_stage, write_stage" in source
    # The shadow half round-trips through each module's own to_json/from_json.
    assert "_SHADOW_ARTEFACTS" in source
    for module in ("QueryRiskProfile", "NumericSpecialistResult",
                   "LargeSetSpecialistResult", "NullTemporalSpecialistResult",
                   "SmallSetSpecialistResult"):
        assert module in source, module
    assert "from_json(row)" in source and "item.to_json()" in source
    # No parallel identity system.
    for forbidden in ("uuid", "def _new_id", "candidate_key =", "origin_id ="):
        assert forbidden not in source, forbidden


def test_no_smaller_model_or_quantization_fallback_exists(harness):
    """Scanned on executable code: the comment *forbids* these words."""
    code = _code_only()
    for forbidden in ("load_in_8bit", "int8", 'device_map="cpu"',
                      "torch_dtype=torch.float16", "fallback_model",
                      "def retry", "smaller_model"):
        assert forbidden not in code, forbidden
    # No checkpoint is named in code; both come from the resolved blocks.
    assert "Mistral" not in code and "Qwen" not in code


def test_oom_is_reported_with_its_phase_and_never_hidden(harness):
    source = HARNESS.read_text()
    assert 'error_type" ' not in source           # the key is `type`
    assert '"CUDA_OOM"' in source
    for field in ('"phase": phase', '"active_role"', '"active_model_id"',
                  '"traceback"'):
        assert field in source, field
    assert 'summary["result"] = "FAIL"' in source
    # The only broad handler that swallows anything is the release helper's
    # best-effort attribute clear; the driver's records and re-raises as FAIL.
    tree = ast.parse(source)
    broad = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and getattr(node.type, "id", "") == "Exception"
    ]
    assert len(broad) == 3        # driver + repo_sha fallback + release clear
    swallowing = [n for n in broad if all(isinstance(b, ast.Pass) for b in n.body)]
    assert len(swallowing) == 1   # only the release clear


def test_staged_is_the_default_memory_mode(harness):
    source = HARNESS.read_text()
    assert '"--memory-mode", choices=("staged",), default="staged"' in source
    # The documented default command carries no memory flag.
    assert "--memory-mode" not in HARNESS.read_text().split('"""')[1]


def test_a_shared_profile_still_loads_once(harness):
    enumerator, _ = _stub_specs()
    staged = harness.StagedRuntimes(enumerator, enumerator)
    assert staged.shared_profile is True
    first = staged.load(staged.ENUMERATOR)
    assert staged.load(staged.ENUMERATOR) is first
    assert staged.history == ["enumerator"]
    staged.release()


def test_the_staged_pass_runs_end_to_end_without_weights(harness, tmp_path):
    """The wiring itself, exercised on stub backends."""
    import yaml

    config = yaml.safe_load(
        Path("configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml").read_text())
    for role in ("enumerator", "verifier"):
        config["model_profile"][role] = {
            **config["model_profile"][role], "backend": "scripted"}
    enumerator, verifier = harness.model_blocks(config)
    staged = harness.StagedRuntimes(enumerator, verifier)

    core = harness.staged_pass(
        config, staged, tmp_path, upgraded=False, tag="core")
    assert staged.runtime is None            # released after every pass
    upgraded = harness.staged_pass(
        config, staged, tmp_path, upgraded=True, tag="upgraded")
    assert staged.runtime is None

    comparison = harness.compare_passes(core, upgraded)
    assert comparison["production_output_unchanged"] is True
    assert comparison["m7_budget_unchanged"] is True
    assert comparison["shadow_only_calls"] > 0
    assert comparison["m9_refined"] is True
    # Every relation reached Module 16, Layer 4 and Module 19.
    assert upgraded["upgraded_state"]["consensus"] == len(harness.SMOKE_MANIFEST)
    assert upgraded["upgraded_state"]["layer4"] == len(harness.SMOKE_MANIFEST)
    assert upgraded["upgraded_state"]["coverage_gap"] == len(harness.SMOKE_MANIFEST)
    # Roles alternated and nothing is left resident.
    assert staged.history == ["enumerator", "verifier"] * 2


# ==========================================================================
# LabelScoreRequest contract
#
# The first real Qwen run reached `score_labels` and failed on a malformed
# `labels` argument. The runtime was right to refuse it; the harness was wrong.
# ==========================================================================


def test_the_primitive_smoke_uses_the_canonical_label_mapping():
    source = HARNESS.read_text()
    assert "from cover_kbc.verification.blind import LABEL_TOKENS" in source
    assert "labels = dict(LABEL_TOKENS)" in source
    # Never a flattened sequence, and never hand-retyped labels.
    for forbidden in ("tuple(LABEL_TOKENS", "list(LABEL_TOKENS",
                      'labels=("A", "B", "C")', '("A", "B", "C")',
                      'LABEL_TOKENS[name]'):
        assert forbidden not in source, forbidden


def test_the_harness_declares_no_duplicate_label_mapping():
    """A/B/C exists once, in Module 4. The harness reads it."""
    code = _code_only()
    for forbidden in ('"VALID": "A"', '"INVALID": "B"', '"UNKNOWN": "C"',
                      "LABEL_TOKENS ="):
        assert forbidden not in code, forbidden


def test_the_harness_matches_the_production_call_shape():
    """Same construction as every `LabelScoreRequest` in production."""
    import inspect

    from cover_kbc.verification import blind, specialist_verifier

    production = inspect.getsource(blind) + inspect.getsource(specialist_verifier)
    # The invariant, not a count: every production `labels=` argument is a
    # `dict(...)` of a declared mapping - LABEL_TOKENS, GATE_LABELS or a
    # passthrough - and never a flattened sequence.
    label_arguments = [
        line.split("labels=", 1)[1].strip().rstrip(",")
        for line in production.splitlines() if "labels=" in line
    ]
    assert label_arguments, "no production label argument found"
    assert all(a.startswith("dict(") for a in label_arguments), label_arguments
    assert "dict(LABEL_TOKENS)" in label_arguments
    from cover_kbc.verification.blind import GATE_LABELS, LABEL_TOKENS

    assert isinstance(LABEL_TOKENS, dict) and isinstance(GATE_LABELS, dict)
    assert "labels=dict(LABEL_TOKENS)" not in HARNESS.read_text()  # via a local
    assert "labels=labels" in HARNESS.read_text()
    assert "labels = dict(LABEL_TOKENS)" in HARNESS.read_text()
    # And the audited verifier system prompt is used, as production does.
    assert "system_prompt=VERIFIER_SYSTEM_PROMPT" in HARNESS.read_text()


def test_the_declared_contract_is_a_mapping_and_the_old_shape_fails():
    import dataclasses

    from cover_kbc.models.base import LabelScoreRequest
    from cover_kbc.verification.blind import LABEL_TOKENS

    field = next(
        f for f in dataclasses.fields(LabelScoreRequest) if f.name == "labels")
    assert "dict" in str(field.type)
    assert isinstance(LABEL_TOKENS, dict)

    # The exact failure the real run hit, reproduced from the old shape.
    flattened = tuple(LABEL_TOKENS[n] for n in ("VALID", "INVALID", "UNKNOWN"))
    with pytest.raises(ValueError, match="length 1; 2 is required"):
        dict(flattened)
    # The corrected shape round-trips.
    assert dict(dict(LABEL_TOKENS)) == LABEL_TOKENS


def test_single_token_scoring_is_inspected_never_assumed():
    """A/B/C may or may not be single-token; the runtime decides."""
    source = HARNESS.read_text()
    assert '"label_single_token"' in source
    assert '"scoring_strategy"' in source
    # The harness never asserts single-token, and never picks a strategy.
    for forbidden in ("single_token = True", "assert encoding.single_token",
                      "next_token_logits\"", "sequence_loglikelihood\""):
        assert forbidden not in source, forbidden


def test_the_production_runtime_and_verifier_are_unchanged():
    """The audited scoring contract, asserted over source rather than git status.

    The working-tree check this replaced could not distinguish an intentional
    change from a regression, and reported any edit anywhere in these files -
    including additive accounting - as a failure.
    """
    # `dict(request.labels)` still fails closed on a malformed sequence.
    source = Path("src/cover_kbc/models/huggingface.py").read_text()
    assert "self.inspect_labels(dict(request.labels))" in source
    # One counted call per forward pass, on both scoring strategies.
    assert source.count("self.calls += 1") == 3        # generate + 2 strategies
    # And prompt tokens are charged from the tensor, never re-tokenised.
    assert source.count("self.charge_prompt_tokens(") == 3


# ==========================================================================
# LabelScoreResult postconditions
#
# The first real Qwen read-out returned successfully and the harness then broke
# validating it: `LabelScoreResult.logits` is a mapping keyed by label name, so
# iterating it yields "VALID"/"INVALID"/"UNKNOWN", not numbers.
# ==========================================================================


class _FakeScoringRuntime:
    """Returns a canonical `LabelScoreResult`. No weights, no generation."""

    label_encoding = None

    def __init__(self, logits):
        self.logits = dict(logits)
        self.generate_calls = 0

    def score_labels(self, request):
        from cover_kbc.models.base import LabelScoreResult

        return LabelScoreResult(
            logits=dict(self.logits), model_id="Qwen/Qwen3.5-4B", prompt_tokens=11)

    def generate(self, request):                      # pragma: no cover
        self.generate_calls += 1
        raise AssertionError("score_labels must never fall back to generation")


_VERIFIER_SPEC = {
    "model_id": "Qwen/Qwen3.5-4B", "revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
    "tokenizer_backend": "huggingface",
}
_CANONICAL_LOGITS = {"VALID": 1.25, "INVALID": -0.5, "UNKNOWN": 0.1}


def test_finiteness_checks_read_mapping_values_not_keys(harness):
    source = HARNESS.read_text()
    # The exact shape that broke: iterating the mapping itself.
    for forbidden in ("for v in logits)", "list(result.logits)",
                      "max(logits)", "sum(logits)", "len(logits) !="):
        assert forbidden not in source, forbidden
    assert "logits.items()" in source
    assert "probabilities.values()" in source


def test_a_canonical_logit_mapping_validates(harness):
    result = harness.primitive_score_labels(
        _FakeScoringRuntime(_CANONICAL_LOGITS), _VERIFIER_SPEC)
    assert result["ok"] is True
    assert result["logits"] == _CANONICAL_LOGITS
    assert result["logits_finite"] is True
    assert result["probabilities_normalise"] is True


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_value_fails(harness, bad):
    logits = {**_CANONICAL_LOGITS, "VALID": bad}
    with pytest.raises(harness.SmokeFailure, match="non-finite logits"):
        harness.primitive_score_labels(
            _FakeScoringRuntime(logits), _VERIFIER_SPEC)


def test_label_keys_are_preserved_end_to_end(harness):
    result = harness.primitive_score_labels(
        _FakeScoringRuntime(_CANONICAL_LOGITS), _VERIFIER_SPEC)
    assert set(result["logits"]) == {"VALID", "INVALID", "UNKNOWN"}
    assert set(result["probabilities"]) == {"VALID", "INVALID", "UNKNOWN"}
    # The request mapping keeps name -> continuation, so A/B/C stay traceable.
    assert result["labels"] == {"VALID": "A", "INVALID": "B", "UNKNOWN": "C"}
    # Never collapsed into an anonymous numeric list.
    assert isinstance(result["logits"], dict)
    assert isinstance(result["probabilities"], dict)


def test_a_mismatched_label_set_fails(harness):
    with pytest.raises(harness.SmokeFailure, match="asked for"):
        harness.primitive_score_labels(
            _FakeScoringRuntime({"VALID": 1.0, "NOPE": 0.0, "UNKNOWN": 0.1}),
            _VERIFIER_SPEC)


def test_probabilities_come_from_the_results_own_softmax(harness):
    """No second, numerically different verifier implementation."""
    source = HARNESS.read_text()
    assert "probabilities = result.probabilities()" in source
    for forbidden in ("math.exp(", "def softmax", "exps = ", "/ total for"):
        assert forbidden not in source, forbidden

    from cover_kbc.models.base import LabelScoreResult

    canonical = LabelScoreResult(
        logits=dict(_CANONICAL_LOGITS), model_id="Qwen/Qwen3.5-4B").probabilities()
    observed = harness.primitive_score_labels(
        _FakeScoringRuntime(_CANONICAL_LOGITS), _VERIFIER_SPEC)["probabilities"]
    assert observed == canonical


def test_the_probability_sum_check_is_real(harness):
    result = harness.primitive_score_labels(
        _FakeScoringRuntime(_CANONICAL_LOGITS), _VERIFIER_SPEC)
    assert abs(result["probability_sum"] - 1.0) <= 1e-6
    source = HARNESS.read_text()
    assert "sum(probabilities.values())" in source
    assert "abs(total - 1.0) > 1e-6" in source


def test_scoring_never_falls_back_to_generation(harness):
    runtime = _FakeScoringRuntime(_CANONICAL_LOGITS)
    result = harness.primitive_score_labels(runtime, _VERIFIER_SPEC)
    assert runtime.generate_calls == 0
    assert result["generated_tokens"] == 0
    source = HARNESS.read_text()
    # The primitive verifier smoke calls score_labels and nothing else.
    verifier_fn = source[source.index("def primitive_score_labels"):
                         source.index("# ---", source.index("def primitive_score_labels"))]
    assert "runtime.score_labels(" in verifier_fn
    assert "runtime.generate(" not in verifier_fn


def test_the_logits_contract_is_a_mapping_and_the_old_check_would_break():
    import dataclasses

    from cover_kbc.models.base import LabelScoreResult

    field = next(
        f for f in dataclasses.fields(LabelScoreResult) if f.name == "logits")
    assert "dict[str, float]" in str(field.type)

    # The exact failure the real run hit, reproduced from the old check.
    import math

    with pytest.raises(TypeError, match="must be real number, not str"):
        all(math.isfinite(v) for v in dict(_CANONICAL_LOGITS))


def test_the_production_runtime_and_label_mapping_stay_unchanged():
    """The audited runtime contract, asserted as behaviour rather than as a
    clean working tree.

    This used to assert ``git status --porcelain`` was empty for the runtime
    files, which made every intentional change - including adding the prompt
    token accounting Module 20 is priced in - look like a regression while
    proving nothing about what the runtime does. What actually has to hold is
    the contract below: the label mapping the real-weight smoke validated, and
    one physical call per scoring request with truthful token accounting.
    """
    from cover_kbc.models.base import BaseRuntime, LabelScoreRequest
    from cover_kbc.verification.blind import LABEL_TOKENS

    assert LABEL_TOKENS == {"VALID": "A", "INVALID": "B", "UNKNOWN": "C"}
    assert "labels = dict(LABEL_TOKENS)" in HARNESS.read_text()

    # Accounting is a counter the backend feeds, never an estimate: the base
    # runtime owns the three cumulative counters and nothing re-derives them.
    from cover_kbc.models.offline import NullRuntime

    assert set(BaseRuntime(NullRuntime().spec).usage()) == {
        "calls", "prompt_tokens", "generated_tokens"}
    runtime = NullRuntime()
    runtime.score_labels(LabelScoreRequest(prompt="a b c", labels=dict(LABEL_TOKENS)))
    assert (runtime.calls, runtime.prompt_tokens) == (1, 3)


# ==========================================================================
# Module 18 real-weight coverage
#
# The composed passes catalogue M18 and execute nothing, which is correct:
# scheduling belongs to Module 20/21 and both are disabled. So an *isolated*
# contract smoke supplies the real-weight evidence, and must never be able to
# masquerade as a natural firing.
# ==========================================================================


def test_natural_m18_coverage_is_reported_separately_and_untouched(harness):
    source = HARNESS.read_text()
    assert '"m18_natural_mechanisms_executed"' in source
    assert '"m18_isolated_contract_mechanisms_executed"' in source
    assert '"m18_real_weight_coverage"' in source
    # Natural coverage is read from the composed passes, never assigned a value.
    assert 'natural = summary["composed"]["m18_mechanisms_executed"]' in source
    assert 'summary["m18_natural_mechanisms_executed"] = natural' in source


def test_the_isolated_smoke_runs_only_when_natural_coverage_is_empty(harness):
    driver = _driver_source()
    assert "if natural:" in driver
    branch = driver[driver.index("if natural:"):]
    # The true branch records an empty isolated list; the else branch runs it.
    assert '"m18_isolated_contract_mechanisms_executed"] = []' in branch
    assert "isolated_m18_smoke(config, staged)" in branch
    assert branch.index("= []") < branch.index("isolated_m18_smoke(")


def test_the_isolated_smoke_uses_the_real_production_seam(harness):
    """`catalogue` -> `build_request` -> `execute` on the real verifier."""
    source = HARNESS.read_text()
    assert ("from cover_kbc.verification.bidirectional_verifier import "
            "BidirectionalVerifier") in source
    for call in ("verifier.catalogue(consensus)", "verifier.build_request(check)",
                 "verifier.execute("):
        assert call in source, call
    # No local duplicate of M18, and no bypass of its eligibility guard.
    for forbidden in ("def _reverse_prompt", "def _counterfactual", "monkeypatch",
                      "eligible = True", "check.eligible = "):
        assert forbidden not in source, forbidden
    # It reads the real eligibility flag rather than assuming it.
    assert "if check.eligible" in source


def test_the_isolated_state_is_hand_declared_and_contract_derived():
    """Synthetic consensus, but every derived field comes from production."""
    from cover_kbc.contracts.router import compile_query
    from cover_kbc.evidence.consensus_adapters import (
        applicable_specialist, candidate_kind)
    from cover_kbc.evidence.consensus_types import (
        CandidateConsensusState, QueryConsensusResult)
    from cover_kbc.verification.bidirectional_verifier import BidirectionalVerifier

    relation, subject = "countryLandBordersCountry", "Portugal"
    _, contract = compile_query(subject, relation, 0)
    consensus = QueryConsensusResult(
        consensus_version="m16-v1", relation=relation, subject=subject,
        row_index=0, applicable_specialist=applicable_specialist(relation),
        candidates=(CandidateConsensusState(
            relation=relation, subject=subject, row_index=0,
            candidate_key="spain", display="Spain",
            candidate_kind=candidate_kind(contract)),))

    catalogue = BidirectionalVerifier().catalogue(consensus)
    eligible = [c for c in catalogue if c.eligible]
    assert eligible, "the isolated state must be genuinely eligible"
    # A real mechanism, from the contract's own declarations.
    assert {c.check_kind.value for c in eligible} <= {
        "REVERSE", "KEY_CONDITION", "COUNTERFACTUAL", "CANDIDATE_FREE_RECALL"}


def test_isolated_evidence_cannot_enter_a_production_or_shadow_graph(harness):
    source = HARNESS.read_text()
    code = _isolated_code()
    # It builds its own consensus and touches no pipeline.
    assert "QueryConsensusResult(" in code
    for forbidden in ("pipeline", "staged_pass", "bidirectional_results",
                      "layer4_results", "decide_graph", "enumerate_query"):
        assert forbidden not in code, forbidden
    source = HARNESS.read_text()
    assert '"entered_production_graph": False' in source
    assert '"entered_shadow_graph": False' in source


def test_isolated_execution_cannot_move_production_accounting(harness):
    """It runs after `compare_passes`, so the invariance verdict precedes it."""
    driver = _driver_source()
    compare = driver.index('summary["composed"] = compare_passes(')
    isolated = driver.index("isolated_m18_smoke(config, staged)")
    assert compare < isolated
    function = HARNESS.read_text()
    section = function[function.index("def isolated_m18_smoke"):
                       function.index("def uncalibrated_activation_fails")]
    # No prediction, no budget, no stop reason is produced or touched.
    for forbidden in ("object_entities", "calls_used", "generated_tokens_used",
                      "stopped_reason", "Budget"):
        assert forbidden not in section, forbidden


def test_the_isolated_smoke_requires_a_real_physical_call(harness):
    source = HARNESS.read_text()
    assert "calls = runtime.calls - before" in source
    assert "if calls <= 0:" in source
    assert "real-weight evidence" in source


def test_no_hard_coded_mechanism_list_can_fake_coverage(harness):
    """Coverage is derived from what actually executed."""
    source = HARNESS.read_text()
    assert 'isolated["mechanism_executed"]' in source
    # Derived from the executed request, which owns mechanism identity.
    assert 'request.check_kind.value' in source
    for forbidden in ('= ["REVERSE"]', '= ["COUNTERFACTUAL"]',
                      '= ["CANDIDATE_FREE_RECALL"]', '= ["KEY_CONDITION"]',
                      'm18_real_weight_coverage"] = True'):
        assert forbidden not in source, forbidden
    # Coverage is a boolean over the two observed lists.
    assert 'summary["m18_real_weight_coverage"] = bool(' in source


def test_the_isolated_smoke_preserves_staged_single_residency(harness):
    source = HARNESS.read_text()
    function = source[source.index("def isolated_m18_smoke"):
                      source.index("def uncalibrated_activation_fails")]
    assert "staged.load(StagedRuntimes.ENUMERATOR)" in function
    assert "staged.release()" in function
    assert function.index("staged.load(") < function.index("staged.release()")
    # Only the role the mechanism genuinely needs.
    assert "StagedRuntimes.VERIFIER" not in function


def test_the_isolated_smoke_reads_no_benchmark_data(harness):
    code = _isolated_code()
    for forbidden in ("load_dataset", "benchmark", "gold", "split",
                      "objectentities"):
        assert forbidden not in code.casefold(), forbidden
    # The subject is a literal declared right here.
    assert 'subject = "countryLandBordersCountry", "Portugal"' in code


# ==========================================================================
# Module 18 result-reporting contract
#
# The real run reached `execute(...)` and got a BidirectionalCheckRecord back,
# then crashed building the summary: mechanism identity lives on the *request*,
# not the record.
# ==========================================================================


def _m18_members(cls):
    import dataclasses

    return {f.name for f in dataclasses.fields(cls)} | {
        name for name, value in vars(cls).items() if isinstance(value, property)}


def test_mechanism_identity_is_owned_by_the_request_not_the_record():
    from cover_kbc.verification.bidirectional_types import (
        BidirectionalCheckRecord, BidirectionalCheckRequest, EligibleCheck)

    # EligibleCheck owns it; the request re-exposes it; the record does not.
    assert "check_kind" in _m18_members(EligibleCheck)
    assert "check_kind" in _m18_members(BidirectionalCheckRequest)
    assert "check_kind" not in _m18_members(BidirectionalCheckRecord)
    # The record carries the request that produced it.
    assert "request" in _m18_members(BidirectionalCheckRecord)
    # And the canonical name is `check_kind`, not `kind`.
    assert "kind" not in _m18_members(BidirectionalCheckRequest)


def test_the_harness_reads_the_mechanism_from_the_executed_request():
    code = _isolated_code()
    assert '"mechanism_executed": request.check_kind.value' in code
    # The exact request that was executed, asserted at run time.
    assert "executed = record.request" in code
    assert "if executed is not request:" in code


def test_no_reference_to_record_check_kind_remains():
    source = HARNESS.read_text()
    assert "record.check_kind" not in source
    assert "request.kind" not in source
    for forbidden in ('getattr(record, "check_kind"', "catalogue[0].check_kind.value",
                      '"mechanism_executed": "CANDIDATE_FREE_RECALL"'):
        assert forbidden not in source, forbidden


def test_every_m18_attribute_the_harness_reads_exists_in_production():
    """The whole class of bug, checked once against the real dataclasses."""
    import re

    from cover_kbc.verification.bidirectional_types import (
        BidirectionalCheckRecord, BidirectionalCheckRequest, EligibleCheck)

    code = _isolated_code()
    for name, cls in (("record", BidirectionalCheckRecord),
                      ("request", BidirectionalCheckRequest)):
        used = set(re.findall(rf"\b{name}\.([a-z_0-9]+)", code))
        assert used, f"the harness reads nothing from {name}"
        missing = used - _m18_members(cls)
        assert not missing, f"{cls.__name__} has no {sorted(missing)}"
    catalogue_reads = set(re.findall(r"\bc\.([a-z_0-9]+)", code))
    assert not (catalogue_reads - _m18_members(EligibleCheck))


def test_the_harness_does_not_reach_into_the_production_m18_types():
    """The harness must adapt to Module 18, never the other way round.

    This replaces a ``git status --porcelain`` assertion on the two production
    files. A clean working tree cannot tell an intentional change from a
    regression - it fails on any legitimate edit and says nothing about what
    the harness actually did - which is the anti-pattern Audit 0041 §34 named
    and Audit 0042 §12 already converted two other tests away from. The
    behavioural contract is asserted instead: the harness reads Module 18's
    published surface (checked above) and never writes to it.
    """
    import re

    code = _isolated_code()
    for forbidden in ("setattr(", "monkeypatch", "object.__setattr__",
                      "__dict__"):
        assert forbidden not in code, forbidden
    # A production M18 object may be constructed and read, never assigned into.
    assert not re.search(r"\b(record|request|check|target)\.[a-z_0-9]+\s*=[^=]",
                         code), "the harness assigns into a production M18 object"


def _fake_m18_record(kind_value="CANDIDATE_FREE_RECALL", calls=1):
    """A record built from the **real** production types. No stubs."""
    from cover_kbc.verification.bidirectional_types import (
        BidirectionalCheckKind, BidirectionalCheckRecord,
        BidirectionalCheckRequest, CheckParseStatus, CheckTarget, EligibleCheck,
        CheckTargetKind,
    )

    check = EligibleCheck(
        check_kind=BidirectionalCheckKind(kind_value),
        target=CheckTarget(
            relation="countryLandBordersCountry", subject="Portugal", row_index=0,
            kind=CheckTargetKind.ENTITY_CANDIDATE, target_id="spain",
            display="Spain"),
    )
    request = BidirectionalCheckRequest(
        check=check, template_id="t1", model_role="enumerator", sample_index=0,
        decode_identity="default", check_version="m18-v1")
    record = BidirectionalCheckRecord(
        request=request, origin_event_id="origin0001", prompt_sha256="deadbeef",
        model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        model_revision="95a6d26c", model_family="mistral",
        independence_group="M18_CANDIDATE_FREE_RECALL", raw_output="Spain",
        parse_status=CheckParseStatus.OK, calls=calls, prompt_tokens=10,
        generated_tokens=4)
    return request, record


def test_a_real_contract_record_produces_the_expected_summary(harness):
    """Exercise the reporting path against genuine production objects."""
    request, record = _fake_m18_record()
    assert request.check_kind.value == "CANDIDATE_FREE_RECALL"
    assert record.request is request
    # The fields the harness reports all resolve.
    assert record.origin_event_id == "origin0001"
    assert record.parse_status.value == "OK"
    assert request.operation_id


def test_physical_call_evidence_is_still_required():
    source = HARNESS.read_text()
    assert "calls = runtime.calls - before" in source
    assert "if calls <= 0:" in source
    assert "real-weight evidence" in source
    # Catalogue/build_request succeeding is never enough on its own.
    code = _isolated_code()
    assert code.index("verifier.execute(") < code.index("if calls <= 0:")


def test_the_summary_records_canonical_execution_evidence():
    code = _isolated_code()
    for field in ('"mechanism_executed"', '"model_role"', '"physical_calls"',
                  '"parse_status"', '"origin_event_id"', '"operation_id"',
                  '"record_calls"'):
        assert field in code, field


def test_natural_and_isolated_lists_stay_separate_after_the_fix():
    source = HARNESS.read_text()
    assert '"m18_natural_mechanisms_executed"' in source
    assert '"m18_isolated_contract_mechanisms_executed"' in source
    assert 'summary["m18_real_weight_coverage"] = bool(' in source
    # Isolated coverage is still derived from what executed.
    assert 'isolated["mechanism_executed"]' in source


def test_production_shadow_invariance_is_untouched_by_this_repair():
    source = HARNESS.read_text()
    assert "def compare_passes(" in source
    assert '"production_output_unchanged"' in source
    assert '"m7_budget_unchanged"' in source
    # The isolated smoke still runs after the comparison.
    driver = _driver_source()
    assert driver.index("compare_passes(") < driver.index("isolated_m18_smoke(")
