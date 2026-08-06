"""Module 17 - Specialist Verifier Suite conformance.

Five things have to hold:

* Table 5's five contracts, one central registry, every relation routed;
* the **blind-verification invariant** - the prompt carries the subject, the
  contract, the target and the labels, and nothing the acquisition layer
  believes about this candidate;
* Module 4's kernel is *called*, not copied: one softmax, one calibration
  subtraction, one divergence formula in the codebase;
* §13.1's bias controls are measured with matched controls, and the three
  disagreements stay three;
* M17 verifies what the caller asks for, decides nothing, and changes no
  production artefact.

Every subject and object below is **fictional**.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.contracts.router import compile_query
from cover_kbc.evidence.consensus import AtomicConsensusEngine
from cover_kbc.evidence.consensus_types import (
    CandidateConsensusState,
    NumericClusterConsensus,
    QueryConsensusResult,
    RiskFlag,
)
from cover_kbc.models.base import LabelScoreResult
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.types import VerificationLabel
from cover_kbc.verification import (
    LABEL_TOKENS,
    ContextualCalibrator,
    normalized_disagreement,
)
from cover_kbc.verification.specialist_contracts import (
    SPECIALIST_CONTRACTS,
    SPECIALIST_CONTRACT_VERSION,
    check_specialist_registry_consistency,
    specialist_contract,
    specialist_family,
)
from cover_kbc.verification.specialist_prompts import (
    CANDIDATE_TEMPLATE_IDS,
    PROPOSITION_TEMPLATE_IDS,
    SPECIALIST_SYSTEM_PROMPT,
    SPECIALIST_TEMPLATE_IDS,
    label_meaning,
    render_label_block,
    render_specialist_prompt,
    specialist_template,
)
from cover_kbc.verification.specialist_types import (
    VERIFICATION_VERSION,
    LabelOrder,
    QueryPropositionKind,
    QuerySpecialistVerificationResult,
    SpecialistTemplateResult,
    SpecialistVerificationRequest,
    SpecialistVerificationResult,
    SpecialistVerifierError,
    SpecialistVerifierFamily,
    TargetIneligible,
    UnsupportedSpecialistRelation,
    VerificationTarget,
    VerificationTargetKind,
    VerifierBiasDiagnostics,
    mean_distribution,
)
from cover_kbc.verification.specialist_verifier import (
    PROPOSITION_TEXT,
    SpecialistVerifier,
    SpecialistVerifierConfig,
    build_specialist_verifier,
    verifiable_targets,
)

AWARD = "awardWonBy"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"

SUBJECTS = {
    AWARD: "Aurora Prize", DEATH: "Person Alpha", CAPACITY: "Example Stadium",
    AREA: "Example Region", BORDERS: "Country Alpha", STOCK: "Example Holdings",
}
DISPLAYS = {
    AWARD: "Recipient Alpha", DEATH: "City Beta", BORDERS: "Country Beta",
    STOCK: "Exchange Alpha",
}
ENTITY_RELATIONS = (AWARD, DEATH, BORDERS, STOCK)
NUMERIC_RELATIONS = (CAPACITY, AREA)

M17_MODULES = (
    "specialist_types.py", "specialist_contracts.py", "specialist_prompts.py",
    "specialist_verifier.py",
)
VERIFIER_MODEL = "Qwen/Qwen3.5-4B"
CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
PRODUCTION_ARTEFACTS = (
    "predictions.jsonl", "diagnostics.json", "trace.jsonl",
    "stage_a_enumerated.jsonl", "stage_b_verified.jsonl", "calls_enumerate.jsonl",
    "calls_verify.jsonl", "query_profiles.jsonl", "prompt_programs.jsonl",
    "parametric_memory.jsonl", "numeric_specialist.jsonl",
    "large_open_set_specialist.jsonl", "null_temporal_specialist.jsonl",
    "small_set_specialist.jsonl", "atomic_consensus.jsonl", "metrics.json",
)


def _code_without_prose(name: str) -> str:
    """Executable source, docstrings and comments removed."""
    import io
    import tokenize

    source = (Path("src/cover_kbc/verification") / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    kept: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING:
            try:
                if ast.literal_eval(token.string) in docstrings:
                    continue
            except (ValueError, SyntaxError):  # pragma: no cover
                pass
        kept.append(token.string)
    return " ".join(kept)


class ScoredRuntime(ScriptedRuntime):
    """A label-scoring stub. Content-free controls score differently on purpose."""

    def __init__(self, *, real=None, control=None, fail=False, incomplete=False,
                 order_bias=0.0, model_id=VERIFIER_MODEL, **kwargs):
        super().__init__({}, model_id=model_id, **kwargs)
        self.real = real or {"VALID": 3.0, "INVALID": 0.5, "UNKNOWN": 0.2}
        self.control = control or {"VALID": 0.5, "INVALID": 1.5, "UNKNOWN": 0.2}
        self.fail = fail
        self.incomplete = incomplete
        self.order_bias = order_bias
        self.label_calls = 0
        self.prompts: list[str] = []
        self.metadata: list[dict] = []

    def score_labels(self, request):
        self.label_calls += 1
        self.prompts.append(request.prompt)
        self.metadata.append(dict(request.metadata))
        if self.fail:
            raise RuntimeError("the verifier fell over")
        is_control = "N/A" in request.prompt
        logits = dict(self.control if is_control else self.real)
        if not is_control and self.order_bias:
            order = request.metadata.get("label_order", "ABC")
            logits["VALID"] = logits["VALID"] + (self.order_bias if order != "ABC" else 0.0)
        if self.incomplete and not is_control:
            logits.pop("UNKNOWN", None)
        return LabelScoreResult(logits=logits, model_id=self.spec.model_id)


@pytest.fixture
def verifier():
    return SpecialistVerifier()


def _contract(relation: str):
    _, contract = compile_query(SUBJECTS[relation], relation, 0)
    return contract


def _entity_target(relation: str, display: str | None = None, **overrides):
    display = display or DISPLAYS[relation]
    base = dict(
        relation=relation, subject=SUBJECTS[relation], row_index=0,
        kind=VerificationTargetKind.ENTITY_CANDIDATE,
        target_id=CONTRACTS[relation].strict_key(display), display=display,
    )
    base.update(overrides)
    return VerificationTarget(**base)


def _numeric_target(relation: str = CAPACITY, representative: float = 25000.0,
                    unit: str = "persons", index: int = 0):
    return VerificationTarget(
        relation=relation, subject=SUBJECTS[relation], row_index=0,
        kind=VerificationTargetKind.NUMERIC_CLUSTER, target_id=str(index),
        display=f"{representative:g} {unit}".strip(),
        numeric_cluster_index=index, canonical_unit=unit,
    )


def _proposition_target(kind: QueryPropositionKind):
    return VerificationTarget(
        relation=DEATH, subject=SUBJECTS[DEATH], row_index=0,
        kind=VerificationTargetKind.QUERY_PROPOSITION, target_id=kind.value,
        proposition=kind,
    )


def _run(verifier, target, runtime=None):
    runtime = runtime or ScoredRuntime()
    result = verifier.verify(
        verifier.build_request(target), _contract(target.relation), runtime
    )
    return result, runtime


def _prompt_for(relation: str, target: VerificationTarget,
                template_id: str = "m17_statement_v1",
                order: LabelOrder = LabelOrder.ABC) -> str:
    proposition = target.kind is VerificationTargetKind.QUERY_PROPOSITION
    template = specialist_template(
        specialist_contract(relation),
        PROPOSITION_TEMPLATE_IDS[0] if proposition else template_id,
        order, proposition=proposition,
    )
    text = (
        PROPOSITION_TEXT[target.proposition] if proposition else target.display
    )
    return render_specialist_prompt(
        template, subject=target.subject, contract=_contract(relation),
        target_text=text,
    )


# --------------------------------------------------------------------------
# 1-3. Proposal conformance, five families, one registry
# --------------------------------------------------------------------------


def test_table_five_is_implemented_exactly():
    check_specialist_registry_consistency()
    assert len(SPECIALIST_CONTRACTS) == 5
    assert {c.family for c in SPECIALIST_CONTRACTS} == set(SpecialistVerifierFamily)

    expected = {
        CAPACITY: SpecialistVerifierFamily.NUMERIC,
        AREA: SpecialistVerifierFamily.NUMERIC,
        AWARD: SpecialistVerifierFamily.AWARD_MEMBERSHIP,
        DEATH: SpecialistVerifierFamily.NULL_TEMPORAL,
        STOCK: SpecialistVerifierFamily.STOCK,
        BORDERS: SpecialistVerifierFamily.BORDER,
    }
    assert {r: specialist_family(r) for r in CONTRACTS} == expected
    for contract in SPECIALIST_CONTRACTS:
        assert contract.question and contract.boundary and contract.rationale
        assert contract.contract_version == SPECIALIST_CONTRACT_VERSION


def test_an_unknown_relation_fails_closed():
    with pytest.raises(UnsupportedSpecialistRelation, match="no specialist"):
        specialist_contract("someOtherRelation")


def test_no_relation_branching_lives_outside_the_registry():
    """One declarative table, not scattered if/elif."""
    for name in ("specialist_prompts.py", "specialist_verifier.py", "specialist_types.py"):
        code = _code_without_prose(name)
        for relation in CONTRACTS:
            assert relation not in code, f"{name} branches on {relation}"
    registry = _code_without_prose("specialist_contracts.py")
    for relation in CONTRACTS:
        assert relation in registry, f"{relation} is not declared in the registry"


def test_the_registry_rejects_a_drifting_declaration(monkeypatch):
    from cover_kbc.verification import specialist_contracts

    broken = tuple(
        replace(c, question="") if c.family is SpecialistVerifierFamily.STOCK else c
        for c in SPECIALIST_CONTRACTS
    )
    monkeypatch.setattr(specialist_contracts, "SPECIALIST_CONTRACTS", broken)
    with pytest.raises(ValueError, match="requires a question"):
        check_specialist_registry_consistency()


# --------------------------------------------------------------------------
# 4-14. The five contracts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", NUMERIC_RELATIONS)
def test_the_numeric_contract_asks_for_the_exact_quantity(relation):
    contract = specialist_contract(relation)
    assert contract.family is SpecialistVerifierFamily.NUMERIC
    assert "exact quantity" in contract.question
    assert contract.target_kinds == (VerificationTargetKind.NUMERIC_CLUSTER,)
    # Table 5's hard negatives are quantity-scope confusions, stated generally.
    assert "different quantity" in contract.boundary
    # The relation's own exclusions reach the prompt through Module 0.
    prompt = _prompt_for(relation, _numeric_target(relation))
    for rule in CONTRACTS[relation].hard_negative_rules:
        assert rule in prompt


def test_capacity_and_area_hard_negatives_come_from_module_0():
    capacity = _prompt_for(CAPACITY, _numeric_target(CAPACITY))
    area = _prompt_for(AREA, _numeric_target(AREA, 100.0, "km2"))
    assert "attendance" in capacity.casefold()
    assert "land" in area.casefold()
    # Neither prompt asserts which error this particular value is.
    for prompt in (capacity, area):
        assert "this value is" not in prompt.casefold()
        assert "suspected" not in prompt.casefold()


def test_the_award_contract_asks_for_the_exact_recipient():
    contract = specialist_contract(AWARD)
    assert contract.question == "Is the candidate a recipient of this exact award?"
    boundary = contract.boundary.casefold()
    for clause in ("only considered", "work or", "different award", "does not stand"):
        assert clause in boundary, clause
    prompt = _prompt_for(AWARD, _entity_target(AWARD))
    for rule in CONTRACTS[AWARD].hard_negative_rules:
        assert rule in prompt


def test_the_null_temporal_contract_covers_both_condition_levels():
    contract = specialist_contract(DEATH)
    assert "existence" in contract.question and "locality" in contract.question
    assert set(contract.target_kinds) == {
        VerificationTargetKind.ENTITY_CANDIDATE,
        VerificationTargetKind.QUERY_PROPOSITION,
    }
    assert set(contract.propositions) == set(QueryPropositionKind)
    prompt = _prompt_for(DEATH, _entity_target(DEATH))
    for rule in CONTRACTS[DEATH].hard_negative_rules:
        assert rule in prompt


def test_only_the_null_relation_declares_query_propositions():
    for contract in SPECIALIST_CONTRACTS:
        if contract.family is SpecialistVerifierFamily.NULL_TEMPORAL:
            assert contract.propositions
        else:
            assert contract.propositions == ()


def test_the_stock_contract_asks_about_the_company_itself():
    contract = specialist_contract(STOCK)
    assert "company itself" in contract.question
    boundary = contract.boundary.casefold()
    for clause in ("related company", "index", "has ended", "not publicly listed"):
        assert clause in boundary, clause
    prompt = _prompt_for(STOCK, _entity_target(STOCK))
    for rule in CONTRACTS[STOCK].hard_negative_rules:
        assert rule in prompt


def test_the_border_contract_asks_for_physical_land_contact():
    contract = specialist_contract(BORDERS)
    assert "physical land contact" in contract.question
    boundary = contract.boundary.casefold()
    for clause in ("sea boundary", "proximity", "territory"):
        assert clause in boundary, clause
    prompt = _prompt_for(BORDERS, _entity_target(BORDERS))
    for rule in CONTRACTS[BORDERS].hard_negative_rules:
        assert rule in prompt


def test_no_reverse_question_is_ever_asked():
    """§11.1's reverse check is Module 18's, not M17's."""
    prompt = _prompt_for(BORDERS, _entity_target(BORDERS)).casefold()
    for forbidden in ("in reverse", "does country beta border", "conversely",
                      "the other direction"):
        assert forbidden not in prompt, forbidden
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES).casefold()
    for forbidden in ("reverse", "counterfactual", "key_condition", "reconstruct"):
        assert forbidden not in blob, forbidden


