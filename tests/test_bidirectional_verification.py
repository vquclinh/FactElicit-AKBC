"""Module 18 - Bidirectional and Counterfactual Verification conformance.

Five things have to hold:

* §14's **four** mechanisms exist, each asking a structurally different
  question - and no generic "think again" anywhere;
* the counterfactual near-miss class is Module 0's own rule text, so no code
  path can invent a factual alternative;
* the candidate-free probe never sees the candidate, and the target is used
  only *after* inference;
* §14 says a natural recall "increases X" while Audit 0008 defines X as
  cross-model - M18 records the provenance and credits nothing;
* eligibility is not scheduling, and nothing runs without an explicit request.

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
from cover_kbc.evidence.consensus_types import (
    CandidateConsensusState,
    NumericClusterConsensus,
    PendingDownstreamCheck,
    QueryConsensusResult,
    RiskFlag,
)
from cover_kbc.models.base import GenerationResult
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.verification.bidirectional_contracts import (
    CHECK_CONTRACT_VERSION,
    CHECK_PROFILES,
    EXPECTED_HARD_NEGATIVES,
    check_profile,
    check_registry_consistency,
    counterfactual_class_text,
    supports_reverse,
)
from cover_kbc.verification.bidirectional_prompts import (
    CANDIDATE_FREE_TEMPLATE_ID,
    COUNTERFACTUAL_TEMPLATE_ID,
    KEY_CONDITION_TEMPLATE_ID,
    REVERSE_TEMPLATE_ID,
    TEMPLATE_IDS,
    render_candidate_free,
    render_counterfactual,
    render_key_condition,
    render_reverse,
)
from cover_kbc.verification.bidirectional_types import (
    CHECK_VERSION,
    BidirectionalCheckError,
    BidirectionalCheckKind,
    BidirectionalCheckRecord,
    BidirectionalCheckRequest,
    CheckIneligible,
    CheckParseStatus,
    CheckTarget,
    CheckTargetKind,
    CounterfactualOutcome,
    EligibleCheck,
    PendingCheckOrigin,
    QueryBidirectionalResult,
    RecallOutcome,
    RecalledCandidate,
    ReconstructionOutcome,
    ReverseOutcome,
    UnsupportedCheckRelation,
    derive_check_origin_id,
)
from cover_kbc.verification.bidirectional_verifier import (
    BidirectionalVerifier,
    BidirectionalVerifierConfig,
    build_bidirectional_verifier,
    eligible_checks,
    parse_candidate_free,
    parse_counterfactual,
    parse_reconstruction,
    parse_reverse,
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
K = BidirectionalCheckKind

M18_MODULES = (
    "bidirectional_types.py", "bidirectional_contracts.py",
    "bidirectional_prompts.py", "bidirectional_verifier.py",
)
CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
PRODUCTION_ARTEFACTS = (
    "predictions.jsonl", "diagnostics.json", "trace.jsonl",
    "stage_a_enumerated.jsonl", "stage_b_verified.jsonl", "calls_enumerate.jsonl",
    "calls_verify.jsonl", "query_profiles.jsonl", "prompt_programs.jsonl",
    "parametric_memory.jsonl", "numeric_specialist.jsonl",
    "large_open_set_specialist.jsonl", "null_temporal_specialist.jsonl",
    "small_set_specialist.jsonl", "atomic_consensus.jsonl",
    "specialist_verification.jsonl", "metrics.json",
)

#: Upstream signals that must never reach a Module 18 prompt.
POISON = (
    "The generator is 99% sure Candidate Alpha is correct.",
    "M17 returned VALID.",
    "M16 independent_support = 5.",
    "M15 suspects a subsidiary.",
    "risk = HIGH.",
)


def _code_without_prose(name: str) -> str:
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


class EchoRuntime(ScriptedRuntime):
    """Returns one scripted text for every generation, recording the prompt."""

    def __init__(self, text: str = "UNKNOWN", *, fail: bool = False,
                 model_id: str = "offline/enumerator", family: str = "mistral", **kw):
        super().__init__({}, model_id=model_id, family=family, **kw)
        self.text = text
        self.fail = fail
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []
        self.gen_calls = 0

    def generate(self, request):
        self.gen_calls += 1
        self.prompts.append(request.prompt)
        self.system_prompts.append(request.system_prompt or "")
        if self.fail:
            raise RuntimeError("the model fell over")
        return GenerationResult(
            text=self.text, model_id=self.spec.model_id,
            generated_tokens=6, prompt_tokens=42,
        )


@pytest.fixture
def verifier():
    return BidirectionalVerifier()


def _contract(relation: str):
    _, contract = compile_query(SUBJECTS[relation], relation, 0)
    return contract


def _state(relation, key=None, display=None, *, violation=False, risk=()):
    display = display or DISPLAYS.get(relation, "Object Alpha")
    key = key or CONTRACTS[relation].strict_key(display)
    return CandidateConsensusState(
        relation=relation, subject=SUBJECTS[relation], row_index=0,
        candidate_key=key, display=display, candidate_kind="ENTITY",
        hard_contract_violation=violation, risk_flags=tuple(risk),
        i_independent_support=4, f_support=0.8, d_semantic=1.0, l_logit=0.9,
    )


def _cluster(index=0, representative=25000.0, unit="persons"):
    return NumericClusterConsensus(
        cluster_index=index, representative=representative, dispersion=0.01,
        canonical_unit=unit, values=(representative,), total_support=2,
        independent_support=2, independence_groups=("a", "b"),
    )


def _consensus(relation, *, candidates=None, clusters=(), pending=()):
    if candidates is None:
        candidates = (
            () if relation in NUMERIC_RELATIONS else (_state(relation),)
        )
    return QueryConsensusResult(
        consensus_version="m16-v1", relation=relation, subject=SUBJECTS[relation],
        row_index=0, applicable_specialist="M16",
        candidates=tuple(candidates), numeric_clusters=tuple(clusters),
        pending_checks=tuple(pending),
    )


def _check(consensus, kind, *, index=0):
    checks = [c for c in eligible_checks(consensus) if c.check_kind is kind]
    assert checks, f"no {kind.value} check for {consensus.relation}"
    return checks[index]


def _run(verifier, consensus, kind, text="UNKNOWN", *, index=0, runtime=None, **kwargs):
    check = _check(consensus, kind, index=index)
    runtime = runtime or EchoRuntime(text)
    record = verifier.execute(
        verifier.build_request(check), _contract(consensus.relation), runtime, **kwargs
    )
    return record, runtime


# --------------------------------------------------------------------------
# 1-5. Proposal mapping, four mechanisms, one registry
# --------------------------------------------------------------------------


def test_section_14_defines_exactly_four_mechanisms():
    assert [k.value for k in BidirectionalCheckKind] == [
        "REVERSE", "KEY_CONDITION", "COUNTERFACTUAL", "CANDIDATE_FREE_RECALL"
    ]
    assert set(TEMPLATE_IDS) == set(BidirectionalCheckKind)
    assert sorted(TEMPLATE_IDS.values()) == sorted({
        REVERSE_TEMPLATE_ID, KEY_CONDITION_TEMPLATE_ID,
        COUNTERFACTUAL_TEMPLATE_ID, CANDIDATE_FREE_TEMPLATE_ID,
    })
    check_registry_consistency()


def test_no_generic_self_correction_exists():
    """§14 opens by contrasting M18 with a generic 'think again'."""
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES).casefold()
    for forbidden in ("think again", "review your", "reconsider", "are you sure",
                      "double-check", "double check", "reflect on", "previous answer",
                      "your earlier", "try again", "self-correct"):
        assert forbidden not in blob, f"M18 implements {forbidden}"


def test_the_registry_is_central_and_relation_aware():
    for name in ("bidirectional_prompts.py", "bidirectional_verifier.py",
                 "bidirectional_types.py"):
        code = _code_without_prose(name)
        for relation in CONTRACTS:
            assert relation not in code, f"{name} branches on {relation}"
    registry = _code_without_prose("bidirectional_contracts.py")
    for relation in CONTRACTS:
        assert relation in registry
    assert {p.relation for p in CHECK_PROFILES} == set(CONTRACTS)


def test_an_unknown_relation_fails_closed():
    with pytest.raises(UnsupportedCheckRelation, match="no check profile"):
        check_profile("someOtherRelation")


def test_the_registry_rejects_a_drifting_declaration(monkeypatch):
    from cover_kbc.verification import bidirectional_contracts

    broken = tuple(
        replace(p, rationale="") if p.relation == STOCK else p for p in CHECK_PROFILES
    )
    monkeypatch.setattr(bidirectional_contracts, "CHECK_PROFILES", broken)
    monkeypatch.setattr(
        bidirectional_contracts, "_BY_RELATION", {p.relation: p for p in broken}
    )
    with pytest.raises(ValueError, match="no recorded rationale"):
        check_registry_consistency()


def test_hard_negative_counts_are_pinned_so_class_ids_cannot_shift():
    for relation, expected in EXPECTED_HARD_NEGATIVES.items():
        assert len(CONTRACTS[relation].hard_negative_rules) == expected, relation


# --------------------------------------------------------------------------
# 6-10. Reverse check
# --------------------------------------------------------------------------


def test_reverse_is_declared_only_where_it_is_meaningful():
    """§14: "when the relation supports a meaningful reverse question"."""
    assert supports_reverse(BORDERS)
    for relation in (AWARD, DEATH, STOCK, CAPACITY, AREA):
        assert not supports_reverse(relation), relation
        profile = check_profile(relation)
        # The decision is recorded either way, with its reason.
        assert profile.reverse_rationale
        assert "unbounded" in profile.reverse_rationale.casefold() or (
            "no subject" in profile.reverse_rationale.casefold()
        )


def test_no_inverse_relation_is_invented():
    """Checked on what a model could be asked, not on the recorded reasoning.

    The registry's rationales name these framings precisely to record that they
    are refused, so the scan runs over the *renderers* and over every prompt
    the four mechanisms can produce.
    """
    blob = " ".join(
        _code_without_prose(n)
        for n in ("bidirectional_prompts.py", "bidirectional_verifier.py")
    ).casefold()
    for forbidden in ("list all companies", "list all recipients", "all winners",
                      "who died in", "inverse relation"):
        assert forbidden not in blob, forbidden

    verifier = BidirectionalVerifier()
    for relation in ENTITY_RELATIONS + NUMERIC_RELATIONS:
        clusters = (_cluster(),) if relation in NUMERIC_RELATIONS else ()
        consensus = _consensus(relation, clusters=clusters)
        for check in verifier.catalogue(consensus):
            if not check.eligible:
                continue
            runtime = EchoRuntime("UNKNOWN")
            verifier.execute(
                verifier.build_request(check), _contract(relation), runtime
            )
            for prompt in runtime.prompts:
                folded = prompt.casefold()
                for forbidden in ("list all companies", "list all recipients",
                                  "all winners", "who died in"):
                    assert forbidden not in folded, f"{relation}: {forbidden}"


def test_the_border_reverse_prompt_is_structurally_reversed():
    profile, contract = check_profile(BORDERS), _contract(BORDERS)
    rendered = render_reverse(
        profile, contract, subject=SUBJECTS[BORDERS], candidate=DISPLAYS[BORDERS]
    )
    prompt = rendered.prompt
    # The candidate is put in the subject position and the subject is asked about.
    assert f'Take "{DISPLAYS[BORDERS]}" as the subject.' in prompt
    assert f'does "{SUBJECTS[BORDERS]}" satisfy this relation for it?' in prompt
    assert rendered.candidate_shown is True

    # Not a renamed Module 17 prompt: different answer vocabulary, no A/B/C.
    assert "SUPPORTED" in prompt and "CONTRADICTED" in prompt
    assert "A = VALID" not in prompt and "B = INVALID" not in prompt


def test_reverse_cannot_be_requested_where_the_registry_forbids_it(verifier):
    consensus = _consensus(STOCK)
    reverse = [c for c in eligible_checks(consensus) if c.check_kind is K.REVERSE]
    assert reverse and not reverse[0].eligible
    assert reverse[0].ineligible_reason is CheckIneligible.RELATION_HAS_NO_REVERSE
    with pytest.raises(BidirectionalCheckError, match="no REVERSE check"):
        verifier.build_request(reverse[0])
    with pytest.raises(BidirectionalCheckError, match="declares no reverse framing"):
        render_reverse(
            check_profile(STOCK), _contract(STOCK), subject="s", candidate="c"
        )


@pytest.mark.parametrize(
    "text,outcome,status",
    [
        ("SUPPORTED", ReverseOutcome.SUPPORTED, CheckParseStatus.OK),
        ("CONTRADICTED", ReverseOutcome.CONTRADICTED, CheckParseStatus.OK),
        ("UNRESOLVED", ReverseOutcome.UNRESOLVED, CheckParseStatus.ABSTAINED),
        ("I don't know", ReverseOutcome.UNRESOLVED, CheckParseStatus.ABSTAINED),
        ("", None, CheckParseStatus.EMPTY),
        ("banana", None, CheckParseStatus.MALFORMED),
    ],
)
def test_reverse_parsing_is_bounded(text, outcome, status):
    assert parse_reverse(text) == (outcome, status)


def test_reverse_does_not_run_for_every_border_candidate(verifier):
    """§11.1's minimal-change rule survives into Module 18."""
    consensus = _consensus(BORDERS, candidates=(
        _state(BORDERS, display="Country Beta"),
        _state(BORDERS, display="Country Gamma"),
        _state(BORDERS, display="Country Delta"),
    ))
    runtime = EchoRuntime("SUPPORTED")
    catalogue = verifier.catalogue(consensus)
    assert runtime.gen_calls == 0            # cataloguing spends nothing

    reverse = [c for c in catalogue if c.check_kind is K.REVERSE and c.eligible]
    assert len(reverse) == 3                 # three are *possible*
    verifier.execute(
        verifier.build_request(reverse[0]), _contract(BORDERS), runtime
    )
    assert runtime.gen_calls == 1            # exactly the one that was asked for


