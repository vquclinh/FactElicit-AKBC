"""End-to-end pipeline behaviour, model runtime, and the budget audit."""

from __future__ import annotations

import json

import pytest

from cover_kbc.data.writer import write_predictions
from cover_kbc.evaluation.harness import evaluate_predictions
from cover_kbc.models.base import (
    GenerationRequest,
    LabelScoreRequest,
    LogitsUnavailable,
    ModelSpec,
    softmax,
)
from cover_kbc.models.budget import PARAMETER_BUDGET, audit_parameter_budget
from cover_kbc.models.offline import NullRuntime, ScriptedRuntime
from cover_kbc.models.registry import build_runtime, spec_from_config
from cover_kbc.pipeline import CoverPipeline
from cover_kbc.types import Query
from cover_kbc.verification import build_verifier_prompt, read_labels


# --- model runtime ---------------------------------------------------------


def test_null_runtime_abstains():
    runtime = NullRuntime()
    result = runtime.generate(GenerationRequest(prompt="anything"))
    assert result.text == "NONE"
    assert not runtime.spec.is_neural


def test_scripted_runtime_replays_in_order():
    script = {("borders_direct", "Testland", "countryLandBordersCountry"): ["Alpha", "Beta"]}
    runtime = ScriptedRuntime(script)
    metadata = {
        "view_id": "borders_direct",
        "subject": "Testland",
        "relation": "countryLandBordersCountry",
    }
    first = runtime.generate(GenerationRequest(prompt="p", metadata=metadata))
    second = runtime.generate(GenerationRequest(prompt="p", metadata=metadata))
    assert (first.text, second.text) == ("Alpha", "Beta")


def test_scripted_runtime_abstains_for_unknown_keys():
    runtime = ScriptedRuntime({})
    assert runtime.generate(GenerationRequest(prompt="p", metadata={})).text == "NONE"


def test_backend_without_logits_raises_a_typed_error():
    class NoLogits(NullRuntime):
        def score_labels(self, request):
            raise LogitsUnavailable("nope")

    with pytest.raises(LogitsUnavailable):
        NoLogits().score_labels(LabelScoreRequest(prompt="p", labels={"VALID": "A"}))


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="Unknown model backend"):
        build_runtime({"backend": "telepathy"})


def test_huggingface_backend_refuses_an_unrecorded_parameter_count():
    with pytest.raises(ValueError, match="published_total_parameters"):
        build_runtime({"backend": "huggingface", "model_id": "some/model"})


# --- budget audit ----------------------------------------------------------


def test_budget_audit_passes_within_the_limit():
    audit = audit_parameter_budget([ModelSpec("m", 9_000_000_000)])
    assert audit.passed
    assert audit.total_parameters == 9_000_000_000


def test_budget_audit_fails_over_the_limit():
    audit = audit_parameter_budget([ModelSpec("a", 24_000_000_000), ModelSpec("b", 27_000_000_000)])
    assert not audit.passed
    assert "exceeds" in " ".join(audit.problems)


def test_budget_audit_fails_on_an_unknown_count():
    """An unrecorded count is a failure, never a pass."""
    audit = audit_parameter_budget([ModelSpec("mystery", None)])
    assert not audit.passed
    assert "unrecorded" in " ".join(audit.problems)


def test_a_shared_checkpoint_is_counted_once():
    audit = audit_parameter_budget(
        [
            ModelSpec("shared", 24_000_000_000, role="generator"),
            ModelSpec("shared", 24_000_000_000, role="verifier"),
        ]
    )
    assert audit.total_parameters == 24_000_000_000
    assert audit.passed


def test_quantization_does_not_reduce_the_counted_size():
    plain = audit_parameter_budget([ModelSpec("m", 27_000_000_000)])
    quantized = audit_parameter_budget([ModelSpec("m", 27_000_000_000, quantization="int4")])
    assert plain.total_parameters == quantized.total_parameters


