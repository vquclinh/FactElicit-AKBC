"""Layer-4 verification-evidence integration and conformance.

The layer boundary between Module 16's pre-verification consensus and Module
19's coverage estimator. Six things have to hold:

* the integration spends **zero** calls and mutates nothing upstream;
* one Module 17 request stays **one** mechanism however many phrasings and
  label orders measured it, and its content-free controls are never evidence;
* Module 18's four outcomes map conservatively - an absence, an unresolved
  answer, a malformed answer and a failed call are never contradictions;
* `F` never moves, `q_g` stays a max, and `X` rises only under the audited
  cross-model rule;
* "not measured" stays distinguishable from "measured and uncertain";
* nothing here decides anything.

Every subject and object below is **fictional**.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.contracts.router import compile_query
from cover_kbc.evidence.consensus_types import (
    CandidateConsensusState,
    NullConsensusState,
    NumericClusterConsensus,
    PendingDownstreamCheck,
    QueryConsensusResult,
)
from cover_kbc.evidence.layer4 import (
    INTEGRATION_VERSION,
    SPECIALIST_VERIFIER_GROUP,
    admits_one_object,
    Layer4EvidenceIntegrator,
    Layer4IntegrationConfig,
    build_layer4_integrator,
    check_origin_consistency,
    cost_ledger,
    cross_model_credit,
    prior_family_map,
    specialist_evidence,
    structural_evidence,
    structural_groups,
)
from cover_kbc.evidence.layer4_types import (
    CandidateEvidenceOverlay,
    CheckExecutionStatus,
    CrossModelCredit,
    Layer4CostLedger,
    Layer4EvidenceState,
    Layer4IntegrationError,
    Layer4ProvenanceError,
    NumericTargetOverlay,
    PendingCheckStatus,
    PropositionEvidenceOverlay,
    SpecialistVerifierEvidence,
    StructuralCheckEvidence,
    StructuralGroupSupport,
    StructuralOutcome,
    VerifierAvailability,
)
from cover_kbc.models.base import GenerationResult, LabelScoreResult
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.verification.bidirectional_types import BidirectionalCheckKind as K
from cover_kbc.verification.bidirectional_verifier import (
    BidirectionalVerifier,
    eligible_checks,
)
from cover_kbc.verification.specialist_types import (
    QueryPropositionKind,
    VerificationTarget,
    VerificationTargetKind,
)
from cover_kbc.verification.specialist_verifier import SpecialistVerifier

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
#: Relations with no entity candidates of their own.
NUMERIC_RELATIONS = (CAPACITY, AREA)
ENUMERATOR_FAMILY = "mistral"
VERIFIER_FAMILY = "qwen"

L4_MODULES = ("layer4_types.py", "layer4.py")
CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
PRODUCTION_ARTEFACTS = (
    "predictions.jsonl", "diagnostics.json", "trace.jsonl",
    "stage_a_enumerated.jsonl", "stage_b_verified.jsonl", "calls_enumerate.jsonl",
    "calls_verify.jsonl", "query_profiles.jsonl", "prompt_programs.jsonl",
    "parametric_memory.jsonl", "numeric_specialist.jsonl",
    "large_open_set_specialist.jsonl", "null_temporal_specialist.jsonl",
    "small_set_specialist.jsonl", "atomic_consensus.jsonl",
    "specialist_verification.jsonl", "bidirectional_verification.jsonl",
    "metrics.json",
)


def _code_without_prose(name: str) -> str:
    import io
    import tokenize

    source = (Path("src/cover_kbc/evidence") / name).read_text(encoding="utf-8")
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


# --------------------------------------------------------------------------
# Synthetic upstream state
# --------------------------------------------------------------------------


class ScoredRuntime(ScriptedRuntime):
    """Module 17 label scorer. Controls score differently from real prompts."""

    def __init__(self, *, real=None, control=None, fail=False,
                 model_id="Qwen/Qwen3.5-4B", family=VERIFIER_FAMILY):
        super().__init__({}, model_id=model_id, family=family)
        self.real = real or {"VALID": 3.0, "INVALID": 0.5, "UNKNOWN": 0.2}
        self.control = control or {"VALID": 0.5, "INVALID": 1.5, "UNKNOWN": 0.2}
        self.fail = fail
        self.label_calls = 0

    def score_labels(self, request):
        self.label_calls += 1
        if self.fail:
            raise RuntimeError("the verifier fell over")
        control = "N/A" in request.prompt
        return LabelScoreResult(
            logits=dict(self.control if control else self.real),
            model_id=self.spec.model_id,
        )


class EchoRuntime(ScriptedRuntime):
    """Module 18 generator."""

    def __init__(self, text="UNKNOWN", *, fail=False, family=VERIFIER_FAMILY,
                 model_id="Qwen/Qwen3.5-4B"):
        super().__init__({}, model_id=model_id, family=family)
        self.text = text
        self.fail = fail
        self.gen_calls = 0

    def generate(self, request):
        self.gen_calls += 1
        if self.fail:
            raise RuntimeError("the model fell over")
        return GenerationResult(
            text=self.text, model_id=self.spec.model_id,
            generated_tokens=5, prompt_tokens=30,
        )


def _state(relation, display=None, *, key=None, i=2, f=0.4, x=0.0, violation=False):
    display = display or DISPLAYS[relation]
    return CandidateConsensusState(
        relation=relation, subject=SUBJECTS[relation], row_index=0,
        candidate_key=key or CONTRACTS[relation].strict_key(display),
        display=display, candidate_kind="ENTITY",
        f_support=f, i_independent_support=i, x_cross_model=x,
        l_logit=0.0, l_available=False, c_contradiction=0.0, u_prompt=0.0,
        u_available=False, d_semantic=1.0, hard_contract_violation=violation,
    )


def _cluster(index=0, representative=25000.0, unit="persons", competing=0):
    return NumericClusterConsensus(
        cluster_index=index, representative=representative, dispersion=0.01,
        canonical_unit=unit, values=(representative,), total_support=2,
        independent_support=2, independence_groups=("a", "b"),
        competing_clusters=competing,
    )


def _consensus(relation, *, candidates=None, clusters=(), pending=(), null=None):
    if candidates is None:
        candidates = () if relation in NUMERIC_RELATIONS else (_state(relation),)
    return QueryConsensusResult(
        consensus_version="m16-v1", relation=relation, subject=SUBJECTS[relation],
        row_index=0, applicable_specialist="M16",
        candidates=tuple(candidates), numeric_clusters=tuple(clusters),
        pending_checks=tuple(pending), null_state=null,
    )


def _contract(relation):
    _, contract = compile_query(SUBJECTS[relation], relation, 0)
    return contract


def _verify(relation, *, display=None, runtime=None, kind=None, proposition=None,
            cluster_index=None):
    verifier = SpecialistVerifier()
    runtime = runtime or ScoredRuntime()
    if proposition is not None:
        target = VerificationTarget(
            relation=relation, subject=SUBJECTS[relation], row_index=0,
            kind=VerificationTargetKind.QUERY_PROPOSITION,
            target_id=proposition.value, proposition=proposition,
        )
    elif cluster_index is not None:
        target = VerificationTarget(
            relation=relation, subject=SUBJECTS[relation], row_index=0,
            kind=VerificationTargetKind.NUMERIC_CLUSTER, target_id=str(cluster_index),
            display="25000 persons", numeric_cluster_index=cluster_index,
            canonical_unit="persons",
        )
    else:
        display = display or DISPLAYS[relation]
        target = VerificationTarget(
            relation=relation, subject=SUBJECTS[relation], row_index=0,
            kind=kind or VerificationTargetKind.ENTITY_CANDIDATE,
            target_id=CONTRACTS[relation].strict_key(display), display=display,
        )
    return verifier.verify(
        verifier.build_request(target), _contract(relation), runtime
    ), runtime


def _check(consensus, kind, text="UNKNOWN", *, runtime=None, index=0):
    verifier = BidirectionalVerifier()
    checks = [
        c for c in eligible_checks(consensus)
        if c.check_kind is kind and c.eligible
    ]
    assert checks, f"no eligible {kind.value} for {consensus.relation}"
    runtime = runtime or EchoRuntime(text)
    record = verifier.execute(
        verifier.build_request(checks[index]), _contract(consensus.relation), runtime
    )
    return record, runtime


@pytest.fixture
def integrator():
    return Layer4EvidenceIntegrator()


FAMILIES = {"recipient alpha": (ENUMERATOR_FAMILY,), "city beta": (ENUMERATOR_FAMILY,),
            "country beta": (ENUMERATOR_FAMILY,)}


# --------------------------------------------------------------------------
# 1-8. Boundary, immutability, determinism
# --------------------------------------------------------------------------


def test_this_is_a_layer_boundary_not_a_new_module():
    """No M18.5, no judge, no reasoner - a deterministic projection."""
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("M18_5", "Module 18.5", "VerificationReasoner",
                      "FinalVerifier", "JudgeAgent", "Reasoner"):
        assert forbidden not in blob, forbidden
    assert INTEGRATION_VERSION == "layer4-v1"


def test_the_integration_makes_zero_neural_calls(integrator):
    consensus = _consensus(AWARD)
    verification, verifier_runtime = _verify(AWARD)
    record, check_runtime = _check(consensus, K.COUNTERFACTUAL, "TARGET")
    before = (verifier_runtime.label_calls, check_runtime.gen_calls)

    integrator.integrate(
        consensus, verifications=[verification], checks=[record],
        prior_families=FAMILIES,
    )
    assert (verifier_runtime.label_calls, check_runtime.gen_calls) == before

    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("score_labels", "generate(", "LMRuntime", "GenerationRequest",
                      "LabelScoreRequest", "runtime"):
        assert forbidden not in blob, f"Layer 4 references {forbidden}"


def test_no_model_machinery_is_imported():
    banned = {"torch", "transformers", "requests", "httpx", "urllib", "socket",
              "numpy", "sklearn"}
    for name in L4_MODULES:
        tree = ast.parse((Path("src/cover_kbc/evidence") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in banned, f"{name}: {module}"


def test_every_upstream_object_is_immutable(integrator):
    consensus = _consensus(AWARD)
    verification, _ = _verify(AWARD)
    record, _ = _check(consensus, K.REVERSE if False else K.COUNTERFACTUAL, "TARGET")

    before = (
        copy.deepcopy(consensus.to_json()),
        copy.deepcopy(verification.to_json()),
        copy.deepcopy(record.to_json()),
    )
    integrator.integrate(
        consensus, verifications=[verification], checks=[record],
        prior_families=FAMILIES,
    )
    assert consensus.to_json() == before[0]
    assert verification.to_json() == before[1]
    assert record.to_json() == before[2]


def test_integration_is_deterministic_and_order_invariant(integrator):
    consensus = _consensus(AWARD)
    verification, _ = _verify(AWARD)
    first, _ = _check(consensus, K.COUNTERFACTUAL, "TARGET", index=0)
    second, _ = _check(consensus, K.COUNTERFACTUAL, "EXCLUDED", index=1)

    forward = integrator.integrate(
        consensus, verifications=[verification], checks=[first, second],
        prior_families=FAMILIES,
    )
    again = integrator.integrate(
        consensus, verifications=[verification], checks=[first, second],
        prior_families=FAMILIES,
    )
    reversed_order = integrator.integrate(
        consensus, verifications=[verification], checks=[second, first],
        prior_families=FAMILIES,
    )
    assert forward == again
    overlay = {c.candidate_key: c for c in forward.candidates}["recipient alpha"]
    other = {c.candidate_key: c for c in reversed_order.candidates}["recipient alpha"]
    assert overlay.structural_groups == other.structural_groups
    assert overlay.layer4_i == other.layer4_i
    assert overlay.layer4_x == other.layer4_x


def test_evidence_for_another_query_is_refused(integrator):
    consensus = _consensus(AWARD)
    other, _ = _verify(AWARD, display="Recipient Alpha")
    moved = replace(
        other,
        request=replace(
            other.request,
            target=replace(other.request.target, subject="Another Prize"),
        ),
    )
    with pytest.raises(Layer4IntegrationError, match="another query"):
        integrator.integrate(consensus, verifications=[moved])


def test_a_target_module_16_does_not_hold_is_refused(integrator):
    consensus = _consensus(AWARD, candidates=(_state(AWARD, "Recipient Gamma"),))
    verification, _ = _verify(AWARD, display="Recipient Alpha")
    with pytest.raises(Layer4IntegrationError, match="Module 16 does not hold"):
        integrator.integrate(consensus, verifications=[verification])


# --------------------------------------------------------------------------
# 9-24. Module 17 integration
# --------------------------------------------------------------------------


def test_one_module_17_request_is_one_mechanism(integrator):
    consensus = _consensus(AWARD)
    verification, runtime = _verify(AWARD)
    assert len(verification.template_results) == 4      # 2 phrasings x 2 orders

    state = integrator.integrate(consensus, verifications=[verification],
                                prior_families=FAMILIES)
    overlay = state.candidates[0]
    evidence = overlay.specialist_verifier

    assert evidence.readings == 4
    assert evidence.independence_group == SPECIALIST_VERIFIER_GROUP
    # Four readings are one mechanism: I is untouched by verifier evidence.
    assert overlay.layer4_i == overlay.base_i
    assert len(overlay.structural_groups) == 0


def test_content_free_controls_are_cost_but_never_evidence(integrator):
    consensus = _consensus(AWARD)
    verification, runtime = _verify(AWARD)
    state = integrator.integrate(consensus, verifications=[verification],
                                prior_families=FAMILIES)
    evidence = state.candidates[0].specialist_verifier

    assert evidence.control_calls == 4                 # cold cache: 4 controls
    assert evidence.physical_calls == 8 == runtime.label_calls
    # Controls produced no support and no contradiction.
    assert state.candidates[0].structural_checks == ()
    assert state.candidates[0].base_c == 0.0
    assert state.cost.verifier_calls == 8


def test_a_warm_control_cache_costs_nothing_extra():
    """Second candidate for one relation reuses the controls."""
    verifier = SpecialistVerifier()
    runtime = ScoredRuntime()
    contract = _contract(AWARD)
    results = []
    for display in ("Recipient Alpha", "Recipient Gamma"):
        target = VerificationTarget(
            relation=AWARD, subject=SUBJECTS[AWARD], row_index=0,
            kind=VerificationTargetKind.ENTITY_CANDIDATE,
            target_id=CONTRACTS[AWARD].strict_key(display), display=display,
        )
        results.append(verifier.verify(verifier.build_request(target), contract, runtime))

    cold, warm = (specialist_evidence(r) for r in results)
    assert (cold.readings, cold.control_calls, cold.physical_calls) == (4, 4, 8)
    assert (warm.readings, warm.control_calls, warm.physical_calls) == (4, 0, 4)
    ledger = cost_ledger(results, ())
    assert ledger.verifier_calls == 12 == runtime.label_calls


def test_the_calibrated_distribution_is_preserved_whole(integrator):
    consensus = _consensus(AWARD)
    verification, _ = _verify(AWARD)
    evidence = integrator.integrate(
        consensus, verifications=[verification], prior_families=FAMILIES
    ).candidates[0].specialist_verifier

    assert evidence.distribution == dict(verification.mean_distribution)
    assert set(evidence.distribution) == {"VALID", "INVALID", "UNKNOWN"}
    assert evidence.argmax_label == "VALID"
    assert evidence.valid_margin is not None
    assert evidence.verifier_entropy is not None
    # Not reduced to an argmax.
    assert len(evidence.distribution) == 3


def test_unknown_is_neither_unavailable_nor_invalid(integrator):
    consensus = _consensus(AWARD)
    unknown, _ = _verify(AWARD, runtime=ScoredRuntime(
        real={"VALID": 0.2, "INVALID": 0.2, "UNKNOWN": 3.0},
        control={"VALID": 0.0, "INVALID": 0.0, "UNKNOWN": 0.0},
    ))
    failed, _ = _verify(AWARD, runtime=ScoredRuntime(fail=True))

    unknown_state = integrator.integrate(
        consensus, verifications=[unknown], prior_families=FAMILIES
    ).candidates[0].specialist_verifier
    failed_state = integrator.integrate(
        consensus, verifications=[failed], prior_families=FAMILIES
    ).candidates[0].specialist_verifier

    assert unknown_state.availability is VerifierAvailability.AVAILABLE
    assert unknown_state.argmax_label == "UNKNOWN"
    assert unknown_state.contradicts is False
    assert failed_state.availability is VerifierAvailability.UNAVAILABLE
    assert failed_state.distribution is None
    assert failed_state.argmax_label is None


def test_a_target_never_verified_is_not_requested(integrator):
    consensus = _consensus(AWARD)
    evidence = integrator.integrate(
        consensus, prior_families=FAMILIES
    ).candidates[0].specialist_verifier
    assert evidence.availability is VerifierAvailability.NOT_REQUESTED
    assert evidence.readings == 0 and evidence.physical_calls == 0
    assert evidence.distribution is None
    assert len(set(VerifierAvailability)) == 3        # three distinct states


def test_module_17_unknown_creates_no_substantive_null(integrator):
    null = NullConsensusState(
        relation=DEATH, subject=SUBJECTS[DEATH], row_index=0,
        failed_recall_operations=100,
        failed_recall_operation_ids=tuple(f"op{i}" for i in range(100)),
    )
    consensus = _consensus(DEATH, null=null)
    verification, _ = _verify(
        DEATH, proposition=QueryPropositionKind.NO_KNOWN_QUALIFYING_LOCALITY,
        runtime=ScoredRuntime(
            real={"VALID": 0.2, "INVALID": 0.2, "UNKNOWN": 3.0},
            control={"VALID": 0.0, "INVALID": 0.0, "UNKNOWN": 0.0},
        ),
    )
    state = integrator.integrate(
        consensus, verifications=[verification], prior_families=FAMILIES
    )
    assert state.null_state.has_substantive_null_evidence is False
    assert state.null_state.failed_recall_only is True
    assert state.null_state.failed_recall_operations == 100
    payload = json.dumps(state.to_json())
    for forbidden in ("final_empty", "accepted_empty", "gold_empty", "ObjectEntities"):
        assert forbidden not in payload, forbidden


def test_module_17_invalid_is_a_signed_contradiction_not_a_rejection(integrator):
    consensus = _consensus(AWARD)
    verification, _ = _verify(AWARD, runtime=ScoredRuntime(
        real={"VALID": 0.2, "INVALID": 3.0, "UNKNOWN": 0.2},
        control={"VALID": 0.0, "INVALID": 0.0, "UNKNOWN": 0.0},
    ))
    overlay = integrator.integrate(
        consensus, verifications=[verification], prior_families=FAMILIES
    ).candidates[0]

    assert overlay.specialist_verifier.argmax_label == "INVALID"
    assert overlay.specialist_verifier.contradicts is True
    # The audited core C is untouched: no denominator was invented for it.
    assert overlay.base_c == 0.0
    payload = json.dumps(overlay.to_json())
    for forbidden in ("rejected", "accepted", "pruned"):
        assert forbidden not in payload, forbidden


def test_the_three_disagreement_channels_stay_three(integrator):
    consensus = _consensus(AWARD)
    verification, _ = _verify(AWARD)
    overlay = integrator.integrate(
        consensus, verifications=[verification], prior_families=FAMILIES
    ).candidates[0]

    assert overlay.base_d == 1.0                                   # M16 semantic
    assert overlay.specialist_verifier.template_disagreement is not None
    assert overlay.specialist_verifier.label_order_disagreement is not None
    payload = overlay.to_json()
    assert payload["base"]["D"] == 1.0
    assert "template_disagreement" in payload["specialist_verifier"]
    assert "label_order_disagreement" in payload["specialist_verifier"]
    # No field combines them.
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("combined_disagreement", "total_uncertainty",
                      "disagreement_sum", "mean_disagreement"):
        assert forbidden not in blob, forbidden


def test_module_17_evidence_never_moves_f_or_x(integrator):
    consensus = _consensus(AWARD)
    baseline = integrator.integrate(consensus, prior_families=FAMILIES).candidates[0]
    verification, _ = _verify(AWARD)
    verified = integrator.integrate(
        consensus, verifications=[verification], prior_families=FAMILIES
    ).candidates[0]

    assert verified.base_f == baseline.base_f == 0.4
    assert verified.layer4_x == baseline.layer4_x == 0.0
    assert verified.layer4_i == baseline.layer4_i


def test_core_l_and_specialist_evidence_stay_separate(integrator):
    """No audited rule combines M4's L with M17's reading, so none is invented."""
    consensus = _consensus(AWARD)
    verification, _ = _verify(AWARD)
    overlay = integrator.integrate(
        consensus, verifications=[verification], prior_families=FAMILIES
    ).candidates[0]

    assert overlay.base_l == 0.0 and overlay.base_l_available is False
    assert overlay.specialist_verifier.available is True
    fields = set(CandidateEvidenceOverlay.__dataclass_fields__)
    for forbidden in ("combined_l", "fused_l", "merged_l", "l_total"):
        assert forbidden not in fields, forbidden
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("base_l +", "log_odds", "+ specialist", "average_margin"):
        assert forbidden not in blob, forbidden


# --------------------------------------------------------------------------
# 25-40. Module 18 integration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,text,outcome,status",
    [
        (K.REVERSE, "SUPPORTED", StructuralOutcome.SUPPORT,
         CheckExecutionStatus.RESOLVED),
        (K.REVERSE, "CONTRADICTED", StructuralOutcome.CONTRADICT,
         CheckExecutionStatus.RESOLVED),
        (K.REVERSE, "UNRESOLVED", StructuralOutcome.UNRESOLVED,
         CheckExecutionStatus.UNRESOLVED),
        (K.REVERSE, "!!!", StructuralOutcome.UNRESOLVED,
         CheckExecutionStatus.UNRESOLVED),
        (K.COUNTERFACTUAL, "TARGET", StructuralOutcome.SUPPORT,
         CheckExecutionStatus.RESOLVED),
        (K.COUNTERFACTUAL, "EXCLUDED", StructuralOutcome.CONTRADICT,
         CheckExecutionStatus.RESOLVED),
        (K.COUNTERFACTUAL, "NEITHER", StructuralOutcome.UNRESOLVED,
         CheckExecutionStatus.UNRESOLVED),
        (K.COUNTERFACTUAL, "UNKNOWN", StructuralOutcome.UNRESOLVED,
         CheckExecutionStatus.UNRESOLVED),
    ],
)
def test_shown_candidate_outcomes_map_conservatively(kind, text, outcome, status):
    consensus = _consensus(BORDERS)
    record, _ = _check(consensus, kind, text)
    evidence = structural_evidence(
        record, target_key="country beta", prior_families=FAMILIES
    )
    assert evidence.outcome is outcome
    assert evidence.status is status
    assert evidence.candidate_shown is True
    assert evidence.cross_model_credit is CrossModelCredit.SHOWN_CANDIDATE


@pytest.mark.parametrize(
    "text,outcome",
    [
        ("Recipient Alpha", StructuralOutcome.SUPPORT),
        # Another recipient of the same award. Both may hold at once, so this
        # is not evidence against the target - see the cardinality matrix below.
        ("Recipient Omega", StructuralOutcome.ALTERNATE_RECOVERED),
        ("UNKNOWN", StructuralOutcome.UNRESOLVED),
    ],
)
def test_key_condition_outcomes_map_conservatively(text, outcome):
    consensus = _consensus(AWARD)
    record, _ = _check(consensus, K.KEY_CONDITION, text)
    evidence = structural_evidence(
        record, target_key="recipient alpha", prior_families=FAMILIES
    )
    assert evidence.outcome is outcome
    # Masked, but not an independent recall - and never L.
    assert evidence.candidate_shown is False
    assert evidence.cross_model_credit is CrossModelCredit.NOT_INDEPENDENT_RECALL


# --------------------------------------------------------------------------
# The key-condition cardinality matrix
#
# Masking the target and receiving a different object means two different
# things. Where the contract admits at most one object the two compete; where
# it admits many, both may hold and the reconstruction says nothing about the
# target. The test source is Module 0's own selection policy.
# --------------------------------------------------------------------------


def test_exclusivity_is_read_from_module_0_not_from_a_relation_name():
    assert admits_one_object(DEATH) is True
    assert admits_one_object(CAPACITY) is True
    assert admits_one_object(AREA) is True
    assert admits_one_object(AWARD) is False
    assert admits_one_object(BORDERS) is False
    assert admits_one_object(STOCK) is False
    for relation in (DEATH, CAPACITY, AWARD, BORDERS):
        assert admits_one_object(relation) is (
            CONTRACTS[relation].selection.max_objects == 1
        )
    with pytest.raises(Layer4IntegrationError, match="no relation contract"):
        admits_one_object("someOtherRelation")

    # And it is read, not hard-coded: no relation name appears in the mapping.
    code = _code_without_prose("layer4.py")
    for relation in CONTRACTS:
        assert relation not in code, f"Layer 4 branches on {relation}"


@pytest.mark.parametrize(
    "relation,target,alternate",
    [
        (AWARD, "Recipient Alpha", "Recipient Beta"),
        (BORDERS, "Country Beta", "Country Gamma"),
        (STOCK, "Exchange Alpha", "Exchange Beta"),
    ],
)
def test_a_set_valued_alternate_never_contradicts_the_target(
    integrator, relation, target, alternate
):
    """§9.A-C: another qualifying object is not evidence against this one."""
    consensus = _consensus(relation, candidates=(_state(relation, target),))
    target_key = CONTRACTS[relation].strict_key(target)
    record, _ = _check(consensus, K.KEY_CONDITION, alternate)

    evidence = structural_evidence(
        record, target_key=target_key, prior_families=FAMILIES
    )
    assert evidence.outcome is StructuralOutcome.ALTERNATE_RECOVERED
    assert evidence.contradicts is False
    assert evidence.status is CheckExecutionStatus.RESOLVED
    # The alternate is preserved as provenance, not discarded.
    assert evidence.recovered_value == alternate
    assert evidence.raw_outcome == "DIFFERENT_VALUE_RECOVERED"

    overlay = {
        c.candidate_key: c
        for c in integrator.integrate(
            consensus, checks=[record], prior_families=FAMILIES
        ).candidates
    }[target_key]
    baseline = integrator.integrate(
        consensus, prior_families=FAMILIES
    ).candidates[0]

    # No contradiction reaches the target, by any channel.
    assert overlay.structural_contradicting_groups == ()
    assert overlay.base_c == baseline.base_c == 0.0
    assert overlay.base_contradicting_groups == baseline.base_contradicting_groups
    assert overlay.specialist_verifier == baseline.specialist_verifier
    # And it is not support either.
    assert overlay.structural_groups[0].q_g == 0
    payload = json.dumps(overlay.to_json())
    for forbidden in ("rejected", "accepted", "CONTRADICT"):
        assert forbidden not in payload, forbidden


@pytest.mark.parametrize(
    "relation,target",
    [
        (AWARD, "Recipient Alpha"),
        (BORDERS, "Country Beta"),
        (STOCK, "Exchange Alpha"),
    ],
)
def test_a_set_valued_exact_recovery_is_still_support(integrator, relation, target):
    consensus = _consensus(relation, candidates=(_state(relation, target),))
    target_key = CONTRACTS[relation].strict_key(target)
    record, _ = _check(consensus, K.KEY_CONDITION, target)

    overlay = {
        c.candidate_key: c
        for c in integrator.integrate(
            consensus, checks=[record], prior_families=FAMILIES
        ).candidates
    }[target_key]
    assert overlay.structural_groups[0].q_g == 1
    assert overlay.structural_contradicting_groups == ()


def test_a_null_single_alternate_competes_with_the_target(integrator):
    """§9.D: the contract admits at most one city, so two localities compete."""
    assert CONTRACTS[DEATH].selection.max_objects == 1
    consensus = _consensus(DEATH)
    record, _ = _check(consensus, K.KEY_CONDITION, "City Gamma")

    evidence = structural_evidence(
        record, target_key="city beta", prior_families=FAMILIES
    )
    assert evidence.outcome is StructuralOutcome.CONTRADICT
    assert evidence.contradicts is True
    assert evidence.recovered_value == "City Gamma"

    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]
    assert overlay.structural_contradicting_groups == ("M18_KEY_CONDITION",)
    # Competing evidence, never a final rejection.
    assert overlay.base_c == 0.0
    payload = json.dumps(overlay.to_json())
    for forbidden in ("rejected", "accepted", "final", "prune"):
        assert forbidden not in payload, forbidden


@pytest.mark.parametrize("relation,unit", [(CAPACITY, "persons"), (AREA, "km2")])
def test_a_numeric_alternate_competes_with_the_target(integrator, relation, unit):
    """§9.E-F: one target quantity, so a different canonical value competes."""
    assert CONTRACTS[relation].selection.max_objects == 1
    representative = 25000.0 if relation == CAPACITY else 100.0
    consensus = _consensus(
        relation, candidates=(),
        clusters=(_cluster(representative=representative, unit=unit),),
    )
    record, _ = _check(consensus, K.KEY_CONDITION, "61000" if relation == CAPACITY else "900")

    evidence = structural_evidence(
        record, target_key="0", prior_families=FAMILIES
    )
    assert evidence.outcome is StructuralOutcome.CONTRADICT

    state = integrator.integrate(consensus, checks=[record], prior_families=FAMILIES)
    target = state.numeric_targets[0]
    assert target.representative == representative      # M12's, untouched
    assert target.canonical_unit == unit
    assert len(target.structural_checks) == 1
    assert target.structural_checks[0].contradicts is True
    # No reclustering and no acceptance tolerance.
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("cluster_values", "0.05", "tolerance", "recluster"):
        assert forbidden not in blob, forbidden


def test_an_alternate_recovery_moves_no_other_channel(integrator):
    """§9.G: F, base L, X, base C and U are all untouched."""
    consensus = _consensus(AWARD)
    baseline = integrator.integrate(consensus, prior_families=FAMILIES).candidates[0]
    record, _ = _check(consensus, K.KEY_CONDITION, "Recipient Beta")
    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]

    assert overlay.base_f == baseline.base_f
    assert (overlay.base_l, overlay.base_l_available) == (
        baseline.base_l, baseline.base_l_available
    )
    assert overlay.layer4_x == baseline.layer4_x == 0.0
    assert overlay.base_c == baseline.base_c
    assert (overlay.base_u, overlay.base_u_available) == (
        baseline.base_u, baseline.base_u_available
    )
    assert overlay.base_d == baseline.base_d
    # I is unchanged too: a masked reconstruction is not independent recall.
    assert overlay.layer4_i == baseline.layer4_i == overlay.base_i
    assert overlay.cross_model_credit is CrossModelCredit.NOT_INDEPENDENT_RECALL


def test_repeated_alternate_recoveries_stay_one_group(integrator):
    """§9.H: same mechanism, one group, max not sum."""
    consensus = _consensus(AWARD)
    verifier = BidirectionalVerifier()
    check = [c for c in eligible_checks(consensus)
             if c.check_kind is K.KEY_CONDITION and c.eligible][0]
    runtime = EchoRuntime("Recipient Beta")
    records = [
        verifier.execute(
            verifier.build_request(check, sample_index=i), _contract(AWARD), runtime
        )
        for i in range(4)
    ]
    overlay = integrator.integrate(
        consensus, checks=records, prior_families=FAMILIES
    ).candidates[0]

    assert len(overlay.structural_groups) == 1
    assert overlay.structural_groups[0].total_events == 4
    assert overlay.structural_groups[0].q_g == 0
    assert overlay.structural_contradicting_groups == ()


def test_a_set_valued_alternate_is_not_serialised_as_a_contradiction(integrator):
    """§9.I: the persisted row must not read as target contradiction."""
    for relation, target, alternate in (
        (AWARD, "Recipient Alpha", "Recipient Beta"),
        (BORDERS, "Country Beta", "Country Gamma"),
    ):
        consensus = _consensus(relation, candidates=(_state(relation, target),))
        record, _ = _check(consensus, K.KEY_CONDITION, alternate)
        state = integrator.integrate(
            consensus, checks=[record], prior_families=FAMILIES
        )
        payload = json.loads(json.dumps(state.to_json()))
        assert Layer4EvidenceState.from_json(payload) == state
        row = payload["candidates"][0]
        assert row["structural_contradicting_groups"] == []
        assert row["structural_checks"][0]["outcome"] == "ALTERNATE_RECOVERED"
        assert row["structural_checks"][0]["recovered_value"] == alternate
        assert "CONTRADICT" not in json.dumps(row)


def test_the_alternate_object_is_not_inserted_anywhere(integrator):
    """It is provenance, not a candidate: widening scope is Module 19's."""
    consensus = _consensus(AWARD)
    before = copy.deepcopy(consensus.to_json())
    record, _ = _check(consensus, K.KEY_CONDITION, "Recipient Beta")
    state = integrator.integrate(consensus, checks=[record], prior_families=FAMILIES)

    assert {c.candidate_key for c in state.candidates} == {"recipient alpha"}
    assert state.discovered_candidates == ()
    assert consensus.to_json() == before
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("novelty", "noveltyRate", "new_object"):
        assert forbidden not in blob, forbidden


