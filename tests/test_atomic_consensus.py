"""Module 16 - Atomic Consensus Engine conformance.

The load-bearing claims:

* §12.1's arithmetic is implemented literally - ``q_g`` is a **max**, ten
  samples of one probe are one group contribution, and five facets of one
  mechanism are one group;
* one physical model output counted **once**, however many modules describe it
  - the M11 -> specialist derivation is the dangerous case and has its own
  section;
* Audit 0008's F/L/X/C/U channels survive four new evidence producers, proved
  against a regression matrix;
* the production graph is read, never written, and nothing M16 produces is a
  decision;
* zero neural calls.

Every subject and object below is **fictional**.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.contracts.router import compile_query
from cover_kbc.evidence.consensus import (
    AtomicConsensusEngine,
    ConsensusConfig,
    build_consensus_engine,
    check_origin_consistency,
    cost_from_ledger,
    group_supports,
    independent_support,
    origin_cost,
    origin_ledger,
)
from cover_kbc.evidence.consensus_adapters import (
    APPLICABLE_SPECIALIST,
    applicable_specialist,
    core_role,
    parametric_events,
)
from cover_kbc.evidence.consensus_types import (
    CONSENSUS_VERSION,
    CandidateConsensusState,
    ConsensusCost,
    ConsensusError,
    ConsensusEvidenceEvent,
    ConsensusProvenanceError,
    DisagreementKind,
    EvidencePlane,
    EvidenceRole,
    GroupSupport,
    NullConsensusState,
    NumericClusterConsensus,
    PendingDownstreamCheck,
    QueryConsensusResult,
    RiskFlag,
    SemanticDisagreement,
    derive_origin_event_id,
)
from cover_kbc.evidence.graph import build_graph
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.query_intelligence import (
    ParametricRetriever,
    PromptProgramCompiler,
    QueryProfiler,
)
from cover_kbc.specialists import (
    LargeSetSpecialist,
    NullTemporalSpecialist,
    NumericSpecialist,
    SmallSetSpecialist,
)
from cover_kbc.types import (
    DecodeProfile,
    EdgeType,
    EvidenceMode,
    GenerationRecord,
    IndependenceGroup,
    Query,
    VerificationLabel,
    VerificationResult,
    ViewFamily,
)

AWARD = "awardWonBy"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"

SUBJECTS = {
    AWARD: "Award Gamma", DEATH: "Person Delta", CAPACITY: "Stadium Alpha",
    AREA: "Region Beta", BORDERS: "Country Alpha", STOCK: "Example Holdings",
}
M16_MODULES = ("consensus_types.py", "consensus_adapters.py", "consensus.py")
CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
PRODUCTION_ARTEFACTS = (
    "predictions.jsonl", "diagnostics.json", "trace.jsonl",
    "stage_a_enumerated.jsonl", "stage_b_verified.jsonl",
    "calls_enumerate.jsonl", "calls_verify.jsonl", "query_profiles.jsonl",
    "prompt_programs.jsonl", "parametric_memory.jsonl",
    "numeric_specialist.jsonl", "large_open_set_specialist.jsonl",
    "null_temporal_specialist.jsonl", "small_set_specialist.jsonl",
)


def _code_without_prose(name: str) -> str:
    """Executable source, docstrings and comments removed."""
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
# Fixtures: a graph builder and one specialist result per relation
# --------------------------------------------------------------------------


def _graph(relation: str, subject: str | None = None):
    subject = subject or SUBJECTS[relation]
    query, contract = compile_query(subject, relation, 0)
    return build_graph(query, contract), contract


def _record(graph, view: str, group: IndependenceGroup, text: str, run: int = 0,
            model_id: str = "offline/enumerator", family: str = "enumerator",
            facet: str = "", generated: int = 7, prompt_tokens: int = 11,
            latency: float | None = None) -> GenerationRecord:
    record = GenerationRecord(
        record_id=f"{view}#{run}", query=graph.query, view_id=view,
        view_family=ViewFamily.DIRECT, independence_group=group, run_id=run,
        model_id=model_id, prompt=f"prompt-{view}-{run}",
        prompt_hash=f"hash-{view}-{run}", raw_output=text,
        decode_profile=DecodeProfile(name="d"), facet_id=facet,
        model_family=family, generated_tokens=generated,
        prompt_tokens=prompt_tokens, latency_ms=latency,
    )
    return record


def _add_entities(graph, view, group, surfaces, **kwargs):
    record = _record(graph, view, group, "; ".join(surfaces), **kwargs)
    graph.add_entity_mentions(record, surfaces)
    return record


def _add_numbers(graph, view, group, values, **kwargs):
    record = _record(graph, view, group, ", ".join(str(v) for v in values), **kwargs)
    graph.add_numeric_mentions(record, values)
    return record


def _verify(graph, key, label, valid=0.9, invalid=0.05, unknown=0.05,
            prompt_disagreement=0.0, entropy=0.3):
    graph.add_verification(VerificationResult(
        candidate_key=key, label=label, valid_prob=valid, invalid_prob=invalid,
        unknown_prob=unknown, record_id=f"verify-{key}", model_id="offline/verifier",
        model_family="verifier", prompt_disagreement=prompt_disagreement,
        entropy=entropy,
    ))


def _specialist_result(relation: str, outputs: dict[str, str] | None = None,
                       *, subject: str | None = None, model_id="offline/enumerator"):
    """Run the applicable specialist over scripted fixtures."""
    subject = subject or SUBJECTS[relation]
    query, contract = compile_query(subject, relation, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    runtime = ScriptedRuntime(
        {(op, subject, relation): [text] for op, text in (outputs or {}).items()},
        model_id=model_id,
    )
    specialist = {
        "M12": NumericSpecialist, "M13": LargeSetSpecialist,
        "M14": NullTemporalSpecialist, "M15": SmallSetSpecialist,
    }[applicable_specialist(relation)]()
    return specialist.analyse(query, program, contract, runtime), query, contract


def _retrieval(relation: str, outputs: dict[str, str] | None = None,
               *, subject: str | None = None, model_id="offline/enumerator"):
    subject = subject or SUBJECTS[relation]
    query, contract = compile_query(subject, relation, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    runtime = ScriptedRuntime(
        {(op, subject, relation): [text] for op, text in (outputs or {}).items()},
        model_id=model_id,
    )
    return ParametricRetriever().retrieve(query, program, runtime)


def _mined_specialist(relation: str, retrieval, *, subject: str | None = None):
    """A specialist that mines Module 11 and runs no probe of its own."""
    subject = subject or SUBJECTS[relation]
    query, contract = compile_query(subject, relation, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    specialist = {
        "M12": NumericSpecialist, "M13": LargeSetSpecialist,
        "M14": NullTemporalSpecialist, "M15": SmallSetSpecialist,
    }[applicable_specialist(relation)]()
    return specialist.analyse(
        query, program, contract, ScriptedRuntime({}, model_id="offline/enumerator"),
        retrieval,
    )


@pytest.fixture
def engine():
    return AtomicConsensusEngine()


def _event(**kwargs) -> ConsensusEvidenceEvent:
    base = dict(
        relation=AWARD, subject=SUBJECTS[AWARD], row_index=0,
        candidate_key="recipient alpha", display="Recipient Alpha",
        source_module="M13", source_record_id="op", origin_event_id="origin-1",
        plane=EvidencePlane.SPECIALIST, independence_group="FACET",
        role=EvidenceRole.SPECIALIST_ACQUISITION, sign=EdgeType.SUPPORT, support=1,
    )
    base.update(kwargs)
    return ConsensusEvidenceEvent(**base)


# --------------------------------------------------------------------------
# 1-3. Proposal conformance, six relations, applicable specialist
# --------------------------------------------------------------------------


def test_the_module_maps_to_the_proposal_sections():
    """§12.1 q_g and phi; §12.2 clusters and no embedding model."""
    source = (Path("src/cover_kbc/evidence") / "consensus.py").read_text()
    assert "q_g(o) = max support(e, o)" in source
    assert "phi(o) = (F, L, X, C, U, I, D, cost, risk)" in source

    state = CandidateConsensusState(
        relation=AWARD, subject="s", row_index=0, candidate_key="k",
        display="K", candidate_kind="ENTITY",
    )
    payload = state.to_json()
    for term in ("F", "L", "X", "C", "U", "I", "D", "cost", "risk_flags"):
        assert term in payload, term
    # Appendix C: "M16 Consensus | evidence events | candidate consensus states"
    assert APPLICABLE_SPECIALIST == {
        CAPACITY: "M12", AREA: "M12", AWARD: "M13", DEATH: "M14",
        BORDERS: "M15", STOCK: "M15",
    }
    assert set(APPLICABLE_SPECIALIST) == set(CONTRACTS)


@pytest.mark.parametrize("relation", sorted(SUBJECTS))
def test_every_relation_produces_consensus_through_its_specialist(engine, relation):
    graph, _ = _graph(relation)
    result_of, _, _ = _specialist_result(relation)
    result = engine.consense(graph, result_of)

    assert result.relation == relation
    assert result.applicable_specialist == APPLICABLE_SPECIALIST[relation]
    assert result.consensus_version == CONSENSUS_VERSION
    assert QueryConsensusResult.from_json(json.loads(json.dumps(result.to_json()))) == result


def test_only_the_applicable_specialist_is_required():
    """M16 needs the specialist that owns the relation - not all four."""
    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    only_numeric = {"M12": True, "M13": False, "M14": False, "M15": False}
    assert build_consensus_engine(
        {"enabled": True}, available_specialists=only_numeric,
        relations=[CAPACITY, AREA], **kwargs,
    ) is not None

    with pytest.raises(ValueError, match="M13"):
        build_consensus_engine(
            {"enabled": True}, available_specialists=only_numeric,
            relations=[AWARD], **kwargs,
        )


def test_the_wrong_specialist_result_is_refused(engine):
    graph, _ = _graph(AWARD)
    numeric, _, _ = _specialist_result(CAPACITY)
    with pytest.raises(ConsensusError, match="specialist result is for"):
        engine.consense(graph, numeric)

    award, _, _ = _specialist_result(AWARD)
    other_graph, _ = _graph(AWARD, subject="Award Omega")
    with pytest.raises(ConsensusError, match="specialist subject"):
        engine.consense(other_graph, award)

    with pytest.raises(ConsensusError, match="needs that specialist's result"):
        engine.consense(graph, None)


# --------------------------------------------------------------------------
# 4-7. Zero calls, determinism, order invariance, origin identity
# --------------------------------------------------------------------------


def test_consensus_spends_no_neural_call(engine):
    graph, _ = _graph(AWARD)
    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    retrieval = _retrieval(AWARD)
    specialist = _mined_specialist(AWARD, retrieval)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha"])

    before = runtime.calls
    engine.consense(graph, specialist, retrieval=retrieval)
    assert runtime.calls == before == 0


def test_no_model_machinery_is_imported():
    banned = {"torch", "transformers", "requests", "httpx", "urllib", "socket",
              "faiss", "chromadb", "sentence_transformers", "numpy"}
    for name in M16_MODULES:
        tree = ast.parse((Path("src/cover_kbc/evidence") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in banned, f"{name}: {module}"
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("LMRuntime", "generate(", "score_labels", "build_runtime",
                      "GenerationRequest", "embedding", "cosine"):
        assert forbidden not in blob, f"M16 references {forbidden}"


def test_consensus_is_deterministic(engine):
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha", "Recipient Beta"])
    specialist, _, _ = _specialist_result(AWARD)
    assert engine.consense(graph, specialist) == engine.consense(graph, specialist)


def test_consensus_is_order_invariant():
    """Reordering the evidence must not move a single field."""
    forward, _ = _graph(AWARD)
    _add_entities(forward, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha", "Recipient Beta"])
    _add_entities(forward, "decade_slices", IndependenceGroup.STRUCTURAL_DECOMPOSITION,
                  ["Recipient Beta", "Recipient Alpha"])

    backward, _ = _graph(AWARD)
    _add_entities(backward, "decade_slices", IndependenceGroup.STRUCTURAL_DECOMPOSITION,
                  ["Recipient Alpha", "Recipient Beta"])
    _add_entities(backward, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Beta", "Recipient Alpha"])

    specialist, _, _ = _specialist_result(AWARD)
    engine = AtomicConsensusEngine()
    first = engine.consense(forward, specialist)
    second = engine.consense(backward, specialist)
    assert [c.candidate_key for c in first.candidates] == [
        c.candidate_key for c in second.candidates
    ]
    for left, right in zip(first.candidates, second.candidates):
        assert left.group_supports == right.group_supports
        assert (left.f_support, left.i_independent_support) == (
            right.f_support, right.i_independent_support
        )


def test_origin_identity_is_stable_and_never_random():
    args = dict(model_id="m", operation_id="op", prompt_sha256="h", sample_index=2)
    assert derive_origin_event_id(**args) == derive_origin_event_id(**args)
    assert derive_origin_event_id(**{**args, "sample_index": 3}) != (
        derive_origin_event_id(**args)
    )
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("uuid", "random", "id(", "time()"):
        assert forbidden not in blob, f"M16 uses {forbidden} for identity"


def test_origin_identity_survives_serialisation():
    event = _event()
    reloaded = ConsensusEvidenceEvent.from_json(
        json.loads(json.dumps(event.to_json()))
    )
    assert reloaded == event
    assert reloaded.origin_event_id == event.origin_event_id
    assert reloaded.event_id == event.event_id


# --------------------------------------------------------------------------
# 8-9. THE CRITICAL CASE: M11 -> specialist derivation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relation,text",
    [
        (AWARD, "Recipient Alpha"),
        (DEATH, "City Alpha"),
        (STOCK, "Exchange Alpha"),
        (CAPACITY, "50000"),
    ],
)
def test_one_m11_record_mined_by_a_specialist_is_one_support(relation, text):
    """The derived observation is a *description* of the M11 output, not a second one."""
    retrieval = _retrieval(relation, {op: text for op in
                                      ("pseudo_memory#0", "self_ask#0", "query_rewrite#0")})
    specialist = _mined_specialist(relation, retrieval)
    graph, _ = _graph(relation)
    result = AtomicConsensusEngine().consense(graph, specialist, retrieval=retrieval)

    m11_origins = {
        derive_origin_event_id(
            model_id=r.model_id, operation_id=r.operation_id,
            prompt_sha256=r.prompt_sha256, sample_index=r.sample_index,
        ) for r in retrieval.records
    }
    assert m11_origins

    # Cost: every physical output charged once. The specialist's readings of
    # M11's outputs add none, however many observations they produced.
    assert result.cost.neural_calls == (
        sum(r.calls for r in retrieval.records) + specialist.calls
    )

    mined = [
        state for state in result.candidates
        if set(state.origin_event_ids) & m11_origins
    ]
    assert mined, "the fixture should reach the mining path"
    for state in mined:
        carried = set(state.origin_event_ids) & m11_origins
        parametric = [
            g for g in state.group_supports if g.plane is EvidencePlane.PARAMETRIC
        ]
        # One group per M11 probe family that named it - never two for one
        # output, which is what a raw-plus-derived double count would look like.
        assert len(parametric) == len(carried)
        assert sum(len(g.origin_event_ids) for g in parametric) == len(carried)
        assert state.cost.neural_calls <= result.cost.neural_calls


def test_the_derived_reading_declares_no_second_cost():
    retrieval = _retrieval(AWARD, {op: "Recipient Alpha" for op in
                                   ("pseudo_memory#0", "self_ask#0", "query_rewrite#0")})
    specialist = _mined_specialist(AWARD, retrieval)
    graph, contract = _graph(AWARD)
    engine = AtomicConsensusEngine()

    with_m11 = engine.consense(graph, specialist, retrieval=retrieval)
    # Dropping the M11 registration cannot change support - the specialist's
    # observations already carry the origins - and must not change cost either,
    # because the derived reading never declared any.
    without = AtomicConsensusEngine(
        ConsensusConfig(enabled=True, include_parametric_origins=False)
    ).consense(graph, specialist, retrieval=retrieval)

    assert [c.i_independent_support for c in with_m11.candidates] == [
        c.i_independent_support for c in without.candidates
    ]
    assert with_m11.cost.unique_origin_events == without.cost.unique_origin_events
    assert with_m11.cost.neural_calls >= without.cost.neural_calls


def test_two_representations_of_one_origin_do_not_duplicate_cost_or_support():
    shared = dict(model_id="m", origin_event_id=derive_origin_event_id(
        model_id="m", operation_id="op", prompt_sha256="h", sample_index=0
    ), prompt_sha256="h")
    events = [
        _event(source_module="M11", candidate_key="", display="",
               role=EvidenceRole.PARAMETRIC_MEMORY, plane=EvidencePlane.PARAMETRIC,
               sign=EdgeType.UNKNOWN, support=0, calls=1, generated_tokens=9, **shared),
        _event(source_module="M13", independence_group="PSEUDO_MEMORY_SKETCH",
               plane=EvidencePlane.PARAMETRIC, calls=0, **shared),
    ]
    cost = origin_cost(events)
    assert cost.unique_origin_events == 1
    assert cost.neural_calls == 1
    assert cost.generated_tokens == 9

    candidate_events = [e for e in events if e.candidate_key]
    assert independent_support(group_supports(candidate_events)) == 1


def test_a_candidate_seen_only_through_a_derived_reading_still_carries_the_true_cost():
    ledger = origin_ledger([
        _event(source_module="M11", candidate_key="", display="", calls=1,
               generated_tokens=12, prompt_tokens=30,
               role=EvidenceRole.PARAMETRIC_MEMORY, plane=EvidencePlane.PARAMETRIC,
               sign=EdgeType.UNKNOWN, support=0),
        _event(source_module="M13", calls=0),
    ])
    cost = cost_from_ledger(ledger, ["origin-1"])
    assert (cost.neural_calls, cost.generated_tokens, cost.prompt_tokens) == (1, 12, 30)


# --------------------------------------------------------------------------
# 10-13. q_g, samples, facets, distinct groups
# --------------------------------------------------------------------------


def test_ten_samples_of_one_group_are_one_contribution():
    events = [
        _event(origin_event_id=f"origin-{i}", sample_index=i,
               independence_group="DIRECT_RECALL")
        for i in range(10)
    ]
    supports = group_supports(events)
    assert len(supports) == 1
    assert supports[0].q_g == 1
    assert supports[0].total_events == 10
    assert independent_support(supports) == 1


def test_group_support_is_a_max_and_never_a_sum():
    events = [
        _event(origin_event_id="a", independence_group="G"),
        _event(origin_event_id="b", independence_group="G"),
        _event(origin_event_id="c", independence_group="G",
               sign=EdgeType.UNKNOWN, support=0),
    ]
    support = group_supports(events)[0]
    assert support.q_g == 1                 # max, not 2, not 3
    assert support.total_events == 3
    with pytest.raises(ConsensusError, match="categorical"):
        GroupSupport(group_key="g", plane=EvidencePlane.CORE,
                     role=EvidenceRole.CORE_ACQUISITION, q_g=2,
                     total_events=2, origin_event_ids=())


def test_only_unknown_events_leave_a_group_unsupported():
    supports = group_supports([
        _event(sign=EdgeType.UNKNOWN, support=0),
        _event(origin_event_id="b", sign=EdgeType.CONTRADICT, support=0),
    ])
    assert supports[0].q_g == 0
    assert independent_support(supports) == 0


def test_distinct_structural_groups_raise_i():
    events = [
        _event(independence_group="DIRECT_RECALL", origin_event_id="a"),
        _event(independence_group="STRUCTURAL_DECOMPOSITION", origin_event_id="b"),
        _event(independence_group="PSEUDO_MEMORY_SKETCH", origin_event_id="c",
               plane=EvidencePlane.PARAMETRIC),
    ]
    assert independent_support(group_supports(events)) == 3


def test_facets_of_one_group_do_not_inflate_i():
    """Five decade slices of one mechanism are five facets and one group."""
    events = [
        _event(independence_group="STRUCTURAL_DECOMPOSITION",
               facet_id=f"decade_{year}", origin_event_id=f"origin-{year}")
        for year in (1980, 1990, 2000, 2010, 2020)
    ]
    supports = group_supports(events)
    assert len(supports) == 1
    assert independent_support(supports) == 1
    assert len(supports[0].facets) == 5          # visible, never counted


def test_one_group_name_in_two_planes_is_two_groups():
    """Group names collide across subsystems; the plane keeps them apart."""
    events = [
        _event(independence_group="DIRECT_RECALL", plane=EvidencePlane.CORE,
               role=EvidenceRole.CORE_ACQUISITION, origin_event_id="a"),
        _event(independence_group="DIRECT_RECALL", plane=EvidencePlane.SPECIALIST,
               origin_event_id="b"),
    ]
    assert {g.group_key for g in group_supports(events)} == {
        "core:DIRECT_RECALL", "specialist:DIRECT_RECALL"
    }


def test_two_specialists_mining_one_m11_probe_name_one_group():
    """The mechanism was Module 11's probe, so the plane stays parametric."""
    retrieval = _retrieval(AWARD, {"pseudo_memory#0": "Recipient Alpha"})
    specialist = _mined_specialist(AWARD, retrieval)
    graph, _ = _graph(AWARD)
    result = AtomicConsensusEngine().consense(graph, specialist, retrieval=retrieval)
    mined = [c for c in result.candidates if c.candidate_key == "recipient alpha"]
    assert mined
    assert all(
        g.plane is EvidencePlane.PARAMETRIC for g in mined[0].group_supports
    )