# --------------------------------------------------------------------------
# 11-17. Key-condition reconstruction
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", ENTITY_RELATIONS + NUMERIC_RELATIONS)
def test_the_key_condition_prompt_masks_the_decisive_value(relation):
    rendered = render_key_condition(
        check_profile(relation), _contract(relation), subject=SUBJECTS[relation]
    )
    assert rendered.candidate_shown is False
    assert SUBJECTS[relation] in rendered.prompt
    if relation in DISPLAYS:
        assert DISPLAYS[relation] not in rendered.prompt
    assert "UNKNOWN" in rendered.prompt          # an explicit way out


def test_key_condition_is_not_self_correction():
    for relation in ENTITY_RELATIONS:
        prompt = render_key_condition(
            check_profile(relation), _contract(relation), subject=SUBJECTS[relation]
        ).prompt.casefold()
        for forbidden in ("your answer", "previously", "again", "confirm",
                          "verify that", "reconsider"):
            assert forbidden not in prompt, f"{relation}: {forbidden}"


@pytest.mark.parametrize(
    "text,outcome,status",
    [
        ("Recipient Alpha", ReconstructionOutcome.TARGET_RECOVERED, CheckParseStatus.OK),
        ("Recipient Omega", ReconstructionOutcome.DIFFERENT_VALUE_RECOVERED,
         CheckParseStatus.OK),
        ("UNKNOWN", ReconstructionOutcome.UNRESOLVED, CheckParseStatus.ABSTAINED),
        ("NONE", ReconstructionOutcome.UNRESOLVED, CheckParseStatus.ABSTAINED),
        ("", None, CheckParseStatus.EMPTY),
    ],
)
def test_entity_reconstruction_uses_strict_identity(text, outcome, status):
    result = parse_reconstruction(
        text, _contract(AWARD), check_profile(AWARD),
        target_display=DISPLAYS[AWARD],
    )
    assert (result[0], result[2]) == (outcome, status)


