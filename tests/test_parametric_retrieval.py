"""Module 11 - Closed-Book Parametric Retrieval conformance.

Five things have to hold:

* the probe families are exactly the three the proposal declares (§7.2);
* there is no external retriever anywhere - the only factual source is the
  frozen model's weights;
* every record is marked unverified parametric memory and creates no evidence;
* every neural call is attributable, counted once, and kept out of Module 7's
  budget;
* enabling M11 changes nothing about what the system predicts.

Unlike Modules 9 and 10, M11 genuinely spends neural calls. The guarantee tested
here is *attributable and separated* cost, never zero cost.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.contracts.router import compile_query
from cover_kbc.models.base import GenerationRequest, GenerationResult
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.query_intelligence import (
    DEFAULT_OPERATIONS,
    OPERATION_SPECS,
    RETRIEVAL_VERSION,
    ExpectedOutputKind,
    MemorySource,
    ParametricIndependenceGroup,
    ParametricMemoryRecord,
    ParametricRetrievalPlan,
    ParametricRetrievalResult,
    ParametricRetriever,
    ParseStatus,
    PromptProgramCompiler,
    QueryProfiler,
    RecallOperationKind,
    RetrievalConfig,
    RetrievalError,
    build_parametric_retriever,
    operation_catalogue,
)
from cover_kbc.types import Query

BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
AWARD = "awardWonBy"
ALL_RELATIONS = (BORDERS, STOCK, DEATH, CAPACITY, AREA, AWARD)

CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
ROLESWAP = "configs/experiments/smoke_staged_roleswap.yaml"
#: Artefacts that must not change when M11 is switched on.
ARTEFACTS = (
    "predictions.jsonl",
    "diagnostics.json",
    "trace.jsonl",
    "stage_a_enumerated.jsonl",
    "stage_b_verified.jsonl",
    "query_profiles.jsonl",
    "prompt_programs.jsonl",
)

M11_MODULES = ("retrieval_types.py", "retrieval_templates.py", "parametric_retrieval.py")


def _code_without_prose(name: str) -> str:
    """Source with docstrings and comments removed.

    These modules document at length what they must *not* do - "no vector DB",
    "does not cluster numbers", "not a retrieved fact". A raw text scan would
    match the prohibition itself, so the scans below read the executable code:
    what the module does, not what it says about itself.
    """
    import ast
    import io
    import tokenize

    path = Path("src/cover_kbc/query_intelligence") / name
    source = path.read_text(encoding="utf-8")

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
        if token.type == tokenize.STRING and token.string.strip("\"'bruBRU") in docstrings:
            continue
        kept.append(token.string)
    return " ".join(kept)


@pytest.fixture
def retriever():
    return ParametricRetriever()


def _program(subject: str, relation: str, row_index: int = 0):
    query, contract = compile_query(subject, relation, row_index)
    profile = QueryProfiler().profile(query, contract)
    return query, PromptProgramCompiler().compile(query, contract, profile)


def _scripted(outputs: dict[str, str] | None = None, subject: str = "Subject",
              relation: str = BORDERS) -> ScriptedRuntime:
    """A runtime that replays one output per M11 operation id."""
    script = {
        (op_id, subject, relation): [text] for op_id, text in (outputs or {}).items()
    }
    return ScriptedRuntime(script, model_id="offline/scripted-m11")


# --------------------------------------------------------------------------
# 1. Proposal dependency
# --------------------------------------------------------------------------


def test_the_probe_families_are_exactly_the_three_the_proposal_declares():
    """Proposal §7.2 names three probe families. A fourth would be a redesign."""
    assert {kind.value for kind in RecallOperationKind} == {
        "pseudo_memory", "self_ask", "query_rewrite"
    }
    assert DEFAULT_OPERATIONS == (
        RecallOperationKind.PSEUDO_MEMORY,
        RecallOperationKind.SELF_ASK,
        RecallOperationKind.QUERY_REWRITE,
    )
    assert set(OPERATION_SPECS) == set(RecallOperationKind)
    assert len(operation_catalogue()) == 3


def test_every_family_has_a_distinct_independence_group():
    groups = [spec.independence_group for spec in OPERATION_SPECS.values()]
    assert len(set(groups)) == len(groups) == 3


def test_an_unknown_operation_family_is_rejected():
    with pytest.raises(ValueError, match="unknown parametric retrieval operation"):
        RetrievalConfig.from_mapping({"enabled": True, "operations": ["web_search"]})


# --------------------------------------------------------------------------
# 2-3. M9/M10 dependency and identity consistency
# --------------------------------------------------------------------------


def test_m11_requires_m9_and_m10():
    with pytest.raises(ValueError, match="profiler and prompt_compiler are not"):
        build_parametric_retriever(
            {"parametric_retrieval": {"enabled": True}},
            profiler_enabled=False, compiler_enabled=False,
        )
    with pytest.raises(ValueError, match="prompt_compiler is not"):
        build_parametric_retriever(
            {"parametric_retrieval": {"enabled": True}},
            profiler_enabled=True, compiler_enabled=False,
        )


def test_a_retriever_without_a_compiler_is_rejected_at_the_pipeline_too():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a prompt compiler"):
        CoverPipeline(
            NullRuntime(model_id="offline/null"), PipelineConfig(),
            profiler=QueryProfiler(), retriever=ParametricRetriever(),
        )


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("relation", AWARD, "program is for"),
        ("subject", "Elsewhere", "program subject"),
        ("row_index", 99, "row_index"),
        ("profile_version", "", "profile_version"),
        ("compiler_version", "", "compiler_version"),
    ],
)
def test_identity_disagreement_fails_loudly(retriever, field, value, message):
    from dataclasses import replace

    query, program = _program("Subject", BORDERS, 0)
    broken = replace(program, **{field: value})
    with pytest.raises(RetrievalError, match=message):
        retriever.plan(query, broken)


def test_the_retriever_never_rebuilds_m9_or_m10():
    """The stack is M1 -> M9 -> M10 -> M11, not M11(reimplements them)."""
    source = Path("src/cover_kbc/query_intelligence/parametric_retrieval.py").read_text()
    for forbidden in ("QueryProfiler", "PromptProgramCompiler", "subject_surface_features",
                      "get_prompt_spec", "RELATION_PROMPT_SPECS"):
        assert forbidden not in source, f"M11 references {forbidden}"


# --------------------------------------------------------------------------
# 4. No external retrieval
# --------------------------------------------------------------------------


def test_no_m11_module_imports_anything_network_capable():
    """AST scan: the package cannot open a connection even by accident."""
    import ast

    root = Path("src/cover_kbc/query_intelligence")
    banned = {
        "requests", "httpx", "urllib", "socket", "http", "aiohttp",
        "ftplib", "smtplib", "sqlite3", "faiss", "chromadb", "pinecone",
    }
    for name in M11_MODULES:
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in banned, f"{name} imports {module}"


def test_no_retriever_abstraction_exists_anywhere_in_m11():
    """The module name must not have tempted a retriever backend into existence."""
    for name in M11_MODULES:
        folded = _code_without_prose(name).casefold()
        for forbidden in (
            "search(", "retrieve_documents", "vector_db", "vectorstore",
            "wikipedia", "wikidata", "elasticsearch", "http://", "https://",
            "api_key", "endpoint", "corpus",
        ):
            assert forbidden not in folded, f"{name} contains {forbidden!r}"


def test_no_new_model_loader_is_introduced():
    """M11 uses the repository's existing runtime abstraction, nothing else."""
    source = Path("src/cover_kbc/query_intelligence/parametric_retrieval.py").read_text()
    assert "from cover_kbc.models.base import" in source
    for forbidden in ("from_pretrained", "AutoModel", "build_runtime", "torch", "transformers"):
        assert forbidden not in source, f"M11 references {forbidden}"