# --------------------------------------------------------------------------
# 15-18. Module 4 reuse and A/B/C semantics
# --------------------------------------------------------------------------


def test_module_4s_kernel_is_called_not_copied():
    source = (Path("src/cover_kbc/verification") / "specialist_verifier.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "cover_kbc.verification.blind":
            imported.update(alias.name for alias in node.names)
    assert {"read_labels", "ContextualCalibrator", "normalized_disagreement",
            "LABEL_TOKENS"} <= imported


def test_no_calibration_or_divergence_mathematics_is_duplicated():
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    for forbidden in ("def softmax", "math.exp", "def _kl", "def entropy",
                      "jensen_shannon", "logsumexp", "- control.get", "def apply("):
        assert forbidden not in blob, f"M17 re-implements {forbidden}"


def test_abc_semantics_are_module_4s():
    assert label_meaning() == {"A": "VALID", "B": "INVALID", "C": "UNKNOWN"}
    assert LABEL_TOKENS == {
        VerificationLabel.VALID.value: "A",
        VerificationLabel.INVALID.value: "B",
        VerificationLabel.UNKNOWN.value: "C",
    }
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    assert 'LABEL_TOKENS' in blob      # imported, not restated
    assert '"A": "VALID"' not in blob


def test_unknown_is_never_treated_as_invalid(verifier):
    """§6: UNKNOWN is epistemic verifier uncertainty, not a soft no."""
    runtime = ScoredRuntime(
        real={"VALID": 0.4, "INVALID": 0.4, "UNKNOWN": 3.0},
        control={"VALID": 0.0, "INVALID": 0.0, "UNKNOWN": 0.0},
    )
    result, _ = _run(verifier, _entity_target(AWARD), runtime)
    assert result.argmax_label == VerificationLabel.UNKNOWN.value
    assert result.mean_distribution[VerificationLabel.UNKNOWN.value] > 0.8
    payload = json.dumps(result.to_json())
    for forbidden in ("rejected", "accepted", "is_false", "probably_false"):
        assert forbidden not in payload, forbidden
    # The system prompt tells the model UNKNOWN is available and is not INVALID.
    assert "not a weaker way of saying INVALID" in SPECIALIST_SYSTEM_PROMPT


# --------------------------------------------------------------------------
# 19-28. THE BLIND-VERIFICATION GUARANTEE
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", ENTITY_RELATIONS)
def test_the_prompt_carries_subject_contract_target_and_labels_only(relation):
    target = _entity_target(relation)
    prompt = _prompt_for(relation, target)
    assert target.subject in prompt
    assert target.display in prompt
    assert CONTRACTS[relation].definition in prompt
    assert "A = VALID" in prompt and "B = INVALID" in prompt and "C = UNKNOWN" in prompt


@pytest.mark.parametrize("relation", ENTITY_RELATIONS + NUMERIC_RELATIONS)
def test_no_acquisition_evidence_reaches_the_prompt(relation):
    target = (
        _numeric_target(relation) if relation in NUMERIC_RELATIONS
        else _entity_target(relation)
    )
    for template_id in CANDIDATE_TEMPLATE_IDS:
        for order in LabelOrder:
            prompt = _prompt_for(relation, target, template_id, order).casefold()
            for leak in (
                # Module 16's support vector.
                "f=", "l=", "x=", "c=", "u=", "i=", "d=", "d_semantic",
                "independent_support", "support count", "supporting groups",
                "independence group", "consensus", "strong consensus", "contested",
                # Specialist internals.
                "pseudo-memory", "pseudo_memory", "parametric memory", "facet",
                "cluster", "dispersion", "closure", "listing gate", "near miss",
                "near-miss detected", "risk flag", "pending check",
                # Generator rationale.
                "the generator", "rationale", "chain of thought", "step by step",
                "explain your", "reasoning",
            ):
                assert leak not in prompt, f"{relation}/{template_id}/{order}: {leak}"


def test_a_poisoned_generator_rationale_never_reaches_the_verifier():
    """Requirement 24: the rationale exists upstream and must not travel."""
    poison = "The generator is 99% sure Candidate Alpha is correct."
    consensus = _consensus(
        AWARD,
        candidates=(_state(AWARD, "recipient alpha", "Recipient Alpha", risk=(
            RiskFlag.NEAR_MISS_MENTION, RiskFlag.SINGLE_GROUP_SUPPORT,
        )),),
    )
    # Store it exactly where a real run would: on the consensus provenance.
    poisoned = replace(
        consensus,
        query_risk={"generator_note": poison, "near_miss_risk": "HIGH"},
    )
    targets = verifiable_targets(poisoned)
    assert targets

    verifier = SpecialistVerifier()
    runtime = ScoredRuntime()
    verifier.verify(verifier.build_request(targets[0]), _contract(AWARD), runtime)

    assert runtime.prompts
    for prompt in runtime.prompts:
        assert poison not in prompt
        assert "99%" not in prompt
        assert "generator" not in prompt.casefold()
        assert "HIGH" not in prompt
        assert "NEAR_MISS" not in prompt.upper()


def test_the_request_has_no_field_for_acquisition_evidence():
    fields = set(SpecialistVerificationRequest.__dataclass_fields__)
    assert fields == {
        "target", "family", "contract_version", "template_ids", "label_orders",
        "verification_version",
    }
    target_fields = set(VerificationTarget.__dataclass_fields__)
    for forbidden in ("support", "score", "consensus", "risk", "evidence",
                      "rationale", "facet", "group"):
        assert not any(forbidden in name for name in target_fields), forbidden


def test_the_numeric_prompt_shows_the_value_and_not_the_cluster():
    prompt = _prompt_for(CAPACITY, _numeric_target(CAPACITY, 25000.0, "persons"))
    assert "25000 persons" in prompt
    for leak in ("cluster", "dispersion", "support", "median", "dominant",
                 "independent"):
        assert leak not in prompt.casefold(), leak


def test_the_award_prompt_never_names_this_candidates_suspected_error():
    prompt = _prompt_for(AWARD, _entity_target(AWARD))
    # The general class boundary is contract text and belongs there.
    assert "only considered for it" in prompt
    # A claim about *this* candidate does not.
    for leak in ("this candidate is a nominee", "suspected of being",
                 "the system believes", "classified as"):
        assert leak not in prompt.casefold(), leak


def test_the_stock_prompt_never_shows_module_15_state():
    prompt = _prompt_for(STOCK, _entity_target(STOCK)).casefold()
    for leak in ("publicly_listed_plausible", "unresolved", "gate", "trigger",
                 "cross-family", "candidate explosion", "temporal status conflict"):
        assert leak not in prompt, leak


def test_the_border_prompt_never_shows_a_pending_check():
    prompt = _prompt_for(BORDERS, _entity_target(BORDERS)).casefold()
    for leak in ("pending", "reverse_adjacency", "singleton", "territory ambiguity"):
        assert leak not in prompt, leak


def test_module_4s_adversarial_near_miss_framing_is_not_reused():
    """That template tells the verifier what the system suspects. M17 must not."""
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    assert "TEMPLATE_ADVERSARIAL" not in blob
    assert "near_miss_block" not in blob
    for relation in ENTITY_RELATIONS:
        prompt = _prompt_for(relation, _entity_target(relation))
        assert "suspected of being a near miss" not in prompt


# --------------------------------------------------------------------------
# 29-36. Contextual calibration and the control cache
# --------------------------------------------------------------------------


def test_the_content_free_control_carries_no_factual_signal(verifier):
    result, runtime = _run(verifier, _entity_target(AWARD))
    controls = [p for p in runtime.prompts if "N/A" in p]
    assert controls
    for prompt in controls:
        assert SUBJECTS[AWARD] not in prompt
        assert DISPLAYS[AWARD] not in prompt
        # It still carries the template and the contract, which is the bias
        # being measured.
        assert CONTRACTS[AWARD].definition in prompt
        assert "A = VALID" in prompt


def test_calibration_uses_module_4s_arithmetic_and_default_temperature(verifier):
    result, _ = _run(verifier, _entity_target(AWARD))
    reading = result.template_results[0]
    assert reading.calibrated
    assert reading.calibrated_logits == {
        k: reading.raw_logits[k] - reading.control_logits[k] for k in reading.raw_logits
    }
    assert verifier.calibrator.temperature == 1.0
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    for forbidden in ("temperature=", "fit(", "train", "LogisticRegression",
                      "Platt", "isotonic"):
        assert forbidden not in blob, forbidden


def test_calibration_can_flip_the_reading(verifier):
    """The point of the control: a template that always prefers A is corrected."""
    runtime = ScoredRuntime(
        real={"VALID": 2.0, "INVALID": 1.0, "UNKNOWN": 0.0},
        control={"VALID": 3.0, "INVALID": 0.5, "UNKNOWN": 0.0},
    )
    result, _ = _run(verifier, _entity_target(AWARD), runtime)
    assert result.argmax_label == VerificationLabel.INVALID.value


@pytest.mark.parametrize("order", list(LabelOrder))
def test_label_order_changes_presentation_and_never_meaning(order):
    block = render_label_block(order)
    assert "A = VALID" in block and "B = INVALID" in block and "C = UNKNOWN" in block
    lines = [line for line in block.splitlines() if " = " in line]
    assert [line[0] for line in lines] == list(order.sequence)


def test_each_label_order_has_its_own_matching_control(verifier):
    result, runtime = _run(verifier, _entity_target(AWARD))
    controls = [p for p in runtime.prompts if "N/A" in p]
    # Two phrasings x two orders = four readings, four distinct controls.
    assert len(controls) == 4
    assert len(set(controls)) == 4
    orders_seen = {
        tuple(line[0] for line in prompt.splitlines() if " = " in line)
        for prompt in controls
    }
    assert orders_seen == {("A", "B", "C"), ("B", "A", "C")}


def test_a_control_never_crosses_a_label_order():
    from cover_kbc.verification import _control_cache_key, _label_signature

    contract = specialist_contract(AWARD)
    keys = {
        order: _control_cache_key(
            VERIFIER_MODEL, AWARD,
            specialist_template(contract, "m17_statement_v1", order).template_id,
            "default", revision="r", label_signature=_label_signature(LABEL_TOKENS),
        )
        for order in LabelOrder
    }
    assert len(set(keys.values())) == len(LabelOrder)


def test_a_control_never_crosses_a_relation_or_a_family():
    from cover_kbc.verification import _control_cache_key, _label_signature

    signature = _label_signature(LABEL_TOKENS)
    keys = {
        relation: _control_cache_key(
            VERIFIER_MODEL, relation,
            specialist_template(
                specialist_contract(relation),
                PROPOSITION_TEMPLATE_IDS[0] if relation == DEATH else "m17_statement_v1",
                LabelOrder.ABC, proposition=relation == DEATH,
            ).template_id,
            "default", revision="r", label_signature=signature,
        )
        for relation in (AWARD, STOCK, DEATH)
    }
    assert len(set(keys.values())) == 3


def test_a_specialist_control_never_collides_with_module_4s():
    from cover_kbc.verification import (
        TEMPLATE_STANDARD, _control_cache_key, _label_signature,
    )

    signature = _label_signature(LABEL_TOKENS)
    generic = _control_cache_key(
        VERIFIER_MODEL, AWARD, TEMPLATE_STANDARD.template_id, "default",
        revision="r", label_signature=signature,
    )
    specialist = _control_cache_key(
        VERIFIER_MODEL, AWARD,
        specialist_template(
            specialist_contract(AWARD), "m17_statement_v1", LabelOrder.ABC
        ).template_id,
        "default", revision="r", label_signature=signature,
    )
    assert generic != specialist
    assert specialist[4].startswith("m17:")


def test_a_cache_miss_costs_a_control_call_and_a_hit_costs_none(verifier):
    runtime = ScoredRuntime()
    first, _ = _run(verifier, _entity_target(AWARD), runtime)
    assert runtime.label_calls == 8            # 4 readings + 4 controls
    assert first.calls == 8
    assert all(not r.control_cache_hit for r in first.template_results)

    before = runtime.label_calls
    second, _ = _run(verifier, _entity_target(AWARD, "Recipient Beta"), runtime)
    assert runtime.label_calls - before == 4   # readings only
    assert second.calls == 4
    assert all(r.control_cache_hit for r in second.template_results)


# --------------------------------------------------------------------------
# 37-43. Disagreement, aggregation, availability, failure
# --------------------------------------------------------------------------


def test_template_and_label_order_disagreement_are_separate_readings(verifier):
    runtime = ScoredRuntime(order_bias=1.2)
    result, _ = _run(verifier, _entity_target(AWARD), runtime)
    bias = result.bias

    assert bias.templates_measured == 2 and bias.label_orders_measured == 2
    assert bias.label_order_disagreement is not None
    assert bias.label_order_disagreement > 0        # the order moved the reading
    assert bias.template_disagreement == 0.0        # the phrasing did not
    assert bias.max_valid_shift is not None and bias.max_valid_shift > 0


def test_disagreement_reuses_module_4s_divergence(verifier):
    runtime = ScoredRuntime(order_bias=1.2)
    result, _ = _run(verifier, _entity_target(AWARD), runtime)
    by_phrasing: dict[str, list] = {}
    for reading in result.template_results:
        by_phrasing.setdefault(reading.phrasing_id, []).append(reading.distribution)
    expected = max(normalized_disagreement(g) for g in by_phrasing.values())
    assert result.bias.label_order_disagreement == expected


def test_the_three_disagreements_stay_three():
    """M17 template, M17 label-order, M16 D_semantic - never one number."""
    fields = set(VerifierBiasDiagnostics.__dataclass_fields__)
    assert {"template_disagreement", "label_order_disagreement"} <= fields
    assert "d_semantic" not in fields
    assert "D" not in fields
    consensus_fields = set(CandidateConsensusState.__dataclass_fields__)
    assert "d_semantic" in consensus_fields
    assert "template_disagreement" not in consensus_fields


def test_per_template_distributions_are_retained_beside_the_mean(verifier):
    runtime = ScoredRuntime(order_bias=1.2)
    result, _ = _run(verifier, _entity_target(AWARD), runtime)
    assert len(result.template_results) == 4
    assert all(r.distribution for r in result.template_results)
    assert result.mean_distribution == mean_distribution(
        [r.distribution for r in result.template_results]
    )
    # A plain mean, never a vote over argmax labels.
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    for forbidden in ("Counter(", "most_common", "majority", "vote"):
        assert forbidden not in blob, forbidden


def test_a_runtime_failure_fabricates_no_distribution(verifier):
    runtime = ScoredRuntime(fail=True)
    result, _ = _run(verifier, _entity_target(AWARD), runtime)

    assert result.available is False
    assert result.mean_distribution is None
    assert result.argmax_label is None
    assert len(result.errors) == 4
    for reading in result.template_results:
        assert reading.distribution is None
        assert reading.raw_logits is None
        assert reading.error and "fell over" in reading.error
    assert result.bias.template_disagreement is None


def test_incomplete_logits_are_refused_rather_than_softmaxed(verifier):
    runtime = ScoredRuntime(incomplete=True)
    result, _ = _run(verifier, _entity_target(AWARD), runtime)
    assert result.available is False
    assert all("incomplete label logits" in (r.error or "") for r in result.template_results)
    assert all(r.distribution is None for r in result.template_results)


def test_a_partial_failure_keeps_the_surviving_readings(verifier):
    class _Flaky(ScoredRuntime):
        def score_labels(self, request):
            if request.metadata.get("label_order") == LabelOrder.BAC.value:
                self.label_calls += 1
                raise RuntimeError("that order failed")
            return super().score_labels(request)

    result, _ = _run(verifier, _entity_target(AWARD), _Flaky())
    usable = result.usable_results
    assert len(usable) == 2 and len(result.errors) == 2
    assert result.available is True
    assert result.mean_distribution == mean_distribution([r.distribution for r in usable])
    # One order survived, so the order diagnostic is honestly unavailable.
    assert result.bias.label_orders_measured == 1
    assert result.bias.max_valid_shift is None


def test_availability_is_not_the_same_as_unknown(verifier):
    unavailable, _ = _run(verifier, _entity_target(AWARD), ScoredRuntime(fail=True))
    unknown, _ = _run(verifier, _entity_target(AWARD), ScoredRuntime(
        real={"VALID": 0.0, "INVALID": 0.0, "UNKNOWN": 3.0},
        control={"VALID": 0.0, "INVALID": 0.0, "UNKNOWN": 0.0},
    ))
    assert unavailable.available is False and unavailable.argmax_label is None
    assert unknown.available is True
    assert unknown.argmax_label == VerificationLabel.UNKNOWN.value


# --------------------------------------------------------------------------
# 44-49. Targets, eligibility and the scheduling boundary
# --------------------------------------------------------------------------


def _state(relation, key, display, *, violation=False, risk=()):
    return CandidateConsensusState(
        relation=relation, subject=SUBJECTS[relation], row_index=0,
        candidate_key=key, display=display, candidate_kind="ENTITY",
        hard_contract_violation=violation, risk_flags=tuple(risk),
        i_independent_support=3, f_support=0.6, d_semantic=1.0,
    )


def _consensus(relation, *, candidates=(), clusters=()):
    return QueryConsensusResult(
        consensus_version="m16-v1", relation=relation, subject=SUBJECTS[relation],
        row_index=0, applicable_specialist="M13",
        candidates=tuple(candidates), numeric_clusters=tuple(clusters),
    )


def test_a_hard_contract_violation_spends_no_call(verifier):
    consensus = _consensus(AWARD, candidates=(
        _state(AWARD, "recipient alpha", "Recipient Alpha"),
        _state(AWARD, "-5", "-5", violation=True),
    ))
    targets = {t.target_id: t for t in verifiable_targets(consensus)}
    assert targets["recipient alpha"].eligible
    assert not targets["-5"].eligible
    assert targets["-5"].ineligible_reason is TargetIneligible.HARD_CONTRACT_VIOLATION

    runtime = ScoredRuntime()
    result = verifier.verify(
        verifier.build_request(targets["-5"]), _contract(AWARD), runtime
    )
    assert runtime.label_calls == 0
    assert result.calls == 0
    assert result.template_results == ()
    assert result.available is False
    # A skip, not a rejection.
    payload = json.dumps(result.to_json())
    for forbidden in ("rejected", "pruned", "accepted"):
        assert forbidden not in payload, forbidden


def test_a_candidate_without_a_printable_value_is_ineligible():
    consensus = _consensus(AWARD, candidates=(_state(AWARD, "ghost", ""),))
    target = verifiable_targets(consensus)[0]
    assert not target.eligible
    assert target.ineligible_reason is TargetIneligible.NO_PRINTABLE_VALUE


def test_the_catalogue_reads_no_evidence_and_selects_nothing():
    """Eligibility is a type judgement; worth is Module 20/21's."""
    strong = _state(AWARD, "recipient alpha", "Recipient Alpha")
    weak = replace(
        _state(AWARD, "recipient beta", "Recipient Beta"),
        i_independent_support=0, f_support=0.0, d_semantic=0.0,
        risk_flags=(RiskFlag.SINGLE_GROUP_SUPPORT,),
    )
    targets = verifiable_targets(_consensus(AWARD, candidates=(strong, weak)))
    assert len(targets) == 2
    assert all(t.eligible for t in targets)     # neither is filtered by evidence

    code = _code_without_prose("specialist_verifier.py")
    # Attribute reads of Module 16's support vector, spelt with the dot so
    # "control_logits" is not mistaken for a read of "l_logit".
    for forbidden in (".i_independent_support", ".f_support", ".d_semantic",
                      ".risk_flags", ".u_prompt", ".l_logit", ".x_cross_model",
                      ".c_contradiction", "should_verify", "select_targets",
                      "budget", "expected_value"):
        assert forbidden not in code, f"M17 schedules on {forbidden}"


def test_an_entity_target_uses_strict_module_3_identity():
    consensus = _consensus(AWARD, candidates=(
        _state(AWARD, CONTRACTS[AWARD].strict_key("The Alpha Foundation"),
               "The Alpha Foundation"),
    ))
    target = verifiable_targets(consensus)[0]
    assert target.target_id == CONTRACTS[AWARD].strict_key("The Alpha Foundation")
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    assert "alias_hint" not in blob


def test_a_numeric_target_uses_module_12s_representative_without_reclustering():
    cluster = NumericClusterConsensus(
        cluster_index=0, representative=25000.0, dispersion=0.01,
        canonical_unit="persons", values=(24900.0, 25000.0, 25100.0),
        total_support=3, independent_support=2, independence_groups=("a", "b"),
    )
    targets = verifiable_targets(_consensus(CAPACITY, clusters=(cluster,)))
    assert len(targets) == 1
    assert targets[0].kind is VerificationTargetKind.NUMERIC_CLUSTER
    assert targets[0].display == "25000 persons"
    assert targets[0].numeric_cluster_index == 0
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    for forbidden in ("cluster_values", "median", "relative_distance", "tolerance"):
        assert forbidden not in blob, forbidden


def test_a_query_proposition_is_not_a_fake_entity_candidate():
    consensus = _consensus(DEATH, candidates=(_state(DEATH, "city beta", "City Beta"),))
    targets = verifiable_targets(consensus)
    propositions = [
        t for t in targets if t.kind is VerificationTargetKind.QUERY_PROPOSITION
    ]
    entities = [t for t in targets if t.kind is VerificationTargetKind.ENTITY_CANDIDATE]

    assert len(propositions) == 3 and len(entities) == 1
    keys = {t.target_id for t in entities}
    for fake in ("__EMPTY__", "NONE", "LIVING", "DECEASED"):
        assert fake not in keys
    assert {t.target_id for t in propositions} == {p.value for p in QueryPropositionKind}
    assert all(t.display == "" for t in propositions)


def test_a_null_proposition_reads_as_a_statement_about_the_subject(verifier):
    target = _proposition_target(QueryPropositionKind.NO_KNOWN_QUALIFYING_LOCALITY)
    result, runtime = _run(verifier, target)
    real = [p for p in runtime.prompts if "N/A" not in p]
    assert real
    for prompt in real:
        assert PROPOSITION_TEXT[target.proposition] in prompt
        assert "Candidate:" not in prompt
    # And an M17 UNKNOWN never becomes Module 14's failed recall.
    payload = json.dumps(result.to_json())
    for forbidden in ("failed_recall", "final_empty", "accepted_empty",
                      "no_known_locality_support", "ObjectEntities"):
        assert forbidden not in payload, forbidden


def test_the_proposition_template_and_the_candidate_templates_do_not_mix():
    contract = specialist_contract(DEATH)
    with pytest.raises(SpecialistVerifierError, match="renders a candidate"):
        specialist_template(contract, "m17_statement_v1", LabelOrder.ABC,
                            proposition=True)
    with pytest.raises(SpecialistVerifierError, match="query-level proposition"):
        specialist_template(contract, "m17_proposition_v1", LabelOrder.ABC)


def test_a_family_refuses_a_target_kind_it_cannot_pose(verifier):
    with pytest.raises(SpecialistVerifierError, match="cannot pose"):
        verifier.build_request(_numeric_target(AWARD))
    with pytest.raises(SpecialistVerifierError, match="cannot pose"):
        verifier.build_request(_proposition_target(
            QueryPropositionKind.SUBJECT_IS_LIVING
        ).__class__(
            relation=STOCK, subject=SUBJECTS[STOCK], row_index=0,
            kind=VerificationTargetKind.QUERY_PROPOSITION,
            target_id="SUBJECT_IS_LIVING",
            proposition=QueryPropositionKind.SUBJECT_IS_LIVING,
        ))


def test_a_mismatched_contract_is_refused(verifier):
    with pytest.raises(SpecialistVerifierError, match="contract is for"):
        verifier.verify(
            verifier.build_request(_entity_target(AWARD)), _contract(STOCK),
            ScoredRuntime(),
        )


# --------------------------------------------------------------------------
# 50-56. Call accounting, scheduling, persistence
# --------------------------------------------------------------------------


def test_an_explicit_request_executes_exactly_the_expected_calls(verifier):
    runtime = ScoredRuntime()
    result, _ = _run(verifier, _entity_target(STOCK), runtime)
    readings = len(CANDIDATE_TEMPLATE_IDS) * len(verifier.config.label_orders)

    assert len(result.template_results) == readings
    assert result.calls == runtime.label_calls == readings * 2
    assert sum(r.calls for r in result.template_results) == result.calls
    for reading in result.template_results:
        assert reading.model_id == VERIFIER_MODEL
        assert reading.prompt_sha256


def test_the_pipeline_catalogues_targets_and_verifies_none():
    """Requirement 49: no automatic fan-out, ever."""
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler,
    )
    from cover_kbc.specialists import LargeSetSpecialist
    from cover_kbc.types import Query

    runtime = ScoredRuntime(model_id="offline/enumerator")
    pipeline = CoverPipeline(
        runtime, PipelineConfig(), profiler=QueryProfiler(),
        prompt_compiler=PromptProgramCompiler(), retriever=ParametricRetriever(),
        large_set_specialist=LargeSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        specialist_verifier=SpecialistVerifier(),
    )
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    before = runtime.label_calls
    pipeline.decide_graph(graph)

    assert runtime.label_calls == before        # the catalogue costs nothing
    assert len(pipeline.specialist_verifications) == 1
    record = pipeline.specialist_verifications[0]
    assert record.results == ()                 # nothing was verified
    assert record.calls == 0
    assert record.family is SpecialistVerifierFamily.AWARD_MEMBERSHIP


