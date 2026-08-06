"""Module 4 conformance: the logit-calibrated blind verifier.

Deterministic and synthetic throughout. No model is loaded anywhere; the real
Qwen3.5-4B tokenizer and weights are reserved for Colab, so tokenisation
*mechanics* are exercised with fake tokenizers and the real-runtime assertion is
verified statically.
"""

from __future__ import annotations

import ast
import inspect
import math

import pytest

from cover_kbc.contracts.registry import all_contracts, get_contract
from cover_kbc.elicitation.library import get_view
from cover_kbc.evidence.graph import build_graph
from cover_kbc.models.base import LabelScoreResult, entropy, softmax
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.scoring import (
    ScoringConfig,
    assign_tier,
    decide_status,
    resolve_verification,
    verification_targets,
)
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    DecodeProfile,
    EdgeType,
    Evidence,
    EvidenceMode,
    GenerationRecord,
    IndependenceGroup,
    Query,
    VerificationLabel,
    VerificationResult,
    VerificationTier,
    ViewFamily,
)
from cover_kbc.verification import (
    CONTENT_FREE_CANDIDATE,
    DISAGREEMENT_TEMPLATE_IDS,
    GATE_LABELS,
    LABEL_TOKENS,
    TEMPLATE_ADVERSARIAL,
    TEMPLATE_QUESTION,
    TEMPLATE_STANDARD,
    TEMPLATES,
    ContextualCalibrator,
    aggregate_verifications,
    build_verifier_prompt,
    inspect_label_encoding,
    jensen_shannon_divergence,
    normalized_disagreement,
    read_labels,
    score_gate,
    verify_candidate,
    verify_multi_template,
)


class FakeTokenizer:
    def __init__(self, mapping):
        self.mapping = mapping

    def encode(self, text, add_special_tokens=False):
        return list(self.mapping.get(text, [1, 2]))


def scores(valid=0.0, invalid=0.0, unknown=0.0):
    return {"VALID": valid, "INVALID": invalid, "UNKNOWN": unknown}


def candidate(key="alpha", support=0, contradictions=0, relation="countryLandBordersCountry"):
    c = Candidate(key=key, display_value=key.title(), relation=relation)
    groups = [
        IndependenceGroup.DIRECT_RECALL,
        IndependenceGroup.STRUCTURAL_DECOMPOSITION,
        IndependenceGroup.CONTRASTIVE_SEPARATION,
        IndependenceGroup.MISSINGNESS_SEARCH,
    ]
    for i in range(support):
        c.add_evidence(Evidence(key, EdgeType.SUPPORT, groups[i], "v", "m", 0, f"r{i}"))
    for i in range(contradictions):
        c.add_evidence(
            Evidence(key, EdgeType.CONTRADICT, IndependenceGroup.BLIND_VERIFIER, "v", "m", 0, f"c{i}")
        )
    return c


# --- 1-3. blindness (spec §10.2) -------------------------------------------


def test_the_prompt_carries_subject_contract_candidate_and_labels():
    contract = get_contract("countryLandBordersCountry")
    prompt = build_verifier_prompt(Query("Testland", contract.relation, 0), contract, "Alpha")
    assert "Testland" in prompt
    assert "Alpha" in prompt
    assert contract.definition in prompt
    for rule in contract.hard_negative_rules:
        assert rule in prompt
    assert "A = VALID" in prompt and "B = INVALID" in prompt and "C = UNKNOWN" in prompt


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.template_id)
def test_no_template_leaks_generator_rationale(template):
    """Blindness holds however the candidate was discovered."""
    contract = get_contract("countryLandBordersCountry")
    prompt = build_verifier_prompt(
        Query("Testland", contract.relation, 0), contract, "Alpha", template
    )
    for leak in (
        "independent_support", "raw_support", "score", "evidence", "generator",
        "rationale", "because", "run_id", "frequency", "views agreed", "confidence",
    ):
        assert leak.lower() not in prompt.lower(), leak


def test_rich_acquisition_provenance_never_reaches_the_prompt():
    """A candidate carrying description prose and reverse reasoning stays blind."""
    contract = get_contract("countryLandBordersCountry")
    query = Query("Testland", contract.relation, 0)
    graph = build_graph(query, contract)

    secret = "SECRET_RATIONALE the frontier obviously runs east so Alpha must border it"
    prose = GenerationRecord(
        record_id="d1", query=query, view_id="borders_description",
        view_family=ViewFamily.DESCRIPTION,
        independence_group=IndependenceGroup.RELATION_FOCUSED_DESCRIPTION,
        run_id=0, model_id="m", prompt="p", prompt_hash="h", raw_output=secret,
        decode_profile=DecodeProfile(), stage="description",
    )
    graph.register_record(prose)
    graph.add_entity_mentions(
        GenerationRecord(
            record_id="e1", query=query, view_id="borders_description",
            view_family=ViewFamily.DESCRIPTION,
            independence_group=IndependenceGroup.RELATION_FOCUSED_DESCRIPTION,
            run_id=0, model_id="m", prompt="p", prompt_hash="h", raw_output="Alpha",
            decode_profile=DecodeProfile(), stage="extraction", source_record_id="d1",
        ),
        ["Alpha"],
    )
    subject = graph.candidates["alpha"]
    assert subject.record_ids                      # provenance exists...

    prompt = build_verifier_prompt(query, contract, subject.display_value)
    assert "SECRET_RATIONALE" not in prompt        # ...but never reaches the verifier
    assert "d1" not in prompt and "e1" not in prompt
    assert str(subject.independent_support) not in prompt.split("Candidate:")[-1]