# --------------------------------------------------------------------------
# 5-6. Frozen runtime only; operation-family coverage
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", ALL_RELATIONS)
def test_every_relation_plans_all_three_families(retriever, relation):
    _, program = _program("Subject", relation)
    query, _ = compile_query("Subject", relation, 0)
    plan = retriever.plan(query, program)

    assert [op.kind for op in plan.operations] == list(DEFAULT_OPERATIONS)
    assert plan.max_operations == 3 and plan.estimated_calls == 3
    for operation in plan.operations:
        assert operation.prompt.strip()
        assert operation.system_prompt.strip()
        assert operation.prompt_sha256


def test_every_family_executes_under_a_scripted_runtime(retriever):
    query, program = _program("Subject", BORDERS)
    runtime = _scripted({
        "pseudo_memory#0": "The subject is a landlocked state with several neighbours.",
        "self_ask#0": "Q: integral territory?\nA: yes\nQ: currently recognised?\nA: yes",
        "query_rewrite#0": "Alpha; Beta",
    })
    result = retriever.retrieve(query, program, runtime)

    assert len(result.records) == 3
    assert all(record.parse_status is ParseStatus.OK for record in result.records)
    assert {record.kind for record in result.records} == set(RecallOperationKind)
    assert result.records[2].raw_output == "Alpha; Beta"