@pytest.mark.parametrize(
    "text,outcome",
    [
        ("Recipient Alpha", StructuralOutcome.SUPPORT),
        ("Recipient Gamma", StructuralOutcome.UNRESOLVED),   # absence, not denial
        ("NONE", StructuralOutcome.UNRESOLVED),
        ("", StructuralOutcome.UNRESOLVED),
    ],
)
def test_candidate_free_outcomes_map_conservatively(text, outcome):
    consensus = _consensus(AWARD)
    record, _ = _check(consensus, K.CANDIDATE_FREE_RECALL, text)
    evidence = structural_evidence(
        record, target_key="recipient alpha", prior_families=FAMILIES
    )
    assert evidence.outcome is outcome
    if outcome is not StructuralOutcome.SUPPORT:
        assert evidence.outcome is not StructuralOutcome.CONTRADICT


def test_a_failed_check_is_never_a_contradiction():
    consensus = _consensus(BORDERS)
    record, _ = _check(consensus, K.REVERSE, runtime=EchoRuntime(fail=True))
    evidence = structural_evidence(
        record, target_key="country beta", prior_families=FAMILIES
    )
    assert evidence.outcome is StructuralOutcome.UNRESOLVED
    assert evidence.status is CheckExecutionStatus.FAILED
    assert evidence.error is not None


