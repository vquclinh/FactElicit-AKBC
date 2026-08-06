"""Module 10 - Prompt Program Compiler conformance.

Four things have to hold:

* compilation is deterministic, closed-book and free;
* Modules 0, 1 and 9 stay authoritative - M10 consumes them and can override
  none of them;
* Modules 2 and 4 keep their prompts, byte for byte;
* enabling M10 changes nothing about what the system predicts.

The last two are load-bearing and are tested the same way Audit 0016 tested M9:
the real staged CLI is run twice over the scripted backend, once with M10 on and
once with it off, and every prediction artefact is compared byte for byte.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS, UnknownRelationError
from cover_kbc.contracts.router import PROGRAM_BY_RELATION, compile_query
from cover_kbc.query_intelligence import (
    COMPILER_VERSION,
    DIRECTIVE_RULES,
    PROFILE_VERSION,
    RELATION_PROMPT_SPECS,
    RISK_AXES,
    SUBJECT_DIRECTIVE_RULES,
    DirectiveKind,
    NumericKind,
    ObjectKind,
    ProfilerConfig,
    PromptCompilerConfig,
    PromptProgram,
    PromptProgramCompiler,
    QueryProfiler,
    RiskLevel,
    SubjectDirectiveKind,
    UnknownRelationPromptError,
    build_prompt_compiler,
    check_prompt_registry_consistency,
    get_prompt_spec,
    program_preview,
)
from cover_kbc.query_intelligence.types import CardinalityRegime
from cover_kbc.types import ProgramType, Query

BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
AWARD = "awardWonBy"
ALL_RELATIONS = (BORDERS, STOCK, DEATH, CAPACITY, AREA, AWARD)

CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
ROLESWAP = "configs/experiments/smoke_staged_roleswap.yaml"
ARTEFACTS = (
    "predictions.jsonl",
    "diagnostics.json",
    "trace.jsonl",
    "stage_a_enumerated.jsonl",
    "stage_b_verified.jsonl",
    "query_profiles.jsonl",       # M9's artefact must not change either
)


@pytest.fixture
def compiler():
    return PromptProgramCompiler()


@pytest.fixture
def profiler():
    return QueryProfiler()


def _compile(compiler, profiler, subject: str, relation: str, row_index: int = 0) -> PromptProgram:
    query, contract = compile_query(subject, relation, row_index)
    profile = profiler.profile(query, contract)
    return compiler.compile(query, contract, profile)


# --------------------------------------------------------------------------
# 1. Determinism
# --------------------------------------------------------------------------


def test_the_same_inputs_compile_identically(compiler, profiler):
    first = _compile(compiler, profiler, "Testland", BORDERS)
    second = _compile(compiler, profiler, "Testland", BORDERS)
    assert first == second
    assert first.to_json() == second.to_json()


def test_determinism_holds_across_compiler_instances():
    profiler = QueryProfiler()
    a = _compile(PromptProgramCompiler(), profiler, "Testcorp", STOCK)
    b = _compile(PromptProgramCompiler(), profiler, "Testcorp", STOCK)
    assert a == b


def test_a_program_is_hashable_by_value(compiler, profiler):
    a = _compile(compiler, profiler, "Testland", BORDERS)
    b = _compile(compiler, profiler, "Testland", BORDERS)
    assert len({a, b}) == 1


def test_rendering_is_deterministic_and_reconstructible(compiler, profiler):
    program = _compile(compiler, profiler, "Testprize", AWARD)
    assert program.fragments() == program.fragments()
    assert program_preview(program) == program_preview(program)
    # Structure is the source: the rendered text is built from the fields, so
    # every constraint appears in it rather than the other way round.
    rendered = program_preview(program)
    for rule in program.negative_constraints:
        assert rule in rendered
    for cue in program.semantic_cues:
        assert cue in rendered


# --------------------------------------------------------------------------
# 2. Zero neural cost
# --------------------------------------------------------------------------


def test_compiling_loads_no_model_backend_at_all(tmp_path):
    """Importing and running M10 must not pull in a runtime or a network client."""
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.query_intelligence import QueryProfiler, PromptProgramCompiler\n"
        "from cover_kbc.contracts.router import compile_query\n"
        "profiler, compiler = QueryProfiler(), PromptProgramCompiler()\n"
        + "".join(
            f"q, c = compile_query('S', {r!r}, 0); "
            "compiler.compile(q, c, profiler.profile(q, c))\n"
            for r in ALL_RELATIONS
        )
        + "loaded = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m in ('torch', 'transformers', 'mistral_common', 'requests',\n"
        "             'urllib.request', 'http.client', 'socket')\n"
        "    or m.startswith('cover_kbc.models')\n"
        ")\n"
        "print(','.join(loaded))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == "", f"M10 loaded {result.stdout.strip()}"


def test_compiling_spends_no_calls_or_tokens(compiler, profiler):
    from cover_kbc.models.offline import ScriptedRuntime

    runtime = ScriptedRuntime({}, model_id="offline/scripted")
    for relation in ALL_RELATIONS:
        _compile(compiler, profiler, "Subject", relation)
    assert (runtime.calls, runtime.generated_tokens) == (0, 0)


def test_the_m10_sources_reference_no_backend_or_network():
    root = Path("src/cover_kbc/query_intelligence")
    for name in ("prompt_types.py", "prompt_registry.py", "prompt_compiler.py"):
        source = (root / name).read_text(encoding="utf-8")
        for forbidden in ("models.registry", "models.huggingface", "LMRuntime", "requests."):
            assert forbidden not in source, f"{name} references {forbidden}"


# --------------------------------------------------------------------------
# 3-4. Six-relation coverage; Module 1 consistency
# --------------------------------------------------------------------------


def test_every_official_relation_compiles(compiler, profiler):
    for relation in ALL_RELATIONS:
        program = _compile(compiler, profiler, "Subject", relation)
        assert program.relation == relation
        assert program.compiler_version == COMPILER_VERSION
        assert program.profile_version == PROFILE_VERSION
        assert program.positive_constraints and program.negative_constraints
        assert program.semantic_cues and program.negative_anchors


def test_prompt_specs_cover_exactly_the_contracted_relations():
    check_prompt_registry_consistency()
    assert set(RELATION_PROMPT_SPECS) == set(CONTRACTS)


def test_program_type_always_agrees_with_module_1(compiler, profiler):
    for relation in ALL_RELATIONS:
        program = _compile(compiler, profiler, "Subject", relation)
        assert program.program_type is PROGRAM_BY_RELATION[relation]
        assert program.program_type is CONTRACTS[relation].program_type


# --------------------------------------------------------------------------
# 5. Module 9 consistency
# --------------------------------------------------------------------------


def test_a_profile_is_required(compiler):
    query, contract = compile_query("Subject", BORDERS, 0)
    with pytest.raises(ValueError, match="requires a Module 9 QueryRiskProfile"):
        compiler.compile(query, contract)


def test_a_profile_for_a_different_query_is_rejected(compiler, profiler):
    query, contract = compile_query("Subject", BORDERS, 0)
    other_query, other_contract = compile_query("Other", BORDERS, 5)
    other_profile = profiler.profile(other_query, other_contract)

    with pytest.raises(ValueError, match="profile subject"):
        compiler.compile(query, contract, other_profile)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("relation", AWARD, "profile is for"),
        ("row_index", 99, "row_index"),
        ("program_type", ProgramType.NUMERIC, "profile programme"),
        ("cardinality_regime", CardinalityRegime.NUMERIC_SINGLE, "cardinality regime"),
    ],
)
def test_every_disagreement_between_m9_and_m1_fails_loudly(
    compiler, profiler, field, value, message
):
    from dataclasses import replace

    query, contract = compile_query("Subject", BORDERS, 0)
    broken = replace(profiler.profile(query, contract), **{field: value})
    with pytest.raises(ValueError, match=message):
        compiler.compile(query, contract, broken)


def test_a_mismatched_contract_is_rejected(compiler, profiler):
    query, contract = compile_query("Subject", CAPACITY, 0)
    profile = profiler.profile(query, contract)
    with pytest.raises(ValueError, match="contract is for"):
        compiler.compile(query, CONTRACTS[AWARD], profile)


def test_the_compiler_never_builds_its_own_profiler():
    """The stack is M1 -> M9 -> M10, not M1 -> M10(reimplements M9)."""
    source = Path("src/cover_kbc/query_intelligence/prompt_compiler.py").read_text()
    assert "QueryProfiler" not in source
    assert "subject_surface_features" not in source


def test_profile_version_is_recorded_on_every_program(compiler):
    """A program must not be pairable with a different risk vocabulary."""
    profiler = QueryProfiler(ProfilerConfig(enabled=True, profile_version="m9-experimental"))
    query, contract = compile_query("Subject", BORDERS, 0)
    program = compiler.compile(query, contract, profiler.profile(query, contract))
    assert program.profile_version == "m9-experimental"


# --------------------------------------------------------------------------
# 6. Contract authority
# --------------------------------------------------------------------------


def test_task_semantics_are_copied_from_the_contract_verbatim(compiler, profiler):
    for relation in ALL_RELATIONS:
        contract = CONTRACTS[relation]
        program = _compile(compiler, profiler, "Subject", relation)
        assert program.task_semantics.definition == contract.definition
        assert program.task_semantics.answer_type == contract.answer_type
        assert program.positive_constraints == tuple(contract.positive_rules)
        assert program.negative_constraints == tuple(contract.hard_negative_rules)


def test_the_registry_declares_no_second_relation_definition():
    """Prompt language may add phrasing; it may not restate the contract."""
    source = Path("src/cover_kbc/query_intelligence/prompt_registry.py").read_text()
    for contract in CONTRACTS.values():
        assert contract.definition not in source, contract.relation
        for rule in (*contract.positive_rules, *contract.hard_negative_rules):
            assert rule not in source, f"{contract.relation}: {rule!r} is restated"


def test_answer_schema_follows_the_contract_not_the_registry(compiler, profiler):
    for relation in ALL_RELATIONS:
        contract = CONTRACTS[relation]
        schema = _compile(compiler, profiler, "Subject", relation).answer_schema
        assert schema.allow_empty is contract.allows_empty
        assert schema.max_objects == contract.max_objects
        assert schema.canonical_unit == (
            contract.selection.numeric_target_unit if contract.is_numeric else None
        )


# --------------------------------------------------------------------------
# 7. Typed answer schemas
# --------------------------------------------------------------------------


def test_numeric_relations_get_a_numeric_schema(compiler, profiler):
    capacity = _compile(compiler, profiler, "Stadium", CAPACITY).answer_schema
    assert capacity.object_kind is ObjectKind.NUMBER
    assert capacity.numeric_kind is NumericKind.INTEGER      # a person count
    assert capacity.canonical_unit == "persons"
    assert capacity.allow_empty is False and capacity.max_objects == 1
    assert "whole number" in capacity.output_instruction
    assert "persons" in capacity.output_instruction

    area = _compile(compiler, profiler, "Testisland", AREA).answer_schema
    assert area.object_kind is ObjectKind.NUMBER
    assert area.numeric_kind is NumericKind.REAL             # areas are not integral
    assert area.canonical_unit == "km2"
    assert "km2" in area.output_instruction


def test_null_single_gets_a_single_optional_entity_schema(compiler, profiler):
    schema = _compile(compiler, profiler, "Testperson", DEATH).answer_schema
    assert schema.object_kind is ObjectKind.ENTITY
    assert schema.cardinality is CardinalityRegime.ZERO_OR_ONE
    assert schema.numeric_kind is NumericKind.NOT_NUMERIC
    assert schema.max_objects == 1 and schema.allow_empty is True
    assert "exactly one name" in schema.output_instruction
    assert schema.empty_token in schema.output_instruction


def test_set_relations_get_a_list_schema(compiler, profiler):
    for relation, regime in ((BORDERS, CardinalityRegime.SMALL_SET),
                             (AWARD, CardinalityRegime.LARGE_OPEN_SET)):
        schema = _compile(compiler, profiler, "Subject", relation).answer_schema
        assert schema.object_kind is ObjectKind.ENTITY
        assert schema.cardinality is regime
        assert schema.max_objects == 0                    # unbounded by the regime
        assert "semicolons" in schema.output_instruction


def test_empty_tokens_agree_with_the_module_2_format_strings():
    """Two spellings of "nothing" would be a parser bug, so agreement is checked."""
    from cover_kbc.elicitation.views import ENTITY_FORMAT, NUMERIC_FORMAT
    from cover_kbc.query_intelligence.prompt_compiler import (
        EMPTY_ENTITY_TOKEN,
        EMPTY_NUMERIC_TOKEN,
    )

    assert EMPTY_ENTITY_TOKEN in ENTITY_FORMAT
    assert EMPTY_NUMERIC_TOKEN in NUMERIC_FORMAT


def test_output_contract_is_a_projection_of_the_schema(compiler, profiler):
    program = _compile(compiler, profiler, "Stadium", CAPACITY)
    assert program.output_contract == program.answer_schema.output_instruction


# --------------------------------------------------------------------------
# 8. Semantic cues and negative anchors
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relation,cue,anchor",
    [
        (BORDERS, "shares a land border with", "maritime border"),
        (DEATH, "died in", "born in"),
        (CAPACITY, "spectator capacity", "record attendance"),
        (AWARD, "recipient of the award", "nominee"),
        (STOCK, "publicly listed on", "stock market index"),
        (AREA, "total area", "population"),
    ],
)
def test_each_relation_gets_its_cue_family_and_anchors(
    compiler, profiler, relation, cue, anchor
):
    program = _compile(compiler, profiler, "Subject", relation)
    assert cue in program.semantic_cues
    assert anchor in program.negative_anchors
    assert program.keyword_bundle == (*program.semantic_cues, *program.negative_anchors)


def test_area_cues_carry_the_alternate_units_the_contract_accepts(compiler, profiler):
    cues = " ".join(_compile(compiler, profiler, "Testisland", AREA).semantic_cues)
    for unit in ("square kilometres", "square miles", "hectares"):
        assert unit in cues


def test_capacity_cues_keep_the_configuration_ambiguity_open(compiler, profiler):
    """M10 must not silently pick one factual reading; M12 will resolve it."""
    cues = _compile(compiler, profiler, "Stadium", CAPACITY).semantic_cues
    assert any("seating" in c for c in cues)
    assert any("total" in c for c in cues)
    assert any("maximum" in c for c in cues)


def test_cues_and_anchors_are_disjoint_for_every_relation():
    for relation in ALL_RELATIONS:
        spec = get_prompt_spec(relation)
        cues = {c.casefold() for c in spec.semantic_cues}
        anchors = {a.casefold() for a in spec.negative_anchors}
        assert not (cues & anchors), relation


def test_every_prompt_spec_states_a_rationale():
    for relation in ALL_RELATIONS:
        assert get_prompt_spec(relation).rationale, relation


def test_cues_are_lexical_steering_not_retrieval_queries():
    """Cues are English phrasings, not requests to any external system.

    Scans the *declared strings*, not the module prose - the file's own
    docstring names the prohibited systems in order to forbid them.
    """
    for relation in ALL_RELATIONS:
        spec = get_prompt_spec(relation)
        declared = (
            *spec.semantic_cues, *spec.negative_anchors, *spec.abstraction_cues,
            spec.relation_focus, spec.semantic_question,
        )
        for value in declared:
            folded = value.casefold()
            for forbidden in (
                "http", "www.", ".com", ".org", "wikipedia", "wikidata",
                "search for", "look up", "query the", "api", "database",
            ):
                assert forbidden not in folded, f"{relation}: {value!r} looks like retrieval"


def test_the_m10_package_imports_nothing_network_capable():
    """Structural check on the import list, independent of any prose."""
    import ast

    root = Path("src/cover_kbc/query_intelligence")
    banned = {"requests", "httpx", "urllib", "socket", "http", "aiohttp", "torch", "transformers"}
    for name in ("prompt_types.py", "prompt_registry.py", "prompt_compiler.py"):
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


# --------------------------------------------------------------------------
# 9. Risk-conditioned directives
# --------------------------------------------------------------------------


def test_high_near_miss_compiles_an_exclusion_directive(compiler, profiler):
    program = _compile(compiler, profiler, "Testland", BORDERS)
    directive = program.directive(DirectiveKind.EXCLUSION)
    assert directive is not None
    assert directive.axis == "near_miss_risk" and directive.level is RiskLevel.HIGH


def test_high_format_sensitivity_compiles_a_strict_output_directive(compiler, profiler):
    for relation in (CAPACITY, AREA):
        program = _compile(compiler, profiler, "Subject", relation)
        assert program.has_directive(DirectiveKind.STRICT_FORMAT)
    # Borders is not format-sensitive and gets no such directive.
    borders = _compile(compiler, profiler, "Testland", BORDERS)
    assert not borders.has_directive(DirectiveKind.STRICT_FORMAT)


def test_high_nullability_compiles_explicit_permission_to_answer_nothing(compiler, profiler):
    for relation in (DEATH, STOCK):
        directive = _compile(compiler, profiler, "Subject", relation).directive(
            DirectiveKind.EMPTY_PERMITTED
        )
        assert directive is not None
        assert "empty" in directive.instruction.casefold()
    # A regime that forbids an empty answer must never be told empty is fine.
    assert not _compile(compiler, profiler, "Stadium", CAPACITY).has_directive(
        DirectiveKind.EMPTY_PERMITTED
    )


def test_high_open_set_and_missingness_compile_completeness_language(compiler, profiler):
    award = _compile(compiler, profiler, "Testprize", AWARD)
    assert award.has_directive(DirectiveKind.RECALL_BREADTH)
    assert award.has_directive(DirectiveKind.COMPLETENESS)
    assert not _compile(compiler, profiler, "Stadium", CAPACITY).has_directive(
        DirectiveKind.RECALL_BREADTH
    )


def test_high_temporal_sensitivity_compiles_temporal_language(compiler, profiler):
    for relation in (DEATH, STOCK):
        assert _compile(compiler, profiler, "Subject", relation).has_directive(
            DirectiveKind.TEMPORAL
        )
    assert not _compile(compiler, profiler, "Testisland", AREA).has_directive(
        DirectiveKind.TEMPORAL
    )


def test_high_identity_ambiguity_compiles_a_disambiguation_directive(compiler, profiler):
    assert _compile(compiler, profiler, "Testcorp", STOCK).has_directive(DirectiveKind.IDENTITY)
    assert not _compile(compiler, profiler, "Testland", BORDERS).has_directive(
        DirectiveKind.IDENTITY
    )


def test_a_directive_fires_exactly_when_its_axis_is_high(compiler, profiler):
    for relation in ALL_RELATIONS:
        query, contract = compile_query("Subject", relation, 0)
        profile = profiler.profile(query, contract)
        program = compiler.compile(query, contract, profile)
        fired = {entry.axis for entry in program.risk_directives}
        expected = {
            rule.axis for rule in DIRECTIVE_RULES if profile.axis(rule.axis) >= rule.trigger
        }
        assert fired == expected, relation
        for entry in program.risk_directives:
            assert entry.level is profile.axis(entry.axis)


def test_directives_are_ordered_by_the_m9_axis_order(compiler, profiler):
    order = {axis: index for index, axis in enumerate(RISK_AXES)}
    for relation in ALL_RELATIONS:
        program = _compile(compiler, profiler, "Subject", relation)
        positions = [order[entry.axis] for entry in program.risk_directives]
        assert positions == sorted(positions), relation


def test_directives_never_name_an_action_a_budget_or_a_stop():
    """Language only. Actions, budgets and stopping belong to M19-M21.

    Whole words only: "recall" is legitimate prompt language and must not be
    caught by a substring match on "call".
    """
    import re

    forbidden = (
        "budget", "budgets", "call", "calls", "token", "tokens",
        "action", "actions", "facet", "facets", "view", "views",
        "sample", "samples", "retry", "retries",
    )
    pattern = re.compile(r"\b(" + "|".join(forbidden) + r")\b")
    for rule in DIRECTIVE_RULES:
        found = pattern.search(rule.instruction.casefold())
        assert found is None, f"{rule.axis}: names {found.group(0)!r}, which is M19-M21's"


# --------------------------------------------------------------------------
# 10-11. No factual leakage; subject preservation
# --------------------------------------------------------------------------


def test_compiled_programs_contain_no_candidate_or_factual_object(compiler, profiler):
    payload = _compile(compiler, profiler, "Estadio X in Madrid", CAPACITY).to_json()
    assert set(payload) == {
        "compiler_version", "profile_version", "SubjectEntity", "Relation", "row_index",
        "program_type", "cardinality_regime", "specialist_hint", "task_semantics",
        "answer_schema", "positive_constraints", "negative_constraints",
        "semantic_cues", "negative_anchors", "risk_directives", "subject_directives",
        "query_specification",
    }
    flat = json.dumps(payload).casefold()
    for leaked in ("objectentities", "candidate_value", "answer_value", "gold"):
        assert leaked not in flat, leaked


def test_two_subjects_of_one_relation_differ_only_in_subject_material(compiler, profiler):
    a = _compile(compiler, profiler, "France", BORDERS)
    b = _compile(compiler, profiler, "Vatican City (enclave)", BORDERS)
    assert a.semantic_cues == b.semantic_cues
    assert a.negative_constraints == b.negative_constraints
    assert a.risk_directives == b.risk_directives
    assert a.subject != b.subject
    assert a.subject_directives != b.subject_directives


@pytest.mark.parametrize(
    "subject,kind",
    [
        ("Mercury (planet)", SubjectDirectiveKind.PRESERVE_PARENTHETICAL),
        ("Springfield, Illinois", SubjectDirectiveKind.PRESERVE_COMMA_QUALIFIER),
        ("Estadio X in Madrid", SubjectDirectiveKind.PRESERVE_PREPOSITIONAL_QUALIFIER),
        ("Köln", SubjectDirectiveKind.PRESERVE_UNICODE),
        ("Boeing 747", SubjectDirectiveKind.PRESERVE_DIGITS),
    ],
)
def test_surface_features_compile_preservation_directives(compiler, profiler, subject, kind):
    program = _compile(compiler, profiler, subject, BORDERS)
    kinds = {entry.kind for entry in program.subject_directives}
    assert kind in kinds
    # The unconditional verbatim rule is always present.
    assert SubjectDirectiveKind.PRESERVE_VERBATIM in kinds


def test_a_plain_subject_gets_only_the_verbatim_directive(compiler, profiler):
    program = _compile(compiler, profiler, "Testland", BORDERS)
    assert [entry.kind for entry in program.subject_directives] == [
        SubjectDirectiveKind.PRESERVE_VERBATIM
    ]


@pytest.mark.parametrize(
    "subject",
    ["Köln", "東京", "Mercury (planet)", "Springfield, Illinois", "Boeing 747", "St. Mary's"],
)
def test_the_subject_survives_compilation_and_serialisation_losslessly(
    compiler, profiler, subject
):
    program = _compile(compiler, profiler, subject, BORDERS)
    assert program.subject == subject
    assert PromptProgram.from_json(json.loads(json.dumps(program.to_json()))).subject == subject


def test_subject_directives_interpret_nothing(compiler, profiler):
    """A qualifier is preserved, never resolved or explained."""
    program = _compile(compiler, profiler, "Estadio X in Madrid", CAPACITY)
    text = " ".join(entry.instruction for entry in program.subject_directives).casefold()
    for leaked in ("madrid", "spain", "stadium in", "means", "refers to"):
        assert leaked not in text, leaked


# --------------------------------------------------------------------------
# 12. Relation-specific declarations are centralised
# --------------------------------------------------------------------------


def test_no_relation_name_is_branched_on_outside_the_registry():
    root = Path("src/cover_kbc/query_intelligence")
    for name in ("prompt_types.py", "prompt_compiler.py"):
        source = (root / name).read_text(encoding="utf-8")
        for relation in ALL_RELATIONS:
            assert relation not in source, f"{name} branches on {relation}"


def test_the_step_back_layer_is_contract_derived_and_factual_free(compiler, profiler):
    for relation in ALL_RELATIONS:
        spec = _compile(compiler, profiler, "Subject", relation).query_specification
        assert spec.semantic_question and spec.abstraction_cues
        # The step-back question is about the relation, never about the subject.
        assert "Subject" not in spec.semantic_question


# --------------------------------------------------------------------------
# 13. Serialisation round-trip
# --------------------------------------------------------------------------


def test_program_round_trips_through_json(compiler, profiler):
    for relation in ALL_RELATIONS:
        original = _compile(compiler, profiler, "Subject (qualified), 1999", relation, 7)
        payload = json.loads(json.dumps(original.to_json()))
        assert PromptProgram.from_json(payload) == original


# --------------------------------------------------------------------------
# 14-15. Module 2 and Module 4 ownership
# --------------------------------------------------------------------------


def test_module_2_still_owns_which_view_runs():
    """M10 says how to talk about a relation, never what to run."""
    source = Path("src/cover_kbc/query_intelligence/prompt_compiler.py").read_text()
    for forbidden in ("ViewSpec", "views_for", "get_view", "ViewFamily", "elicitation"):
        assert forbidden not in source, f"M10 references {forbidden}"


def test_the_program_carries_no_view_plan(compiler, profiler):
    payload = _compile(compiler, profiler, "Testprize", AWARD).to_json()
    flat = json.dumps(payload).casefold()
    for forbidden in ("view_id", "facet_id", "independence_group", "decode", "template"):
        assert forbidden not in flat, forbidden


#: sha256 of Module 4's entire prompt surface at the M10 milestone. The blind
#: verifier is frozen and audited; changing any of it must be a deliberate act
#: that updates this constant, never a side effect of a prompt-compiler edit.
M4_PROMPT_SURFACE_SHA256 = (
    "3acd7109fd22cf37b9b0c1c8a3ab63e4a4a1b65875eab02888e3fbc491e6d874"
)


def test_module_4_verifier_prompts_are_byte_identical():
    """The blind verifier is frozen; M10 may not reach it."""
    import hashlib

    from cover_kbc.verification import (
        GATE_TEMPLATE,
        LABEL_TOKENS,
        TEMPLATES,
        VERIFIER_SYSTEM_PROMPT,
    )

    blob = (
        VERIFIER_SYSTEM_PROMPT + "\n" + GATE_TEMPLATE + "\n"
        + repr(sorted(LABEL_TOKENS.items()))
    )
    for template in TEMPLATES:
        blob += "\n" + template.template_id + "\n" + template.body
    assert hashlib.sha256(blob.encode()).hexdigest() == M4_PROMPT_SURFACE_SHA256


def test_module_10_cannot_reach_the_verifier():
    source = Path("src/cover_kbc/query_intelligence/prompt_compiler.py").read_text()
    assert "verification" not in source
    assert "VerifierTemplate" not in source


def test_module_2_format_strings_are_untouched():
    from cover_kbc.elicitation.views import ENTITY_FORMAT, NUMERIC_FORMAT, SYSTEM_PROMPT

    assert SYSTEM_PROMPT.startswith("You answer knowledge-base completion questions")
    assert ENTITY_FORMAT.startswith("Output format: one line, items separated by semicolons")
    assert NUMERIC_FORMAT.startswith("Output format: a single number and its unit")


# --------------------------------------------------------------------------
# 16-17. Shadow invariance and persistence
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


def _config(tmp_path: Path, base: str, *, m10: bool, tag: str) -> Path:
    import yaml

    config = yaml.safe_load(Path(base).read_text())
    config["query_intelligence"] = {
        "profiler": {"enabled": True, "mode": "shadow"},
        "prompt_compiler": {"enabled": m10, "mode": "shadow"},
    }
    path = tmp_path / f"config_{tag}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _run(cli, monkeypatch, config: Path, run_dir: Path, relation: str, limit: int = 4) -> None:
    monkeypatch.setattr(
        sys, "argv",
        [
            "run_staged.py", "all",
            "--config", str(config),
            "--split", "train",
            "--limit", str(limit),
            "--relation", relation,
            "--run-dir", str(run_dir),
        ],
    )
    assert cli.main() == 0


@pytest.mark.parametrize("relation", [BORDERS, AWARD, CAPACITY])
def test_shadow_mode_changes_no_prediction_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m10=True, tag="on"), on, relation)
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m10=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in ARTEFACTS:
        assert (on / name).read_bytes() == (off / name).read_bytes(), name

    assert (on / "prompt_programs.jsonl").is_file()
    assert not (off / "prompt_programs.jsonl").exists()


def test_shadow_mode_changes_nothing_across_a_role_swap(cli, tmp_path, monkeypatch, capsys):
    on, off = tmp_path / "rs_on", tmp_path / "rs_off"
    _run(cli, monkeypatch, _config(tmp_path, ROLESWAP, m10=True, tag="rs_on"), on, AWARD, 3)
    _run(cli, monkeypatch, _config(tmp_path, ROLESWAP, m10=False, tag="rs_off"), off, AWARD, 3)
    capsys.readouterr()

    for name in (*ARTEFACTS, "stage_r1_enumerator.jsonl"):
        assert (on / name).read_bytes() == (off / name).read_bytes(), name


def test_shadow_mode_changes_no_neural_call_count(cli, tmp_path, monkeypatch, capsys):
    on, off = tmp_path / "on", tmp_path / "off"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m10=True, tag="on"), on, AWARD)
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m10=False, tag="off"), off, AWARD)
    capsys.readouterr()

    a = json.loads((on / "diagnostics.json").read_text())
    b = json.loads((off / "diagnostics.json").read_text())
    assert a == b
    for key in ("total_calls", "total_verification_calls", "total_generated_tokens"):
        if key in a:
            assert a[key] == b[key], key


def test_one_program_per_selected_query_in_manifest_order(cli, tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "staged"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m10=True, tag="on"), run_dir, STOCK)
    capsys.readouterr()

    programs = [
        json.loads(line)
        for line in (run_dir / "prompt_programs.jsonl").read_text().splitlines()
    ]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(programs) == len(manifest)
    assert [(p["SubjectEntity"], p["Relation"], p["row_index"]) for p in programs] == [
        (q["SubjectEntity"], q["Relation"], q["row_index"]) for q in manifest
    ]
    # Paired one-to-one with the M9 artefact, which is unchanged in schema.
    profiles = (run_dir / "query_profiles.jsonl").read_text().splitlines()
    assert len(profiles) == len(programs)


def test_persisted_programs_equal_recompiled_ones(cli, tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "recompile"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m10=True, tag="on"), run_dir, AREA)
    capsys.readouterr()

    profiler, compiler = QueryProfiler(), PromptProgramCompiler()
    for line in (run_dir / "prompt_programs.jsonl").read_text().splitlines():
        payload = json.loads(line)
        query, contract = compile_query(
            payload["SubjectEntity"], payload["Relation"], payload["row_index"]
        )
        recompiled = compiler.compile(query, contract, profiler.profile(query, contract))
        assert PromptProgram.from_json(payload) == recompiled


def test_no_rendered_prose_is_persisted(cli, tmp_path, monkeypatch, capsys):
    """The structured program is primary; a preview would only duplicate it."""
    run_dir = tmp_path / "preview"
    _run(cli, monkeypatch, _config(tmp_path, CONFIG, m10=True, tag="on"), run_dir, BORDERS)
    capsys.readouterr()

    for line in (run_dir / "prompt_programs.jsonl").read_text().splitlines():
        payload = json.loads(line)
        assert "preview" not in payload and "rendered" not in payload


def test_pipeline_without_a_compiler_is_the_pre_m10_path():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        NullRuntime(model_id="offline/null"), PipelineConfig(), profiler=QueryProfiler()
    )
    assert pipeline.prompt_compiler is None
    pipeline.enumerate_query(Query("Testland", BORDERS, 0))
    assert len(pipeline.query_profiles) == 1
    assert pipeline.prompt_programs == []


def test_programs_never_reach_the_evidence_graph():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        NullRuntime(model_id="offline/null"), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
    )
    graph = pipeline.enumerate_query(Query("Testland", BORDERS, 0))
    assert len(pipeline.prompt_programs) == 1
    blob = json.dumps(
        {k: str(v) for k, v in vars(graph).items() if not k.startswith("_")}
    ).casefold()
    for leaked in ("compiler_version", "semantic_cues", "answer_schema", "risk_directives"):
        assert leaked not in blob, leaked


# --------------------------------------------------------------------------
# 18. Configuration failure
# --------------------------------------------------------------------------


def test_m10_enabled_without_m9_fails_loudly():
    with pytest.raises(ValueError, match="prompt_compiler is enabled but"):
        build_prompt_compiler({"prompt_compiler": {"enabled": True}}, profiler_enabled=False)


def test_a_compiler_without_a_profiler_is_rejected_at_the_pipeline_too():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a profiler"):
        CoverPipeline(
            NullRuntime(model_id="offline/null"), PipelineConfig(),
            prompt_compiler=PromptProgramCompiler(),
        )


def test_disabled_or_absent_config_builds_no_compiler():
    assert build_prompt_compiler(None, profiler_enabled=True) is None
    assert build_prompt_compiler({}, profiler_enabled=True) is None
    assert build_prompt_compiler(
        {"prompt_compiler": {"enabled": False}}, profiler_enabled=False
    ) is None
    assert isinstance(
        build_prompt_compiler({"prompt_compiler": {"enabled": True}}, profiler_enabled=True),
        PromptProgramCompiler,
    )


def test_an_unsupported_mode_is_rejected():
    with pytest.raises(ValueError, match="unsupported prompt compiler mode"):
        PromptProgramCompiler(PromptCompilerConfig(enabled=True, mode="active"))


def test_unknown_config_keys_are_rejected():
    with pytest.raises(ValueError, match="unknown query_intelligence.prompt_compiler key"):
        PromptCompilerConfig.from_mapping({"enabled": True, "enabledd": True})


def test_an_unknown_relation_fails_loudly(compiler, profiler):
    with pytest.raises(UnknownRelationError):
        query = Query("Subject", "notARelation", 0)
        compiler.compile(query, None, profiler.profile(Query("Subject", BORDERS, 0)))
    with pytest.raises(UnknownRelationPromptError):
        get_prompt_spec("notARelation")


def test_a_malformed_prompt_override_is_rejected():
    with pytest.raises(ValueError, match="unknown relation"):
        PromptProgramCompiler(
            PromptCompilerConfig(enabled=True, relation_prompts={"notARelation": {}})
        )
    with pytest.raises(ValueError, match="unknown field"):
        PromptProgramCompiler(
            PromptCompilerConfig(enabled=True, relation_prompts={BORDERS: {"vibes": []}})
        )
    with pytest.raises(ValueError, match="must be a list"):
        PromptProgramCompiler(
            PromptCompilerConfig(
                enabled=True, relation_prompts={BORDERS: {"semantic_cues": "a cue"}}
            )
        )
    with pytest.raises(ValueError, match="at least one semantic cue"):
        PromptProgramCompiler(
            PromptCompilerConfig(
                enabled=True, relation_prompts={BORDERS: {"semantic_cues": []}}
            )
        )


def test_a_valid_override_applies_without_mutating_the_registry():
    from cover_kbc.query_intelligence import prompt_registry

    compiler = PromptProgramCompiler(
        PromptCompilerConfig(
            enabled=True, relation_prompts={BORDERS: {"semantic_cues": ["borders on"]}}
        )
    )
    profiler = QueryProfiler()
    assert _compile(compiler, profiler, "S", BORDERS).semantic_cues == ("borders on",)
    assert prompt_registry.RELATION_PROMPT_SPECS[BORDERS].semantic_cues[0] == (
        "shares a land border with"
    )
    assert _compile(PromptProgramCompiler(), profiler, "S", BORDERS).semantic_cues[0] == (
        "shares a land border with"
    )


def test_registry_consistency_catches_a_missing_relation(monkeypatch):
    from cover_kbc.query_intelligence import prompt_registry

    broken = dict(prompt_registry.RELATION_PROMPT_SPECS)
    broken.pop(AWARD)
    monkeypatch.setattr(prompt_registry, "RELATION_PROMPT_SPECS", broken)
    with pytest.raises(ValueError, match="no M10 prompt spec"):
        check_prompt_registry_consistency()


def test_directive_rules_reference_only_real_m9_axes():
    for rule in DIRECTIVE_RULES:
        assert rule.axis in RISK_AXES, rule.axis
    from cover_kbc.query_intelligence.types import SubjectSurfaceFeatures

    fields = set(SubjectSurfaceFeatures.__dataclass_fields__)
    for rule in SUBJECT_DIRECTIVE_RULES:
        assert not rule.feature or rule.feature in fields, rule.feature