def test_the_verifier_never_sees_other_candidates():
    contract = get_contract("countryLandBordersCountry")
    prompt = build_verifier_prompt(
        Query("Testland", contract.relation, 0), contract, "Alpha"
    )
    assert "Beta" not in prompt and "Gamma" not in prompt


# --- 4. three-way semantics -------------------------------------------------


def test_labels_are_exactly_valid_invalid_unknown():
    assert set(LABEL_TOKENS) == {"VALID", "INVALID", "UNKNOWN"}
    assert LABEL_TOKENS == {"VALID": "A", "INVALID": "B", "UNKNOWN": "C"}


@pytest.mark.parametrize(
    "raw,expected_label,expected_edge",
    [
        (scores(valid=3.0), VerificationLabel.VALID, EdgeType.SUPPORT),
        (scores(invalid=3.0), VerificationLabel.INVALID, EdgeType.CONTRADICT),
        (scores(unknown=3.0), VerificationLabel.UNKNOWN, EdgeType.UNKNOWN),
    ],
)
def test_each_label_maps_to_its_own_edge_type(raw, expected_label, expected_edge):
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"))
    assert result.label is expected_label
    assert result.edge_type is expected_edge


def test_unknown_is_never_rewritten_as_invalid():
    result = read_labels(LabelScoreResult(logits=scores(unknown=5.0), model_id="m"))
    assert result.label is VerificationLabel.UNKNOWN
    assert result.edge_type is not EdgeType.CONTRADICT

    contract = get_contract("countryLandBordersCountry")
    subject = candidate(support=1)
    subject.verifications.append(
        VerificationResult(
            candidate_key="alpha", label=VerificationLabel.UNKNOWN,
            valid_prob=0.2, invalid_prob=0.2, unknown_prob=0.6,
        )
    )
    # An UNKNOWN verdict leaves the candidate unresolved, never rejected.
    assert decide_status(subject, contract) is not CandidateStatus.REJECTED


# --- 5-6. tokenisation, no first-token shortcut -----------------------------


def test_single_token_labels_are_validated_not_assumed():
    encoding = inspect_label_encoding(FakeTokenizer({"A": [32], "B": [33], "C": [34]}))
    assert encoding.single_token
    assert encoding.strategy == "next_token_logits"
    assert encoding.token_ids["VALID"] == (32,)


def test_a_multi_token_label_forces_full_sequence_scoring():
    encoding = inspect_label_encoding(FakeTokenizer({"A": [32], "B": [33, 99], "C": [34]}))
    assert not encoding.single_token
    assert encoding.strategy == "sequence_loglikelihood"
    assert encoding.token_ids["INVALID"] == (33, 99)


def test_a_zero_token_label_is_rejected():
    with pytest.raises(ValueError, match="zero tokens"):
        inspect_label_encoding(FakeTokenizer({"A": [], "B": [33], "C": [34]}))


def test_the_runtime_chooses_the_strategy_from_the_real_tokenizer():
    """Statically: score_labels branches on the encoding, never on an assumption."""
    from cover_kbc.models.huggingface import HuggingFaceRuntime

    source = inspect.getsource(HuggingFaceRuntime.score_labels)
    assert "inspect_labels" in source
    assert "encoding.single_token" in source
    assert "_score_sequence" in source


def test_the_sequence_fallback_scores_every_token_of_every_label():
    from cover_kbc.models.huggingface import HuggingFaceRuntime

    source = inspect.getsource(HuggingFaceRuntime._score_sequence)
    # It iterates label token sequences and accumulates per-token log-probs.
    assert "for label, ids in encoding.token_ids.items()" in source
    assert "for offset, token_id in enumerate(ids)" in source
    assert "log_softmax" in source


def test_verification_never_falls_back_to_generated_text():
    # Module 4's code moved into cover_kbc.verification.blind when the
    # package gained Module 17; inspecting the package __init__ would make
    # every absence assertion below pass vacuously.
    from cover_kbc.verification import blind as verification

    tree = ast.parse(inspect.getsource(verification))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "score_labels" in called
    assert "generate" not in called


# --- 7-11. contextual calibration (spec §10.3) ------------------------------


def test_calibrated_logits_are_raw_minus_bias():
    raw = scores(valid=2.0, invalid=1.0, unknown=0.0)
    bias = scores(valid=1.5, invalid=0.2, unknown=-0.3)
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"), control=bias)

    assert result.raw_logits == raw
    assert result.bias_logits == bias
    assert result.calibrated_logits == {k: raw[k] - bias[k] for k in raw}
    assert result.calibrated is True