def test_planning_calls_no_model(retriever):
    query, program = _program("Subject", BORDERS)
    runtime = _scripted()
    retriever.plan(query, program)
    assert runtime.calls == 0


def test_expected_output_kind_follows_the_answer_schema(retriever):
    for relation, kind in (
        (CAPACITY, ExpectedOutputKind.NUMBER),
        (AREA, ExpectedOutputKind.NUMBER),
        (BORDERS, ExpectedOutputKind.OBJECT_LIST),
        (AWARD, ExpectedOutputKind.OBJECT_LIST),
    ):
        query, program = _program("Subject", relation)
        plan = retriever.plan(query, program)
        rewrite = next(op for op in plan.operations if op.kind is RecallOperationKind.QUERY_REWRITE)
        assert rewrite.expected_output_kind is kind
    # The two structural probes ask for their own shapes regardless of relation.
    query, program = _program("Subject", CAPACITY)
    plan = retriever.plan(query, program)
    assert plan.operations[0].expected_output_kind is ExpectedOutputKind.PROSE
    assert plan.operations[1].expected_output_kind is ExpectedOutputKind.QA_PAIRS


# --------------------------------------------------------------------------
# 7-8. PromptProgram authority; no relation scattering
# --------------------------------------------------------------------------


def test_prompts_are_rendered_from_module_10_structured_fields(retriever):
    query, program = _program("Testcorp", STOCK)
    plan = retriever.plan(query, program)
    rewrite = next(op for op in plan.operations if op.kind is RecallOperationKind.QUERY_REWRITE)

    assert program.task_semantics.definition in rewrite.prompt
    for rule in program.negative_constraints:
        assert rule in rewrite.prompt
    for anchor in program.negative_anchors:
        assert anchor in rewrite.prompt
    assert program.output_contract in rewrite.prompt


def test_the_self_ask_probe_uses_module_10_step_back_specification(retriever):
    query, program = _program("Testperson", DEATH)
    plan = retriever.plan(query, program)
    self_ask = next(op for op in plan.operations if op.kind is RecallOperationKind.SELF_ASK)

    spec = program.query_specification
    assert spec.semantic_question in self_ask.prompt
    for cue in spec.abstraction_cues:
        assert cue in self_ask.prompt


def test_the_pseudo_memory_probe_uses_module_10_cues_and_anchors(retriever):
    query, program = _program("Stadium", CAPACITY)
    plan = retriever.plan(query, program)
    sketch = plan.operations[0]

    for cue in program.semantic_cues:
        assert cue in sketch.prompt
    for anchor in program.negative_anchors:
        assert anchor in sketch.prompt


def test_no_relation_name_appears_in_m11_source():
    root = Path("src/cover_kbc/query_intelligence")
    for name in M11_MODULES:
        source = (root / name).read_text(encoding="utf-8")
        for relation in ALL_RELATIONS:
            assert relation not in source, f"{name} branches on {relation}"


def test_no_relation_definition_is_restated_in_m11():
    blob = " ".join(_code_without_prose(name) for name in M11_MODULES)
    for contract in CONTRACTS.values():
        assert contract.definition not in blob, contract.relation


def test_the_rendered_preview_is_never_parsed_back(retriever):
    source = Path("src/cover_kbc/query_intelligence/retrieval_templates.py").read_text()
    assert "program_preview" not in source
    assert "fragments()" not in source


# --------------------------------------------------------------------------
# 9. Independence groups
# --------------------------------------------------------------------------


def test_distinct_families_carry_distinct_groups(retriever):
    query, program = _program("Subject", BORDERS)
    plan = retriever.plan(query, program)
    assert len(plan.independence_groups) == 3
    assert set(plan.independence_groups) == {
        ParametricIndependenceGroup.PSEUDO_MEMORY_SKETCH,
        ParametricIndependenceGroup.SELF_ASK_DECOMPOSITION,
        ParametricIndependenceGroup.QUERY_REWRITE,
    }