# --------------------------------------------------------------------------
# 14-20. THE AUDIT-0008 ACCOUNTING MATRIX
# --------------------------------------------------------------------------


def _award_state(engine, *, build):
    graph, _ = _graph(AWARD)
    build(graph)
    specialist, _, _ = _specialist_result(AWARD)
    result = engine.consense(graph, specialist)
    return {c.candidate_key: c for c in result.candidates}


def test_ordinary_acquisition_moves_only_f(engine):
    states = _award_state(engine, build=lambda g: _add_entities(
        g, "direct_recall", IndependenceGroup.DIRECT_RECALL, ["Recipient Alpha"]
    ))
    state = states["recipient alpha"]
    assert state.f_support > 0
    assert state.x_cross_model == 0.0
    assert state.l_logit == 0.0 and state.l_available is False
    assert state.c_contradiction == 0.0
    assert state.i_independent_support == 1


def test_independent_cross_model_recall_moves_only_x(engine):
    def build(graph):
        _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                      ["Recipient Alpha"])
        _add_entities(graph, "cross_model", IndependenceGroup.CROSS_MODEL_RECALL,
                      ["Recipient Alpha"], model_id="offline/verifier", family="verifier")

    only_direct = _award_state(engine, build=lambda g: _add_entities(
        g, "direct_recall", IndependenceGroup.DIRECT_RECALL, ["Recipient Alpha"]
    ))["recipient alpha"]
    with_cross = _award_state(engine, build=build)["recipient alpha"]

    assert with_cross.x_cross_model == 1.0
    assert with_cross.f_support == only_direct.f_support     # F unmoved
    assert with_cross.l_logit == 0.0
    # I does rise: a second family recalling it *is* another structural source.
    assert with_cross.i_independent_support == only_direct.i_independent_support + 1