def test_structural_evidence_never_becomes_l_or_a_verifier_reading(integrator):
    consensus = _consensus(BORDERS)
    record, _ = _check(consensus, K.REVERSE, "SUPPORTED")
    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]

    assert overlay.base_l == 0.0 and overlay.base_l_available is False
    assert overlay.specialist_verifier.availability is VerifierAvailability.NOT_REQUESTED
    fields = set(StructuralCheckEvidence.__dataclass_fields__)
    for forbidden in ("verifier_logit", "verifier_probability", "logit",
                      "calibrated", "label_distribution"):
        assert not any(forbidden in name for name in fields), forbidden


def test_a_new_candidate_appears_only_in_the_layer_4_view(integrator):
    from cover_kbc.evidence.graph import build_graph

    consensus = _consensus(AWARD)
    query, contract = compile_query(SUBJECTS[AWARD], AWARD, 0)
    graph = build_graph(query, contract)
    record, _ = _check(
        consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha\nRecipient Gamma"
    )
    before = copy.deepcopy(consensus.to_json())

    state = integrator.integrate(consensus, checks=[record], prior_families=FAMILIES)
    keys = {c.candidate_key for c in state.candidates}
    assert keys == {"recipient alpha", "recipient gamma"}
    discovered = {c.candidate_key for c in state.discovered_candidates}
    assert discovered == {"recipient gamma"}

    assert consensus.to_json() == before          # M16 untouched
    assert graph.candidates == {}                 # M3 untouched
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("add_entity_mentions", "add_evidence", "EvidenceGraph("):
        assert forbidden not in blob, forbidden