def test_resamples_share_one_independence_group():
    """Five seeds of one family are one structural source, not five."""
    retriever = ParametricRetriever(
        RetrievalConfig(enabled=True, operations=(RecallOperationKind.PSEUDO_MEMORY,),
                        samples_per_operation=3)
    )
    query, program = _program("Subject", BORDERS)
    plan = retriever.plan(query, program)

    assert plan.max_operations == 3
    assert len(plan.independence_groups) == 1
    assert {op.sample_index for op in plan.operations} == {0, 1, 2}
    assert len({op.operation_id for op in plan.operations}) == 3


def test_m11_groups_are_separate_from_the_core_evidence_groups():
    """Enrolling parametric recall into q(o) = g(o)/m(o) is not M11's call."""
    from cover_kbc.types import IndependenceGroup

    core = {group.value for group in IndependenceGroup}
    shadow = {group.value for group in ParametricIndependenceGroup}
    assert not (core & shadow)


# --------------------------------------------------------------------------
# 10. Pseudo-memory provenance
# --------------------------------------------------------------------------


def test_every_record_is_unverified_frozen_model_memory(retriever):
    query, program = _program("Subject", BORDERS)
    runtime = _scripted({"pseudo_memory#0": "A recalled sketch.",
                         "self_ask#0": "Q: x\nA: y", "query_rewrite#0": "Alpha"})
    result = retriever.retrieve(query, program, runtime)

    for record in result.records:
        assert record.source is MemorySource.FROZEN_MODEL_PARAMETRIC_MEMORY
        assert record.verified is False


def test_a_verified_record_cannot_be_constructed():
    kwargs = dict(
        operation_id="x", kind=RecallOperationKind.PSEUDO_MEMORY,
        independence_group=ParametricIndependenceGroup.PSEUDO_MEMORY_SKETCH,
        raw_output="text", parse_status=ParseStatus.OK,
        model_id="m", model_revision="r", decode_profile="d", prompt_sha256="h",
    )
    with pytest.raises(ValueError, match="never verifies"):
        ParametricMemoryRecord(**kwargs, verified=True)


def test_ok_status_is_a_shape_check_not_a_truth_claim(retriever):
    """A confidently wrong answer is still OK; M11 cannot tell the difference."""
    query, program = _program("Subject", BORDERS)
    runtime = _scripted({"query_rewrite#0": "Atlantis; Narnia"})
    result = retriever.retrieve(query, program, runtime)
    rewrite = next(r for r in result.records if r.kind is RecallOperationKind.QUERY_REWRITE)
    assert rewrite.parse_status is ParseStatus.OK
    assert rewrite.verified is False


def test_the_vocabulary_never_implies_an_external_source():
    for name in M11_MODULES:
        folded = _code_without_prose(name).casefold()
        for forbidden in ("retrieved_fact", "retrieved_document", "source_url", "document_id"):
            assert forbidden not in folded, f"{name} names {forbidden!r}"


# --------------------------------------------------------------------------
# 11-13. No evidence mutation; no M4; no specialist logic
# --------------------------------------------------------------------------


def test_running_m11_alone_mutates_no_evidence_graph():
    from cover_kbc.evidence.graph import build_graph

    query, program = _program("Testland", BORDERS)
    contract = CONTRACTS[BORDERS]
    graph = build_graph(query, contract)
    before = (len(graph.candidates), len(graph.records), len(graph._edge_ids))

    runtime = _scripted({"query_rewrite#0": "Alpha; Beta"}, subject="Testland")
    ParametricRetriever().retrieve(query, program, runtime)

    assert (len(graph.candidates), len(graph.records), len(graph._edge_ids)) == before


def test_m11_never_touches_the_evidence_graph_in_source():
    source = Path("src/cover_kbc/query_intelligence/parametric_retrieval.py").read_text()
    for forbidden in ("EvidenceGraph", "build_graph", "add_candidate", "Evidence("):
        assert forbidden not in source, f"M11 references {forbidden}"


def test_m11_uses_no_verifier_semantics():
    """M11 asks what the model recalls; M4 asks whether a candidate is valid."""
    root = Path("src/cover_kbc/query_intelligence")
    for name in M11_MODULES:
        source = (root / name).read_text(encoding="utf-8")
        for forbidden in ("VerificationLabel", "score_labels", "LABEL_TOKENS",
                          "VerifierTemplate", "A = VALID", "adversarial"):
            assert forbidden not in source, f"{name} references {forbidden}"


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