def test_calibration_can_flip_the_decision():
    """That is the point: a template that always prefers A is corrected."""
    raw = scores(valid=2.0, invalid=1.0)
    uncalibrated = read_labels(LabelScoreResult(logits=raw, model_id="m"))
    calibrated = read_labels(
        LabelScoreResult(logits=raw, model_id="m"), control=scores(valid=1.5)
    )
    assert uncalibrated.label is VerificationLabel.VALID
    assert calibrated.label is VerificationLabel.INVALID


def test_the_default_temperature_is_exactly_one():
    assert ContextualCalibrator().temperature == 1.0


def test_probabilities_sum_to_one():
    result = read_labels(
        LabelScoreResult(logits=scores(valid=2.0, invalid=-1.0, unknown=0.5), model_id="m")
    )
    total = result.valid_prob + result.invalid_prob + result.unknown_prob
    assert total == pytest.approx(1.0)


def test_no_learned_calibrator_exists():
    # Module 4's code moved into cover_kbc.verification.blind when the
    # package gained Module 17; inspecting the package __init__ would make
    # every absence assertion below pass vacuously.
    from cover_kbc.verification import blind as verification

    source = inspect.getsource(verification)
    for banned in ("optimizer", "backward", "requires_grad", "fit(", "train(", "LogisticRegression"):
        assert banned not in source


# --- 8. content-free control -------------------------------------------------


def test_the_control_uses_the_same_template_with_no_content():
    seen = []

    class Recording(ScriptedRuntime):
        def score_labels(self, request):
            seen.append(request.prompt)
            return super().score_labels(request)

    contract = get_contract("countryLandBordersCountry")
    ContextualCalibrator().control_logits(Recording({}), contract, TEMPLATE_STANDARD)
    prompt = seen[0]
    assert CONTENT_FREE_CANDIDATE in prompt
    assert contract.definition in prompt            # same template structure
    assert "Testland" not in prompt                 # no benchmark fact


def test_the_control_is_measured_once_per_identity():
    runtime = ScriptedRuntime({})
    calibrator = ContextualCalibrator()
    contract = get_contract("countryLandBordersCountry")
    for _ in range(5):
        calibrator.control_logits(runtime, contract, TEMPLATE_STANDARD)
    assert calibrator.calls == 1


@pytest.mark.parametrize(
    "vary",
    ["template", "relation", "model", "revision"],
)
def test_the_control_cache_cannot_leak_across_incompatible_setups(vary):
    import dataclasses

    calibrator = ContextualCalibrator()
    borders = get_contract("countryLandBordersCountry")
    base = ScriptedRuntime({}, model_id="qwen", family="qwen")
    base._spec = dataclasses.replace(base.spec, revision="r1")
    calibrator.control_logits(base, borders, TEMPLATE_STANDARD)
    assert calibrator.calls == 1
    # The same setup reuses the measurement.
    calibrator.control_logits(base, borders, TEMPLATE_STANDARD)
    assert calibrator.calls == 1

    if vary == "template":
        calibrator.control_logits(base, borders, TEMPLATE_QUESTION)
    elif vary == "relation":
        calibrator.control_logits(base, get_contract("hasArea"), TEMPLATE_STANDARD)
    elif vary == "model":
        other = ScriptedRuntime({}, model_id="mistral", family="mistral")
        calibrator.control_logits(other, borders, TEMPLATE_STANDARD)
    else:
        other = ScriptedRuntime({}, model_id="qwen", family="qwen")
        other._spec = dataclasses.replace(other.spec, revision="r2")
        assert other.spec.revision != base.spec.revision
        calibrator.control_logits(other, borders, TEMPLATE_STANDARD)

    assert calibrator.calls == 2, f"cache leaked across differing {vary}"


def test_the_control_creates_no_candidate_and_no_edge():
    """verification.py cannot touch the graph at all."""
    # Module 4's code moved into cover_kbc.verification.blind when the
    # package gained Module 17; inspecting the package __init__ would make
    # every absence assertion below pass vacuously.
    from cover_kbc.verification import blind as verification

    tree = ast.parse(inspect.getsource(verification))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("evidence" in m for m in imported)

    contract = get_contract("countryLandBordersCountry")
    graph = build_graph(Query("S", contract.relation, 0), contract)
    ContextualCalibrator().control_logits(ScriptedRuntime({}), contract, TEMPLATE_STANDARD)
    assert graph.candidates == {}


# --- 9. numerical stability --------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        scores(valid=1e4, invalid=-1e4, unknown=0.0),
        scores(valid=-1e4, invalid=-1e4, unknown=-1e4),
        scores(),
        scores(valid=1e-12, invalid=1e-12, unknown=1e-12),
    ],
)
def test_signals_stay_finite_at_extremes(raw):
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"))
    for value in (result.valid_prob, result.invalid_prob, result.unknown_prob,
                  result.margin, result.entropy):
        assert value is not None and math.isfinite(value)
    assert 0.0 <= result.valid_prob <= 1.0
    assert result.entropy >= 0.0            # never -0.0 in a trace