def test_a_newly_discovered_candidate_gets_no_cross_model_credit(integrator):
    consensus = _consensus(AWARD)
    record, _ = _check(
        consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha\nRecipient Gamma",
        runtime=EchoRuntime("Recipient Alpha\nRecipient Gamma", family=VERIFIER_FAMILY),
    )
    overlays = {
        c.candidate_key: c
        for c in integrator.integrate(
            consensus, checks=[record], prior_families=FAMILIES
        ).candidates
    }
    new = overlays["recipient gamma"]
    assert new.discovered_by_structural_check is True
    assert new.layer4_x == 0.0
    assert new.cross_model_credit is CrossModelCredit.FIRST_DISCOVERY
    payload = json.dumps(new.to_json())
    for forbidden in ("accepted", "final", "trusted"):
        assert forbidden not in payload, forbidden


# --------------------------------------------------------------------------
# 41-50. The X matrix
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [K.REVERSE, K.COUNTERFACTUAL])
def test_a_shown_candidate_check_never_credits_x(integrator, kind):
    consensus = _consensus(BORDERS)
    record, _ = _check(
        consensus, kind, "SUPPORTED", runtime=EchoRuntime("SUPPORTED", family="qwen")
    )
    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]
    assert overlay.layer4_x == 0.0
    assert overlay.cross_model_credit is CrossModelCredit.SHOWN_CANDIDATE