def test_m11_implements_no_specialist_logic():
    """M12-M21 responsibilities must be absent, not merely unused."""
    blob = " ".join(_code_without_prose(name) for name in M11_MODULES)
    for forbidden in (
        "cluster",          # M12 numeric clustering
        "convert_unit", "canonical_unit_value",
        "facet_plan", "closure",     # M13 / M15
        "existence_gate",            # M14
        "consensus",                 # M16
        "missingness_estimate",      # M19
        "schedule_budget", "allocate_budget",   # M20
        "expected_value",            # M21
    ):
        assert forbidden not in blob, f"M11 implements {forbidden}"


def test_module_2_is_untouched():
    from cover_kbc.elicitation.views import ENTITY_FORMAT, NUMERIC_FORMAT, SYSTEM_PROMPT

    assert SYSTEM_PROMPT.startswith("You answer knowledge-base completion questions")
    assert ENTITY_FORMAT.startswith("Output format: one line, items separated by semicolons")
    assert NUMERIC_FORMAT.startswith("Output format: a single number and its unit")
    source = Path("src/cover_kbc/query_intelligence/parametric_retrieval.py").read_text()
    for forbidden in ("ViewSpec", "views_for", "get_view", "ElicitationEngine"):
        assert forbidden not in source, f"M11 references {forbidden}"


def test_m11_uses_its_own_system_prompt_not_module_2s():
    from cover_kbc.elicitation.views import SYSTEM_PROMPT
    from cover_kbc.query_intelligence import RETRIEVAL_SYSTEM_PROMPT

    assert RETRIEVAL_SYSTEM_PROMPT != SYSTEM_PROMPT
    assert "no access to search" in RETRIEVAL_SYSTEM_PROMPT


# --------------------------------------------------------------------------
# 14-15. Call accounting; prompt hashing
# --------------------------------------------------------------------------


def test_each_operation_costs_exactly_one_call(retriever):
    query, program = _program("Subject", BORDERS)
    runtime = _scripted({"pseudo_memory#0": "a", "self_ask#0": "Q: x\nA: y",
                         "query_rewrite#0": "Alpha"})
    result = retriever.retrieve(query, program, runtime)

    assert [record.calls for record in result.records] == [1, 1, 1]
    assert result.total_calls == 3 == runtime.calls          # no phantom, no double charge
    assert result.total_generated_tokens == runtime.generated_tokens


def test_call_accounting_is_measured_not_assumed(retriever):
    """The delta comes off the runtime, so a record cannot over-claim."""

    class _SilentRuntime(ScriptedRuntime):
        def generate(self, request):        # never increments `calls`
            return GenerationResult(text="x", model_id=self.spec.model_id,
                                    generated_tokens=0, prompt_tokens=0)

    query, program = _program("Subject", BORDERS)
    result = retriever.retrieve(query, program, _SilentRuntime({}))
    assert result.total_calls == 0


def test_resampling_scales_cost_linearly_and_visibly():
    retriever = ParametricRetriever(
        RetrievalConfig(enabled=True, operations=(RecallOperationKind.SELF_ASK,),
                        samples_per_operation=3)
    )
    query, program = _program("Subject", BORDERS)
    runtime = _scripted()
    plan = retriever.plan(query, program)
    assert plan.estimated_calls == 3          # knowable before spending anything
    result = retriever.retrieve(query, program, runtime)
    assert result.total_calls == runtime.calls == 3


def test_prompt_hashes_and_operation_ids_are_stable(retriever):
    query, program = _program("Subject", AREA)
    first, second = retriever.plan(query, program), retriever.plan(query, program)
    assert [op.operation_id for op in first.operations] == [
        op.operation_id for op in second.operations
    ]
    assert [op.prompt_sha256 for op in first.operations] == [
        op.prompt_sha256 for op in second.operations
    ]
    # A different query yields different hashes; a plan cannot be confused.
    _, other = _program("Elsewhere", AREA)
    other_query, _ = compile_query("Elsewhere", AREA, 0)
    assert first.operations[0].prompt_sha256 != (
        retriever.plan(other_query, other).operations[0].prompt_sha256
    )


def test_the_record_carries_the_prompt_hash_that_ran(retriever):
    query, program = _program("Subject", BORDERS)
    runtime = _scripted({"query_rewrite#0": "Alpha"})
    plan = retriever.plan(query, program)
    result = retriever.retrieve(query, program, runtime)
    by_id = {op.operation_id: op for op in plan.operations}
    for record in result.records:
        assert record.prompt_sha256 == by_id[record.operation_id].prompt_sha256