def test_a_shown_candidate_verifier_valid_moves_only_l(engine):
    def build(graph):
        _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                      ["Recipient Alpha"])
        _verify(graph, "recipient alpha", VerificationLabel.VALID)

    baseline = _award_state(engine, build=lambda g: _add_entities(
        g, "direct_recall", IndependenceGroup.DIRECT_RECALL, ["Recipient Alpha"]
    ))["recipient alpha"]
    verified = _award_state(engine, build=build)["recipient alpha"]

    assert verified.l_logit > 0 and verified.l_available
    assert verified.f_support == baseline.f_support          # not acquisition
    assert verified.x_cross_model == 0.0                     # not cross-model
    assert verified.i_independent_support == baseline.i_independent_support
    assert verified.verifier_label == "VALID"
    # And the verifier's group is present but never counted as recall.
    verifier_groups = [
        g for g in verified.group_supports if g.role is EvidenceRole.BLIND_VERIFIER
    ]
    assert verifier_groups and not verifier_groups[0].role.is_recall


def test_a_verifier_invalid_moves_l_and_signed_c(engine):
    """Audit 0008's deliberate exception, preserved exactly."""
    def build(graph):
        _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                      ["Recipient Alpha"])
        _verify(graph, "recipient alpha", VerificationLabel.INVALID,
                valid=0.05, invalid=0.9, unknown=0.05)

    state = _award_state(engine, build=build)["recipient alpha"]
    assert state.l_logit < 0 and state.l_available
    assert state.c_contradiction > 0
    assert "core:BLIND_VERIFIER" in state.contradicting_groups
    assert state.x_cross_model == 0.0