def test_an_explicit_caller_can_ask_and_the_spend_is_shadow():
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler,
    )
    from cover_kbc.specialists import LargeSetSpecialist
    from cover_kbc.types import Query

    enumerator = ScriptedRuntime(
        {(op, SUBJECTS[AWARD], AWARD): ["Recipient Alpha"]
         for op in ("pseudo_memory#0", "self_ask#0", "query_rewrite#0")},
        model_id="offline/enumerator",
    )
    verifier_runtime = ScoredRuntime()
    pipeline = CoverPipeline(
        enumerator, PipelineConfig(), verifier_runtime=verifier_runtime,
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), large_set_specialist=LargeSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        specialist_verifier=SpecialistVerifier(),
    )
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    prediction = pipeline.decide_graph(graph)
    consensus = pipeline.consensus_results[0]
    targets = [t for t in verifiable_targets(consensus) if t.eligible][:1]
    assert targets

    shadow_before = pipeline.shadow_calls
    result = pipeline.verify_specialist_targets(consensus, targets, verifier_runtime)

    assert result.calls == verifier_runtime.label_calls > 0
    assert pipeline.shadow_calls == shadow_before + result.calls
    # Shadow spend never enters the production budget Module 7 reasons about.
    assert prediction.calls_used == graph.budget_snapshot.get("calls_used", 0)
    assert len(pipeline.specialist_verifications) == 1
    assert pipeline.specialist_verifications[0].results