def test_key_condition_never_credits_x_merely_because_the_target_was_masked(integrator):
    consensus = _consensus(AWARD)
    record, _ = _check(
        consensus, K.KEY_CONDITION, "Recipient Alpha",
        runtime=EchoRuntime("Recipient Alpha", family="qwen"),
    )
    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]
    assert overlay.layer4_x == 0.0
    assert overlay.cross_model_credit is CrossModelCredit.NOT_INDEPENDENT_RECALL


def test_same_family_candidate_free_recall_does_not_credit_x(integrator):
    consensus = _consensus(AWARD)
    record, _ = _check(
        consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha",
        runtime=EchoRuntime("Recipient Alpha", family=ENUMERATOR_FAMILY,
                            model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506"),
    )
    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]
    assert overlay.layer4_x == 0.0
    assert overlay.cross_model_credit is CrossModelCredit.SAME_FAMILY


def test_distinct_family_candidate_free_recall_credits_x_once(integrator):
    consensus = _consensus(AWARD)
    record, _ = _check(
        consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha",
        runtime=EchoRuntime("Recipient Alpha", family=VERIFIER_FAMILY),
    )
    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]
    assert overlay.layer4_x == 1.0
    assert overlay.cross_model_credit is CrossModelCredit.CREDITED
    assert overlay.base_x == 0.0                  # M16's own X is untouched


def test_repeating_the_distinct_family_probe_does_not_multiply_x(integrator):
    consensus = _consensus(AWARD)
    runtime = EchoRuntime("Recipient Alpha", family=VERIFIER_FAMILY)
    verifier = BidirectionalVerifier()
    check = [c for c in eligible_checks(consensus)
             if c.check_kind is K.CANDIDATE_FREE_RECALL][0]
    records = [
        verifier.execute(
            verifier.build_request(check, sample_index=i), _contract(AWARD), runtime
        )
        for i in range(3)
    ]
    overlay = integrator.integrate(
        consensus, checks=records, prior_families=FAMILIES
    ).candidates[0]

    assert overlay.layer4_x == 1.0                # once, not three times
    groups = [g for g in overlay.structural_groups if g.is_recall]
    assert len(groups) == 1 and groups[0].total_events == 3
    assert overlay.layer4_i == overlay.base_i + 1


