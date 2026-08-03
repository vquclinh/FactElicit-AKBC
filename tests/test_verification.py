"""Blind verifier: label tokenisation, calibration, disagreement, tiering.

All synthetic. No model is loaded anywhere in this file.
"""

from __future__ import annotations

import math

import pytest

from cover_kbc.models.base import LabelScoreResult, entropy, softmax
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.scoring import (
    DEFAULT_SCORING,
    ScoringConfig,
    assign_tier,
    decide_status,
    score_candidate,
    verification_targets,
)
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    EdgeType,
    Evidence,
    EvidenceMode,
    IndependenceGroup,
    VerificationLabel,
    VerificationResult,
    VerificationTier,
)
from cover_kbc.verification import (
    CONTENT_FREE_CANDIDATE,
    GATE_LABELS,
    LABEL_TOKENS,
    TEMPLATE_ADVERSARIAL,
    TEMPLATE_QUESTION,
    TEMPLATE_STANDARD,
    ContextualCalibrator,
    aggregate_verifications,
    build_verifier_prompt,
    inspect_label_encoding,
    jensen_shannon_divergence,
    normalized_disagreement,
    prompt_disagreement,
    read_labels,
    score_gate,
    verify_candidate,
    verify_multi_template,
)


class FakeTokenizer:
    """Minimal tokenizer stand-in with configurable per-string encodings."""

    def __init__(self, mapping):
        self.mapping = mapping

    def encode(self, text, add_special_tokens=False):
        return list(self.mapping.get(text, [1, 2]))


# --- label tokenisation (M2 requirement 6) --------------------------------


def test_single_token_labels_use_next_token_logits():
    tokenizer = FakeTokenizer({"A": [32], "B": [33], "C": [34]})
    encoding = inspect_label_encoding(tokenizer, LABEL_TOKENS)
    assert encoding.single_token
    assert encoding.strategy == "next_token_logits"


def test_multi_token_labels_force_the_sequence_fallback():
    """Never silently compare first tokens of multi-token labels."""
    tokenizer = FakeTokenizer({"A": [32], "B": [33, 99], "C": [34]})
    encoding = inspect_label_encoding(tokenizer, LABEL_TOKENS)
    assert not encoding.single_token
    assert encoding.strategy == "sequence_loglikelihood"


def test_zero_token_label_is_rejected():
    tokenizer = FakeTokenizer({"A": [], "B": [33], "C": [34]})
    with pytest.raises(ValueError, match="zero tokens"):
        inspect_label_encoding(tokenizer, LABEL_TOKENS)


def test_gate_labels_are_inspected_too():
    tokenizer = FakeTokenizer({"A": [32], "B": [33], "C": [34]})
    assert inspect_label_encoding(tokenizer, GATE_LABELS).single_token


# --- verifier prompt -------------------------------------------------------


def test_verifier_prompt_carries_contract_rules_and_hides_reasoning(
    borders_query, borders_contract
):
    prompt = build_verifier_prompt(borders_query, borders_contract, "Alpha")
    assert borders_query.subject in prompt
    assert "Alpha" in prompt
    for rule in borders_contract.hard_negative_rules:
        assert rule in prompt
    assert "A = VALID" in prompt and "B = INVALID" in prompt and "C = UNKNOWN" in prompt


def test_templates_are_distinct_but_ask_the_same_question(borders_query, borders_contract):
    prompts = {
        t.template_id: build_verifier_prompt(borders_query, borders_contract, "Alpha", t)
        for t in (TEMPLATE_STANDARD, TEMPLATE_QUESTION, TEMPLATE_ADVERSARIAL)
    }
    assert len(set(prompts.values())) == 3
    for prompt in prompts.values():
        assert "A = VALID" in prompt


# --- calibration math (M2 requirement 7) ----------------------------------


def test_calibration_subtracts_control_logits():
    raw = {"VALID": 2.0, "INVALID": 1.0, "UNKNOWN": 0.0}
    control = {"VALID": 1.5, "INVALID": 0.0, "UNKNOWN": 0.0}
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"), control=control)

    assert result.calibrated is True
    assert result.calibrated_logits == {"VALID": 0.5, "INVALID": 1.0, "UNKNOWN": 0.0}
    assert result.raw_logits == raw
    # The template favoured VALID; after removing that bias INVALID wins.
    assert result.label is VerificationLabel.INVALID