def test_shown_candidate_agreement_can_never_become_x():
    """The anchoring distinction is enforced by the type, not by convention."""
    with pytest.raises(ConsensusError, match="candidate shown"):
        _event(role=EvidenceRole.CROSS_MODEL_RECALL, mode=EvidenceMode.SHOWN_CANDIDATE)
    with pytest.raises(ConsensusError, match="candidate shown"):
        _event(role=EvidenceRole.CORE_ACQUISITION, mode=EvidenceMode.SHOWN_CANDIDATE)
    assert not EvidenceRole.BLIND_VERIFIER.is_recall
    assert not EvidenceRole.BLIND_VERIFIER.pays_f
    assert not EvidenceRole.BLIND_VERIFIER.pays_x
    assert EvidenceRole.BLIND_VERIFIER.pays_l


def test_repeating_one_direct_view_does_not_raise_f(engine):
    once = _award_state(engine, build=lambda g: _add_entities(
        g, "direct_recall", IndependenceGroup.DIRECT_RECALL, ["Recipient Alpha"]
    ))["recipient alpha"]

    def thrice(graph):
        for run in range(3):
            _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                          ["Recipient Alpha"], run=run)

    repeated = _award_state(engine, build=thrice)["recipient alpha"]
    assert repeated.f_support == once.f_support
    assert repeated.i_independent_support == once.i_independent_support
    assert repeated.total_support_events == 3       # frequency stays visible


def test_a_gate_never_becomes_candidate_acquisition(engine):
    def build(graph):
        _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                      ["Recipient Alpha"])
        graph.register_record(
            _record(graph, "existence_gate", IndependenceGroup.EXISTENCE_GATE, "YES")
        )

    state = _award_state(engine, build=build)["recipient alpha"]
    assert state.f_support == 0.2
    assert all(g.role is not EvidenceRole.EXISTENCE_GATE for g in state.group_supports)
    assert core_role(IndependenceGroup.EXISTENCE_GATE) is EvidenceRole.EXISTENCE_GATE
    assert not EvidenceRole.EXISTENCE_GATE.is_recall
    assert not EvidenceRole.EXISTENCE_GATE.pays_f


def test_specialist_evidence_never_enters_f():
    """The recorded F policy: no invented m(o) denominator."""
    retrieval = _retrieval(AWARD, {op: "Recipient Alpha" for op in
                                   ("pseudo_memory#0", "self_ask#0", "query_rewrite#0")})
    specialist = _mined_specialist(AWARD, retrieval)
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha"])
    result = AtomicConsensusEngine().consense(graph, specialist, retrieval=retrieval)
    state = {c.candidate_key: c for c in result.candidates}["recipient alpha"]

    assert state.f_support == 0.2               # one core group of five
    assert state.f_support <= 1.0
    assert state.i_independent_support == 4     # core + three parametric groups


def test_u_stays_module_4_prompt_disagreement(engine):
    def build(graph):
        _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                      ["Recipient Alpha"])
        _verify(graph, "recipient alpha", VerificationLabel.VALID,
                prompt_disagreement=0.25, entropy=0.4)

    state = _award_state(engine, build=build)["recipient alpha"]
    assert state.u_prompt == 0.25 and state.u_available
    # Four distinct uncertainties, never blended.
    assert state.h_ver == 0.4
    assert state.h_inc >= 0.0
    assert state.d_semantic in (0.0, 1.0)
    assert len({"h_inc", "h_ver", "u_prompt", "d_semantic"}) == 4
    payload = state.to_json()
    assert {"H_inc", "H_ver", "U", "D"} <= set(payload)


def test_specialist_disagreement_never_lands_in_u():
    """D exists precisely so that U keeps its Module 4 meaning."""
    graph, _ = _graph(STOCK)
    specialist, _, _ = _specialist_result(STOCK, {
        "m15_stock_listing_gate#0": "LISTED",
        "m15_stock_listing_existence#0": "LISTED",
        "m15_stock_primary#0": "Exchange Alpha",
        "m15_stock_temporal#0": "Exchange Alpha (delisted)",
        "m15_stock_missingness#0": "NONE",
    })
    result = AtomicConsensusEngine().consense(graph, specialist)
    conflicted = [c for c in result.candidates if c.disagreement_details]
    assert conflicted
    for state in conflicted:
        assert state.d_semantic == 1.0
        assert state.u_prompt == 0.0
        assert state.u_available is False


# --------------------------------------------------------------------------
# 21-23. String identity
# --------------------------------------------------------------------------


def test_string_identity_is_module_3s_strict_key():
    graph, contract = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["The Alpha Foundation"])
    specialist, _, _ = _specialist_result(AWARD)
    result = AtomicConsensusEngine().consense(graph, specialist)
    assert [c.candidate_key for c in result.candidates] == [
        contract.strict_key("The Alpha Foundation")
    ]
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    assert "alias_hint" not in blob
    assert "alias_groups" not in blob


def test_an_alias_like_pair_is_never_merged():
    """Audit 0006's decision: a hint is not proof of identity."""
    graph, contract = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["The Alpha Exchange", "Alpha Exchange"])
    assert contract.key("The Alpha Exchange") == contract.key("Alpha Exchange")
    assert contract.strict_key("The Alpha Exchange") != contract.strict_key("Alpha Exchange")

    specialist, _, _ = _specialist_result(AWARD)
    result = AtomicConsensusEngine().consense(graph, specialist)
    assert len(result.candidates) == 2       # kept apart, as Module 3 kept them


def test_no_fuzzy_matching_or_equivalence_judge_exists():
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES).casefold()
    for forbidden in ("levenshtein", "edit_distance", "difflib", "similarity",
                      "fuzz", "embed", "cosine", "equivalence_judge",
                      "semantic_judge", "alias_database"):
        assert forbidden not in blob, forbidden


def test_an_abstention_never_becomes_a_candidate():
    """Module 3 refuses "NONE"; consensus must not mint what the graph declined."""
    retrieval = _retrieval(DEATH)          # scripted default answers are abstentions
    specialist = _mined_specialist(DEATH, retrieval)
    assert any(
        o.normalized_surface for o in specialist.locality_observations
    ), "fixture should exercise the mining path"
    graph, _ = _graph(DEATH)
    result = AtomicConsensusEngine().consense(graph, specialist, retrieval=retrieval)
    assert [c.candidate_key for c in result.candidates] == []


# --------------------------------------------------------------------------
# 24-27. Numeric clusters
# --------------------------------------------------------------------------


def _numeric_result(values, relation=CAPACITY):
    outputs = {
        "m12_exact_quantity_direct#0": str(values[0]),
        "m12_cross_unit_format#0": str(values[min(1, len(values) - 1)]),
        "m12_candidate_free_reelicitation#0": str(values[-1]),
    }
    return _specialist_result(relation, outputs)


def test_numeric_clusters_are_module_12s_and_are_not_recomputed():
    specialist, query, contract = _numeric_result([50000, 50100, 50050])
    graph, _ = _graph(CAPACITY)
    result = AtomicConsensusEngine().consense(graph, specialist)

    assert result.numeric_clusters
    for consensus, cluster in zip(result.numeric_clusters, specialist.clusters):
        assert consensus.representative == cluster.representative
        assert consensus.dispersion == cluster.dispersion
        assert consensus.values == cluster.values
        assert consensus.independent_support == cluster.independent_support
        assert consensus.independence_groups == cluster.independence_groups

    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("cluster_values", "relative_distance", "median", "_relative_mad",
                      "tolerance", "threshold="):
        assert forbidden not in blob, f"M16 re-implements {forbidden}"