@pytest.mark.parametrize("relation", sorted(SUBJECTS))
def test_every_public_type_round_trips(relation, verifier):
    target = (
        _numeric_target(relation) if relation in NUMERIC_RELATIONS
        else _entity_target(relation)
    )
    result, _ = _run(verifier, target)
    payload = json.loads(json.dumps(result.to_json()))
    assert SpecialistVerificationResult.from_json(payload) == result
    assert SpecialistVerificationRequest.from_json(payload["request"]) == result.request
    assert VerificationTarget.from_json(payload["request"]["target"]) == target
    assert VerifierBiasDiagnostics.from_json(payload["bias_diagnostics"]) == result.bias
    for original, entry in zip(result.template_results, payload["template_results"]):
        assert SpecialistTemplateResult.from_json(entry) == original

    query_result = QuerySpecialistVerificationResult(
        verification_version=VERIFICATION_VERSION, relation=relation,
        subject=SUBJECTS[relation], row_index=0,
        family=specialist_family(relation),
        contract_version=SPECIALIST_CONTRACT_VERSION, results=(result,),
        catalogue=(target,),
    )
    reloaded = QuerySpecialistVerificationResult.from_json(
        json.loads(json.dumps(query_result.to_json()))
    )
    assert reloaded == query_result


def test_the_payload_carries_no_verdict_and_no_gold(verifier):
    result, _ = _run(verifier, _entity_target(AWARD))
    payload = json.dumps(result.to_json())
    for forbidden in ("gold", "ObjectEntities", "accepted", "rejected",
                      "final_set", "should_stop", "prediction", "system_decision",
                      "prune", "rank"):
        assert forbidden not in payload, forbidden
    assert "argmax_label" in payload          # the model's own output stays
    assert "calibrated_label_distribution" in payload
    assert "probability_of_truth" not in payload