def test_a_recovered_alternative_is_evidence_not_a_rejection(verifier):
    consensus = _consensus(AWARD)
    record, _ = _run(verifier, consensus, K.KEY_CONDITION, "Recipient Omega")
    assert record.reconstruction_outcome is ReconstructionOutcome.DIFFERENT_VALUE_RECOVERED
    assert record.recovered_value == "Recipient Omega"
    payload = json.dumps(record.to_json())
    for forbidden in ("rejected", "accepted", "invalid", "prune"):
        assert forbidden not in payload.casefold(), forbidden


def test_an_alias_like_recovery_is_not_merged():
    """Audit 0006: a lexical fold is not identity."""
    result = parse_reconstruction(
        "The Recipient Alpha", _contract(AWARD), check_profile(AWARD),
        target_display="Recipient Alpha",
    )
    assert result[0] is ReconstructionOutcome.DIFFERENT_VALUE_RECOVERED
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    assert "alias_hint" not in blob


@pytest.mark.parametrize(
    "text,outcome",
    [
        ("25,000", ReconstructionOutcome.TARGET_RECOVERED),
        ("25000 persons", ReconstructionOutcome.TARGET_RECOVERED),
        ("61000", ReconstructionOutcome.DIFFERENT_VALUE_RECOVERED),
    ],
)
def test_numeric_reconstruction_reuses_module_12_semantics(text, outcome):
    result = parse_reconstruction(
        text, _contract(CAPACITY), check_profile(CAPACITY),
        target_display="25000 persons",
    )
    assert result[0] is outcome


def test_a_numeric_answer_that_parses_to_nothing_is_not_an_abstention():
    result = parse_reconstruction(
        "banana", _contract(CAPACITY), check_profile(CAPACITY),
        target_display="25000 persons",
    )
    assert result[0] is None
    assert result[2] is CheckParseStatus.NUMERIC_PARSE_FAILED


def test_no_second_numeric_clustering_implementation_exists():
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    for forbidden in ("cluster_values", "_relative_mad", "dominant_cluster",
                      "relative_distance", "tolerance", "0.05"):
        assert forbidden not in blob, f"M18 re-implements {forbidden}"
    source = (Path("src/cover_kbc/verification") / "bidirectional_verifier.py").read_text()
    assert "canonicalise" in source and "numeric_spec" in source


# --------------------------------------------------------------------------
# 18-27. Counterfactual pair
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", ENTITY_RELATIONS + NUMERIC_RELATIONS)
def test_counterfactual_classes_come_only_from_the_contract(relation):
    contract = CONTRACTS[relation]
    profile = check_profile(relation)
    classes = profile.counterfactual_classes(contract)
    assert len(classes) == len(contract.hard_negative_rules)
    for index, class_id in enumerate(classes):
        assert counterfactual_class_text(relation, class_id) == (
            contract.hard_negative_rules[index]
        )
    with pytest.raises(UnsupportedCheckRelation):
        counterfactual_class_text(relation, "hn99")


def test_no_near_miss_prose_is_written_by_module_18():
    """The class text is Module 0's. M18 owns no factual alternative."""
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES).casefold()
    for phrase in (
        "nominee", "attendance", "land area", "maritime", "subsidiary",
        "birthplace", "residence", "delisted", "stock index", "shortlisted",
        "rescinded", "privately held",
    ):
        assert phrase not in blob, f"M18 writes its own near-miss prose: {phrase}"


@pytest.mark.parametrize(
    "relation,needle",
    [
        (AWARD, "nominee, finalist or shortlisted"),
        (AWARD, "the winning work"),
        (CAPACITY, "record or peak attendance"),
        (AREA, "the land-only area"),
        (DEATH, "the city of birth, of residence"),
        (STOCK, "the parent company is listed"),
        (STOCK, "a subsidiary is listed"),
        (STOCK, "a stock index, a broker"),
        (BORDERS, "a maritime-only border"),
        (BORDERS, "merely nearby"),
    ],
)
def test_every_required_counterfactual_class_renders(relation, needle):
    contract = CONTRACTS[relation]
    index = next(
        i for i, rule in enumerate(contract.hard_negative_rules) if needle in rule
    )
    display = DISPLAYS.get(relation, "25000 persons")
    rendered = render_counterfactual(
        check_profile(relation), contract, subject=SUBJECTS[relation],
        candidate=display, counterfactual_class=f"hn{index}",
    )
    assert needle in rendered.prompt
    assert display in rendered.prompt
    assert "TARGET" in rendered.prompt and "EXCLUDED" in rendered.prompt