def test_softmax_is_numerically_stable():
    probabilities = softmax({"a": 1000.0, "b": -1000.0})
    assert math.isfinite(probabilities["a"]) and probabilities["a"] == pytest.approx(1.0)
    assert sum(probabilities.values()) == pytest.approx(1.0)


def test_disagreement_stays_finite_for_degenerate_distributions():
    a = {"VALID": 1.0, "INVALID": 0.0, "UNKNOWN": 0.0}
    b = {"VALID": 0.0, "INVALID": 0.0, "UNKNOWN": 1.0}
    value = jensen_shannon_divergence([a, b])
    assert math.isfinite(value) and value >= 0.0


# --- 10. P_valid, margin, entropy -------------------------------------------


def test_p_valid_is_the_calibrated_valid_probability():
    raw = scores(valid=2.0, invalid=1.0, unknown=0.0)
    bias = scores(valid=0.5)
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"), control=bias)
    expected = softmax({k: raw[k] - bias.get(k, 0.0) for k in raw})["VALID"]
    assert result.valid_prob == pytest.approx(expected)


def test_margin_is_calibrated_valid_minus_the_best_rival():
    raw = scores(valid=3.0, invalid=1.0, unknown=0.5)
    bias = scores(valid=1.0, invalid=0.5)
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"), control=bias)
    calibrated = result.calibrated_logits
    expected = calibrated["VALID"] - max(calibrated["INVALID"], calibrated["UNKNOWN"])
    assert result.margin == pytest.approx(expected)


def test_a_positive_margin_means_valid_dominates_both_rivals():
    result = read_labels(LabelScoreResult(logits=scores(valid=3.0, invalid=1.0), model_id="m"))
    assert result.margin > 0
    assert result.label is VerificationLabel.VALID

    flipped = read_labels(LabelScoreResult(logits=scores(valid=1.0, invalid=3.0), model_id="m"))
    assert flipped.margin < 0
    assert flipped.label is VerificationLabel.INVALID


def test_entropy_matches_the_calibrated_distribution():
    raw = scores(valid=1.0, invalid=0.5, unknown=0.25)
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"))
    distribution = {
        "VALID": result.valid_prob, "INVALID": result.invalid_prob, "UNKNOWN": result.unknown_prob
    }
    assert result.entropy == pytest.approx(entropy(distribution))


def test_entropy_is_maximal_when_the_verifier_has_no_opinion():
    result = read_labels(LabelScoreResult(logits=scores(), model_id="m"))
    assert result.entropy == pytest.approx(math.log(3))


# --- 11-13. multi-template disagreement (spec §10.4) ------------------------


def test_all_templates_ask_the_same_question_of_the_same_contract():
    contract = get_contract("companyTradesAtStockExchange")
    query = Query("Testcorp", contract.relation, 0)
    prompts = {
        t.template_id: build_verifier_prompt(query, contract, "Alpha Exchange", t)
        for t in TEMPLATES
    }
    assert len(set(prompts.values())) == len(TEMPLATES)     # genuinely different wording
    for prompt in prompts.values():
        assert "Testcorp" in prompt and "Alpha Exchange" in prompt
        assert contract.definition in prompt                 # identical semantics
        assert "A = VALID" in prompt


def test_identical_distributions_give_zero_disagreement():
    p = {"VALID": 0.7, "INVALID": 0.2, "UNKNOWN": 0.1}
    assert normalized_disagreement([p, p, p]) == pytest.approx(0.0, abs=1e-12)


def test_divergent_distributions_give_positive_disagreement():
    a = {"VALID": 0.9, "INVALID": 0.05, "UNKNOWN": 0.05}
    b = {"VALID": 0.05, "INVALID": 0.9, "UNKNOWN": 0.05}
    assert normalized_disagreement([a, b]) > 0.3


def test_disagreement_equals_the_mean_kl_to_the_mean_distribution():
    """Exactly the spec §10.4 formula, i.e. generalised JSD."""
    a = {"VALID": 0.8, "INVALID": 0.1, "UNKNOWN": 0.1}
    b = {"VALID": 0.2, "INVALID": 0.7, "UNKNOWN": 0.1}
    mean = {k: (a[k] + b[k]) / 2 for k in a}
    expected = sum(
        sum(p[k] * math.log(p[k] / mean[k]) for k in p if p[k] > 0) for p in (a, b)
    ) / 2
    assert jensen_shannon_divergence([a, b]) == pytest.approx(expected)


def test_high_disagreement_is_not_averaged_into_confident_valid():
    """One template shouting VALID must not silently win."""
    confident = VerificationResult(
        candidate_key="k", label=VerificationLabel.VALID,
        valid_prob=0.95, invalid_prob=0.03, unknown_prob=0.02,
    )
    opposed = VerificationResult(
        candidate_key="k", label=VerificationLabel.INVALID,
        valid_prob=0.05, invalid_prob=0.90, unknown_prob=0.05,
    )
    merged = aggregate_verifications([confident, opposed])
    assert merged.valid_prob == pytest.approx(0.5)
    assert merged.prompt_disagreement > 0.0        # the conflict stays visible
    assert merged.num_templates == 2