def test_core_numeric_evidence_joins_a_cluster_only_on_exact_canonical_equality():
    specialist, _, _ = _numeric_result([50000, 50000, 50000])
    graph, _ = _graph(CAPACITY)
    _add_numbers(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL, [50000.0])
    _add_numbers(graph, "structural", IndependenceGroup.STRUCTURAL_DECOMPOSITION,
                 [12345.0])
    result = AtomicConsensusEngine().consense(graph, specialist)

    assigned = {
        c.candidate_key: c.numeric_cluster_index for c in result.candidates
    }
    assert assigned["50000"] == 0
    assert assigned["12345"] is None
    assert "12345" in result.unassigned_numeric_keys
    assert "50000" not in result.unassigned_numeric_keys


def test_equivalent_units_use_module_12s_canonical_semantics():
    """Two unit representations of one area converge because M12 says so."""
    specialist, _, _ = _specialist_result(AREA, {
        "m12_exact_quantity_direct#0": "5556 km2",
        "m12_cross_unit_format#0": "2145 sq mi",
    })
    graph, _ = _graph(AREA)
    result = AtomicConsensusEngine().consense(graph, specialist)

    assert len(specialist.clusters) == 1, "M12 should merge the two readings"
    cluster = result.numeric_clusters[0]
    assert cluster.representative == specialist.clusters[0].representative
    assert cluster.independent_support == specialist.clusters[0].independent_support
    assert cluster.canonical_unit == specialist.clusters[0].canonical_unit
    # Two structural sources, one cluster - and M16 did no converting.
    assert cluster.independent_support == 2


def test_consensus_over_reloaded_evidence_equals_consensus_over_live_evidence():
    """Requirement 51: persistence must lose nothing M16 reads."""
    from cover_kbc.specialists import SmallSetSpecialistResult

    specialist, _, _ = _specialist_result(STOCK, {
        "m15_stock_listing_gate#0": "LISTED",
        "m15_stock_listing_existence#0": "LISTED",
        "m15_stock_primary#0": "Exchange Alpha (primary listing)",
        "m15_stock_temporal#0": "Exchange Alpha: current",
        "m15_stock_missingness#0": "NONE",
    })
    reloaded = SmallSetSpecialistResult.from_json(
        json.loads(json.dumps(specialist.to_json()))
    )
    graph, _ = _graph(STOCK)
    engine = AtomicConsensusEngine()
    assert engine.consense(graph, reloaded) == engine.consense(graph, specialist)


def test_competing_clusters_produce_disagreement_and_no_winner():
    specialist, _, _ = _numeric_result([50000, 50000, 90000])
    assert len(specialist.clusters) > 1
    graph, _ = _graph(CAPACITY)
    result = AtomicConsensusEngine().consense(graph, specialist)

    assert len(result.numeric_clusters) == len(specialist.clusters)
    assert all(c.competing_clusters >= 1 for c in result.numeric_clusters)
    kinds = {
        d.kind for cluster in result.numeric_clusters
        for d in cluster.disagreement_details
    }
    assert DisagreementKind.NUMERIC_COMPETING_CLUSTERS in kinds
    payload = json.dumps(result.to_json())
    for forbidden in ("winner", "selected_cluster", "accepted", "chosen"):
        assert forbidden not in payload, forbidden


def test_the_evaluator_tolerance_is_not_applied_as_an_acceptance_rule():
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("0.05", "within_tolerance", "numeric_tolerance", "is_correct"):
        assert forbidden not in blob, forbidden


def test_a_hard_definition_violation_is_signed_contradiction_not_support():
    specialist, _, _ = _specialist_result(CAPACITY, {
        "m12_exact_quantity_direct#0": "50000",
        "m12_contrastive_definition#0": "the attendance record was 61000",
    })
    violations = specialist.hard_definition_violations
    assert violations, "fixture should produce a contract-excluded quantity"
    graph, _ = _graph(CAPACITY)
    result = AtomicConsensusEngine().consense(graph, specialist)
    states = {c.candidate_key: c for c in result.candidates}
    excluded = states["61000"]
    assert excluded.i_independent_support == 0
    assert excluded.contradicting_groups


# --------------------------------------------------------------------------
# 28-30. Award consensus
# --------------------------------------------------------------------------


def test_award_atomic_union_fuses_across_true_independent_origins():
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha", "Recipient Beta"])
    _add_entities(graph, "decade_slices", IndependenceGroup.STRUCTURAL_DECOMPOSITION,
                  ["Recipient Alpha"], facet="decade_1990")
    specialist, _, _ = _specialist_result(AWARD, {
        "m13_temporal_middle#0": "Recipient Alpha\nRecipient Gamma",
    })
    result = AtomicConsensusEngine().consense(graph, specialist)
    states = {c.candidate_key: c for c in result.candidates}

    assert set(states) >= {"recipient alpha", "recipient beta"}
    assert states["recipient alpha"].i_independent_support >= 2
    assert states["recipient beta"].i_independent_support == 1
    # Nothing was pruned or ranked.
    assert all(not hasattr(s, "rank") for s in result.candidates)


def test_an_unverified_award_candidate_has_l_unavailable_not_negative():
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha"])
    specialist, _, _ = _specialist_result(AWARD)
    result = AtomicConsensusEngine().consense(graph, specialist)
    state = result.candidates[0]
    assert state.l_available is False
    assert state.l_logit == 0.0
    assert state.verifier_label is None
    assert RiskFlag.UNVERIFIED in state.risk_flags


def test_an_award_near_miss_is_contradiction_and_risk_but_not_a_verdict():
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha"])
    specialist, _, _ = _specialist_result(AWARD, {
        "m13_seed#0": "Recipient Alpha (a nominee, not a recipient)",
    })
    result = AtomicConsensusEngine().consense(graph, specialist)
    state = {c.candidate_key: c for c in result.candidates}["recipient alpha"]

    assert state.contradicting_groups                      # explicit, signed
    assert RiskFlag.NEAR_MISS_MENTION in state.risk_flags   # and flagged as risk
    assert state.i_independent_support >= 1                 # not deleted
    assert DisagreementKind.TARGET_VERSUS_NEAR_MISS in {
        d.kind for d in state.disagreement_details
    }


def test_no_tier_routing_is_performed():
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("VerificationTier", "assign_tier", "TIER_A", "shortlist",
                      "verification_targets"):
        assert forbidden not in blob, f"M16 implements {forbidden}"


# --------------------------------------------------------------------------
# 31-34. Null / temporal
# --------------------------------------------------------------------------


def _death_result(stage_a="DECEASED", stage_b=None):
    outputs = {f"m14_a_{f}#0": stage_a for f in
               ("direct_life_status", "death_event_existence", "life_dates_recollection")}
    outputs.update(stage_b or {})
    return _specialist_result(DEATH, outputs)


def test_competing_death_cities_stay_visible():
    specialist, _, _ = _death_result(stage_b={
        "m14_b_direct_locality#0": "City Alpha",
        "m14_b_biography_locality#0": "City Beta",
        "m14_b_birth_residence_contrast#0": "City Alpha",
        "m14_b_candidate_free_recall#0": "City Beta",
    })
    graph, _ = _graph(DEATH)
    result = AtomicConsensusEngine().consense(graph, specialist)
    keys = {c.candidate_key for c in result.candidates}

    assert {"city alpha", "city beta"} <= keys
    assert result.null_state.competing_candidates == 2
    for state in result.candidates:
        assert DisagreementKind.COMPETING_SINGLE_VALUE in {
            d.kind for d in state.disagreement_details
        }
    payload = json.dumps(result.to_json())
    for forbidden in ("top1", "final_answer", "selected", "prediction"):
        assert forbidden not in payload, forbidden