def test_the_plan_records_upstream_versions(retriever):
    query, program = _program("Subject", BORDERS)
    plan = retriever.plan(query, program)
    assert plan.retrieval_version == RETRIEVAL_VERSION
    assert plan.compiler_version == program.compiler_version
    assert plan.profile_version == program.profile_version
    assert plan.program_sha256


# --------------------------------------------------------------------------
# 16. Serialization
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", ALL_RELATIONS)
def test_every_public_record_round_trips_through_json(retriever, relation):
    query, program = _program("Subject (qualified), 1999", relation, 7)
    runtime = _scripted({"pseudo_memory#0": "sketch", "self_ask#0": "Q: x\nA: y",
                         "query_rewrite#0": "Alpha"},
                        subject="Subject (qualified), 1999", relation=relation)
    result = retriever.retrieve(query, program, runtime)

    payload = json.loads(json.dumps(result.to_json()))
    assert ParametricRetrievalResult.from_json(payload) == result
    assert ParametricRetrievalPlan.from_json(payload["plan"]) == result.plan
    for original, entry in zip(result.records, payload["records"]):
        assert ParametricMemoryRecord.from_json(entry) == original


# --------------------------------------------------------------------------
# 18-19. Empty / malformed / failing responses
# --------------------------------------------------------------------------


def test_an_empty_response_is_recorded_as_empty(retriever):
    query, program = _program("Subject", BORDERS)
    result = retriever.retrieve(query, program, _scripted({"query_rewrite#0": "   "}))
    rewrite = next(r for r in result.records if r.kind is RecallOperationKind.QUERY_REWRITE)
    assert rewrite.parse_status is ParseStatus.EMPTY
    assert rewrite.raw_output == ""


def test_an_abstention_is_recorded_as_abstained(retriever):
    query, program = _program("Subject", BORDERS)
    result = retriever.retrieve(query, program, _scripted({
        "pseudo_memory#0": "NO RECOLLECTION", "query_rewrite#0": "NONE",
    }))
    by_kind = {record.kind: record for record in result.records}
    assert by_kind[RecallOperationKind.PSEUDO_MEMORY].parse_status is ParseStatus.ABSTAINED
    assert by_kind[RecallOperationKind.QUERY_REWRITE].parse_status is ParseStatus.ABSTAINED


def test_a_malformed_structured_response_is_recorded_as_malformed(retriever):
    query, program = _program("Subject", BORDERS)
    result = retriever.retrieve(query, program, _scripted({
        "self_ask#0": "I think the answer is probably Alpha.",
    }))
    self_ask = next(r for r in result.records if r.kind is RecallOperationKind.SELF_ASK)
    assert self_ask.parse_status is ParseStatus.MALFORMED
    # The text is kept: a later specialist may still find it useful.
    assert self_ask.raw_output.startswith("I think")


def test_a_runtime_failure_is_explicit_and_fabricates_nothing(retriever):
    class _BrokenRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            raise RuntimeError("the model fell over")

    query, program = _program("Subject", BORDERS)
    result = retriever.retrieve(query, program, _BrokenRuntime({}))

    assert len(result.records) == 3
    for record in result.records:
        assert record.parse_status is ParseStatus.RUNTIME_ERROR
        assert record.raw_output == ""            # nothing invented to fill the gap
        assert "the model fell over" in record.error
    assert len(result.errors) == 3
    assert result.usable_records == ()


def test_one_failing_probe_does_not_kill_the_others():
    class _FlakyRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            if request.metadata.get("view_id", "").startswith("self_ask"):
                raise RuntimeError("boom")
            return super().generate(request)

    query, program = _program("Subject", BORDERS)
    runtime = _FlakyRuntime(
        {("query_rewrite#0", "Subject", BORDERS): ["Alpha"]}
    )
    result = ParametricRetriever().retrieve(query, program, runtime)
    statuses = {r.kind: r.parse_status for r in result.records}
    assert statuses[RecallOperationKind.SELF_ASK] is ParseStatus.RUNTIME_ERROR
    assert statuses[RecallOperationKind.QUERY_REWRITE] is ParseStatus.OK
    assert len(result.errors) == 1


# --------------------------------------------------------------------------
# 20-21. Shadow isolation and disabled-path invariance
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