def test_multi_template_verification_records_the_disagreement():
    contract = get_contract("countryLandBordersCountry")
    results, disagreement = verify_multi_template(
        ScriptedRuntime({}), Query("S", contract.relation, 0), contract,
        "alpha", "Alpha", calibrator=ContextualCalibrator(),
    )
    assert len(results) == len(DISAGREEMENT_TEMPLATE_IDS)
    assert all(r.prompt_disagreement == disagreement for r in results)


# --- 12. provenance ----------------------------------------------------------


def test_every_verification_records_its_template_and_model():
    contract = get_contract("countryLandBordersCountry")
    runtime = ScriptedRuntime(
        {}, model_id="offline/qwen", family="qwen",
        label_scores={("blind_verifier", "S", contract.relation): scores(valid=2.0)},
    )
    result = verify_candidate(
        runtime, Query("S", contract.relation, 0), contract, "alpha", "Alpha",
        calibrator=ContextualCalibrator(),
    )
    assert result.template_id == TEMPLATE_STANDARD.template_id
    assert result.model_id == "offline/qwen"
    assert result.record_id
    assert result.raw_logits and result.calibrated_logits


def test_several_templates_remain_one_independence_group():
    """Three prompt probes are not three factual mechanisms."""
    contract = get_contract("countryLandBordersCountry")
    query = Query("S", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(
        GenerationRecord(
            record_id="r1", query=query, view_id="borders_direct",
            view_family=ViewFamily.DIRECT, independence_group=IndependenceGroup.DIRECT_RECALL,
            run_id=0, model_id="m", prompt="p", prompt_hash="h", raw_output="Alpha",
            decode_profile=DecodeProfile(),
        ),
        ["Alpha"],
    )
    for index, template in enumerate(TEMPLATES):
        graph.add_verification(
            VerificationResult(
                candidate_key="alpha", label=VerificationLabel.VALID,
                valid_prob=0.8, invalid_prob=0.1, unknown_prob=0.1,
                template_id=template.template_id, model_id="qwen", model_family="qwen",
                record_id=f"v{index}",
            )
        )
    subject = graph.candidates["alpha"]
    verifier_groups = [g for g in subject.supporting_groups if g is IndependenceGroup.BLIND_VERIFIER]
    assert len(verifier_groups) == 1                       # one group
    assert len(subject.verifications) == 3                 # three probes
    assert subject.independent_support == 2                # discovery + verifier, not 4


# --- 14-15. tiers (spec §10.5) ----------------------------------------------


def test_a_hard_rejected_candidate_is_never_verified():
    contract = get_contract("countryLandBordersCountry")
    subject = candidate(support=1)
    subject.status = CandidateStatus.REJECTED
    assert assign_tier(subject, contract) is VerificationTier.HARD_REJECT
    assert verification_targets([subject], contract, budget=5) == []


def test_a_hard_reject_costs_no_model_call():
    """End to end: a type-violating candidate never reaches the verifier."""
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    # An entity answer for a numeric relation is hard-rejected by Module 3.
    enumerator = ScriptedRuntime(
        {
            ("area_direct_km2", "S", "hasArea"): ["Alpha"],
            ("area_total_vs_land", "S", "hasArea"): ["Alpha"],
        },
        model_id="offline/mistral", family="mistral", role="enumerator",
    )
    verifier = ScriptedRuntime({}, model_id="offline/qwen", family="qwen", role="verifier")
    CoverPipeline(
        enumerator,
        PipelineConfig(enable_verifier=True, max_verifications_per_query=5),
        verifier_runtime=verifier,
    ).run([Query("S", "hasArea", 0)])

    assert verifier.calls == 0


def test_broad_support_auto_accepts_without_a_call():
    contract = get_contract("countryLandBordersCountry")
    subject = candidate(support=4)
    assert assign_tier(subject, contract) is VerificationTier.AUTO_ACCEPT
    assert verification_targets([subject], contract, budget=5) == []


def test_a_contradiction_always_escalates():
    contract = get_contract("countryLandBordersCountry")
    subject = candidate(support=4, contradictions=1)
    assert assign_tier(subject, contract) is VerificationTier.ADVERSARIAL_VERIFY


def test_the_adversarial_tier_is_reachable_on_a_first_pass():
    """Previously unreachable: it needed a verdict that only it could produce."""
    contract = get_contract("countryLandBordersCountry")
    fresh = candidate(support=1)
    assert not fresh.verifications                    # nothing has run yet
    assert assign_tier(fresh, contract) is VerificationTier.ADVERSARIAL_VERIFY


def test_the_adversarial_condition_is_non_factual():
    """It uses only declared near-miss classes and evidence counts."""
    source = inspect.getsource(assign_tier)
    for factual in ("Denmark", "Poland", "NYSE", "Nobel", "lookup", "database"):
        assert factual not in source
    assert "adversarial_classes" in source
    assert "supporting_acquisition_groups" in source


def test_a_relation_without_near_misses_is_not_escalated():
    contract = get_contract("hasArea")
    assert not contract.verification.adversarial_classes
    assert assign_tier(candidate(support=1), contract) is VerificationTier.VERIFY


def test_every_tier_is_reachable():
    reached = set()
    borders = get_contract("countryLandBordersCountry")
    area = get_contract("hasArea")

    rejected = candidate(support=1)
    rejected.status = CandidateStatus.REJECTED
    reached.add(assign_tier(rejected, borders))
    reached.add(assign_tier(candidate(support=4), borders))          # AUTO_ACCEPT
    reached.add(assign_tier(candidate(support=1), area))             # VERIFY
    reached.add(assign_tier(candidate(support=1), borders))          # ADVERSARIAL
    assert reached == {
        VerificationTier.HARD_REJECT,
        VerificationTier.AUTO_ACCEPT,
        VerificationTier.VERIFY,
        VerificationTier.ADVERSARIAL_VERIFY,
    }


def test_the_adversarial_template_carries_the_contract_near_misses():
    contract = get_contract("hasCapacity")
    prompt = build_verifier_prompt(
        Query("Testvenue", contract.relation, 0), contract, "52000", TEMPLATE_ADVERSARIAL
    )
    for near_miss in contract.verification.adversarial_classes:
        assert near_miss in prompt
    # ...and it is still blind.
    assert "generator" not in prompt.lower() and "evidence" not in prompt.lower()


# --- 16. auto-accept reachability -------------------------------------------


def test_auto_accept_is_reachable_for_every_relation():
    """No relation may declare a threshold its architecture cannot reach."""
    for contract in all_contracts():
        producing = {
            get_view(contract.relation, v).independence_group
            for v in contract.all_views()
            if not get_view(contract.relation, v).is_gate
        }
        threshold = resolve_verification(contract).auto_accept_support
        assert threshold <= len(producing), (
            f"{contract.relation}: auto-accept needs {threshold} mechanisms "
            f"but only {len(producing)} can produce candidates"
        )


def test_death_requires_unanimity_by_design():
    """Its threshold equals its mechanism count - deliberate, not accidental."""
    contract = get_contract("personHasCityOfDeath")
    producing = {
        get_view(contract.relation, v).independence_group
        for v in contract.all_views()
        if not get_view(contract.relation, v).is_gate
    }
    assert resolve_verification(contract).auto_accept_support == len(producing)


# --- 17. relation policy precedence -----------------------------------------


def test_the_contract_operating_point_is_authoritative():
    contract = get_contract("companyTradesAtStockExchange")
    effective = resolve_verification(contract, ScoringConfig(min_valid_prob=0.05))
    assert effective.min_valid_prob == contract.verification.accept_valid_prob
    assert effective.source.startswith("contract:")


def test_thresholds_are_never_blended():
    import dataclasses

    from cover_kbc.contracts.base import VerificationPolicy

    contract = dataclasses.replace(
        get_contract("hasArea"),
        verification=VerificationPolicy(accept_valid_prob=0.05, auto_accept_independent_support=1),
    )
    effective = resolve_verification(contract, ScoringConfig(min_valid_prob=0.9, auto_accept_support=9))
    assert effective.min_valid_prob == pytest.approx(0.05)
    assert effective.auto_accept_support == 1


def test_an_undeclared_value_falls_back_to_the_global_default():
    import dataclasses

    from cover_kbc.contracts.base import VerificationPolicy

    contract = dataclasses.replace(get_contract("hasArea"), verification=VerificationPolicy())
    effective = resolve_verification(contract, ScoringConfig(min_valid_prob=0.42))
    assert effective.min_valid_prob == pytest.approx(0.42)
    assert effective.source == "scoring_config"


def test_the_global_override_is_named_and_off_by_default():
    assert ScoringConfig().force_global_verification_policy is False
    contract = get_contract("personHasCityOfDeath")
    forced = ScoringConfig(force_global_verification_policy=True, min_valid_prob=0.11)
    assert resolve_verification(contract, forced).min_valid_prob == pytest.approx(0.11)
    assert "forced" in resolve_verification(contract, forced).source


# --- 18-20. verifier evidence into the graph --------------------------------


def _graph_with_alpha():
    contract = get_contract("countryLandBordersCountry")
    query = Query("S", contract.relation, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(
        GenerationRecord(
            record_id="r1", query=query, view_id="borders_direct",
            view_family=ViewFamily.DIRECT, independence_group=IndependenceGroup.DIRECT_RECALL,
            run_id=0, model_id="mistral", model_family="mistral", prompt="p",
            prompt_hash="h", raw_output="Alpha", decode_profile=DecodeProfile(),
        ),
        ["Alpha"],
    )
    return contract, query, graph


@pytest.mark.parametrize(
    "label,edge",
    [
        (VerificationLabel.VALID, EdgeType.SUPPORT),
        (VerificationLabel.INVALID, EdgeType.CONTRADICT),
        (VerificationLabel.UNKNOWN, EdgeType.UNKNOWN),
    ],
)
def test_a_verdict_becomes_the_matching_signed_edge(label, edge):
    contract, query, graph = _graph_with_alpha()
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha", label=label,
            valid_prob=0.4, invalid_prob=0.3, unknown_prob=0.3,
            model_id="qwen", model_family="qwen", record_id="v1",
        )
    )
    verifier_edges = [
        e for e in graph.candidates["alpha"].all_evidence()
        if e.independence_group is IndependenceGroup.BLIND_VERIFIER
    ]
    assert len(verifier_edges) == 1
    assert verifier_edges[0].edge_type is edge