def test_calibration_is_a_no_op_without_a_control():
    raw = {"VALID": 2.0, "INVALID": 1.0, "UNKNOWN": 0.0}
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"))
    assert result.calibrated is False
    assert result.calibrated_logits == raw


def test_margin_and_entropy_are_computed_from_calibrated_logits():
    raw = {"VALID": 3.0, "INVALID": 1.0, "UNKNOWN": 0.0}
    result = read_labels(LabelScoreResult(logits=raw, model_id="m"))
    assert result.margin == pytest.approx(2.0)
    expected = entropy(softmax(raw))
    assert result.entropy == pytest.approx(expected)


def test_entropy_is_maximal_for_a_uniform_distribution():
    uniform = read_labels(
        LabelScoreResult(logits={"VALID": 0.0, "INVALID": 0.0, "UNKNOWN": 0.0}, model_id="m")
    )
    assert uniform.entropy == pytest.approx(math.log(3))
    assert uniform.margin == pytest.approx(0.0)


def test_control_logits_are_cached_per_relation_and_template(borders_contract):
    runtime = ScriptedRuntime({}, label_scores={})
    calibrator = ContextualCalibrator()
    for _ in range(4):
        calibrator.control_logits(runtime, borders_contract, TEMPLATE_STANDARD)
    assert calibrator.calls == 1
    calibrator.control_logits(runtime, borders_contract, TEMPLATE_QUESTION)
    assert calibrator.calls == 2


def test_control_probe_is_content_free(borders_contract):
    seen = []

    class Recording(ScriptedRuntime):
        def score_labels(self, request):
            seen.append(request.prompt)
            return super().score_labels(request)

    ContextualCalibrator().control_logits(Recording({}), borders_contract, TEMPLATE_STANDARD)
    assert CONTENT_FREE_CANDIDATE in seen[0]


def test_calibration_is_deterministic(borders_query, borders_contract):
    runtime = ScriptedRuntime(
        {},
        label_scores={
            ("blind_verifier", borders_query.subject, borders_query.relation): {
                "VALID": 2.0, "INVALID": 0.5, "UNKNOWN": 0.1
            }
        },
    )
    outputs = [
        verify_candidate(
            runtime, borders_query, borders_contract, "alpha", "Alpha",
            calibrator=ContextualCalibrator(),
        ).valid_prob
        for _ in range(3)
    ]
    assert len(set(outputs)) == 1


# --- prompt disagreement (M2 requirement 8) -------------------------------


def test_identical_distributions_have_zero_disagreement():
    p = {"VALID": 0.7, "INVALID": 0.2, "UNKNOWN": 0.1}
    assert jensen_shannon_divergence([p, p]) == pytest.approx(0.0, abs=1e-12)
    assert normalized_disagreement([p, p]) == pytest.approx(0.0, abs=1e-12)


def test_opposite_distributions_reach_maximal_disagreement():
    a = {"VALID": 1.0, "INVALID": 0.0, "UNKNOWN": 0.0}
    b = {"VALID": 0.0, "INVALID": 1.0, "UNKNOWN": 0.0}
    assert jensen_shannon_divergence([a, b]) == pytest.approx(math.log(2), rel=1e-6)
    assert normalized_disagreement([a, b]) == pytest.approx(1.0, rel=1e-6)


def test_disagreement_is_symmetric():
    a = {"VALID": 0.8, "INVALID": 0.1, "UNKNOWN": 0.1}
    b = {"VALID": 0.2, "INVALID": 0.7, "UNKNOWN": 0.1}
    assert jensen_shannon_divergence([a, b]) == pytest.approx(jensen_shannon_divergence([b, a]))


def test_a_single_distribution_cannot_disagree():
    assert normalized_disagreement([{"VALID": 1.0}]) == 0.0
    assert prompt_disagreement([]) == 0.0


def test_multi_template_verification_reports_disagreement(borders_query, borders_contract):
    runtime = ScriptedRuntime({})
    results, disagreement = verify_multi_template(
        runtime, borders_query, borders_contract, "alpha", "Alpha",
        calibrator=ContextualCalibrator(),
    )
    assert len(results) == 2
    assert all(r.prompt_disagreement == disagreement for r in results)