def test_the_counterfactual_never_says_what_upstream_suspects():
    for relation in ENTITY_RELATIONS:
        prompt = render_counterfactual(
            check_profile(relation), CONTRACTS[relation], subject=SUBJECTS[relation],
            candidate=DISPLAYS[relation], counterfactual_class="hn0",
        ).prompt.casefold()
        for forbidden in ("suspect", "flagged", "probably", "likely to be",
                          "the system", "thinks", "detected", "believes"):
            assert forbidden not in prompt, f"{relation}: {forbidden}"


def test_the_counterfactual_result_is_not_module_17s_abc():
    assert {o.value for o in CounterfactualOutcome} == {
        "TARGET_RELATION", "NEAR_MISS_RELATION", "NEITHER", "UNRESOLVED"
    }
    for value in ("VALID", "INVALID", "UNKNOWN"):
        assert value not in {o.value for o in CounterfactualOutcome}
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    for forbidden in ("LABEL_TOKENS", "score_labels", "read_labels",
                      "ContextualCalibrator", "VerificationLabel"):
        assert forbidden not in blob, f"M18 touches Module 4's kernel via {forbidden}"


@pytest.mark.parametrize(
    "text,outcome,status",
    [
        ("TARGET", CounterfactualOutcome.TARGET_RELATION, CheckParseStatus.OK),
        ("EXCLUDED", CounterfactualOutcome.NEAR_MISS_RELATION, CheckParseStatus.OK),
        ("NEITHER", CounterfactualOutcome.NEITHER, CheckParseStatus.OK),
        ("UNKNOWN", CounterfactualOutcome.UNRESOLVED, CheckParseStatus.ABSTAINED),
        ("", None, CheckParseStatus.EMPTY),
        ("!!!", None, CheckParseStatus.MALFORMED),
    ],
)
def test_counterfactual_parsing_is_bounded(text, outcome, status):
    assert parse_counterfactual(text) == (outcome, status)


def test_an_unknown_counterfactual_class_is_refused(verifier):
    consensus = _consensus(AWARD)
    check = _check(consensus, K.COUNTERFACTUAL)
    with pytest.raises(UnsupportedCheckRelation):
        verifier.build_request(replace(check, counterfactual_class="hn99"))


# --------------------------------------------------------------------------
# 28-39. Candidate-free recall
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", ENTITY_RELATIONS + NUMERIC_RELATIONS)
def test_the_candidate_free_prompt_contains_no_candidate(relation):
    rendered = render_candidate_free(
        check_profile(relation), _contract(relation), subject=SUBJECTS[relation]
    )
    assert rendered.candidate_shown is False
    assert SUBJECTS[relation] in rendered.prompt
    for display in DISPLAYS.values():
        assert display not in rendered.prompt
    assert "25000" not in rendered.prompt


def test_the_candidate_free_renderer_takes_no_candidate_at_all():
    """A leak would have to be added deliberately."""
    import inspect

    signature = inspect.signature(render_candidate_free)
    assert set(signature.parameters) == {"profile", "contract", "subject"}


def test_a_poisoned_upstream_never_reaches_any_module_18_prompt():
    """Requirement 32: the poison sits in provenance and must not travel."""
    poisoned = _consensus(
        AWARD,
        candidates=(_state(AWARD, risk=(RiskFlag.NEAR_MISS_MENTION,)),),
        pending=(PendingDownstreamCheck(
            source_module="M15", kind="PARENT_SUBSIDIARY",
            reason="PARENT_SUBSIDIARY_RISK", candidate="Recipient Alpha",
            detail=POISON[3],
        ),),
    )
    poisoned = replace(poisoned, query_risk={
        "generator_note": POISON[0], "m17": POISON[1], "m16": POISON[2],
        "near_miss_risk": "HIGH",
    })

    verifier = BidirectionalVerifier()
    runtime = EchoRuntime("Recipient Alpha")
    for kind in (K.KEY_CONDITION, K.COUNTERFACTUAL, K.CANDIDATE_FREE_RECALL):
        check = _check(poisoned, kind)
        verifier.execute(verifier.build_request(check), _contract(AWARD), runtime)

    assert runtime.prompts
    for prompt in (*runtime.prompts, *runtime.system_prompts):
        for poison in POISON:
            assert poison not in prompt
        for leak in ("99%", "generator", "independent_support", "HIGH",
                     "PARENT_SUBSIDIARY", "suspects", "VALID"):
            assert leak not in prompt, leak


def test_the_candidate_free_prompt_hides_every_upstream_signal():
    prompt = render_candidate_free(
        check_profile(STOCK), _contract(STOCK), subject=SUBJECTS[STOCK]
    ).prompt
    for leak in (DISPLAYS[STOCK], "VALID", "INVALID", "independent_support",
                 "support", "risk", "pending", "consensus", "D=", "F=", "I="):
        assert leak not in prompt, leak


def test_candidate_free_recall_can_rediscover_the_target(verifier):
    consensus = _consensus(AWARD)
    record, runtime = _run(
        verifier, consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha"
    )
    assert record.recall_outcome is RecallOutcome.TARGET_RECALLED
    assert [c.is_target for c in record.recalled_candidates] == [True]
    assert DISPLAYS[AWARD] not in runtime.prompts[0]     # it was never shown


def test_candidate_free_recall_can_discover_new_candidates(verifier):
    consensus = _consensus(AWARD)
    record, _ = _run(
        verifier, consensus, K.CANDIDATE_FREE_RECALL,
        "Recipient Alpha\nRecipient Gamma\nRecipient Delta",
    )
    assert record.recall_outcome is RecallOutcome.TARGET_RECALLED
    assert len(record.recalled_candidates) == 3
    new = record.new_candidates
    assert {c.surface for c in new} == {"Recipient Gamma", "Recipient Delta"}
    assert all(not c.verified for c in record.recalled_candidates)


def test_new_candidates_are_not_inserted_into_module_3_or_16():
    from cover_kbc.evidence.graph import build_graph

    query, contract = compile_query(SUBJECTS[AWARD], AWARD, 0)
    graph = build_graph(query, contract)
    consensus = _consensus(AWARD)
    before_graph = copy.deepcopy(graph.to_json())
    before_consensus = copy.deepcopy(consensus.to_json())

    verifier = BidirectionalVerifier()
    _run(verifier, consensus, K.CANDIDATE_FREE_RECALL, "Recipient Gamma")

    assert graph.to_json() == before_graph
    assert graph.candidates == {}
    assert consensus.to_json() == before_consensus
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    for forbidden in ("add_entity_mentions", "add_evidence", "EvidenceGraph",
                      "CandidateConsensusState("):
        assert forbidden not in blob, forbidden


