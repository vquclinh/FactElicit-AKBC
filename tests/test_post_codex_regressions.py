"""Regressions for the defects the independent Codex review found.

Each test reproduces one confirmed finding and pins its fix. Deterministic and
synthetic; no model is loaded.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from cover_kbc.contracts.registry import get_contract
from cover_kbc.controller import Action, ActionType
from cover_kbc.evidence.graph import build_graph
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.models.registry import build_runtime, model_blocks
from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
from cover_kbc.staging import StageWriter, graph_from_json, graph_to_json, read_stage
from cover_kbc.types import Budget, BudgetExceeded, ModelRole, Query
from cover_kbc.verification import GateResult, TEMPLATE_STANDARD

TARGET_CONFIG = Path("configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml")
BORDERS = "countryLandBordersCountry"


def qwen_only_phase_b() -> CoverPipeline:
    """Exactly what `run_staged.py` Phase B builds: one Qwen under both names."""
    qwen = ScriptedRuntime(
        fallback=lambda r: "Alpha", model_id="Qwen/Qwen3.5-4B",
        family="qwen", role="verifier",
    )
    config = PipelineConfig(
        mode=ExecutionMode.STAGED, enable_calibrated_gate=True,
        enable_cross_model_recall=True, enable_active_controller=True,
        enumerator_model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        verifier_model_id="Qwen/Qwen3.5-4B",
    )
    return CoverPipeline(qwen, config, verifier_runtime=qwen)


# --- BLOCKER 1: capability must not depend on object identity ----------------


def test_the_verifier_capability_survives_being_the_only_resident_runtime():
    pipeline = qwen_only_phase_b()
    assert pipeline.verifier_available, (
        "Qwen loaded as both runtime and verifier_runtime must still be a verifier"
    )


def test_a_deferred_gate_stops_being_deferred_once_qwen_is_resident():
    assert not qwen_only_phase_b()._gate_deferred()


def test_cross_model_recall_is_reachable_when_only_qwen_is_resident():
    """Heterogeneity is measured against the configured enumerator."""
    assert qwen_only_phase_b().cross_model_recall_available


def test_a_bare_enumerator_is_not_a_verifier():
    """The capability must not be granted by a fallback standing in."""
    mistral = ScriptedRuntime(
        fallback=lambda r: "Alpha", model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        family="mistral", role="enumerator",
    )
    pipeline = CoverPipeline(
        mistral,
        PipelineConfig(enable_calibrated_gate=True, enable_cross_model_recall=True,
                       enumerator_model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
                       verifier_model_id="Qwen/Qwen3.5-4B"),
    )
    assert not pipeline.verifier_available
    assert not pipeline.cross_model_recall_available


def test_cross_model_recall_is_false_when_both_roles_are_one_model():
    """Not heterogeneous if the config assigns the same checkpoint to both."""
    qwen = ScriptedRuntime(fallback=lambda r: "Alpha", model_id="Qwen/Qwen3.5-4B",
                           family="qwen", role="verifier")
    pipeline = CoverPipeline(
        qwen,
        PipelineConfig(enable_cross_model_recall=True,
                       enumerator_model_id="Qwen/Qwen3.5-4B",
                       verifier_model_id="Qwen/Qwen3.5-4B"),
        verifier_runtime=qwen,
    )
    assert not pipeline.cross_model_recall_available


# --- BLOCKER 2: the hard ceiling is unbreakable ------------------------------


def verify_pipeline():
    enum = ScriptedRuntime(fallback=lambda r: "Alpha", model_id="offline/m",
                           family="m", role="enumerator")
    ver = ScriptedRuntime(label_scores={}, model_id="offline/q", family="q", role="verifier")
    config = PipelineConfig(enable_verifier=True, use_calibration=True,
                            max_verifications_per_query=4)
    return CoverPipeline(enum, config, verifier_runtime=ver), ver


def test_an_uncached_calibrated_verify_is_refused_with_one_call_left():
    """The reproduced overrun: 1 call left, 2 needed, finished at 5/4."""
    pipeline, _ = verify_pipeline()
    contract = get_contract(BORDERS)
    action = Action(ActionType.VERIFY, candidate_key="alpha")

    cost = pipeline._planned_neural_cost(contract, action)
    assert cost == 2, "candidate score plus an uncached control"
    budget = Budget(max_calls=4, max_generated_tokens=9999)
    budget.charge(calls=3)
    assert not budget.can_afford(cost)


def test_the_same_verify_becomes_affordable_once_the_control_is_cached():
    pipeline, verifier = verify_pipeline()
    contract = get_contract(BORDERS)
    action = Action(ActionType.VERIFY, candidate_key="alpha")

    pipeline.calibrator.control_logits(verifier, contract, TEMPLATE_STANDARD)
    assert pipeline._planned_neural_cost(contract, action) == 1
    budget = Budget(max_calls=4, max_generated_tokens=9999)
    budget.charge(calls=3)
    assert budget.can_afford(pipeline._planned_neural_cost(contract, action))


def test_an_adversarial_verification_plans_templates_and_controls():
    pipeline, _ = verify_pipeline()
    pipeline.config.enable_prompt_disagreement = True
    contract = get_contract(BORDERS)
    cost = pipeline._planned_neural_cost(
        contract, Action(ActionType.ADVERSARIAL_VERIFY, candidate_key="alpha")
    )
    assert cost == 2 * len(pipeline.config.disagreement_template_ids)


def test_a_description_first_view_plans_two_calls():
    contract = get_contract(BORDERS)
    pipeline, _ = verify_pipeline()
    from cover_kbc.elicitation.library import get_view

    description = next(
        (v for v in contract.all_views() if get_view(contract.relation, v).is_description),
        None,
    )
    if description is None:
        pytest.skip("no description view declared")
    assert pipeline._planned_neural_cost(
        contract, Action(ActionType.RUN_VIEW, view_id=description)
    ) == 2


def test_the_runtime_guard_refuses_a_call_that_would_cross_the_ceiling():
    budget = Budget(max_calls=4, max_generated_tokens=9999)
    budget.charge(calls=3)
    with pytest.raises(BudgetExceeded):
        budget.reserve(2)
    budget.reserve(1)                       # exact fit is fine
    assert budget.calls_used == 4
    with pytest.raises(BudgetExceeded):
        budget.reserve(1)


@pytest.mark.parametrize("relation", ["countryLandBordersCountry", "personHasCityOfDeath",
                                      "hasArea", "awardWonBy"])
def test_no_query_ever_exceeds_its_hard_call_ceiling(relation, tmp_path):
    contract = get_contract(relation)
    answer = "5000" if contract.is_numeric else "Alpha; Beta"
    enum = ScriptedRuntime(fallback=lambda r: answer, model_id="offline/mistral",
                           family="offline-mistral", role="enumerator")
    ver = ScriptedRuntime(
        fallback=lambda r: answer,
        label_scores={("blind_verifier", "S", relation):
                      {"VALID": 3.0, "INVALID": -1.0, "UNKNOWN": 0.0}},
        model_id="offline/qwen", family="offline-qwen", role="verifier",
    )
    pipeline = CoverPipeline(
        enum,
        PipelineConfig(
            mode=ExecutionMode.STAGED, enable_active_controller=True, enable_verifier=True,
            use_calibration=True, max_verifications_per_query=6,
            enable_cross_model_recall=True, enable_prompt_disagreement=True,
            max_steps_per_query=10, max_calls_per_query=24,
            enumerator_model_id="offline/mistral", verifier_model_id="offline/qwen",
        ),
        verifier_runtime=ver,
    )
    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate([Query("S", relation, 0)]):
            writer.write(graph)
    source = tmp_path / "a.jsonl"
    for cycle in range(12):
        graphs = list(read_stage(source))
        pending = {CoverPipeline.pending_role(g) for g in graphs} - {None}
        if not pending:
            break
        role = ModelRole.VERIFIER if ModelRole.VERIFIER in pending else ModelRole.ENUMERATOR
        driver = pipeline.verify if role is ModelRole.VERIFIER else pipeline.resume
        target = tmp_path / f"r{cycle}.jsonl"
        with StageWriter(target) as writer:
            for graph in driver(iter(graphs)):
                writer.write(graph)
        source = target
    graph = list(read_stage(source))[0]
    assert graph.budget_snapshot["calls_used"] <= contract.stopping.max_calls, relation
    assert enum.calls + ver.calls == graph.budget_snapshot["calls_used"], relation


# --- MAJOR 1: no stale budget in any mode ------------------------------------


@pytest.mark.parametrize("mode", [ExecutionMode.INTERLEAVED, ExecutionMode.STAGED])
@pytest.mark.parametrize("active", [True, False])
def test_every_execution_mode_charges_verifier_calls(mode, active, tmp_path):
    enum = ScriptedRuntime(fallback=lambda r: "Alpha; Beta", model_id="offline/m",
                           family="m", role="enumerator")
    ver = ScriptedRuntime(label_scores={}, model_id="offline/q", family="q", role="verifier")
    config = PipelineConfig(
        mode=mode, enable_verifier=True, use_calibration=True,
        max_verifications_per_query=4, enable_active_controller=active,
        run_optional_views=not active,
        enumerator_model_id="offline/m", verifier_model_id="offline/q",
    )
    pipeline = CoverPipeline(enum, config, verifier_runtime=ver)
    query = Query("S", BORDERS, 0)

    if mode is ExecutionMode.INTERLEAVED:
        prediction = pipeline.run([query]).predictions[0]
    else:
        with StageWriter(tmp_path / "a.jsonl") as writer:
            for graph in pipeline.enumerate([query]):
                writer.write(graph)
        with StageWriter(tmp_path / "b.jsonl") as writer:
            for graph in pipeline.verify(read_stage(tmp_path / "a.jsonl")):
                writer.write(graph)
        graphs = list(read_stage(tmp_path / "b.jsonl"))
        if graphs[0].pending_action:
            pytest.skip("scenario needs a role swap; covered elsewhere")
        prediction = pipeline.decide(graphs).predictions[0]

    assert prediction.calls_used == enum.calls + ver.calls, (
        f"{mode.value}/active={active}: stale or partial budget"
    )


# --- MAJOR 2: gate_result survives staging -----------------------------------


def test_a_gate_result_round_trips_losslessly():
    contract = get_contract("personHasCityOfDeath")
    graph = build_graph(Query("S", contract.relation, 0), contract)
    graph.gate_result = GateResult(
        question="is S deceased?", p_yes=0.10, p_no=0.85, p_unknown=0.05,
        raw_logits={"YES": -1.0, "NO": 2.0, "UNKNOWN": 0.0},
        calibrated=True, margin=2.4, entropy=0.42, decision="NO",
        model_id="Qwen/Qwen3.5-4B",
    )
    restored = graph_from_json(graph_to_json(graph))
    assert restored.gate_result == graph.gate_result


def test_a_confident_negative_gate_survives_a_role_swap(tmp_path):
    contract = get_contract("personHasCityOfDeath")
    graph = build_graph(Query("S", contract.relation, 0), contract)
    graph.gate_result = GateResult(question="q", p_no=0.9, decision="NO",
                                   model_id="Qwen/Qwen3.5-4B", calibrated=True)
    graph.close_gate("calibrated gate: NO")

    with StageWriter(tmp_path / "g.jsonl") as writer:
        writer.write(graph)
    restored = list(read_stage(tmp_path / "g.jsonl"))[0]
    assert restored.gate_result is not None, "a scored gate became None after a swap"
    assert restored.gate_result.decision == "NO"
    assert restored.gate_negative


# --- MAJOR 3: no silent NullRuntime ------------------------------------------


def test_the_frozen_target_profile_cannot_silently_build_a_null_runtime():
    config = yaml.safe_load(TARGET_CONFIG.read_text())
    with pytest.raises(ValueError, match="backend"):
        build_runtime(config["model_profile"])


def test_the_canonical_resolver_handles_the_nested_target_profile():
    config = yaml.safe_load(TARGET_CONFIG.read_text())
    enumerator, verifier = model_blocks(config)
    assert enumerator["model_id"] == "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    assert verifier["model_id"] == "Qwen/Qwen3.5-4B"
    assert "backend" in enumerator and "backend" in verifier


def test_a_null_runtime_still_requires_an_explicit_request():
    assert build_runtime({"backend": "null"}).spec.model_id == "offline/null"
    assert build_runtime({"backend": None}).spec.model_id == "offline/null"
    with pytest.raises(ValueError):
        build_runtime({"model_id": "something"})


def test_both_production_entry_points_use_one_resolver():
    for script in ("scripts/run_staged.py", "scripts/run_cover.py"):
        source = Path(script).read_text()
        assert "model_blocks" in source, f"{script} does not use the canonical resolver"


# --- MODERATE: row completeness ----------------------------------------------


def test_output_completeness_is_checked_against_the_intended_query_set():
    import importlib.util

    import sys

    scripts_dir = str(Path("scripts").resolve())
    if scripts_dir not in sys.path:                   # the runner's own bootstrap
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_staged", "scripts/run_staged.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from cover_kbc.types import Prediction

    with tempfile.TemporaryDirectory() as directory:
        run_dir = Path(directory)
        (run_dir / module.QUERY_MANIFEST).write_text(json.dumps({
            "split": "val", "relation": BORDERS, "limit": 2,
            "queries": [
                {"SubjectEntity": "A", "Relation": BORDERS, "row_index": 0},
                {"SubjectEntity": "B", "Relation": BORDERS, "row_index": 1},
            ],
        }))

        exact = [Prediction(subject="A", relation=BORDERS),
                 Prediction(subject="B", relation=BORDERS)]
        assert len(module._expected_queries(run_dir, exact)) == 2      # passes

        for broken in (
            [Prediction(subject="A", relation=BORDERS)],                        # missing
            exact + [Prediction(subject="C", relation=BORDERS)],                # extra
            exact + [Prediction(subject="A", relation=BORDERS)],                # duplicate
        ):
            with pytest.raises(SystemExit):
                module._expected_queries(run_dir, broken)