def test_no_result_field_is_named_like_a_decision():
    fields = set(SpecialistVerificationResult.__dataclass_fields__)
    for forbidden in ("accepted", "rejected", "final", "decision", "score", "rank",
                      "prune", "verdict"):
        assert not any(forbidden in name for name in fields), forbidden


# --------------------------------------------------------------------------
# 57-64. Read-only, invariance, regressions
# --------------------------------------------------------------------------


def test_module_16_state_is_read_only(verifier):
    consensus = _consensus(AWARD, candidates=(
        _state(AWARD, "recipient alpha", "Recipient Alpha"),
    ))
    before = copy.deepcopy(consensus.to_json())
    targets = verifiable_targets(consensus)
    verifier.verify(verifier.build_request(targets[0]), _contract(AWARD), ScoredRuntime())
    assert consensus.to_json() == before

    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    for forbidden in ("candidate.status", "score_breakdown", "add_evidence",
                      "add_verification", "graph.", "EvidenceGraph"):
        assert forbidden not in blob, f"M17 writes through {forbidden}"


def test_module_4s_prompt_surface_is_byte_identical():
    import hashlib

    from cover_kbc.verification import (
        GATE_TEMPLATE, LABEL_TOKENS as TOKENS, TEMPLATES, VERIFIER_SYSTEM_PROMPT,
    )

    blob = (
        VERIFIER_SYSTEM_PROMPT + "\n" + GATE_TEMPLATE + "\n"
        + repr(sorted(TOKENS.items()))
    )
    for template in TEMPLATES:
        blob += "\n" + template.template_id + "\n" + template.body
    assert hashlib.sha256(blob.encode()).hexdigest() == (
        "3acd7109fd22cf37b9b0c1c8a3ab63e4a4a1b65875eab02888e3fbc491e6d874"
    )