def test_a_recalled_candidate_cannot_claim_verification():
    with pytest.raises(BidirectionalCheckError, match="never verifies"):
        RecalledCandidate(surface="X", candidate_key="x", verified=True)


@pytest.mark.parametrize(
    "text,outcome,status",
    [
        ("", RecallOutcome.NOTHING_RECALLED, CheckParseStatus.EMPTY),
        ("NONE", RecallOutcome.NOTHING_RECALLED, CheckParseStatus.ABSTAINED),
        ("UNKNOWN", RecallOutcome.NOTHING_RECALLED, CheckParseStatus.ABSTAINED),
        ("I don't know", RecallOutcome.NOTHING_RECALLED, CheckParseStatus.ABSTAINED),
    ],
)
def test_an_empty_or_abstained_recall_is_not_a_contradiction(text, outcome, status):
    result = parse_candidate_free(
        text, _contract(AWARD), check_profile(AWARD),
        target_keys=("recipient alpha",),
    )
    assert (result[0], result[2]) == (outcome, status)
    assert result[1] == ()


def test_audit_0024_abstention_safety_holds_on_the_recall_path():
    """NONE and UNKNOWN never become a candidate, on any Module 18 path."""
    for relation in (DEATH, AWARD, BORDERS, STOCK):
        result = parse_candidate_free(
            "NONE\nUNKNOWN\nCity Beta", _contract(relation), check_profile(relation),
            target_keys=(),
        )
        surfaces = {c.surface for c in result[1]}
        assert "NONE" not in surfaces and "UNKNOWN" not in surfaces, relation
        assert "City Beta" in surfaces, relation

    from cover_kbc.specialists import asserts_relation_level_absence, is_epistemic_abstention

    assert is_epistemic_abstention("UNKNOWN")
    assert not asserts_relation_level_absence("UNKNOWN", sentinel_is_defined=True)


def test_a_malformed_recall_is_not_support(verifier):
    consensus = _consensus(CAPACITY, clusters=(_cluster(),))
    record, _ = _run(verifier, consensus, K.CANDIDATE_FREE_RECALL, "banana")
    assert record.recall_outcome is None
    assert record.parse_status is CheckParseStatus.NUMERIC_PARSE_FAILED
    assert record.recalled_candidates == ()


# --------------------------------------------------------------------------
# 40-46. X / cross-model semantics
# --------------------------------------------------------------------------


def test_same_family_candidate_free_recall_is_not_cross_model_eligible(verifier):
    consensus = _consensus(AWARD)
    runtime = EchoRuntime("Recipient Alpha", family="mistral")
    record, _ = _run(
        verifier, consensus, K.CANDIDATE_FREE_RECALL, runtime=runtime,
        primary_model_family="mistral",
    )
    assert record.recall_outcome is RecallOutcome.TARGET_RECALLED
    assert record.candidate_shown is False
    assert record.independent_recall is True
    assert record.cross_model_eligible is False


def test_distinct_family_candidate_free_recall_may_be_cross_model_eligible(verifier):
    consensus = _consensus(AWARD)
    runtime = EchoRuntime("Recipient Alpha", model_id="Qwen/Qwen3.5-4B", family="qwen")
    record, _ = _run(
        verifier, consensus, K.CANDIDATE_FREE_RECALL, runtime=runtime,
        primary_model_family="mistral",
    )
    assert record.candidate_shown is False
    assert record.cross_model_eligible is True


@pytest.mark.parametrize("kind", [K.REVERSE, K.COUNTERFACTUAL])
def test_a_shown_candidate_check_is_never_cross_model_eligible(verifier, kind):
    consensus = _consensus(BORDERS)
    runtime = EchoRuntime("SUPPORTED", model_id="Qwen/Qwen3.5-4B", family="qwen")
    record, _ = _run(
        verifier, consensus, kind, runtime=runtime, primary_model_family="mistral"
    )
    assert record.candidate_shown is True
    assert record.cross_model_eligible is False


def test_the_type_refuses_a_shown_candidate_marked_cross_model_eligible():
    """Structural, not editorial: the combination cannot be constructed."""
    with pytest.raises(BidirectionalCheckError, match="cannot be cross-model"):
        BidirectionalCheckRecord(
            request=BidirectionalCheckRequest(
                check=EligibleCheck(
                    check_kind=K.REVERSE,
                    target=CheckTarget(
                        relation=BORDERS, subject="s", row_index=0,
                        kind=CheckTargetKind.ENTITY_CANDIDATE,
                    ),
                ),
                template_id=REVERSE_TEMPLATE_ID,
            ),
            origin_event_id="o", prompt_sha256="p", model_id="m",
            candidate_shown=True, cross_model_eligible=True,
        )


def test_key_condition_records_that_the_target_was_hidden(verifier):
    consensus = _consensus(AWARD)
    record, _ = _run(verifier, consensus, K.KEY_CONDITION, "Recipient Alpha")
    assert record.candidate_shown is False
    # It is not a candidate-free probe, so it claims no independent recall.
    assert record.independent_recall is False
    assert record.cross_model_eligible is False