def test_offline_stub_profile_is_not_counted_as_neural():
    spec = spec_from_config({"backend": "null", "model_id": "offline/null"})
    assert not spec.is_neural
    assert audit_parameter_budget([spec]).passed


def test_parameter_budget_constant():
    assert PARAMETER_BUDGET == 32_000_000_000


# --- verifier interface ----------------------------------------------------


def test_verifier_prompt_hides_the_generator_reasoning(borders_query, borders_contract):
    prompt = build_verifier_prompt(borders_query, borders_contract, "Alpha")
    assert "Alpha" in prompt
    assert "A = VALID" in prompt and "B = INVALID" in prompt and "C = UNKNOWN" in prompt
    assert "reasoning" not in prompt.lower()


def test_read_labels_is_marked_uncalibrated():
    from cover_kbc.models.base import LabelScoreResult

    result = read_labels(
        LabelScoreResult(logits={"VALID": 2.0, "INVALID": 0.0, "UNKNOWN": -1.0}, model_id="m")
    )
    assert result.label.value == "VALID"
    assert result.calibrated is False
    assert result.valid_prob == pytest.approx(softmax({"VALID": 2.0, "INVALID": 0.0, "UNKNOWN": -1.0})["VALID"])


def test_calibration_is_not_silently_faked():
    from cover_kbc.verification import calibrate, prompt_disagreement

    with pytest.raises(NotImplementedError):
        calibrate()
    with pytest.raises(NotImplementedError):
        prompt_disagreement()


# --- pipeline --------------------------------------------------------------


def test_pipeline_produces_one_prediction_per_query_in_order():
    queries = [
        Query("Testland", "countryLandBordersCountry", 0),
        Query("Testisland", "hasArea", 1),
        Query("Testperson", "personHasCityOfDeath", 2),
    ]
    result = CoverPipeline(NullRuntime()).run(queries)
    assert [(p.subject, p.relation) for p in result.predictions] == [
        (q.subject, q.relation) for q in queries
    ]
    assert all(p.object_entities == [] for p in result.predictions)


def test_pipeline_collects_multi_view_evidence():
    subject = "Testland"
    relation = "countryLandBordersCountry"
    script = {
        ("borders_direct", subject, relation): ["Alpha; Beta"],
        ("borders_compass", subject, relation): ["Alpha; Gamma"],
    }
    result = CoverPipeline(ScriptedRuntime(script)).run([Query(subject, relation, 0)])
    prediction = result.predictions[0]

    assert sorted(prediction.object_entities) == ["Alpha", "Beta", "Gamma"]
    by_key = {c.key: c for c in prediction.candidates}
    # Alpha came from two structurally different views; Beta from only one.
    assert by_key["alpha"].independent_support == 2
    assert by_key["beta"].independent_support == 1


def test_pipeline_stops_discovery_when_a_gate_answers_no():
    subject, relation = "Testperson", "personHasCityOfDeath"
    script = {
        ("death_status_gate", subject, relation): ["NO"],
        ("death_city_direct", subject, relation): ["Testville"],
    }
    runtime = ScriptedRuntime(script)
    result = CoverPipeline(runtime).run([Query(subject, relation, 0)])

    assert result.predictions[0].object_entities == []
    assert result.predictions[0].stopped_reason == "gate_negative"
    # The gate short-circuits, so the discovery view is never paid for.
    assert runtime.calls == 1


def test_pipeline_survives_a_backend_failure():
    class Exploding(NullRuntime):
        def generate(self, request):
            raise RuntimeError("backend exploded")

    result = CoverPipeline(Exploding()).run([Query("Testland", "countryLandBordersCountry", 0)])
    assert result.predictions[0].object_entities == []