def test_aggregate_averages_the_distributions():
    def make(v, i, u):
        return VerificationResult(
            candidate_key="k", label=VerificationLabel.VALID,
            valid_prob=v, invalid_prob=i, unknown_prob=u,
        )

    merged = aggregate_verifications([make(0.9, 0.05, 0.05), make(0.1, 0.8, 0.1)])
    assert merged.valid_prob == pytest.approx(0.5)
    assert merged.num_templates == 2
    assert merged.prompt_disagreement > 0.0


# --- calibrated existence gate (M2 requirement 10) ------------------------


def _gate_runtime(logits):
    return ScriptedRuntime({}, label_scores={("calibrated_gate", "S", "personHasCityOfDeath"): logits})


def test_confident_negative_gate_closes():
    runtime = _gate_runtime({"YES": 0.0, "NO": 4.0, "UNKNOWN": 0.0})
    result = score_gate(runtime, "q?", relation="personHasCityOfDeath", subject="S",
                        calibrator=ContextualCalibrator(), use_calibration=False)
    assert result.decision == "NO"
    assert result.is_confident_negative


def test_weak_negative_gate_does_not_close():
    """A narrow NO must not force an empty answer (M2 requirement 10)."""
    runtime = _gate_runtime({"YES": 0.0, "NO": 0.3, "UNKNOWN": 0.0})
    result = score_gate(runtime, "q?", relation="personHasCityOfDeath", subject="S",
                        calibrator=ContextualCalibrator(), use_calibration=False)
    assert result.decision == "UNKNOWN"
    assert not result.is_confident_negative


def test_high_entropy_gate_does_not_close():
    runtime = _gate_runtime({"YES": 0.0, "NO": 0.0, "UNKNOWN": 0.0})
    result = score_gate(runtime, "q?", relation="personHasCityOfDeath", subject="S",
                        calibrator=ContextualCalibrator(), use_calibration=False)
    assert not result.is_confident_negative
    assert result.entropy == pytest.approx(math.log(3))


def test_positive_gate_never_closes():
    runtime = _gate_runtime({"YES": 5.0, "NO": 0.0, "UNKNOWN": 0.0})
    result = score_gate(runtime, "q?", relation="personHasCityOfDeath", subject="S",
                        calibrator=ContextualCalibrator(), use_calibration=False)
    assert result.decision == "YES"
    assert not result.is_confident_negative


# --- tiering (M2 requirement 9) -------------------------------------------


def _candidate(key="alpha", support=0, contradictions=0, disagreement=None):
    candidate = Candidate(key=key, display_value=key.title(), relation="countryLandBordersCountry")
    groups = [
        IndependenceGroup.DIRECT_RECALL,
        IndependenceGroup.STRUCTURAL_DECOMPOSITION,
        IndependenceGroup.CONTRASTIVE_SEPARATION,
        IndependenceGroup.MISSINGNESS_SEARCH,
    ]
    for i in range(support):
        candidate.add_evidence(
            Evidence(key, EdgeType.SUPPORT, groups[i], "v", "m", 0, f"r{i}")
        )
    for i in range(contradictions):
        candidate.add_evidence(
            Evidence(key, EdgeType.CONTRADICT, IndependenceGroup.BLIND_VERIFIER, "v", "m", 0, f"c{i}")
        )
    if disagreement is not None:
        candidate.verifications.append(
            VerificationResult(
                candidate_key=key, label=VerificationLabel.VALID,
                valid_prob=0.5, invalid_prob=0.3, unknown_prob=0.2,
                prompt_disagreement=disagreement,
            )
        )
    return candidate


def test_tier_hard_reject_for_a_contract_violation(borders_contract):
    candidate = _candidate(support=3)
    candidate.status = CandidateStatus.REJECTED
    assert assign_tier(candidate, borders_contract) is VerificationTier.HARD_REJECT


def test_tier_auto_accept_for_broad_support(borders_contract):
    assert assign_tier(_candidate(support=3), borders_contract) is VerificationTier.AUTO_ACCEPT


def test_tier_verify_for_weak_support(borders_contract):
    assert assign_tier(_candidate(support=1), borders_contract) is VerificationTier.VERIFY