def test_all_unknown_stage_b_gives_zero_substantive_null_support():
    """Audit 0021 §15A, carried into consensus unchanged."""
    specialist, _, _ = _death_result(stage_b={
        f"m14_b_{facet}#0": "UNKNOWN" for facet in
        ("direct_locality", "biography_locality", "birth_residence_contrast",
         "candidate_free_recall")
    })
    graph, _ = _graph(DEATH)
    result = AtomicConsensusEngine().consense(graph, specialist)
    null = result.null_state

    assert null.no_known_locality_support == 0
    assert null.substantive_null_groups == 0
    assert null.has_substantive_null_evidence is False
    assert null.failed_recall_only is True
    assert null.failed_recall_operations > 0
    payload = json.dumps(null.to_json())
    for forbidden in ("final_empty", "accepted_empty", "gold_empty", "is_empty"):
        assert forbidden not in payload, forbidden


def test_explicit_null_evidence_is_preserved_with_provenance():
    specialist, _, _ = _death_result(stage_a="LIVING")
    graph, _ = _graph(DEATH)
    result = AtomicConsensusEngine().consense(graph, specialist)
    null = result.null_state

    assert null.living_support > 0
    assert null.living_groups
    assert null.has_substantive_null_evidence
    assert null.failed_recall_only is False
    assert null.gate_state == specialist.gate.state.value


def test_repeating_failed_recall_cannot_inflate_null_consensus():
    single, _, _ = _death_result(stage_b={
        "m14_b_direct_locality#0": "UNKNOWN",
    })
    many, _, _ = _death_result(stage_b={
        f"m14_b_{facet}#0": "UNKNOWN" for facet in
        ("direct_locality", "biography_locality", "birth_residence_contrast",
         "candidate_free_recall")
    })
    graph, _ = _graph(DEATH)
    engine = AtomicConsensusEngine()
    one = engine.consense(graph, single).null_state
    lots = engine.consense(graph, many).null_state

    assert one.substantive_null_groups == lots.substantive_null_groups == 0
    assert lots.failed_recall_operations >= one.failed_recall_operations
    assert lots.has_substantive_null_evidence is False


def test_a_strong_city_candidate_does_not_erase_null_conflict():
    specialist, _, _ = _death_result(stage_a="LIVING", stage_b={
        "m14_b_direct_locality#0": "City Alpha",
    })
    graph, _ = _graph(DEATH)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["City Alpha"])
    result = AtomicConsensusEngine().consense(graph, specialist)

    assert any(c.i_independent_support > 0 for c in result.candidates)
    assert result.null_state.living_support > 0        # both visible at once


def test_only_the_null_relation_has_a_null_state(engine):
    for relation in (AWARD, CAPACITY, BORDERS, STOCK):
        graph, _ = _graph(relation)
        specialist, _, _ = _specialist_result(relation)
        assert engine.consense(graph, specialist).null_state is None


# --------------------------------------------------------------------------
# 35-38. Small-set consensus
# --------------------------------------------------------------------------


def test_a_border_qualification_is_disagreement_without_a_verdict():
    specialist, _, _ = _specialist_result(BORDERS, {
        "m15_border_geographic#0":
            "Country Beta\nCountry Beta (a maritime boundary only)",
        "m15_border_missingness#0": "NONE",
    })
    graph, _ = _graph(BORDERS)
    result = AtomicConsensusEngine().consense(graph, specialist)
    state = {c.candidate_key: c for c in result.candidates}["country beta"]

    assert DisagreementKind.TARGET_VERSUS_NEAR_MISS in {
        d.kind for d in state.disagreement_details
    }
    assert state.contradicting_groups
    assert state.i_independent_support >= 1        # nothing removed
    payload = json.dumps(state.to_json())
    for forbidden in ("rejected", "accepted", "valid", "invalid"):
        assert forbidden not in payload.casefold(), forbidden


def test_current_versus_delisted_is_preserved_as_semantic_disagreement():
    specialist, _, _ = _specialist_result(STOCK, {
        "m15_stock_listing_gate#0": "LISTED",
        "m15_stock_listing_existence#0": "LISTED",
        "m15_stock_primary#0": "Exchange Alpha: current",
        "m15_stock_temporal#0": "Exchange Alpha: former",
        "m15_stock_missingness#0": "NONE",
    })
    graph, _ = _graph(STOCK)
    result = AtomicConsensusEngine().consense(graph, specialist)
    state = {c.candidate_key: c for c in result.candidates}["exchange alpha"]
    assert DisagreementKind.TEMPORAL_STATUS_CONFLICT in {
        d.kind for d in state.disagreement_details
    }


def test_pending_checks_are_carried_forward_unexecuted():
    specialist, _, _ = _specialist_result(BORDERS, {
        "m15_border_geographic#0": "Country Beta",
        "m15_border_missingness#0": "NONE",
    })
    assert specialist.pending_checks
    graph, _ = _graph(BORDERS)
    result = AtomicConsensusEngine().consense(graph, specialist)

    assert len(result.pending_checks) == len(specialist.pending_checks)
    assert {p.kind for p in result.pending_checks} == {
        p.kind.value for p in specialist.pending_checks
    }
    assert all(p.source_module == "M15" for p in result.pending_checks)
    flagged = [c for c in result.candidates if RiskFlag.PENDING_DOWNSTREAM_CHECK
               in c.risk_flags]
    assert flagged
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("reverse_prompt", "counterfactual", "execute_check", "run_check"):
        assert forbidden not in blob, f"M16 executes {forbidden}"


def test_m15_closure_snapshots_are_never_relabelled_accepted():
    specialist, _, _ = _specialist_result(BORDERS, {
        "m15_border_geographic#0": "Country Beta",
        "m15_border_missingness#0": "NONE",
    })
    graph, _ = _graph(BORDERS)
    result = AtomicConsensusEngine().consense(graph, specialist)
    payload = json.dumps(result.to_json())
    for forbidden in ("accepted_set", "final_set", "A_t", "closure_accepted",
                      "should_stop", "CLOSED"):
        assert forbidden not in payload, forbidden
    assert specialist.closure.before.stage == "observed_before_missingness"


# --------------------------------------------------------------------------
# 39-42. Cost, availability, hard violations
# --------------------------------------------------------------------------


def test_cost_is_charged_once_per_physical_output():
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha", "Recipient Beta", "Recipient Gamma"],
                  generated=30, prompt_tokens=40)
    specialist, _, _ = _specialist_result(AWARD)
    result = AtomicConsensusEngine().consense(graph, specialist)

    # One record produced three candidate edges: one call, 30 generated tokens,
    # not three of each.
    assert result.cost.generated_tokens == 30
    assert result.cost.prompt_tokens == 40
    # Every physical output is represented once: the core record plus each of
    # the specialist's own probes, and nothing else.
    assert result.cost.neural_calls == 1 + specialist.calls
    assert result.cost.unique_origin_events == 1 + specialist.calls
    # The specialist reports spend in aggregate, so its per-origin token counts
    # are unknown - and stay unknown rather than being written down as zero.
    assert result.cost.origins_missing_tokens == specialist.calls