def test_unknown_prior_families_never_inflate_x(integrator):
    consensus = _consensus(AWARD)
    record, _ = _check(
        consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha",
        runtime=EchoRuntime("Recipient Alpha", family=VERIFIER_FAMILY),
    )
    for families in (None, {}, {"recipient alpha": ()}):
        overlay = integrator.integrate(
            consensus, checks=[record], prior_families=families
        ).candidates[0]
        assert overlay.layer4_x == 0.0, families
        assert overlay.cross_model_credit is CrossModelCredit.UNRESOLVED_PROVENANCE


def test_the_cross_model_rule_checks_every_condition_in_order():
    """Each refusal is named, so a reader can see which condition stopped it."""
    consensus = _consensus(AWARD)
    hidden, _ = _check(
        consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha",
        runtime=EchoRuntime("Recipient Alpha", family=VERIFIER_FAMILY),
    )
    shown, _ = _check(consensus, K.COUNTERFACTUAL, "TARGET")
    masked, _ = _check(consensus, K.KEY_CONDITION, "Recipient Alpha")

    assert cross_model_credit(
        shown, target_key="recipient alpha", prior_families=FAMILIES
    ) is CrossModelCredit.SHOWN_CANDIDATE
    assert cross_model_credit(
        masked, target_key="recipient alpha", prior_families=FAMILIES
    ) is CrossModelCredit.NOT_INDEPENDENT_RECALL
    assert cross_model_credit(
        hidden, target_key="recipient omega", prior_families=FAMILIES
    ) is CrossModelCredit.TARGET_NOT_RECALLED
    assert cross_model_credit(
        hidden, target_key="recipient alpha", prior_families=FAMILIES,
        previously_known=False,
    ) is CrossModelCredit.FIRST_DISCOVERY
    assert cross_model_credit(
        hidden, target_key="recipient alpha",
        prior_families={"recipient alpha": (VERIFIER_FAMILY,)},
    ) is CrossModelCredit.SAME_FAMILY
    assert cross_model_credit(
        hidden, target_key="recipient alpha", prior_families=None
    ) is CrossModelCredit.UNRESOLVED_PROVENANCE
    assert cross_model_credit(
        hidden, target_key="recipient alpha", prior_families=FAMILIES
    ) is CrossModelCredit.CREDITED
    # Seven states: six named refusals plus the credit itself.
    assert len(set(CrossModelCredit)) == 7


def test_prior_families_come_from_module_3():
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.types import (
        DecodeProfile, GenerationRecord, IndependenceGroup, ViewFamily,
    )

    query, contract = compile_query(SUBJECTS[AWARD], AWARD, 0)
    graph = build_graph(query, contract)
    record = GenerationRecord(
        record_id="r0", query=query, view_id="direct", view_family=ViewFamily.DIRECT,
        independence_group=IndependenceGroup.DIRECT_RECALL, run_id=0,
        model_id="offline/enumerator", prompt="p", prompt_hash="h",
        raw_output="Recipient Alpha", decode_profile=DecodeProfile(name="d"),
        model_family=ENUMERATOR_FAMILY,
    )
    graph.add_entity_mentions(record, ["Recipient Alpha"])
    assert prior_family_map(graph) == {"recipient alpha": (ENUMERATOR_FAMILY,)}


def test_module_16s_existing_x_is_never_overwritten(integrator):
    consensus = _consensus(AWARD, candidates=(_state(AWARD, x=1.0),))
    record, _ = _check(
        consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha",
        runtime=EchoRuntime("Recipient Alpha", family=ENUMERATOR_FAMILY),
    )
    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]
    assert overlay.base_x == 1.0
    assert overlay.layer4_x == 1.0             # not lowered by a same-family probe


def test_cross_model_support_never_increases_f(integrator):
    consensus = _consensus(AWARD)
    record, _ = _check(
        consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha",
        runtime=EchoRuntime("Recipient Alpha", family=VERIFIER_FAMILY),
    )
    overlay = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    ).candidates[0]
    assert overlay.layer4_x == 1.0
    assert overlay.base_f == 0.4
    fields = set(CandidateEvidenceOverlay.__dataclass_fields__)
    assert "layer4_f" not in fields
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    assert "base_f +" not in blob


def test_audit_0008_matrix_remains_green():
    from cover_kbc.scoring import DEFAULT_SCORING, cross_model_term, support_term
    from cover_kbc.types import (
        Candidate, EdgeType, Evidence, EvidenceMode, IndependenceGroup,
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
        independence_group=IndependenceGroup.BLIND_VERIFIER, view_id="b",
        model_id="q", run_id=0, record_id="v1", mode=EvidenceMode.SHOWN_CANDIDATE,
    ))
    assert support_term(candidate, contract, DEFAULT_SCORING) == baseline
    assert cross_model_term(candidate, DEFAULT_SCORING) == 0.0


# --------------------------------------------------------------------------
# 51-60. Independence, q_g, origins, cost
# --------------------------------------------------------------------------


def test_q_g_is_a_max_over_a_structural_group():
    checks = [
        StructuralCheckEvidence(
            check_kind="REVERSE", independence_group="M18_REVERSE",
            outcome=outcome, status=CheckExecutionStatus.RESOLVED,
            origin_event_id=f"o{index}",
        )
        for index, outcome in enumerate((
            StructuralOutcome.UNRESOLVED, StructuralOutcome.SUPPORT,
            StructuralOutcome.UNRESOLVED,
        ))
    ]
    groups = structural_groups(checks)
    assert len(groups) == 1
    assert groups[0].q_g == 1 and groups[0].total_events == 3
    with pytest.raises(Layer4IntegrationError, match="categorical"):
        StructuralGroupSupport(group_key="g", q_g=3, total_events=3)


def test_ten_repeats_in_one_group_do_not_inflate_i(integrator):
    consensus = _consensus(BORDERS)
    verifier = BidirectionalVerifier()
    check = [c for c in eligible_checks(consensus)
             if c.check_kind is K.REVERSE and c.eligible][0]
    runtime = EchoRuntime("SUPPORTED")
    records = [
        verifier.execute(
            verifier.build_request(check, sample_index=i), _contract(BORDERS), runtime
        )
        for i in range(10)
    ]
    overlay = integrator.integrate(
        consensus, checks=records, prior_families=FAMILIES
    ).candidates[0]

    assert len({r.origin_event_id for r in records}) == 10       # ten origins
    groups = overlay.structural_groups
    assert len(groups) == 1 and groups[0].total_events == 10
    # Shown-candidate checks are anchored, so I does not move at all.
    assert overlay.layer4_i == overlay.base_i


def test_three_counterfactual_classes_are_one_group(integrator):
    consensus = _consensus(AWARD)
    verifier = BidirectionalVerifier()
    checks = [c for c in eligible_checks(consensus)
              if c.check_kind is K.COUNTERFACTUAL][:3]
    assert len({c.counterfactual_class for c in checks}) == 3
    runtime = EchoRuntime("TARGET")
    records = [
        verifier.execute(verifier.build_request(c), _contract(AWARD), runtime)
        for c in checks
    ]
    overlay = integrator.integrate(
        consensus, checks=records, prior_families=FAMILIES
    ).candidates[0]
    assert [g.group_key for g in overlay.structural_groups] == ["M18_COUNTERFACTUAL"]
    assert overlay.structural_groups[0].total_events == 3