def test_pipeline_is_deterministic():
    script = {("borders_direct", "Testland", "countryLandBordersCountry"): ["Gamma; Alpha; Beta"]}
    query = Query("Testland", "countryLandBordersCountry", 0)
    first = CoverPipeline(ScriptedRuntime(script)).run([query]).predictions[0]
    second = CoverPipeline(ScriptedRuntime(script)).run([query]).predictions[0]
    assert first.object_entities == second.object_entities


def test_pipeline_output_is_evaluator_valid(tmp_path):
    subject, relation = "Testcorp", "companyTradesAtStockExchange"
    script = {
        ("stock_listing_gate", subject, relation): ["YES"],
        ("stock_exchange_direct", subject, relation): ["Alpha Stock Exchange; The Alpha Stock Exchange"],
    }
    query = Query(subject, relation, 0)
    result = CoverPipeline(ScriptedRuntime(script)).run([query])

    path = write_predictions(result.predictions, tmp_path / "p.jsonl", expected_queries=[query])
    rows = [json.loads(line) for line in path.read_text().splitlines()]

    # The alias-like duplicate was collapsed before it reached the file.
    assert rows[0]["ObjectEntities"] == ["Alpha Stock Exchange"]

    gold = [
        {
            "SubjectEntity": subject,
            "Relation": relation,
            "ObjectEntities": [["Alpha Stock Exchange", "ASE"]],
        }
    ]
    report = evaluate_predictions(rows, gold)
    assert report.overall_macro_f1 == pytest.approx(1.0)


def test_numeric_pipeline_emits_a_bare_numeral():
    subject, relation = "Testisland", "hasArea"
    script = {
        ("area_direct_km2", subject, relation): ["5000 km2"],
        ("area_total_vs_land", subject, relation): ["5050 square kilometres"],
    }
    result = CoverPipeline(ScriptedRuntime(script)).run([Query(subject, relation, 0)])
    values = result.predictions[0].object_entities
    assert len(values) == 1
    assert float(values[0]) == pytest.approx(5025.0, abs=30)


def test_malformed_model_output_still_produces_a_valid_row():
    """Junk in must not crash the run, and must not leak markup into the output.

    A garbled fragment that still looks like a name is not something the parser
    can reject - that is the verifier's job. What is asserted here is that the
    row stays submittable and the refusal contributes nothing.
    """
    from cover_kbc.data.schema import validate_prediction_row

    subject, relation = "Testland", "countryLandBordersCountry"
    script = {
        ("borders_direct", subject, relation): ["```json\n{{{ Alpha"],
        ("borders_compass", subject, relation): ["I'm sorry, I cannot help with that."],
    }
    result = CoverPipeline(ScriptedRuntime(script)).run([Query(subject, relation, 0)])
    prediction = result.predictions[0]

    validate_prediction_row(prediction.to_official_row(), index=0)
    assert prediction.object_entities == ["Alpha"]
    assert not any("sorry" in value.lower() for value in prediction.object_entities)


def test_pure_refusals_yield_an_empty_prediction():
    subject, relation = "Testland", "countryLandBordersCountry"
    script = {
        ("borders_direct", subject, relation): ["I'm sorry, I cannot help with that."],
        ("borders_compass", subject, relation): ["As an AI, I do not have that information."],
    }
    result = CoverPipeline(ScriptedRuntime(script)).run([Query(subject, relation, 0)])
    assert result.predictions[0].object_entities == []


def test_every_emitted_candidate_traces_back_to_a_generation_record():
    """Spec invariant 3: no untraceable prediction."""
    subject, relation = "Testland", "countryLandBordersCountry"
    script = {("borders_direct", subject, relation): ["Alpha; Beta"]}
    result = CoverPipeline(ScriptedRuntime(script)).run([Query(subject, relation, 0)])
    prediction = result.predictions[0]
    emitted = [c for c in prediction.candidates if c.display_value in prediction.object_entities]
    assert emitted
    for candidate in emitted:
        assert candidate.record_ids
        assert candidate.all_evidence()