def _config(tmp_path: Path, base: str, *, m11: bool, tag: str) -> Path:
    import yaml

    config = yaml.safe_load(Path(base).read_text())
    config["query_intelligence"] = {
        "profiler": {"enabled": True, "mode": "shadow"},
        "prompt_compiler": {"enabled": True, "mode": "shadow"},
        "parametric_retrieval": {"enabled": m11, "mode": "shadow"},
    }
    path = tmp_path / f"config_{tag}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _run(cli, monkeypatch, config: Path, run_dir: Path, relation: str, limit: int = 4) -> None:
    monkeypatch.setattr(
        sys, "argv",
        ["run_staged.py", "all", "--config", str(config), "--split", "train",
         "--limit", str(limit), "--relation", relation, "--run-dir", str(run_dir)],
    )
    assert cli.main() == 0


@pytest.mark.parametrize("relation", [BORDERS, AWARD, CAPACITY])
def test_shadow_mode_changes_no_production_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m11=True, tag="on"), on, relation)
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m11=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in ARTEFACTS:
        assert (on / name).read_bytes() == (off / name).read_bytes(), name

    assert (on / "parametric_memory.jsonl").is_file()
    assert not (off / "parametric_memory.jsonl").exists()


def test_shadow_mode_changes_nothing_across_a_role_swap(cli, tmp_path, monkeypatch, capsys):
    on, off = tmp_path / "rs_on", tmp_path / "rs_off"
    _run(cli, monkeypatch, _config(tmp_path, ROLESWAP, m11=True, tag="rs_on"), on, AWARD, 3)
    _run(cli, monkeypatch, _config(tmp_path, ROLESWAP, m11=False, tag="rs_off"), off, AWARD, 3)
    capsys.readouterr()

    for name in (*ARTEFACTS, "stage_r1_enumerator.jsonl"):
        assert (on / name).read_bytes() == (off / name).read_bytes(), name


def test_production_call_accounting_is_unchanged_by_shadow_calls(
    cli, tmp_path, monkeypatch, capsys
):
    """M11 spends real calls; production diagnostics must stay comparable."""
    on, off = tmp_path / "on", tmp_path / "off"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m11=True, tag="on"), on, AWARD)
    out = capsys.readouterr().out
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m11=False, tag="off"), off, AWARD)
    capsys.readouterr()

    assert json.loads((on / "diagnostics.json").read_text()) == json.loads(
        (off / "diagnostics.json").read_text()
    )
    # The shadow spend is reported honestly rather than hidden.
    assert "[M11] parametric memory" in out
    assert "shadow calls" in out


def test_shadow_calls_never_enter_the_controller_budget():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        NullRuntime(model_id="offline/null"), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
    )
    graph = pipeline.enumerate_query(Query("Testland", BORDERS, 0))

    assert pipeline.shadow_calls == 3                       # M11 really ran
    assert len(pipeline.retrieval_results) == 1

    # The controller's per-query spend counts production views only. Compared
    # against the identical run without a retriever, it is unchanged - so the
    # three shadow calls are outside the budget, not merely small.
    baseline = CoverPipeline(
        NullRuntime(model_id="offline/null"), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
    )
    baseline_graph = baseline.enumerate_query(Query("Testland", BORDERS, 0))
    assert graph.budget_snapshot == baseline_graph.budget_snapshot
    assert graph.budget_snapshot["calls_used"] < pipeline.shadow_calls + 1
    assert baseline.shadow_calls == 0


def test_pipeline_without_a_retriever_is_the_pre_m11_path():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        NullRuntime(model_id="offline/null"), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
    )
    assert pipeline.retriever is None
    pipeline.enumerate_query(Query("Testland", BORDERS, 0))
    assert pipeline.retrieval_results == [] and pipeline.shadow_calls == 0


def test_m11_records_never_reach_the_evidence_graph():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        NullRuntime(model_id="offline/null"), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
    )
    graph = pipeline.enumerate_query(Query("Testland", BORDERS, 0))
    blob = json.dumps(
        {k: str(v) for k, v in vars(graph).items() if not k.startswith("_")}
    ).casefold()
    for leaked in ("parametric", "pseudo_memory", "self_ask", "prompt_sha256"):
        assert leaked not in blob, leaked


# --------------------------------------------------------------------------
# 17. Persistence
# --------------------------------------------------------------------------


def test_the_artefact_is_manifest_ordered_and_complete(cli, tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "persist"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m11=True, tag="on"), run_dir, STOCK)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "parametric_memory.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]

    assert len(rows) == len(manifest) * 3          # three probes per query
    seen = [(r["SubjectEntity"], r["Relation"], r["row_index"]) for r in rows[::3]]
    assert seen == [(q["SubjectEntity"], q["Relation"], q["row_index"]) for q in manifest]


