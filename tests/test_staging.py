"""Staged execution: graph persistence and the three-phase pipeline.

Uses ScriptedRuntime with synthetic logits throughout. No model is loaded.
"""

from __future__ import annotations

import pytest

from cover_kbc.evaluation.harness import evaluate_predictions
from cover_kbc.evidence.graph import build_graph
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
from cover_kbc.staging import (
    StageError,
    StageWriter,
    check_stage_matches,
    graph_from_json,
    graph_to_json,
    read_stage,
    stage_summary,
    write_stage,
)
from cover_kbc.types import (
    DecodeProfile,
    EvidenceMode,
    GenerationRecord,
    IndependenceGroup,
    ModelRole,
    Query,
    VerificationLabel,
    VerificationResult,
    ViewFamily,
)


def make_graph(subject="Testland", relation="countryLandBordersCountry"):
    from cover_kbc.contracts.registry import get_contract

    query = Query(subject, relation, 0)
    graph = build_graph(query, get_contract(relation))
    record = GenerationRecord(
        record_id="r1",
        query=query,
        view_id="borders_direct",
        view_family=ViewFamily.DIRECT,
        independence_group=IndependenceGroup.DIRECT_RECALL,
        run_id=0,
        model_id="offline/scripted",
        prompt="p",
        prompt_hash="h",
        raw_output="Alpha; Beta",
        decode_profile=DecodeProfile(),
        facet_id="borders_direct",
        model_family="mistral",
        model_role=ModelRole.ENUMERATOR,
        generated_tokens=12,
        prompt_tokens=30,
    )
    graph.add_entity_mentions(record, ["Alpha", "Beta"])
    return graph


# --- serialisation round-trip ---------------------------------------------


def test_graph_round_trips_through_json():
    original = make_graph()
    restored = graph_from_json(graph_to_json(original))

    assert restored.query.subject == original.query.subject
    assert set(restored.candidates) == set(original.candidates)
    assert len(restored.records) == len(original.records)
    for key, candidate in original.candidates.items():
        other = restored.candidates[key]
        assert other.independent_support == candidate.independent_support
        assert other.raw_support_count == candidate.raw_support_count
        assert other.facet_ids == candidate.facet_ids
        assert other.display_value == candidate.display_value


def test_round_trip_preserves_evidence_mode_and_model_family():
    original = make_graph()
    restored = graph_from_json(graph_to_json(original))
    edges = restored.candidates["alpha"].all_evidence()
    assert edges[0].model_family == "mistral"
    assert edges[0].mode is EvidenceMode.INDEPENDENT_RECALL


def test_round_trip_preserves_verification_evidence():
    graph = make_graph()
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha",
            label=VerificationLabel.VALID,
            valid_prob=0.8, invalid_prob=0.1, unknown_prob=0.1,
            raw_logits={"VALID": 2.0, "INVALID": 0.0, "UNKNOWN": 0.0},
            calibrated_logits={"VALID": 1.5, "INVALID": 0.0, "UNKNOWN": 0.0},
            calibrated=True, prompt_disagreement=0.2,
            model_id="qwen", model_family="qwen",
        )
    )
    restored = graph_from_json(graph_to_json(graph))
    verification = restored.candidates["alpha"].verifications[0]
    assert verification.calibrated is True
    assert verification.valid_prob == pytest.approx(0.8)
    assert verification.prompt_disagreement == pytest.approx(0.2)
    assert verification.model_family == "qwen"


def test_prompts_are_omitted_unless_requested():
    graph = make_graph()
    assert "prompt" not in graph_to_json(graph)["records"][0]
    assert "prompt" in graph_to_json(graph, keep_prompts=True)["records"][0]


def test_a_foreign_stage_version_is_rejected():
    payload = graph_to_json(make_graph())
    payload["version"] = 999
    with pytest.raises(StageError, match="version"):
        graph_from_json(payload)


# --- stage files -----------------------------------------------------------


def test_stage_file_round_trips_in_order(tmp_path):
    graphs = [
        make_graph("Alphaland"),
        make_graph("Betaland"),
        make_graph("Gammaland"),
    ]
    path = write_stage(graphs, tmp_path / "stage.jsonl")
    restored = list(read_stage(path))
    assert [g.query.subject for g in restored] == ["Alphaland", "Betaland", "Gammaland"]


def test_stage_summary_reports_contents(tmp_path):
    path = write_stage([make_graph("A"), make_graph("B")], tmp_path / "s.jsonl")
    summary = stage_summary(path)
    assert summary["graphs"] == 2
    assert summary["candidates"] == 4
    assert summary["relations"] == {"countryLandBordersCountry": 2}


def test_stage_mismatch_is_detected(tmp_path):
    path = write_stage([make_graph("A")], tmp_path / "s.jsonl")
    with pytest.raises(StageError, match="does not match"):
        check_stage_matches(path, [Query("A", "countryLandBordersCountry"), Query("B", "countryLandBordersCountry")])


def test_missing_stage_file_is_reported(tmp_path):
    with pytest.raises(StageError, match="not found"):
        list(read_stage(tmp_path / "absent.jsonl"))


def test_stage_writer_is_a_context_manager(tmp_path):
    path = tmp_path / "s.jsonl"
    with StageWriter(path) as writer:
        writer.write(make_graph())
        assert writer.count == 1
    assert len(path.read_text().strip().splitlines()) == 1


# --- three-phase pipeline --------------------------------------------------