def test_tier_adversarial_on_contradiction(borders_contract):
    tier = assign_tier(_candidate(support=2, contradictions=1), borders_contract)
    assert tier is VerificationTier.ADVERSARIAL_VERIFY


def test_tier_adversarial_on_high_disagreement(borders_contract):
    tier = assign_tier(_candidate(support=2, disagreement=0.9), borders_contract)
    assert tier is VerificationTier.ADVERSARIAL_VERIFY


def test_contradiction_beats_broad_support_in_tiering(borders_contract):
    """A conflict must be escalated even when support is otherwise strong."""
    tier = assign_tier(_candidate(support=4, contradictions=1), borders_contract)
    assert tier is VerificationTier.ADVERSARIAL_VERIFY


def test_verification_targets_prioritise_adversarial_then_weakest(borders_contract):
    candidates = [
        _candidate("weak", support=1),
        _candidate("conflict", support=2, contradictions=1),
        _candidate("solid", support=3),
    ]
    targets = verification_targets(candidates, borders_contract, budget=2)
    assert [c.key for c in targets] == ["conflict", "weak"]


def test_verification_targets_respect_the_budget(borders_contract):
    candidates = [_candidate(f"c{i}", support=1) for i in range(5)]
    assert len(verification_targets(candidates, borders_contract, budget=2)) == 2
    assert verification_targets(candidates, borders_contract, budget=0) == []


# --- candidate score components (M2 requirement 11) -----------------------


def test_score_components_are_stored_separately(borders_contract):
    candidate = _candidate(support=2)
    breakdown = score_candidate(candidate, borders_contract)
    assert breakdown.support == pytest.approx(0.5)
    assert set(breakdown.to_json()) >= {
        "F_support", "L_logit", "X_cross_model", "C_contradiction", "U_disagreement",
        "weights", "total",
    }
    assert candidate.score == breakdown.total


def test_score_total_matches_the_weighted_sum(borders_contract):
    config = ScoringConfig(
        alpha_support=1.0, beta_logit=0.0, gamma_cross_model=0.0,
        delta_contradiction=2.0, eta_disagreement=0.0,
    )
    candidate = _candidate(support=2, contradictions=1)
    breakdown = score_candidate(candidate, borders_contract, config)
    expected = 1.0 * breakdown.support - 2.0 * breakdown.contradiction
    assert breakdown.total == pytest.approx(expected)


def test_unverified_candidates_get_a_neutral_logit_term(borders_contract):
    """No verifier evidence is neutral, not negative."""
    breakdown = score_candidate(_candidate(support=1), borders_contract)
    assert breakdown.logit == 0.0


def test_invalid_verdict_rejects_the_candidate(borders_contract):
    candidate = _candidate(support=2)
    candidate.verifications.append(
        VerificationResult(
            candidate_key=candidate.key, label=VerificationLabel.INVALID,
            valid_prob=0.05, invalid_prob=0.9, unknown_prob=0.05,
        )
    )
    score_candidate(candidate, borders_contract)
    candidate.tier = assign_tier(candidate, borders_contract)
    assert decide_status(candidate, borders_contract) is CandidateStatus.REJECTED


def test_cross_model_independent_recall_outweighs_shown_agreement(borders_contract):
    shown = _candidate("shown", support=1)
    shown.add_evidence(
        Evidence("shown", EdgeType.SUPPORT, IndependenceGroup.BLIND_VERIFIER,
                 "blind_verifier", "qwen", 0, "v1",
                 model_family="qwen", mode=EvidenceMode.SHOWN_CANDIDATE)
    )
    recalled = _candidate("recalled", support=1)
    recalled.add_evidence(
        Evidence("recalled", EdgeType.SUPPORT, IndependenceGroup.CROSS_MODEL_RECALL,
                 "borders_direct", "qwen", 0, "v2",
                 model_family="qwen", mode=EvidenceMode.INDEPENDENT_RECALL)
    )
    a = score_candidate(shown, borders_contract)
    b = score_candidate(recalled, borders_contract)
    assert b.cross_model > a.cross_model
    assert a.cross_model == pytest.approx(DEFAULT_SCORING.shown_candidate_weight)