def test_the_generic_verifier_still_works_unchanged():
    from cover_kbc.verification import TEMPLATE_STANDARD, verify_candidate

    query, contract = compile_query(SUBJECTS[AWARD], AWARD, 0)
    runtime = ScoredRuntime()
    result = verify_candidate(
        runtime, query, contract, "recipient alpha", "Recipient Alpha",
        calibrator=ContextualCalibrator(),
    )
    assert result.label is VerificationLabel.VALID
    assert result.template_id == TEMPLATE_STANDARD.template_id
    assert result.calibrated


# --------------------------------------------------------------------------
# 65-75. Boundaries, configuration, compliance
# --------------------------------------------------------------------------


def test_no_module_18_mechanism_exists():
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES).casefold()
    for forbidden in ("reverse_check", "counterfactual", "key_condition",
                      "reconstruct", "candidate_free", "adversarial_pair",
                      "near_miss_generation"):
        assert forbidden not in blob, f"M17 implements {forbidden}"


def test_no_dola_adapter_exists():
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES).casefold()
    for forbidden in ("dola", "early_exit", "layer_contrast", "premature_layer"):
        assert forbidden not in blob, forbidden


def test_no_module_19_20_21_logic_exists():
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    for forbidden in ("residual", "missingness", "saturation", "allocate_budget",
                      "schedule", "next_action", "expected_value", "should_stop",
                      "STOP"):
        assert forbidden not in blob, f"M17 implements {forbidden}"


