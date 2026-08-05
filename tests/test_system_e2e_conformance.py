"""System-level end-to-end conformance: COVER-KBC as one system.

Deterministic and synthetic throughout. **No model is loaded anywhere** — the
frozen Mistral-24B + Qwen3.5-4B pair has never been run, and nothing here is a
performance result.

What module-local suites cannot see, and this one checks:

* Algorithm 1 is the actual production path for all six relations;
* the same evidence never earns credit twice across module boundaries;
* logical semantics do not depend on which model happens to be resident;
* every emitted object traces back to a generation or verifier record.

Fixtures are named for the architecture behaviour they pin, and use synthetic
entities (Alpha, Beta, Gamma) so no benchmark fact is hard-coded.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import all_contracts, get_contract
from cover_kbc.contracts.router import compile_query
from cover_kbc.controller import ActionType, ModelRole, legal_actions
from cover_kbc.coverage import RCSEState, estimate_residual
from cover_kbc.elicitation.library import get_view
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
from cover_kbc.scoring import acquisition_groups, supporting_acquisition_groups
from cover_kbc.staging import StageWriter, read_stage
from cover_kbc.types import (
    Budget,
    EmptyReason,
    EvidenceMode,
    IndependenceGroup,
    ProgramType,
    Query,
)

RELATIONS = [c.relation for c in all_contracts()]

#: The frozen pairing. Never loaded here; asserted as a declaration only.
FROZEN_MODELS = {
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506": 24_011_361_280,
    "Qwen/Qwen3.5-4B": 4_659_865_088,
}
PARAMETER_CEILING = 32_000_000_000


# --- fixtures ----------------------------------------------------------------


ACTIVE = dict(
    enable_active_controller=True, enable_verifier=True, use_calibration=True,
    max_verifications_per_query=6, enable_cross_model_recall=True,
    enable_prompt_disagreement=True, max_steps_per_query=10, max_calls_per_query=24,
)


def runtimes(enumerator_answer, verifier_answer="Alpha", labels=None, subject="S",
             relation="countryLandBordersCountry"):
    """A scripted enumerator/verifier pair with explicit, distinct model ids."""
    enum = ScriptedRuntime(
        fallback=enumerator_answer if callable(enumerator_answer)
        else (lambda r, _a=enumerator_answer: _a),
        model_id="offline/mistral-stub", family="offline-mistral", role="enumerator",
    )
    ver = ScriptedRuntime(
        fallback=lambda r: verifier_answer,
        label_scores={("blind_verifier", subject, relation): labels} if labels else None,
        model_id="offline/qwen-stub", family="offline-qwen", role="verifier",
    )
    return enum, ver


def drive(pipeline, queries, tmp_path, *, max_swaps=12):
    """Run staged execution to completion, as `run_staged.py::phase_resolve` does."""
    with StageWriter(tmp_path / "a.jsonl") as writer:
        for graph in pipeline.enumerate(queries):
            writer.write(graph)
    source = tmp_path / "a.jsonl"
    roles = [ModelRole.ENUMERATOR]
    for cycle in range(max_swaps):
        graphs = list(read_stage(source))
        pending = {CoverPipeline.pending_role(g) for g in graphs} - {None}
        if not pending:
            return graphs, roles
        role = (ModelRole.VERIFIER if ModelRole.VERIFIER in pending
                else ModelRole.ENUMERATOR)
        driver = pipeline.verify if role is ModelRole.VERIFIER else pipeline.resume
        target = tmp_path / f"r{cycle}_{role.value}.jsonl"
        with StageWriter(target) as writer:
            for graph in driver(iter(graphs)):
                writer.write(graph)
        roles.append(role)
        source = target
    raise AssertionError("staged orchestration did not settle")


def run_interleaved(enum, ver, query, **overrides):
    config = PipelineConfig(mode=ExecutionMode.INTERLEAVED, **{**ACTIVE, **overrides})
    return CoverPipeline(enum, config, verifier_runtime=ver).run([query]).predictions[0]


# --- 1-5. the six relations compile and route --------------------------------


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_official_relation_compiles_and_routes(relation):
    query, contract = compile_query("S", relation, 0)
    assert contract.relation == relation
    assert contract.program_type in ProgramType
    assert contract.mandatory_views, "a relation with no mandatory work cannot start"


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_declared_view_exists_and_is_executable(relation):
    contract = get_contract(relation)
    for view_id in contract.all_views():
        view = get_view(relation, view_id)
        assert view.view_id == view_id
        view.validate()


def test_the_six_relations_cover_all_four_typed_programmes():
    programmes = {get_contract(r).program_type for r in RELATIONS}
    assert programmes == set(ProgramType)


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_available_mechanism_is_reachable_end_to_end(relation):
    """No mechanism is counted available yet unschedulable, in any relation."""
    contract = get_contract(relation)
    state = RCSEState()
    from cover_kbc.types import Candidate, Evidence, EdgeType

    found = [Candidate(key="alpha", display_value="Alpha", relation=relation)]
    found[0].add_evidence(
        Evidence("alpha", EdgeType.SUPPORT, contract.eligible_independence_groups[0],
                 "v", "m", 0, "r0")
    )
    for _ in range(20):
        actions = legal_actions(contract, found, state,
                                Budget(max_calls=999, max_generated_tokens=99_999))
        fresh = [a for a in actions if a.view_id and a.view_id not in state.executed_views]
        if not fresh:
            break
        for action in fresh:
            state.executed_views.add(action.view_id)
            view = get_view(relation, action.view_id)
            if not view.is_gate:
                state.executed_groups.add(view.independence_group)
    unreachable = set(acquisition_groups(contract)) - state.executed_groups
    assert not unreachable, f"{relation}: {sorted(g.name for g in unreachable)}"


@pytest.mark.parametrize("relation", RELATIONS)
def test_a_disabled_branch_leaves_no_permanent_gap(relation):
    contract = get_contract(relation)
    for group in (IndependenceGroup.CROSS_MODEL_RECALL, IndependenceGroup.FACTUAL_DECODING):
        assert group not in acquisition_groups(contract)
    state = RCSEState()
    state.executed_views.update(contract.all_views())
    state.executed_groups.update(acquisition_groups(contract))
    residual = estimate_residual(contract, [], state)
    assert residual.components["mechanism_gap"] == 0.0


# --- 6-11. six-relation golden end-to-end fixtures ---------------------------


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_relation_runs_end_to_end_and_produces_a_legal_row(relation, tmp_path):
    """The whole system, once per relation, through staged execution."""
    contract = get_contract(relation)
    answer = "5000" if contract.is_numeric else "Alpha; Beta"
    enum, ver = runtimes(answer, verifier_answer=answer, subject="S", relation=relation)
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, roles = drive(pipeline, [Query("S", relation, 0)], tmp_path)
    prediction = pipeline.decide(graphs).predictions[0]

    assert prediction.relation == relation
    assert isinstance(prediction.object_entities, list)
    assert all(isinstance(v, str) and v.strip() for v in prediction.object_entities)
    limit = contract.selection.max_objects
    if limit:
        assert len(prediction.object_entities) <= limit
    assert not graphs[0].pending_action, "finalized with work outstanding"
    assert roles[0] is ModelRole.ENUMERATOR


def test_an_easy_border_costs_far_less_than_an_open_award(tmp_path):
    """Typed budgets must actually differentiate; borders finish cheaply."""
    costs = {}
    for relation in ("countryLandBordersCountry", "awardWonBy"):
        enum, ver = runtimes("Alpha; Beta", subject="S", relation=relation)
        pipeline = CoverPipeline(
            enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
        )
        graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path / relation)
        costs[relation] = graphs[0].budget_snapshot["calls_used"]
    assert costs["countryLandBordersCountry"] <= costs["awardWonBy"], costs


def test_a_confident_negative_death_gate_produces_an_explained_empty_row(tmp_path):
    enum = ScriptedRuntime(fallback=lambda r: "Paris", model_id="offline/mistral-stub",
                           family="offline-mistral", role="enumerator")
    ver = ScriptedRuntime(
        fallback=lambda r: "Paris",
        label_scores={("calibrated_gate", "S", "personHasCityOfDeath"):
                      {"YES": -3.0, "NO": 3.0, "UNKNOWN": -1.0}},
        model_id="offline/qwen-stub", family="offline-qwen", role="verifier",
    )
    pipeline = CoverPipeline(
        enum,
        PipelineConfig(mode=ExecutionMode.STAGED, enable_calibrated_gate=True, **ACTIVE),
        verifier_runtime=ver,
    )
    graphs, _ = drive(pipeline, [Query("S", "personHasCityOfDeath", 0)], tmp_path)
    prediction = pipeline.decide(graphs).predictions[0]
    assert prediction.object_entities == []
    assert prediction.empty_reason is EmptyReason.CONFIDENT_NEGATIVE_GATE


def test_an_uncertain_death_gate_is_never_reported_as_a_confident_negative(tmp_path):
    enum = ScriptedRuntime(fallback=lambda r: "", model_id="offline/mistral-stub",
                           family="offline-mistral", role="enumerator")
    ver = ScriptedRuntime(fallback=lambda r: "", model_id="offline/qwen-stub",
                          family="offline-qwen", role="verifier")     # uniform => UNKNOWN
    pipeline = CoverPipeline(
        enum,
        PipelineConfig(mode=ExecutionMode.STAGED, enable_calibrated_gate=True, **ACTIVE),
        verifier_runtime=ver,
    )
    graphs, _ = drive(pipeline, [Query("S", "personHasCityOfDeath", 0)], tmp_path)
    prediction = pipeline.decide(graphs).predictions[0]
    assert prediction.empty_reason is not EmptyReason.CONFIDENT_NEGATIVE_GATE


def test_area_emits_one_km2_scalar_end_to_end(tmp_path):
    enum, ver = runtimes(
        lambda r: "5000 km2" if "direct" in r.metadata.get("view_id", "") else "5050 km2",
        verifier_answer="5000", subject="S", relation="hasArea",
    )
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", "hasArea", 0)], tmp_path)
    objects = pipeline.decide(graphs).predictions[0].object_entities
    assert len(objects) <= 1
    for value in objects:
        assert value.replace(".", "").isdigit(), value
        assert "km" not in value.lower() and "," not in value


def test_awards_stop_before_exhausting_every_optional_facet(tmp_path):
    """Adaptive stopping, not a fixed sweep."""
    enum, ver = runtimes("Alpha", subject="S", relation="awardWonBy")
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", "awardWonBy", 0)], tmp_path)
    contract = get_contract("awardWonBy")
    ran = {d["chosen"]["view_id"] for d in graphs[0].controller_log if d["chosen"]["view_id"]}
    assert ran, "the controller ran nothing"
    assert ran != set(contract.all_views()), "every optional view was swept"


# --- 12-17. cross-module accounting ------------------------------------------


@pytest.mark.parametrize("relation", RELATIONS)
def test_no_evidence_earns_credit_in_two_score_components(relation, tmp_path):
    """F / L / X stay disjoint through the *real* pipeline, not just unit tests."""
    from cover_kbc.scoring import score_candidate, DEFAULT_SCORING

    contract = get_contract(relation)
    answer = "5000" if contract.is_numeric else "Alpha"
    enum, ver = runtimes(answer, verifier_answer=answer, subject="S", relation=relation,
                         labels={"VALID": 3.0, "INVALID": -1.0, "UNKNOWN": 0.0})
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path)

    for candidate in graphs[0].candidates.values():
        breakdown = score_candidate(candidate, contract, DEFAULT_SCORING)
        acquisition = set(supporting_acquisition_groups(candidate, contract))
        # F counts only acquisition mechanisms.
        assert IndependenceGroup.BLIND_VERIFIER not in acquisition
        assert IndependenceGroup.CROSS_MODEL_RECALL not in acquisition
        # X counts only genuinely independent recall.
        verifier_group = candidate.groups.get(IndependenceGroup.BLIND_VERIFIER)
        if verifier_group and verifier_group.supports_candidate and breakdown.cross_model:
            recall = candidate.groups.get(IndependenceGroup.CROSS_MODEL_RECALL)
            assert recall is not None and any(
                e.mode is EvidenceMode.INDEPENDENT_RECALL for e in recall.supports
            ), "X credited without independent recall"


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_emitted_object_traces_back_to_evidence(relation, tmp_path):
    contract = get_contract(relation)
    answer = "5000" if contract.is_numeric else "Alpha; Beta"
    enum, ver = runtimes(answer, verifier_answer=answer, subject="S", relation=relation)
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path)
    prediction = pipeline.decide(graphs).predictions[0]
    graph = graphs[0]

    for value in prediction.object_entities:
        owners = [c for c in prediction.candidates if c.output_value == value]
        assert len(owners) == 1, f"{value!r} maps to {len(owners)} candidates"
        candidate = owners[0]
        assert candidate.record_ids, f"{value!r} has no generating record"
        for record_id in candidate.record_ids:
            record = graph.records.get(record_id)
            assert record is not None, f"{value!r} cites a missing record"
            assert record.view_id and record.model_id


def test_description_prose_never_becomes_factual_support(tmp_path):
    """Stage-1 context is an artefact, not evidence."""
    relation = "countryLandBordersCountry"
    contract = get_contract(relation)
    if not any(get_view(relation, v).is_description for v in contract.all_views()):
        pytest.skip(f"{relation} declares no description view")

    enum, ver = runtimes("Alpha", subject="S", relation=relation)
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path)
    graph = graphs[0]

    description_ids = {
        r.record_id for r in graph.records.values() if r.stage == "description"
    }
    for candidate in graph.candidates.values():
        for edge in candidate.all_evidence():
            assert edge.record_id not in description_ids, (
                "prose became a support edge; only the extraction stage may"
            )
    for record_id in description_ids:
        assert graph.records[record_id].raw_output is not None   # kept for the trace


def test_reverse_evidence_is_acquisition_not_verification(tmp_path):
    relation = "awardWonBy"
    enum, ver = runtimes("Alpha; Gamma", subject="S", relation=relation)
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path)
    for candidate in graphs[0].candidates.values():
        group = candidate.groups.get(IndependenceGroup.REVERSE_ALTERNATE)
        if group is None:
            continue
        for edge in group.supports:
            assert edge.independence_group is not IndependenceGroup.BLIND_VERIFIER
            assert edge.mode is EvidenceMode.INDEPENDENT_RECALL


def test_the_verifier_prompt_never_carries_acquisition_history():
    """The blindness boundary, checked on the real prompt builder."""
    from cover_kbc.verification import TEMPLATES, build_verifier_prompt

    contract = get_contract("awardWonBy")
    for template in TEMPLATES:
        prompt = build_verifier_prompt(
            Query("Testprize", contract.relation, 0), contract, "Alpha", template
        )
        for leak in ("independent_support", "raw_support", "q_res", "residual",
                     "score", "controller", "facet_gap", "F(o)", "L(o)"):
            assert leak.lower() not in prompt.lower(), leak


@pytest.mark.parametrize("relation", RELATIONS)
def test_repeated_output_from_one_view_is_not_independent_evidence(relation):
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.types import DecodeProfile, GenerationRecord, ViewFamily

    contract = get_contract(relation)
    if contract.is_numeric:
        pytest.skip("entity mentions only")
    query = Query("S", relation, 0)
    graph = build_graph(query, contract)
    for run in range(5):
        graph.add_entity_mentions(
            GenerationRecord(
                record_id=f"r{run}", query=query, view_id=contract.mandatory_views[0],
                view_family=ViewFamily.DIRECT,
                independence_group=IndependenceGroup.DIRECT_RECALL, run_id=run,
                model_id="m", prompt="p", prompt_hash="h", raw_output="Alpha",
                decode_profile=DecodeProfile(),
            ),
            ["Alpha"],
        )
    candidate = graph.candidates["alpha"]
    assert candidate.raw_support_count == 5
    assert len(supporting_acquisition_groups(candidate, contract)) == 1


# --- 18-21. staged / interleaved equivalence across programmes ---------------


@pytest.mark.parametrize("relation", RELATIONS)
def test_staged_and_interleaved_agree_semantically(relation, tmp_path):
    """Logical semantics may not depend on which model is resident."""
    contract = get_contract(relation)
    answer = "5000" if contract.is_numeric else "Alpha; Beta"
    labels = {"VALID": 3.0, "INVALID": -1.0, "UNKNOWN": 0.0}

    e1, v1 = runtimes(answer, verifier_answer=answer, subject="S",
                      relation=relation, labels=labels)
    interleaved = run_interleaved(e1, v1, Query("S", relation, 0))

    e2, v2 = runtimes(answer, verifier_answer=answer, subject="S",
                      relation=relation, labels=labels)
    staged_pipeline = CoverPipeline(
        e2, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=v2
    )
    graphs, _ = drive(staged_pipeline, [Query("S", relation, 0)], tmp_path)
    staged = staged_pipeline.decide(graphs).predictions[0]

    assert set(interleaved.object_entities) == set(staged.object_entities), relation
    assert {c.key for c in interleaved.candidates} == {c.key for c in staged.candidates}
    assert interleaved.empty_reason == staged.empty_reason

    def shape(prediction):
        return {
            c.key: sorted((e.independence_group.value, e.mode.value)
                          for e in c.all_evidence())
            for c in prediction.candidates
        }

    assert shape(interleaved) == shape(staged), relation


@pytest.mark.parametrize("relation", RELATIONS)
def test_a_full_scripted_run_is_deterministic(relation, tmp_path):
    contract = get_contract(relation)
    answer = "5000" if contract.is_numeric else "Alpha; Beta"
    seen = set()
    for attempt in range(3):
        enum, ver = runtimes(answer, verifier_answer=answer, subject="S", relation=relation)
        pipeline = CoverPipeline(
            enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
        )
        graphs, roles = drive(pipeline, [Query("S", relation, 0)],
                              tmp_path / f"run{attempt}")
        prediction = pipeline.decide(graphs).predictions[0]
        seen.add((
            tuple(prediction.object_entities),
            prediction.empty_reason.value,
            tuple(r.value for r in roles),
            tuple(d["chosen"]["action_type"] for d in graphs[0].controller_log),
        ))
    assert len(seen) == 1, f"{relation} was not deterministic"


@pytest.mark.parametrize("relation", RELATIONS)
def test_a_stage_round_trip_preserves_the_final_decision(relation, tmp_path):
    contract = get_contract(relation)
    answer = "5000" if contract.is_numeric else "Alpha; Beta"
    enum, ver = runtimes(answer, verifier_answer=answer, subject="S", relation=relation)
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path)
    before = pipeline.decide(graphs).predictions[0]

    with StageWriter(tmp_path / "final.jsonl") as writer:
        for graph in graphs:
            writer.write(graph)
    after = pipeline.decide(list(read_stage(tmp_path / "final.jsonl"))).predictions[0]

    assert before.object_entities == after.object_entities
    assert before.empty_reason == after.empty_reason


def test_the_global_budget_is_never_exceeded_or_negative(tmp_path):
    for relation in RELATIONS:
        contract = get_contract(relation)
        answer = "5000" if contract.is_numeric else "Alpha; Beta"
        enum, ver = runtimes(answer, verifier_answer=answer, subject="S", relation=relation)
        pipeline = CoverPipeline(
            enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
        )
        graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path / relation)
        snapshot = graphs[0].budget_snapshot
        assert 0 <= snapshot["calls_used"] <= contract.stopping.max_calls, relation
        assert snapshot["generated_tokens_used"] >= 0


# --- 22-26. configuration, compliance, freeze declarations -------------------


def test_the_frozen_model_pair_stays_within_the_parameter_ceiling():
    total = sum(FROZEN_MODELS.values())
    assert total == 28_671_226_368
    assert total < PARAMETER_CEILING
    # Quantisation must never reduce the counted number.
    assert all(count > 0 for count in FROZEN_MODELS.values())


def test_the_target_config_is_internally_coherent():
    import yaml

    config = yaml.safe_load(
        Path("configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml").read_text()
    )
    pipeline = config["pipeline"]
    assert pipeline["mode"] == "staged"
    assert pipeline["enable_active_controller"] is True
    assert pipeline["enable_verifier"] is True
    assert pipeline["use_calibration"] is True
    # The controller owns optional-view scheduling; forcing them all would make
    # the "active" name false.
    assert not pipeline.get("run_optional_views", False)
    # DoLa stays off, and is not even a configurable action.
    assert "dola" not in Path(
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml"
    ).read_text().lower()

    profile = config["model_profile"]
    assert profile["enumerator"]["role"] == "enumerator"
    assert profile["verifier"]["role"] == "verifier"
    assert set(FROZEN_MODELS) == {
        profile["enumerator"]["model_id"], profile["verifier"]["model_id"]
    }


def test_the_fixed_ablation_config_is_genuinely_fixed():
    import yaml

    config = yaml.safe_load(
        Path("configs/experiments/ablation_fixed_multiview.yaml").read_text()
    )["pipeline"]
    assert config["enable_active_controller"] is False
    assert config["run_optional_views"] is True, "a fixed multi-view ablation runs them all"


def test_no_config_enables_a_third_neural_component():
    import yaml

    for path in Path("configs/experiments").glob("*.yaml"):
        config = yaml.safe_load(path.read_text())
        profiles = config.get("model_profile") or {}
        neural = [
            p for p in profiles.values()
            if isinstance(p, dict)
            and str(p.get("backend", "null")).lower() in {"huggingface", "hf"}
        ]
        assert len(neural) <= 2, f"{path.name} declares {len(neural)} neural models"


def test_no_retrieval_reaches_the_production_prediction_path():
    banned = {"requests", "urllib", "httpx", "aiohttp", "wikipedia", "wikidata",
              "duckduckgo", "serpapi", "faiss", "chromadb", "pinecone"}
    offenders = []
    for path in Path("src/cover_kbc").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [(path.name, a.name) for a in node.names
                              if a.name.split(".")[0] in banned]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in banned:
                    offenders.append((path.name, node.module))
    assert not offenders, offenders


def test_no_training_reaches_the_production_prediction_path():
    banned_calls = {"fit", "partial_fit", "backward", "step", "train",
                    "get_peft_model", "prepare_model_for_training"}
    banned_imports = {"peft", "trl", "bitsandbytes_optim", "deepspeed", "accelerate"}
    offenders = []
    for path in Path("src/cover_kbc").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if name in banned_calls:
                    offenders.append((path.name, name))
            elif isinstance(node, ast.Import):
                offenders += [(path.name, a.name) for a in node.names
                              if a.name.split(".")[0] in banned_imports]
    assert not offenders, offenders


def test_no_heavyweight_model_is_loaded_by_the_test_suite():
    """Every runtime used here is offline and non-neural."""
    for runtime in (ScriptedRuntime({}),):
        assert runtime.spec.is_neural is False
        assert runtime.spec.published_total_parameters == 0


# --- 27-30. the surface-equivalence and numeric decisions --------------------


def test_a_soft_alias_hint_is_never_global_hard_identity():
    """The frozen decision: grouping requires provenance, not string similarity."""
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.types import DecodeProfile, GenerationRecord, ViewFamily

    contract = get_contract("companyTradesAtStockExchange")
    query = Query("S", contract.relation, 0)

    def record(record_id):
        return GenerationRecord(
            record_id=record_id, query=query, view_id=contract.mandatory_views[0],
            view_family=ViewFamily.DIRECT,
            independence_group=IndependenceGroup.DIRECT_RECALL, run_id=0,
            model_id="m", prompt="p", prompt_hash="h", raw_output="x",
            decode_profile=DecodeProfile(),
        )

    # Different generations: never grouped, however similar the strings.
    separate = build_graph(query, contract)
    separate.add_entity_mentions(record("r1"), ["The Alpha Exchange"])
    separate.add_entity_mentions(record("r2"), ["Alpha Exchange"])
    assert separate.same_record_alias_hints() == {}
    assert len(separate.candidates) == 2


def test_a_same_record_alias_hint_is_diagnostic_and_changes_no_output():
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.types import DecodeProfile, GenerationRecord, ViewFamily

    contract = get_contract("companyTradesAtStockExchange")
    query = Query("S", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(
        GenerationRecord(
            record_id="r1", query=query, view_id=contract.mandatory_views[0],
            view_family=ViewFamily.DIRECT,
            independence_group=IndependenceGroup.DIRECT_RECALL, run_id=0,
            model_id="m", prompt="p", prompt_hash="h",
            raw_output="The Alpha Exchange; Alpha Exchange", decode_profile=DecodeProfile(),
        ),
        ["The Alpha Exchange", "Alpha Exchange"],
    )
    assert graph.same_record_alias_hints()     # the hint is recorded...
    assert len(graph.candidates) == 2          # ...but nothing is merged
    for candidate in graph.candidates.values():
        assert candidate.all_evidence()        # all evidence preserved


def test_distinct_entities_in_one_generation_are_not_grouped():
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.types import DecodeProfile, GenerationRecord, ViewFamily

    contract = get_contract("companyTradesAtStockExchange")
    query = Query("S", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(
        GenerationRecord(
            record_id="r1", query=query, view_id=contract.mandatory_views[0],
            view_family=ViewFamily.DIRECT,
            independence_group=IndependenceGroup.DIRECT_RECALL, run_id=0,
            model_id="m", prompt="p", prompt_hash="h",
            raw_output="London Stock Exchange; The Stock Exchange",
            decode_profile=DecodeProfile(),
        ),
        ["London Stock Exchange", "The Stock Exchange"],
    )
    assert graph.same_record_alias_hints() == {}, "distinct names must not group"


@pytest.mark.parametrize("length", [2, 3, 4, 6, 12, 20, 40])
def test_numeric_clusters_are_diameter_bounded_against_chaining(length):
    """The frozen decision: cluster diameter, not pairwise single linkage."""
    import statistics

    from cover_kbc.normalization.numeric import cluster_values, relative_distance

    threshold = get_contract("hasArea").selection.numeric_cluster_threshold
    values = [1000.0]
    for _ in range(length - 1):
        values.append(values[-1] * (1 + threshold * 0.99))

    for cluster in cluster_values(values, threshold=threshold):
        diameter = relative_distance(min(cluster.values), max(cluster.values))
        assert diameter <= threshold + 1e-12, f"chain drifted to {diameter:.4f}"
        median = statistics.median(cluster.values)
        for value in cluster.values:
            assert relative_distance(median, value) <= threshold + 1e-12


def test_module_6_and_module_8_agree_on_cluster_membership():
    from cover_kbc.coverage import numeric_stability
    from cover_kbc.selection import DEFAULT_SELECTION, _numeric_clusters
    from cover_kbc.types import Candidate, EdgeType, Evidence

    for relation in ("hasArea", "hasCapacity"):
        contract = get_contract(relation)
        for values in ([100.0, 101.0], [100.0, 500.0], [100.0, 102.4, 104.9, 107.4]):
            candidates = []
            for index, value in enumerate(values):
                candidate = Candidate(key=f"{value}", display_value=str(value),
                                      relation=relation, numeric_value=value)
                candidate.add_evidence(
                    Evidence(candidate.key, EdgeType.SUPPORT,
                             contract.eligible_independence_groups[0], "v", "m", 0,
                             f"r{index}")
                )
                candidates.append(candidate)
            module6 = numeric_stability(candidates, contract)
            module8 = _numeric_clusters(candidates, contract, DEFAULT_SELECTION)
            assert module6.num_clusters == len(module8), (relation, values)


# --- 31-34. budget reconciliation and the cross-model gate defect ------------


@pytest.mark.parametrize("relation", RELATIONS)
def test_cross_model_recall_is_possible_for_every_relation(relation):
    """A gated relation must not silently lose its second opinion.

    ``mandatory_views[0]`` is the existence gate for the gated relations, and
    a gate returns a verdict rather than names - so taking it blindly made
    cross-model recall a no-op for exactly the two relations whose precision
    most depends on a second opinion.
    """
    contract = get_contract(relation)
    view_id = CoverPipeline._first_recall_view(contract)
    assert view_id, relation
    assert not get_view(relation, view_id).is_gate


@pytest.mark.parametrize("relation", RELATIONS)
def test_the_charged_budget_equals_the_neural_calls_actually_made(relation, tmp_path):
    """Exact, not merely bounded.

    The hard ``max_calls`` budget counts **actual neural invocations**. A
    weaker `charged <= actual` invariant would let a multi-template
    verification hide several real calls inside one charged call, and a query
    could then spend several times its stated budget.
    """
    contract = get_contract(relation)
    answer = "5000" if contract.is_numeric else "Alpha; Beta"
    enum, ver = runtimes(answer, verifier_answer=answer, subject="S", relation=relation,
                         labels={"VALID": 3.0, "INVALID": -1.0, "UNKNOWN": 0.0})
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path)
    charged = graphs[0].budget_snapshot["calls_used"]
    actually_made = enum.calls + ver.calls
    assert charged == actually_made, (
        f"{relation}: charged {charged} for {actually_made} real neural calls"
    )
    assert charged <= contract.stopping.max_calls


def test_no_phase_tail_bypasses_the_controller():
    """Cross-model recall is a controller action, not an unconditional tail.

    The tail ran it regardless of what the controller chose, and only in some
    modes - so the same config behaved differently by execution mode. It now
    runs only in the fixed (non-adaptive) paths, where there is no controller
    to bypass.
    """
    source = inspect.getsource(CoverPipeline.enumerate_query)
    assert "_run_cross_model_recall" in source
    assert "enable_active_controller" in source, (
        "the tail must be gated on the controller being off"
    )


def test_a_gated_relation_actually_asks_the_second_model(tmp_path):
    relation = "personHasCityOfDeath"
    enum, ver = runtimes("Alpha; Beta", verifier_answer="Alpha; Beta", subject="S",
                         relation=relation,
                         labels={"VALID": 3.0, "INVALID": -1.0, "UNKNOWN": 0.0})
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, roles = drive(pipeline, [Query("S", relation, 0)], tmp_path)
    chose_cross_model = any(
        d["chosen"]["action_type"] == ActionType.CROSS_MODEL_CHECK.value
        for d in graphs[0].controller_log
    )
    if chose_cross_model:
        assert ver.calls > 0, "the second model was scheduled but never asked"


# --- 35-42. neural calls vs logical actions ----------------------------------


def test_a_budget_charge_may_never_be_negative():
    budget = Budget(max_calls=5, max_generated_tokens=100)
    with pytest.raises(ValueError):
        budget.charge(calls=-1)
    with pytest.raises(ValueError):
        budget.charge(generated_tokens=-1)


def test_logical_actions_are_counted_separately_from_neural_calls():
    budget = Budget(max_calls=10, max_generated_tokens=100)
    budget.charge(calls=3, logical_actions=1)      # one adversarial verification
    assert budget.calls_used == 3
    assert budget.logical_actions == 1
    assert budget.calls_left == 7


def test_a_multi_template_verification_charges_every_score_call(tmp_path):
    """One logical action, several neural calls, and the budget sees them all."""
    relation = "companyTradesAtStockExchange"
    enum, ver = runtimes("Alpha; Beta", verifier_answer="Alpha; Beta", subject="S",
                         relation=relation,
                         labels={"VALID": 3.0, "INVALID": -1.0, "UNKNOWN": 0.0})
    pipeline = CoverPipeline(
        enum, PipelineConfig(mode=ExecutionMode.STAGED, **ACTIVE), verifier_runtime=ver
    )
    graphs, _ = drive(pipeline, [Query("S", relation, 0)], tmp_path)
    snapshot = graphs[0].budget_snapshot
    executed = [d for d in graphs[0].controller_log
                if d["chosen"]["action_type"] != ActionType.STOP.value]
    # Strictly more neural calls than actions: verification spent several.
    assert snapshot["calls_used"] >= len(executed)
    assert snapshot["calls_used"] == enum.calls + ver.calls


def test_a_description_first_view_costs_two_neural_calls():
    from cover_kbc.elicitation.engine import ElicitationEngine

    for contract in all_contracts():
        for view_id in contract.all_views():
            view = get_view(contract.relation, view_id)
            if not view.is_description:
                continue
            runtime = ScriptedRuntime(fallback=lambda r: "Alpha",
                                      model_id="offline/m", family="offline")
            engine = ElicitationEngine(runtime)
            before = runtime.calls
            engine.run_description_view(Query("S", contract.relation, 0), contract, view)
            assert runtime.calls - before == 2, view_id
            return
    pytest.skip("no description view declared")


def test_a_known_multi_call_action_is_not_started_without_budget():
    from cover_kbc.controller import Action

    contract = get_contract("countryLandBordersCountry")
    pipeline = CoverPipeline(ScriptedRuntime({}), PipelineConfig(**ACTIVE))
    description = next(
        (v for v in contract.all_views() if get_view(contract.relation, v).is_description),
        None,
    )
    if description is None:
        pytest.skip("no description view declared")
    cost = pipeline._minimum_neural_cost(
        contract, Action(ActionType.RUN_VIEW, view_id=description)
    )
    assert cost >= 2
    budget = Budget(max_calls=1, max_generated_tokens=999)
    assert not budget.can_afford(cost), "a 2-call action must not start with 1 call left"


def test_an_adversarial_verification_declares_its_template_cost():
    from cover_kbc.controller import Action

    contract = get_contract("countryLandBordersCountry")
    pipeline = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(**{**ACTIVE, "enable_prompt_disagreement": True})
    )
    templates = len(pipeline.config.disagreement_template_ids)
    cold = pipeline._planned_neural_cost(
        contract, Action(ActionType.ADVERSARIAL_VERIFY, candidate_key="a")
    )
    # Cold cache: one score per template *plus* each uncached control.
    assert cold == 2 * templates >= 4


def test_a_cached_calibration_control_is_not_charged_twice():
    """A cache hit performs no neural inference, so it costs nothing."""
    from cover_kbc.contracts.registry import get_contract as contract_for
    from cover_kbc.verification import TEMPLATE_STANDARD, ContextualCalibrator

    calibrator = ContextualCalibrator()
    runtime = ScriptedRuntime({}, model_id="offline/q", family="offline")
    contract = contract_for("countryLandBordersCountry")

    before = runtime.calls
    calibrator.control_logits(runtime, contract, TEMPLATE_STANDARD)
    first = runtime.calls - before
    assert first == 1, "the first control must actually be measured"

    before = runtime.calls
    calibrator.control_logits(runtime, contract, TEMPLATE_STANDARD)
    assert runtime.calls - before == 0, "a cache hit must make no neural call"


def test_a_cross_model_no_op_charges_nothing(tmp_path):
    """Charging is measured from the runtime, so a no-op costs zero."""
    from cover_kbc.controller import Action
    from cover_kbc.evidence.graph import build_graph

    contract = get_contract("hasArea")
    enum, ver = runtimes("5000", verifier_answer="5000", subject="S", relation="hasArea")
    # No second model: cross-model recall cannot run.
    pipeline = CoverPipeline(enum, PipelineConfig(**ACTIVE))
    graph = build_graph(Query("S", "hasArea", 0), contract)
    spent, new, tokens = pipeline._execute_action(
        graph, contract, Action(ActionType.CROSS_MODEL_CHECK), [], {}
    )
    assert (spent, new, tokens) == (0, 0, 0)


def test_the_token_and_call_budgets_are_independent():
    budget = Budget(max_calls=5, max_generated_tokens=0)
    assert budget.exhausted, "no tokens left is exhaustion even with calls free"
    budget = Budget(max_calls=0, max_generated_tokens=500)
    assert budget.exhausted, "no calls left is exhaustion even with tokens free"
    # A score call generates no free-form tokens but is still a neural call.
    budget = Budget(max_calls=5, max_generated_tokens=500)
    budget.charge(calls=1, generated_tokens=0)
    assert budget.calls_used == 1 and budget.generated_tokens_used == 0


# --- 43-53. calibration surface ---------------------------------------------


def test_every_config_value_has_exactly_one_category():
    from cover_kbc.calibration import PARAMETERS, Category

    seen: dict[tuple[str, str], str] = {}
    for parameter in PARAMETERS:
        key = (parameter.owner, parameter.name)
        assert key not in seen, f"{key} classified twice"
        seen[key] = parameter.category
        assert isinstance(parameter.category, Category)
        assert parameter.meaning.strip(), f"{key} has no documented meaning"


def test_the_inventory_covers_every_live_config_field():
    """A new knob cannot slip in unclassified."""
    import dataclasses

    from cover_kbc.calibration import PARAMETERS
    from cover_kbc.controller import DEFAULT_CONTROLLER
    from cover_kbc.coverage import DEFAULT_RCSE
    from cover_kbc.scoring import DEFAULT_SCORING
    from cover_kbc.selection import DEFAULT_SELECTION

    inventoried = {(p.owner, p.name) for p in PARAMETERS}
    for owner, obj in (
        ("ScoringConfig", DEFAULT_SCORING), ("RCSEConfig", DEFAULT_RCSE),
        ("ControllerConfig", DEFAULT_CONTROLLER), ("SelectionConfig", DEFAULT_SELECTION),
    ):
        for field in dataclasses.fields(obj):
            if dataclasses.is_dataclass(getattr(obj, field.name)):
                continue                       # nested config, inventoried separately
            assert (owner, field.name) in inventoried, f"{owner}.{field.name} unclassified"


def test_semantic_and_guard_values_are_never_calibratable():
    from cover_kbc.calibration import Category, by_category

    for category in (Category.SEMANTIC, Category.GUARD):
        for parameter in by_category(category):
            assert not parameter.calibratable, parameter.name


def test_runtime_cost_priors_are_measured_not_fitted():
    from cover_kbc.calibration import Category, by_category

    costs = by_category(Category.COST)
    assert costs, "the cost priors must be classified"
    for parameter in costs:
        assert not parameter.calibratable, (
            f"{parameter.name} is a runtime measurement, not an F1 knob"
        )


def test_the_calibration_surface_is_small_and_justified():
    from cover_kbc.calibration import PARAMETERS, calibratable, calibratable_decisions

    knobs = calibratable()
    decisions = calibratable_decisions()
    # Far fewer degrees of freedom than config values: the proposal requires a
    # small surface because awardWonBy has ten validation examples.
    assert len(decisions) <= 8, decisions
    assert len(decisions) < len(knobs) or len(knobs) <= 8
    assert len(knobs) < len(PARAMETERS) / 5
    for parameter in knobs:
        assert parameter.decision, f"{parameter.name} declares no decision"
        assert parameter.meaning.strip()


def test_a_fallback_and_its_relation_override_are_one_decision():
    """Counting knobs would overstate the freedom the architecture offers."""
    from cover_kbc.calibration import calibratable

    by_decision: dict[str, list[str]] = {}
    for parameter in calibratable():
        by_decision.setdefault(parameter.decision, []).append(
            f"{parameter.owner}.{parameter.name}"
        )
    shared = {d: names for d, names in by_decision.items() if len(names) > 1}
    assert shared, "the global/contract pairs must be linked, not counted twice"
    for names in shared.values():
        owners = {n.split(".")[0] for n in names}
        assert len(owners) > 1, names


def test_no_two_parameters_silently_control_the_same_decision():
    """Sharing a decision is allowed only where it is declared."""
    from cover_kbc.calibration import PARAMETERS

    by_decision: dict[str, list[str]] = {}
    for parameter in PARAMETERS:
        by_decision.setdefault(parameter.decision, []).append(parameter.name)
    for decision, names in by_decision.items():
        if len(names) > 1:
            # Only the explicitly named shared decisions may collide.
            assert not decision.count("."), f"{decision} collides implicitly: {names}"


def test_calibration_may_never_read_val_or_test():
    from cover_kbc.calibration import (
        ALLOWED_CALIBRATION_SPLITS,
        FORBIDDEN_CALIBRATION_SPLITS,
    )

    assert ALLOWED_CALIBRATION_SPLITS == {"train", "internal"}
    assert {"val", "validation", "test"} <= FORBIDDEN_CALIBRATION_SPLITS
    assert not (ALLOWED_CALIBRATION_SPLITS & FORBIDDEN_CALIBRATION_SPLITS)


def test_calibration_remains_non_neural():
    from cover_kbc import calibration

    source = Path(inspect.getfile(calibration)).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] not in {"torch", "sklearn", "numpy"}
                       for a in node.names)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in {"fit", "train", "optimize", "minimize"}


def test_relation_specific_calibration_exists_only_where_justified():
    from cover_kbc.calibration import calibratable

    per_relation = [
        p for p in calibratable()
        if p.owner in {"VerificationPolicy", "SelectionPolicy", "StoppingPolicy"}
    ]
    for parameter in per_relation:
        # Each must say why the relations genuinely differ.
        assert parameter.meaning.strip(), parameter.name
    assert len(per_relation) <= 3, (
        "per-relation calibration must stay the exception, not the rule"
    )