SUBJECT = "Testland"
RELATION = "countryLandBordersCountry"


def staged_config(**overrides):
    base = dict(
        mode=ExecutionMode.STAGED,
        enable_verifier=True,
        max_verifications_per_query=3,
        use_calibration=True,
        enable_cross_model_recall=True,
    )
    base.update(overrides)
    return PipelineConfig(**base)


def enumerator():
    return ScriptedRuntime(
        {
            ("borders_direct", SUBJECT, RELATION): ["Alpha; Beta"],
            ("borders_compass", SUBJECT, RELATION): ["Alpha; Gamma"],
        },
        model_id="offline/mistral",
        family="mistral",
        role="enumerator",
    )


def verifier(label_scores=None):
    return ScriptedRuntime(
        {("borders_direct", SUBJECT, RELATION): ["Alpha; Delta"]},
        model_id="offline/qwen",
        family="qwen",
        role="verifier",
        label_scores=label_scores or {},
    )


def test_phase_a_produces_candidates_without_the_verifier(tmp_path):
    pipeline = CoverPipeline(enumerator(), staged_config())
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    assert set(graph.candidates) >= {"alpha", "beta", "gamma"}
    # No verifier ran, so nothing carries a verification yet.
    assert all(not c.verifications for c in graph.candidates.values())


def test_phase_b_adds_cross_model_recall_and_verification():
    enum_pipeline = CoverPipeline(enumerator(), staged_config())
    graph = enum_pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))

    verify_pipeline = CoverPipeline(
        enumerator(), staged_config(), verifier_runtime=verifier()
    )
    verify_pipeline.verify_graph(graph)

    # "Delta" was recalled independently by the second family in Phase B.
    assert "delta" in graph.candidates
    delta_edges = graph.candidates["delta"].all_evidence()
    assert any(
        e.independence_group is IndependenceGroup.CROSS_MODEL_RECALL
        and e.mode is EvidenceMode.INDEPENDENT_RECALL
        and e.model_family == "qwen"
        for e in delta_edges
    )
    assert graph.model_family_summary().get("qwen", 0) >= 1


def test_shown_candidate_agreement_is_marked_differently_from_recall():
    graph = CoverPipeline(enumerator(), staged_config()).enumerate_query(
        Query(SUBJECT, RELATION, 0)
    )
    CoverPipeline(
        enumerator(), staged_config(), verifier_runtime=verifier()
    ).verify_graph(graph)

    verified = [c for c in graph.candidates.values() if c.verifications]
    assert verified
    for candidate in verified:
        blind = candidate.groups.get(IndependenceGroup.BLIND_VERIFIER)
        if blind:
            assert all(
                e.mode is EvidenceMode.SHOWN_CANDIDATE for e in blind.all_evidence()
            )


def test_full_staged_run_matches_the_interleaved_run(tmp_path):
    """Splitting into phases must not change the answer."""
    query = Query(SUBJECT, RELATION, 0)

    interleaved = CoverPipeline(
        enumerator(),
        staged_config(mode=ExecutionMode.INTERLEAVED),
        verifier_runtime=verifier(),
    )
    direct = interleaved.run([query]).predictions[0]

    enum_pipeline = CoverPipeline(enumerator(), staged_config())
    path = write_stage(enum_pipeline.enumerate([query]), tmp_path / "a.jsonl")

    verify_pipeline = CoverPipeline(
        enumerator(), staged_config(), verifier_runtime=verifier()
    )
    path_b = write_stage(verify_pipeline.verify(read_stage(path)), tmp_path / "b.jsonl")

    decided = CoverPipeline(enumerator(), staged_config()).decide(read_stage(path_b))
    assert sorted(decided.predictions[0].object_entities) == sorted(direct.object_entities)


def test_phase_c_needs_no_model_and_is_repeatable(tmp_path):
    query = Query(SUBJECT, RELATION, 0)
    path = write_stage(
        CoverPipeline(enumerator(), staged_config()).enumerate([query]), tmp_path / "a.jsonl"
    )
    from cover_kbc.models.offline import NullRuntime

    first = CoverPipeline(NullRuntime(), staged_config()).decide(read_stage(path))
    second = CoverPipeline(NullRuntime(), staged_config()).decide(read_stage(path))
    assert first.predictions[0].object_entities == second.predictions[0].object_entities


def test_staged_output_is_evaluator_valid(tmp_path):
    query = Query(SUBJECT, RELATION, 0)
    path = write_stage(
        CoverPipeline(enumerator(), staged_config()).enumerate([query]), tmp_path / "a.jsonl"
    )
    result = CoverPipeline(enumerator(), staged_config()).decide(read_stage(path))
    rows = [p.to_official_row() for p in result.predictions]
    gold = [
        {
            "SubjectEntity": SUBJECT,
            "Relation": RELATION,
            "ObjectEntities": [["Alpha"], ["Beta"], ["Gamma"]],
        }
    ]
    report = evaluate_predictions(rows, gold)
    assert report.overall_macro_f1 > 0.0


def test_controller_log_survives_staging(tmp_path):
    query = Query(SUBJECT, RELATION, 0)
    pipeline = CoverPipeline(enumerator(), staged_config(enable_active_controller=True))
    path = write_stage(pipeline.enumerate([query]), tmp_path / "a.jsonl")
    restored = next(iter(read_stage(path)))
    assert restored.controller_log
    assert restored.budget_snapshot.get("calls_used", 0) >= 1