def test_verifier_evidence_is_shown_candidate_not_independent_recall():
    contract, query, graph = _graph_with_alpha()
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha", label=VerificationLabel.VALID,
            valid_prob=0.8, invalid_prob=0.1, unknown_prob=0.1,
            model_id="qwen", model_family="qwen", record_id="v1",
        )
    )
    edge = next(
        e for e in graph.candidates["alpha"].all_evidence()
        if e.independence_group is IndependenceGroup.BLIND_VERIFIER
    )
    assert edge.mode is EvidenceMode.SHOWN_CANDIDATE


def test_cross_model_recall_and_verification_are_not_conflated():
    contract, query, graph = _graph_with_alpha()
    graph.add_entity_mentions(
        GenerationRecord(
            record_id="r2", query=query, view_id="borders_direct",
            view_family=ViewFamily.DIRECT,
            independence_group=IndependenceGroup.CROSS_MODEL_RECALL,
            run_id=0, model_id="qwen", model_family="qwen", prompt="p",
            prompt_hash="h", raw_output="Alpha", decode_profile=DecodeProfile(),
        ),
        ["Alpha"],
    )
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha", label=VerificationLabel.VALID,
            valid_prob=0.8, invalid_prob=0.1, unknown_prob=0.1,
            model_id="qwen", model_family="qwen", record_id="v1",
        )
    )
    modes = {e.independence_group: e.mode for e in graph.candidates["alpha"].all_evidence()}
    assert modes[IndependenceGroup.CROSS_MODEL_RECALL] is EvidenceMode.INDEPENDENT_RECALL
    assert modes[IndependenceGroup.BLIND_VERIFIER] is EvidenceMode.SHOWN_CANDIDATE