def test_each_persisted_row_carries_full_provenance(cli, tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "provenance"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m11=True, tag="on"), run_dir, AREA)
    capsys.readouterr()

    for line in (run_dir / "parametric_memory.jsonl").read_text().splitlines():
        row = json.loads(line)
        for key in (
            "retrieval_version", "compiler_version", "profile_version", "program_sha256",
            "SubjectEntity", "Relation", "row_index", "program_type",
            "operation_id", "kind", "independence_group", "prompt_sha256",
            "model_id", "model_revision", "decode_profile", "parse_status",
            "raw_output", "calls", "generated_tokens", "source", "verified",
        ):
            assert key in row, key
        assert row["source"] == "FROZEN_MODEL_PARAMETRIC_MEMORY"
        assert row["verified"] is False
        # Nothing external, nothing gold.
        for forbidden in ("url", "document_id", "gold", "ObjectEntities", "label"):
            assert forbidden not in row, forbidden


# --------------------------------------------------------------------------
# 22. Configuration validation
# --------------------------------------------------------------------------


def test_unknown_config_keys_are_rejected():
    with pytest.raises(ValueError, match="unknown query_intelligence.parametric_retrieval key"):
        RetrievalConfig.from_mapping({"enabled": True, "operationz": []})


def test_an_unsupported_mode_is_rejected():
    with pytest.raises(ValueError, match="unsupported parametric retrieval mode"):
        ParametricRetriever(RetrievalConfig(enabled=True, mode="production"))


def test_duplicate_operations_are_rejected():
    with pytest.raises(ValueError, match="duplicate parametric retrieval operation"):
        RetrievalConfig.from_mapping(
            {"enabled": True, "operations": ["self_ask", "self_ask"]}
        )


def test_a_non_list_operations_value_is_rejected():
    with pytest.raises(ValueError, match="must be a list"):
        RetrievalConfig.from_mapping({"enabled": True, "operations": "self_ask"})


def test_an_empty_operation_set_is_rejected():
    with pytest.raises(ValueError, match="at least one parametric retrieval operation"):
        ParametricRetriever(RetrievalConfig(enabled=True, operations=()))


def test_a_non_positive_sample_count_is_rejected():
    with pytest.raises(ValueError, match="samples_per_operation"):
        RetrievalConfig.from_mapping({"enabled": True, "samples_per_operation": 0})


def test_disabled_or_absent_config_builds_no_retriever():
    assert build_parametric_retriever(None, profiler_enabled=True, compiler_enabled=True) is None
    assert build_parametric_retriever({}, profiler_enabled=True, compiler_enabled=True) is None
    assert build_parametric_retriever(
        {"parametric_retrieval": {"enabled": False}},
        profiler_enabled=False, compiler_enabled=False,
    ) is None
    assert isinstance(
        build_parametric_retriever(
            {"parametric_retrieval": {"enabled": True}},
            profiler_enabled=True, compiler_enabled=True,
        ),
        ParametricRetriever,
    )


def test_the_shipped_configs_keep_m11_disabled_by_default():
    """M11 costs real calls, so it must be opted into, never inherited."""
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        config = yaml.safe_load(Path(name).read_text())
        block = config["query_intelligence"]["parametric_retrieval"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name


def test_operations_are_config_driven_and_explicit():
    config = RetrievalConfig.from_mapping(
        {"enabled": True, "operations": ["pseudo_memory", "query_rewrite"]}
    )
    retriever = ParametricRetriever(config)
    query, program = _program("Subject", BORDERS)
    plan = retriever.plan(query, program)
    assert [op.kind for op in plan.operations] == [
        RecallOperationKind.PSEUDO_MEMORY, RecallOperationKind.QUERY_REWRITE
    ]
    assert plan.estimated_calls == 2


# --------------------------------------------------------------------------
# 23. Model budget
# --------------------------------------------------------------------------


def test_m11_introduces_no_new_parameters(tmp_path):
    """A clean interpreter: M11 loads no weights and declares no model."""
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.query_intelligence.parametric_retrieval import ParametricRetriever\n"
        "ParametricRetriever()\n"
        "loaded = sorted(m for m in sys.modules if m in "
        "('torch', 'transformers', 'mistral_common'))\n"
        "print(','.join(loaded))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == ""