def test_no_external_retrieval_or_training():
    banned = {"requests", "httpx", "urllib", "socket", "sklearn", "torch",
              "transformers", "numpy", "scipy"}
    for name in M17_MODULES:
        tree = ast.parse((Path("src/cover_kbc/verification") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in banned, f"{name}: {module}"
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES).casefold()
    for forbidden in ("wikipedia", "wikidata", "http://", "https://", "api_key",
                      "fine_tune", "lora", ".fit("):
        assert forbidden not in blob, forbidden


def test_m17_introduces_no_new_parameters(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.verification.specialist_verifier import SpecialistVerifier\n"
        "SpecialistVerifier()\n"
        "print(','.join(sorted(m for m in sys.modules if m in "
        "('torch', 'transformers', 'mistral_common'))))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == ""


def test_the_frozen_verifier_role_is_used(verifier):
    result, runtime = _run(verifier, _entity_target(AWARD))
    assert result.verifier_model_id == VERIFIER_MODEL
    assert all(r.model_id == VERIFIER_MODEL for r in result.template_results)
    blob = " ".join(_code_without_prose(n) for n in M17_MODULES)
    for forbidden in ("build_runtime", "model_id=", "bake_off", "third_model"):
        assert forbidden not in blob, forbidden


def test_configuration_failures_are_loud():
    with pytest.raises(ValueError, match="unsupported specialist_verifier mode"):
        SpecialistVerifierConfig.from_mapping({"enabled": True, "mode": "production"})
    with pytest.raises(ValueError, match="unknown specialist_verifier key"):
        SpecialistVerifierConfig.from_mapping({"enabled": True, "enabledd": True})
    with pytest.raises(ValueError, match="unsupported verification_version"):
        SpecialistVerifierConfig.from_mapping(
            {"enabled": True, "verification_version": "m17-v9"}
        )
    with pytest.raises(ValueError, match="unknown specialist_verifier template"):
        SpecialistVerifierConfig.from_mapping(
            {"enabled": True, "templates": ["m17_nope_v1"]}
        )
    with pytest.raises(ValueError, match="unknown label order"):
        SpecialistVerifierConfig.from_mapping(
            {"enabled": True, "bias_controls": {"label_orders": ["ZZZ"]}}
        )
    with pytest.raises(ValueError, match="bias_controls key"):
        SpecialistVerifierConfig.from_mapping(
            {"enabled": True, "bias_controls": {"orders": ["ABC"]}}
        )
    with pytest.raises(ValueError, match="must be a list"):
        SpecialistVerifierConfig.from_mapping(
            {"enabled": True, "bias_controls": {"label_orders": "ABC"}}
        )


def test_the_builder_requires_m16_and_the_verifier_role():
    with pytest.raises(ValueError, match="requires consensus"):
        build_specialist_verifier(
            {"enabled": True}, consensus_enabled=False, verifier_available=True
        )
    with pytest.raises(ValueError, match="requires the verifier model role"):
        build_specialist_verifier(
            {"enabled": True}, consensus_enabled=True, verifier_available=False
        )
    assert build_specialist_verifier(
        None, consensus_enabled=True, verifier_available=True
    ) is None
    assert build_specialist_verifier(
        {"enabled": False}, consensus_enabled=True, verifier_available=True
    ) is None


def test_the_pipeline_refuses_a_verifier_without_consensus():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a consensus engine"):
        CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            specialist_verifier=SpecialistVerifier(),
        )


def test_no_fitted_value_exists_in_the_config():
    fields = set(SpecialistVerifierConfig.__dataclass_fields__)
    for forbidden in ("threshold", "weight", "alpha", "beta", "temperature",
                      "min_", "max_"):
        assert not any(forbidden in name for name in fields), forbidden


def test_the_shipped_configs_keep_m17_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        block = yaml.safe_load(Path(name).read_text())["specialist_verifier"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["verification_version"] == VERIFICATION_VERSION, name
        assert block["templates"] == list(CANDIDATE_TEMPLATE_IDS), name
        assert block["bias_controls"]["label_orders"] == ["ABC", "BAC"], name
    assert set(SPECIALIST_TEMPLATE_IDS) >= set(CANDIDATE_TEMPLATE_IDS)


def test_benchmark_is_untouched():
    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True
        ).stdout == "", args


# --------------------------------------------------------------------------
# Shadow / production invariance
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cli():
    scripts_dir = str(Path("scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_staged", "scripts/run_staged.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(tmp_path: Path, *, m17: bool, tag: str) -> Path:
    import yaml

    config = yaml.safe_load(Path(CONFIG).read_text())
    config["query_intelligence"] = {
        key: {"enabled": True, "mode": "shadow"}
        for key in ("profiler", "prompt_compiler", "parametric_retrieval")
    }
    config["specialists"] = {
        key: {"enabled": True, "mode": "shadow"}
        for key in ("numeric", "large_open_set", "null_temporal", "small_set_closure")
    }
    config["consensus"] = {"enabled": True, "mode": "shadow"}
    config["specialist_verifier"] = {
        **config["specialist_verifier"], "enabled": m17,
    }
    path = tmp_path / f"config_{tag}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _run_cli(cli, monkeypatch, config: Path, run_dir: Path, relation: str, limit=3):
    monkeypatch.setattr(
        sys, "argv",
        ["run_staged.py", "all", "--config", str(config), "--split", "train",
         "--limit", str(limit), "--relation", relation, "--run-dir", str(run_dir)],
    )
    assert cli.main() == 0


@pytest.mark.parametrize("relation", [AWARD, DEATH])
def test_shadow_mode_changes_no_production_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run_cli(cli, monkeypatch, _config(tmp_path, m17=True, tag="on"), on, relation)
    _run_cli(cli, monkeypatch, _config(tmp_path, m17=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in PRODUCTION_ARTEFACTS:
        left, right = on / name, off / name
        if not left.exists() and not right.exists():
            continue
        assert left.read_bytes() == right.read_bytes(), name

    assert (on / "specialist_verification.jsonl").is_file()
    assert not (off / "specialist_verification.jsonl").exists()


def test_the_artefact_is_manifest_ordered_and_verifies_nothing_on_its_own(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run_cli(cli, monkeypatch, _config(tmp_path, m17=True, tag="on"), run_dir, DEATH)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "specialist_verification.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["SubjectEntity"], r["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        assert row["verification_version"] == VERIFICATION_VERSION
        assert row["family"] == SpecialistVerifierFamily.NULL_TEMPORAL.value
        assert row["results"] == []            # no automatic fan-out
        assert row["calls"] == 0
        assert row["catalogue"]                # but the targets are recorded
        for forbidden in ("gold", "ObjectEntities", "accepted", "rejected",
                          "prediction", "should_stop"):
            assert forbidden not in json.dumps(row), forbidden


# --------------------------------------------------------------------------
# Prior-audit regressions
# --------------------------------------------------------------------------


def test_audit_0008_accounting_matrix_is_untouched():
    from cover_kbc.scoring import (
        DEFAULT_SCORING, acquisition_groups, cross_model_term, logit_term,
        support_term,
    )
    from cover_kbc.types import (
        Candidate, Evidence, EdgeType, EvidenceMode, IndependenceGroup,
    )

    contract = CONTRACTS[AWARD]
    candidate = Candidate(key="recipient alpha", display_value="Recipient Alpha",
                          relation=AWARD)
    candidate.add_evidence(Evidence(
        candidate_key="recipient alpha", edge_type=EdgeType.SUPPORT,
        independence_group=IndependenceGroup.DIRECT_RECALL, view_id="v",
        model_id="m", run_id=0, record_id="r",
    ))
    baseline = support_term(candidate, contract, DEFAULT_SCORING)
    candidate.add_evidence(Evidence(
        candidate_key="recipient alpha", edge_type=EdgeType.SUPPORT,
        independence_group=IndependenceGroup.BLIND_VERIFIER, view_id="blind",
        model_id="q", run_id=0, record_id="v1", mode=EvidenceMode.SHOWN_CANDIDATE,
    ))
    assert support_term(candidate, contract, DEFAULT_SCORING) == baseline
    assert cross_model_term(candidate, DEFAULT_SCORING) == 0.0
    assert logit_term(candidate, DEFAULT_SCORING) == 0.0
    assert IndependenceGroup.BLIND_VERIFIER not in acquisition_groups(contract)


def test_audit_0024_null_semantics_stay_green():
    from cover_kbc.specialists import asserts_relation_level_absence, is_epistemic_abstention

    assert is_epistemic_abstention("UNKNOWN")
    assert not asserts_relation_level_absence("UNKNOWN", sentinel_is_defined=False)
    assert not asserts_relation_level_absence("UNKNOWN", sentinel_is_defined=True)


def test_audit_0022_cross_family_rationales_stay_green():
    from cover_kbc.query_intelligence import PromptProgramCompiler, QueryProfiler
    from cover_kbc.specialists import NullTemporalSpecialist

    query, contract = compile_query(SUBJECTS[DEATH], DEATH, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    plan = NullTemporalSpecialist().plan(query, program, contract,
                                         cross_family_available=True)
    assert plan.cross_family_rationale == "disabled in configuration"


def test_module_16_consensus_is_unchanged_by_m17():
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.query_intelligence import PromptProgramCompiler, QueryProfiler
    from cover_kbc.specialists import LargeSetSpecialist

    query, contract = compile_query(SUBJECTS[AWARD], AWARD, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    specialist = LargeSetSpecialist().analyse(
        query, program, contract, ScriptedRuntime({}, model_id="offline/enumerator")
    )
    graph = build_graph(query, contract)
    engine = AtomicConsensusEngine()
    before = engine.consense(graph, specialist)

    SpecialistVerifier()      # constructing M17 must not perturb anything
    after = engine.consense(graph, specialist)
    assert before == after