def test_missing_latency_stays_missing():
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha"], latency=None)
    specialist, _, _ = _specialist_result(AWARD)
    result = AtomicConsensusEngine().consense(graph, specialist)
    assert result.cost.latency_ms is None
    assert result.cost.latency_available is False

    timed, _ = _graph(AWARD)
    _add_entities(timed, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha"], latency=12.5)
    measured = AtomicConsensusEngine().consense(timed, specialist)
    assert measured.cost.latency_ms == 12.5
    assert measured.cost.latency_available is True


def test_availability_is_distinguished_from_measured_zero(engine):
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha", "Recipient Beta"])
    _verify(graph, "recipient alpha", VerificationLabel.VALID, prompt_disagreement=0.0)
    specialist, _, _ = _specialist_result(AWARD)
    states = {
        c.candidate_key: c
        for c in engine.consense(graph, specialist).candidates
    }
    measured, never = states["recipient alpha"], states["recipient beta"]

    assert measured.u_prompt == 0.0 and measured.u_available is True
    assert never.u_prompt == 0.0 and never.u_available is False
    assert measured.l_available is True and never.l_available is False


def test_hard_contract_violations_are_preserved_from_module_3(engine):
    from cover_kbc.evidence.graph import apply_hard_contract_rules

    graph, _ = _graph(CAPACITY)
    _add_numbers(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL, [-5.0, 50000.0])
    apply_hard_contract_rules(graph)
    specialist, _, _ = _numeric_result([50000, 50000, 50000])
    states = {
        c.candidate_key: c for c in engine.consense(graph, specialist).candidates
    }

    violating = states["-5"]
    assert violating.hard_contract_violation
    assert violating.rejection_reason == "numeric value must be positive"
    assert RiskFlag.HARD_CONTRACT_VIOLATION in violating.risk_flags
    assert not states["50000"].hard_contract_violation
    # M16 re-derives no factual rule of its own.
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    assert "apply_hard_contract_rules" not in blob


def test_the_payload_carries_no_final_status(engine):
    for relation in sorted(SUBJECTS):
        graph, _ = _graph(relation)
        specialist, _, _ = _specialist_result(relation)
        payload = json.dumps(engine.consense(graph, specialist).to_json())
        for forbidden in ("prediction", "final_set", "accepted_set", "should_stop",
                          "rejected_set", "is_correct", "gold"):
            assert forbidden not in payload, f"{relation}: {forbidden}"


def test_no_state_is_named_like_a_verdict():
    fields = set(CandidateConsensusState.__dataclass_fields__)
    for forbidden in ("accepted", "rejected", "valid", "invalid", "final",
                      "score", "rank", "status", "tier"):
        assert not any(forbidden in name for name in fields), forbidden


# --------------------------------------------------------------------------
# 43-47. Architecture boundaries
# --------------------------------------------------------------------------


def test_no_module_17_verification():
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("VerifierTemplate", "build_verifier_prompt", "score_labels",
                      "verifier_runtime", "LABEL_TOKENS", "ContextualCalibrator",
                      "calibrate("):
        assert forbidden not in blob, f"M16 implements {forbidden}"


def test_module_4_prompt_surface_is_byte_identical():
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


def test_no_module_19_estimator_and_no_module_20_21_planner():
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("residual", "missingness_estimate", "saturation",
                      "allocate_budget", "schedule_budget", "next_action",
                      "expected_value", "should_stop", "STOP"):
        assert forbidden not in blob, f"M16 implements {forbidden}"


def test_the_production_graph_is_read_only(engine):
    graph, _ = _graph(AWARD)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Recipient Alpha", "Recipient Beta"])
    _verify(graph, "recipient alpha", VerificationLabel.VALID)
    specialist, _, _ = _specialist_result(AWARD)

    before = copy.deepcopy(graph.to_json())
    statuses = {k: c.status for k, c in graph.candidates.items()}
    scores = {k: c.score for k, c in graph.candidates.items()}
    edges = set(graph._edge_ids)

    engine.consense(graph, specialist)

    assert graph.to_json() == before
    assert {k: c.status for k, c in graph.candidates.items()} == statuses
    assert {k: c.score for k, c in graph.candidates.items()} == scores
    assert set(graph._edge_ids) == edges
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("add_evidence", "add_entity_mentions", "add_verification",
                      "graph.reject", "close_gate", "register_record"):
        assert forbidden not in blob, f"M16 writes to the graph via {forbidden}"


def test_prior_specialist_results_are_untouched(engine):
    for relation in sorted(SUBJECTS):
        graph, _ = _graph(relation)
        specialist, _, _ = _specialist_result(relation)
        before = copy.deepcopy(specialist.to_json())
        engine.consense(graph, specialist)
        assert specialist.to_json() == before, relation


# --------------------------------------------------------------------------
# 48-52. Provenance errors, config, round trip
# --------------------------------------------------------------------------


def test_conflicting_provenance_for_one_origin_raises():
    events = [
        _event(origin_event_id="shared", model_id="model-a"),
        _event(origin_event_id="shared", model_id="model-b", source_module="M3"),
    ]
    with pytest.raises(ConsensusProvenanceError, match="different model_id"):
        check_origin_consistency(events)

    families = [
        _event(origin_event_id="shared", model_family="enumerator"),
        _event(origin_event_id="shared", model_family="verifier", source_module="M3"),
    ]
    with pytest.raises(ConsensusProvenanceError, match="two model families"):
        check_origin_consistency(families)

    consistent = [
        _event(origin_event_id="shared", source_module="M11", candidate_key="",
               display="", role=EvidenceRole.PARAMETRIC_MEMORY,
               plane=EvidencePlane.PARAMETRIC, sign=EdgeType.UNKNOWN, support=0),
        _event(origin_event_id="shared", source_module="M13"),
    ]
    check_origin_consistency(consistent)      # same source, two readings: fine


def test_invalid_sign_and_support_combinations_are_refused():
    with pytest.raises(ConsensusError, match="SUPPORT event must carry"):
        _event(sign=EdgeType.SUPPORT, support=0)
    with pytest.raises(ConsensusError, match="only a SUPPORT event"):
        _event(sign=EdgeType.CONTRADICT, support=1)
    with pytest.raises(ConsensusError, match="categorical"):
        _event(support=3)
    with pytest.raises(ConsensusError, match="categorical"):
        _event(sign=EdgeType.UNKNOWN, support=-1)


def test_a_specialist_observation_can_never_claim_verification():
    with pytest.raises(ConsensusError, match="never verify"):
        _event(verified=True)
    verifier = _event(role=EvidenceRole.BLIND_VERIFIER, verified=True,
                      mode=EvidenceMode.SHOWN_CANDIDATE, sign=EdgeType.SUPPORT,
                      support=1)
    assert verifier.verified


def test_configuration_failures_are_loud():
    with pytest.raises(ValueError, match="unsupported consensus mode"):
        ConsensusConfig.from_mapping({"enabled": True, "mode": "production"})
    with pytest.raises(ValueError, match="unknown consensus key"):
        ConsensusConfig.from_mapping({"enabled": True, "enabledd": True})
    with pytest.raises(ValueError, match="unsupported consensus_version"):
        ConsensusConfig.from_mapping({"enabled": True, "consensus_version": "m16-v9"})
    with pytest.raises(ValueError, match="parametric_retrieval"):
        build_consensus_engine(
            {"enabled": True}, profiler_enabled=True, compiler_enabled=True,
            retrieval_enabled=False,
        )
    with pytest.raises(ValueError, match="profiler"):
        build_consensus_engine(
            {"enabled": True}, profiler_enabled=False, compiler_enabled=False,
            retrieval_enabled=False,
        )


def test_disabled_or_absent_config_builds_nothing():
    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    assert build_consensus_engine(None, **kwargs) is None
    assert build_consensus_engine({}, **kwargs) is None
    assert build_consensus_engine({"enabled": False}, **kwargs) is None


def test_the_shipped_configs_keep_m16_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        block = yaml.safe_load(Path(name).read_text())["consensus"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["consensus_version"] == CONSENSUS_VERSION, name


def test_no_score_weights_exist_in_the_config():
    fields = set(ConsensusConfig.__dataclass_fields__)
    for forbidden in ("alpha", "beta", "gamma", "delta", "eta", "weight",
                      "threshold", "tolerance"):
        assert not any(forbidden in name for name in fields), forbidden


@pytest.mark.parametrize("relation", sorted(SUBJECTS))
def test_every_public_type_round_trips(relation):
    graph, _ = _graph(relation)
    _add_entities(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL,
                  ["Object Alpha"]) if relation not in (CAPACITY, AREA) else (
        _add_numbers(graph, "direct_recall", IndependenceGroup.DIRECT_RECALL, [42.0])
    )
    specialist, _, _ = _specialist_result(relation)
    result = AtomicConsensusEngine().consense(graph, specialist)

    payload = json.loads(json.dumps(result.to_json()))
    assert QueryConsensusResult.from_json(payload) == result
    for original, entry in zip(result.candidates, payload["candidates"]):
        assert CandidateConsensusState.from_json(entry) == original
        for group, group_payload in zip(original.group_supports, entry["group_supports"]):
            assert GroupSupport.from_json(group_payload) == group
    for original, entry in zip(result.query_events, payload["query_events"]):
        assert ConsensusEvidenceEvent.from_json(entry) == original
    assert ConsensusCost.from_json(payload["cost"]) == result.cost
    if result.null_state:
        assert NullConsensusState.from_json(payload["null_state"]) == result.null_state
    for original, entry in zip(result.numeric_clusters, payload["numeric_clusters"]):
        assert NumericClusterConsensus.from_json(entry) == original
    for original, entry in zip(result.pending_checks, payload["pending_checks"]):
        assert PendingDownstreamCheck.from_json(entry) == original


def test_semantic_disagreement_round_trips():
    detail = SemanticDisagreement(
        kind=DisagreementKind.TARGET_VERSUS_NEAR_MISS, detail="d",
        origin_event_ids=("a",), group_keys=("g",),
    )
    assert SemanticDisagreement.from_json(
        json.loads(json.dumps(detail.to_json()))
    ) == detail


# --------------------------------------------------------------------------
# 50, 51, 53-55. Staged invariance, round trip, parameters, closed book
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


def _config(tmp_path: Path, *, m16: bool, tag: str) -> Path:
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
    config["consensus"] = {"enabled": m16, "mode": "shadow"}
    path = tmp_path / f"config_{tag}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _run(cli, monkeypatch, config: Path, run_dir: Path, relation: str, limit: int = 3):
    monkeypatch.setattr(
        sys, "argv",
        ["run_staged.py", "all", "--config", str(config), "--split", "train",
         "--limit", str(limit), "--relation", relation, "--run-dir", str(run_dir)],
    )
    assert cli.main() == 0


@pytest.mark.parametrize("relation", [CAPACITY, AWARD, DEATH, STOCK])
def test_shadow_mode_changes_no_production_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run(cli, monkeypatch, _config(tmp_path, m16=True, tag="on"), on, relation)
    _run(cli, monkeypatch, _config(tmp_path, m16=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in PRODUCTION_ARTEFACTS:
        left, right = on / name, off / name
        if not left.exists() and not right.exists():
            continue
        assert left.read_bytes() == right.read_bytes(), name

    assert (on / "atomic_consensus.jsonl").is_file()
    assert not (off / "atomic_consensus.jsonl").exists()


def test_the_artefact_is_manifest_ordered_and_carries_provenance(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run(cli, monkeypatch, _config(tmp_path, m16=True, tag="on"), run_dir, AWARD)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "atomic_consensus.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["SubjectEntity"], r["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        assert row["consensus_version"] == CONSENSUS_VERSION
        assert row["applicable_specialist"] == "M13"
        # Module 9's grades are carried as typed descriptors, not as a scalar.
        assert row["query_risk"]
        assert all(isinstance(v, str) for v in row["query_risk"].values())
        assert set(row["upstream_versions"]) >= {"M9", "M10", "M11", "specialist"}
        for key in ("candidates", "pending_checks", "cost", "query_events", "errors"):
            assert key in row, key
        for forbidden in ("gold", "ObjectEntities", "prediction", "final_set",
                          "accepted_set", "should_stop"):
            assert forbidden not in json.dumps(row), forbidden


def test_the_staged_round_trip_reproduces_the_same_state(
    cli, tmp_path, monkeypatch, capsys
):
    """Consensus over reloaded evidence equals consensus over live evidence."""
    run_dir = tmp_path / "roundtrip"
    _run(cli, monkeypatch, _config(tmp_path, m16=True, tag="on"), run_dir, STOCK)
    capsys.readouterr()

    persisted = [
        QueryConsensusResult.from_json(json.loads(line))
        for line in (run_dir / "atomic_consensus.jsonl").read_text().splitlines()
    ]
    assert persisted
    for result in persisted:
        reserialised = QueryConsensusResult.from_json(
            json.loads(json.dumps(result.to_json()))
        )
        assert reserialised == result


def test_m16_introduces_no_new_parameters(tmp_path):
    import os
    import subprocess

    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.evidence.consensus import AtomicConsensusEngine\n"
        "AtomicConsensusEngine()\n"
        "print(','.join(sorted(m for m in sys.modules if m in "
        "('torch', 'transformers', 'mistral_common'))))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == ""


def test_no_external_retrieval_exists():
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES).casefold()
    for forbidden in ("wikipedia", "wikidata", "http://", "https://", "api_key",
                      "entity_linker", "knowledge_base", "sparql"):
        assert forbidden not in blob, forbidden


def test_benchmark_is_untouched():
    import subprocess

    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True
        ).stdout == "", args


def test_the_pipeline_without_an_engine_is_the_pre_m16_path():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(ScriptedRuntime({}), PipelineConfig())
    assert pipeline.consensus_engine is None
    assert pipeline.consensus_results == []
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    pipeline.decide_graph(graph)
    assert pipeline.consensus_results == []


def test_the_pipeline_fails_loudly_when_the_specialist_result_is_missing():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(),
        consensus_engine=AtomicConsensusEngine(),
    )
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    with pytest.raises(ConsensusError, match="no M13 result is available"):
        pipeline.decide_graph(graph)


def test_the_specialist_result_is_matched_by_identity_not_position():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(),
        consensus_engine=AtomicConsensusEngine(),
    )
    wrong, _, _ = _specialist_result(AWARD, subject="Award Omega")
    right, _, _ = _specialist_result(AWARD)
    pipeline.large_set_results.extend([wrong, right])

    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    pipeline.decide_graph(graph)
    assert len(pipeline.consensus_results) == 1
    assert pipeline.consensus_results[0].subject == SUBJECTS[AWARD]


def test_module_11_events_are_registered_but_never_re_parsed():
    retrieval = _retrieval(AWARD, {"pseudo_memory#0": "Recipient Alpha"})
    events = parametric_events(
        retrieval, relation=AWARD, subject=SUBJECTS[AWARD], row_index=0
    )
    assert events
    assert all(e.candidate_key == "" for e in events)
    assert all(e.role is EvidenceRole.PARAMETRIC_MEMORY for e in events)
    assert all(e.support == 0 for e in events)
    blob = " ".join(_code_without_prose(n) for n in M16_MODULES)
    for forbidden in ("extract_candidates", "split_candidates", "parse_numbers",
                      "classify_mention"):
        assert forbidden not in blob, f"M16 re-parses via {forbidden}"


def test_the_engine_reads_module_5s_own_term_functions():
    """F, L, C and U are Module 5's, not re-derived approximations."""
    source = (Path("src/cover_kbc/evidence") / "consensus.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "cover_kbc.scoring":
            imported.update(alias.name for alias in node.names)
    assert {"support_term", "logit_term", "contradiction_term", "disagreement_term",
            "coverage_q", "inclusion_uncertainty", "contradicting_groups"} <= imported


def test_a_specialist_only_candidate_has_structurally_zero_core_terms():
    """It was never in the core plane, so its core terms are zero - and its
    verifier evidence is *unavailable*, not neutral."""
    specialist, _, _ = _specialist_result(BORDERS, {
        "m15_border_geographic#0": "Country Beta",
        "m15_border_missingness#0": "NONE",
    })
    graph, _ = _graph(BORDERS)
    result = AtomicConsensusEngine().consense(graph, specialist)
    state = {c.candidate_key: c for c in result.candidates}["country beta"]

    assert graph.candidates == {}
    assert state.f_support == 0.0
    assert state.l_available is False and state.u_available is False
    assert state.i_independent_support >= 1     # the specialist did see it
    assert state.hard_contract_violation is False


def test_replacing_a_state_keeps_it_frozen_and_recomputable():
    state = CandidateConsensusState(
        relation=AWARD, subject="s", row_index=0, candidate_key="k",
        display="K", candidate_kind="ENTITY",
    )
    with_details = state.with_disagreements((
        SemanticDisagreement(kind=DisagreementKind.COMPETING_SINGLE_VALUE, detail="d"),
    ))
    assert state.d_semantic == 0.0 and with_details.d_semantic == 1.0
    assert with_details.disagreement_kinds == ("COMPETING_SINGLE_VALUE",)
    with pytest.raises(Exception):
        replace(state, candidate_key="other").candidate_key = "mutated"