def test_module_16s_x_is_not_mutated():
    consensus = _consensus(AWARD)
    before = copy.deepcopy(consensus.to_json())
    verifier = BidirectionalVerifier()
    _run(verifier, consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha")
    assert consensus.to_json() == before
    assert all(c.x_cross_model == 0.0 for c in consensus.candidates)
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    for forbidden in ("x_cross_model", "CROSS_MODEL_RECALL", "f_support",
                      "i_independent_support"):
        assert forbidden not in blob, f"M18 writes {forbidden}"


def test_audit_0008_x_invariant_remains_green():
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
    assert cross_model_term(candidate, DEFAULT_SCORING) == 0.0
    candidate.add_evidence(Evidence(
        candidate_key="recipient alpha", edge_type=EdgeType.SUPPORT,
        independence_group=IndependenceGroup.CROSS_MODEL_RECALL, view_id="x",
        model_id="q", run_id=0, record_id="x1",
        mode=EvidenceMode.INDEPENDENT_RECALL,
    ))
    assert cross_model_term(candidate, DEFAULT_SCORING) == 1.0
    assert support_term(candidate, contract, DEFAULT_SCORING) == baseline


# --------------------------------------------------------------------------
# 47-56. Independence, origins, cost, scheduling
# --------------------------------------------------------------------------


def test_each_mechanism_has_one_stable_independence_group():
    assert {k.independence_group for k in BidirectionalCheckKind} == {
        "M18_REVERSE", "M18_KEY_CONDITION", "M18_COUNTERFACTUAL",
        "M18_CANDIDATE_FREE_RECALL",
    }


def test_repeats_of_one_mechanism_share_its_group(verifier):
    consensus = _consensus(AWARD)
    check = _check(consensus, K.COUNTERFACTUAL)
    runtime = EchoRuntime("TARGET")
    records = [
        verifier.execute(
            verifier.build_request(check, sample_index=index), _contract(AWARD), runtime
        )
        for index in range(3)
    ]
    assert {r.independence_group for r in records} == {"M18_COUNTERFACTUAL"}
    # Three samples are three origins and three calls - but one group.
    assert len({r.origin_event_id for r in records}) == 3
    assert sum(r.calls for r in records) == 3 == runtime.gen_calls


def test_a_different_counterfactual_class_does_not_invent_independence(verifier):
    consensus = _consensus(AWARD)
    checks = [c for c in eligible_checks(consensus) if c.check_kind is K.COUNTERFACTUAL]
    assert len(checks) == len(CONTRACTS[AWARD].hard_negative_rules)
    runtime = EchoRuntime("TARGET")
    records = [
        verifier.execute(verifier.build_request(c), _contract(AWARD), runtime)
        for c in checks[:3]
    ]
    assert {r.independence_group for r in records} == {"M18_COUNTERFACTUAL"}


def test_one_output_with_many_candidates_is_one_origin_and_one_call(verifier):
    consensus = _consensus(AWARD)
    record, runtime = _run(
        verifier, consensus, K.CANDIDATE_FREE_RECALL,
        "Recipient Alpha\nRecipient Gamma\nRecipient Delta\nRecipient Epsilon",
    )
    assert len(record.recalled_candidates) == 4
    assert record.calls == 1 == runtime.gen_calls
    assert record.origin_event_id


def test_origin_identity_is_deterministic_and_never_random():
    args = dict(model_id="m", operation_id="op", prompt_sha256="h", sample_index=0)
    assert derive_check_origin_id(**args) == derive_check_origin_id(**args)
    assert derive_check_origin_id(**{**args, "sample_index": 1}) != (
        derive_check_origin_id(**args)
    )
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    for forbidden in ("uuid", "random", "time.time"):
        assert forbidden not in blob, forbidden


def test_an_m18_call_never_reuses_an_upstream_origin(verifier):
    from cover_kbc.evidence.consensus_types import derive_origin_event_id

    consensus = _consensus(AWARD)
    record, _ = _run(verifier, consensus, K.CANDIDATE_FREE_RECALL, "Recipient Alpha")
    upstream = derive_origin_event_id(
        model_id="offline/enumerator", operation_id="pseudo_memory#0",
        prompt_sha256="h", sample_index=0,
    )
    assert record.origin_event_id != upstream


def test_pending_checks_are_carried_with_provenance_and_kept_out_of_prompts(verifier):
    consensus = _consensus(BORDERS, pending=(PendingDownstreamCheck(
        source_module="M15", kind="REVERSE_ADJACENCY", reason="SINGLETON_CANDIDATE",
        candidate=DISPLAYS[BORDERS], detail="seen from one structural source only",
    ),))
    reverse = _check(consensus, K.REVERSE)
    assert reverse.requested_by is not None
    assert reverse.requested_by.source_module == "M15"
    assert reverse.requested_by.reason == "SINGLETON_CANDIDATE"

    runtime = EchoRuntime("SUPPORTED")
    verifier.execute(verifier.build_request(reverse), _contract(BORDERS), runtime)
    for prompt in (*runtime.prompts, *runtime.system_prompts):
        for leak in ("SINGLETON", "REVERSE_ADJACENCY", "M15", "one structural source"):
            assert leak not in prompt, leak


def test_a_non_reverse_pending_reason_does_not_motivate_a_reverse_check():
    consensus = _consensus(BORDERS, pending=(PendingDownstreamCheck(
        source_module="M15", kind="COMPANY_ITSELF", reason="CANDIDATE_EXPLOSION",
        candidate=DISPLAYS[BORDERS],
    ),))
    reverse = _check(consensus, K.REVERSE)
    assert reverse.requested_by is None


def test_module_17_results_never_trigger_module_18(verifier):
    """A verifier UNKNOWN escalates nothing: that is Module 20/21's."""
    consensus = _consensus(AWARD)
    runtime = EchoRuntime("TARGET")
    catalogue = verifier.catalogue(consensus)
    assert catalogue
    assert runtime.gen_calls == 0

    code = _code_without_prose("bidirectional_verifier.py")
    for forbidden in ("SpecialistVerificationResult", "argmax_label", "l_available",
                      "UNKNOWN_LABEL", "should_check", "escalate", "auto_run"):
        assert forbidden not in code, f"M18 reacts to {forbidden}"


def test_the_catalogue_reads_no_evidence_and_selects_nothing():
    strong = _state(AWARD, display="Recipient Alpha")
    weak = replace(
        _state(AWARD, display="Recipient Gamma"),
        i_independent_support=0, f_support=0.0, d_semantic=0.0, l_logit=-0.9,
        risk_flags=(RiskFlag.SINGLE_GROUP_SUPPORT,),
    )
    catalogue = eligible_checks(_consensus(AWARD, candidates=(strong, weak)))
    per_candidate = {}
    for check in catalogue:
        if check.target.kind is CheckTargetKind.ENTITY_CANDIDATE:
            per_candidate.setdefault(check.target.target_id, []).append(check)
    assert len(per_candidate) == 2
    assert len(per_candidate["recipient alpha"]) == len(per_candidate["recipient gamma"])

    code = _code_without_prose("bidirectional_verifier.py")
    for forbidden in (".f_support", ".i_independent_support", ".d_semantic",
                      ".risk_flags", ".l_logit", "budget", "expected_value",
                      "next_action", "should_stop"):
        assert forbidden not in code, f"M18 schedules on {forbidden}"


def test_a_hard_contract_violation_is_ineligible_and_costs_nothing(verifier):
    consensus = _consensus(AWARD, candidates=(
        _state(AWARD, key="-5", display="-5", violation=True),
    ))
    checks = [c for c in eligible_checks(consensus) if c.target.target_id == "-5"]
    assert checks and all(not c.eligible for c in checks)
    # awardWonBy declares no reverse at all, and that answer comes first; every
    # check the relation *does* declare is blocked by the violation itself.
    reasons = {c.check_kind: c.ineligible_reason for c in checks}
    assert reasons[K.REVERSE] is CheckIneligible.RELATION_HAS_NO_REVERSE
    assert all(
        reason is CheckIneligible.HARD_CONTRACT_VIOLATION
        for kind, reason in reasons.items() if kind is not K.REVERSE
    )
    runtime = EchoRuntime("TARGET")
    with pytest.raises(BidirectionalCheckError, match="not eligible"):
        verifier.execute(
            replace(verifier.build_request(replace(checks[0], eligible=True)),
                    check=checks[0]),
            _contract(AWARD), runtime,
        )
    assert runtime.gen_calls == 0


@pytest.mark.parametrize("kind", list(BidirectionalCheckKind))
def test_an_explicit_execution_costs_exactly_one_call(verifier, kind):
    consensus = _consensus(BORDERS)
    record, runtime = _run(verifier, consensus, kind, "SUPPORTED")
    assert runtime.gen_calls == 1
    assert record.calls == 1
    assert record.prompt_tokens == 42 and record.generated_tokens == 6


def test_a_runtime_failure_is_recorded_and_not_retried(verifier):
    consensus = _consensus(AWARD)
    runtime = EchoRuntime(fail=True)
    record, _ = _run(verifier, consensus, K.COUNTERFACTUAL, runtime=runtime)

    assert runtime.gen_calls == 1                 # no retry loop
    assert record.parse_status is CheckParseStatus.RUNTIME_ERROR
    assert record.error and "fell over" in record.error
    assert record.counterfactual_outcome is None
    assert record.calls == 1
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    for forbidden in ("retry", "max_attempts", "backoff", "while True"):
        assert forbidden not in blob, forbidden


# --------------------------------------------------------------------------
# 57-72. Shadow seam, invariance, persistence
# --------------------------------------------------------------------------


def _pipeline(with_m18=True, runtime=None):
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler,
    )
    from cover_kbc.specialists import LargeSetSpecialist

    return CoverPipeline(
        runtime or EchoRuntime("Recipient Alpha"), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), large_set_specialist=LargeSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        bidirectional_verifier=BidirectionalVerifier() if with_m18 else None,
    )