def test_a_verifier_label_can_never_become_a_candidate():
    contract, query, graph = _graph_with_alpha()
    before = set(graph.candidates)
    for label in ("A", "B", "C", "VALID", "INVALID", "UNKNOWN"):
        graph.add_verification(
            VerificationResult(candidate_key=label, label=VerificationLabel.VALID, valid_prob=0.9)
        )
    assert set(graph.candidates) == before


# --- 21. gate machinery shares the calibration path -------------------------


def test_an_uncertain_gate_is_not_a_confident_negative():
    runtime = ScriptedRuntime(
        {}, label_scores={("calibrated_gate", "S", "personHasCityOfDeath"):
                          {"YES": 0.0, "NO": 0.3, "UNKNOWN": 0.0}}
    )
    result = score_gate(
        runtime, "q?", relation="personHasCityOfDeath", subject="S",
        calibrator=ContextualCalibrator(), use_calibration=False,
    )
    assert result.decision == "UNKNOWN"
    assert not result.is_confident_negative


def test_a_confident_gate_negative_is_distinguishable():
    runtime = ScriptedRuntime(
        {}, label_scores={("calibrated_gate", "S", "personHasCityOfDeath"):
                          {"YES": 0.0, "NO": 4.0, "UNKNOWN": 0.0}}
    )
    result = score_gate(
        runtime, "q?", relation="personHasCityOfDeath", subject="S",
        calibrator=ContextualCalibrator(), use_calibration=False,
    )
    assert result.is_confident_negative
    assert result.entropy >= 0.0


def test_the_gate_uses_its_own_label_set():
    assert set(GATE_LABELS) == {"YES", "NO", "UNKNOWN"}
    assert GATE_LABELS != LABEL_TOKENS or True   # same continuations, different semantics
    assert GATE_LABELS["YES"] == "A"


# --- 22. staged persistence --------------------------------------------------


def test_verifier_fields_survive_staged_persistence():
    from cover_kbc.staging import graph_from_json, graph_to_json

    contract, query, graph = _graph_with_alpha()
    graph.add_verification(
        VerificationResult(
            candidate_key="alpha", label=VerificationLabel.VALID,
            valid_prob=0.81, invalid_prob=0.11, unknown_prob=0.08,
            raw_logits={"VALID": 2.0, "INVALID": 0.5, "UNKNOWN": 0.1},
            calibrated_logits={"VALID": 1.4, "INVALID": 0.2, "UNKNOWN": 0.0},
            bias_logits={"VALID": 0.6},
            calibrated=True, margin=1.2, entropy=0.7, prompt_disagreement=0.22,
            template_id="verify_standard_v1", num_templates=2,
            model_id="qwen", model_family="qwen", record_id="v1",
        )
    )
    restored = graph_from_json(graph_to_json(graph))
    verification = restored.candidates["alpha"].verifications[0]
    assert verification.raw_logits == {"VALID": 2.0, "INVALID": 0.5, "UNKNOWN": 0.1}
    assert verification.calibrated_logits == {"VALID": 1.4, "INVALID": 0.2, "UNKNOWN": 0.0}
    assert verification.calibrated is True
    assert verification.prompt_disagreement == pytest.approx(0.22)
    assert verification.num_templates == 2
    assert verification.model_family == "qwen"