def test_one_output_naming_five_candidates_costs_one_call(integrator):
    consensus = _consensus(AWARD)
    record, runtime = _check(
        consensus, K.CANDIDATE_FREE_RECALL,
        "Recipient Alpha\nRecipient Gamma\nRecipient Delta\nRecipient Epsilon\n"
        "Recipient Zeta",
    )
    state = integrator.integrate(consensus, checks=[record], prior_families=FAMILIES)

    assert len(state.candidates) == 5
    assert sum(len(c.structural_checks) for c in state.candidates) == 5
    # Five evidence events, one physical call.
    assert state.cost.structural_calls == 1 == runtime.gen_calls
    assert state.cost.unique_origin_events == 1


def test_a_record_projected_twice_is_charged_once():
    consensus = _consensus(BORDERS)
    record, _ = _check(consensus, K.REVERSE, "SUPPORTED")
    once = cost_ledger((), [record])
    twice = cost_ledger((), [record, record])
    assert once == twice
    assert once.structural_calls == 1


def test_conflicting_origin_metadata_fails_loudly():
    consensus = _consensus(BORDERS)
    record, _ = _check(consensus, K.REVERSE, "SUPPORTED")
    clashing = replace(record, model_id="another/model")
    check_origin_consistency([record, record])          # identical: fine
    with pytest.raises(Layer4ProvenanceError, match="different model_id"):
        check_origin_consistency([record, clashing])


def test_the_ledger_equals_the_physical_calls(integrator):
    consensus = _consensus(AWARD)
    verification, verifier_runtime = _verify(AWARD)
    record, check_runtime = _check(consensus, K.COUNTERFACTUAL, "TARGET")
    state = integrator.integrate(
        consensus, verifications=[verification], checks=[record],
        prior_families=FAMILIES,
    )
    assert state.cost.verifier_calls == verifier_runtime.label_calls
    assert state.cost.structural_calls == check_runtime.gen_calls
    assert state.cost.total_calls == (
        verifier_runtime.label_calls + check_runtime.gen_calls
    )
    assert state.cost.integration_calls == 0


# --------------------------------------------------------------------------
# 61-72. Numeric, null, identity, pending checks
# --------------------------------------------------------------------------


def test_numeric_evidence_attaches_to_module_12s_cluster(integrator):
    consensus = _consensus(CAPACITY, clusters=(_cluster(competing=1),))
    verification, _ = _verify(CAPACITY, cluster_index=0)
    record, _ = _check(consensus, K.KEY_CONDITION, "25,000")

    state = integrator.integrate(
        consensus, verifications=[verification], checks=[record],
        prior_families=FAMILIES,
    )
    assert len(state.numeric_targets) == 1
    target = state.numeric_targets[0]
    assert target.representative == 25000.0        # M12's, not recomputed
    assert target.canonical_unit == "persons"
    assert target.competing_clusters == 1          # competition stays visible
    assert target.specialist_verifier.available
    assert len(target.structural_checks) == 1
    assert target.structural_checks[0].outcome is StructuralOutcome.SUPPORT

    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("cluster_values", "median", "relative_distance", "tolerance",
                      "0.05", "recluster"):
        assert forbidden not in blob, forbidden


def test_a_null_proposition_never_becomes_a_candidate(integrator):
    consensus = _consensus(DEATH)
    verification, _ = _verify(
        DEATH, proposition=QueryPropositionKind.NO_KNOWN_QUALIFYING_LOCALITY
    )
    state = integrator.integrate(
        consensus, verifications=[verification], prior_families=FAMILIES
    )
    assert len(state.propositions) == 1
    assert state.propositions[0].proposition == "NO_KNOWN_QUALIFYING_LOCALITY"
    keys = {c.candidate_key for c in state.candidates}
    assert keys == {"city beta"}
    for fake in ("NO_KNOWN_QUALIFYING_LOCALITY", "__EMPTY__", "NONE", "LIVING"):
        assert fake not in keys


def test_audit_0024_semantics_survive_integration(integrator):
    from cover_kbc.specialists import asserts_relation_level_absence, is_epistemic_abstention

    assert is_epistemic_abstention("UNKNOWN")
    assert not asserts_relation_level_absence("UNKNOWN", sentinel_is_defined=True)
    consensus = _consensus(DEATH)
    record, _ = _check(consensus, K.CANDIDATE_FREE_RECALL, "NONE\nUNKNOWN\nCity Beta")
    state = integrator.integrate(consensus, checks=[record], prior_families=FAMILIES)
    keys = {c.candidate_key for c in state.candidates}
    assert "none" not in keys and "unknown" not in keys
    assert "city beta" in keys


def test_strict_identity_is_preserved(integrator):
    consensus = _consensus(AWARD, candidates=(_state(AWARD, "Alpha Exchange"),))
    record, _ = _check(consensus, K.CANDIDATE_FREE_RECALL, "The Alpha Exchange")
    state = integrator.integrate(consensus, checks=[record], prior_families=FAMILIES)
    keys = {c.candidate_key for c in state.candidates}
    assert len(keys) == 2          # not merged on a lexical fold
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES).casefold()
    for forbidden in ("alias_hint", "levenshtein", "difflib", "fuzz", "embedding",
                      "cosine", "similarity"):
        assert forbidden not in blob, forbidden


def test_a_pending_check_stays_pending_until_one_runs(integrator):
    pending = PendingDownstreamCheck(
        source_module="M15", kind="REVERSE_ADJACENCY", reason="SINGLETON_CANDIDATE",
        candidate=DISPLAYS[BORDERS],
    )
    consensus = _consensus(BORDERS, pending=(pending,))

    unexecuted = integrator.integrate(consensus, prior_families=FAMILIES)
    assert len(unexecuted.pending_checks) == 1
    assert unexecuted.pending_checks[0].status is (
        CheckExecutionStatus.ELIGIBLE_NOT_SCHEDULED
    )

    record, _ = _check(consensus, K.REVERSE, "SUPPORTED")
    executed = integrator.integrate(
        consensus, checks=[record], prior_families=FAMILIES
    )
    status = executed.pending_checks[0]
    assert status.status is CheckExecutionStatus.RESOLVED
    assert status.executed_origin_ids == (record.origin_event_id,)
    # Execution status is not truth status.
    payload = json.dumps(status.to_json())
    for forbidden in ("factual_resolved", "candidate_resolved", "risk_resolved"):
        assert forbidden not in payload, forbidden


def test_a_failed_check_leaves_the_pending_request_failed(integrator):
    pending = PendingDownstreamCheck(
        source_module="M15", kind="REVERSE_ADJACENCY", reason="SINGLETON_CANDIDATE",
        candidate=DISPLAYS[BORDERS],
    )
    consensus = _consensus(BORDERS, pending=(pending,))
    record, _ = _check(consensus, K.REVERSE, runtime=EchoRuntime(fail=True))
    state = integrator.integrate(consensus, checks=[record], prior_families=FAMILIES)
    assert state.pending_checks[0].status is CheckExecutionStatus.FAILED


def test_execution_states_stay_distinguishable():
    assert len(set(CheckExecutionStatus)) == 5
    assert {s.value for s in CheckExecutionStatus} == {
        "NOT_ELIGIBLE", "ELIGIBLE_NOT_SCHEDULED", "FAILED", "UNRESOLVED", "RESOLVED"
    }


# --------------------------------------------------------------------------
# 73-90. Boundaries, config, persistence
# --------------------------------------------------------------------------


def test_no_final_decision_field_exists():
    for cls in (CandidateEvidenceOverlay, Layer4EvidenceState,
                StructuralCheckEvidence, SpecialistVerifierEvidence):
        fields = set(cls.__dataclass_fields__)
        for forbidden in ("accepted", "rejected", "final", "prediction", "prune",
                          "rank", "should_stop", "decision", "confidence"):
            assert not any(forbidden in name for name in fields), (cls, forbidden)


def test_no_global_confidence_score_is_invented():
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("final_confidence", "combined_probability",
                      "verification_score", "weighted_sum", "w1", "alpha *",
                      "fitted"):
        assert forbidden not in blob, forbidden