def test_the_pipeline_catalogues_and_executes_nothing():
    from cover_kbc.types import Query

    runtime = EchoRuntime("Recipient Alpha")
    pipeline = _pipeline(runtime=runtime)
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    before = runtime.gen_calls
    pipeline.decide_graph(graph)

    assert runtime.gen_calls == before        # cataloguing costs nothing
    assert len(pipeline.bidirectional_results) == 1
    record = pipeline.bidirectional_results[0]
    assert record.records == ()
    assert record.calls == 0
    assert record.catalogue


def test_an_explicit_caller_can_execute_and_the_spend_is_shadow():
    from cover_kbc.types import Query

    runtime = EchoRuntime("Recipient Alpha")
    pipeline = _pipeline(runtime=runtime)
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    prediction = pipeline.decide_graph(graph)
    consensus = pipeline.consensus_results[0]

    verifier = pipeline.bidirectional_verifier
    checks = [c for c in verifier.catalogue(consensus)
              if c.eligible and c.check_kind is K.CANDIDATE_FREE_RECALL]
    assert checks
    shadow_before = pipeline.shadow_calls
    result = pipeline.execute_bidirectional_checks(
        consensus, [verifier.build_request(checks[0])], runtime
    )

    assert result.calls == 1
    assert pipeline.shadow_calls == shadow_before + 1
    assert prediction.calls_used == graph.budget_snapshot.get("calls_used", 0)
    assert len(pipeline.bidirectional_results) == 1
    assert pipeline.bidirectional_results[0].records


def test_the_pipeline_refuses_a_verifier_without_consensus():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a consensus engine"):
        CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            bidirectional_verifier=BidirectionalVerifier(),
        )