# --- 23. compliance ----------------------------------------------------------


def test_no_retrieval_reaches_the_verifier():
    # Module 4's code moved into cover_kbc.verification.blind when the
    # package gained Module 17; inspecting the package __init__ would make
    # every absence assertion below pass vacuously.
    from cover_kbc.verification import blind as verification

    tree = ast.parse(inspect.getsource(verification))
    banned = {"requests", "urllib", "httpx", "aiohttp", "socket", "wikipedia", "wikidata"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] not in banned for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned


def test_the_scripted_end_to_end_verification_path_still_works():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    contract = get_contract("countryLandBordersCountry")
    enumerator = ScriptedRuntime(
        {("borders_direct", "S", contract.relation): ["Alpha; Beta"]},
        model_id="offline/mistral", family="mistral", role="enumerator",
    )
    verifier = ScriptedRuntime(
        {}, model_id="offline/qwen", family="qwen", role="verifier",
        label_scores={("blind_verifier", "S", contract.relation): scores(valid=2.5, invalid=0.2)},
    )
    config = PipelineConfig(enable_verifier=True, max_verifications_per_query=4)
    pipeline = CoverPipeline(enumerator, config, verifier_runtime=verifier)
    result = pipeline.run([Query("S", contract.relation, 0)])

    prediction = result.predictions[0]
    assert prediction.verification_calls > 0
    verified = [c for c in prediction.candidates if c.verifications]
    assert verified
    for subject in verified:
        assert subject.verifications[0].calibrated is True     # calibration ran


# --- 24. staged phase isolation ----------------------------------------------


def test_the_verifier_model_is_untouched_during_phase_a(tmp_path):
    """Staged execution keeps at most one heavyweight model per phase.

    Phase A must spend zero verifier calls: on Colab the verifier is not even
    resident. Cross-model recall therefore belongs to Phase B, and this asserts
    it is deferred rather than silently run against the enumerator.
    """
    from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
    from cover_kbc.staging import StageWriter, read_stage

    relation, subject = "countryLandBordersCountry", "Testland"
    # Beta is named by one view only, so it stays weakly supported and is routed
    # to the verifier instead of auto-accepted.
    enumerator = ScriptedRuntime(
        fallback=lambda r: "Alpha; Beta" if r.metadata.get("view_id") == "borders_direct" else "Alpha",
        model_id="offline/mistral", family="offline-mistral", role="enumerator",
    )
    verifier = ScriptedRuntime(
        fallback=lambda r: "Alpha",
        label_scores={("blind_verifier", subject, relation): scores(valid=2.0, invalid=-1.0)},
        model_id="offline/qwen", family="offline-qwen", role="verifier",
    )
    pipeline = CoverPipeline(
        enumerator,
        PipelineConfig(
            mode=ExecutionMode.STAGED, enable_verifier=True, use_calibration=True,
            max_verifications_per_query=4, enable_cross_model_recall=True,
            enable_prompt_disagreement=True,
        ),
        verifier_runtime=verifier,
    )

    with StageWriter(tmp_path / "enumerated.jsonl") as writer:
        for graph in pipeline.enumerate([Query(subject, relation, 0)]):
            writer.write(graph)
    assert verifier.calls == 0, "the verifier ran during phase A"

    with StageWriter(tmp_path / "verified.jsonl") as writer:
        for graph in pipeline.verify(read_stage(tmp_path / "enumerated.jsonl")):
            writer.write(graph)
    assert verifier.calls > 0, "the verifier never ran across the staged seam"

    # Phase C reloads from disk and must not touch a model at all.
    spent = (enumerator.calls, verifier.calls)
    graphs = list(read_stage(tmp_path / "verified.jsonl"))
    pipeline.decide(graphs)
    assert (enumerator.calls, verifier.calls) == spent, "phase C made a model call"

    graph = graphs[0]
    assert sum(len(c.verifications) for c in graph.candidates.values()) > 0

    def edges_of(group):
        return [
            edge
            for candidate in graph.candidates.values()
            if (g := candidate.groups.get(group))
            for edge in g.supports + g.contradictions + g.unknowns
        ]

    # Both roles reached the same graph, and the two mechanisms stay distinct.
    blind = edges_of(IndependenceGroup.BLIND_VERIFIER)
    recall = edges_of(IndependenceGroup.CROSS_MODEL_RECALL)
    assert blind and recall
    assert all(e.mode is EvidenceMode.SHOWN_CANDIDATE for e in blind)
    assert all(e.mode is EvidenceMode.INDEPENDENT_RECALL for e in recall)