def test_no_accepted_set_and_no_closure_stop(integrator):
    consensus = _consensus(BORDERS)
    verification, _ = _verify(BORDERS)
    record, _ = _check(consensus, K.REVERSE, "SUPPORTED")
    payload = json.dumps(integrator.integrate(
        consensus, verifications=[verification], checks=[record],
        prior_families=FAMILIES,
    ).to_json())
    for forbidden in ("accepted_set", "A_t", "final_set", "should_stop", "CLOSED",
                      "closure"):
        assert forbidden not in payload, forbidden


def test_no_module_19_20_or_21_logic_exists():
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES)
    for forbidden in ("residual", "missingness", "novelty", "singleton_ratio",
                      "facet_gap", "unresolved_mass", "saturation", "coverage_map",
                      "allocate_budget", "schedule", "next_action",
                      "expected_value", "should_stop", "STOP"):
        assert forbidden not in blob, f"Layer 4 implements {forbidden}"


def test_no_dola_and_no_scheduling():
    blob = " ".join(_code_without_prose(n) for n in L4_MODULES).casefold()
    for forbidden in ("dola", "hidden_state", "auto_run", "auto_verify",
                      "escalate", "trigger_check"):
        assert forbidden not in blob, forbidden


def test_configuration_failures_are_loud():
    with pytest.raises(ValueError, match="unsupported layer4_integration mode"):
        Layer4IntegrationConfig.from_mapping({"enabled": True, "mode": "production"})
    with pytest.raises(ValueError, match="unknown layer4_integration key"):
        Layer4IntegrationConfig.from_mapping({"enabled": True, "threshold": 0.5})
    with pytest.raises(ValueError, match="unsupported integration_version"):
        Layer4IntegrationConfig.from_mapping(
            {"enabled": True, "integration_version": "layer4-v9"}
        )
    with pytest.raises(ValueError, match="requires consensus"):
        build_layer4_integrator({"enabled": True}, consensus_enabled=False)
    assert build_layer4_integrator(None, consensus_enabled=True) is None


def test_the_config_has_no_tunable(integrator):
    fields = set(Layer4IntegrationConfig.__dataclass_fields__)
    assert fields == {"enabled", "mode", "integration_version"}


def test_the_shipped_configs_keep_layer4_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        block = yaml.safe_load(Path(name).read_text())["layer4_integration"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["integration_version"] == INTEGRATION_VERSION, name


@pytest.mark.parametrize("relation", [AWARD, DEATH, BORDERS, CAPACITY])
def test_every_public_type_round_trips(integrator, relation):
    clusters = (_cluster(),) if relation == CAPACITY else ()
    consensus = _consensus(relation, clusters=clusters)
    verifications = []
    checks = []
    if relation == CAPACITY:
        verification, _ = _verify(relation, cluster_index=0)
        verifications.append(verification)
    else:
        verification, _ = _verify(relation)
        verifications.append(verification)
        record, _ = _check(consensus, K.COUNTERFACTUAL, "TARGET")
        checks.append(record)

    state = integrator.integrate(
        consensus, verifications=verifications, checks=checks,
        prior_families=FAMILIES,
    )
    payload = json.loads(json.dumps(state.to_json()))
    assert Layer4EvidenceState.from_json(payload) == state
    for original, entry in zip(state.candidates, payload["candidates"]):
        assert CandidateEvidenceOverlay.from_json(entry) == original
        assert SpecialistVerifierEvidence.from_json(
            entry["specialist_verifier"]
        ) == original.specialist_verifier
        for check, check_entry in zip(original.structural_checks,
                                      entry["structural_checks"]):
            assert StructuralCheckEvidence.from_json(check_entry) == check
        for group, group_entry in zip(original.structural_groups,
                                      entry["structural_groups"]):
            assert StructuralGroupSupport.from_json(group_entry) == group
    for original, entry in zip(state.numeric_targets, payload["numeric_targets"]):
        assert NumericTargetOverlay.from_json(entry) == original
    for original, entry in zip(state.propositions, payload["propositions"]):
        assert PropositionEvidenceOverlay.from_json(entry) == original
    for original, entry in zip(state.pending_checks, payload["pending_checks"]):
        assert PendingCheckStatus.from_json(entry) == original
    assert Layer4CostLedger.from_json(payload["cost"]) == state.cost


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
# Pipeline seam and shadow invariance
# --------------------------------------------------------------------------


def _pipeline(with_layer4=True, runtime=None):
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler,
    )
    from cover_kbc.specialists import LargeSetSpecialist

    return CoverPipeline(
        runtime or ScriptedRuntime({}, model_id="offline/enumerator"),
        PipelineConfig(), profiler=QueryProfiler(),
        prompt_compiler=PromptProgramCompiler(), retriever=ParametricRetriever(),
        large_set_specialist=LargeSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        specialist_verifier=SpecialistVerifier(),
        bidirectional_verifier=BidirectionalVerifier(),
        layer4_integrator=Layer4EvidenceIntegrator() if with_layer4 else None,
    )


def test_the_pipeline_seam_spends_nothing():
    from cover_kbc.types import Query

    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    pipeline = _pipeline(runtime=runtime)
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    before = runtime.calls
    pipeline.decide_graph(graph)

    assert runtime.calls == before
    assert len(pipeline.layer4_results) == 1
    state = pipeline.layer4_results[0]
    assert state.cost.total_calls == 0
    assert state.cost.integration_calls == 0


def test_the_pipeline_refuses_an_integrator_without_consensus():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a consensus engine"):
        CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            layer4_integrator=Layer4EvidenceIntegrator(),
        )


def test_module_3_and_module_5_are_unchanged_by_integration():
    from cover_kbc.scoring import DEFAULT_SCORING, candidate_state
    from cover_kbc.types import Query

    pipeline = _pipeline()
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    before_graph = copy.deepcopy(graph.to_json())
    before_states = [
        candidate_state(c, graph.contract, DEFAULT_SCORING).to_json()
        for c in graph.candidates.values()
    ]
    pipeline.decide_graph(graph)
    after_states = [
        candidate_state(c, graph.contract, DEFAULT_SCORING).to_json()
        for c in graph.candidates.values()
    ]
    assert graph.to_json() == before_graph
    assert after_states == before_states


@pytest.fixture(scope="module")
def cli():
    scripts_dir = str(Path("scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_staged", "scripts/run_staged.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(tmp_path: Path, *, layer4: bool, tag: str) -> Path:
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
    config["specialist_verifier"] = {**config["specialist_verifier"], "enabled": True}
    config["bidirectional_verification"] = {
        **config["bidirectional_verification"], "enabled": True,
    }
    config["layer4_integration"] = {
        **config["layer4_integration"], "enabled": layer4,
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


@pytest.mark.parametrize("relation", [AWARD, BORDERS])
def test_shadow_mode_changes_no_production_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run_cli(cli, monkeypatch, _config(tmp_path, layer4=True, tag="on"), on, relation)
    _run_cli(cli, monkeypatch, _config(tmp_path, layer4=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in PRODUCTION_ARTEFACTS:
        left, right = on / name, off / name
        if not left.exists() and not right.exists():
            continue
        assert left.read_bytes() == right.read_bytes(), name

    assert (on / "layer4_evidence.jsonl").is_file()
    assert not (off / "layer4_evidence.jsonl").exists()


def test_the_artefact_is_manifest_ordered_and_reloads_identically(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run_cli(cli, monkeypatch, _config(tmp_path, layer4=True, tag="on"), run_dir, AWARD)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "layer4_evidence.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["SubjectEntity"], r["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        assert row["integration_version"] == INTEGRATION_VERSION
        assert row["base_consensus_version"] == "m16-v1"
        assert row["cost"]["integration_calls"] == 0
        reloaded = Layer4EvidenceState.from_json(row)
        assert json.loads(json.dumps(reloaded.to_json())) == row
        for forbidden in ("gold", "ObjectEntities", "accepted", "rejected",
                          "prediction", "should_stop", "residual", "next_action"):
            assert forbidden not in json.dumps(row), forbidden