def test_module_3_5_16_and_17_are_untouched():
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.query_intelligence import PromptProgramCompiler, QueryProfiler
    from cover_kbc.specialists import LargeSetSpecialist
    from cover_kbc.verification.specialist_verifier import (
        SpecialistVerifier, verifiable_targets,
    )

    query, contract = compile_query(SUBJECTS[AWARD], AWARD, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    specialist = LargeSetSpecialist().analyse(
        query, program, contract, ScriptedRuntime({}, model_id="offline/enumerator")
    )
    graph = build_graph(query, contract)
    consensus = _consensus(AWARD)

    m17 = SpecialistVerifier()
    targets_before = verifiable_targets(consensus)
    graph_before = copy.deepcopy(graph.to_json())
    consensus_before = copy.deepcopy(consensus.to_json())
    specialist_before = copy.deepcopy(specialist.to_json())

    verifier = BidirectionalVerifier()
    for kind in BidirectionalCheckKind:
        checks = [c for c in eligible_checks(consensus)
                  if c.check_kind is kind and c.eligible]
        if not checks:
            continue
        verifier.execute(
            verifier.build_request(checks[0]), contract, EchoRuntime("TARGET")
        )

    assert graph.to_json() == graph_before
    assert consensus.to_json() == consensus_before
    assert specialist.to_json() == specialist_before
    assert verifiable_targets(consensus) == targets_before
    assert m17.calibrator.calls == 0
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    for forbidden in ("score_breakdown", "candidate.status", "add_verification"):
        assert forbidden not in blob, forbidden


@pytest.mark.parametrize("relation", sorted(SUBJECTS))
def test_every_public_type_round_trips(verifier, relation):
    clusters = (_cluster(),) if relation in NUMERIC_RELATIONS else ()
    consensus = _consensus(relation, clusters=clusters)
    catalogue = verifier.catalogue(consensus)
    eligible = [c for c in catalogue if c.eligible]
    records = [
        verifier.execute(
            verifier.build_request(eligible[0]), _contract(relation),
            EchoRuntime("UNKNOWN"),
        )
    ] if eligible else []
    result = QueryBidirectionalResult(
        check_version=CHECK_VERSION, relation=relation, subject=SUBJECTS[relation],
        row_index=0, catalogue=catalogue, records=tuple(records),
    )
    payload = json.loads(json.dumps(result.to_json()))
    assert QueryBidirectionalResult.from_json(payload) == result
    for original, entry in zip(result.catalogue, payload["catalogue"]):
        assert EligibleCheck.from_json(entry) == original
    for original, entry in zip(result.records, payload["records"]):
        assert BidirectionalCheckRecord.from_json(entry) == original
        assert BidirectionalCheckRequest.from_json(entry["request"]) == original.request
    origin = PendingCheckOrigin(source_module="M15", kind="k", reason="r")
    assert PendingCheckOrigin.from_json(
        json.loads(json.dumps(origin.to_json()))
    ) == origin


def test_the_payload_carries_no_decision(verifier):
    consensus = _consensus(BORDERS)
    record, _ = _run(verifier, consensus, K.REVERSE, "SUPPORTED")
    payload = json.dumps(record.to_json())
    for forbidden in ("accepted", "rejected", "final_set", "final_score",
                      "should_stop", "prune", "prediction", "gold",
                      "ObjectEntities", "residual", "budget"):
        assert forbidden not in payload, forbidden


def test_no_result_field_is_named_like_a_decision():
    fields = set(BidirectionalCheckRecord.__dataclass_fields__)
    for forbidden in ("accepted", "rejected", "final", "decision", "score",
                      "rank", "prune", "verdict", "stop"):
        assert not any(forbidden in name for name in fields), forbidden


# --------------------------------------------------------------------------
# 73-90. Configuration, boundaries, compliance
# --------------------------------------------------------------------------


def test_configuration_failures_are_loud():
    with pytest.raises(ValueError, match="unsupported bidirectional_verification mode"):
        BidirectionalVerifierConfig.from_mapping({"enabled": True, "mode": "production"})
    with pytest.raises(ValueError, match="unknown bidirectional_verification key"):
        BidirectionalVerifierConfig.from_mapping({"enabled": True, "enabledd": True})
    with pytest.raises(ValueError, match="unsupported check_version"):
        BidirectionalVerifierConfig.from_mapping(
            {"enabled": True, "check_version": "m18-v9"}
        )
    with pytest.raises(ValueError, match="unknown check kind"):
        BidirectionalVerifierConfig.from_mapping(
            {"enabled": True, "supported_checks": ["THINK_AGAIN"]}
        )
    with pytest.raises(ValueError, match="must be a list"):
        BidirectionalVerifierConfig.from_mapping(
            {"enabled": True, "supported_checks": "REVERSE"}
        )


def test_the_builder_requires_consensus():
    with pytest.raises(ValueError, match="requires consensus"):
        build_bidirectional_verifier({"enabled": True}, consensus_enabled=False)
    assert build_bidirectional_verifier(None, consensus_enabled=True) is None
    assert build_bidirectional_verifier(
        {"enabled": False}, consensus_enabled=True
    ) is None


def test_no_fitted_or_scheduling_value_exists_in_the_config():
    fields = set(BidirectionalVerifierConfig.__dataclass_fields__)
    for forbidden in ("threshold", "weight", "retry", "max_", "min_", "budget",
                      "auto", "dola", "temperature"):
        assert not any(forbidden in name for name in fields), forbidden


def test_the_shipped_configs_keep_m18_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        text = Path(name).read_text()
        block = yaml.safe_load(text)["bidirectional_verification"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["check_version"] == CHECK_VERSION, name
        assert block["supported_checks"] == [k.value for k in BidirectionalCheckKind]
        assert "dola" not in text.casefold(), name


def test_no_dola_and_no_hidden_state_access():
    """§14.1 is optional and experimental; it is deferred, not implemented."""
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES).casefold()
    for forbidden in ("dola", "hidden_state", "hidden_states", "premature_layer",
                      "early_exit", "layer_contrast", "output_hidden"):
        assert forbidden not in blob, forbidden


def test_no_abc_calibration_is_touched():
    import hashlib

    from cover_kbc.verification import (
        GATE_TEMPLATE, LABEL_TOKENS, TEMPLATES, VERIFIER_SYSTEM_PROMPT,
    )

    blob = (
        VERIFIER_SYSTEM_PROMPT + "\n" + GATE_TEMPLATE + "\n"
        + repr(sorted(LABEL_TOKENS.items()))
    )
    for template in TEMPLATES:
        blob += "\n" + template.template_id + "\n" + template.body
    assert hashlib.sha256(blob.encode()).hexdigest() == (
        "3acd7109fd22cf37b9b0c1c8a3ab63e4a4a1b65875eab02888e3fbc491e6d874"
    )


def test_no_module_19_20_or_21_logic_exists():
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES)
    for forbidden in ("residual", "missingness", "saturation", "coverage_gap",
                      "allocate_budget", "schedule", "next_action",
                      "expected_value", "should_stop", "STOP"):
        assert forbidden not in blob, f"M18 implements {forbidden}"


def test_no_external_retrieval_no_training_and_no_third_model():
    banned = {"requests", "httpx", "urllib", "socket", "sklearn", "torch",
              "transformers", "numpy", "scipy"}
    for name in M18_MODULES:
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
    blob = " ".join(_code_without_prose(n) for n in M18_MODULES).casefold()
    for forbidden in ("wikipedia", "wikidata", "http://", "https://", "api_key",
                      "fine_tune", "lora", ".fit(", "mistralai/", "qwen/",
                      "embedding", "cosine", "levenshtein", "difflib", "fuzz"):
        assert forbidden not in blob, forbidden


def test_m18_introduces_no_new_parameters(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.verification.bidirectional_verifier import "
        "BidirectionalVerifier\n"
        "BidirectionalVerifier()\n"
        "print(','.join(sorted(m for m in sys.modules if m in "
        "('torch', 'transformers', 'mistral_common'))))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == ""


def test_benchmark_is_untouched():
    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True
        ).stdout == "", args


def test_audit_0022_cross_family_rationale_stays_green():
    from cover_kbc.query_intelligence import PromptProgramCompiler, QueryProfiler
    from cover_kbc.specialists import NullTemporalSpecialist

    query, contract = compile_query(SUBJECTS[DEATH], DEATH, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    plan = NullTemporalSpecialist().plan(
        query, program, contract, cross_family_available=True
    )
    assert plan.cross_family_rationale == "disabled in configuration"


def test_audit_0025_specialist_contracts_stay_green():
    from cover_kbc.verification.specialist_contracts import (
        SPECIALIST_CONTRACTS as M17_CONTRACTS,
        check_specialist_registry_consistency,
    )

    check_specialist_registry_consistency()
    assert len(M17_CONTRACTS) == 5
    assert CHECK_CONTRACT_VERSION == "m18-contract-v1"


# --------------------------------------------------------------------------
# Staged shadow invariance
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


def _config(tmp_path: Path, *, m18: bool, tag: str) -> Path:
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
        **config["bidirectional_verification"], "enabled": m18,
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


@pytest.mark.parametrize("relation", [BORDERS, AWARD])
def test_shadow_mode_changes_no_production_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run_cli(cli, monkeypatch, _config(tmp_path, m18=True, tag="on"), on, relation)
    _run_cli(cli, monkeypatch, _config(tmp_path, m18=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in PRODUCTION_ARTEFACTS:
        left, right = on / name, off / name
        if not left.exists() and not right.exists():
            continue
        assert left.read_bytes() == right.read_bytes(), name

    assert (on / "bidirectional_verification.jsonl").is_file()
    assert not (off / "bidirectional_verification.jsonl").exists()


def test_the_artefact_is_manifest_ordered_and_executes_nothing(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run_cli(cli, monkeypatch, _config(tmp_path, m18=True, tag="on"), run_dir, BORDERS)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "bidirectional_verification.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["SubjectEntity"], r["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        assert row["check_version"] == CHECK_VERSION
        assert row["records"] == []           # no automatic execution
        assert row["calls"] == 0
        for forbidden in ("gold", "ObjectEntities", "accepted", "rejected",
                          "prediction", "should_stop", "residual", "budget"):
            assert forbidden not in json.dumps(row), forbidden
